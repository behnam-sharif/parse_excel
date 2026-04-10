"""
V3A pipeline runner.

Orchestration:
  1. Load upstream outputs (reuse upstream_compat.load_upstream)
  2. Open workbooks
  3. Group tables by sheet
  4. Per sheet (PARALLEL at sheet level):
     a. Open per-thread workbook
     b. Per table (SEQUENTIAL within sheet):
        - Run table agent
     c. Run sheet coordinator (collect pieces + leftover regions)
  5. Collect all sheet results
  6. Export
"""

from __future__ import annotations

import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Dict, List, Optional

import openpyxl

from core.paths import get_sheetagent_output_path

from ..upstream_compat import load_upstream
from .schemas import V3ARunResult, V3ASheetResult
from .table_agent import run_table_agent
from .sheet_coordinator import collect_sheet
from .llm_ownership import decide_ownership
from .exporter import write_v3a_outputs

_log = logging.getLogger("xl_sheetagent.v3a.runner")

V3A_VERSION = "3a.1.0"  # Phase 4+: Hierarchical splitting support


def run_v3a(
    run_id: str,
    output_path: Optional[str] = None,
    config=None,
) -> V3ARunResult:
    """Run the V3A pipeline.

    Parameters
    ----------
    run_id : str
        Run identifier for locating upstream outputs.
    output_path : str, optional
        Override output directory. Defaults to ``get_sheetagent_output_path(run_id)``.
    config : XlSheetAgentConfig
        Stage configuration with v3a_* fields.

    Returns
    -------
    V3ARunResult
    """
    if config is None:
        from core.config import load_config
        from core.stage_config import sheetagent_config_from_pipeline
        config = sheetagent_config_from_pipeline(load_config())

    if output_path is None:
        output_path = str(get_sheetagent_output_path(run_id))
    os.makedirs(output_path, exist_ok=True)

    t0 = time.time()
    total_llm_calls = 0

    # 1. Load upstream
    upstream = load_upstream(run_id)
    _log.info("V3A: loaded %d tables from upstream", len(upstream.table_objects))

    # 2. Group tables by sheet
    tables_by_sheet: Dict[str, list] = {}
    for tv in upstream.table_objects:
        tables_by_sheet.setdefault(tv.sheet_name, []).append(tv)

    # Group rejected blocks by sheet for coordinator
    rejected_by_sheet: Dict[str, list] = {}
    for rb in upstream.rejected_blocks:
        sn = rb.get("sheet_name", "")
        rejected_by_sheet.setdefault(sn, []).append(rb)

    # 3. Build LLM function (if enabled)
    llm_fn = None
    if not config.v3a_skip_llm:
        def llm_fn(table_context, candidates):
            nonlocal total_llm_calls
            total_llm_calls += 1
            return decide_ownership(
                table_context, candidates,
                model=config.v3a_llm_model,
                max_tokens=config.v3a_llm_max_tokens,
            )

    # 4. Process sheets
    all_sheet_results: List[V3ASheetResult] = []

    def _process_sheet(sheet_name: str, table_views: list) -> V3ASheetResult:
        """Process one sheet with optional recursive splitting (Phase 4+).

        If v3a_hierarchical_splits enabled:
          - Pass 1: Detect on all root tables
          - Pass 2+: Recursive split-and-detect (breadth-first)
        Otherwise:
          - Legacy two-pass behavior
        """
        wb_local = openpyxl.load_workbook(
            upstream.workbook_path, data_only=False, read_only=True,
        )
        ws_local = wb_local[sheet_name]

        hierarchical_enabled = getattr(config, "v3a_hierarchical_splits", False)

        if hierarchical_enabled:
            # --- NEW: Recursive hierarchical splitting ---
            _log.debug("Sheet %s: Using hierarchical splitting mode", sheet_name)

            # Pass 1: Detect on all root tables
            root_results = []
            for tv in table_views:
                result = run_table_agent(
                    tv, ws_local, config,
                    llm_fn=llm_fn,
                    cut_scores=upstream.cut_scores,
                    parent_id=None,
                    split_depth=0,
                )
                root_results.append(result)

            # Pass 2+: Recursive splitting
            max_depth = getattr(config, "v3a_split_max_depth", 2)
            final_results = []

            for root_result in root_results:
                tree = _recursive_split_and_detect(
                    root_result, ws_local, config, llm_fn,
                    upstream.cut_scores, max_depth
                )
                final_results.append(tree)

            # Flatten tree for coordinator
            all_tables = _flatten_tree(final_results)
            table_results = final_results  # Keep hierarchical for output

        else:
            # --- LEGACY: Original two-pass behavior ---
            first_pass_results = []
            tables_needing_split = []

            for tv in table_views:
                result = run_table_agent(
                    tv, ws_local, config,
                    llm_fn=llm_fn,
                    cut_scores=upstream.cut_scores,
                )
                first_pass_results.append(result)

                # Check if split needed
                if result.split_candidates:
                    tables_needing_split.append((tv, result))

            # Pass 2: Split and re-detect (if enabled)
            if getattr(config, "v3a_two_pass_enabled", False) and tables_needing_split:
                from .split_agent import decide_split
                from .cell_analysis import read_cell_grid, classify_grid

                final_table_results = []

                for tv, first_result in tables_needing_split:
                    # Re-read grid for split decision
                    grid, data_types = read_cell_grid(
                        ws_local, tv.r_top, tv.c_left, tv.r_bot, tv.c_right
                    )
                    types = classify_grid(grid, data_types)

                    split_decision = decide_split(
                        grid, types, tv.r_top, tv.c_left, tv.r_bot, tv.c_right,
                        first_result.core_data_region, first_result.split_candidates, config,
                    )

                    if split_decision:
                        # Split into child tables
                        child_tables = _split_table(tv, split_decision)

                        # Re-run detection on each child
                        for child_tv in child_tables:
                            child_result = run_table_agent(
                                child_tv, ws_local, config,
                                llm_fn=llm_fn,
                                cut_scores=upstream.cut_scores,
                            )
                            final_table_results.append(child_result)
                    else:
                        # Split decision rejected, keep original
                        final_table_results.append(first_result)

                # Add tables that didn't need splitting
                for tv, first_result in zip(table_views, first_pass_results):
                    if (tv, first_result) not in tables_needing_split:
                        final_table_results.append(first_result)

                table_results = final_table_results
            else:
                # No two-pass or no splits needed
                table_results = first_pass_results

            all_tables = table_results

        # Run sheet coordinator
        coordinator = collect_sheet(
            sheet_name,
            all_tables,
            rejected_by_sheet.get(sheet_name, []),
            ws=ws_local,
        )

        try:
            wb_local.close()
        except Exception:
            pass

        return V3ASheetResult(
            sheet_name=sheet_name,
            table_results=table_results,
            coordinator=coordinator,
        )

    # Parallel at sheet level
    max_workers = min(config.v3a_max_parallel_sheets, len(tables_by_sheet))
    max_workers = max(1, max_workers)

    if max_workers == 1 or len(tables_by_sheet) <= 1:
        for sheet_name, table_views in tables_by_sheet.items():
            try:
                result = _process_sheet(sheet_name, table_views)
                all_sheet_results.append(result)
            except Exception as exc:
                _log.error("V3A sheet %r failed: %s", sheet_name, exc)
    else:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {}
            for sheet_name, table_views in tables_by_sheet.items():
                fut = executor.submit(_process_sheet, sheet_name, table_views)
                futures[fut] = sheet_name

            for fut in as_completed(futures):
                sheet_name = futures[fut]
                try:
                    result = fut.result()
                    all_sheet_results.append(result)
                except Exception as exc:
                    _log.error("V3A sheet %r failed: %s", sheet_name, exc)

    # Sort sheets by name for consistent output
    all_sheet_results.sort(key=lambda s: s.sheet_name)

    elapsed = time.time() - t0
    n_tables = sum(len(sr.table_results) for sr in all_sheet_results)

    run_result = V3ARunResult(
        run_id=run_id,
        run_ts=datetime.now(tz=timezone.utc).isoformat(),
        version=V3A_VERSION,
        sheets=all_sheet_results,
        elapsed_seconds=round(elapsed, 2),
        n_tables_processed=n_tables,
        n_llm_calls=total_llm_calls,
    )

    # 5. Export
    write_v3a_outputs(run_result, output_path)

    _log.info(
        "V3A complete: %d sheets, %d tables, %d LLM calls, %.1fs",
        len(all_sheet_results), n_tables, total_llm_calls, elapsed,
    )

    return run_result


