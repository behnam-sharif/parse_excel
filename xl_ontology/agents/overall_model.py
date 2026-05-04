"""Overall-model ontology agent.

Walks overview sheets, dropdowns, and currency inferences to populate
the eight expected concepts (intervention, comparator, country, etc.).
Records concepts (not classifications) and produces the LLM-or-
template summary.
"""

from __future__ import annotations

from typing import ClassVar, Dict, List, Optional

from xl_ontology.agents.planner_execute import (
    DEFAULT_OVERVIEW_NAME_PATTERNS,
    resolve_overall_model_plan_step,
)
from xl_ontology.agents.prompts import (
    OVERALL_MODEL_LEXICON,
    template_human_readable_overall_model,
    template_overall_model_summary_no_llm,
)
from xl_ontology.agents.runner import OntologyAgent
from xl_ontology.budget import BudgetExhausted
from xl_ontology.schemas import (
    AgentOutputRecord,
    Evidence,
    NodeName,
    SearchLogEntry,
    _TraceBase,
)
from xl_ontology.tools.composite import (
    extract_currency_symbols,
    find_overview_sheets,
    list_data_validation_dropdowns,
)
from xl_ontology.tools.lexicon import search_workbook_lexicon
from xl_ontology.tools.llm import ask_llm
from xl_ontology.tools.writers import record_concept, record_search_log

_CURRENCY_TO_COUNTRY = {
    "£": "UK",
    "C$": "Canada",
    "A$": "Australia",
    "₹": "India",
    "R$": "Brazil",
}


