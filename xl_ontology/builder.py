"""SummaryModelBuilder — mutable scratch state for the agent run.

Tools mutate this in-place via the write-tool functions; at run end
the runner serialises ``builder.summary`` to disk via
``write_summary_model``. Same pattern for agent_outputs and table_tags.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from xl_ontology.schemas import (
    AgentOutputRecord,
    AgentOutputsFile,
    CandidateTable,
    ConceptRecord,
    Evidence,
    LlmBudgetSnapshot,
    NodeName,
    OntologyClassificationNode,
    OverallModelNode,
    ParentGroup,
    ProposedCorrection,
    SearchLogEntry,
    SummaryModel,
    SummarySource,
    TableTagRecord,
    TableTagsFile,
    TagSummary,
    UnclassifiedTable,
)


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def _empty_classification() -> OntologyClassificationNode:
    return OntologyClassificationNode(
        present=False, evidence_quality="absent", summary_source="template"
    )


@dataclass
class SummaryModelBuilder:
    """In-memory scratch model. Methods are intended to be called by the
    write-tool surface (xl_ontology.tools.writers)."""

    model_id: str
    ontology_id: str
    ontology_version: str
    workbook_file_hash: str = "sha256:0"
    seed_calls_max: int = 8
    enrichment_calls_max: int = 5

    summary: SummaryModel = field(init=False)
    agent_outputs: AgentOutputsFile = field(init=False)
    _classified_table_ids: Dict[str, NodeName] = field(default_factory=dict)
    _seen_overrides: int = 0

    def __post_init__(self) -> None:
        ts = _now_iso()
        self.summary = SummaryModel(
            model_id=self.model_id,
            ontology_id=self.ontology_id,
            ontology_version=self.ontology_version,
            seed_timestamp=ts,
            workbook_file_hash=self.workbook_file_hash,
            overall_model=OverallModelNode(
                present=False, evidence_quality="absent", summary_source="template"
            ),
            inputs=_empty_classification(),
            calculations=_empty_classification(),
            results=_empty_classification(),
            llm_budget=LlmBudgetSnapshot(
                seed_calls_used=0,
                seed_calls_max=self.seed_calls_max,
                enrichment_calls_used=0,
            ),
        )
        self.agent_outputs = AgentOutputsFile(
            model_id=self.model_id,
            ontology_id=self.ontology_id,
            run_timestamp=ts,
            agents={},
        )

    # ---------- mutation helpers ----------

    def record_concept(
        self,
        concept_name: str,
        value,
        evidence: List[Evidence],
        confidence: Optional[float],
        evidence_quality: str,
    ) -> None:
        existing = self.summary.overall_model.concepts.get(concept_name)
        if existing is None:
            self.summary.overall_model.concepts[concept_name] = ConceptRecord(
                value=value,
                evidence=list(evidence),
                confidence=confidence,
                evidence_quality=evidence_quality,  # type: ignore[arg-type]
            )
            return
        # Merge: prefer the new value if non-None; concat evidence.
        if value is not None:
            existing.value = value
        existing.evidence.extend(evidence)
        if confidence is not None:
            existing.confidence = confidence
        existing.evidence_quality = evidence_quality  # type: ignore[assignment]

    def record_search_log(
        self, concept_name: str, entries: List[SearchLogEntry]
    ) -> None:
        existing = self.summary.overall_model.concepts.get(concept_name)
        if existing is None:
            existing = ConceptRecord(value=None, evidence_quality="absent")
            self.summary.overall_model.concepts[concept_name] = existing
        existing.search_log.extend(entries)

    def record_summary_text(
        self, node: NodeName, text: Optional[str], source: SummarySource
    ) -> None:
        target = getattr(self.summary, node)
        target.summary = text
        target.summary_source = source

    def record_classification(
        self,
        candidate: CandidateTable,
        agent: NodeName,
    ) -> bool:
        """Append a classification. Returns True if accepted, False if
        the table_id was already classified by another node (one-table-
        one-role per spec §7.4 / plan §2.9)."""
        prior = self._classified_table_ids.get(candidate.table_id)
        if prior is not None and prior != agent:
            return False
        self._classified_table_ids[candidate.table_id] = agent
        node = self._classification_target(agent)
        # Replace if already classified by same agent (idempotent updates)
        node.candidate_tables = [
            c for c in node.candidate_tables if c.table_id != candidate.table_id
        ]
        node.candidate_tables.append(candidate)
        node.present = True
        if candidate.node_type_override_proposed:
            self._seen_overrides += 1
        return True

    def record_parent_group(self, agent: NodeName, group: ParentGroup) -> None:
        node = self._classification_target(agent)
        node.parent_groups.append(group)

    def record_unclassified(self, entry: UnclassifiedTable) -> None:
        # Avoid duplicates.
        existing = {u.table_id for u in self.summary.unclassified_tables}
        if entry.table_id in existing:
            return
        self.summary.unclassified_tables.append(entry)

    def record_correction(self, c: ProposedCorrection) -> None:
        self.summary.proposed_table_corrections.append(c)

    def record_agent_output(self, agent: NodeName, rec: AgentOutputRecord) -> None:
        self.agent_outputs.agents[agent] = rec

    def update_budget_snapshot(self, snapshot: LlmBudgetSnapshot) -> None:
        self.summary.llm_budget = snapshot

    def is_classified(self, table_id: str) -> Optional[NodeName]:
        return self._classified_table_ids.get(table_id)

    def total_overrides(self) -> int:
        return self._seen_overrides

    # ---------- table_tags assembly ----------

    def build_table_tags(
        self,
        table_id_to_finalize_summary: Dict[str, dict],
    ) -> TableTagsFile:
        """Build a TableTagsFile by walking the finalize entries.

        ``table_id_to_finalize_summary`` is provided by the caller as a
        dict of {table_id: {sheet, title, upstream_node_type}}. The
        builder pulls classification info from its own state.
        """
        tables: List[TableTagRecord] = []
        counts = {
            "classified_input": 0,
            "classified_calculation": 0,
            "classified_result": 0,
            "classified_overall_evidence": 0,
            "unclassified": 0,
            "out_of_scope_for_pass_1": 0,
            "node_type_overrides_proposed": 0,
        }
        # Build a fast lookup from table_id -> CandidateTable (per agent)
        cand_lookup: Dict[str, CandidateTable] = {}
        for agent_name in ("inputs", "calculations", "results"):
            node = getattr(self.summary, agent_name)
            for c in node.candidate_tables:
                cand_lookup[c.table_id] = c

        for table_id, fin_summary in table_id_to_finalize_summary.items():
            cand = cand_lookup.get(table_id)
            agent_owner = self.is_classified(table_id)
            if cand is not None:
                role = cand.ontology_role
                subtype = cand.ontology_subtype
                confidence = cand.confidence
                quality = cand.evidence_quality
                heuristics = list(cand.applied_heuristics)
                flags = list(cand.flags)
                override = cand.node_type_override_proposed
                in_top = True
            elif fin_summary.get("pass1_eligible", True) is False or \
                    fin_summary.get("in_dag", True) is False:
                # v1.A: tables that are out of scope for Pass-1 labelling.
                # Two reasons to land here:
                #   - no DAG node at all (orphan / unreferenced)
                #   - DAG node but node_type is metadata/data_table
                #     (description / narrative / reference content)
                # Distinguished from `unclassified` so future comprehension
                # passes can decide whether to treat them.
                role = "out_of_scope_for_pass_1"
                subtype = None
                confidence = None
                quality = None
                heuristics = []
                flags = []
                override = False
                in_top = False
            else:
                role = "unclassified"
                subtype = None
                confidence = None
                quality = None
                heuristics = []
                flags = []
                override = False
                in_top = False
            tag = TableTagRecord(
                table_id=table_id,
                sheet=fin_summary["sheet"],
                title=fin_summary.get("title"),
                upstream_node_type=fin_summary["upstream_node_type"],
                ontology_role=role,  # type: ignore[arg-type]
                ontology_subtype=subtype,
                node_type_override_proposed=override,
                in_top_candidates=in_top,
                confidence=confidence,
                evidence_quality=quality,
                agent_owner=agent_owner,
                applied_heuristics=heuristics,
                flags=flags,
            )
            tables.append(tag)
            if role == "input":
                counts["classified_input"] += 1
            elif role == "calculation":
                counts["classified_calculation"] += 1
            elif role == "result":
                counts["classified_result"] += 1
            elif role == "overall_evidence":
                counts["classified_overall_evidence"] += 1
            elif role == "out_of_scope_for_pass_1":
                counts["out_of_scope_for_pass_1"] += 1
            else:
                counts["unclassified"] += 1
            if override:
                counts["node_type_overrides_proposed"] += 1

        return TableTagsFile(
            model_id=self.model_id,
            ontology_id=self.ontology_id,
            run_timestamp=self.agent_outputs.run_timestamp,
            tables=tables,
            tag_summary=TagSummary(
                total_tables=len(tables),
                **counts,
            ),
        )

    def _classification_target(self, agent: NodeName) -> OntologyClassificationNode:
        if agent == "inputs":
            return self.summary.inputs
        if agent == "calculations":
            return self.summary.calculations
        if agent == "results":
            return self.summary.results
        raise ValueError(f"agent {agent!r} does not own classifications")
