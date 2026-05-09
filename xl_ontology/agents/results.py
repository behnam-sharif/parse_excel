"""Results ontology agent."""

from __future__ import annotations

from typing import ClassVar, Dict, List

from xl_ontology.agents.prompts import (
    RESULTS_LEXICON,
    template_human_readable_classification,
    template_results_summary,
)
from xl_ontology.agents.runner import OntologyAgent, dispatch_defect_stub
from xl_ontology.schemas import (
    AgentOutputRecord,
    CandidateTable,
    EvidenceSignal,
    NodeName,
    OverrideEvidence,
    ResultPayload,
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


class ResultsAgent(OntologyAgent):
    node_name: ClassVar[NodeName] = "results"

    def _cross_check_phase(self) -> None:
        # A. STARTING SET — node_type=output
        starting_ids = {
            e.table_id for e in self.ctx.final_map.all_entries() if e.node_type == "output"
        }
        # D. CROSS-CHECK BY EXPECTED CONCEPT — lexicon (whole_table)
        # F.2 — terms come from the loaded pack via concept_terms.
        from xl_ontology.concept_terms import get_concept_terms
        for concept in self.state.expected_concepts:
            terms = get_concept_terms(self.ctx.pack, f"results.{concept}")
            if not terms or terms == [concept.replace("_", " ")]:
                terms = RESULTS_LEXICON.get(concept, [concept.replace("_", " ")])
            self._track_tool("search_workbook_lexicon")
            hits = search_workbook_lexicon(self.ctx, terms, "whole_table")
            if not hits:
                continue
            # Single best-weighted hit per concept (pre-v0.1 behavior).
            # Filter out already-classified tables so the parent concept
            # can fall back to its second-best hit (see inputs.py).
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
        # E. STRUCTURAL SANITY CHECK — fanout==0 AND fanin>1 AND
        # (sheet OR title matches results lexicon)
        self._track_tool("structural_sanity")
        results_lexicon_terms = list(RESULTS_LEXICON.get("incremental_budget_impact", []))
        results_lexicon_terms += ["result", "output", "summary", "headline"]
        for entry in self.ctx.final_map.all_entries():
            if self.ctx.builder.is_classified(entry.table_id):
                continue
            fanout = self.ctx.sanity.get_dag_fanout(entry.table_id)
            fanin = self.ctx.sanity.get_dag_fanin(entry.table_id)
            sheet_match = self.ctx.sanity.sheet_name_matches_lexicon(
                entry.sheet_name, results_lexicon_terms
            )
            title_match = self.ctx.sanity.title_matches_lexicon(
                entry.title, results_lexicon_terms
            )
            if fanout == 0 and fanin > 1 and (sheet_match or title_match) and entry.node_type != "output":
                self._classify_structural(entry, fanin, sheet_match, title_match)
        # I. DEFECT FLAGS — dispatch stubs
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
        override = entry.node_type != "output"
        weight = _LOCATION_WEIGHT.get(hit.match_location, 0.4)
        confidence = 0.7 if in_starting else 0.55 * weight
        cand = CandidateTable(
            table_id=entry.table_id,
            ontology_role="result",
            ontology_subtype=concept,
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
            extracted_payload=ResultPayload(
                time_indexed=False,
                has_cumulative_column=False,
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
                    f"override {entry.table_id} ({entry.node_type} → result) "
                    f"via lexicon hit {hit.matched_term!r}"
                )
        except AlreadyClassified:
            pass

    def _classify_structural(
        self,
        entry: FinalMapEntry,
        fanin: int,
        sheet_match: bool,
        title_match: bool,
    ) -> None:
        # v1.A Pass-1 eligibility — same filter as `_classify`.
        from xl_ontology.upstream import is_pass1_eligible
        if not is_pass1_eligible(self.ctx.dag, entry.table_id, entry.node_type):
            return
        cand = CandidateTable(
            table_id=entry.table_id,
            ontology_role="result",
            ontology_subtype="structural_result",
            confidence=0.55,
            evidence_quality="partial",
            why_selected=(
                "structural sanity: terminal in DAG, multiple inputs, name "
                "matches results lexicon"
            ),
            evidence_signals=[
                EvidenceSignal(type="dag_fanout", value=0),
                EvidenceSignal(
                    type="format_match" if title_match else "structural_fingerprint",
                    value="title" if title_match else "sheet_name",
                ),
            ],
            upstream_node_type=entry.node_type,  # type: ignore[arg-type]
            node_type_override_proposed=True,
            parent_table_id=entry.parent_table_id,
            parent_path=list(entry.parent_path),
            extracted_payload=ResultPayload(
                time_indexed=False, has_cumulative_column=False
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
                        signal="structural_sanity_terminal_named_result",
                        value={
                            "fanin": fanin,
                            "sheet_match": sheet_match,
                            "title_match": title_match,
                        },
                    )
                ],
            )
            self._key_decisions.append(
                f"structural override {entry.table_id} → result "
                f"(terminal + named like result)"
            )
        except AlreadyClassified:
            pass

    def _validator_phase(self, elapsed: float) -> AgentOutputRecord:
        node = self.ctx.builder.summary.results
        candidates = node.candidate_tables
        subtypes = sorted({c.ontology_subtype for c in candidates if c.ontology_subtype})
        has_incremental = any(
            c.ontology_subtype == "incremental_budget_impact" for c in candidates
        )
        summary = template_results_summary(len(candidates), subtypes, has_incremental)
        node.summary = summary
        node.summary_source = "template"
        node.evidence_quality = "high" if has_incremental else (
            "partial" if candidates else "absent"
        )
        node.present = bool(candidates)
        for c in self.state.expected_concepts:
            node.expected_concepts_status[c] = (
                "found" if any(cand.ontology_subtype == c for cand in candidates) else "absent"
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
                "results",
                n_classified=len(candidates),
                expected_found=found,
                expected_absent=absent,
                overrides=sum(1 for c in candidates if c.node_type_override_proposed),
            ),
        )
