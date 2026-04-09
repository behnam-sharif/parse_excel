"""
upstream_compat.py — Normalise all upstream inputs for xl_sheetagent.

Single place for field defaults, fallback logic, and version detection.
Implements the REPO FILENAME MAP from spec §3.

Field classifications:
    REQUIRED — fail fast if missing
    EXPECTED — apply fallback default, log warning
    OPTIONAL — skip gracefully
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.paths import (
    get_init_output_path,
    get_parse_output_path,
    get_table_output_path,
)

_log = logging.getLogger("xl_sheetagent.upstream_compat")


# ---------------------------------------------------------------------------
# Normalised upstream view
# ---------------------------------------------------------------------------

@dataclass
class TableObjectView:
    """Normalised view of a TableObject from structural_tables.json."""
    table_id: str
    sheet_name: str
    r_top: int
    c_left: int
    r_bot: int
    c_right: int
    bbox_excel: str
    header_row_indices: List[int]
    header_confidence: float
    structural_type: str
    provenance: Dict[str, Any]
    n_rows: int
    n_cols: int
    density: float
    split_depth: int
    parent_table_id: Optional[str]
    split_lineage: List[str]
    finalize_reason: str
    body_bbox: Optional[Dict[str, int]]
    split_history: Dict[str, Any]
    confidence: float


@dataclass
class UpstreamData:
    """All upstream inputs normalised for xl_sheetagent consumption."""
    workbook_path: str
    approved_sheets: List[str]
    shapes_manifest: List[Dict[str, Any]]
    candidate_rows: List[Dict[str, Any]]
    rejected_blocks: List[Dict[str, Any]]
    structural_tables_raw: Dict[str, Any]
    table_objects: List[TableObjectView]
    schema_versions: Dict[str, str] = field(default_factory=dict)
    # Optional: xl_parse cut_scores.jsonl (full lines)
    cut_scores: List[Dict[str, Any]] = field(default_factory=list)
    # Optional: xl_table decisions.jsonl (split_attempt / finalize / …)
    table_decisions: List[Dict[str, Any]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> Any:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _normalise_provenance(raw: Any) -> Dict[str, Any]:
    """Ensure provenance is {method: str, ancestor_ids: list}."""
    if isinstance(raw, str):
        return {"method": raw, "ancestor_ids": []}
    if isinstance(raw, dict):
        method = raw.get("method", "unknown")
        ancestor_ids = raw.get("ancestor_ids", [])
        return {"method": method, "ancestor_ids": ancestor_ids}
    return {"method": "unknown", "ancestor_ids": []}


def _normalise_candidate_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Apply REQUIRED / EXPECTED / OPTIONAL rules to a candidate_rows entry."""
    # REQUIRED — fail fast
    for key in ("candidate_id", "sheet_name", "density"):
        if key not in row:
            raise ValueError(f"candidate_rows.jsonl: missing REQUIRED field {key!r}")

    # bbox: accept either {r0,c0,r1,c1} or {bbox_r1,...} or {r_top,...}
    if "bbox" in row and isinstance(row["bbox"], dict):
        b = row["bbox"]
        row.setdefault("r_top", b.get("r0", b.get("r_top", b.get("r1", 0))))
        row.setdefault("c_left", b.get("c0", b.get("c_left", b.get("c1", 0))))
        row.setdefault("r_bot", b.get("r1", b.get("r_bot", b.get("r2", 0))))
        row.setdefault("c_right", b.get("c1", b.get("c_right", b.get("c2", 0))))

    # EXPECTED — fallback + warn
    if "provenance" in row:
        row["provenance"] = _normalise_provenance(row["provenance"])
    else:
        _log.warning("candidate_rows: missing EXPECTED field 'provenance', defaulting")
        row["provenance"] = {"method": "unknown", "ancestor_ids": []}

    row.setdefault("header_row_indices", [])
    if "header_row_indices" not in row or row["header_row_indices"] is None:
        _log.warning("candidate_rows: missing EXPECTED 'header_row_indices', defaulting to []")
        row["header_row_indices"] = []

    row.setdefault("header_confidence", 0.0)
    if row["header_confidence"] is None:
        row["header_confidence"] = 0.0

    row.setdefault("separator_scores", [])
    row.setdefault("qc_flags", [])
    row.setdefault("structural_type", "table")
    row.setdefault("bbox_stats", {})

    # OPTIONAL — skip gracefully
    row.setdefault("col_separator_scores", [])
    row.setdefault("cell_features", {})

    return row


