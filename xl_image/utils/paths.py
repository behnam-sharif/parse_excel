"""Path helpers for xl_image output layout."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict

from openpyxl.utils import get_column_letter


def sanitize_sheet_name(name: str) -> str:
    """Replace characters invalid in Windows/Unix directory names."""
    return re.sub(r'[\\/:*?"<>|]', "_", name)


def get_image_dir(xl_image_out: Path, sheet_name: str) -> Path:
    return xl_image_out / "images" / sanitize_sheet_name(sheet_name)


def get_features_dir(xl_image_out: Path, sheet_name: str) -> Path:
    return xl_image_out / "features" / sanitize_sheet_name(sheet_name)


def get_bbox_image_path(xl_image_out: Path, sheet_name: str, candidate_id: str) -> Path:
    return get_image_dir(xl_image_out, sheet_name) / f"{candidate_id}.png"


def get_context_image_path(xl_image_out: Path, sheet_name: str, candidate_id: str) -> Path:
    return get_image_dir(xl_image_out, sheet_name) / f"{candidate_id}__context.png"


def get_feature_path(xl_image_out: Path, sheet_name: str, candidate_id: str) -> Path:
    return get_features_dir(xl_image_out, sheet_name) / f"{candidate_id}.json"


def bbox_cells_to_excel_range(bbox_cells: Dict[str, int]) -> str:
    """Convert 0-indexed bbox_cells {r1,c1,r2,c2} to Excel A1-style range, e.g. 'B48:L64'."""
    r1 = int(bbox_cells["r1"])
    c1 = int(bbox_cells["c1"])
    r2 = int(bbox_cells["r2"])
    c2 = int(bbox_cells["c2"])
    tl = f"{get_column_letter(c1 + 1)}{r1 + 1}"
    br = f"{get_column_letter(c2 + 1)}{r2 + 1}"
    return f"{tl}:{br}"
