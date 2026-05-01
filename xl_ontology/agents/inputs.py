"""Inputs ontology agent (per ontology_inputs.txt §6 / spec §5)."""

from __future__ import annotations

from typing import ClassVar, Dict, List, Optional, Set

from xl_ontology.agents.prompts import (
    INPUTS_LEXICON,
    template_human_readable_classification,
    template_inputs_summary,
)
from xl_ontology.agents.runner import OntologyAgent, dispatch_defect_stub
from xl_ontology.schemas import (
    AgentOutputRecord,
    CandidateTable,
    EvidenceSignal,
    InputPayload,
    NodeName,
    OverrideEvidence,
)
from xl_ontology.tools.lexicon import Hit, search_workbook_lexicon
from xl_ontology.tools.writers import (
    AlreadyClassified,
    record_classification,
    record_unclassified,
)
from xl_ontology.upstream.final_map import FinalMapEntry


_LOCATION_WEIGHT = {
    "title": 1.0,
    "header": 0.8,
    "row_label": 0.7,
    "body_cell_value": 0.4,
    "formula_text": 0.2,
}


class InputsAgent(OntologyAgent):
    node_name: ClassVar[NodeName] = "inputs"

    def _cross_check_phase(self) -> None:
        # A. STARTING SET — node_type=input
        starting_set: List[FinalMapEntry] = [
            e for e in self.ctx.final_map.all_entries() if e.node_type == "input"
        ]
        # B. CROSS-CHECK BY EXPECTED CONCEPT — lexicon (whole_table)
        for concept in self.state.expected_concepts:
            terms = INPUTS_LEXICON.get(concept, [concept.replace("_", " ")])
            self._track_tool("search_workbook_lexicon")
            hits = search_workbook_lexicon(self.ctx, terms, "whole_table")
            if not hits:
                continue
            best = max(hits, key=lambda h: _LOCATION_WEIGHT.get(h.match_location, 0.0))
            entry = self.ctx.final_map.get(best.table_id) if best.table_id else None
            if entry is None:
                continue
            self._classify(entry, concept, best, in_starting=entry in starting_set)
        # C. STRUCTURAL SANITY CHECK — formula_density < 0.20 AND fanout > 5
        self._track_tool("structural_sanity")
        for entry in self.ctx.final_map.all_entries():
            if self.ctx.builder.is_classified(entry.table_id):
                continue
            density = self.ctx.sanity.compute_formula_density(entry.table_id)
            fanout = self.ctx.sanity.get_dag_fanout(entry.table_id)
            if density < 0.20 and fanout > 5 and entry.node_type != "input":
                self._classify_structural(entry)
        # H. DEFECT FLAGS — dispatch stubs
        for entry in self.ctx.final_map.all_entries():
            if self.ctx.builder.is_classified(entry.table_id) != self.node_name:
                continue
            dispatch_defect_stub(self.ctx, entry, self.node_name)

    def _classify(
        self,
        entry: FinalMapEntry,
        concept: str,
        hit: Hit,
        in_starting: bool,
    ) -> None:
        if self.ctx.builder.is_classified(entry.table_id):
            return
        # Inputs can never be terminal output tables. Skip rather than
        # propose an output→input override — those are results.
        if entry.node_type == "output":
            return
        override = entry.node_type != "input"
        weight = _LOCATION_WEIGHT.get(hit.match_location, 0.4)
        confidence = 0.7 if in_starting else 0.55 * weight
        signals = [
            EvidenceSignal(
                type="title_match" if hit.match_location == "title" else "format_match",
                value=hit.snippet,
                matched_term=hit.matched_term,
            )
        ]
        cand = CandidateTable(
            table_id=entry.table_id,
            ontology_role="input",
            ontology_subtype=concept,
            confidence=confidence,
            evidence_quality="high" if in_starting and hit.match_location in ("title", "header") else "partial",
            why_selected=(
                f"lexicon match in {hit.match_location} for concept {concept!r}"
            ),
            evidence_signals=signals,
            upstream_node_type=entry.node_type,  # type: ignore[arg-type]
            node_type_override_proposed=override,
            parent_table_id=entry.parent_table_id,
            parent_path=list(entry.parent_path),
            extracted_payload=InputPayload(),
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
                    f"override {entry.table_id} ({entry.node_type} → input) "
                    f"via lexicon hit {hit.matched_term!r} in {hit.match_location}"
                )
        except AlreadyClassified:
            pass

    def _classify_structural(self, entry: FinalMapEntry) -> None:
        cand = CandidateTable(
            table_id=entry.table_id,
            ontology_role="input",
            ontology_subtype="structural_input",
            confidence=0.55,
            evidence_quality="partial",
            why_selected=(
                "structural sanity: low formula density, high DAG fan-out — "
                "input fingerprint despite upstream label"
            ),
            evidence_signals=[
                EvidenceSignal(
                    type="formula_density",
                    value=self.ctx.sanity.compute_formula_density(entry.table_id),
                ),
                EvidenceSignal(
                    type="dag_fanout",
                    value=self.ctx.sanity.get_dag_fanout(entry.table_id),
                ),
            ],
            upstream_node_type=entry.node_type,  # type: ignore[arg-type]
            node_type_override_proposed=True,
            parent_table_id=entry.parent_table_id,
            parent_path=list(entry.parent_path),
            extracted_payload=InputPayload(),
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
                        signal="structural_sanity_low_formula_high_fanout",
                        value={
                            "formula_density": self.ctx.sanity.compute_formula_density(entry.table_id),
                            "dag_fanout": self.ctx.sanity.get_dag_fanout(entry.table_id),
                        },
                    )
                ],
            )
            self._key_decisions.append(
                f"structural override {entry.table_id} → input "
                f"(density+fanout fingerprint)"
            )
        except AlreadyClassified:
            pass

    def _validator_phase(self, elapsed: float) -> AgentOutputRecord:
        node = self.ctx.builder.summary.inputs
        candidates = node.candidate_tables
        # Found / absent concepts.
        found_concepts: List[str] = []
        absent_concepts: List[str] = []
        for c in self.state.expected_concepts:
            sub = c
            if any(cand.ontology_subtype == sub for cand in candidates):
                found_concepts.append(sub)
                node.expected_concepts_status[sub] = "found"
            else:
                absent_concepts.append(sub)
                node.expected_concepts_status[sub] = "absent"
        n = len(candidates)
        summary = template_inputs_summary(n, found_concepts, absent_concepts)
        node.summary = summary
        node.summary_source = "template"
        node.evidence_quality = "high" if found_concepts and not absent_concepts else (
            "partial" if found_concepts else "absent"
        )
        node.present = bool(candidates)
        return AgentOutputRecord(
            status="complete" if candidates else "partial",
            evidence_quality=node.evidence_quality,
            summary=summary,
            summary_source="template",
            tables_classified=n,
            node_type_overrides_proposed=sum(
                1 for c in candidates if c.node_type_override_proposed
            ),
            expected_concepts_found=found_concepts,
            expected_concepts_absent=absent_concepts,
            applied_heuristics=list(self.state.applied_heuristics),
            tools_called=list(self._tools_called),
            llm_calls_used=0,
            elapsed_seconds=elapsed,
            key_decisions=list(self._key_decisions),
            human_readable_output=template_human_readable_classification(
                "inputs",
                n_classified=n,
                expected_found=found_concepts,
                expected_absent=absent_concepts,
                overrides=sum(1 for c in candidates if c.node_type_override_proposed),
            ),
        )
