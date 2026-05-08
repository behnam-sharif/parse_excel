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

import inspect
import json
import typing
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


def _extract_json_object(s: str) -> str:
    """Extract the first balanced ``{...}`` block from ``s``.

    LLMs frequently emit prose then JSON despite "JSON ONLY" instructions
    (e.g. ``"Hmm, I need to ... {\\"thought\\": ..., \\"action\\": ...}"``).
    Returns the substring from the first ``{`` to its matching ``}``,
    skipping over braces inside string literals.  Returns the input
    unchanged if no balanced block is found.
    """
    if not s:
        return s
    # Fast path: already starts with `{`.
    if s.lstrip().startswith("{"):
        return s
    start = s.find("{")
    if start < 0:
        return s
    depth = 0
    in_str = False
    escape = False
    for i in range(start, len(s)):
        ch = s[i]
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return s[start:i + 1]
    # Unbalanced — give the original; the JSON parser will raise a
    # clearer error than silently returning a truncated slice.
    return s


def parse_react_step(text: str):  # type: ignore[no-untyped-def]
    """Return ``ReactActionStep`` | ``ReactStopStep`` | ``ReactParseError``.

    Tolerant: accepts the two canonical shapes, plus ``{"action": {...}}``
    without a ``thought`` (treated as ``thought=""``), plus prose-then-JSON
    responses where the LLM disregards the "JSON ONLY" instruction.  Any
    other shape becomes a ``ReactParseError`` so the loop can re-prompt.
    """
    raw = text or ""
    cleaned = _strip_code_fence(raw)
    cleaned = _extract_json_object(cleaned)
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

    Tool-author convention: if a tool's result is a dict that carries a
    server-generated ``summary_for_llm`` field (a paragraph the tool's
    author wrote specifically for the LLM to quote / reason over), use
    that as the observation directly — the agent gets a structured
    one-line debrief instead of a generic ``dict keys=[...]`` preview.
    Falls back to the keys-preview for dicts that don't carry the
    convention.  Errors / lists / scalars retain their original shape.
    """
    if result is None:
        return f"[{tool}] returned None"
    try:
        if isinstance(result, dict) and isinstance(
            result.get("summary_for_llm"), str
        ) and result["summary_for_llm"].strip():
            text = f"[{tool}] {result['summary_for_llm'].strip()}"
        elif isinstance(result, list):
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
# Tool documentation for the prompt
# ---------------------------------------------------------------------------

def _literal_allowed_values(annotation: Any) -> Optional[List[Any]]:
    """If ``annotation`` is a ``typing.Literal[...]`` (or a type alias
    that resolves to one), return the list of allowed values.

    Handles both direct ``Literal["a", "b"]`` and aliased types like
    ``SearchScope = Literal["all", "sheet"]`` as long as the alias is
    fully resolvable via ``typing.get_origin`` / ``get_args``.
    """
    try:
        origin = typing.get_origin(annotation)
    except Exception:
        return None
    if origin is typing.Literal:  # type: ignore[comparison-overlap]
        try:
            return list(typing.get_args(annotation))
        except Exception:
            return None
    # Older typing fallback — get_origin returns the Literal special form.
    if str(origin) == "typing.Literal":
        try:
            return list(typing.get_args(annotation))
        except Exception:
            return None
    return None


def _tool_arg_spec(fn: Any) -> List[Dict[str, Any]]:
    """Return one dict per parameter (skipping the leading ``ctx``)
    listing name, required-vs-optional, default, annotation string,
    and (when applicable) ``allowed_values`` for ``Literal[...]`` types.

    Surfaces enough info that the LLM knows which args it MUST pass
    AND which fixed enum values it must choose from.  Fixes the
    ``scope="Workbook"`` failure mode where the LLM guessed at allowed
    enum members.
    """
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return []
    # Resolve type-alias annotations (``SearchScope = Literal["all", ...]``)
    # to their underlying types via ``get_type_hints``.  Falls back to
    # signature.parameters annotations on failure.
    try:
        resolved_hints = typing.get_type_hints(fn)
    except Exception:
        resolved_hints = {}
    args: List[Dict[str, Any]] = []
    skip_first = True
    for name, param in sig.parameters.items():
        if skip_first:
            # The leading ``ctx: ToolContext`` is always supplied by the
            # dispatcher — never asked of the LLM.
            skip_first = False
            continue
        if param.kind in (
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        annotation_raw = resolved_hints.get(name)
        if annotation_raw is None and param.annotation is not inspect.Parameter.empty:
            annotation_raw = param.annotation
        annotation_str = (
            "" if annotation_raw is None
            else str(annotation_raw).replace("typing.", "")
        )
        required = param.default is inspect.Parameter.empty
        spec: Dict[str, Any] = {
            "name": name,
            "required": required,
            "default": (
                None if param.default is inspect.Parameter.empty
                else _safe_repr(param.default)
            ),
            "annotation": annotation_str,
        }
        # Literal[...] enum extraction — surface allowed_values so the
        # LLM doesn't have to guess.
        if annotation_raw is not None:
            allowed = _literal_allowed_values(annotation_raw)
            if allowed is not None:
                spec["allowed_values"] = [_safe_repr(v) for v in allowed]
        args.append(spec)
    return args


def _safe_repr(v: Any) -> str:
    try:
        return repr(v)
    except Exception:
        return type(v).__name__


def _tool_docstring(fn: Any, *, max_chars: int = 400) -> str:
    """First paragraph of the tool's docstring, trimmed."""
    doc = (fn.__doc__ or "").strip()
    if not doc:
        return ""
    # First paragraph = up to first blank line.
    para = doc.split("\n\n", 1)[0].strip()
    para = " ".join(para.split())  # collapse whitespace
    if len(para) > max_chars:
        para = para[:max_chars] + "..."
    return para