# ---------------------------------------------------------------------------
# Phase 4: Two-Pass Split Helper
# ---------------------------------------------------------------------------

def _partition_bbox_by_rows(r_top, c_left, r_bot, c_right, split_rows):
    """
    Partition a bbox into non-overlapping row segments.

    Parameters
    ----------
    r_top, c_left, r_bot, c_right : int
        Parent bbox coordinates
    split_rows : list[int]
        Sorted list of absolute row indices where splits occur

    Returns
    -------
    list[tuple]
        List of (segment_r_top, c_left, segment_r_bot, c_right) for each segment

    Example
    -------
    Parent bbox: rows 3-48
    Split rows: [6, 20, 34]
    Result: [(3,_,5,_), (6,_,19,_), (20,_,33,_), (34,_,48,_)]
    """
    if not split_rows:
        return [(r_top, c_left, r_bot, c_right)]

    # Sort and deduplicate
    split_rows = sorted(set(split_rows))

    # Filter splits to only those within bbox bounds
    valid_splits = [s for s in split_rows if r_top < s <= r_bot]

    if not valid_splits:
        return [(r_top, c_left, r_bot, c_right)]

    segments = []
    current_top = r_top

    for split_row in valid_splits:
        # Create segment [current_top, split_row-1]
        if current_top <= split_row - 1:
            segments.append((current_top, c_left, split_row - 1, c_right))
        # Next segment starts at split_row
        current_top = split_row

    # Final segment from last split to r_bot
    if current_top <= r_bot:
        segments.append((current_top, c_left, r_bot, c_right))

    return segments


