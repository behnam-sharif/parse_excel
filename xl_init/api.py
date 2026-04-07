"""
api.py — Public entry-point for the xl_init pipeline.

Provides:
  - run_xl_init(): full pipeline (validate -> clean -> summarize -> triage -> review)
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict
from typing import Optional

from .config import XlInitConfig
from .types_data import XlInitResult
from .pipeline import (
    _validate_workbook,
    _run_vba_cleaning,
    _build_sheet_summaries,
    _user_review_sheets,
    _auto_approve_sheets,
    _auto_approve_all_sheets,
    _extract_sheet_metadata,
    _write_jsonl,
)
from .sheet_triage import classify_sheets

logger = logging.getLogger(__name__)


def run_xl_init(
    workbook_path: str,
    out_dir: str,
    config: Optional[XlInitConfig] = None,
) -> XlInitResult:
    """
    Full xl_init pipeline:
      1. Validate workbook path
      2. VBA cleaning (shapes removal + unhide)
      3. Generate per-sheet summaries (bounding box + cell sample + GPT description)
      4. Classify sheets via Claude LLM (table / text_only / references / unknown)
      5. User review (interactive: confirm/add/remove sheets)
      6. Return cleaned path + approved table sheet list
    """
    if config is None:
        from core.config import load_config
        from core.stage_config import xl_init_config_from_pipeline

        config = xl_init_config_from_pipeline(load_config())

    # -- Setup --
    os.makedirs(out_dir, exist_ok=True)
    log_dir = os.path.join(out_dir, "logs")
    os.makedirs(log_dir, exist_ok=True)

    # File handler for this run
    fh = logging.FileHandler(os.path.join(log_dir, "xl_init.log"))
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
    ))
    logging.getLogger().addHandler(fh)
    logging.getLogger().setLevel(logging.INFO)

    logger.info("xl_init started: workbook=%s  out_dir=%s", workbook_path, out_dir)

    # -- Stage 1: Validate --
    abs_path = _validate_workbook(workbook_path)

    # -- Stage 2: VBA Cleaning --
    if config.run_vba_cleaning:
        cleaned_path = _run_vba_cleaning(abs_path, out_dir)
    else:
        # Skip cleaning -- use original workbook
        import shutil
        cleaned_path = os.path.join(out_dir, "cleaned_workbook.xlsx")
        shutil.copy2(abs_path, cleaned_path)
        logger.info("Stage 2: Skipped VBA cleaning; copied original to %s", cleaned_path)

    # -- Stage 3: Sheet Summaries --
    summaries = _build_sheet_summaries(cleaned_path, cell_limit=config.cell_limit)

    # Write summaries to JSONL
    summaries_path = os.path.join(out_dir, "sheet_summaries.jsonl")
    _write_jsonl(summaries_path, [asdict(s) for s in summaries.values()])
    logger.info("Stage 3: Wrote %d summaries -> %s", len(summaries), summaries_path)

    # -- Stage 4: LLM Sheet Triage --
    classification_path = os.path.join(out_dir, "sheet_classification.jsonl")

    if config.skip_llm_triage:
        # Pipeline mode: skip LLM, write stub classifications
        from .types_data import SheetClassification
        classifications = {
            sn: SheetClassification(
                sheet_name=sn,
                class_label="unknown",
                confidence=0.0,
                rationale_short="skipped (pipeline mode)",
            )
            for sn in summaries
        }
        table_sheets = list(summaries.keys())
        excluded_sheets = []
        token_usage = {"total_tokens": 0}
        _write_jsonl(classification_path, [asdict(c) for c in classifications.values()])
        logger.info("Stage 4: Skipped LLM triage (pipeline mode); wrote stub classifications")
    else:
        classifications, table_sheets, excluded_sheets, token_usage = classify_sheets(
            summaries,
            model=config.llm_model,
            max_tokens=config.llm_max_tokens,
        )
        _write_jsonl(classification_path, [asdict(c) for c in classifications.values()])
        logger.info("Stage 4: Wrote classifications -> %s  (tokens: %d)", classification_path, token_usage["total_tokens"])

    # -- Stage 4b: Metadata Extraction --
    try:
        _extract_sheet_metadata(cleaned_path, list(summaries.keys()), out_dir)
    except Exception as exc:
        logger.warning("Metadata extraction failed (non-fatal): %s", exc)

    # -- Stage 5: User Review --
    all_sheets = list(summaries.keys())

    if config.approve_all_sheets:
        final_sheets = _auto_approve_all_sheets(summaries, out_dir)
    elif config.interactive:
        final_sheets = _user_review_sheets(
            classifications, table_sheets, all_sheets, out_dir,
        )
    else:
        final_sheets = _auto_approve_sheets(table_sheets, out_dir)

    approved_path = os.path.join(out_dir, "approved_sheets.json")

    # -- Return --
    result = XlInitResult(
        cleaned_workbook_path=cleaned_path,
        table_sheets=final_sheets,
        sheet_classification_path=classification_path,
        approved_sheets_path=approved_path,
    )
    logger.info("xl_init complete: %d table sheets approved", len(final_sheets))
    return result
