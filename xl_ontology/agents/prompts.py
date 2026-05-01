"""Prompt + summary templates per agent.

Templates land here so the agents themselves stay focused on
orchestration. v0 templates are template-only (no LLM substitution
in the fallback path); when ``ctx.llm_complete`` is set, the
overall_model agent uses the planner / summary prompts to invoke
the model.
"""

from __future__ import annotations

from typing import Iterable, List, Optional


# ---------------------------------------------------------------------------
# Per-agent expected-concept lexicons. v0 hardcodes a thin synonym set
# per concept; the pack's full §5 LEXICON is informative for LLM
# context but not parsed structurally in v0. (Open question for v0.1:
# parse §5 LEXICON automatically so packs can extend without code
# changes.)
# ---------------------------------------------------------------------------

OVERALL_MODEL_LEXICON = {
    "intervention": [
        "intervention", "new drug", "novel therapy", "study drug",
        "treatment", "therapy",
    ],
    "comparator": [
        "comparator", "standard of care", "current practice", "control",
        "baseline arm",
    ],
    "country": [
        "country", "region", "geography", "market", "setting",
        "jurisdiction",
    ],
    "population": [
        "population", "patient population", "eligible patients",
        "indication population", "cohort",
    ],
    "disease": [
        "disease", "indication", "condition", "diagnosis",
        "therapeutic area",
    ],
    "time_horizon": [
        "time horizon", "model horizon", "years modelled",
        "projection period", "analysis period",
    ],
    "perspective": [
        "perspective", "viewpoint", "payer perspective",
        "societal perspective",
    ],
    "objective": [
        "objective", "purpose", "aim", "goal", "scope",
    ],
}

INPUTS_LEXICON = {
    "population": [
        "population", "patient pop", "eligible pop", "target pop",
        "cohort", "covered lives", "treated pop",
    ],
    "epidemiology": [
        "incidence", "prevalence", "mortality", "progression rate",
    ],
    "market_share": [
        "market share", "uptake", "penetration", "treatment mix",
    ],
    "drug_cost": [
        "drug cost", "unit cost", "list price", "WAC", "net price",
        "cost per dose", "cost per pack",
    ],
    "administration_cost": [
        "admin cost", "administration", "infusion fee", "injection fee",
    ],
    "monitoring_cost": [
        "monitoring", "labs", "imaging", "follow-up",
    ],
    "adverse_event_cost": [
        "AE cost", "adverse event", "toxicity cost",
    ],
    "hospitalisation_cost": [
        "hospitalisation", "inpatient", "admission", "bed day",
    ],
}

CALCULATIONS_LEXICON = {
    "total_cost_with_intervention": [
        "total cost", "total spend", "with intervention", "scenario A",
        "intervention scenario",
    ],
    "total_cost_without_intervention": [
        "without intervention", "scenario B", "comparator scenario",
        "current practice cost",
    ],
    "patient_flow": [
        "treated patients", "patients on treatment", "patient numbers",
    ],
    "aggregator": [
        "subtotal", "grand total", "summary", "all-cost",
    ],
}

RESULTS_LEXICON = {
    "incremental_budget_impact": [
        "incremental", "difference", "net impact", "delta", "change",
        "budget impact", "vs comparator",
    ],
    "total_cost_summary": [
        "total cost", "grand total", "overall cost",
    ],
    "per_patient_impact": [
        "per patient", "per person", "per treated", "per eligible",
    ],
    "scenario_results": [
        "scenario", "sensitivity", "best case", "worst case",
    ],
}


# ---------------------------------------------------------------------------
# Templates for inputs / calculations / results summaries (no LLM call)
# ---------------------------------------------------------------------------

def template_inputs_summary(
    n_classified: int,
    found: Iterable[str],
    absent: Iterable[str],
) -> str:
    found_list = list(found)
    absent_list = list(absent)
    if n_classified == 0:
        return "No input tables identified."
    parts: List[str] = [f"{n_classified} input table{'s' if n_classified != 1 else ''} identified"]
    if found_list:
        parts.append("covering " + ", ".join(found_list))
    out = " ".join(parts) + "."
    if absent_list:
        out += " " + ", ".join(absent_list) + " not located."
    return out


def template_calculations_summary(
    n_main: int, main_subtypes: Iterable[str], n_aggregators: int
) -> str:
    sub = list(main_subtypes)
    if n_main == 0 and n_aggregators == 0:
        return (
            "No calculation tables identified. Model may be lookup-only or "
            "non-standard."
        )
    parts: List[str] = []
    parts.append(
        f"{n_main} main calculation table{'s' if n_main != 1 else ''} identified"
    )
    if sub:
        parts.append(f"({', '.join(sub)})")
    main_clause = " ".join(parts)
    agg_clause = (
        f"{n_aggregators} aggregator{'s' if n_aggregators != 1 else ''}."
    )
    return f"{main_clause}. {agg_clause}"


def template_results_summary(
    n: int, subtypes: Iterable[str], has_incremental: bool
) -> str:
    sub = list(subtypes)
    if n == 0:
        return "No result tables identified. Model may be incomplete."
    parts: List[str] = []
    parts.append(
        f"{n} result table{'s' if n != 1 else ''} identified"
    )
    if sub:
        parts.append(f"({', '.join(sub)})")
    out = " ".join(parts) + "."
    if has_incremental:
        out += " Incremental row present."
    else:
        out += " No incremental row located."
    return out


def template_overall_model_summary_no_llm(
    intervention: Optional[str],
    indication: Optional[str],
    country: Optional[str],
    horizon: Optional[str],
) -> Optional[str]:
    """Template fallback when no LLM is available. The spec calls for
    1 LLM call here; this template is the v0 graceful degradation when
    `ctx.llm_complete is None` (Phase D synthetic-fixture runs)."""
    bits: List[str] = []
    if intervention:
        bits.append(f"intervention: {intervention}")
    if indication:
        bits.append(f"indication: {indication}")
    if country:
        bits.append(f"country: {country}")
    if horizon:
        bits.append(f"time horizon: {horizon}")
    if not bits:
        return None
    return "Budget impact model with " + "; ".join(bits) + "."


# ---------------------------------------------------------------------------
# Human-readable agent output template (validator builds these)
# ---------------------------------------------------------------------------

def template_human_readable_overall_model(
    summary: Optional[str],
    concepts_resolved: int,
    concepts_absent: int,
    key_decisions: List[str],
) -> str:
    paragraphs: List[str] = []
    paragraphs.append(
        f"The overall_model agent resolved {concepts_resolved} concept(s) and "
        f"recorded {concepts_absent} as absent."
    )
    if summary:
        paragraphs.append(f"Summary: {summary}")
    if key_decisions:
        paragraphs.append("Key decisions:")
        for d in key_decisions:
            paragraphs.append(f"  - {d}")
    return "\n\n".join(paragraphs)


def template_human_readable_classification(
    agent_label: str,
    n_classified: int,
    expected_found: List[str],
    expected_absent: List[str],
    overrides: int,
) -> str:
    paragraphs: List[str] = []
    paragraphs.append(
        f"The {agent_label} agent classified {n_classified} table(s)."
    )
    if expected_found:
        paragraphs.append(
            "Expected concepts found: " + ", ".join(expected_found) + "."
        )
    if expected_absent:
        paragraphs.append(
            "Expected concepts absent: " + ", ".join(expected_absent) + "."
        )
    if overrides:
        paragraphs.append(
            f"Proposed {overrides} node_type override(s) (logged to "
            "node_type_override_log.jsonl)."
        )
    return "\n\n".join(paragraphs)
