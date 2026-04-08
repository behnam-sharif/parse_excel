"""
api.py — Public entry-point for the xl_init pipeline.

Minimal production path:
  validate → VBA cleaning → sheet metadata (figures/textboxes/etc.) → approve all data sheets

Per-sheet pandas summaries, LLM descriptions, and LLM triage are **not** run (legacy code remains
in ``pipeline.py`` but is unused here).
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from core.env_bootstrap import load_project_dotenv

from .config import XlInitConfig
from .types_data import XlInitResult
from .pipeline import (
    _validate_workbook,
    _run_vba_cleaning,
    _list_data_sheet_names,
    _write_minimal_classification_and_approved,
    _extract_sheet_metadata,
)

logger = logging.getLogger(__name__)


def run_xl_init(
    workbook_path: str,
    out_dir: str,
    config: Optional[XlInitConfig] = None,
) -> XlInitResult:
    """
    xl_init pipeline (streamlined):

      1. Validate workbook path
      2. VBA cleaning (shapes removal + unhide) or copy-through
      3. List data sheets; write stub ``sheet_classification.jsonl`` + ``approved_sheets.json`` (all sheets)
      4. Extract per-sheet metadata (hidden rows/cols, CF, validations, …) under ``sheet_metadata/``

    Config fields such as ``skip_llm_triage`` / ``approve_all_sheets`` are ignored for behavior
    (kept on ``XlInitConfig`` for YAML/dataclass compatibility only).
    """
    load_project_dotenv()
    if config is None:
        from core.config import load_config
        from core.stage_config import xl_init_config_from_pipeline

        config = xl_init_config_from_pipeline(load_config())

    # -- Setup --
    os.makedirs(out_dir, exist_ok=True)
    log_dir = os.path.join(out_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)

    xl_init_log = logging.getLogger("xl_init")
    log_path = os.path.abspath(os.path.join(log_dir, "xl_init.log"))
    for h in list(xl_init_log.handlers):
        if isinstance(h, logging.FileHandler) and os.path.abspath(h.baseFilename) == log_path:
            xl_init_log.removeHandler(h)
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    )
    xl_init_log.addHandler(fh)
    xl_init_log.setLevel(logging.INFO)
    xl_init_log.propagate = False

    logger.info("xl_init started: workbook=%s  out_dir=%s", workbook_path, out_dir)

    # -- Stage 1: Validate --
    abs_path = _validate_workbook(workbook_path)

    # -- Stage 2: VBA Cleaning --
    if config.run_vba_cleaning:
        cleaned_path = _run_vba_cleaning(abs_path, out_dir)
    else:
        import shutil

        cleaned_path = os.path.join(out_dir, "cleaned_workbook.xlsx")
        shutil.copy2(abs_path, cleaned_path)
        logger.info("Stage 2: Skipped VBA cleaning; copied original to %s", cleaned_path)

    # -- Stage 3: Sheet list + stub artifacts (no summaries / no LLM) --
    sheet_names = _list_data_sheet_names(cleaned_path)
    classification_path, final_sheets = _write_minimal_classification_and_approved(
        out_dir, sheet_names
    )
    approved_path = os.path.join(out_dir, "approved_sheets.json")

    # -- Stage 4: Metadata extraction (textboxes, images, figures-related workbook state, etc.) --
    try:
        _extract_sheet_metadata(cleaned_path, list(sheet_names), out_dir)
    except Exception as exc:
        logger.warning("Metadata extraction failed (non-fatal): %s", exc)

    # -------------------------------------------------------------------------
    # Legacy stages (removed from active path — were slow in Streamlit):
    #
    # - _build_sheet_summaries(...)  # pandas per sheet + optional GPT descriptions
    # - classify_sheets(...)         # LLM triage
    # - _user_review_sheets / _auto_approve_* 
    #
    # See pipeline.py for the old implementations.
    # -------------------------------------------------------------------------

    result = XlInitResult(
        cleaned_workbook_path=cleaned_path,
        table_sheets=final_sheets,
        sheet_classification_path=classification_path,
        approved_sheets_path=approved_path,
    )
    logger.info("xl_init complete: %d sheets in approved list", len(final_sheets))
    return result