class OverallModelAgent(OntologyAgent):
    node_name: ClassVar[NodeName] = "overall_model"

    def __init__(self, ctx, pack_node) -> None:
        super().__init__(ctx, pack_node)
        self._overview_sheets: List[str] = []
        self._summary_text: Optional[str] = None

    def _execute_planner_tools_phase(self) -> None:
        if not self.state.planner_plan:
            return
        for step in self.state.planner_plan:
            resolved = resolve_overall_model_plan_step(step)
            if resolved is None:
                continue
            tool, args = resolved
            if tool == "find_overview_sheets":
                patterns = args.get("name_patterns") or DEFAULT_OVERVIEW_NAME_PATTERNS
                if not isinstance(patterns, list):
                    patterns = DEFAULT_OVERVIEW_NAME_PATTERNS
                self._discover_overview_sheets(patterns)
            elif tool == "list_data_validation_dropdowns":
                sheets = args.get("sheets") or self._overview_sheets
                if not sheets:
                    self._discover_overview_sheets(DEFAULT_OVERVIEW_NAME_PATTERNS)
                    sheets = self._overview_sheets
                self._track_tool("list_data_validation_dropdowns")
                list_data_validation_dropdowns(self.ctx, sheets)
            elif tool == "extract_currency_symbols":
                self._currency_country_fallback()
            elif tool == "search_workbook_lexicon":
                raw = args.get("concepts")
                concepts: Optional[List[str]]
                if raw is None:
                    concepts = None
                elif isinstance(raw, str):
                    concepts = [raw]
                elif isinstance(raw, list):
                    concepts = [str(c) for c in raw]
                else:
                    concepts = None
                self._lexicon_pass_for_concepts(concepts)

    def _discover_overview_sheets(self, name_patterns: List[str]) -> List[str]:
        self._track_tool("find_overview_sheets")
        self._overview_sheets = find_overview_sheets(self.ctx, name_patterns)
        return self._overview_sheets

    def _lexicon_pass_for_concepts(
        self, concepts: Optional[List[str]] = None
    ) -> None:
        todo = concepts if concepts is not None else list(self.state.expected_concepts)
        for concept in todo:
            if concept not in self.state.expected_concepts:
                continue
            terms = OVERALL_MODEL_LEXICON.get(concept, [concept])
            self._track_tool("search_workbook_lexicon")
            hits = search_workbook_lexicon(self.ctx, terms, "whole_table")
            if hits:
                preferred = next(
                    (
                        h
                        for h in hits
                        if h.match_location in ("row_label", "body_cell_value")
                    ),
                    hits[0],
                )
                value = self._read_neighbour_value(
                    preferred.sheet, preferred.cell
                )
                value_str = str(value) if value is not None else preferred.snippet
                evidence = [
                    Evidence(
                        sheet=preferred.sheet,
                        cell=preferred.cell,
                        table_id=preferred.table_id,
                        snippet=preferred.snippet,
                        match_type="lexicon_match",
                        matched_term=preferred.matched_term,
                    )
                ]
                record_concept(
                    self.ctx,
                    concept_name=concept,
                    value=value_str,
                    evidence=evidence,
                    confidence=0.7,
                    evidence_quality="partial",
                )
            else:
                record_search_log(
                    self.ctx,
                    concept_name=concept,
                    entries=[
                        SearchLogEntry(
                            tool="search_workbook_lexicon",
                            args_summary={"terms": terms[:3], "scope": "whole_table"},
                            result_summary="0 hits",
                            hits=0,
                        )
                    ],
                )

    def _currency_country_fallback(self) -> None:
        country_rec = self.ctx.builder.summary.overall_model.concepts.get(
            "country"
        )
        if country_rec is not None and country_rec.value is not None:
            return
        self._track_tool("extract_currency_symbols")
        sym_map = extract_currency_symbols(
            self.ctx, self.ctx.workbook.sheet_names()
        )
        for sym, country in _CURRENCY_TO_COUNTRY.items():
            if sym in sym_map and sym_map[sym]:
                cell = sym_map[sym][0]
                record_concept(
                    self.ctx,
                    concept_name="country",
                    value=country,
                    evidence=[
                        Evidence(
                            sheet=cell.sheet,
                            cell=cell.cell,
                            table_id=None,
                            snippet=str(cell.value or sym),
                            match_type="format_match",
                            matched_term=sym,
                        )
                    ],
                    confidence=0.9
                    if sym in ("£", "C$", "A$", "₹", "R$")
                    else 0.5,
                    evidence_quality="partial",
                )
                self._key_decisions.append(
                    f"inferred country={country} from currency symbol {sym}"
                )
                break

    def _cross_check_phase(self) -> None:
        self._discover_overview_sheets(DEFAULT_OVERVIEW_NAME_PATTERNS)
        self._lexicon_pass_for_concepts(None)
        self._currency_country_fallback()

    def _read_neighbour_value(self, sheet: str, cell_addr: str):
        # Very lightweight: parse "B7" → (7, 2), read column 2 + 1 = 3.
        import re
        m = re.match(r"^([A-Z]+)([0-9]+)$", cell_addr)
        if not m:
            return None
        letters, row_str = m.groups()
        col = 0
        for ch in letters.upper():
            col = col * 26 + (ord(ch) - ord("A") + 1)
        row = int(row_str)
        snap = self.ctx.workbook.get_cell(sheet, row, col + 1)
        return snap.value

    def _validator_phase(self, elapsed: float) -> AgentOutputRecord:
        node = self.ctx.builder.summary.overall_model
        resolved = sum(
            1 for r in node.concepts.values() if r.value is not None
        )
        absent = sum(
            1 for r in node.concepts.values() if r.value is None
        )
        node.present = resolved > 0
        if resolved == 0:
            node.evidence_quality = "absent"
        elif absent == 0:
            node.evidence_quality = "high"
        else:
            node.evidence_quality = "partial"

        # Summary: LLM if available, else template.
        intervention = (node.concepts.get("intervention") or _empty()).value
        indication = (node.concepts.get("disease") or _empty()).value
        country = (node.concepts.get("country") or _empty()).value
        horizon = (node.concepts.get("time_horizon") or _empty()).value
        llm_calls = 0
        summary_source = "template"
        summary_text: Optional[str] = None
        if self.ctx.llm_complete is not None and resolved > 0:
            try:
                resp = ask_llm(
                    self.ctx,
                    agent=self.node_name,
                    phase="summary_synthesis",
                    prompt_payload=(
                        "Produce a 3-sentence factual summary of the BIM "
                        "based on these concepts:\n"
                        f"intervention={intervention}\n"
                        f"indication={indication}\n"
                        f"country={country}\n"
                        f"time_horizon={horizon}"
                    ),
                    ontology_context=[self.pack_node.txt_text],
                    max_tokens=300,
                    temperature=0.0,
                )
                summary_text = resp.response.strip() or None
                summary_source = "llm_synthesised"
                llm_calls = 1
            except BudgetExhausted:
                summary_text = template_overall_model_summary_no_llm(
                    intervention, indication, country, horizon
                )
        else:
            summary_text = template_overall_model_summary_no_llm(
                intervention, indication, country, horizon
            )
        node.summary = summary_text
        node.summary_source = summary_source  # type: ignore[assignment]

        return AgentOutputRecord(
            status="complete" if resolved > 0 else "partial",
            evidence_quality=node.evidence_quality,
            summary=summary_text,
            summary_source=summary_source,  # type: ignore[arg-type]
            concepts_resolved=resolved,
            concepts_absent=absent,
            applied_heuristics=list(self.state.applied_heuristics),
            tools_called=list(self._tools_called),
            llm_calls_used=llm_calls,
            elapsed_seconds=elapsed,
            key_decisions=list(self._key_decisions),
            human_readable_output=template_human_readable_overall_model(
                summary=summary_text,
                concepts_resolved=resolved,
                concepts_absent=absent,
                key_decisions=list(self._key_decisions),
            ),
        )


class _Empty:
    value = None


_EMPTY = _Empty()


def _empty():
    return _EMPTY
