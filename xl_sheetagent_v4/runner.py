"""
Phase 1 Runner for xl_sheetagent_v4.

Main entry point for table intelligence analysis.
"""

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional

from .agents import TableIntelligenceAgent
from .tools import load_workbook_safe, get_sheet_by_name
from .schemas.enriched_table import EnrichedTable, V4RunResult

logger = logging.getLogger(__name__)


def run_v4_analysis(
    run_id: str,
    output_base: Path,
    llm_client: Optional[Any] = None,
    model: str = "gpt-4o",
    max_recursion_depth: int = 3,
) -> V4RunResult:
    """
    Run Phase 1 table intelligence analysis.

    Parameters
    ----------
    run_id : str
        Run ID (e.g., "streamlit_run_simple")
    output_base : Path
        Base output directory (e.g., "data/output")
    llm_client : Any, optional
        LLM client instance
    model : str
        LLM model name
    max_recursion_depth : int
        Max depth for recursive child analysis

    Returns
    -------
    V4RunResult
        Analysis results

    Workflow
    --------
    1. Load xl_table output (tables_per_sheet.json)
    2. Load cleaned workbook
    3. For each table:
       - Read table data from Excel
       - Call TableIntelligenceAgent to analyze
       - Recursively analyze children
    4. Export enriched results
    """

    run_dir = output_base / run_id
    if not run_dir.exists():
        raise FileNotFoundError(f"Run directory not found: {run_dir}")

    logger.info(f"Starting V4 analysis for run: {run_id}")

    # Step 1: Load xl_table output
    table_output_path = run_dir / "table_output" / "tables_per_sheet.json"
    if not table_output_path.exists():
        raise FileNotFoundError(
            f"xl_table output not found: {table_output_path}\n"
            "Run xl_table pipeline first."
        )

    with open(table_output_path, "r", encoding="utf-8") as f:
        tables_per_sheet = json.load(f)

    logger.info(f"Loaded xl_table output: {len(tables_per_sheet)} sheets")

    # Step 2: Load cleaned workbook
    workbook_path = run_dir / "init_output" / "cleaned_workbook.xlsx"
    if not workbook_path.exists():
        raise FileNotFoundError(
            f"Cleaned workbook not found: {workbook_path}\n"
            "Run xl_init pipeline first."
        )

    wb = load_workbook_safe(workbook_path, data_only=True)
    logger.info(f"Loaded workbook: {workbook_path}")

    # Step 3: Create agent
    agent = TableIntelligenceAgent(
        llm_client=llm_client,
        model=model,
        max_tokens=2000,
        temperature=0.0,
    )

    # Step 4: Analyze tables
    start_time = datetime.utcnow()

    sheets_results = []
    n_tables_analyzed = 0
    n_children_detected = 0

    for sheet_data in tables_per_sheet:
        sheet_name = sheet_data.get("sheet_name")
        tables = sheet_data.get("tables", [])

        if not tables:
            logger.info(f"Skipping sheet '{sheet_name}' (no tables)")
            continue

        logger.info(f"Analyzing sheet '{sheet_name}' ({len(tables)} tables)")

        # Get worksheet
        try:
            ws = get_sheet_by_name(wb, sheet_name)
        except ValueError as e:
            logger.warning(f"Sheet '{sheet_name}' not found: {e}")
            continue

        # Analyze each table
        sheet_enriched_tables = []
        for table in tables:
            table_id = table.get("table_id", f"unknown_{n_tables_analyzed}")
            bbox = table.get("bbox")

            if not bbox:
                logger.warning(f"Table {table_id} has no bbox, skipping")
                continue

            try:
                enriched = agent.analyze_table(
                    table_id=table_id,
                    sheet_name=sheet_name,
                    bbox=bbox,
                    ws=ws,
                    depth=0,
                    max_depth=max_recursion_depth,
                )

                sheet_enriched_tables.append(enriched)
                n_tables_analyzed += 1
                n_children_detected += len(enriched.children)

            except Exception as e:
                logger.error(
                    f"Error analyzing table {table_id}: {e}",
                    exc_info=True,
                )
                continue

        # Add to results
        sheets_results.append({
            "sheet_name": sheet_name,
            "n_tables": len(sheet_enriched_tables),
            "tables": [
                _serialize_enriched_table(t)
                for t in sheet_enriched_tables
            ],
        })

    end_time = datetime.utcnow()
    elapsed = (end_time - start_time).total_seconds()

    # Step 5: Build result
    result = V4RunResult(
        run_id=run_id,
        run_ts=start_time.isoformat(),
        version="4.0.0",
        sheets=sheets_results,
        n_tables_analyzed=n_tables_analyzed,
        n_children_detected=n_children_detected,
        n_llm_calls=agent.llm_calls,
        total_llm_tokens=agent.total_tokens,
        elapsed_seconds=elapsed,
    )

    logger.info(
        f"V4 analysis complete: "
        f"{n_tables_analyzed} tables, "
        f"{n_children_detected} children, "
        f"{agent.llm_calls} LLM calls, "
        f"{elapsed:.1f}s"
    )

    # Step 6: Export
    v4_output_dir = run_dir / "v4_output"
    v4_output_dir.mkdir(parents=True, exist_ok=True)

    export_path = v4_output_dir / "v4_results.json"
    export_v4_results(result, export_path)

    result.json_export_path = str(export_path)

    return result


