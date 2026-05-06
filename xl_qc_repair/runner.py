"""xl_qc_repair runner — simplified.

Pipeline:
  1. Load V6 final_table_map + workbook.  (DAG / formulas no longer read.)
  2. For every leaf, detect ``title_missing`` / ``rowname_missing``.
  3. Build a small bundle (data values only — above-cells when title
     missing, left-cells when row_names missing).
  4. Safe-auto for unambiguous cases; else one LLM call per candidate.
  5. Apply verdicts to a deep copy of the final map and write artifacts.

Per-sheet LLM cap and ``target_sheets`` filter are honored.

Future flags ``core_region_split`` / ``title_looks_like_footnote`` are
recognised on the leaf and propagated into the bundle's
``future_flags`` field for visibility, but they do NOT fire as triggers
yet.
"""

from __future__ import annotations

import copy
import json
import logging
import math
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from core.workbook_accessor import WorkbookAccessor
from core.paths import (
    get_dag_graph_path,
    get_init_output_path,
    get_v6_final_table_map_path,
)

from xl_qc_repair.candidate_detector import build_bundle, detect_triggers
from xl_qc_repair.config import XlQcRepairConfig
from xl_qc_repair.decision_applier import (
    apply_verdict_to_final_map,
    derive_safe_auto_verdict,
)
from xl_qc_repair.llm_review import CompleteFn, review_bundle
from xl_qc_repair.schemas import (
    QcRepairAuditEntry,
    QcRepairRunResult,
    QcVerdict,
    RepairCandidateBundle,
)

logger = logging.getLogger(__name__)


def _now_ts() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _resolve_workbook_path(run_id: str, output_base: str) -> Path:
    init_dir = get_init_output_path(run_id, output_base)
    candidate = init_dir / "cleaned_workbook.xlsx"
    if candidate.is_file():
        return candidate
    if init_dir.is_dir():
        for x in sorted(init_dir.glob("*.xlsx")):
            return x
    raise FileNotFoundError(f"Could not resolve workbook for run_id={run_id!r}")


def _resolve_llm(config: XlQcRepairConfig) -> Optional[CompleteFn]:
    if not config.use_llm:
        return None
    try:
        from core.env_bootstrap import load_project_dotenv
        load_project_dotenv()
    except Exception:
        pass
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        logger.warning(
            "xl_qc_repair: use_llm=true but OPENAI_API_KEY missing — running deterministic-only"
        )
        return None
    try:
        from openai import OpenAI
        from xl_sheetagent.llm_dispatch import complete_text
    except Exception as exc:
        logger.warning("xl_qc_repair: cannot init LLM client: %s", exc)
        return None
    client = OpenAI(api_key=api_key)
    model = config.llm_model

    def _complete(prompt: str, max_tokens: int, temperature: float) -> Tuple[str, int]:
        return complete_text(client, model, prompt, max_tokens, temperature)

    return _complete


def _effective_budget_cap(
    config: XlQcRepairConfig, n_targets: int,
) -> int:
    cap = int(config.qc_repair_llm_budget_max)
    strategy = (config.qc_repair_llm_budget_strategy or "fixed").lower()
    if strategy == "scaled_with_targets":
        scaled = int(math.ceil(max(0, n_targets) * 0.6))
        return min(cap, scaled if scaled > 0 else cap)
    return cap


