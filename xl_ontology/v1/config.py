"""Pass-2 runtime config — derived from XlOntologyConfig.

Kept as its own dataclass so Pass 2 internals don't reach into the
broader stage config. The runner builds a Pass2Config from
``XlOntologyConfig`` once at the top of ``run_pass2``.
"""

from __future__ import annotations

from dataclasses import dataclass

from xl_ontology.config import XlOntologyConfig


@dataclass(frozen=True)
class Pass2Config:
    enabled: bool
    max_iterations: int
    llm_budget_max: int
    overview_sheet_max_chars: int
    llm_model: str
    llm_max_tokens: int
    llm_temperature: float

    @classmethod
    def from_stage_config(cls, cfg: XlOntologyConfig) -> "Pass2Config":
        return cls(
            enabled=cfg.pass2_enabled,
            max_iterations=cfg.pass2_max_iterations,
            llm_budget_max=cfg.pass2_llm_budget_max,
            overview_sheet_max_chars=cfg.pass2_overview_sheet_max_chars,
            llm_model=cfg.llm_model,
            llm_max_tokens=cfg.llm_max_tokens,
            llm_temperature=cfg.llm_temperature,
        )


__all__ = ["Pass2Config"]
