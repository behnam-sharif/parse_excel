"""
xl_sheetagent — Semantic enrichment of structural tables.

Public API
----------
run_sheetagent(run_id, output_path=None, config=None)
    V1 or V2 pipeline depending on cfg.v2_enabled.
"""

from __future__ import annotations

from typing import Optional


def run_sheetagent(
    run_id: str,
    output_path: Optional[str] = None,
    config=None,
):
    """Public entry point for the xl_sheetagent stage.

    Dispatches to V1 or V2 pipeline based on cfg.v2_enabled.

    Parameters
    ----------
    run_id : str
        Run identifier. Used to locate upstream stage outputs via core/paths.
    output_path : str, optional
        Override for the output directory. When *None* (production), resolves
        via ``get_sheetagent_output_path(run_id)``.  Use only for tests or
        one-off runs — bypasses the run-root policy.
    config : XlSheetAgentConfig, optional
        Stage configuration. Loaded from YAML if not provided.
    """
    if config is None:
        from core.config import load_config
        from core.stage_config import sheetagent_config_from_pipeline
        config = sheetagent_config_from_pipeline(load_config())

    if getattr(config, "v2_enabled", False):
        return _run_sheetagent_v2(run_id, output_path, config)
    return _run_sheetagent_v1(run_id, output_path, config)


# ---------------------------------------------------------------------------
# V1 pipeline (original)
# ---------------------------------------------------------------------------

def _run_sheetagent_v1(run_id, output_path, config):
    """V1 sequential pipeline (unchanged from previous implementation)."""
    import os
    import time
    from datetime import datetime, timezone

    import openpyxl

    from core.paths import get_sheetagent_output_path

    if output_path is None:
        output_path = str(get_sheetagent_output_path(run_id))

    os.makedirs(output_path, exist_ok=True)

    from .upstream_compat import load_upstream
    from .modules.table_enricher import enrich_all_tables
    from .modules.sheet_coordinator import coordinate_sheet
    from .modules.grammar_builder import build_grammar
    from .modules.text_dominant_handler import detect_text_dominant_sheets
    from .steps.step4_interpretation import run_step4_interpretation
    from .assembly.exporter import write_outputs
    from .schemas.run_manifest import RunManifest
    from .tools.debug_trace import TraceAccumulator, ToolCallRecorder

    t0 = time.time()

    trace = TraceAccumulator()
    recorder = ToolCallRecorder()

    upstream = load_upstream(run_id)

    ws_by_sheet = {}
    ws_formula_by_sheet = {}
    wb = None
    wb_formula = None
    try:
        wb = openpyxl.load_workbook(upstream.workbook_path, data_only=True, read_only=True)
        for sn in wb.sheetnames:
            ws_by_sheet[sn] = wb[sn]
    except Exception:
        wb = None

    try:
        wb_formula = openpyxl.load_workbook(upstream.workbook_path, data_only=False, read_only=True)
        for sn in wb_formula.sheetnames:
            ws_formula_by_sheet[sn] = wb_formula[sn]
    except Exception:
        wb_formula = None

    grammar = build_grammar(upstream, ws_by_sheet, config)

    text_dominant = detect_text_dominant_sheets(
        upstream, ws_by_sheet, ws_formula_by_sheet, config,
    )

    for table in upstream.table_objects:
        if table.sheet_name in text_dominant:
            pass

    enriched_tables, signals, enrich_debug = enrich_all_tables(
        upstream, config, grammar=grammar, trace=trace,
    )

    for table in enriched_tables:
        td = text_dominant.get(table.sheet_name)
        if td:
            table.sheet_level_text_dominant = True
            table.sheet_text_summary = td.sheet_summary

    enriched_tables, signals, trees_by_sheet, rels_by_sheet, coord_debug = coordinate_sheet(
        enriched_tables, signals, upstream, ws_by_sheet, config,
        grammar=grammar, trace=trace,
    )

    enriched_tables = run_step4_interpretation(
        enriched_tables, ws_by_sheet, ws_formula_by_sheet, grammar, config,
        recorder=recorder, trace=trace,
    )

    for w in (wb, wb_formula):
        if w is not None:
            try:
                w.close()
            except Exception:
                pass

    for t in enriched_tables:
        trace.finalize_table(t.table_id, t.confidence, t.table_category)

    debug_events = enrich_debug + coord_debug

    elapsed = time.time() - t0

    confirmed_patterns = sum(1 for p in grammar.patterns if p.status == "confirmed")
    tables_llm = sum(1 for t in enriched_tables if t.llm_table_category is not None)

    manifest = RunManifest(
        run_id=run_id,
        run_ts=datetime.now(tz=timezone.utc).isoformat(),
        xl_sheetagent_version="0.4.0",
        upstream_schema_versions=upstream.schema_versions,
        config_snapshot={},
        n_tables_input=len(upstream.table_objects),
        n_tables_enriched=len([t for t in enriched_tables if not t.is_context_block]),
        n_context_blocks=len([t for t in enriched_tables if t.is_context_block]),
        n_signals_emitted=len(signals),
        elapsed_seconds=round(elapsed, 2),
        grammar_patterns_confirmed=confirmed_patterns,
        guide_sheets_detected=len(grammar.guide_sheets),
        tables_llm_confirmed=tables_llm,
        text_dominant_sheets=len(text_dominant),
        tool_calls_recorded=recorder.count(),
    )

    write_outputs(
        enriched_tables, signals, manifest, debug_events, output_path, config,
        trees_by_sheet=trees_by_sheet,
        rels_by_sheet=rels_by_sheet,
        grammar=grammar,
        text_dominant=text_dominant,
        trace_accumulator=trace,
        tool_call_recorder=recorder,
    )

    return enriched_tables


