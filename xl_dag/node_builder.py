"""
xl_dag/node_builder.py — Build DagNode per leaf entry.

Spec §7.1: one node per leaf with or without a core_region. Parents yield
zero nodes. Formulas inside the core_region feed representative grouping
and ref extraction.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

from xl_sheetagent.coordinates import bbox_to_coords

from .config import XlDagConfig
from .formula_parser import ast_shape_key, extract_refs
from .schemas import (
    CellAddress,
    DagBbox,
    DagNode,
    ExtractedRefs,
    FootnoteNoteRef,
    FormulaAst,
    RepresentativeFormula,
)


logger = logging.getLogger(__name__)


# Flags contributed by the DAG build (spec §7.5).
FLAG_NO_REP_FORMULA = "no_representative_formula"
FLAG_HETEROGENEOUS = "heterogeneous_formulas_in_column"
FLAG_PARSE_ERROR = "parse_error"


def build_nodes(
    table_map: Dict[str, Dict[str, Any]],
    accessor: Any,
    config: XlDagConfig,
) -> Tuple[
    List[DagNode],
    Dict[str, List[RepresentativeFormula]],
    List[ExtractedRefs],
    List[Dict[str, Any]],
]:
    """Build nodes from final_table_map.json.

    Returns
    -------
    (nodes, rep_formulas_by_node_id, per_node_refs, errors)
        * ``rep_formulas_by_node_id`` is used by the formulas.json writer.
        * ``per_node_refs`` has one entry per node in ``nodes``; used by the
          edge builder. Leaves with no core_region carry an empty
          ``ExtractedRefs``.
        * ``errors`` is a list of per-node error records for ``dag_build_errors.jsonl``.
    """
    nodes: List[DagNode] = []
    rep_formulas: Dict[str, List[RepresentativeFormula]] = {}
    per_node_refs: List[ExtractedRefs] = []
    errors: List[Dict[str, Any]] = []

    for sheet_name, leaf, kind_label in _iter_leaves(table_map):
        try:
            node, reps, refs = _build_single_node(
                sheet_name, leaf, accessor, config,
            )
        except Exception as exc:
            logger.error(
                "Node build failed: sheet=%s table_id=%s err=%s",
                sheet_name, leaf.get("table_id"), exc,
                exc_info=True,
            )
            errors.append(
                {
                    "stage": "node_builder",
                    "sheet_name": sheet_name,
                    "table_id": leaf.get("table_id"),
                    "error": str(exc),
                }
            )
            # Emit a degraded node with parse_error flag.
            node = _degraded_node(sheet_name, leaf, error=str(exc))
            reps = []
            refs = ExtractedRefs()

        if not config.include_null_core_nodes and leaf.get("core_region") is None:
            continue
        nodes.append(node)
        rep_formulas[node.node_id] = reps
        per_node_refs.append(refs)
    return nodes, rep_formulas, per_node_refs, errors


# ---------------------------------------------------------------------------
# Leaf iteration
# ---------------------------------------------------------------------------


def _iter_leaves(
    table_map: Dict[str, Dict[str, Any]],
):
    for sheet_name, sheet_map in table_map.items():
        for entry in sheet_map.get("entries") or []:
            kind = entry.get("kind")
            if kind == "standalone":
                yield sheet_name, entry, "standalone"
            elif kind == "parent":
                for child in entry.get("children") or []:
                    yield sheet_name, child, "child"


# ---------------------------------------------------------------------------
# Per-leaf build
# ---------------------------------------------------------------------------


def _build_single_node(
    sheet_name: str,
    leaf: Dict[str, Any],
    accessor: Any,
    config: XlDagConfig,
) -> Tuple[DagNode, List[RepresentativeFormula], ExtractedRefs]:
    table_id = leaf.get("table_id") or f"{sheet_name}_unknown"
    sheet_slug = _slugify(sheet_name, lowercase=config.sheet_slug_lowercase)
    node_id = f"{sheet_slug}__{table_id}__0"

    core_region = leaf.get("core_region")  # dict or None
    leaf_bbox = leaf.get("bbox") or {"r1": 0, "c1": 0, "r2": 0, "c2": 0}

    # node_bbox = core_region when present, else leaf bbox (spec §7.2 says
    # bbox mirrors the core region; we fall back to leaf bbox for null-core
    # leaves so Cheri still has a rectangle to highlight).
    bbox_src = core_region or leaf_bbox
    node_bbox = DagBbox(
        r1=int(bbox_src["r1"]),
        c1=int(bbox_src["c1"]),
        r2=int(bbox_src["r2"]),
        c2=int(bbox_src["c2"]),
    )
    coordinates = bbox_to_coords(sheet_name, bbox_src)

    flags: List[str] = list(leaf.get("source_flags") or [])

    if core_region is None:
        if FLAG_NO_REP_FORMULA not in flags:
            flags.append(FLAG_NO_REP_FORMULA)
        reps: List[RepresentativeFormula] = []
        refs = ExtractedRefs()
    else:
        reps, refs, had_heterogeneous = _collect_formulas_from_core(
            sheet_name, core_region, accessor, config,
        )
        if had_heterogeneous and FLAG_HETEROGENEOUS not in flags:
            flags.append(FLAG_HETEROGENEOUS)
        if not reps and FLAG_NO_REP_FORMULA not in flags:
            flags.append(FLAG_NO_REP_FORMULA)

    search_text = _build_search_text(leaf, sheet_name)

    node = DagNode(
        node_id=node_id,
        table_id=table_id,
        sheet_name=sheet_name,
        sheet_slug=sheet_slug,
        bbox=node_bbox,
        coordinates=coordinates,
        title=leaf.get("title"),
        headers=list(leaf.get("headers") or []),
        row_names=leaf.get("row_names"),
        footnotes_notes_refs=[
            FootnoteNoteRef(
                row=int(f.get("row", 0)),
                col=int(f.get("col", 0)),
                text=str(f.get("text", "")),
            )
            for f in (leaf.get("footnotes_notes_refs") or [])
        ],
        parent_path=list(leaf.get("parent_path") or []),
        node_type=str(leaf.get("node_type") or "mixed"),
        referenced_cells=_cell_addresses_from_refs(refs),
        referenced_ranges=_range_strings_from_refs(refs),
        referenced_named_ranges=list(refs.named_ranges),
        search_text=search_text,
        flags=flags,
    )
    return node, reps, refs


# ---------------------------------------------------------------------------
# Formula collection within a core region
# ---------------------------------------------------------------------------


def _collect_formulas_from_core(
    sheet_name: str,
    core_region: Dict[str, Any],
    accessor: Any,
    config: XlDagConfig,
) -> Tuple[List[RepresentativeFormula], ExtractedRefs, bool]:
    """Walk every cell in ``core_region``, group by column+AST-shape.

    Returns (rep_formulas, unioned_refs, had_heterogeneous).
    """
    reps: List[RepresentativeFormula] = []
    unioned = ExtractedRefs()
    had_heterogeneous = False
    seen_cells: set = set()
    seen_ranges: set = set()
    seen_named: set = set()
    seen_unresolved: set = set()

    r1 = int(core_region["r1"])
    c1 = int(core_region["c1"])
    r2 = int(core_region["r2"])
    c2 = int(core_region["c2"])

    if accessor is None:
        # No workbook reader available — treat as no-formula region.
        return reps, unioned, had_heterogeneous

    for col in range(c1, c2 + 1):
        # Group formulas by AST-shape within this column.
        groups: Dict[str, Dict[str, Any]] = {}
        for row in range(r1, r2 + 1):
            try:
                ft = accessor.get_formula(sheet_name, row, col)
            except Exception as exc:
                logger.debug(
                    "get_formula failed at %s!%d,%d: %s",
                    sheet_name, row, col, exc,
                )
                continue
            if not ft:
                continue
            refs = extract_refs(ft, sheet_name)
            key = ast_shape_key(refs.ast)
            group = groups.setdefault(
                key,
                {"refs": refs, "formula": ft, "rows": [row, row]},
            )
            group["rows"][1] = row
        if len(groups) > config.max_representative_formulas_per_col:
            had_heterogeneous = True

        # Emit one representative per group (capped); merge refs across groups.
        for i, (key, group) in enumerate(groups.items()):
            if i >= config.max_representative_formulas_per_col:
                had_heterogeneous = True
                break
            refs: ExtractedRefs = group["refs"]
            rep = RepresentativeFormula(
                column_index=col,
                row_range=[group["rows"][0], group["rows"][1]],
                raw_formula_string=group["formula"],
                ast=refs.ast or FormulaAst(node_type="shape", raw=""),
            )
            reps.append(rep)

            # Union into node-level ref lists.
            for ref in refs.cells:
                if ref not in seen_cells:
                    seen_cells.add(ref)
                    unioned.cells.append(ref)
            for ref in refs.ranges:
                # Dedup by the raw tuple identity excluding raw string.
                rkey = (ref[0], ref[1], ref[2], ref[3], ref[4])
                if rkey not in seen_ranges:
                    seen_ranges.add(rkey)
                    unioned.ranges.append(ref)
            for nr in refs.named_ranges:
                if nr not in seen_named:
                    seen_named.add(nr)
                    unioned.named_ranges.append(nr)
            for reason, raw in refs.unresolved:
                if (reason, raw) not in seen_unresolved:
                    seen_unresolved.add((reason, raw))
                    unioned.unresolved.append((reason, raw))

    return reps, unioned, had_heterogeneous


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(sheet_name: str, lowercase: bool = True) -> str:
    s = sheet_name.lower() if lowercase else sheet_name
    s = _SLUG_RE.sub("_", s).strip("_")
    return s or "sheet"


def _build_search_text(leaf: Dict[str, Any], sheet_name: str) -> str:
    title = leaf.get("title") or ""
    headers = leaf.get("headers") or []
    flat_headers = " ".join(
        str(c) for row in headers if isinstance(row, list)
        for c in row if c is not None
    )
    row_names = " ".join(str(r) for r in (leaf.get("row_names") or []))
    parent_path = " ".join(str(p) for p in (leaf.get("parent_path") or []))
    node_type = str(leaf.get("node_type") or "")
    parts = [
        title,
        flat_headers,
        row_names,
        parent_path,
        sheet_name,
        node_type,
    ]
    return " | ".join(p for p in parts if p)


def _cell_addresses_from_refs(refs: ExtractedRefs) -> List[CellAddress]:
    return [CellAddress(sheet=s, row=r, col=c) for s, r, c in refs.cells]


def _range_strings_from_refs(refs: ExtractedRefs) -> List[str]:
    out: List[str] = []
    seen: set = set()
    for _sheet, _r1, _c1, _r2, _c2, raw in refs.ranges:
        if raw not in seen:
            seen.add(raw)
            out.append(raw)
    return out


def _degraded_node(
    sheet_name: str,
    leaf: Dict[str, Any],
    error: str,
) -> DagNode:
    table_id = leaf.get("table_id") or f"{sheet_name}_unknown"
    sheet_slug = _slugify(sheet_name)
    node_id = f"{sheet_slug}__{table_id}__0"
    leaf_bbox = leaf.get("bbox") or {"r1": 0, "c1": 0, "r2": 0, "c2": 0}
    node_bbox = DagBbox(
        r1=int(leaf_bbox["r1"]),
        c1=int(leaf_bbox["c1"]),
        r2=int(leaf_bbox["r2"]),
        c2=int(leaf_bbox["c2"]),
    )
    flags = list(leaf.get("source_flags") or [])
    if FLAG_PARSE_ERROR not in flags:
        flags.append(FLAG_PARSE_ERROR)
    if FLAG_NO_REP_FORMULA not in flags:
        flags.append(FLAG_NO_REP_FORMULA)
    return DagNode(
        node_id=node_id,
        table_id=table_id,
        sheet_name=sheet_name,
        sheet_slug=sheet_slug,
        bbox=node_bbox,
        coordinates=bbox_to_coords(sheet_name, leaf_bbox),
        title=leaf.get("title"),
        headers=list(leaf.get("headers") or []),
        row_names=leaf.get("row_names"),
        footnotes_notes_refs=[],
        parent_path=list(leaf.get("parent_path") or []),
        node_type=str(leaf.get("node_type") or "mixed"),
        flags=flags,
    )
