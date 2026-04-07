"""
xl_init — Stage 0-1: Workbook ingest, VBA cleaning, sheet approval, metadata extraction.

Public API
----------
run_init(input_path, output_path, config=None) -> XlInitResult
"""

from .api import run_xl_init
from .config import XlInitConfig
from .types_data import XlInitResult


def run_init(input_path: str, output_path: str, config=None) -> XlInitResult:
    """Public entry point for the xl_init stage.

    Parameters
    ----------
    input_path : str
        Path to the input Excel workbook (.xlsx or .xlsm).
    output_path : str
        Directory where init artifacts will be written.
    config : XlInitConfig, optional
        Stage configuration. Uses defaults if not provided.
    """
    return run_xl_init(input_path, output_path, config=config)
