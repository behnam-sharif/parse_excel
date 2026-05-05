"""
xl_sheetagent/runner.py — Entry point with sheet-level parallelization.

V3a is the splitter + hierarchy builder. V6 is the enricher (core-first algorithm).
Sheets processed in parallel via ThreadPoolExecutor;
within a sheet, tables processed sequentially (depth-first).
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, FrozenSet, List, Optional, Set, Tuple

from core.workbook_accessor import WorkbookAccessor

from .config import XlSheetAgentV6Config
from .v6_models import V6RunResult, V6TableResult
from .table_agent import analyze_table
from .formatting_agent import extract_table_formatting
from .exporter import export_results
from .finalize import finalize_sheet
from .final_map_exporter import export_final_map
from .repair_titles_rownames import repair_titles_and_rownames
from .settings_harvest import harvest_named_settings

logger = logging.getLogger(__name__)


def _v6_formula_snapshot_flags(
    config: XlSheetAgentV6Config,
) -> Tuple[bool, bool, bool]:
    """Return (use_formula_typing, effective_expansion, open_formula_workbook).

    *use_formula_typing* — classify formula cells as FORMULA vs cached NUMBER.
    *effective_expansion* — formula-dependency core expansion (refs); forced
    off when typing is values-only because that mode never builds formula refs.
    *open_formula_workbook* — whether to scan ``data_only=False`` once per sheet.
    """
    use_typing = bool(getattr(config, "use_formula_view_for_types", True))
    raw_exp = bool(getattr(config, "core_formula_expansion_enabled", False))
    allow_refs_only = bool(
        getattr(config, "core_formula_expansion_allow_without_formula_typing", False)
    )

    # Expansion needs access to formula refs (formula-view workbook), but does not
    # necessarily need FORMULA typing in the main grid.
    effective_exp = raw_exp and (use_typing or allow_refs_only)
    if raw_exp and not use_typing and not allow_refs_only:
        logger.warning(
            "xl_sheetagent: core_formula_expansion_enabled=true requires "
            "use_formula_view_for_types=true unless "
            "core_formula_expansion_allow_without_formula_typing=true; "
            "formula dependency expansion is disabled (values-only typing).",
        )
    need_wb = use_typing or effective_exp
    return use_typing, effective_exp, need_wb


def run_v6_analysis(
    run_id: str,
    output_base: str = "data/output",
    config: Optional[XlSheetAgentV6Config] = None,
    llm_client: Any = None,
    workbook_path: Optional[str] = None,
    sheets: Optional[List[str]] = None,
    all_bboxes: Optional[Dict[str, List[Dict[str, int]]]] = None,
    all_cut_scores: Optional[Dict[str, List[Dict[str, Any]]]] = None,
    accessor: Optional[WorkbookAccessor] = None,
) -> List[V6RunResult]:
    """
    Top-level V6 entry point.

    Parameters
    ----------
    run_id : str
        Run identifier.
    output_base : str
        Base output directory.
    config : XlSheetAgentV6Config, optional
        V6 config (loaded from YAML if not provided).
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
    List[V6RunResult]
    """
    if config is None:
        from core.config import load_config
        from core.stage_config import sheetagent_v6_config_from_pipeline
        pc = load_config()
        config = sheetagent_v6_config_from_pipeline(pc)

    from core.paths import get_v6_sheetagent_output_path
    output_dir = get_v6_sheetagent_output_path(run_id, output_base)
    output_dir.mkdir(parents=True, exist_ok=True)
    # Audit policy: avoid stale audit files when re-running the same run_id.
    if config.emit_audit:
        audit_dir = output_dir / "audit"
        audit_dir.mkdir(parents=True, exist_ok=True)
        try:
            (audit_dir / "llm_calls.jsonl").unlink(missing_ok=True)
        except Exception:
            pass

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
        "V6 analysis: %d sheets, workbook=%s", len(sheets), workbook_path,
    )

    # Auto-construct accessor when called standalone.
    # When the pipeline runner hands us one, reuse so snapshots stay warm.
    accessor_owned = False
    if accessor is None:
        logger.info(
            "WorkbookAccessor auto-constructed for standalone V6 run: %s",
            workbook_path,
        )
        accessor = WorkbookAccessor(workbook_path)
        accessor_owned = True

    # Audit accumulator (thread-safe via per-sheet dicts merged after)
    audit_records: Dict[str, List] = {}

    # Dispatch sheets in parallel
    results: List[V6RunResult] = []

    try:
        if config.max_workers <= 1 or len(sheets) == 1:
            for sheet_name in sheets:
                bboxes = all_bboxes.get(sheet_name, [])
                cuts = all_cut_scores.get(sheet_name, [])
                result = _run_v6_sheet(
                    workbook_path, sheet_name, bboxes, cuts,
                    config, llm_client, run_id, audit_records,
                    accessor=accessor,
                )
                results.append(result)
        else:
            with ThreadPoolExecutor(max_workers=config.max_workers) as pool:
                futures = {}
                for sheet_name in sheets:
                    bboxes = all_bboxes.get(sheet_name, [])
                    cuts = all_cut_scores.get(sheet_name, [])
                    sheet_audit: Dict[str, List] = {}
                    fut = pool.submit(
                        _run_v6_sheet,
                        workbook_path, sheet_name, bboxes, cuts,
                        config, llm_client, run_id, sheet_audit,
                        accessor,
                    )
                    futures[fut] = (sheet_name, sheet_audit)

                for fut in as_completed(futures):
                    sheet_name, sheet_audit = futures[fut]
                    try:
                        result = fut.result()
                        results.append(result)
                        for key, entries in sheet_audit.items():
                            audit_records.setdefault(key, []).extend(entries)
                    except Exception as exc:
                        logger.error("Sheet %s failed: %s", sheet_name, exc, exc_info=True)
                        results.append(V6RunResult(
                            run_id=run_id,
                            sheet_name=sheet_name,
                            errors=[str(exc)],
                        ))
    finally:
        if accessor_owned:
            accessor.close()

    # Export
    export_results(results, output_dir, config, audit_records)

    # Finalize export (deterministic flat map for DAG consumption)
    if getattr(config, "enable_finalize", False):
        import json as _json

        # NOTE: deterministic post-finalize title/row-name repair has
        # been moved to the new `xl_qc_repair` stage (post-DAG, LLM-
        # gated). V6's final_table_map.json is now an immutable
        # upstream contract. Set `enable_post_finalize_repair: true`
        # in YAML if you really need the legacy in-V6 repair path.
        if getattr(config, "enable_post_finalize_repair", False):
            try:
                _accessor = accessor
                _accessor_owned_locally = False
                if _accessor is None:
                    _accessor = WorkbookAccessor(workbook_path)
                    _accessor_owned_locally = True
                try:
                    n_titles, n_rownames, repair_audit = repair_titles_and_rownames(
                        results, _accessor, config,
                    )
                    if n_titles or n_rownames:
                        logger.info(
                            "[deprecated] In-V6 repair pass: %d titles, %d rowname sets",
                            n_titles, n_rownames,
                        )
                    audit_path = output_dir / "repair_titles_rownames_audit.jsonl"
                    audit_path.parent.mkdir(parents=True, exist_ok=True)
                    with open(audit_path, "w", encoding="utf-8") as f:
                        for row in repair_audit:
                            f.write(_json.dumps(row, ensure_ascii=False, default=str) + "\n")
                finally:
                    if _accessor_owned_locally:
                        _accessor.close()
            except Exception as exc:
                logger.warning(
                    "[deprecated] In-V6 repair failed; proceeding: %s", exc, exc_info=True,
                )

        # Post-pass B: harvest unattached named-variable cells into
        # synthetic ``node_type="setting"`` leaves so DAG can resolve
        # named-range references to a real source node.
        try:
            n_settings, settings_audit = harvest_named_settings(workbook_path, results)
            if n_settings:
                logger.info("Settings harvest: created %d synthetic settings", n_settings)
            # Persist audit log alongside the final_table_map.
            audit_path = output_dir / "settings_harvest_audit.jsonl"
            audit_path.parent.mkdir(parents=True, exist_ok=True)
            with open(audit_path, "w", encoding="utf-8") as f:
                for row in settings_audit:
                    f.write(_json.dumps(row, ensure_ascii=False) + "\n")
        except Exception as exc:
            logger.warning("Settings harvest failed; proceeding without: %s", exc, exc_info=True)
        export_final_map(results, output_dir / "final_table_map.json")

    total_tables = sum(len(r.tables) for r in results)
    total_tokens = sum(r.token_count for r in results)
    logger.info(
        "V6 complete: %d sheets, %d tables, %d tokens",
        len(results), total_tables, total_tokens,
    )

    return results


# ---------------------------------------------------------------------------
# Per-sheet worker
# ---------------------------------------------------------------------------

def _run_v6_sheet(
    workbook_path: str,
    sheet_name: str,
    bboxes: List[Dict[str, int]],
    cut_scores: List[Dict[str, Any]],
    config: XlSheetAgentV6Config,
    llm_client: Any,
    run_id: str,
    audit_records: Dict[str, List],
    accessor: Optional[WorkbookAccessor] = None,
) -> V6RunResult:
    """
    Per-sheet worker. Reads through the shared :class:`WorkbookAccessor`.
    Processes tables sequentially (depth-first through V3a hierarchy).
    """
    logger.info("V6 processing sheet: %s (%d bboxes)", sheet_name, len(bboxes))

    if len(bboxes) > config.max_tables_per_sheet_warn:
        logger.warning(
            "Sheet %s has %d bboxes (exceeds warning threshold %d)",
            sheet_name, len(bboxes), config.max_tables_per_sheet_warn,
        )

    if accessor is None:
        # Defensive fallback — parent code always supplies one today,
        # but keep stage-callable-standalone semantics intact.
        logger.info(
            "WorkbookAccessor auto-constructed in _run_v6_sheet: %s",
            workbook_path,
        )
        accessor = WorkbookAccessor(workbook_path)
        accessor_owned = True
    else:
        accessor_owned = False

    # Resolve formula-view flag bundle. Neither workbook opens here — the
    # values ws opens lazily via accessor.get_worksheet; the formula wb +
    # formula cache build on first demand below.
    use_formula_typing, effective_expansion, _need_formula_wb = (
        _v6_formula_snapshot_flags(config)
    )

    try:
        ws = accessor.get_worksheet(sheet_name)
    except Exception as exc:
        logger.error("Failed to open sheet %s: %s", sheet_name, exc)
        if accessor_owned:
            accessor.close()
        return V6RunResult(run_id=run_id, sheet_name=sheet_name, errors=[str(exc)])
    if ws is None:
        if accessor_owned:
            accessor.close()
        msg = f"Sheet '{sheet_name}' not found in workbook"
        logger.error(msg)
        return V6RunResult(run_id=run_id, sheet_name=sheet_name, errors=[msg])

    llm_call_counter = [0]
    total_tokens = 0
    errors: List[str] = []
    all_tables: List[V6TableResult] = []

    bboxes = _sort_bboxes_parent_before_child(bboxes)

    # Build formula_refs ONCE per sheet over the union of all bbox extents.
    # Per-bbox consumers filter down to their own footprint.
    sheet_formula_refs: Optional[Dict[Tuple[int, int], List[Tuple[int, int]]]] = None
    if effective_expansion and bboxes:
        try:
            r1 = min(int(b["r1"]) for b in bboxes)
            c1 = min(int(b["c1"]) for b in bboxes)
            r2 = max(int(b["r2"]) for b in bboxes)
            c2 = max(int(b["c2"]) for b in bboxes)
            sheet_formula_refs = accessor.formula_refs_in(
                sheet_name, {"r1": r1, "c1": c1, "r2": r2, "c2": c2},
            )
            logger.info(
                "Sheet %s: built formula_refs once over bbox union (%d formulas)",
                sheet_name, len(sheet_formula_refs),
            )
        except Exception as exc:
            logger.warning(
                "Failed to build sheet-level formula_refs for %s: %s",
                sheet_name, exc,
            )
            sheet_formula_refs = None

    # formula_peek: closure over accessor preserves the original
    # (bbox, local_row_idx) -> List[int] contract the validator expects.
    def _formula_peek(bbox: Dict[str, int], local_row_idx: int = 0) -> List[int]:
        if not use_formula_typing:
            return []
        return accessor.peek_formula_cols_in_local_row(
            sheet_name, bbox, local_row_idx,
        )

    for bbox_idx, bbox in enumerate(bboxes):
        try:
            table_result = _process_one_bbox(
                accessor, ws, sheet_name, bbox, bbox_idx,
                all_tables, cut_scores, config,
                llm_client, llm_call_counter, audit_records,
                formula_peek=_formula_peek,
                sheet_formula_refs=sheet_formula_refs,
                use_formula_typing=use_formula_typing,
                effective_expansion=effective_expansion,
            )
            all_tables.append(table_result)

            # Formatting (runs after semantic assignment). Uses the raw
            # worksheet handle because it reads cell-level styling.
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

    # Resolution stage: merge parent + children into V6ResolvedTables
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

    # Finalize stage: deterministic flat map for DAG consumption
    final_map = None
    if getattr(config, "enable_finalize", False) and resolved:
        try:
            def _grid_reader(abs_r: int, abs_c: int, _ws=ws) -> Any:
                try:
                    return _ws.cell(row=abs_r + 1, column=abs_c + 1).value
                except Exception:
                    return None
            final_map = finalize_sheet(
                sheet_name, resolved, config, grid_reader=_grid_reader,
            )
        except Exception as exc:
            logger.error("Finalize stage failed for %s: %s", sheet_name, exc, exc_info=True)
            errors.append(f"finalize: {exc}")

    if accessor_owned:
        accessor.close()

    return V6RunResult(
        run_id=run_id,
        sheet_name=sheet_name,
        tables=all_tables,
        resolved_tables=resolved,
        token_count=total_tokens,
        errors=errors,
        final_map=final_map,
    )


def _process_one_bbox(
    accessor: WorkbookAccessor,
    ws: Any,
    sheet_name: str,
    bbox: Dict[str, int],
    bbox_idx: int,
    all_tables: List[V6TableResult],
    cut_scores: List[Dict[str, Any]],
    config: XlSheetAgentV6Config,
    llm_client: Any,
    llm_call_counter: List[int],
    audit_records: Dict[str, List],
    formula_peek: Any = None,
    sheet_formula_refs: Optional[Dict[Tuple[int, int], List[Tuple[int, int]]]] = None,
    use_formula_typing: bool = True,
    effective_expansion: bool = False,
) -> V6TableResult:
    """Process a single bbox: read grid, run core-first analysis.

    Values are read directly off the already-open ``data_only=True``,
    ``read_only=False`` worksheet (``.cell()`` is O(1) in that mode).
    Formula typing, if enabled, consults the accessor's per-sheet formula
    cache built once via a single ``iter_rows`` pass on the formula workbook.
    """
    r_top = bbox["r1"]
    c_left = bbox["c1"]
    r_bot = bbox["r2"]
    c_right = bbox["c2"]

    # Lazy-build the formula cache on first need; subsequent bboxes reuse it.
    is_formula_set: FrozenSet[Tuple[int, int]] = frozenset()
    if use_formula_typing:
        is_formula_set = accessor._formula_cache(sheet_name).is_formula_set

    grid, types = _read_cell_grid(
        ws, r_top, c_left, r_bot, c_right,
        is_formula_set=is_formula_set if use_formula_typing else None,
    )

    # Formula-ref gating: only pay for expansion when the bbox has at least
    # one fully-blank column. Dense tables skip the cost entirely.
    formula_refs: Optional[Dict[Tuple[int, int], List[Tuple[int, int]]]] = None
    if (
        sheet_formula_refs
        and effective_expansion
        and _bbox_has_empty_column(types)
    ):
        formula_refs = _filter_formula_refs_to_bbox(sheet_formula_refs, bbox)

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
        formula_peek=formula_peek,
        formula_refs=formula_refs,
    )


def _bbox_has_empty_column(types: List[List[str]]) -> bool:
    """True if any column inside ``types`` is entirely BLANK.

    Cheap O(rows*cols) pre-check used to gate formula-dependency core
    expansion: if every column already has some NUMBER/FORMULA/TEXT cell,
    there is no gap for expansion to fill and the (expensive) dict iteration
    can be skipped.
    """
    if not types:
        return False
    n_rows = len(types)
    n_cols = max((len(r) for r in types), default=0)
    for c in range(n_cols):
        all_blank = True
        for r in range(n_rows):
            row = types[r]
            if c < len(row) and row[c] != "BLANK":
                all_blank = False
                break
        if all_blank:
            return True
    return False


def _filter_formula_refs_to_bbox(
    refs: Dict[Tuple[int, int], List[Tuple[int, int]]],
    bbox: Dict[str, int],
) -> Dict[Tuple[int, int], List[Tuple[int, int]]]:
    """Return the subset of ``refs`` whose formula-cell keys sit inside ``bbox``."""
    r1 = int(bbox["r1"]); c1 = int(bbox["c1"])
    r2 = int(bbox["r2"]); c2 = int(bbox["c2"])
    return {
        k: v
        for k, v in refs.items()
        if r1 <= k[0] <= r2 and c1 <= k[1] <= c2
    }


def _sort_bboxes_parent_before_child(bboxes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Process parents before children so header inheritance sees donor tables."""
    if len(bboxes) <= 1:
        return bboxes
    return sorted(
        bboxes,
        key=lambda b: (b.get("split_depth", 0), b.get("r1", 0), b.get("c1", 0)),
    )