def _split_table(table_view, split_decision):
    """
    Split table into two children based on split decision.

    Parameters
    ----------
    table_view : TableView
        Original table view to split
    split_decision : SplitDecision
        Decision from split agent

    Returns
    -------
    list[TableView]
        [top_child, bottom_child] for row splits
        [left_child, right_child] for col splits

    Notes
    -----
    Only row splits supported in Phase 4. Column splits deferred to v3b.
    """
    from copy import copy

    if split_decision.split_axis != "row":
        # Only row splits supported for now
        _log.warning(
            "Table %s: Column split requested but not yet supported, keeping original",
            table_view.table_id,
        )
        return [table_view]

    split_row = split_decision.split_index
    cut_side = split_decision.cut_side

    # Validate split creates valid regions
    if cut_side == "bottom":
        # top region: [r_top, split_row-1], bottom region: [split_row, r_bot]
        if split_row <= table_view.r_top or split_row > table_view.r_bot:
            _log.warning(
                "Table %s: Invalid split_row=%d for cut_side=bottom (r_top=%d, r_bot=%d), keeping original",
                table_view.table_id, split_row, table_view.r_top, table_view.r_bot
            )
            return [table_view]
    else:  # cut_side == "top"
        # top region: [r_top, split_row], bottom region: [split_row+1, r_bot]
        if split_row < table_view.r_top or split_row >= table_view.r_bot:
            _log.warning(
                "Table %s: Invalid split_row=%d for cut_side=top (r_top=%d, r_bot=%d), keeping original",
                table_view.table_id, split_row, table_view.r_top, table_view.r_bot
            )
            return [table_view]

    if cut_side == "bottom":
        # Split row belongs to bottom child
        top = copy(table_view)
        top.table_id = f"{table_view.table_id}_top"
        top.r_bot = split_row - 1
        top.n_rows = top.r_bot - top.r_top + 1

        bottom = copy(table_view)
        bottom.table_id = f"{table_view.table_id}_bottom"
        bottom.r_top = split_row
        bottom.n_rows = bottom.r_bot - bottom.r_top + 1
    else:  # cut_side == "top"
        # Split row belongs to top child
        top = copy(table_view)
        top.table_id = f"{table_view.table_id}_top"
        top.r_bot = split_row
        top.n_rows = top.r_bot - top.r_top + 1

        bottom = copy(table_view)
        bottom.table_id = f"{table_view.table_id}_bottom"
        bottom.r_top = split_row + 1
        bottom.n_rows = bottom.r_bot - bottom.r_top + 1

    _log.info(
        "Table %s split at row %d (%s): top=%s (%d rows), bottom=%s (%d rows)",
        table_view.table_id, split_row, cut_side,
        top.table_id, top.n_rows, bottom.table_id, bottom.n_rows,
    )

    return [top, bottom]


