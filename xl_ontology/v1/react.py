"""ReAct loop primitives — single-step LLM contract for Pass-2 agents.

Each LLM turn returns ONE of two JSON shapes:

  Action turn:
    {
      "thought": "...free-form reasoning...",
      "action": {"tool": "<name>", "args": {...}, "rationale": "..."}
    }

  Stop turn:
    {
      "thought": "I have enough — drug X across UK, 5y horizon, ...",
      "stop": true,
      "summary": "..."
    }

The agent loop:
  1. Build prompt with criteria + observations_so_far + iteration count
  2. Ask LLM for one step
  3. If stop: emit Pass2ReactStop trace event, terminate
  4. Else:
       - emit Pass2ReactThought + Pass2ReactAction
       - run the tool deterministically
       - emit Pass2ReactObservation with one-line tool-result summary
       - append (tool, args, observation) to observations_so_far
       - re-loop
  5. After ``max_iterations`` without stop → completion_status = "iteration_cap_reached"
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from xl_ontology.v1.agent_base import ToolStep
from xl_ontology.v1.planner import PASS2_TOOL_REGISTRY


# ---------------------------------------------------------------------------
# Step types
# ---------------------------------------------------------------------------

@dataclass
class ReactObservation:
    """One observation in the running log fed to the next iteration."""

    iteration: int
    tool: str
    args_summary: Dict[str, Any] = field(default_factory=dict)
    observation_text: str = ""
    error: bool = False


@dataclass
class ReactActionStep:
    """LLM emitted a tool call (not a stop)."""

    thought: str
    tool_step: ToolStep


@dataclass
class ReactStopStep:
    """LLM emitted ``stop: true``."""

    thought: str
    summary: str


@dataclass
class ReactParseError:
    """LLM response couldn't be parsed.  The agent treats this as a
    no-op iteration with an error observation, then re-prompts."""

    raw_text: str
    error: str


ReactStep = "ReactActionStep | ReactStopStep | ReactParseError"


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

def _strip_code_fence(s: str) -> str:
    s = (s or "").strip()
    if s.startswith("```"):
        lines = s.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        s = "\n".join(lines)
    return s


def parse_react_step(text: str):  # type: ignore[no-untyped-def]
    """Return ``ReactActionStep`` | ``ReactStopStep`` | ``ReactParseError``.

    Tolerant: accepts the two canonical shapes, plus ``{"action": {...}}``
    without a ``thought`` (treated as ``thought=""``).  Any other shape
    becomes a ``ReactParseError`` so the loop can re-prompt.
    """
    raw = text or ""
    cleaned = _strip_code_fence(raw)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError as exc:
        return ReactParseError(raw_text=raw, error=f"json_decode: {exc}")
    if not isinstance(data, dict):
        return ReactParseError(raw_text=raw, error="top_level_not_object")

    thought = str(data.get("thought") or "").strip()

    # Stop turn?
    if bool(data.get("stop")):
        return ReactStopStep(
            thought=thought,
            summary=str(data.get("summary") or "").strip(),
        )

    # Action turn.
    action = data.get("action")
    if not isinstance(action, dict):
        return ReactParseError(
            raw_text=raw, error="missing_or_malformed_action",
        )
    tool = action.get("tool")
    if not isinstance(tool, str) or not tool:
        return ReactParseError(raw_text=raw, error="action_missing_tool")
    args = action.get("args") or {}
    if not isinstance(args, dict):
        args = {}
    rationale = str(action.get("rationale") or "").strip()
    return ReactActionStep(
        thought=thought,
        tool_step=ToolStep(tool=tool, args=args, rationale=rationale),
    )


# ---------------------------------------------------------------------------
# Observation formatting
# ---------------------------------------------------------------------------

def format_observation(tool: str, result: Any, *, max_chars: int = 600) -> str:
    """One-line summary of a tool result, fed back into the next prompt.

    Compact enough to keep the prompt bounded; informative enough that the
    agent can decide its next move.
    """
    if result is None:
        return f"[{tool}] returned None"
    try:
        if isinstance(result, list):
            n = len(result)
            preview = json.dumps(result[:3], default=str, ensure_ascii=False)
            text = f"[{tool}] list[{n}] preview={preview}"
        elif isinstance(result, dict):
            keys = sorted(list(result.keys()))[:8]
            text = f"[{tool}] dict keys={keys}"
        else:
            text = f"[{tool}] {result!s}"
    except Exception as exc:
        text = f"[{tool}] <unprintable: {exc}>"
    if len(text) > max_chars:
        text = text[:max_chars] + "..."
    return text


def format_error_observation(tool: str, error_type: str, message: str) -> str:
    """Observation for a failed tool call — kept short, agent can pivot."""
    snippet = (message or "")[:200]
    return f"[{tool}] FAILED ({error_type}): {snippet}"


# ---------------------------------------------------------------------------
# Prompt building
# ---------------------------------------------------------------------------

_REACT_PROMPT_TMPL = """You are the {stage} ReAct agent in a budget-impact-model
(BIM) comprehension run.  At each turn you may call ONE tool, OR stop and
write a summary.  You have a deterministic tool registry — pick the most
useful tool given the observations so far.