def _normalise_table_object(raw: Dict[str, Any]) -> TableObjectView:
    """Build a normalised TableObjectView from a raw table dict."""
    # REQUIRED
    for key in ("table_id", "sheet_name"):
        if key not in raw:
            raise ValueError(f"structural_tables.json table: missing REQUIRED {key!r}")

    # Coordinates
    r_top = raw.get("r_top", 0)
    c_left = raw.get("c_left", 0)
    r_bot = raw.get("r_bot", 0)
    c_right = raw.get("c_right", 0)

    # EXPECTED with fallbacks
    header_row_indices = raw.get("header_row_indices", [])
    if header_row_indices is None:
        header_row_indices = []

    header_confidence = raw.get("header_confidence", 0.0)
    if header_confidence is None:
        header_confidence = 0.0

    body_bbox = raw.get("body_bbox")
    if body_bbox is None:
        body_bbox = {"r_top": r_top, "c_left": c_left, "r_bot": r_bot, "c_right": c_right}

    split_history = raw.get("split_history")
    if split_history is None:
        split_history = {"was_split": False}

    confidence = raw.get("confidence", 0.5)
    if confidence is None:
        confidence = 0.5

    provenance = _normalise_provenance(raw.get("provenance", "unknown"))

    return TableObjectView(
        table_id=raw["table_id"],
        sheet_name=raw["sheet_name"],
        r_top=r_top,
        c_left=c_left,
        r_bot=r_bot,
        c_right=c_right,
        bbox_excel=raw.get("bbox_excel", ""),
        header_row_indices=header_row_indices,
        header_confidence=header_confidence,
        structural_type=raw.get("structural_type", "table"),
        provenance=provenance,
        n_rows=raw.get("n_rows", r_bot - r_top + 1),
        n_cols=raw.get("n_cols", c_right - c_left + 1),
        density=raw.get("density", 0.0),
        split_depth=raw.get("split_depth", 0),
        parent_table_id=raw.get("parent_table_id"),
        split_lineage=raw.get("split_lineage", []),
        finalize_reason=raw.get("finalize_reason", ""),
        body_bbox=body_bbox,
        split_history=split_history,
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# Public loader
# ---------------------------------------------------------------------------

def load_upstream(run_id: str, base: str = "data/output") -> UpstreamData:
    """Load and normalise all upstream stage outputs for a run.

    Raises on missing REQUIRED files/fields. Logs warnings for EXPECTED fallbacks.
    """
    init_dir = get_init_output_path(run_id, base)
    parse_dir = get_parse_output_path(run_id, base)
    table_dir = get_table_output_path(run_id, base)

    # ---- xl_init outputs ----
    wb_path = init_dir / "cleaned_workbook.xlsx"
    if not wb_path.is_file():
        raise FileNotFoundError(f"REQUIRED: {wb_path}")
    workbook_path = str(wb_path)

    approved_path = init_dir / "approved_sheets.json"
    if not approved_path.is_file():
        raise FileNotFoundError(f"REQUIRED: {approved_path}")
    approved_raw = _read_json(approved_path)
    # xl_init writes {"approved_sheets": [...]}; accept a raw list too.
    if isinstance(approved_raw, list):
        approved_sheets = approved_raw
    elif isinstance(approved_raw, dict) and "approved_sheets" in approved_raw:
        approved_sheets = approved_raw["approved_sheets"]
        if not isinstance(approved_sheets, list):
            raise ValueError(
                "approved_sheets.json: key 'approved_sheets' must be a list, "
                f"got {type(approved_sheets).__name__}"
            )
    else:
        raise ValueError(
            "approved_sheets.json must be a list or an object with key 'approved_sheets', "
            f"got {type(approved_raw).__name__}"
        )

    shapes_path = init_dir / "shapes_manifest.jsonl"
    if shapes_path.is_file():
        shapes_manifest = _read_jsonl(shapes_path)
    else:
        _log.warning("shapes_manifest.jsonl not found — shapes will be empty")
        shapes_manifest = []

    # ---- xl_parse outputs ----
    cr_path = parse_dir / "candidate_rows.jsonl"
    if not cr_path.is_file():
        raise FileNotFoundError(f"REQUIRED: {cr_path}")
    candidate_rows_raw = _read_jsonl(cr_path)
    candidate_rows = [_normalise_candidate_row(r) for r in candidate_rows_raw]

    rb_path = parse_dir / "rejected_blocks.jsonl"
    if rb_path.is_file():
        rejected_blocks = _read_jsonl(rb_path)
    else:
        _log.warning("rejected_blocks.jsonl not found — rejected blocks will be empty")
        rejected_blocks = []

    cs_path = parse_dir / "cut_scores.jsonl"
    if cs_path.is_file():
        cut_scores = _read_jsonl(cs_path)
    else:
        _log.warning("cut_scores.jsonl not found — Step 2 will not use parse cuts")
        cut_scores = []

    # ---- xl_table outputs ----
    st_path = table_dir / "structural_tables.json"
    if not st_path.is_file():
        raise FileNotFoundError(f"REQUIRED: {st_path}")
    structural_tables_raw = _read_json(st_path)

    dec_path = table_dir / "decisions.jsonl"
    if dec_path.is_file():
        table_decisions = _read_jsonl(dec_path)
    else:
        _log.warning("decisions.jsonl not found — Step 2 will not use xl_table split decisions")
        table_decisions = []

    # Version detection
    xl_table_version = structural_tables_raw.get("xl_table_version", "unknown")
    schema_versions = {
        "xl_parse": "unknown",
        "xl_table": xl_table_version,
    }

    # Normalise table objects
    table_objects: List[TableObjectView] = []
    for sheet in structural_tables_raw.get("sheets", []):
        for tbl in sheet.get("tables", []):
            table_objects.append(_normalise_table_object(tbl))

    return UpstreamData(
        workbook_path=workbook_path,
        approved_sheets=approved_sheets,
        shapes_manifest=shapes_manifest,
        candidate_rows=candidate_rows,
        rejected_blocks=rejected_blocks,
        structural_tables_raw=structural_tables_raw,
        table_objects=table_objects,
        schema_versions=schema_versions,
        cut_scores=cut_scores,
        table_decisions=table_decisions,
    )
