"""Inputs ontology agent (per ontology_inputs.txt §6 / spec §5)."""

from __future__ import annotations

from typing import ClassVar, Dict, List, Optional, Set

from xl_ontology.agents.inputs_helpers import (
    core_region_formula_density,
    dag_similar_table_ids,
    sheet_matches_anchor,
    table_has_value_cells,
)
from xl_ontology.agents.prompts import (
    INPUTS_LEXICON,
    SECONDARY_INPUT_LEXICON,
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
        """Two-phase v1 inputs algorithm — refactored for sheet-name
        signal and DAG-similarity sibling discovery.

        Phase A — anchor identification (best of 3 evidence sources):
            (1) sheet-name substring match against ANCHOR_SHEET_NAME_PATTERNS,
            (2) cell lexicon hit weighted by _LOCATION_WEIGHT,
            (3) low fan-in (≤1) — true DAG roots.
          Pre-filters reject metadata/non-DAG (Pass-1 ineligibility),
          all-formula tables (no value cells), and high-formula-density
          tables (formula_density >= 0.5; those belong to the calc agent).

        Phase B — sibling discovery via DAG fan-in similarity:
          Find all eligible tables sharing a fan-in with any anchor.
          Anchor sheets = sheets-of-anchors ∪ sheets-of-similar-tables.
          Run SECONDARY_INPUT_LEXICON over tables on anchor sheets;
          best-weighted concept wins → structural_input_<concept>.
          Tables with no concept hit but on an anchor sheet → generic
          structural_input.

        Phase C — fan-in fallback (catches inputs on non-anchor sheets):
          fanin <= 1 AND has_value_cells AND formula_density < 0.5
          → structural_input.
        """
        # A. STARTING SET — node_type=input
        starting_set: List[FinalMapEntry] = [
            e for e in self.ctx.final_map.all_entries() if e.node_type == "input"
        ]

        # PHASE A — anchor identification with combined sheet-name + lexicon + fan-in scoring.
        anchors: List[FinalMapEntry] = []
        for concept in self.state.expected_concepts:
            anchor_entry = self._find_anchor_for_concept(concept)
            if anchor_entry is None:
                continue
            classified_before = self.ctx.builder.is_classified(anchor_entry.table_id)
            # We have to synthesise a "hit" for _classify when the win
            # came from sheet-name match alone. Use a lightweight stub.
            stub_hit = self._anchor_hits_cache.get(anchor_entry.table_id)
            if stub_hit is None:
                stub_hit = Hit(
                    sheet=anchor_entry.sheet_name,
                    cell="A1",
                    table_id=anchor_entry.table_id,
                    snippet=anchor_entry.sheet_name or "",
                    matched_term=concept.replace("_", " "),
                    match_location="title",
                )
            self._classify(
                anchor_entry, concept, stub_hit,
                in_starting=anchor_entry in starting_set,
            )
            if (
                self.ctx.builder.is_classified(anchor_entry.table_id) == self.node_name
                and classified_before is None
            ):
                anchors.append(anchor_entry)

        # PHASE B — DAG-similarity-based sibling discovery + secondary lexicon.
        if len(anchors) >= 2:
            self._phase_b_secondary(anchors, starting_set)

        # PHASE C — fan-in based structural fallback.
        self._track_tool("structural_sanity")
        for entry in self.ctx.final_map.all_entries():
            if self.ctx.builder.is_classified(entry.table_id):
                continue
            from xl_ontology.upstream import is_pass1_eligible
            if not is_pass1_eligible(self.ctx.dag, entry.table_id, entry.node_type):
                continue
            fanin = self.ctx.sanity.get_dag_fanin(entry.table_id)
            density = self.ctx.sanity.compute_formula_density(entry.table_id)
            core_density = core_region_formula_density(self.ctx, entry)
            if (
                fanin <= 1
                and density < 0.5
                and core_density < 1.0
                and table_has_value_cells(self.ctx, entry)
            ):
                self._classify_structural(entry)

    # ---------------------------------------------------------------- Phase A helpers

    def _find_anchor_for_concept(self, concept: str) -> Optional[FinalMapEntry]:
        """Pick the best anchor table for one concept.

        Combined score per candidate:
            sheet_match: +1.5  (sheet name substring-matches concept's
                                ANCHOR_SHEET_NAME_PATTERNS)
            lexicon_hit: +location_weight  (max 1.0 for title)
            fanin_root:  +0.5  (fanin <= 1 — true DAG root)

        Pre-filters: Pass-1 eligible (must have a DAG node, not
        metadata/data_table); not already classified; either a sheet-
        name match OR a cell lexicon hit; has at least one non-formula
        value cell in the core region; core region not 100% formulas
        (array-formula spill cells included). Sheet-matched candidates
        get a relaxed bbox-density cap (< 1.0) since the workbook
        author named the sheet for this concept; lexicon-only
        candidates keep the strict 0.5 cap to avoid claiming calc
        tables that incidentally mention a term.

        NOTE: there is no filter on sheet name — calculation_* and
        Results sheets are still considered candidates here. Only the
        positive sheet-name boost applies; nothing rejects a sheet by
        name.
        """
        from xl_ontology.upstream import is_pass1_eligible

        if not hasattr(self, "_anchor_hits_cache"):
            self._anchor_hits_cache: Dict[str, Hit] = {}

        # F.2 — read terms from the loaded pack via concept_terms.
        # Falls back to INPUTS_LEXICON only if pack lookup returns
        # nothing (legacy ctx without pack, or unknown concept).
        from xl_ontology.concept_terms import get_concept_terms
        terms = get_concept_terms(self.ctx.pack, f"inputs.{concept}")
        if not terms or terms == [concept.replace("_", " ")]:
            # Pack didn't carry this concept — keep the legacy dict
            # as a backup so removing a concept folder mid-run doesn't
            # silently drop matches.
            terms = INPUTS_LEXICON.get(concept, [concept.replace("_", " ")])
        self._track_tool("search_workbook_lexicon")
        hits = search_workbook_lexicon(self.ctx, terms, "whole_table")
        # Best lexicon hit per table_id
        hit_by_table: Dict[str, Hit] = {}
        for h in hits:
            if not h.table_id:
                continue
            cur = hit_by_table.get(h.table_id)
            if cur is None or _LOCATION_WEIGHT.get(h.match_location, 0.0) > _LOCATION_WEIGHT.get(cur.match_location, 0.0):
                hit_by_table[h.table_id] = h

        # Build candidate set: eligible leaf tables that either have a
        # sheet-name match OR a cell-content lexicon hit.
        candidates: List[tuple[float, FinalMapEntry, Optional[Hit]]] = []
        all_entries = list(self.ctx.final_map.all_entries())
        for entry in all_entries:
            if self.ctx.builder.is_classified(entry.table_id):
                continue
            if not is_pass1_eligible(self.ctx.dag, entry.table_id, entry.node_type):
                continue
            sheet_match = sheet_matches_anchor(entry.sheet_name, concept)
            cell_hit = hit_by_table.get(entry.table_id)
            if not sheet_match and cell_hit is None:
                continue
            # HARD: core region not 100% formulas (array-formula spills counted).
            core_density = core_region_formula_density(self.ctx, entry)
            if core_density >= 1.0:
                continue
            if not table_has_value_cells(self.ctx, entry):
                continue
            # Density cap: relaxed for sheet-matched candidates.
            density = self.ctx.sanity.compute_formula_density(entry.table_id)
            density_cap = 1.0 if sheet_match else 0.5
            if density >= density_cap:
                continue
            score = 0.0
            if sheet_match:
                score += 1.5
            if cell_hit is not None:
                score += _LOCATION_WEIGHT.get(cell_hit.match_location, 0.0)
            fanin = self.ctx.sanity.get_dag_fanin(entry.table_id)
            if fanin <= 1:
                score += 0.5
            candidates.append((score, entry, cell_hit))

        if not candidates:
            return None
        # Pick the strongest. Stable tiebreak by table_id for determinism.
        candidates.sort(key=lambda c: (-c[0], c[1].table_id))
        score, entry, cell_hit = candidates[0]
        if cell_hit is not None:
            self._anchor_hits_cache[entry.table_id] = cell_hit
        return entry

    def _phase_b_secondary(
        self,
        anchors: List[FinalMapEntry],
        starting_set: List[FinalMapEntry],
    ) -> None:
        """Apply SECONDARY_INPUT_LEXICON to tables on anchor sheets.

        Anchor sheets are computed via DAG similarity:
            anchor_sheets = sheets-of-anchors
                          ∪ sheets-of-tables-with-same-fanin-as-any-anchor
        This lets a `Market shares` anchor on the "Market shares"
        sheet pull in the "Population by province" sheet's input
        tables when they share the anchor's fan-in (= 0 typically for
        DAG roots).

        Each anchor-sheet table is scored against every secondary
        concept's lexicon; best title/header/row_label match wins
        as `structural_input_<concept>`. Tables with no concept
        match still get the generic `structural_input` so they
        surface in candidate_tables.
        """
        from xl_ontology.upstream import is_pass1_eligible

        all_entries = list(self.ctx.final_map.all_entries())
        # DAG-similarity expansion: tables sharing fan-in with an anchor.
        similar_table_ids = dag_similar_table_ids(
            self.ctx.sanity, anchors, all_entries, same_fanin=True,
        )
        similar_sheets: Set[str] = {
            e.sheet_name for e in all_entries if e.table_id in similar_table_ids
        }
        anchor_sheets = {a.sheet_name for a in anchors} | similar_sheets

        anchor_fanins = [self.ctx.sanity.get_dag_fanin(a.table_id) for a in anchors]
        anchor_fanouts = [self.ctx.sanity.get_dag_fanout(a.table_id) for a in anchors]
        fanin_lo, fanin_hi = (min(anchor_fanins), max(anchor_fanins)) if anchor_fanins else (0, 0)
        fanout_lo, fanout_hi = (min(anchor_fanouts), max(anchor_fanouts)) if anchor_fanouts else (0, 0)

        # Pre-run lexicon searches once per secondary concept; group by table_id.
        # F.2 — terms come from the loaded pack via concept_terms;
        # SECONDARY_INPUT_LEXICON is the fallback when pack lookup
        # returns nothing.
        from xl_ontology.concept_terms import get_concept_terms
        concept_hits_by_table: Dict[str, List[tuple[str, Hit]]] = {}
        for concept, fallback_terms in SECONDARY_INPUT_LEXICON.items():
            terms = get_concept_terms(self.ctx.pack, f"inputs.{concept}")
            if not terms or terms == [concept.replace("_", " ")]:
                terms = fallback_terms
            self._track_tool("search_workbook_lexicon")
            for h in search_workbook_lexicon(self.ctx, terms, "whole_table"):
                if not h.table_id:
                    continue
                concept_hits_by_table.setdefault(h.table_id, []).append((concept, h))

        for entry in all_entries:
            if entry.sheet_name not in anchor_sheets:
                continue
            if not is_pass1_eligible(self.ctx.dag, entry.table_id, entry.node_type):
                continue
            if self.ctx.builder.is_classified(entry.table_id):
                continue
            # Input fingerprint: fan-in 0 or 1 (DAG root). Tables with
            # higher fan-in are downstream consumers (calculations).
            entry_fanin = self.ctx.sanity.get_dag_fanin(entry.table_id)
            if entry_fanin > 1:
                continue
            # Hard rejection: all-formula core region → not an input.
            if core_region_formula_density(self.ctx, entry) >= 1.0:
                continue
            # Must be mostly value cells overall.
            density = self.ctx.sanity.compute_formula_density(entry.table_id)
            if density >= 0.5:
                continue
            if not table_has_value_cells(self.ctx, entry):
                continue

            # Pick the highest-weighted concept hit on this table.
            tagged_hits = concept_hits_by_table.get(entry.table_id, [])
            best_concept: Optional[str] = None
            best_hit: Optional[Hit] = None
            best_weight = -1.0
            for concept, hit in tagged_hits:
                w = _LOCATION_WEIGHT.get(hit.match_location, 0.0)
                if w > best_weight:
                    best_weight = w
                    best_concept = concept
                    best_hit = hit

            # Threshold: title / header / row_label only (weight ≥ 0.7).
            # Tables matched only in body or formula text don't get a
            # secondary concept (too noisy); they fall through to the
            # generic structural_input below.
            if best_concept and best_hit and best_weight >= 0.7:
                self._classify_secondary(
                    entry, best_concept, best_hit,
                    fanin_range=(fanin_lo, fanin_hi),
                    fanout_range=(fanout_lo, fanout_hi),
                    in_starting=entry in starting_set,
                )
            else:
                # No secondary concept matched — still on an anchor
                # sheet, so emit a generic structural_input candidate
                # for operator visibility.
                self._classify_secondary(
                    entry, "unspecified", best_hit,
                    fanin_range=(fanin_lo, fanin_hi),
                    fanout_range=(fanout_lo, fanout_hi),
                    in_starting=entry in starting_set,
                    generic=True,
                )

    def _classify_secondary(
        self,
        entry: FinalMapEntry,
        concept: str,
        hit: Optional[Hit],
        *,
        fanin_range: tuple[int, int],
        fanout_range: tuple[int, int],
        in_starting: bool,
        generic: bool = False,
    ) -> None:
        """Record a Phase-B candidate as `structural_input_<concept>`.

        Adds a flag if fan-in or fan-out is sharply outside the
        anchor range (>2x or <0.5x of either bound). Override is
        proposed when upstream node_type isn't `input`.
        """
        from xl_ontology.upstream import is_pass1_eligible

        if not is_pass1_eligible(self.ctx.dag, entry.table_id, entry.node_type):
            return

        subtype = "structural_input" if generic else f"structural_input_{concept}"
        my_fanin = self.ctx.sanity.get_dag_fanin(entry.table_id)
        my_fanout = self.ctx.sanity.get_dag_fanout(entry.table_id)

        flags: List[str] = []
        if fanin_range[1] > 0 and my_fanin > 2 * fanin_range[1]:
            flags.append("fanin_outlier_high")
        if fanin_range[0] > 0 and my_fanin * 2 < fanin_range[0]:
            flags.append("fanin_outlier_low")
        if fanout_range[1] > 0 and my_fanout > 2 * fanout_range[1]:
            flags.append("fanout_outlier_high")
        if fanout_range[0] > 0 and my_fanout * 2 < fanout_range[0]:
            flags.append("fanout_outlier_low")

        if hit is not None:
            why = (
                f"phase B: anchor-sheet sibling, secondary lexicon match "
                f"in {hit.match_location} for concept {concept!r}"
            )
            evidence_signals = [
                EvidenceSignal(
                    type="title_match" if hit.match_location == "title" else "format_match",
                    value=hit.snippet,
                    matched_term=hit.matched_term,
                ),
                EvidenceSignal(type="dag_fanin", value=my_fanin),
                EvidenceSignal(type="dag_fanout", value=my_fanout),
            ]
        else:
            why = "phase B: anchor-sheet sibling, no concept lexicon fired"
            evidence_signals = [
                EvidenceSignal(type="dag_fanin", value=my_fanin),
                EvidenceSignal(type="dag_fanout", value=my_fanout),
            ]

        override = entry.node_type != "input"
        cand = CandidateTable(
            table_id=entry.table_id,
            ontology_role="input",
            ontology_subtype=subtype,
            confidence=0.55,
            evidence_quality="partial",
            why_selected=why,
            evidence_signals=evidence_signals,
            upstream_node_type=entry.node_type,  # type: ignore[arg-type]
            node_type_override_proposed=override,
            parent_table_id=entry.parent_table_id,
            parent_path=list(entry.parent_path),
            extracted_payload=InputPayload(),
            flags=flags,
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
                        signal="phase_b_anchor_sheet_sibling",
                        value=f"matched_term={hit.matched_term if hit else None}",
                        matched_term=hit.matched_term if hit else None,
                    )
                ] if override else None,
            )
            self._key_decisions.append(
                f"phase_b: {entry.table_id} -> {subtype}"
                + (f" (flags: {','.join(flags)})" if flags else "")
            )
        except AlreadyClassified:
            pass
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
        # v1.A Pass-1 eligibility: skip tables with no DAG node, AND
        # skip metadata/data_table entries even when they're in the DAG
        # (they're descriptions, not inputs/calcs/results).
        from xl_ontology.upstream import is_pass1_eligible
        if not is_pass1_eligible(self.ctx.dag, entry.table_id, entry.node_type):
            return
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
        # v1.A Pass-1 eligibility: same filter as `_classify`. The
        # fanout>5 predicate that gates structural-sanity already
        # implicitly excludes most non-DAG tables, but the metadata
        # filter is non-redundant: a metadata-typed table can still
        # have fanout>5 if other tables reference it.
        from xl_ontology.upstream import is_pass1_eligible
        if not is_pass1_eligible(self.ctx.dag, entry.table_id, entry.node_type):
            return
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