# ---------------------------------------------------------------------------
# Cell reading (direct ws.cell() — O(1) on a read_only=False worksheet)
# ---------------------------------------------------------------------------

def _read_cell_grid(
    ws: Any,
    r_top: int,
    c_left: int,
    r_bot: int,
    c_right: int,
    *,
    is_formula_set: Optional[FrozenSet[Tuple[int, int]]] = None,
) -> Tuple[List[List[Any]], List[List[str]]]:
    """Read cell values and classify types. Coords are 0-indexed inclusive.

    ``is_formula_set`` (absolute coords) is consulted to recover FORMULA
    typing.  When ``None`` (``use_formula_view_for_types=false``), formula
    cells classify as NUMBER or BLANK from their cached values.
    """
    grid: List[List[Any]] = []
    types: List[List[str]] = []
    for r in range(r_top, r_bot + 1):
        row_vals: List[Any] = []
        row_types: List[str] = []
        for c in range(c_left, c_right + 1):
            cell = ws.cell(row=r + 1, column=c + 1)
            val = cell.value
            row_vals.append(val)
            if is_formula_set is not None and (r, c) in is_formula_set:
                row_types.append("FORMULA")
            else:
                row_types.append(_classify_cell(val))
        grid.append(row_vals)
        types.append(row_types)
    return grid, types


