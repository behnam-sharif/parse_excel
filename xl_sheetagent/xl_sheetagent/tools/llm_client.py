"""
llm_client.py — Thin OpenAI wrapper for xl_sheetagent LLM calls.

Same operational pattern as xl_init/sheet_triage.py:
  - openai Python client, chat completions
  - OPENAI_API_KEY from environment via core.env_bootstrap
  - Model name + limits from XlSheetAgentConfig
  - temperature=0 for deterministic results

All LLM text must go through payload_builder first.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, Optional

_log = logging.getLogger("xl_sheetagent.llm_client")

# V2: Shared OpenAI client singleton (thread-safe)
_shared_client = None
_client_lock = threading.Lock()


def get_shared_client(cfg):
    """Return a shared OpenAI client instance. Thread-safe."""
    global _shared_client
    if _shared_client is not None:
        return _shared_client
    with _client_lock:
        if _shared_client is not None:
            return _shared_client
        from core.env_bootstrap import load_project_dotenv
        load_project_dotenv()
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError(
                "OPENAI_API_KEY is not set. Set the env var or use "
                "skip_llm_enrichment: true in config/stages.yaml."
            )
        from openai import OpenAI
        _shared_client = OpenAI(api_key=api_key)
        return _shared_client


def is_llm_available(cfg) -> bool:
    """Check whether LLM calls can be made (skip flag + API key)."""
    if cfg.skip_llm_enrichment:
        return False
    return bool(os.getenv("OPENAI_API_KEY"))


def call_llm(
    system_prompt: str,
    user_prompt: str,
    cfg,
    *,
    recorder=None,
    table_id: str = "",
) -> str:
    """Call OpenAI chat completions and return the assistant message text.

    Returns empty string when skip_llm_enrichment is True or on failure.

    Parameters
    ----------
    recorder : ToolCallRecorder, optional
        V1D recorder. When present, captures prompt/response/usage/latency.
    table_id : str, optional
        Table identifier for recorder context.
    """
    if cfg.skip_llm_enrichment:
        return ""

    from core.env_bootstrap import load_project_dotenv
    load_project_dotenv()

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError(
            "OPENAI_API_KEY is not set. Set the env var or use "
            "skip_llm_enrichment: true in config/stages.yaml."
        )

    try:
        from openai import OpenAI

        client = OpenAI(api_key=api_key)
        t0 = time.time()
        response = client.chat.completions.create(
            model=cfg.llm_model,
            max_tokens=cfg.llm_max_tokens,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0,
        )
        latency_ms = (time.time() - t0) * 1000.0
        text = response.choices[0].message.content
        text = text if text is not None else ""

        if recorder is not None:
            tokens_in = getattr(response.usage, "prompt_tokens", 0) if response.usage else 0
            tokens_out = getattr(response.usage, "completion_tokens", 0) if response.usage else 0
            recorder.record(
                table_id=table_id,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_text=text,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                latency_ms=latency_ms,
                model=cfg.llm_model,
            )

        return text
    except Exception as exc:
        _log.warning("LLM call failed: %s", exc)
        return ""


def call_llm_json(
    system_prompt: str,
    user_prompt: str,
    cfg,
    *,
    recorder=None,
    table_id: str = "",
) -> Dict[str, Any]:
    """Call LLM and parse the response as JSON.

    Returns empty dict on skip, failure, or parse error.
    """
    raw = call_llm(
        system_prompt, user_prompt, cfg,
        recorder=recorder, table_id=table_id,
    )
    if not raw:
        return {}

    # Strip markdown code fences if present
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        # Remove first and last lines (fences)
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)

    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError) as exc:
        _log.warning("LLM JSON parse failed: %s — raw: %.200s", exc, raw)
        return {}