# ---------------------------------------------------------------------------
# V2 pipeline (agent-based, parallel, LLM-first)
# ---------------------------------------------------------------------------

def _run_sheetagent_v2(run_id, output_path, config):
    """V2 agent-based pipeline.

    Execution flow per plan_v2.sheetagent.txt Section 3:
      1. load_upstream
      2. Open workbooks (sequential)
      3. detect_text_dominant_sheets (sequential)
      4. run_grammar_workbook_agent (BLOCKING)
      5a. Per sheet (PARALLEL via ThreadPoolExecutor):
            Per table (SEQUENTIAL within sheet):
              run_table_agent -> merge grammar updates
      5b. Collect results per sheet
      5c. run_sheet_coordinator per sheet
      6-8. Assembly + write outputs (sequential)
    """
    import logging
    import os
    import time
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from datetime import datetime, timezone

    import openpyxl

    from core.paths import get_sheetagent_output_path

    from .upstream_compat import load_upstream
    from .agents.grammar_workbook_agent import run_grammar_workbook_agent
    from .agents.table_agent import run_table_agent
    from .agents.sheet_coordinator_agent import run_sheet_coordinator
    from .modules.text_dominant_handler import detect_text_dominant_sheets
    from .assembly.exporter import write_outputs
    from .schemas.run_manifest import RunManifest
    from .tools.debug_trace import TraceAccumulator, ToolCallRecorder

    _log = logging.getLogger("xl_sheetagent.v2")

    if output_path is None:
        output_path = str(get_sheetagent_output_path(run_id))
    os.makedirs(output_path, exist_ok=True)

    t0 = time.time()
    trace = TraceAccumulator()
    recorder = ToolCallRecorder()

    # ---- Step 1: Load upstream ----
    upstream = load_upstream(run_id)

    # ---- Step 2: Open workbooks (sequential, main thread) ----
    ws_by_sheet = {}
    ws_formula_by_sheet = {}
    wb = None
    wb_formula = None
    try:
        wb = openpyxl.load_workbook(upstream.workbook_path, data_only=True, read_only=True)
        for sn in wb.sheetnames:
            ws_by_sheet[sn] = wb[sn]
    except Exception:
        wb = None
    try:
        wb_formula = openpyxl.load_workbook(upstream.workbook_path, data_only=False, read_only=True)
        for sn in wb_formula.sheetnames:
            ws_formula_by_sheet[sn] = wb_formula[sn]
    except Exception:
        wb_formula = None

    # ---- Step 3: Text-dominant detection ----
    text_dominant = detect_text_dominant_sheets(
        upstream, ws_by_sheet, ws_formula_by_sheet, config,
    )

    # ---- Step 4: Grammar workbook agent (BLOCKING) ----
    grammar_result = run_grammar_workbook_agent(
        upstream, ws_by_sheet, ws_formula_by_sheet, config,
    )
    grammar = grammar_result.profile

    _log.info(
        "Grammar workbook: %d samples, %d LLM calls, confidence=%.2f",
        len(grammar_result.sample_tables_used),
        grammar_result.llm_calls_made,
        grammar.confidence,
    )

    # ---- Step 5: Per-sheet processing (PARALLEL) ----
    tables_by_sheet = {}
    for tv in upstream.table_objects:
        tables_by_sheet.setdefault(tv.sheet_name, []).append(tv)

    # V2 counters (accumulated per-sheet for thread safety)
    all_enriched = []
    all_signals = []
    all_debug = []
    trees_by_sheet_out = {}
    rels_by_sheet_out = {}
    sheet_counters = []  # list of per-sheet counter dicts

    def _process_sheet(sheet_name, tables):
        """Process one sheet: per-thread workbook handles, sequential tables,
        then coordinator. Returns (SheetProcessingResult, counters_dict)."""
        counters = {
            "grammar_shortcut": 0,
            "llm_structure": 0,
            "escalated": 0,
            "split_by_llm": 0,
            "grammar_updates": 0,
        }

        # Per-thread workbook handles
        wb_local = openpyxl.load_workbook(
            upstream.workbook_path, data_only=True, read_only=True,
        )
        wb_formula_local = openpyxl.load_workbook(
            upstream.workbook_path, data_only=False, read_only=True,
        )
        ws_local = wb_local[sheet_name]
        ws_formula_local = wb_formula_local[sheet_name]

        # Process each table SEQUENTIALLY
        table_results = []
        for tv in tables:
            result = run_table_agent(
                tv, upstream, ws_local, ws_formula_local,
                grammar, config,
                trace=trace, recorder=recorder,
            )
            table_results.append(result)

            # Merge grammar updates into shared profile (thread-safe)
            for obs in result.grammar_updates:
                grammar.add_observation(obs)
                counters["grammar_updates"] += 1

            # Track V2 counters
            method = result.enriched_table.structure_method
            if method == "grammar_match":
                counters["grammar_shortcut"] += 1
            elif method == "llm_structure":
                counters["llm_structure"] += 1
            if result.escalations:
                counters["escalated"] += 1
            if result.enriched_table.semantic_partitions:
                for p in result.enriched_table.semantic_partitions:
                    if p.partition_type == "v2_llm_split":
                        counters["split_by_llm"] += 1
                        break

        # Run coordinator
        sheet_result = run_sheet_coordinator(
            sheet_name, table_results, upstream, ws_local, grammar, config,
        )

        # Close per-thread handles
        try:
            wb_local.close()
        except Exception:
            pass
        try:
            wb_formula_local.close()
        except Exception:
            pass

        return sheet_result, counters

    def _collect_result(sheet_name, result_and_counters):
        """Merge a sheet's results into the global accumulators."""
        result, counters = result_and_counters
        all_enriched.extend(result.enriched_tables)
        all_signals.extend(result.signals)
        all_debug.extend(result.debug_events)
        if result.tree is not None:
            trees_by_sheet_out[sheet_name] = result.tree
        if result.rels is not None:
            rels_by_sheet_out[sheet_name] = result.rels
        sheet_counters.append(counters)

    # Execute sheets in parallel
    max_workers = min(config.max_parallel_sheets, len(tables_by_sheet))
    max_workers = max(1, max_workers)

    if max_workers == 1 or len(tables_by_sheet) <= 1:
        for sheet_name, tables in tables_by_sheet.items():
            if sheet_name not in ws_by_sheet:
                _log.warning("Sheet %r not in workbook, skipping", sheet_name)
                continue
            _collect_result(sheet_name, _process_sheet(sheet_name, tables))
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {}
            for sheet_name, tables in tables_by_sheet.items():
                if sheet_name not in ws_by_sheet:
                    _log.warning("Sheet %r not in workbook, skipping", sheet_name)
                    continue
                fut = executor.submit(_process_sheet, sheet_name, tables)
                futures[fut] = sheet_name

            for fut in as_completed(futures):
                sheet_name = futures[fut]
                try:
                    _collect_result(sheet_name, fut.result())
                except Exception as exc:
                    _log.error("Sheet %r failed: %s", sheet_name, exc)

    # Aggregate per-sheet counters
    tables_grammar_shortcut = sum(c["grammar_shortcut"] for c in sheet_counters)
    tables_llm_structure = sum(c["llm_structure"] for c in sheet_counters)
    tables_escalated = sum(c["escalated"] for c in sheet_counters)
    tables_split_by_llm = sum(c["split_by_llm"] for c in sheet_counters)
    grammar_updates_count = sum(c["grammar_updates"] for c in sheet_counters)

    # Apply text-dominant metadata
    for table in all_enriched:
        td = text_dominant.get(table.sheet_name)
        if td:
            table.sheet_level_text_dominant = True
            table.sheet_text_summary = td.sheet_summary

    # Close main-thread workbooks
    for w in (wb, wb_formula):
        if w is not None:
            try:
                w.close()
            except Exception:
                pass

    # Finalize traces
    for t in all_enriched:
        trace.finalize_table(t.table_id, t.confidence, t.table_category)

    elapsed = time.time() - t0

    # ---- Build manifest ----
    confirmed_patterns = len([
        p for p in grammar.patterns if p.get("status") == "confirmed"
    ])
    tables_llm = sum(1 for t in all_enriched if t.llm_table_category is not None)

    manifest = RunManifest(
        run_id=run_id,
        run_ts=datetime.now(tz=timezone.utc).isoformat(),
        xl_sheetagent_version="0.5.0",
        upstream_schema_versions=upstream.schema_versions,
        config_snapshot={},
        n_tables_input=len(upstream.table_objects),
        n_tables_enriched=len([t for t in all_enriched if not t.is_context_block]),
        n_context_blocks=len([t for t in all_enriched if t.is_context_block]),
        n_signals_emitted=len(all_signals),
        elapsed_seconds=round(elapsed, 2),
        grammar_patterns_confirmed=confirmed_patterns,
        guide_sheets_detected=0,
        tables_llm_confirmed=tables_llm,
        text_dominant_sheets=len(text_dominant),
        tool_calls_recorded=recorder.count(),
        # V2 metadata
        v2_mode=True,
        grammar_workbook_tables=len(grammar_result.sample_tables_used),
        tables_grammar_shortcut=tables_grammar_shortcut,
        tables_llm_structure=tables_llm_structure,
        tables_escalated=tables_escalated,
        tables_split_by_llm=tables_split_by_llm,
        parallel_sheets=max_workers,
        grammar_updates_during_run=grammar_updates_count,
    )

    # ---- Write outputs ----
    write_outputs(
        all_enriched, all_signals, manifest, all_debug, output_path, config,
        trees_by_sheet=trees_by_sheet_out,
        rels_by_sheet=rels_by_sheet_out,
        grammar=grammar,
        text_dominant=text_dominant,
        trace_accumulator=trace,
        tool_call_recorder=recorder,
    )

    _log.info(
        "V2 complete: %d tables enriched, %d grammar shortcuts, "
        "%d LLM structure, %d escalated, %d split, %.1fs",
        len(all_enriched), tables_grammar_shortcut,
        tables_llm_structure, tables_escalated,
        tables_split_by_llm, elapsed,
    )

    return all_enriched