def run_qc_repair(
    run_id: str,
    *,
    output_base: str = "data/output",
    config: Optional[XlQcRepairConfig] = None,
    llm_complete: Optional[CompleteFn] = None,
    target_sheets: Optional[List[str]] = None,
) -> QcRepairRunResult:
    """Run QC repair for ``run_id``.

    ``target_sheets``: when provided, only candidates from these sheets are
    detected and repaired.  Tables on other sheets pass through unchanged.
    ``None`` or empty list = all sheets.
    """
    t0 = time.perf_counter()
    config = config or XlQcRepairConfig()
    if llm_complete is None:
        llm_complete = _resolve_llm(config)

    final_map_path = get_v6_final_table_map_path(run_id, output_base)
    if not final_map_path.is_file():
        raise FileNotFoundError(f"V6 final_table_map.json missing at {final_map_path}")

    final_map = json.loads(final_map_path.read_text(encoding="utf-8"))
    wb_path = _resolve_workbook_path(run_id, output_base)

    # QC.A — load DAG + formulas + node_index when synthesis-context
    # injection is enabled.  Empty dicts on failure (best-effort: the
    # bundle just won't carry the synthesis_context_dag block).
    dag_data: Dict[str, Any] = {}
    node_index: Dict[str, Any] = {}
    formulas_data: Dict[str, Any] = {}
    if getattr(config, "qc_repair_include_dag_context_for_synthesis", False):
        try:
            dag_path = get_dag_graph_path(run_id, output_base)
            if dag_path.is_file():
                dag_data = json.loads(dag_path.read_text(encoding="utf-8"))
                node_index_path = dag_path.parent / "node_index.json"
                if node_index_path.is_file():
                    node_index = json.loads(node_index_path.read_text(encoding="utf-8"))
                formulas_path = dag_path.parent / "formulas.json"
                if formulas_path.is_file():
                    formulas_data = json.loads(formulas_path.read_text(encoding="utf-8"))
        except Exception:
            # Best-effort — synthesis_context just won't be injected.
            dag_data = {}
            node_index = {}
            formulas_data = {}

    out_dir = Path(output_base) / run_id / "qc_repair_output"
    out_dir.mkdir(parents=True, exist_ok=True)
    audit_path = out_dir / "qc_repair_audit.jsonl"
    trace_path = out_dir / "qc_repair_trace.jsonl"
    repaired_path = out_dir / "final_table_map_repaired.json"
    errors_path = out_dir / "qc_repair_build_errors.jsonl"
    messages_path = out_dir / "qc_repair_llm_messages.jsonl"
    v6_snapshot_path = out_dir / "final_table_map_v6_snapshot.json"
    v6_snapshot_path.write_text(
        json.dumps(final_map, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )

    repaired = copy.deepcopy(final_map)

    audit_lines: List[Dict[str, Any]] = []
    trace_lines: List[Dict[str, Any]] = []
    error_lines: List[str] = []
    messages: List[Dict[str, Any]] = []

    n_targets = 0
    n_safe_auto = 0
    n_llm = 0
    n_repaired = 0
    n_unresolved = 0
    llm_calls_used = 0

    target_sheets_set = set(target_sheets) if target_sheets else None

    sheet_leaves: Dict[str, List[Dict[str, Any]]] = {}
    leaf_iter: List[Tuple[Dict[str, Any], List[str]]] = []
    for sheet, block in repaired.items():
        if not isinstance(block, dict):
            continue
        in_scope = (target_sheets_set is None) or (sheet in target_sheets_set)
        leaves: List[Dict[str, Any]] = []
        for entry in (block.get("entries") or []):
            if not isinstance(entry, dict):
                continue
            if entry.get("kind") == "parent":
                pflags = list(entry.get("source_flags") or [])
                for c in (entry.get("children") or []):
                    leaves.append(c)
                    if in_scope:
                        leaf_iter.append((c, pflags))
            else:
                leaves.append(entry)
                if in_scope:
                    leaf_iter.append((entry, []))
        sheet_leaves[sheet] = leaves

    accessor = WorkbookAccessor(str(wb_path))
    try:
        trace_lines.append({
            "ts": _now_ts(), "event": "qc_run_start", "run_id": run_id,
            "n_total_leaves": sum(len(v) for v in sheet_leaves.values()),
            "target_sheets": (
                sorted(target_sheets_set) if target_sheets_set else "ALL"
            ),
        })

        # ------------------------------------------------------------------
        # Phase 1 — detect candidates and build bundles.
        # ------------------------------------------------------------------
        bundles: List[RepairCandidateBundle] = []
        n_seen = 0
        for leaf, parent_flags in leaf_iter:
            if n_seen >= int(config.max_tables_per_run):
                break
            n_seen += 1
            if leaf.get("node_type") in (config.skip_node_types or []):
                continue
            sheet_name = leaf.get("sheet_name")
            sibling_leaves = sheet_leaves.get(sheet_name) or []
            triggers = detect_triggers(
                leaf, parent_flags,
                accessor=accessor, config=config,
                sheet_leaves=sibling_leaves,
            )
            if not triggers:
                continue
            n_targets += 1
            try:
                bundle = build_bundle(
                    leaf, sibling_leaves,
                    trigger=triggers[0], triggers=triggers,
                    config=config, accessor=accessor,
                    parent_flags=parent_flags,
                    final_map=final_map,
                    dag_data=dag_data,
                    node_index=node_index,
                    formulas_data=formulas_data,
                )
            except Exception as exc:
                error_lines.append(f"build_bundle failed for {leaf.get('table_id')}: {exc}")
                continue
            bundles.append(bundle)
            trace_lines.append({
                "ts": _now_ts(), "event": "qc_candidate_detected",
                "table_id": bundle.table_id, "triggers": triggers,
                "classification": bundle.classification,
                "sheet_name": sheet_name,
                "future_flags": list(bundle.future_flags),
            })

        budget_cap = _effective_budget_cap(config, n_targets)
        per_sheet_cap = max(0, int(config.qc_repair_max_llm_calls_per_sheet))
        per_sheet_calls: Dict[str, int] = {}

        def _sheet_cap_remaining(sheet: str) -> int:
            return max(0, per_sheet_cap - per_sheet_calls.get(sheet, 0))

        # ------------------------------------------------------------------
        # Phase 2 — resolve verdicts per bundle.
        # ------------------------------------------------------------------
        for bundle in bundles:
            if bundle.classification == "safe_auto":
                verdict = derive_safe_auto_verdict(bundle)
                _record_apply(
                    repaired, verdict, bundle,
                    audit_lines, trace_lines,
                    dispatch_mode="safe_auto",
                )
                n_safe_auto += 1
                if verdict.verdict == "unresolved":
                    n_unresolved += 1
                elif verdict.verdict != "keep_unchanged":
                    n_repaired += 1
                continue

            sheet = bundle.sheet_name or ""
            if _sheet_cap_remaining(sheet) <= 0:
                trace_lines.append({
                    "ts": _now_ts(), "event": "qc_sheet_cap_exhausted",
                    "sheet_name": sheet,
                    "calls_used": per_sheet_calls.get(sheet, 0),
                    "cap": per_sheet_cap,
                    "table_id": bundle.table_id,
                })
                verdict = QcVerdict(
                    table_id=bundle.table_id,
                    verdict="keep_unchanged",
                    rationale=(
                        f"per_sheet_cap_exhausted (max {per_sheet_cap} "
                        f"LLM calls per sheet); sheet '{sheet}' already "
                        f"used {per_sheet_calls.get(sheet, 0)}"
                    ),
                    source="fallback_keep",
                )
                _record_apply(
                    repaired, verdict, bundle,
                    audit_lines, trace_lines,
                    dispatch_mode="per_sheet_cap_exhausted",
                )
                continue

            budget_remaining = max(0, budget_cap - llm_calls_used)
            trace_lines.append({
                "ts": _now_ts(), "event": "qc_llm_review_started",
                "table_id": bundle.table_id,
                "budget_remaining": budget_remaining,
                "sheet_cap_remaining": _sheet_cap_remaining(sheet),
            })
            verdict, tokens, prompt, raw = review_bundle(
                bundle,
                llm_complete=llm_complete,
                config_max_tokens=int(config.llm_max_tokens),
                config_temperature=float(config.llm_temperature),
                budget_remaining=budget_remaining,
            )
            if verdict.source == "llm":
                llm_calls_used += 1
                per_sheet_calls[sheet] = per_sheet_calls.get(sheet, 0) + 1
                n_llm += 1
            messages.append({
                "ts": _now_ts(), "kind": "per_table",
                "sheet_name": bundle.sheet_name,
                "candidate_ids": [bundle.table_id],
                "tokens": tokens,
                "prompt": prompt or "",
                "response": raw or "",
            })
            trace_lines.append({
                "ts": _now_ts(), "event": "qc_llm_review_completed",
                "table_id": bundle.table_id,
                "verdict": verdict.verdict, "tokens": tokens,
                "sheet_calls_after": per_sheet_calls.get(sheet, 0),
            })
            _record_apply(
                repaired, verdict, bundle,
                audit_lines, trace_lines,
                dispatch_mode="per_table",
            )
            if verdict.verdict == "unresolved":
                n_unresolved += 1
            elif verdict.verdict != "keep_unchanged":
                n_repaired += 1
    finally:
        accessor.close()

    trace_lines.append({
        "ts": _now_ts(), "event": "qc_run_complete",
        "n_targets": n_targets, "n_safe_auto": n_safe_auto,
        "n_llm_reviewed": n_llm, "n_repaired": n_repaired,
        "n_unresolved": n_unresolved, "llm_calls_used": llm_calls_used,
    })

    repaired_path.write_text(
        json.dumps(repaired, indent=2, ensure_ascii=False, default=str),
        encoding="utf-8",
    )
    with open(audit_path, "w", encoding="utf-8") as f:
        for row in audit_lines:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    with open(trace_path, "w", encoding="utf-8") as f:
        for row in trace_lines:
            f.write(json.dumps(row, ensure_ascii=False, default=str) + "\n")
    with open(errors_path, "w", encoding="utf-8") as f:
        for line in error_lines:
            f.write(line + "\n")
    with open(messages_path, "w", encoding="utf-8") as f:
        for msg in messages:
            f.write(json.dumps(msg, ensure_ascii=False, default=str) + "\n")

    elapsed = time.perf_counter() - t0
    return QcRepairRunResult(
        run_id=run_id,
        n_targets=n_targets,
        n_safe_auto=n_safe_auto,
        n_llm_reviewed=n_llm,
        n_repaired=n_repaired,
        n_unresolved=n_unresolved,
        llm_calls_used=llm_calls_used,
        elapsed_seconds=elapsed,
        artifacts=[
            str(repaired_path), str(audit_path), str(trace_path),
            str(errors_path), str(messages_path), str(v6_snapshot_path),
        ],
        errors=error_lines,
    )


def _record_apply(
    repaired: Dict[str, Any],
    verdict: QcVerdict,
    bundle: RepairCandidateBundle,
    audit_lines: List[Dict[str, Any]],
    trace_lines: List[Dict[str, Any]],
    *,
    dispatch_mode: str,
) -> None:
    applied = apply_verdict_to_final_map(repaired, verdict, bundle)
    audit_lines.append(QcRepairAuditEntry(
        ts=_now_ts(),
        table_id=bundle.table_id,
        sheet_name=bundle.sheet_name,
        trigger=bundle.trigger,
        classification=bundle.classification,
        verdict=verdict.verdict,
        rationale=verdict.rationale,
        confidence=verdict.confidence,
        source=verdict.source,
        dispatch_mode=dispatch_mode,
        applied_changes=applied,
    ).model_dump(mode="json"))


__all__ = ["QcRepairRunResult", "run_qc_repair"]
