"""
xl_dag/workbook_name.py — Resolve ``workbook_name`` for output artifacts.

The TS contracts (``dag.schema.ts``, ``dagSummary.schema.ts``,
``tableMapOverview.schema.ts``) require a ``workbook_name`` field, but
neither ``summary.json`` nor ``final_table_map.json`` carries it.

Resolution order:
    1. Original uploaded xlsx filename stem (from xl_init's input record or
       a non-cleaned xlsx sitting under init_output/).
    2. ``cleaned_workbook.xlsx`` → ``"cleaned_workbook"`` (generic fallback).
    3. ``run_id`` (final fallback).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from core.paths import get_init_output_path


def resolve_workbook_name(run_id: str, output_base: str = "data/output") -> str:
    init_dir = get_init_output_path(run_id, output_base)

    # (1) Try xl_init's manifest / input record for the ORIGINAL filename.
    for candidate in ("approved_sheets.json", "sheet_classifications.json", "run_manifest.json"):
        p = init_dir / candidate
        name = _read_workbook_stem_from_json(p)
        if name:
            return name

    # (1b) Look for any non-cleaned xlsx sitting in init_output/.
    if init_dir.exists():
        for xlsx in init_dir.glob("*.xlsx"):
            if xlsx.stem.lower() != "cleaned_workbook":
                return xlsx.stem

    # (2) Fallback: cleaned workbook stem.
    cleaned = init_dir / "cleaned_workbook.xlsx"
    if cleaned.exists():
        return cleaned.stem

    # (3) Final fallback.
    return run_id


def _read_workbook_stem_from_json(path: Path) -> Optional[str]:
    if not path.exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return None
    # Try common keys used by xl_init manifests.
    for key in ("input_path", "workbook_path", "original_input", "input_file"):
        v = _extract_stem(data, key)
        if v:
            return v
    return None


def _extract_stem(data: object, key: str) -> Optional[str]:
    if isinstance(data, dict):
        if key in data and isinstance(data[key], str) and data[key]:
            return Path(data[key]).stem
        for v in data.values():
            found = _extract_stem(v, key)
            if found:
                return found
    elif isinstance(data, list):
        for item in data:
            found = _extract_stem(item, key)
            if found:
                return found
    return None