def _classify_cell(value: Any) -> str:
    """Classify cell without formula typing: BLANK, NUMBER, or TEXT."""
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
    """Row indices (bbox-relative) whose first non-empty cell is bold."""
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
    """Merged cell ranges overlapping ``bbox``, in bbox-relative coords."""
    merges: List[Tuple[int, int, int, int]] = []
    try:
        for merged_range in ws.merged_cells.ranges:
            mr_top = merged_range.min_row - 1
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


# ---------------------------------------------------------------------------
# Structural summary (pure compute over cached grid/types)
# ---------------------------------------------------------------------------

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
    for p in init_dir.glob("*.xlsx"):
        return str(p)
    return None


def _flatten_v3a_table_nodes(tables: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Depth-first flatten of V3a ``table_results`` trees."""
    out: List[Dict[str, Any]] = []
    for t in tables:
        out.append(t)
        ch = t.get("children") or []
        if isinstance(ch, list) and ch:
            out.extend(_flatten_v3a_table_nodes(ch))
    return out


def _xl_table_entry_to_v6_bbox(entry: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Map an xl_table TableObject-like dict to V6 bbox keys (r1,c1,r2,c2) + hierarchy hints.
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
                        bb_dict = _xl_table_entry_to_v6_bbox(entry)
                        if bb_dict:
                            sheet_bboxes.append(bb_dict)
                    if sheet_bboxes:
                        bboxes[sheet_name] = sheet_bboxes
            if bboxes:
                logger.info("Loaded %d sheets from tables_per_sheet.json", len(bboxes))
                return bboxes
        except Exception as exc:
            logger.warning("Failed to load tables_per_sheet.json: %s", exc)

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
                    bb_dict = _xl_table_entry_to_v6_bbox(entry)
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
