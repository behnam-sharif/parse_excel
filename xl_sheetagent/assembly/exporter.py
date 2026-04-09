"""
exporter.py — Write xl_sheetagent outputs to disk.

V1A outputs:
    enriched_tables.json
    summaries.json        (stub: table_id + title + category)
    run_manifest.json
    debug_log.jsonl

V1B additions:
    sheet_object_trees/{sheet}_tree.json
    relationship_graphs/{sheet}_rels.json
    streamlit_view_bundle.json

V1C additions:
    grammar_snapshot.json
    grammar_discovery_report.json
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict
from typing import Any, Dict, List, Optional

from ..schemas.enriched_table import EnrichedTableObject
from ..schemas.signals import TableSignal
from ..schemas.run_manifest import RunManifest
from ..schemas.sheet_object_tree import (
    RelationshipGraph,
    SheetObjectTree,
)


def _serialise_enriched(table: EnrichedTableObject) -> Dict[str, Any]:
    """Convert an EnrichedTableObject to a JSON-safe dict."""
    d = asdict(table)
    return d


def write_outputs(
    enriched_tables: List[EnrichedTableObject],
    signals: List[TableSignal],
    manifest: RunManifest,
    debug_events: List[Dict[str, Any]],
    output_path: str,
    cfg,
    *,
    trees_by_sheet: Optional[Dict[str, SheetObjectTree]] = None,
    rels_by_sheet: Optional[Dict[str, RelationshipGraph]] = None,
    grammar=None,
    text_dominant: Optional[Dict[str, Any]] = None,
    trace_accumulator=None,
    tool_call_recorder=None,
) -> None:
    """Write all outputs to *output_path*/."""
    os.makedirs(output_path, exist_ok=True)

    # enriched_tables.json
    tables_out = [_serialise_enriched(t) for t in enriched_tables]
    _write_json(os.path.join(output_path, "enriched_tables.json"), tables_out)

    # summaries.json (V1C: includes LLM summary + rag_fields)
    summaries = [
        {
            "table_id": t.table_id,
            "sheet_name": t.sheet_name,
            "title": t.title,
            "table_category": t.table_category,
            "table_category_confidence": t.table_category_confidence,
            "is_context_block": t.is_context_block,
            "llm_summary": t.llm_summary,
            "rag_fields": t.rag_fields,
            "section_path": t.section_path,
        }
        for t in enriched_tables
    ]
    _write_json(os.path.join(output_path, "summaries.json"), summaries)

    # run_manifest.json
    _write_json(os.path.join(output_path, "run_manifest.json"), asdict(manifest))

    # debug_log.jsonl
    debug_path = os.path.join(output_path, "debug_log.jsonl")
    with open(debug_path, "w", encoding="utf-8") as f:
        # Write scoring events
        for ev in debug_events:
            f.write(json.dumps(ev, default=str) + "\n")
        # Write signal events
        for sig in signals:
            f.write(json.dumps({
                "event_type": "signal",
                "signal_type": sig.signal_type,
                "source_table_id": sig.source_table_id,
                "confidence": sig.confidence,
                "ambiguity_class": sig.ambiguity_class,
            }, default=str) + "\n")

    # --- V1B outputs ---
    if trees_by_sheet:
        _write_trees(output_path, trees_by_sheet)

    if rels_by_sheet:
        _write_rels(output_path, rels_by_sheet)

    if trees_by_sheet:
        _write_view_bundle(
            output_path, manifest, trees_by_sheet,
            enriched_tables=enriched_tables,
            grammar=grammar,
            text_dominant=text_dominant,
            debug_level=getattr(cfg, "debug_level", "minimal"),
        )

    # --- V1D outputs ---
    tree_png_count = 0
    if trees_by_sheet:
        try:
            tree_png_count = _write_tree_pngs(
                output_path, trees_by_sheet, rels_by_sheet or {}, enriched_tables, cfg,
            )
        except Exception as exc:
            import logging
            logging.getLogger("xl_sheetagent.exporter").warning(
                "Tree PNG rendering failed: %s", exc,
            )

    # V1D: Debug trace outputs
    debug_traces_written = 0
    if trace_accumulator is not None or tool_call_recorder is not None:
        try:
            from ..tools.debug_trace import write_debug_outputs
            debug_traces_written = write_debug_outputs(
                output_path, trace_accumulator, tool_call_recorder,
                debug_level=getattr(cfg, "debug_level", "minimal"),
            )
        except Exception as exc:
            import logging
            logging.getLogger("xl_sheetagent.exporter").warning(
                "Debug trace writing failed: %s", exc,
            )

    # --- V1C outputs ---
    if grammar is not None:
        _write_grammar(output_path, grammar)

    if text_dominant:
        _write_text_dominant(output_path, text_dominant)


def _write_trees(output_path: str, trees: Dict[str, SheetObjectTree]) -> None:
    """Write per-sheet tree JSON files."""
    tree_dir = os.path.join(output_path, "sheet_object_trees")
    os.makedirs(tree_dir, exist_ok=True)

    for sheet_name, tree in trees.items():
        safe_name = sheet_name.replace("/", "_").replace("\\", "_").replace(" ", "_")
        path = os.path.join(tree_dir, f"{safe_name}_tree.json")
        _write_json(path, asdict(tree))


def _write_rels(output_path: str, rels: Dict[str, RelationshipGraph]) -> None:
    """Write per-sheet relationship graph JSON files."""
    rels_dir = os.path.join(output_path, "relationship_graphs")
    os.makedirs(rels_dir, exist_ok=True)

    for sheet_name, graph in rels.items():
        safe_name = sheet_name.replace("/", "_").replace("\\", "_").replace(" ", "_")
        path = os.path.join(rels_dir, f"{safe_name}_rels.json")
        _write_json(path, asdict(graph))


def _write_view_bundle(
    output_path: str,
    manifest: RunManifest,
    trees: Dict[str, SheetObjectTree],
    enriched_tables: Optional[List[EnrichedTableObject]] = None,
    grammar=None,
    text_dominant: Optional[Dict[str, Any]] = None,
    debug_level: str = "minimal",
) -> None:
    """Write streamlit_view_bundle.json for the Streamlit viewer (V1D)."""
    # Build per-sheet table summaries
    tables_by_sheet: Dict[str, list] = {}
    if enriched_tables:
        for t in enriched_tables:
            tables_by_sheet.setdefault(t.sheet_name, []).append({
                "table_id": t.table_id,
                "title": t.title,
                "table_category": t.table_category,
                "table_category_confidence": t.table_category_confidence,
                "section_path": t.section_path,
                "llm_summary": t.llm_summary,
                "is_context_block": t.is_context_block,
            })

    sheets = []
    for sheet_name, tree in trees.items():
        safe_name = sheet_name.replace("/", "_").replace("\\", "_").replace(" ", "_")
        sheet_entry: Dict[str, Any] = {
            "sheet_name": sheet_name,
            "tree_path": f"sheet_object_trees/{safe_name}_tree.json",
            "rels_path": f"relationship_graphs/{safe_name}_rels.json",
            "tree_png_path": f"sheet_tree_visuals/{safe_name}_tree.png",
            "n_sections": tree.n_sections,
            "n_tables": tree.n_tables,
            "tables": tables_by_sheet.get(sheet_name, []),
        }
        # Text-dominant flag
        if text_dominant and sheet_name in text_dominant:
            sheet_entry["is_text_dominant"] = True
        sheets.append(sheet_entry)

    bundle: Dict[str, Any] = {
        "run_id": manifest.run_id,
        "xl_sheetagent_version": manifest.xl_sheetagent_version,
        "sheets": sheets,
        "grammar_snapshot_path": "grammar_snapshot.json" if grammar else None,
        "grammar_report_path": "grammar_discovery_report.json" if grammar else None,
        "debug_log_path": "debug_log.jsonl",
    }

    if debug_level == "full":
        bundle["debug_traces_path"] = "debug/"

    _write_json(os.path.join(output_path, "streamlit_view_bundle.json"), bundle)


def _write_grammar(output_path: str, grammar) -> None:
    """Write grammar_snapshot.json and grammar_discovery_report.json."""
    snapshot = asdict(grammar)
    _write_json(os.path.join(output_path, "grammar_snapshot.json"), snapshot)

    # Discovery report: summary of patterns
    confirmed = [p for p in grammar.patterns if p.status == "confirmed"]
    observed = [p for p in grammar.patterns if p.status == "observed"]
    report = {
        "discovery_method": grammar.discovery_method,
        "guide_sheets": grammar.guide_sheets,
        "guide_sheet_confidence": grammar.guide_sheet_confidence,
        "sample_sheets": grammar.sample_sheets,
        "total_patterns": len(grammar.patterns),
        "confirmed_patterns": len(confirmed),
        "observed_patterns": len(observed),
        "confirmed_by_type": {},
        "observed_by_type": {},
    }
    for p in confirmed:
        report["confirmed_by_type"][p.pattern_type] = (
            report["confirmed_by_type"].get(p.pattern_type, 0) + 1
        )
    for p in observed:
        report["observed_by_type"][p.pattern_type] = (
            report["observed_by_type"].get(p.pattern_type, 0) + 1
        )
    _write_json(os.path.join(output_path, "grammar_discovery_report.json"), report)


def _write_tree_pngs(
    output_path: str,
    trees_by_sheet: Dict[str, SheetObjectTree],
    rels_by_sheet: Dict[str, RelationshipGraph],
    enriched_tables: List[EnrichedTableObject],
    cfg,
) -> int:
    """Render tree PNGs for all sheets. Returns count of PNGs written."""
    from .tree_renderer import render_all_trees

    results = render_all_trees(trees_by_sheet, rels_by_sheet, output_path, cfg)
    return len(results)


def _write_text_dominant(output_path: str, text_dominant: Dict[str, Any]) -> None:
    """Write text_dominant_sheets.json."""
    out = {}
    for sheet_name, td_result in text_dominant.items():
        out[sheet_name] = {
            "is_text_dominant": td_result.is_text_dominant,
            "text_ratio": td_result.text_ratio,
            "classification": td_result.classification,
            "sheet_summary": td_result.sheet_summary,
            "n_chunks": len(td_result.chunks),
        }
    _write_json(os.path.join(output_path, "text_dominant_sheets.json"), out)


def _write_json(path: str, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
