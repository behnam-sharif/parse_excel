"""
xl_image — Stage 3 (optional): Visual rendering and image-derived feature extraction.

Public API
----------
run_image(parse_output_path, output_path, config=None)
"""

from .config import XlImageConfig


def run_image(parse_output_path: str, output_path: str, config=None):
    """Public entry point for the xl_image stage.

    Parameters
    ----------
    parse_output_path : str
        Directory containing xl_parse outputs (``candidate_rows.jsonl`` at root).
    output_path : str
        Directory where image artifacts will be written.
    config : XlImageConfig, optional
        Stage configuration. Uses defaults if not provided.
    """
    import os

    if config is None:
        from core.config import load_config
        from core.stage_config import xl_image_config_from_pipeline

        config = xl_image_config_from_pipeline(load_config())

    # Find workbook and candidates
    parent = os.path.dirname(parse_output_path)
    workbook_path = None
    for candidate in [
        os.path.join(parent, "init_output", "cleaned_workbook.xlsx"),
        os.path.join(parse_output_path, "cleaned_workbook.xlsx"),
    ]:
        if os.path.isfile(candidate):
            workbook_path = candidate
            break

    if workbook_path is None:
        raise FileNotFoundError("cleaned_workbook.xlsx not found")

    candidates_path = os.path.join(parse_output_path, "candidate_rows.jsonl")
    if not os.path.isfile(candidates_path):
        raise FileNotFoundError(
            f"candidate_rows.jsonl not found at {candidates_path!r}. Run xl_parse first."
        )

    os.makedirs(output_path, exist_ok=True)

    from .run import run as _run_image
    _run_image(
        workbook_path=workbook_path,
        candidates_path=candidates_path,
        out_dir=output_path,
        config=config,
    )
