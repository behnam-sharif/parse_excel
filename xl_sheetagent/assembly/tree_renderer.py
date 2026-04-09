"""
tree_renderer.py — Render SheetObjectTree as PNG using matplotlib (V1D).

Uses Agg backend (headless). Same pattern as xl_image/render/fallback_renderer.py.
All rendering wrapped in try/except — never crashes the pipeline.
"""

from __future__ import annotations

import logging
import os
from dataclasses import asdict
from typing import Any, Dict, List, Optional, Tuple

_log = logging.getLogger("xl_sheetagent.tree_renderer")

# ---------------------------------------------------------------------------
# Colour / style constants
# ---------------------------------------------------------------------------

_NODE_COLOURS = {
    "section": "#4A90D9",
    "table": "#5CB85C",
    "table_partition": "#8FD98F",
    "context_block": "#AAAAAA",
    "note": "#F0E68C",
    "shape": "#F5A623",
}

_EDGE_COLOURS = {
    "contains": "#888888",
    "inherits_header": "#4A90D9",
    "visualizes": "#9B59B6",
    "annotates": "#F5A623",
    "groups": "#5CB85C",
}


# ---------------------------------------------------------------------------
# Layout helpers
# ---------------------------------------------------------------------------

def _build_adjacency(tree) -> Dict[str, List[str]]:
    """Return parent_id -> [child_ids] mapping."""
    children: Dict[str, List[str]] = {}
    for nid, node in tree.nodes.items():
        pid = node.parent_id
        if pid is not None:
            children.setdefault(pid, []).append(nid)
    return children


def _compute_positions(
    tree,
    children_map: Dict[str, List[str]],
    compact: bool = False,
) -> Dict[str, Tuple[float, float]]:
    """Compute (x, y) positions for each node using hierarchical layout."""
    positions: Dict[str, Tuple[float, float]] = {}
    y_gap = 1.5 if not compact else 1.0
    x_gap = 2.0 if not compact else 1.2

    # BFS to compute depth and width at each depth level
    depth_map: Dict[str, int] = {}
    depth_order: Dict[int, List[str]] = {}

    # Process root nodes first
    queue: List[Tuple[str, int]] = [(rid, 0) for rid in tree.root_nodes]
    while queue:
        nid, d = queue.pop(0)
        if nid in depth_map:
            continue
        depth_map[nid] = d
        depth_order.setdefault(d, []).append(nid)
        for cid in children_map.get(nid, []):
            queue.append((cid, d + 1))

    # Handle orphan nodes (not reachable from root_nodes)
    for nid in tree.nodes:
        if nid not in depth_map:
            depth_map[nid] = 0
            depth_order.setdefault(0, []).append(nid)

    # Assign x positions: evenly space nodes at each depth level
    for depth, nodes_at_depth in depth_order.items():
        width = len(nodes_at_depth)
        for i, nid in enumerate(nodes_at_depth):
            x = (i - (width - 1) / 2.0) * x_gap
            y = -depth * y_gap
            positions[nid] = (x, y)

    return positions


def _truncate(text: str, max_len: int = 30) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 3] + "..."


def _node_label(node, compact: bool = False) -> str:
    """Build display label for a tree node."""
    ntype = node.node_type
    title = node.title or ""

    if ntype == "section":
        return _truncate(title, 25) if title else "Section"
    elif ntype == "table":
        tid = node.table_id or ""
        short_tid = tid.split("_")[-1] if "_" in tid else tid
        if title:
            return f"{short_tid}\n{_truncate(title)}"
        return short_tid
    elif ntype == "table_partition":
        return "Partition"
    elif ntype == "context_block":
        return "Context"
    elif ntype == "note":
        return _truncate(title, 20) if title else "Note"
    elif ntype == "shape":
        sub = node.shape_subtype or "shape"
        return sub.capitalize()
    return ntype


# ---------------------------------------------------------------------------
# Core rendering
# ---------------------------------------------------------------------------

