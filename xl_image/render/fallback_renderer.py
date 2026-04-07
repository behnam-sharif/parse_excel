"""
Fallback renderer: openpyxl cell reading + matplotlib grid drawing.

Used when Excel COM is unavailable or fails for an individual bbox.
Renders cell values with fill colors, bold text, and a light cell grid.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional, Tuple

from ..config import XlImageConfig

logger = logging.getLogger(__name__)


def _argb_to_rgb(argb: Optional[str]) -> Optional[Tuple[float, float, float]]:
    """Convert 8-char ARGB hex (e.g. 'FFAABB33') to normalised (r, g, b) tuple."""
    if not argb:
        return None
    argb = argb.lstrip("#")
    try:
        if len(argb) == 8:
            r = int(argb[2:4], 16) / 255.0
            g = int(argb[4:6], 16) / 255.0
            b = int(argb[6:8], 16) / 255.0
            return (r, g, b)
        if len(argb) == 6:
            r = int(argb[0:2], 16) / 255.0
            g = int(argb[2:4], 16) / 255.0
            b = int(argb[4:6], 16) / 255.0
            return (r, g, b)
    except (ValueError, TypeError):
        pass
    return None


def render_bbox_fallback(
    workbook_path: str,
    sheet_name: str,
    r0: int, c0: int, r1: int, c1: int,
    output_path: str,
    config: Optional[XlImageConfig] = None,
    nrows_sheet: Optional[int] = None,
    ncols_sheet: Optional[int] = None,
) -> bool:
    """
    Render a bbox (0-indexed inclusive) as a matplotlib cell grid.
    Returns True on success, False on any failure.
    """
    cfg = config or XlImageConfig()
    try:
        return _render(
            workbook_path, sheet_name,
            r0, c0, r1, c1,
            output_path, cfg, nrows_sheet, ncols_sheet,
        )
    except Exception as e:
        logger.warning(
            "Fallback render failed [%s r%d:c%d-r%d:c%d -> %s]: %s",
            sheet_name, r0, c0, r1, c1, output_path, e,
            exc_info=True,
        )
        raise


def _render(
    workbook_path: str,
    sheet_name: str,
    r0: int, c0: int, r1: int, c1: int,
    output_path: str,
    cfg: XlImageConfig,
    nrows_sheet: Optional[int],
    ncols_sheet: Optional[int],
) -> bool:
    import openpyxl
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.patches as mpatches

    wb = openpyxl.load_workbook(workbook_path, data_only=True, read_only=False)
    try:
        ws = wb[sheet_name]

        # Clamp to sheet bounds
        max_row = nrows_sheet if nrows_sheet else (ws.max_row or 1)
        max_col = ncols_sheet if ncols_sheet else (ws.max_column or 1)
        r1_c = min(r1, max_row - 1)
        c1_c = min(c1, max_col - 1)

        nrows = r1_c - r0 + 1
        ncols = c1_c - c0 + 1

        fig_w = max(ncols * cfg.fallback_cell_width_in, 1.0)
        fig_h = max(nrows * cfg.fallback_cell_height_in, 0.5)

        fig, ax = plt.subplots(figsize=(fig_w, fig_h))
        ax.set_xlim(0, ncols)
        ax.set_ylim(0, nrows)
        ax.invert_yaxis()
        ax.axis("off")

        for rel_r in range(nrows):
            abs_row = r0 + rel_r + 1   # openpyxl is 1-indexed
            for rel_c in range(ncols):
                abs_col = c0 + rel_c + 1

                try:
                    cell = ws.cell(row=abs_row, column=abs_col)
                except Exception:
                    continue

                # Cell text
                value = cell.value
                text = "" if value is None else str(value)
                if len(text) > cfg.max_cell_text_chars:
                    text = text[: cfg.max_cell_text_chars - 1] + "\u2026"

                # Fill colour
                fill_color: object = "white"
                try:
                    fg = cell.fill.fgColor
                    if fg and fg.type == "rgb":
                        rgb = _argb_to_rgb(fg.rgb)
                        if rgb and fg.rgb not in ("00000000", "FF000000", "FFFFFFFF"):
                            fill_color = rgb
                except Exception:
                    pass

                # Draw cell rectangle
                rect = mpatches.FancyBboxPatch(
                    (rel_c, rel_r), 1.0, 1.0,
                    boxstyle="square,pad=0",
                    linewidth=0.3,
                    edgecolor="#aaaaaa",
                    facecolor=fill_color,
                )
                ax.add_patch(rect)

                # Text (bold if cell.font.bold)
                bold = False
                try:
                    bold = bool(cell.font and cell.font.bold)
                except Exception:
                    pass

                if text:
                    ax.text(
                        rel_c + 0.5, rel_r + 0.5, text,
                        ha="center", va="center",
                        fontsize=cfg.fallback_font_size,
                        fontweight="bold" if bold else "normal",
                        clip_on=True,
                    )

        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(output_path, dpi=cfg.fallback_dpi, bbox_inches="tight", pad_inches=0.02)
        plt.close(fig)
    finally:
        wb.close()

    return Path(output_path).exists()
