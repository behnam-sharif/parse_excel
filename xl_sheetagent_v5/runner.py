"""
xl_sheetagent_v5/runner.py — Entry point with sheet-level parallelization.

V3a is the splitter + hierarchy builder. V5 is the enricher.
Sheets processed in parallel via ThreadPoolExecutor;
within a sheet, tables processed sequentially (depth-first).
"""

from __future__ import annotations

import logging
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import openpyxl

from .config import XlSheetAgentV5Config
from .schemas import V5RunResult, V5TableResult
from .table_agent import analyze_table
from .formatting_agent import extract_table_formatting
from .exporter import export_results

logger = logging.getLogger(__name__)


def run_v5_analysis(
    run_id: str,
    output_base: str = "data/output",
    config: Optional[XlSheetAgentV5Config] = None,
    llm_client: Any = None,
    workbook_path: Optional[str] = None,
    sheets: Optional[List[str]] = None,
    all_bboxes: Optional[Dict[str, List[Dict[str, int]]]] = None,
    all_cut_scores: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> List[V5RunResult]:
    """
    Top-level V5 entry point.

    Parameters
    ----------
    run_id : str
        Run identifier.
    output_base : str
        Base output directory.
    config : XlSheetAgentV5Config, optional
        V5 config (loaded from YAML if not provided).
    llm_client : Any, optional
        OpenAI/Anthropic client for LLM calls.
    workbook_path : str, optional
        Path to workbook. If None, looks in run output for init artifacts.
    sheets : List[str], optional
        Sheet names to process. If None, processes all approved sheets.
    all_bboxes : Dict[str, List], optional
        Pre-computed bboxes per sheet. If None, loads from V3a/parse output.
    all_cut_scores : Dict[str, List], optional
        Cut scores per sheet. If None, loads from parse output.

    Returns
    -------
    List[V5RunResult]
    """
    if config is None:
        from core.config import load_config
        from core.stage_config import sheetagent_v5_config_from_pipeline
        pc = load_config()
        config = sheetagent_v5_config_from_pipeline(pc)

    from core.paths import get_v5_sheetagent_output_path
    output_dir = get_v5_sheetagent_output_path(run_id, output_base)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Resolve workbook path
    if workbook_path is None:
        workbook_path = _find_workbook(run_id, output_base)

    if workbook_path is None or not Path(workbook_path).exists():
        logger.error("Workbook not found: %s", workbook_path)
        return []

    # Load bboxes if not provided
    if all_bboxes is None:
        all_bboxes = _load_bboxes(run_id, output_base)

    if all_cut_scores is None:
        all_cut_scores = _load_cut_scores(run_id, output_base)

    # Determine sheets
    if sheets is None:
        sheets = list(all_bboxes.keys()) if all_bboxes else []

    if not sheets:
        logger.warning("No sheets to process")
        return []

    logger.info(
        "V5 analysis: %d sheets, workbook=%s", len(sheets), workbook_path,
    )

    # Audit accumulator (thread-safe via per-sheet dicts merged after)
    audit_records: Dict[str, List] = {}

    # Dispatch sheets in parallel
    results: List[V5RunResult] = []

    if config.max_workers <= 1 or len(sheets) == 1:
        # Sequential for single sheet or single worker
        for sheet_name in sheets:
            bboxes = all_bboxes.get(sheet_name, [])
            cuts = all_cut_scores.get(sheet_name, [])
            result = _run_v5_sheet(
                workbook_path, sheet_name, bboxes, cuts,
                config, llm_client, run_id, audit_records,
            )
            results.append(result)
    else:
        with ThreadPoolExecutor(max_workers=config.max_workers) as pool:
            futures = {}
            for sheet_name in sheets:
                bboxes = all_bboxes.get(sheet_name, [])
                cuts = all_cut_scores.get(sheet_name, [])
                # Each thread gets its own audit dict, merged later
                sheet_audit: Dict[str, List] = {}
                fut = pool.submit(
                    _run_v5_sheet,
                    workbook_path, sheet_name, bboxes, cuts,
                    config, llm_client, run_id, sheet_audit,
                )
                futures[fut] = (sheet_name, sheet_audit)

            for fut in as_completed(futures):
                sheet_name, sheet_audit = futures[fut]
                try:
                    result = fut.result()
                    results.append(result)
                    # Merge audit records
                    for key, entries in sheet_audit.items():
                        audit_records.setdefault(key, []).extend(entries)
                except Exception as exc:
                    logger.error("Sheet %s failed: %s", sheet_name, exc, exc_info=True)
                    results.append(V5RunResult(
                        run_id=run_id,
                        sheet_name=sheet_name,
                        errors=[str(exc)],
                    ))

    # Export
    export_results(results, output_dir, config, audit_records)

    total_tables = sum(len(r.tables) for r in results)
    total_tokens = sum(r.token_count for r in results)
    logger.info(
        "V5 complete: %d sheets, %d tables, %d tokens",
        len(results), total_tables, total_tokens,
    )

    return results


# ---------------------------------------------------------------------------
# Per-sheet worker
# ---------------------------------------------------------------------------

def _run_v5_sheet(
    workbook_path: str,
    sheet_name: str,
    bboxes: List[Dict[str, int]],
    cut_scores: List[Dict[str, Any]],
    config: XlSheetAgentV5Config,
    llm_client: Any,
    run_id: str,
    audit_records: Dict[str, List],
) -> V5RunResult:
    """
    Per-sheet worker. Opens its own workbook handle (thread safety).
    Processes tables sequentially (depth-first through V3a hierarchy).
    """
    logger.info("V5 processing sheet: %s (%d bboxes)", sheet_name, len(bboxes))

    if len(bboxes) > config.max_tables_per_sheet_warn:
        logger.warning(
            "Sheet %s has %d bboxes (exceeds warning threshold %d)",
            sheet_name, len(bboxes), config.max_tables_per_sheet_warn,
        )

    # Open own workbook handle
    try:
        wb = openpyxl.load_workbook(workbook_path, data_only=True, read_only=False)
    except Exception as exc:
        logger.error("Failed to open workbook for sheet %s: %s", sheet_name, exc)
        return V5RunResult(run_id=run_id, sheet_name=sheet_name, errors=[str(exc)])

    try:
        ws = wb[sheet_name]
    except KeyError:
        wb.close()
        msg = f"Sheet '{sheet_name}' not found in workbook"
        logger.error(msg)
        return V5RunResult(run_id=run_id, sheet_name=sheet_name, errors=[msg])

    llm_call_counter = [0]
    total_tokens = 0
    errors: List[str] = []
    all_tables: List[V5TableResult] = []

    bboxes = _sort_bboxes_parent_before_child(bboxes)

    for bbox_idx, bbox in enumerate(bboxes):
        try:
            table_result = _process_one_bbox(
                ws, bbox, sheet_name, bbox_idx,
                all_tables, cut_scores, config,
                llm_client, llm_call_counter, audit_records,
            )
            all_tables.append(table_result)

            # Formatting (runs after semantic assignment)
            if config.extract_formatting:
                fmt = extract_table_formatting(ws, bbox, table_result, config)
                table_result.formatting = fmt

        except Exception as exc:
            logger.error(
                "Failed to process bbox %d on %s: %s", bbox_idx, sheet_name, exc,
                exc_info=True,
            )
            errors.append(f"bbox_{bbox_idx}: {exc}")

    # Reconstruct V3a hierarchy tree (populate children lists)
    table_by_id = {t.table_id: t for t in all_tables}
    for t in all_tables:
        if t.parent_table_id and t.parent_table_id in table_by_id:
            table_by_id[t.parent_table_id].children.append(t)

    # Resolution stage: merge parent + children into ResolvedTables
    resolved = []
    if config.enable_resolution:
        try:
            from .table_resolver import resolve_sheet_tables
            resolved = resolve_sheet_tables(
                all_tables, ws, config, llm_client,
                cut_scores=cut_scores, audit_records=audit_records,
                sheet_name=sheet_name,
            )
        except Exception as exc:
            logger.error("Resolution stage failed for %s: %s", sheet_name, exc, exc_info=True)
            errors.append(f"resolution: {exc}")

    wb.close()

    return V5RunResult(
        run_id=run_id,
        sheet_name=sheet_name,
        tables=all_tables,
        resolved_tables=resolved,
        token_count=total_tokens,
        errors=errors,
    )


def _process_one_bbox(
    ws: Any,
    bbox: Dict[str, int],
    sheet_name: str,
    bbox_idx: int,
    all_tables: List[V5TableResult],
    cut_scores: List[Dict[str, Any]],
    config: XlSheetAgentV5Config,
    llm_client: Any,
    llm_call_counter: List[int],
    audit_records: Dict[str, List],
) -> V5TableResult:
    """Process a single bbox: read grid, run V3a-style analysis, enrich with V5."""
    r_top = bbox["r1"]
    c_left = bbox["c1"]
    r_bot = bbox["r2"]
    c_right = bbox["c2"]

    # Read grid from worksheet
    grid, types = _read_cell_grid(ws, r_top, c_left, r_bot, c_right)

    # Get bold rows and merge info
    bold_rows = _detect_bold_rows(ws, r_top, c_left, r_bot, c_right)
    merge_info = _get_merge_info(ws, r_top, c_left, r_bot, c_right)

    # Structural summary
    structural_summary = _compute_structural_summary(grid, types, bold_rows)

    # Determine hierarchy role (standalone if no parent info in bbox)
    hierarchy_role = bbox.get("hierarchy_role", "standalone")
    parent_table_id = bbox.get("parent_table_id")
    split_depth = bbox.get("split_depth", 0)

    table_id = bbox.get("v3a_table_id") or f"{sheet_name}_t{bbox_idx}"

    return analyze_table(
        table_id=table_id,
        bbox=bbox,
        grid=grid,
        types=types,
        hierarchy_role=hierarchy_role,
        parent_table_id=parent_table_id,
        split_depth=split_depth,
        sheet_name=sheet_name,
        merge_info=merge_info,
        bold_rows=bold_rows,
        all_tables_in_sheet=all_tables,
        structural_summary=structural_summary,
        config=config,
        llm_client=llm_client,
        llm_call_counter=llm_call_counter,
        audit_records=audit_records,
        cut_scores=cut_scores,
    )


def _sort_bboxes_parent_before_child(bboxes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Process parents before children so header inheritance sees donor tables."""
    if len(bboxes) <= 1:
        return bboxes
    n_with_id = sum(1 for b in bboxes if b.get("v3a_table_id"))
    if n_with_id != len(bboxes):
        return sorted(
            bboxes,
            key=lambda b: (b.get("split_depth", 0), b.get("r1", 0), b.get("c1", 0)),
        )
    return sorted(
        bboxes,
        key=lambda b: (b.get("split_depth", 0), b.get("r1", 0), b.get("c1", 0)),
    )


# ---------------------------------------------------------------------------
# Cell reading (openpyxl)
# ---------------------------------------------------------------------------

def _read_cell_grid(
    ws: Any, r_top: int, c_left: int, r_bot: int, c_right: int,
) -> Tuple[List[List[Any]], List[List[str]]]:
    """Read cell values and classify types. Coords are 0-indexed inclusive."""
    grid = []
    types = []
    for r in range(r_top, r_bot + 1):
        row_vals = []
        row_types = []
        for c in range(c_left, c_right + 1):
            cell = ws.cell(row=r + 1, column=c + 1)  # openpyxl is 1-indexed
            val = cell.value
            row_vals.append(val)
            row_types.append(_classify_cell(val, cell.data_type))
        grid.append(row_vals)
        types.append(row_types)
    return grid, types


def _classify_cell(value: Any, data_type: str = None) -> str:
    """Classify cell: BLANK, FORMULA, NUMBER, TEXT."""
    if data_type == "f":
        return "FORMULA"
    if value is None or (isinstance(value, str) and not value.strip()):
        return "BLANK"
    if isinstance(value, bool):
        return "NUMBER"
    if isinstance(value, (int, float)):
        return "NUMBER"
    return "TEXT"


def _detect_bold_rows(
    ws: Any, r_top: int, c_left: int, r_bot: int, c_right: int,
) -> Set[int]:
    """Detect rows where the first non-empty cell is bold (relative indices)."""
    bold_rows: Set[int] = set()
    for r in range(r_top, r_bot + 1):
        for c in range(c_left, c_right + 1):
            cell = ws.cell(row=r + 1, column=c + 1)
            if cell.value is not None:
                if cell.font and cell.font.bold:
                    bold_rows.add(r - r_top)
                break
    return bold_rows


def _get_merge_info(
    ws: Any, r_top: int, c_left: int, r_bot: int, c_right: int,
) -> List[Tuple[int, int, int, int]]:
    """Get merged cell ranges overlapping the bbox (relative coords)."""
    merges = []
    try:
        for merged_range in ws.merged_cells.ranges:
            mr_top = merged_range.min_row - 1  # to 0-indexed
            mc_left = merged_range.min_col - 1
            mr_bot = merged_range.max_row - 1
            mc_right = merged_range.max_col - 1

            if mr_bot >= r_top and mr_top <= r_bot and mc_right >= c_left and mc_left <= c_right:
                merges.append((
                    max(0, mr_top - r_top),
                    max(0, mc_left - c_left),
                    min(r_bot - r_top, mr_bot - r_top),
                    min(c_right - c_left, mc_right - c_left),
                ))
    except Exception:
        pass
    return merges


def _compute_structural_summary(
    grid: List[List[Any]],
    types: List[List[str]],
    bold_rows: Set[int],
) -> Dict[str, Any]:
    """
    Compute structural summary for prompt context.
    Ported from V4 table_reader.compute_structural_summary().
    """
    n_rows = len(grid)
    n_cols = len(grid[0]) if grid else 0

    blank_row_indices = []
    bold_first_cell_rows = list(bold_rows)
    type_transition_rows = []
    section_header_rows = []

    prev_num_ratio = None
    for r in range(n_rows):
        row_types = types[r] if r < len(types) else []

        if all(t == "BLANK" for t in row_types):
            blank_row_indices.append(r)
            prev_num_ratio = None
            continue

        non_blank = sum(1 for t in row_types if t != "BLANK")
        num_count = sum(1 for t in row_types if t == "NUMBER")
        text_count = sum(1 for t in row_types if t == "TEXT")
        num_ratio = num_count / max(non_blank, 1)

        if prev_num_ratio is not None and abs(num_ratio - prev_num_ratio) >= 0.4:
            type_transition_rows.append(r)
        prev_num_ratio = num_ratio

        if non_blank > 0 and non_blank <= n_cols * 0.5 and text_count >= non_blank * 0.7:
            first_text = None
            for val in grid[r]:
                if val is not None and str(val).strip():
                    first_text = str(val).strip()
                    break
            if first_text and len(first_text) < 80:
                section_header_rows.append({"row": r, "text": first_text})

    return {
        "total_rows": n_rows,
        "total_cols": n_cols,
        "blank_row_indices": blank_row_indices,
        "bold_first_cell_rows": sorted(bold_first_cell_rows),
        "type_transition_rows": type_transition_rows,
        "section_header_rows": section_header_rows,
    }


# ---------------------------------------------------------------------------
# Artifact loaders
# ---------------------------------------------------------------------------

def _find_workbook(run_id: str, output_base: str) -> Optional[str]:
    """Find cleaned workbook from init output."""
    from core.paths import get_init_output_path
    init_dir = get_init_output_path(run_id, output_base)
    cleaned = init_dir / "cleaned_workbook.xlsx"
    if cleaned.exists():
        return str(cleaned)
    # Try to find any xlsx in init_output
    for p in init_dir.glob("*.xlsx"):
        return str(p)
    return None


def _flatten_v3a_table_nodes(tables: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Depth-first flatten of V3a ``table_results`` trees (parent ``children`` nested)."""
    out: List[Dict[str, Any]] = []
    for t in tables:
        out.append(t)
        ch = t.get("children") or []
        if isinstance(ch, list) and ch:
            out.extend(_flatten_v3a_table_nodes(ch))
    return out


def _xl_table_entry_to_v5_bbox(entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Map an xl_table TableObject-like dict to V5 bbox keys (r1,c1,r2,c2) + hierarchy hints.
    Accepts either nested ``bbox`` or flat r_top/c_left/r_bot/c_right.
    """
    bb = entry.get("bbox")
    if isinstance(bb, dict):
        r1 = bb.get("r_top", bb.get("r1"))
        c1 = bb.get("c_left", bb.get("c1"))
        r2 = bb.get("r_bot", bb.get("r2"))
        c2 = bb.get("c_right", bb.get("c2"))
    else:
        r1 = entry.get("r_top", entry.get("r1"))
        c1 = entry.get("c_left", entry.get("c1"))
        r2 = entry.get("r_bot", entry.get("r2"))
        c2 = entry.get("c_right", entry.get("c2"))
    if r1 is None or c1 is None or r2 is None or c2 is None:
        return None
    return {
        "r1": int(r1),
        "c1": int(c1),
        "r2": int(r2),
        "c2": int(c2),
        "hierarchy_role": entry.get("hierarchy_role", "standalone"),
        "parent_table_id": entry.get("parent_table_id"),
        "split_depth": int(entry.get("split_depth", 0)),
    }


def _load_bboxes(
    run_id: str, output_base: str,
) -> Dict[str, List[Dict[str, int]]]:
    """Load bboxes from V3a/table output."""
    import json
    from core.paths import get_sheetagent_output_path, get_table_output_path

    bboxes: Dict[str, List[Dict[str, int]]] = {}

    # Try V3a output first
    sa_dir = get_sheetagent_output_path(run_id, output_base)
    v3a_results = sa_dir / "v3a_results.json"
    if v3a_results.exists():
        try:
            with open(v3a_results, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Current V3a export uses "sheets" + per-sheet "table_results"; older drafts
            # used "sheet_results" + "tables".
            sheet_blocks = (
                data.get("sheets")
                or data.get("sheet_results")
                or []
            )
            for sheet_result in sheet_blocks:
                sheet_name = sheet_result.get("sheet_name", "")
                raw_tables = (
                    sheet_result.get("table_results")
                    or sheet_result.get("tables")
                    or []
                )
                sheet_bboxes = []
                for table in _flatten_v3a_table_nodes(raw_tables):
                    bb = table.get("trimmed_bbox") or table.get("original_bbox")
                    if bb:
                        sheet_bboxes.append({
                            "r1": bb.get("r_top", 0),
                            "c1": bb.get("c_left", 0),
                            "r2": bb.get("r_bot", 0),
                            "c2": bb.get("c_right", 0),
                            "hierarchy_role": table.get(
                                "hierarchy_role", "standalone",
                            ),
                            "parent_table_id": table.get("parent_table_id"),
                            "split_depth": table.get("split_depth", 0),
                            "v3a_table_id": table.get("table_id"),
                        })
                if sheet_bboxes:
                    bboxes[sheet_name] = sheet_bboxes
            if bboxes:
                logger.info("Loaded %d sheets from V3a results", len(bboxes))
                return bboxes
        except Exception as exc:
            logger.warning("Failed to load V3a results: %s", exc)

    table_dir = get_table_output_path(run_id, output_base)

    # xl_table writes tables_per_sheet.json as {sheet_name: [TableObject dicts]}
    # with flat r_top/c_left/r_bot/c_right (no nested "bbox").
    tps = table_dir / "tables_per_sheet.json"
    if tps.exists():
        try:
            with open(tps, "r", encoding="utf-8") as f:
                tps_data = json.load(f)
            if isinstance(tps_data, dict):
                for sheet_name, tables in tps_data.items():
                    if not isinstance(tables, list):
                        continue
                    sheet_bboxes = []
                    for entry in tables:
                        bb_dict = _xl_table_entry_to_v5_bbox(entry)
                        if bb_dict:
                            sheet_bboxes.append(bb_dict)
                    if sheet_bboxes:
                        bboxes[sheet_name] = sheet_bboxes
            if bboxes:
                logger.info("Loaded %d sheets from tables_per_sheet.json", len(bboxes))
                return bboxes
        except Exception as exc:
            logger.warning("Failed to load tables_per_sheet.json: %s", exc)

    # structural_tables.json: { "sheets": [ { "sheet_name", "tables": [...] } ] }
    st_path = table_dir / "structural_tables.json"
    if st_path.exists():
        try:
            with open(st_path, "r", encoding="utf-8") as f:
                st_data = json.load(f)
            for sheet_block in st_data.get("sheets") or []:
                sheet_name = sheet_block.get("sheet_name", "")
                tables = sheet_block.get("tables") or []
                if not sheet_name or not isinstance(tables, list):
                    continue
                sheet_bboxes = []
                for entry in tables:
                    bb_dict = _xl_table_entry_to_v5_bbox(entry)
                    if bb_dict:
                        sheet_bboxes.append(bb_dict)
                if sheet_bboxes:
                    bboxes[sheet_name] = sheet_bboxes
            if bboxes:
                logger.info("Loaded %d sheets from structural_tables.json", len(bboxes))
                return bboxes
        except Exception as exc:
            logger.warning("Failed to load structural_tables.json: %s", exc)

    # Fallback: try legacy per-sheet JSON blobs
    for json_path in table_dir.glob("*.json"):
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            sheet_name = data.get("sheet_name", json_path.stem)
            sheet_bboxes = []
            for entry in data.get("final_tables", data.get("tables", [])):
                bb = entry.get("bbox")
                if bb:
                    sheet_bboxes.append({
                        "r1": bb.get("r_top", bb.get("r1", 0)),
                        "c1": bb.get("c_left", bb.get("c1", 0)),
                        "r2": bb.get("r_bot", bb.get("r2", 0)),
                        "c2": bb.get("c_right", bb.get("c2", 0)),
                    })
            if sheet_bboxes:
                bboxes[sheet_name] = sheet_bboxes
        except Exception:
            pass

    logger.info("Loaded %d sheets from table output", len(bboxes))
    return bboxes


def _load_cut_scores(
    run_id: str, output_base: str,
) -> Dict[str, List[Dict[str, Any]]]:
    """Load cut scores from parse output."""
    import json
    from core.paths import get_parse_output_path

    cuts: Dict[str, List[Dict[str, Any]]] = {}
    parse_dir = get_parse_output_path(run_id, output_base)
    cut_file = parse_dir / "cut_scores.jsonl"

    if not cut_file.exists():
        return cuts

    try:
        with open(cut_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                entry = json.loads(line)
                sheet = entry.get("sheet_name", "")
                cuts.setdefault(sheet, []).append(entry)
    except Exception as exc:
        logger.warning("Failed to load cut scores: %s", exc)

    return cuts