Style: write your thought in first person, freely — "Hmm, that wasn't
what I expected; let me try X instead" is fine.  The thought is logged
to the trace and is your reasoning.  Keep it concise (≤300 chars).

STAGE: {stage}
ITERATION: {iteration} / {max_iterations}

OVERALL CONTEXT (Stage 2.1):
{overall_context_json}

COMPLETION CRITERIA (concept → required items):
{criteria_json}

OBSERVATIONS SO FAR (oldest → newest):
{observations_json}

AVAILABLE TOOLS:
{tools_json}

WHEN TO STOP:
- You have enough information to write a 2-3 paragraph narrative
  covering the criteria.  Stopping at iteration 1 is forbidden — you
  must take at least one productive action first.
- Continuing past iteration 8 without finding new evidence is wasteful;
  prefer stopping with what you have.
- If a tool is failing repeatedly, switch tools or stop.

OUTPUT — return JSON ONLY, exactly one of these two shapes:

Action turn:
{{
  "thought": "...your reasoning...",
  "action": {{"tool": "<name>", "args": {{...}}, "rationale": "<concept>"}}
}}

Stop turn:
{{
  "thought": "I have enough to narrate.",
  "stop": true,
  "summary": "1-sentence wrap-up of what you concluded"
}}
"""


def build_react_prompt(
    *,
    stage: str,
    iteration: int,
    max_iterations: int,
    overall_context: Dict[str, Any],
    criteria: Dict[str, Any],
    observations: List[ReactObservation],
    extra_context: Optional[Dict[str, Any]] = None,
    max_observations_in_prompt: int = 12,
) -> str:
    """Assemble the per-iteration ReAct prompt.

    ``observations`` is appended-to in order; we keep the last N so the
    prompt size stays bounded.  ``extra_context`` is a per-stage hook
    (e.g. for calculations: the terminal-result table_ids; for inputs:
    the Pass-1 anchor distribution) — the stage decides what to inject.
    """
    obs_payload = []
    tail = observations[-max_observations_in_prompt:]
    for obs in tail:
        obs_payload.append({
            "iteration": obs.iteration,
            "tool": obs.tool,
            "args": obs.args_summary,
            "observation": obs.observation_text,
            "error": bool(obs.error),
        })
    crit_payload = {}
    for concept, body in (criteria or {}).items():
        if hasattr(body, "model_dump"):
            crit_payload[concept] = body.model_dump(mode="json")  # type: ignore[attr-defined]
        elif isinstance(body, dict):
            crit_payload[concept] = body
        else:
            crit_payload[concept] = {"required_items": []}
    tools_payload = [
        {
            "tool": name,
            "description": (fn.__doc__ or "").strip().splitlines()[0][:120]
            if (fn.__doc__ or "").strip()
            else "",
        }
        for name, fn in PASS2_TOOL_REGISTRY.items()
    ]
    base = _REACT_PROMPT_TMPL.format(
        stage=stage,
        iteration=iteration,
        max_iterations=max_iterations,
        overall_context_json=json.dumps(overall_context, indent=2, default=str),
        criteria_json=json.dumps(crit_payload, indent=2, default=str),
        observations_json=json.dumps(obs_payload, indent=2, default=str),
        tools_json=json.dumps(tools_payload, indent=2, default=str),
    )
    if extra_context:
        base += (
            "\nADDITIONAL CONTEXT FROM PRIOR PASS-2 STAGES:\n"
            + json.dumps(extra_context, indent=2, default=str)
            + "\n"
        )
    return base


__all__ = [
    "ReactActionStep",
    "ReactObservation",
    "ReactParseError",
    "ReactStep",
    "ReactStopStep",
    "build_react_prompt",
    "format_error_observation",
    "format_observation",
    "parse_react_step",
]
