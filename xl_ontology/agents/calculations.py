"""Calculations ontology agent."""

from __future__ import annotations

from typing import ClassVar, Dict, List

from xl_ontology.agents.prompts import (
    CALCULATIONS_LEXICON,
    template_calculations_summary,
    template_human_readable_classification,
)
from xl_ontology.agents.runner import OntologyAgent, dispatch_defect_stub
from xl_ontology.schemas import (
    AgentOutputRecord,
    CalcPayload,
    CandidateTable,
    EvidenceSignal,
    NodeName,
    OverrideEvidence,
)
from xl_ontology.tools.lexicon import Hit, search_workbook_lexicon
from xl_ontology.tools.writers import (
    AlreadyClassified,
    record_classification,
)
from xl_ontology.upstream.final_map import FinalMapEntry


_LOCATION_WEIGHT = {
    "title": 1.0,
    "header": 0.8,
    "row_label": 0.7,
    "body_cell_value": 0.4,
    "formula_text": 0.2,
}


class CalculationsAgent(OntologyAgent):
    node_name: ClassVar[NodeName] = "calculations"

    def _cross_check_phase(self) -> None:
        # A. STARTING SET — node_type ∈ {calc, mixed}
        starting_ids = {
            e.table_id
            for e in self.ctx.final_map.all_entries()
            if e.node_type in ("calc", "mixed")
        }
        # D. CROSS-CHECK BY EXPECTED CONCEPT — lexicon (whole_table)
        for concept in self.state.expected_concepts:
            terms = CALCULATIONS_LEXICON.get(concept, [concept.replace("_", " ")])
            self._track_tool("search_workbook_lexicon")
            hits = search_workbook_lexicon(self.ctx, terms, "whole_table")
            if not hits:
                continue
            # Single best-weighted hit per concept (pre-v0.1 behavior).
            # Filter out already-classified tables so the parent concept
            # can fall back to its second-best hit (see inputs.py for
            # the rationale).
            unclaimed = [
                h for h in hits
                if h.table_id and not self.ctx.builder.is_classified(h.table_id)
            ]
            if not unclaimed:
                continue
            best = max(unclaimed, key=lambda h: _LOCATION_WEIGHT.get(h.match_location, 0.0))
            entry = self.ctx.final_map.get(best.table_id) if best.table_id else None
            if entry is None:
                continue
            in_starting = entry.table_id in starting_ids
            self._classify(entry, concept, best, in_starting=in_starting)
        # E. STRUCTURAL SANITY CHECK — formula_density > 0.50 AND fanin > 0
        # AND fanout > 0; override input/data_table.
        self._track_tool("structural_sanity")
        for entry in self.ctx.final_map.all_entries():
            if self.ctx.builder.is_classified(entry.table_id):
                continue
            density = self.ctx.sanity.compute_formula_density(entry.table_id)
            fanin = self.ctx.sanity.get_dag_fanin(entry.table_id)
            fanout = self.ctx.sanity.get_dag_fanout(entry.table_id)
            if (
                density > 0.50
                and fanin > 0
                and fanout > 0
                and entry.node_type in ("input", "data_table")
            ):
                self._classify_structural(entry, density, fanin, fanout)
        # H. DEFECT FLAGS — dispatch stubs
        for entry in self.ctx.final_map.all_entries():
            if self.ctx.builder.is_classified(entry.table_id) != self.node_name:
                continue
            dispatch_defect_stub(self.ctx, entry, self.node_name)

    def _classify(self, entry, concept, hit, in_starting: bool) -> None:
        # v1.A Pass-1 eligibility (DAG residence + node_type not metadata/data_table).
        from xl_ontology.upstream import is_pass1_eligible
        if not is_pass1_eligible(self.ctx.dag, entry.table_id, entry.node_type):
            return
        if self.ctx.builder.is_classified(entry.table_id):
            return
        # Calc tables are middle-of-DAG. Tables tagged 'output' upstream
        # are results, not calcs — defer to the results agent unless
        # they're not actually terminal (fanout > 0).
        if entry.node_type == "output" and self.ctx.sanity.get_dag_fanout(entry.table_id) == 0:
            return
        override = entry.node_type not in ("calc", "mixed")
        weight = _LOCATION_WEIGHT.get(hit.match_location, 0.4)
        confidence = 0.7 if in_starting else 0.55 * weight
        if concept == "total_cost_with_intervention":
            subtype = "total_cost_with"
        elif concept == "total_cost_without_intervention":
            subtype = "total_cost_without"
        elif concept == "patient_flow":
            subtype = "patient_flow"
        elif concept == "aggregator":
            subtype = "aggregator"
        else:
            subtype = "cost_component"
        cand = CandidateTable(
            table_id=entry.table_id,
            ontology_role="calculation",
            ontology_subtype=subtype,
            confidence=confidence,
            evidence_quality="high" if in_starting and hit.match_location in ("title", "header") else "partial",
            why_selected=(
                f"lexicon match in {hit.match_location} for concept {concept!r}"
            ),
            evidence_signals=[
                EvidenceSignal(
                    type="title_match" if hit.match_location == "title" else "format_match",
                    value=hit.snippet,
                    matched_term=hit.matched_term,
                )
            ],
            upstream_node_type=entry.node_type,  # type: ignore[arg-type]
            node_type_override_proposed=override,
            parent_table_id=entry.parent_table_id,
            parent_path=list(entry.parent_path),
            extracted_payload=CalcPayload(
                time_indexed=False,
                treatment_dimension="n/a",
            ),
        )
        try:
            record_classification(
                self.ctx,
                candidate=cand,
                agent=self.node_name,
                upstream_label=entry.node_type,
                sheet=entry.sheet_name,
                title=entry.title,
                override_evidence=[
                    OverrideEvidence(
                        signal="lexicon_match_whole_table",
                        value=hit.snippet,
                        matched_term=hit.matched_term,
                    )
                ] if override else None,
            )
            if override:
                self._key_decisions.append(
                    f"override {entry.table_id} ({entry.node_type} → calculation) "
                    f"via lexicon hit {hit.matched_term!r}"
                )
        except AlreadyClassified:
            pass

    def _classify_structural(
        self, entry: FinalMapEntry, density: float, fanin: int, fanout: int
    ) -> None:
        # v1.A Pass-1 eligibility — same filter as `_classify`.
        from xl_ontology.upstream import is_pass1_eligible
        if not is_pass1_eligible(self.ctx.dag, entry.table_id, entry.node_type):
            return
        cand = CandidateTable(
            table_id=entry.table_id,
            ontology_role="calculation",
            ontology_subtype="structural_calc",
            confidence=0.55,
            evidence_quality="partial",
            why_selected=(
                "structural sanity: high formula density + non-zero fan-in/out — "
                "calculation fingerprint despite upstream label"
            ),
            evidence_signals=[
                EvidenceSignal(type="formula_density", value=density),
                EvidenceSignal(type="dag_fanout", value=fanout),
            ],
            upstream_node_type=entry.node_type,  # type: ignore[arg-type]
            node_type_override_proposed=True,
            parent_table_id=entry.parent_table_id,
            parent_path=list(entry.parent_path),
            extracted_payload=CalcPayload(
                time_indexed=False, treatment_dimension="n/a"
            ),
        )
        try:
            record_classification(
                self.ctx,
                candidate=cand,
                agent=self.node_name,
                upstream_label=entry.node_type,
                sheet=entry.sheet_name,
                title=entry.title,
                override_evidence=[
                    OverrideEvidence(
                        signal="structural_sanity_high_formula_density",
                        value={
                            "formula_density": density,
                            "dag_fanin": fanin,
                            "dag_fanout": fanout,
                        },
                    )
                ],
            )
            self._key_decisions.append(
                f"structural override {entry.table_id} → calculation "
                f"(density={density:.2f})"
            )
        except AlreadyClassified:
            pass

    def _validator_phase(self, elapsed: float) -> AgentOutputRecord:
        node = self.ctx.builder.summary.calculations
        candidates = node.candidate_tables
        n_main = sum(
            1 for c in candidates if c.ontology_subtype != "aggregator"
        )
        n_aggregators = sum(
            1 for c in candidates if c.ontology_subtype == "aggregator"
        )
        subtypes = sorted({c.ontology_subtype for c in candidates if c.ontology_subtype})
        summary = template_calculations_summary(n_main, subtypes, n_aggregators)
        node.summary = summary
        node.summary_source = "template"
        node.evidence_quality = "high" if candidates else "absent"
        node.present = bool(candidates)
        for c in self.state.expected_concepts:
            node.expected_concepts_status[c] = (
                "found" if any(
                    (cand.ontology_subtype == c)
                    or (c.startswith("total_cost") and cand.ontology_subtype and cand.ontology_subtype.startswith("total_cost"))
                    for cand in candidates
                ) else "absent"
            )
        found = [c for c, st in node.expected_concepts_status.items() if st == "found"]
        absent = [c for c, st in node.expected_concepts_status.items() if st == "absent"]
        return AgentOutputRecord(
            status="complete" if candidates else "partial",
            evidence_quality=node.evidence_quality,
            summary=summary,
            summary_source="template",
            tables_classified=len(candidates),
            node_type_overrides_proposed=sum(
                1 for c in candidates if c.node_type_override_proposed
            ),
            expected_concepts_found=found,
            expected_concepts_absent=absent,
            applied_heuristics=list(self.state.applied_heuristics),
            tools_called=list(self._tools_called),
            llm_calls_used=0,
            elapsed_seconds=elapsed,
            key_decisions=list(self._key_decisions),
            human_readable_output=template_human_readable_classification(
                "calculations",
                n_classified=len(candidates),
                expected_found=found,
                expected_absent=absent,
                overrides=sum(1 for c in candidates if c.node_type_override_proposed),
            ),
        )