def build_tool_specs() -> List[Dict[str, Any]]:
    """Per-tool dict with name, doc paragraph, and full arg spec.

    Used to populate the ``AVAILABLE TOOLS`` block of the ReAct prompt
    so the LLM doesn't guess argument names — the cause of every
    ``TypeError: missing required argument`` we saw in v0 traces.
    """
    out: List[Dict[str, Any]] = []
    for name, fn in PASS2_TOOL_REGISTRY.items():
        out.append({
            "tool": name,
            "doc": _tool_docstring(fn),
            "args": _tool_arg_spec(fn),
        })
    return out


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

UPSTREAM CONTEXT:
{upstream_context_json}

COMPLETION CRITERIA (concept → required items):
{criteria_json}

OBSERVATIONS SO FAR (oldest → newest):
{observations_json}

AVAILABLE TOOLS — each entry shows the tool's name, a short doc, and
its argument spec.  Every ``required: true`` arg MUST be present in
your ``action.args`` dict.  Use the ``annotation`` field to pick valid
shapes (e.g. ``List[str]`` means a list of strings).  Tools that take
``sheet_name`` / ``sheets`` need real sheet names from the workbook —
look at upstream context or prior observations to find them.
{tools_json}

TABLE-ID RULES (read carefully — wrong ids waste iterations):
- Always copy ``table_id`` values VERBATIM from one of: the
  ``UPSTREAM CONTEXT`` section above, a prior ``observation`` in this
  loop, or a ``list_tables_on_sheet`` result.
- Child slots inside a parent table are SPARSE.  V6 finalize numbers
  surviving children with arbitrary suffixes — e.g. a parent's children
  may be ``..._child2``, ``..._child4``, ``..._child5``, ``..._child7``,
  ``..._child8`` with NO ``_child1``, ``_child3``, or ``_child6``.
  NEVER guess ``_childN`` from a sequence — only use ids you have
  literally seen in the upstream context, an observation, or a tool
  result.  An invented id will return ``error: unknown_table_id`` and
  burn an iteration.
- A ``_t...`` / ``_b...`` segment encodes the V3a hierarchy path; never
  edit those segments either.

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
  "action": {{"tool": "<name>", "args": {{...required + optional args...}}, "rationale": "<concept>"}}
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
    upstream_reports: Optional[Dict[str, Any]] = None,
) -> str:
    """Assemble the per-iteration ReAct prompt.

    ``observations`` is appended-to in order; we keep the last N so the
    prompt size stays bounded.  ``extra_context`` is a per-stage hook
    (e.g. for calculations: the terminal-result table_ids).
    ``upstream_reports`` is the structured summary handed forward by
    earlier Pass-2 stages — used by the overall agent to consume the
    inputs / calc / results agent reports.
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

    # Rich tool specs — name, doc, full arg signature.  Replaces the
    # previous one-liner that left the LLM guessing at arg names.
    tools_payload = build_tool_specs()

    # The "upstream context" block carries:
    #   - overall_context (Pass-2 Stage 2.1 output, when this stage is
    #     not the overall agent itself)
    #   - upstream_reports (this stage's view of every prior stage's
    #     StageFindingsReport — populated for the overall agent in the
    #     V1.J flipped order, empty for the first agent in the sequence)
    upstream_context = {
        "overall_concepts": overall_context or {},
        "upstream_reports": upstream_reports or {},
    }

    base = _REACT_PROMPT_TMPL.format(
        stage=stage,
        iteration=iteration,
        max_iterations=max_iterations,
        upstream_context_json=json.dumps(upstream_context, indent=2, default=str),
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
    "build_tool_specs",
    "format_error_observation",
    "format_observation",
    "parse_react_step",
]
