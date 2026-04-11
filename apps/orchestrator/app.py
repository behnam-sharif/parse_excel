"""
xlgen_v3 Streamlit orchestrator — thin UI over stage entry points.

Run from repo root:
    streamlit run apps/orchestrator/app.py

Concurrency follows the Streamlit pattern from xlgen_v2 (structural-table stage UI): one ThreadPoolExecutor worker,
Future polling with time.sleep + st.rerun while work is in flight.
"""

from __future__ import annotations

import concurrent.futures
import importlib.util
import os
import sys
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Dict, Optional

_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import streamlit as st

from core.config import load_config
from core.paths import (
    get_init_output_path,
    get_parse_output_path,
    get_table_output_path,
    get_sheetagent_output_path,
    get_v4_sheetagent_output_path,
)
from core.stage_config import (
    xl_table_config_from_mapping,
    merge_stage_dict,
    parse_config_from_mapping,
    xl_init_config_from_mapping,
    sheetagent_config_from_mapping,
)

_executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)


def _load_v3a_viewer():
    """Load sibling ``v3a_viewer.py`` by path.

    ``streamlit run apps/orchestrator/app.py`` executes this file as ``__main__``,
    so ``from .v3a_viewer`` relative imports fail.
    """
    path = Path(__file__).resolve().parent / "v3a_viewer.py"
    spec = importlib.util.spec_from_file_location("orchestrator_v3a_viewer", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load v3a_viewer from {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_v4_viewer():
    """Load sibling ``v4_viewer.py`` by path.

    ``streamlit run apps/orchestrator/app.py`` executes this file as ``__main__``,
    so ``from .v4_viewer`` relative imports fail.
    """
    path = Path(__file__).resolve().parent / "v4_viewer.py"
    spec = importlib.util.spec_from_file_location("orchestrator_v4_viewer", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load v4_viewer from {path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_v3a_viewer = _load_v3a_viewer()
_v4_viewer = _load_v4_viewer()


def _default_session() -> None:
    if "run_id" not in st.session_state:
        st.session_state["run_id"] = "streamlit_run"
    if "output_base" not in st.session_state:
        st.session_state["output_base"] = "data/output"
    if "uploaded_path" not in st.session_state:
        st.session_state["uploaded_path"] = None
    if "cfg_ov" not in st.session_state:
        st.session_state["cfg_ov"] = {
            "xl_init": {},
            "xl_parse": {},
            "xl_table": {},
            "xl_sheetagent": {},
        }
    if "pipeline_future" not in st.session_state:
        st.session_state["pipeline_future"] = None
    if "stage_result" not in st.session_state:
        st.session_state["stage_result"] = None
    if "stage_error" not in st.session_state:
        st.session_state["stage_error"] = None
    if "stage_op" not in st.session_state:
        st.session_state["stage_op"] = None
    if "pipeline_started_at" not in st.session_state:
        st.session_state["pipeline_started_at"] = None
    if "orchestrator_view_saved_v3a" not in st.session_state:
        st.session_state["orchestrator_view_saved_v3a"] = False
    # Older sessions may lack xl_sheetagent overrides
    _co = st.session_state.get("cfg_ov")
    if isinstance(_co, dict) and "xl_sheetagent" not in _co:
        _co["xl_sheetagent"] = {}


def _effective_init_config():
    pc = load_config()
    merged = merge_stage_dict(pc.xl_init, st.session_state["cfg_ov"]["xl_init"])
    cfg = xl_init_config_from_mapping(merged)
    return replace(cfg, interactive=False)


def _effective_parse_config():
    pc = load_config()
    merged = merge_stage_dict(pc.xl_parse, st.session_state["cfg_ov"]["xl_parse"])
    return parse_config_from_mapping(merged)


def _effective_table_config():
    pc = load_config()
    merged = merge_stage_dict(pc.xl_table, st.session_state["cfg_ov"]["xl_table"])
    return xl_table_config_from_mapping(merged)


def _poll_future() -> bool:
    """Return True if a job is still running."""
    fut: Optional[concurrent.futures.Future] = st.session_state["pipeline_future"]
    return fut is not None and not fut.done()


def _harvest_future() -> None:
    fut: Optional[concurrent.futures.Future] = st.session_state["pipeline_future"]
    if fut is None or not fut.done():
        return
    try:
        st.session_state["stage_result"] = fut.result()
        st.session_state["stage_error"] = None
    except Exception as exc:
        st.session_state["stage_error"] = str(exc)
        st.session_state["stage_result"] = None
    finally:
        st.session_state["pipeline_future"] = None
        st.session_state["pipeline_started_at"] = None


def app() -> None:
    st.set_page_config(page_title="xlgen_v3 — Orchestrator", layout="wide")
    st.title("xlgen_v3 — Excel pipeline orchestrator")
    _default_session()
    _harvest_future()

    # --- Sidebar: run + upload + CLI + config --------------------------------
    st.sidebar.header("Run")
    _pc0 = load_config()
    st.sidebar.caption(
        f"general.yaml — log level: {_pc0.general.logging_level} | "
        f"output_base: {_pc0.general.output_base}"
    )
    st.session_state["run_id"] = st.sidebar.text_input("Run ID", st.session_state["run_id"])
    st.session_state["output_base"] = st.sidebar.text_input(
        "Output base", st.session_state["output_base"]
    )

    up = st.sidebar.file_uploader("Upload workbook (.xlsx / .xlsm)", type=["xlsx", "xlsm"])
    if up is not None:
        inp = _ROOT / "data" / "input"
        inp.mkdir(parents=True, exist_ok=True)
        buf = up.getbuffer()
        upload_sig = getattr(up, "file_id", None)
        if upload_sig is None:
            upload_sig = (up.name, buf.nbytes)
        prev_path = st.session_state.get("uploaded_path")
        need_save = st.session_state.get("_upload_sig") != upload_sig or not (
            prev_path and Path(prev_path).is_file()
        )
        if need_save:
            src = Path(up.name)
            stem = src.stem or "workbook"
            suffix = src.suffix.lower()
            if suffix not in (".xlsx", ".xlsm"):
                suffix = ".xlsx"
            # Unique path avoids Windows PermissionError when the same filename is open in Excel.
            dest = inp / f"{stem}_{uuid.uuid4().hex[:10]}{suffix}"
            try:
                dest.write_bytes(buf)
            except PermissionError as exc:
                st.sidebar.error(
                    f"Could not save upload ({exc}). Close the file in Excel if it is open, "
                    "or check folder permissions for data/input."
                )
            else:
                st.session_state["uploaded_path"] = str(dest)
                st.session_state["_upload_sig"] = upload_sig
                st.sidebar.success(f"Saved: {dest}")

    input_path = st.session_state["uploaded_path"] or ""
    st.sidebar.text_input("Input path (upload or paste)", input_path, disabled=True)

    run_id = st.session_state["run_id"]
    base = st.session_state["output_base"]
    init_out = get_init_output_path(run_id, base)
    parse_out = get_parse_output_path(run_id, base)
    table_out = get_table_output_path(run_id, base)
    sa_out = get_sheetagent_output_path(run_id, base)
    v4_out = get_v4_sheetagent_output_path(run_id, base)

    cli = f"""```text
py -3 scripts/run_init.py --input <workbook.xlsx> --run-id {run_id} --output-base {base} --no-interactive
py -3 scripts/run_parse.py --run-id {run_id} --output-base {base}
py -3 scripts/run_table.py --run-id {run_id} --output-base {base}
```"""
    with st.sidebar.expander("CLI (equivalent commands)", expanded=False):
        st.markdown(cli)

    with st.sidebar.expander("⚙ Session config overrides", expanded=False):
        st.caption(
            "Overrides merge on top of config/stages.yaml for this browser session only."
        )
        pc = load_config()
        st.markdown("**xl_parse**")
        st.session_state["cfg_ov"]["xl_parse"]["max_workers"] = st.number_input(
            "max_workers",
            min_value=1,
            max_value=32,
            value=int(pc.xl_parse.get("max_workers", 4)),
            key="ov_parse_workers",
        )
        st.session_state["cfg_ov"]["xl_parse"]["export_cut_candidates"] = st.checkbox(
            "export_cut_candidates",
            value=bool(pc.xl_parse.get("export_cut_candidates", True)),
            key="ov_parse_export_cuts",
        )
        st.markdown("**xl_table**")
        st.session_state["cfg_ov"]["xl_table"]["min_rows"] = st.number_input(
            "min_rows",
            min_value=1,
            max_value=50,
            value=int(pc.xl_table.get("min_rows", 2)),
            key="ov_table_min_rows",
        )
        st.session_state["cfg_ov"]["xl_table"]["min_density"] = st.slider(
            "min_density",
            0.0,
            1.0,
            float(pc.xl_table.get("min_density", 0.03)),
            0.01,
            key="ov_table_min_density",
        )
        st.session_state["cfg_ov"]["xl_table"]["include_cut_candidates"] = st.checkbox(
            "include_cut_candidates",
            value=bool(pc.xl_table.get("include_cut_candidates", True)),
            key="ov_table_inc_cuts",
        )
        st.session_state["cfg_ov"]["xl_table"]["use_other_tools"] = st.checkbox(
            "use_other_tools",
            value=bool(pc.xl_table.get("use_other_tools", False)),
            key="ov_table_other_tools",
        )
        st.markdown("**xl_sheetagent (V3A)**")
        st.caption(
            "Merged when you click **Run V3A sheetagent**. `OPENAI_API_KEY` in repo-root "
            "`.env` is loaded automatically (see core/env_bootstrap.py) when LLM is on."
        )
        _sa_ov = st.session_state["cfg_ov"].setdefault("xl_sheetagent", {})
        _pc_sa = pc.xl_sheetagent
        _enable_v3a_llm = st.checkbox(
            "V3A: LLM ownership decisions (sets v3a_skip_llm=false)",
            value=not bool(_pc_sa.get("v3a_skip_llm", True)),
            key="ov_xl_sa_v3a_llm",
        )
        _sa_ov["v3a_skip_llm"] = not _enable_v3a_llm

    busy = _poll_future()
    if busy:
        op = st.session_state.get("stage_op", "stage")
        t0 = st.session_state.get("pipeline_started_at")
        elapsed = (time.time() - float(t0)) if t0 is not None else 0.0
        lines = [f"Running **{op}** — {elapsed:.0f}s elapsed"]
        if op == "xl_init":
            lines.append(
                "xl_init runs VBA cleaning, then sheet metadata extraction (short vs. old LLM/summary path)."
            )
        st.sidebar.info("\n\n".join(lines))
        time.sleep(1)
        st.rerun()

    def _clear_for_run(op: str) -> None:
        st.session_state["stage_result"] = None
        st.session_state["stage_error"] = None
        st.session_state["stage_op"] = op
        st.session_state["orchestrator_view_saved_v3a"] = False

    init_btn = st.sidebar.button("▶ Run xl_init", disabled=busy or not input_path)
    parse_btn = st.sidebar.button("▶ Run xl_parse", disabled=busy)
    table_btn = st.sidebar.button("▶ Run xl_table", disabled=busy)
    v3a_btn = st.sidebar.button("▶ Run V3A sheetagent", disabled=busy)
    v4_btn = st.sidebar.button("▶ Run xl_sheetagent_v4", disabled=busy)
    st.sidebar.caption(
        "V4: install deps from requirements.txt; set OPENAI_API_KEY in repo-root .env. "
        "Orchestrator loads .env and passes an OpenAI client into Phase 1."
    )

    if init_btn and not busy:
        _clear_for_run("xl_init")
        # Resolve config on the main thread — worker threads must not read st.session_state.
        _init_cfg = _effective_init_config()

        def _work():
            from xl_init import run_init

            init_out.mkdir(parents=True, exist_ok=True)
            return run_init(input_path, str(init_out), config=_init_cfg)

        st.session_state["pipeline_future"] = _executor.submit(_work)
        st.session_state["pipeline_started_at"] = time.time()
        st.rerun()

    if parse_btn and not busy:
        _clear_for_run("xl_parse")
        _parse_cfg = _effective_parse_config()

        def _work():
            from xl_parse import run_parse

            wb = init_out / "cleaned_workbook.xlsx"
            if not wb.is_file():
                raise FileNotFoundError(
                    f"Missing {wb}. Run xl_init first or upload a workbook and run init."
                )
            parse_out.mkdir(parents=True, exist_ok=True)
            return run_parse(str(wb), str(parse_out), config=_parse_cfg)

        st.session_state["pipeline_future"] = _executor.submit(_work)
        st.session_state["pipeline_started_at"] = time.time()
        st.rerun()

    if table_btn and not busy:
        _clear_for_run("xl_table")
        _table_cfg = _effective_table_config()

        def _work():
            from xl_table import run_table

            parse_out.mkdir(parents=True, exist_ok=True)
            table_out.mkdir(parents=True, exist_ok=True)
            return run_table(str(parse_out), str(table_out), config=_table_cfg)

        st.session_state["pipeline_future"] = _executor.submit(_work)
        st.session_state["pipeline_started_at"] = time.time()
        st.rerun()

    if v3a_btn and not busy:
        _clear_for_run("v3a_sheetagent")
        pc = load_config()
        _sa_user = st.session_state["cfg_ov"].get("xl_sheetagent") or {}
        _sa_merged = merge_stage_dict(pc.xl_sheetagent, _sa_user)
        _sa_merged = merge_stage_dict(_sa_merged, {"v3a_enabled": True})
        _sa_cfg = sheetagent_config_from_mapping(_sa_merged)

        _run_id_capture = run_id
        _sa_out_capture = sa_out

        def _work():
            from xl_sheetagent import run_sheetagent

            _sa_out_capture.mkdir(parents=True, exist_ok=True)
            return run_sheetagent(_run_id_capture, str(_sa_out_capture), config=_sa_cfg)

        st.session_state["pipeline_future"] = _executor.submit(_work)
        st.session_state["pipeline_started_at"] = time.time()
        st.rerun()

    if v4_btn and not busy:
        _clear_for_run("xl_sheetagent_v4")

        _run_id_capture = run_id
        _v4_out_capture = v4_out
        _table_out_capture = table_out

        def _work():
            from core.env_bootstrap import load_project_dotenv

            load_project_dotenv()

            import openai
            from xl_sheetagent_v4.runner import run_v4_analysis

            _v4_out_capture.mkdir(parents=True, exist_ok=True)

            if not os.environ.get("OPENAI_API_KEY", "").strip():
                raise RuntimeError(
                    "OPENAI_API_KEY is missing. Add it to the repo-root .env file "
                    "(or set it in the environment) before running xl_sheetagent_v4."
                )
            llm_client = openai.OpenAI()
            return run_v4_analysis(
                run_id=_run_id_capture,
                output_base=Path(_v4_out_capture).parent.parent,
                llm_client=llm_client,
                model="gpt-4o",
                max_recursion_depth=3,
            )

        st.session_state["pipeline_future"] = _executor.submit(_work)
        st.session_state["pipeline_started_at"] = time.time()
        st.rerun()

    view_v3a_saved_btn = st.sidebar.button(
        "📂 View saved V3A results (no re-run)",
        disabled=busy,
        help="Loads sheetagent_output/v3a_results.json for the current Run ID — same UI as after Run V3A.",
    )
    if view_v3a_saved_btn and not busy:
        st.session_state["orchestrator_view_saved_v3a"] = True
        st.rerun()

    # --- Main area -----------------------------------------------------------
    st.subheader("Paths")
    st.code(
        f"init_output:          {init_out}\n"
        f"parse_output:         {parse_out}\n"
        f"table_output:         {table_out}\n"
        f"sheetagent_output:    {sa_out}\n"
        f"v4_sheetagent_output: {v4_out}",
        language="text",
    )

    if st.session_state["stage_error"]:
        st.error(st.session_state["stage_error"])

    res = st.session_state["stage_result"]
    op = st.session_state["stage_op"]

    if st.session_state.get("orchestrator_view_saved_v3a"):
        _v3a_viewer.render_v3a_from_json(str(sa_out))
    elif res is not None and op == "xl_table":
        rs = res.run_summary
        st.success(
            f"xl_table complete — {rs.total_final_tables} tables "
            f"({rs.elapsed_seconds:.2f}s)"
        )
        tab_a, tab_b, tab_c = st.tabs(["Summary", "Per-sheet", "Notes"])
        with tab_a:
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Input bboxes", rs.total_input_bboxes)
            c2.metric("Final tables", rs.total_final_tables)
            c3.metric("Elapsed (s)", f"{rs.elapsed_seconds:.2f}")
            c4.metric("Structural splits ✓", rs.total_structural_splits_accepted)
        with tab_b:
            if rs.per_sheet:
                import pandas as pd

                st.dataframe(
                    pd.DataFrame(
                        [{"sheet": k, **v} for k, v in rs.per_sheet.items()]
                    ),
                    use_container_width=True,
                )
            else:
                st.info("No per-sheet breakdown.")
        with tab_c:
            st.write(
                "Final tables by sheet (Excel ranges): `accepted_tables.json` or "
                "`accepted_tables.txt` under table_output. Full payload: `structural_tables.json`. "
                "Optional debug log: `decisions.jsonl`."
            )
    elif res is not None and op == "v3a_sheetagent":
        # V3ARunResult has a .sheets attribute; enriched table list does not
        if hasattr(res, "sheets"):
            _v3a_viewer.render_v3a_results(res)
        else:
            # Fallback: load from saved JSON
            _v3a_viewer.render_v3a_from_json(str(sa_out))
    elif res is not None and op == "xl_sheetagent_v4":
        # V4 results - show Phase 1 & 2 output (tables, metadata, children)
        _v4_viewer.render_v4_from_json(str(v4_out))
    elif res is not None and op == "xl_init":
        st.success(
            f"xl_init complete — {len(res.table_sheets)} approved sheet(s). "
            f"Cleaned: {res.cleaned_workbook_path}"
        )
    elif res is not None and op == "xl_parse":
        st.success(f"xl_parse complete — {len(res)} sheet result(s).")

    if (
        res is None
        and not st.session_state["stage_error"]
        and not st.session_state.get("orchestrator_view_saved_v3a")
    ):
        st.info(
            "Upload a workbook, then run **xl_init** → **xl_parse** → **xl_table** "
            "in order. Then optionally run **V3A sheetagent** or **xl_sheetagent_v4** "
            "to extract semantic pieces and metadata."
        )


if __name__ == "__main__":
    app()
else:
    app()
