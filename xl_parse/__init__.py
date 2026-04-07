"""
xl_parse — Stage 1: Deterministic structural candidate extraction.

Public API
----------
run_parse(input_path, output_path, config=None) -> dict[str, SheetResult]
"""

from .parse_tools.models import Config, SheetResult
from .pipeline import parse_workbook
from .run_export import export_run


def run_parse(input_path: str, output_path: str, config=None):
    """Public entry point for the xl_parse stage.

    Parameters
    ----------
    input_path : str
        Path to the cleaned workbook (.xlsx).
    output_path : str
        Directory where parse artifacts will be written.
    config : Config, optional
        Stage configuration. If omitted, loads from ``config/stages.yaml`` via ``core.config``.

    Returns
    -------
    dict[str, SheetResult]
        Per-sheet parse results keyed by sheet name.
    """
    import json
    import os

    if config is None:
        from core.config import load_config
        from core.stage_config import parse_config_from_pipeline

        config = parse_config_from_pipeline(load_config())

    # Load approved sheets
    approved_path = os.path.join(os.path.dirname(input_path), "approved_sheets.json")
    if os.path.isfile(approved_path):
        with open(approved_path, "r") as f:
            approved = json.load(f).get("approved_sheets", [])
    else:
        # If no approved_sheets.json, parse all sheets
        from openpyxl import load_workbook
        wb = load_workbook(input_path, read_only=True)
        approved = wb.sheetnames
        wb.close()

    results = parse_workbook(
        file_path=input_path,
        sheet_names=approved,
        config=config,
        export_base_dir=output_path,
    )

    return results