def _recursive_split_and_detect(
    parent_result,
    ws, config, llm_fn, cut_scores, max_depth: int,
):
    """
    Split table into flat non-overlapping segments (Phase 4+).

    FLAT PARTITION: All splits applied in one pass, creating N+1 non-overlapping
    children from N split points. No recursion, no grandchildren.

    Parameters
    ----------
    parent_result : TableAgentResult
        Parent table to potentially split
    ws : openpyxl worksheet
        Worksheet object
    config : XlSheetAgentConfig
        Configuration
    llm_fn : callable or None
        LLM function
    cut_scores : list
        Cut scores from xl_parse
    max_depth : int
        Maximum depth (unused in flat mode, kept for compatibility)

    Returns
    -------
    TableAgentResult
        Parent result with children populated (no grandchildren)
    """
    from .split_agent import decide_splits
    from .cell_analysis import read_cell_grid, classify_grid

    # Base case: no split candidates
    if not parent_result.split_candidates:
        return parent_result

    # Get split decisions (ALL valid splits if multi-split enabled)
    tv = _result_to_table_view(parent_result)
    grid, data_types = read_cell_grid(ws, tv.r_top, tv.c_left, tv.r_bot, tv.c_right)
    types = classify_grid(grid, data_types)

    split_decisions = decide_splits(
        grid, types, tv.r_top, tv.c_left, tv.r_bot, tv.c_right,
        parent_result.core_data_region, parent_result.split_candidates, config,
    )

    if not split_decisions:
        return parent_result

    # Extract split row indices (only row splits supported)
    split_rows = [d.split_index for d in split_decisions if d.split_axis == "row"]

    if not split_rows:
        _log.debug("Table %s: No valid row splits found", parent_result.table_id)
        return parent_result

    _log.info(
        "Table %s: Partitioning into %d segments using %d split(s): rows=%s",
        parent_result.table_id, len(split_rows) + 1, len(split_rows), split_rows
    )

    # Partition parent bbox into non-overlapping segments
    segments = _partition_bbox_by_rows(tv.r_top, tv.c_left, tv.r_bot, tv.c_right, split_rows)

    _log.debug(
        "Table %s: Created %d segments from bbox [%d:%d]",
        parent_result.table_id, len(segments), tv.r_top, tv.r_bot
    )

    # Mark parent as "parent" role
    parent_result.hierarchy_role = "parent"

    # Create child table view for each segment
    from copy import copy

    for i, (seg_r_top, seg_c_left, seg_r_bot, seg_c_right) in enumerate(segments):
        child_tv = copy(tv)
        child_tv.table_id = f"{parent_result.table_id}_child{i+1}"
        child_tv.r_top = seg_r_top
        child_tv.c_left = seg_c_left
        child_tv.r_bot = seg_r_bot
        child_tv.c_right = seg_c_right
        child_tv.n_rows = seg_r_bot - seg_r_top + 1
        child_tv.n_cols = seg_c_right - seg_c_left + 1

        # Run detection on child (depth=1, no further splitting)
        child_result = run_table_agent(
            child_tv, ws, config,
            llm_fn=llm_fn,
            cut_scores=cut_scores,
            parent_id=parent_result.table_id,
            split_depth=1,  # All children are depth 1
        )

        parent_result.children.append(child_result)

        _log.debug(
            "Table %s: Child %d created [%d:%d] (%d rows)",
            parent_result.table_id, i+1, seg_r_top, seg_r_bot, child_tv.n_rows
        )

    return parent_result


def _flatten_tree(results: List) -> List:
    """
    Flatten hierarchical tree for coordinator (depth-first traversal).

    Parameters
    ----------
    results : list[TableAgentResult]
        Root-level results (may have children)

    Returns
    -------
    list[TableAgentResult]
        Flattened list of all tables
    """
    flat = []
    for result in results:
        flat.append(result)
        if result.children:
            flat.extend(_flatten_tree(result.children))
    return flat


def _result_to_table_view(result):
    """
    Convert TableAgentResult back to TableView-like object for re-processing.

    Parameters
    ----------
    result : TableAgentResult
        Result to convert

    Returns
    -------
    object
        Minimal TableView-like object with required attributes
    """
    bbox = result.trimmed_bbox

    # Create minimal object with attributes needed for processing
    class TableView:
        pass

    tv = TableView()
    tv.table_id = result.table_id
    tv.sheet_name = result.sheet_name
    tv.r_top = bbox['r_top']
    tv.c_left = bbox['c_left']
    tv.r_bot = bbox['r_bot']
    tv.c_right = bbox['c_right']
    tv.n_rows = bbox['r_bot'] - bbox['r_top'] + 1
    tv.n_cols = bbox['c_right'] - bbox['c_left'] + 1

    return tv
