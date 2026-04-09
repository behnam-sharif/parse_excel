"""
xl_sheetagent — Semantic enrichment of structural tables.

Public API
----------
run_sheetagent(run_id, output_path=None, config=None)
"""

from __future__ import annotations

from typing import Optional


def run_sheetagent(
    run_id: str,
    output_path: Optional[str] = None,
    config=None,
):
    """Public entry point for the xl_sheetagent stage.

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
    import os
    import time
    from datetime import datetime, timezone

    import openpyxl

    from core.paths import (
        get_init_output_path,
        get_parse_output_path,
        get_sheetagent_output_path,
        get_table_output_path,
    )

    if config is None:
        from core.config import load_config
        from core.stage_config import sheetagent_config_from_pipeline

        config = sheetagent_config_from_pipeline(load_config())

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

    # V1D: Debug trace infrastructure
    trace = TraceAccumulator()
    recorder = ToolCallRecorder()

    upstream = load_upstream(run_id)

    # Open workbooks: data_only=True (cell values) + data_only=False (formulas)
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

    # --- Phase 0: Grammar discovery (V1C) ---
    grammar = build_grammar(upstream, ws_by_sheet, config)

    # --- Phase 0b: Text-dominant sheet detection (V1C) ---
    text_dominant = detect_text_dominant_sheets(
        upstream, ws_by_sheet, ws_formula_by_sheet, config,
    )

    # Apply text-dominant flags to tables
    for table in upstream.table_objects:
        if table.sheet_name in text_dominant:
            pass  # Will be applied to enriched tables below

    # --- Phase 1: Enrichment (Steps 0-3 + V1C confirmations) ---
    enriched_tables, signals, enrich_debug = enrich_all_tables(
        upstream, config, grammar=grammar, trace=trace,
    )

    # Apply text-dominant metadata to enriched tables
    for table in enriched_tables:
        td = text_dominant.get(table.sheet_name)
        if td:
            table.sheet_level_text_dominant = True
            table.sheet_text_summary = td.sheet_summary

    # --- Phase 2: Coordinator (grammar-aware scoring) ---
    enriched_tables, signals, trees_by_sheet, rels_by_sheet, coord_debug = coordinate_sheet(
        enriched_tables, signals, upstream, ws_by_sheet, config,
        grammar=grammar, trace=trace,
    )

    # --- Phase 3: Step 4 LLM interpretation (V1C — per partition) ---
    enriched_tables = run_step4_interpretation(
        enriched_tables, ws_by_sheet, ws_formula_by_sheet, grammar, config,
        recorder=recorder, trace=trace,
    )

    # Close workbooks
    for w in (wb, wb_formula):
        if w is not None:
            try:
                w.close()
            except Exception:
                pass

    # Finalize traces
    for t in enriched_tables:
        trace.finalize_table(t.table_id, t.confidence, t.table_category)

    # Merge debug events
    debug_events = enrich_debug + coord_debug

    elapsed = time.time() - t0

    # Compute V1C manifest metadata
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
