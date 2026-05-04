"""Resolve ``ToolContext.llm_complete`` from stage config + environment."""

from __future__ import annotations

import logging
import os
from typing import Callable, Optional, Tuple

from xl_ontology.config import XlOntologyConfig

logger = logging.getLogger(__name__)

# Signature expected by ``xl_ontology.tools.llm.ask_llm``.
CompleteFn = Callable[[str, int, float], Tuple[str, int]]


def resolve_llm_complete(config: XlOntologyConfig) -> Optional[CompleteFn]:
    """Return a completion callable when ``use_llm`` is true and a client can
    be constructed; otherwise ``None`` (deterministic run)."""
    if not config.use_llm:
        return None

    from core.env_bootstrap import load_project_dotenv

    load_project_dotenv()

    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        logger.warning(
            "xl_ontology: use_llm=true but OPENAI_API_KEY is unset — "
            "running without LLM (planner/summary calls skipped)."
        )
        return None

    from openai import OpenAI

    from xl_sheetagent.llm_dispatch import complete_text

    client = OpenAI(api_key=api_key)
    model = config.llm_model

    def _complete(prompt: str, max_tokens: int, temperature: float) -> Tuple[str, int]:
        return complete_text(client, model, prompt, max_tokens, temperature)

    return _complete


__all__ = ["CompleteFn", "resolve_llm_complete"]
