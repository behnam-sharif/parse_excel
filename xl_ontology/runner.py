"""xl_ontology runner — top-level seed-phase entry point.

Wires upstream loaders + ToolContext + agents + writers and
orchestrates the four-agent run. Phase D scope (synthetic fixture,
no real LLM): produces all five required artifacts on disk.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

from core.config import load_config
from core.paths import (
    get_dag_graph_path,
    get_init_output_path,
    get_node_type_override_log_path,
    get_ontology_build_errors_path,
    get_ontology_output_path,
    get_ontology_trace_path,
    get_v6_final_table_map_path,
)
from core.stage_config import ontology_config_from_pipeline
from xl_ontology.agents import (
    CalculationsAgent,
    InputsAgent,
    OverallModelAgent,
    ResultsAgent,
)
from xl_ontology.builder import SummaryModelBuilder
from xl_ontology.budget import LlmBudget
from xl_ontology.config import XlOntologyConfig
from xl_ontology.output_writers import (
    next_summary_model_version,
    write_agent_outputs,
    write_summary_model,
    write_table_tags,
)
from xl_ontology.pack_loader import load_pack
from xl_ontology.schemas import (
    BuildErrorRecord,
    RunComplete,
    RunStart,
    WarningEvent,
    _TraceBase,
)
from xl_ontology.tools import ToolContext
from xl_ontology.trace import (
    BuildErrorWriter,
    NodeTypeOverrideWriter,
    TraceWriter,
)
from xl_ontology.upstream import (
    DagIndex,
    FinalMapIndex,
    FormulasIndex,
    StructuralSanity,
    WorkbookView,
)


@dataclass
class OntologyRunResult:
    run_id: str
    version_written: int
    artifacts: List[Path] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    llm_calls_used: int = 0
    elapsed_seconds: float = 0.0


class OntologyRunError(Exception):
    """Raised when the run cannot start (e.g., upstream artifacts missing)."""

    def __init__(self, reason: str, partial_artifacts: Optional[List[Path]] = None):
        super().__init__(reason)
        self.reason = reason
        self.partial_artifacts = partial_artifacts or []


def run_ontology(
    run_id: str,
    *,
    output_base: str = "data/output",
    config: Optional[XlOntologyConfig] = None,
    workbook_path: Optional[str | Path] = None,
    llm_complete=None,
) -> OntologyRunResult:
    """Seed-phase entry point.

    Resolves upstream artifacts under ``output_base/run_id/``,
    constructs the agent pipeline, and writes the five required
    artifacts. ``llm_complete`` is the optional CompleteFn (Phase E
    integration); when None, the pipeline runs deterministically with
    template-only summaries.
    """
    t0 = time.perf_counter()
    if config is None:
        pc = load_config()
        config = ontology_config_from_pipeline(pc)

    # --- resolve upstream paths ---
    final_map_path = get_v6_final_table_map_path(run_id, output_base)
    dag_path = get_dag_graph_path(run_id, output_base)
    if not final_map_path.is_file():
        raise OntologyRunError(
            f"final_table_map.json missing at {final_map_path} — run V6 "
            "finalize first."
        )
    if not dag_path.is_file():
        raise OntologyRunError(
            f"dag.json missing at {dag_path} — run xl_dag first."
        )
    node_index_path = dag_path.parent / "node_index.json"
    formulas_path = dag_path.parent / "formulas.json"
    wb_path = _resolve_workbook_path(run_id, output_base, workbook_path)

    # --- output dir ---
    out_dir = get_ontology_output_path(run_id, output_base)
    out_dir.mkdir(parents=True, exist_ok=True)

    # --- load pack ---
    pack = load_pack(config.packs_root, config.pack_name)

    # --- workbook hash ---
    workbook_file_hash = "sha256:" + hashlib.sha256(
        wb_path.read_bytes()
    ).hexdigest()

    # --- writers (open with append=False since this is the seed run) ---
    trace = TraceWriter(get_ontology_trace_path(run_id, output_base))
    overrides = NodeTypeOverrideWriter(
        get_node_type_override_log_path(run_id, output_base)
    )
    errors = BuildErrorWriter(
        get_ontology_build_errors_path(run_id, output_base)
    )

    # --- emit RunStart + any pack warnings as WarningEvents ---
    trace.emit(
        RunStart(
            ts=_TraceBase.now_ts(),
            run_id=run_id,
            ontology_id=pack.id,
            phase="seed",
        )
    )
    for w in pack.warnings:
        trace.emit(
            WarningEvent(
                ts=_TraceBase.now_ts(),
                category=w.category,
                message=w.message,
                file_path=w.file_path,
            )
        )

    artifacts: List[Path] = []
    error_list: List[str] = []
    llm_calls_used = 0
    try:
        # --- upstream loaders + context ---
        final_map = FinalMapIndex.load(final_map_path)
        dag = DagIndex.load(dag_path, node_index_path if node_index_path.exists() else None)
        formulas = FormulasIndex(formulas_path if formulas_path.exists() else None)
        workbook = WorkbookView.open(wb_path)
        try:
            sanity = StructuralSanity(
                final_map=final_map, dag=dag, workbook=workbook
            )
            builder = SummaryModelBuilder(
                model_id=run_id,
                ontology_id=pack.id,
                ontology_version=pack.version,
                workbook_file_hash=workbook_file_hash,
                seed_calls_max=config.seed_llm_budget_max,
                enrichment_calls_max=config.enrichment_llm_budget_max,
            )
            budget = LlmBudget(
                seed_calls_max=config.seed_llm_budget_max,
                enrichment_calls_max=config.enrichment_llm_budget_max,
            )
            ctx = ToolContext(
                final_map=final_map,
                dag=dag,
                formulas=formulas,
                workbook=workbook,
                sanity=sanity,
                builder=builder,
                budget=budget,
                trace=trace,
                override_writer=overrides,
                build_errors=errors,
                llm_complete=llm_complete,
            )

            # --- agents (sequential per plan §7.1) ---
            agents_run = 0
            for agent_cls in (
                OverallModelAgent,
                InputsAgent,
                CalculationsAgent,
                ResultsAgent,
            ):
                agent = agent_cls(ctx, pack.nodes[agent_cls.node_name])
                agent.run(pack.pack_overview_text)
                agents_run += 1

            # --- finalise budget snapshot in the summary ---
            builder.update_budget_snapshot(budget.snapshot())
            llm_calls_used = budget.seed_calls_used

            # --- table_tags assembly ---
            lookup = {
                e.table_id: {
                    "sheet": e.sheet_name,
                    "title": e.title,
                    "upstream_node_type": e.node_type,
                }
                for e in final_map.all_entries()
            }
            tags = builder.build_table_tags(lookup)

            # --- write artifacts ---
            version = next_summary_model_version(
                run_id, output_base, seed_phase=True
            )
            artifacts.append(
                write_summary_model(builder.summary, run_id, version, output_base)
            )
            artifacts.append(write_agent_outputs(builder.agent_outputs, run_id, output_base))
            artifacts.append(write_table_tags(tags, run_id, output_base))
            artifacts.append(get_ontology_trace_path(run_id, output_base))
            artifacts.append(get_node_type_override_log_path(run_id, output_base))
            # build_errors.jsonl exists too (created on writer open).
            artifacts.append(get_ontology_build_errors_path(run_id, output_base))

            elapsed = time.perf_counter() - t0
            trace.emit(
                RunComplete(
                    ts=_TraceBase.now_ts(),
                    status="complete",
                    agents_run=agents_run,
                    total_elapsed_seconds=elapsed,
                    total_llm_calls=llm_calls_used,
                )
            )
            return OntologyRunResult(
                run_id=run_id,
                version_written=version,
                artifacts=artifacts,
                errors=error_list,
                llm_calls_used=llm_calls_used,
                elapsed_seconds=elapsed,
            )
        finally:
            workbook.close()
    except Exception as exc:
        elapsed = time.perf_counter() - t0
        errors.emit(
            BuildErrorRecord(
                ts=_TraceBase.now_ts(),
                agent=None,
                table_id=None,
                error_type=type(exc).__name__,
                message=str(exc),
            )
        )
        trace.emit(
            RunComplete(
                ts=_TraceBase.now_ts(),
                status="failed",
                agents_run=0,
                total_elapsed_seconds=elapsed,
                total_llm_calls=llm_calls_used,
            )
        )
        error_list.append(f"{type(exc).__name__}: {exc}")
        raise OntologyRunError(
            f"Run failed: {exc}", partial_artifacts=artifacts
        ) from exc
    finally:
        trace.close()
        overrides.close()
        errors.close()


def _resolve_workbook_path(
    run_id: str, output_base: str, explicit: Optional[str | Path]
) -> Path:
    if explicit is not None:
        p = Path(explicit)
        if not p.is_file():
            raise OntologyRunError(f"Workbook not found: {p}")
        return p
    init_dir = get_init_output_path(run_id, output_base)
    candidate = init_dir / "cleaned_workbook.xlsx"
    if candidate.is_file():
        return candidate
    # Fallback: any .xlsx in init_output/.
    if init_dir.is_dir():
        xlsxes = sorted(init_dir.glob("*.xlsx"))
        if xlsxes:
            return xlsxes[0]
    raise OntologyRunError(
        f"Could not resolve workbook for run_id={run_id!r}: pass "
        "workbook_path explicitly or ensure xl_init produced "
        f"{candidate}"
    )


__all__ = ["OntologyRunError", "OntologyRunResult", "run_ontology"]
