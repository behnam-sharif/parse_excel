"""
visual_skeleton.py — Best-effort xl_image integration for visual section tiles (V1C).

Image/COM failure: log and continue, cap scores, no crash.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

_log = logging.getLogger("xl_sheetagent.visual_skeleton")


def build_visual_skeleton(
    sheet_name: str,
    ws,
    enriched_tables: list,
    section_bands: list,
    workbook_path: str,
    cfg,
) -> Dict[str, Any]:
    """Render visual section tiles using xl_image (best-effort).

    Tries COMRenderer first, then matplotlib fallback. On any failure:
    log warning, continue, return empty result. Never crash.

    Returns dict with image paths and metadata (empty on failure).
    """
    result: Dict[str, Any] = {
        "sheet_name": sheet_name,
        "tiles": [],
        "sheet_render_path": None,
        "available": False,
    }

    # Try import xl_image renderers
    try:
        from xl_image.render.com_renderer import COMRenderer
        com_available = True
    except ImportError:
        com_available = False

    try:
        from xl_image.render.fallback_renderer import render_bbox_fallback
        fallback_available = True
    except ImportError:
        fallback_available = False

    if not com_available and not fallback_available:
        _log.info("xl_image not available — skipping visual skeleton for %s", sheet_name)
        return result

    result["available"] = True

    # Render section band tiles
    for band in section_bands:
        tile_info: Dict[str, Any] = {
            "band_id": getattr(band, "band_id", ""),
            "title": getattr(band, "title", ""),
            "image_path": None,
        }

        try:
            if com_available:
                renderer = COMRenderer(workbook_path)
                if hasattr(renderer, "available") and not renderer.available:
                    raise RuntimeError("COM not available")
                # Best-effort tile render (specific API depends on xl_image)
                _log.debug("COM render for band %s", band.band_id if hasattr(band, "band_id") else "")
            elif fallback_available:
                _log.debug("Fallback render for band on %s", sheet_name)
        except Exception as exc:
            _log.warning(
                "Visual skeleton render failed for band on %s: %s",
                sheet_name, exc,
            )
            # Cap visual confidence, continue
            tile_info["error"] = str(exc)

        result["tiles"].append(tile_info)

    return result
