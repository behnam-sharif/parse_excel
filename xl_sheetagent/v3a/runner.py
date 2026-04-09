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

V3A_VERSION = "3a.0.1"


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
        """Process one sheet: per-thread workbook, sequential table agents, coordinator."""
        wb_local = openpyxl.load_workbook(
            upstream.workbook_path, data_only=True, read_only=True,
        )
        ws_local = wb_local[sheet_name]

        # Run table agents sequentially within the sheet
        table_results = []
        for tv in table_views:
            result = run_table_agent(tv, ws_local, config, llm_fn=llm_fn)
            table_results.append(result)

        # Run sheet coordinator
        coordinator = collect_sheet(
            sheet_name,
            table_results,
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
