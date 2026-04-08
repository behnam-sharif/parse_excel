"""
config.py — Configuration dataclass for xl_init pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class XlInitConfig:
    """Centralised settings for the xl_init pipeline."""
    run_vba_cleaning: bool = True
    llm_model: str = "gpt-3.5-turbo"
    llm_max_tokens: int = 1024
    cell_limit: int = 50                # Max cells per sheet sent to LLM
    interactive: bool = True            # Enable user review step
    skip_llm_triage: bool = True        # Skip OpenAI (sheet descriptions + triage)
    approve_all_sheets: bool = True     # Auto-approve all sheets (pipeline mode)
