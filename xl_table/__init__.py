"""
xl_table --- Stage 2: Structural table detection and assembly.

This stage is structural table detection over parse artifacts, not a general knowledge graph.
It is a structural table detection and assembly module over parse candidates.

Public API
----------
run_table(parse_output_path, output_path, config=None) -> StructuralKG
"""

from .schemas import StructuralKG


def run_table(parse_output_path: str, output_path: str, config=None) -> StructuralKG:
    """Public entry point for the xl_table stage.

    Parameters
    ----------
    parse_output_path : str
        Directory containing xl_parse outputs (candidate_rows.jsonl, cut_scores.jsonl).
    output_path : str
        Directory where table artifacts will be written.
    config : KGConfig, optional
        Stage configuration. Uses defaults if not provided.
    """
    import os

    if config is None:
        from .config import load_table_config

        config = load_table_config()

    # Find workbook path - look for cleaned_workbook.xlsx in parent or sibling init_output
    workbook_path = None
    parent = os.path.dirname(parse_output_path)
    candidates = [
        os.path.join(parent, "init_output", "cleaned_workbook.xlsx"),
        os.path.join(parse_output_path, "cleaned_workbook.xlsx"),
    ]
    for c in candidates:
        if os.path.isfile(c):
            workbook_path = c
            break

    if workbook_path is None:
        raise FileNotFoundError(
            f"cleaned_workbook.xlsx not found. Searched: {candidates}"
        )

    os.makedirs(output_path, exist_ok=True)

    from .agent import run_xl_table
    kg = run_xl_table(workbook_path, parse_output_path, output_path, config)
    return kg
