"""
parse_tools — Package for Excel table detection and cell analysis.

Re-exports all public names for backward compatibility with the old flat parse_tools.py.
"""

from .models import (
    BBox, BlockCandidate, CellFeatures, BlockFeatures, TableDecision, Config, CutCandidate,
    QCEvent, HeaderInfo, KeyColumnInfo, FinalCandidate, SheetResult,
)
from .presence import (
    get_presence_matrix_for_sheet,
    get_presence_matrix_from_accessor,
    bbox_to_excel_range,
    CELL_REF_PATTERN,
)
from .sheet_accessor import SheetAccessor
from .cell_classifier import classify_cell_type, extract_cell_features
from .feature_extraction import classify_block_cells, compute_block_features
from .id_utils import compute_workbook_id, compute_candidate_id

__all__ = [
    # models
    "BBox",
    "BlockCandidate",
    "CellFeatures",
    "BlockFeatures",
    "TableDecision",
    "Config",
    "CutCandidate",
    "QCEvent",
    "HeaderInfo",
    "KeyColumnInfo",
    "FinalCandidate",
    "SheetResult",
    # presence
    "get_presence_matrix_for_sheet",
    "get_presence_matrix_from_accessor",
    "bbox_to_excel_range",
    "CELL_REF_PATTERN",
    # sheet_accessor
    "SheetAccessor",
    # cell_classifier
    "classify_cell_type",
    "extract_cell_features",
    # feature_extraction
    "classify_block_cells",
    "compute_block_features",
    # id_utils
    "compute_workbook_id",
    "compute_candidate_id",
]
