"""
Dispatch completions for OpenAI- or Anthropic-style Python SDK clients.
"""

from __future__ import annotations

from typing import Any, Tuple


def complete_text(
    client: Any,
    model: str,
    prompt: str,
    max_tokens: int,
    temperature: float,
) -> Tuple[str, int]:
    """
    Single user-message completion. Returns (text, reported total token count).

    OpenAI: uses ``client.chat.completions`` and ``usage.total_tokens`` when present.
    Anthropic: uses ``client.messages`` and input_tokens + output_tokens when present.
    """
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
