"""
Dispatch completions for OpenAI- or Anthropic-style Python SDK clients.

Includes retry with exponential backoff for rate-limit (429) errors.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Tuple

logger = logging.getLogger(__name__)

_MAX_RETRIES = 5
_BASE_DELAY = 1.0  # seconds


def complete_text(
    client: Any,
    model: str,
    prompt: str,
    max_tokens: int,
    temperature: float,
) -> Tuple[str, int]:
    """
    Single user-message completion. Returns (text, reported total token count).

    Retries up to 5 times on rate-limit (429) errors with exponential backoff.

    OpenAI: uses ``client.chat.completions`` and ``usage.total_tokens`` when present.
    Anthropic: uses ``client.messages`` and input_tokens + output_tokens when present.
    """
    last_err: Exception | None = None

    for attempt in range(_MAX_RETRIES):
        try:
            return _complete_once(client, model, prompt, max_tokens, temperature)
        except Exception as exc:
            if _is_rate_limit(exc):
                delay = _BASE_DELAY * (2 ** attempt)
                logger.warning(
                    "Rate limit hit (attempt %d/%d), retrying in %.1fs: %s",
                    attempt + 1, _MAX_RETRIES, delay, exc,
                )
                time.sleep(delay)
                last_err = exc
            else:
                raise

    raise RuntimeError(
        f"Rate limit exceeded after {_MAX_RETRIES} retries"
    ) from last_err


def _is_rate_limit(exc: Exception) -> bool:
    """Check if exception is a rate-limit error (HTTP 429)."""
    cls_name = type(exc).__name__
    if "RateLimitError" in cls_name:
        return True
    status = getattr(exc, "status_code", None) or getattr(exc, "status", None)
    if status == 429:
        return True
    return False


def _complete_once(
    client: Any,
    model: str,
    prompt: str,
    max_tokens: int,
    temperature: float,
) -> Tuple[str, int]:
    """Execute a single completion call (no retry)."""
    if hasattr(client, "chat") and hasattr(client.chat, "completions"):
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        choice = response.choices[0]
        msg = choice.message
        raw = (msg.content or "").strip() if msg is not None else ""
        if not raw:
            raise RuntimeError("LLM returned empty content")
        usage = getattr(response, "usage", None)
        delta = 0
        if usage is not None:
            delta = int(getattr(usage, "total_tokens", None) or 0)
        return raw, delta

    if hasattr(client, "messages"):
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        block = response.content[0]
        raw = getattr(block, "text", None)
        if raw is None:
            raw = str(block)
        raw = (raw or "").strip()
        if not raw:
            raise RuntimeError("LLM returned empty content")
        usage = getattr(response, "usage", None)
        delta = 0
        if usage is not None:
            delta = int(getattr(usage, "input_tokens", None) or 0) + int(
                getattr(usage, "output_tokens", None) or 0
            )
        return raw, delta

    raise TypeError(
        "llm_client must be OpenAI (chat.completions) or Anthropic (messages); "
        f"got {type(client).__name__!r}"
    )