def render_tree_png(tree, rels, output_path: str, cfg=None) -> str:
    """Render a SheetObjectTree + relationships to a PNG file.

    Parameters
    ----------
    tree : SheetObjectTree
    rels : RelationshipGraph
    output_path : str
        Full path for the output PNG file.
    cfg : optional
        Config (unused currently, reserved for future styling knobs).

    Returns the output_path on success, empty string on failure.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.patches as mpatches
    except ImportError:
        _log.warning("matplotlib not available — skipping tree PNG render")
        return ""

    try:
        n_nodes = len(tree.nodes)
        if n_nodes == 0:
            return ""

        compact = n_nodes > 50
        collapse = n_nodes > 100

        children_map = _build_adjacency(tree)

        # In collapse mode, remove section children beyond a count badge
        display_nodes = dict(tree.nodes)
        collapsed_badges: Dict[str, int] = {}
        if collapse:
            for nid, node in tree.nodes.items():
                if node.node_type == "section":
                    kids = children_map.get(nid, [])
                    if len(kids) > 3:
                        collapsed_badges[nid] = len(kids)
                        # Keep only first 2 children
                        keep = set(kids[:2])
                        for kid in kids[2:]:
                            display_nodes.pop(kid, None)

        positions = _compute_positions(tree, children_map, compact=compact)

        # Figure sizing
        if compact:
            fig_w = max(8, n_nodes * 0.3)
            fig_h = max(6, n_nodes * 0.15)
            fontsize = 6
        else:
            fig_w = max(10, n_nodes * 0.8)
            fig_h = max(6, n_nodes * 0.4)
            fontsize = 8

        fig_w = min(fig_w, 40)
        fig_h = min(fig_h, 30)

        fig, ax = plt.subplots(1, 1, figsize=(fig_w, fig_h))
        ax.set_aspect("equal")
        ax.axis("off")

        # Draw edges (parent-child)
        for nid, node in display_nodes.items():
            if node.parent_id and node.parent_id in positions and nid in positions:
                px, py = positions[node.parent_id]
                cx, cy = positions[nid]
                ax.plot(
                    [px, cx], [py, cy],
                    color=_EDGE_COLOURS["contains"], linewidth=0.8, zorder=1,
                )

        # Draw relationship edges
        if rels and not compact:
            for edge in rels.edges:
                if edge.edge_type == "contains":
                    continue  # Already drawn as parent-child
                src_pos = positions.get(edge.source_id)
                tgt_pos = positions.get(edge.target_id)
                if src_pos and tgt_pos:
                    colour = _EDGE_COLOURS.get(edge.edge_type, "#888888")
                    ax.annotate(
                        "",
                        xy=tgt_pos, xytext=src_pos,
                        arrowprops=dict(
                            arrowstyle="->",
                            color=colour,
                            linestyle="dashed",
                            linewidth=0.6,
                        ),
                        zorder=2,
                    )

        # Draw nodes
        node_w = 1.6 if not compact else 0.9
        node_h = 0.6 if not compact else 0.35

        for nid, node in display_nodes.items():
            if nid not in positions:
                continue
            x, y = positions[nid]
            colour = _NODE_COLOURS.get(node.node_type, "#CCCCCC")
            label = _node_label(node, compact=compact)

            # Badge for collapsed sections
            if nid in collapsed_badges:
                label += f"\n[{collapsed_badges[nid]} children]"

            # Node shape
            if node.node_type == "shape":
                # Diamond for shapes
                diamond = mpatches.FancyBboxPatch(
                    (x - node_w / 2, y - node_h / 2), node_w, node_h,
                    boxstyle="round,pad=0.1",
                    facecolor=colour, edgecolor="#333333",
                    linewidth=0.8, alpha=0.85, zorder=3,
                )
                ax.add_patch(diamond)
            elif node.node_type == "section":
                box = mpatches.FancyBboxPatch(
                    (x - node_w / 2, y - node_h / 2), node_w, node_h,
                    boxstyle="round,pad=0.15",
                    facecolor=colour, edgecolor="#2C5F8A",
                    linewidth=1.2, alpha=0.9, zorder=3,
                )
                ax.add_patch(box)
            elif node.node_type == "table_partition":
                box = mpatches.FancyBboxPatch(
                    (x - node_w / 2, y - node_h / 2), node_w, node_h,
                    boxstyle="round,pad=0.1",
                    facecolor=colour, edgecolor="#4CAF50",
                    linewidth=0.8, linestyle="dashed", alpha=0.7, zorder=3,
                )
                ax.add_patch(box)
            else:
                box = mpatches.FancyBboxPatch(
                    (x - node_w / 2, y - node_h / 2), node_w, node_h,
                    boxstyle="round,pad=0.1",
                    facecolor=colour, edgecolor="#333333",
                    linewidth=0.8, alpha=0.85, zorder=3,
                )
                ax.add_patch(box)

            # Label
            weight = "bold" if node.node_type == "section" else "normal"
            ax.text(
                x, y, label,
                ha="center", va="center",
                fontsize=fontsize, fontweight=weight,
                color="white" if node.node_type in ("section",) else "black",
                zorder=4,
                clip_on=True,
            )

        # Title
        ax.set_title(f"Sheet: {tree.sheet_name}", fontsize=fontsize + 2, pad=10)

        plt.tight_layout()

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        fig.savefig(output_path, dpi=120, bbox_inches="tight")
        plt.close(fig)

        return output_path

    except Exception as exc:
        _log.warning("Tree PNG render failed for %s: %s", tree.sheet_name, exc)
        try:
            plt.close("all")
        except Exception:
            pass
        return ""


# ---------------------------------------------------------------------------
# Batch render
# ---------------------------------------------------------------------------

def render_all_trees(
    trees_by_sheet: Dict[str, Any],
    rels_by_sheet: Dict[str, Any],
    output_dir: str,
    cfg=None,
) -> Dict[str, str]:
    """Render tree PNGs for all sheets.

    Returns {sheet_name: png_path} for successfully rendered sheets.
    """
    png_dir = os.path.join(output_dir, "sheet_tree_visuals")
    os.makedirs(png_dir, exist_ok=True)

    results: Dict[str, str] = {}

    for sheet_name, tree in trees_by_sheet.items():
        rels = rels_by_sheet.get(sheet_name)
        safe_name = sheet_name.replace("/", "_").replace("\\", "_").replace(" ", "_")
        png_path = os.path.join(png_dir, f"{safe_name}_tree.png")

        path = render_tree_png(tree, rels, png_path, cfg)
        if path:
            results[sheet_name] = path

    return results