def export_v4_results(result: V4RunResult, output_path: Path) -> None:
    """
    Export V4 results as JSON.

    Parameters
    ----------
    result : V4RunResult
        Analysis results
    output_path : Path
        Output JSON file path
    """

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Serialize
    result_dict = {
        "run_id": result.run_id,
        "run_ts": result.run_ts,
        "version": result.version,
        "sheets": result.sheets,
        "statistics": {
            "n_tables_analyzed": result.n_tables_analyzed,
            "n_children_detected": result.n_children_detected,
            "n_llm_calls": result.n_llm_calls,
            "total_llm_tokens": result.total_llm_tokens,
            "elapsed_seconds": result.elapsed_seconds,
        },
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(result_dict, f, indent=2, ensure_ascii=False)

    logger.info(f"Exported V4 results: {output_path}")


def _serialize_enriched_table(table: EnrichedTable) -> Dict[str, Any]:
    """
    Serialize EnrichedTable to dict.

    Parameters
    ----------
    table : EnrichedTable
        Enriched table

    Returns
    -------
    Dict[str, Any]
        Serialized dict
    """

    metadata_dict = None
    if table.metadata:
        metadata_dict = {
            "title": table.metadata.title,
            "title_confidence": table.metadata.title_confidence,
            "header": table.metadata.header,
            "header_confidence": table.metadata.header_confidence,
            "header_location": table.metadata.header_location,
            "row_names": table.metadata.row_names,
            "row_names_confidence": table.metadata.row_names_confidence,
            "row_names_location": table.metadata.row_names_location,
            "data_region": table.metadata.data_region,
            "table_type": table.metadata.table_type,
            "analysis_confidence": table.metadata.analysis_confidence,
            "reasoning": table.metadata.reasoning,
        }

    children_list = []
    for child in table.children:
        child_dict = {
            "section_name": child.section_name,
            "bbox": child.bbox,
            "row_range": child.row_range,
            "confidence": child.confidence,
            "reasoning": child.reasoning,
            "summary": child.summary,
            # Recursively serialize child metadata
            "metadata": _serialize_metadata(child.metadata) if child.metadata else None,
            # Recursively serialize grandchildren
            "children": [
                _serialize_child(gc) for gc in child.children
            ],
        }
        children_list.append(child_dict)

    return {
        "table_id": table.table_id,
        "sheet_name": table.sheet_name,
        "bbox": table.bbox,
        "bbox_coords": table.bbox_coords,
        "metadata": metadata_dict,
        "summary": table.summary,
        "summary_confidence": table.summary_confidence,
        "children": children_list,
        "depth": table.depth,
        "ontology_tags": table.ontology_tags,
        "ontology_confidence": table.ontology_confidence,
    }


def _serialize_metadata(metadata) -> Dict[str, Any]:
    """Serialize TableMetadata."""
    return {
        "title": metadata.title,
        "title_confidence": metadata.title_confidence,
        "header": metadata.header,
        "header_confidence": metadata.header_confidence,
        "header_location": metadata.header_location,
        "row_names": metadata.row_names,
        "row_names_confidence": metadata.row_names_confidence,
        "row_names_location": metadata.row_names_location,
        "data_region": metadata.data_region,
        "table_type": metadata.table_type,
        "analysis_confidence": metadata.analysis_confidence,
        "reasoning": metadata.reasoning,
    }


def _serialize_child(child) -> Dict[str, Any]:
    """Serialize ChildTable recursively."""
    return {
        "section_name": child.section_name,
        "bbox": child.bbox,
        "row_range": child.row_range,
        "confidence": child.confidence,
        "reasoning": child.reasoning,
        "summary": child.summary,
        "metadata": _serialize_metadata(child.metadata) if child.metadata else None,
        "children": [_serialize_child(gc) for gc in child.children],
    }
