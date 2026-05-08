"""xl_ontology/schemas.py — Pydantic v2 data model.

Mirrors the JSON shapes in spec §12 and the structures named in plan §4.
Phase A defines the surface; behaviour (validators, builders) lands in
later phases. Field names match the spec verbatim.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Strict base — extra keys forbidden in v0 to catch contract drift early.
# ---------------------------------------------------------------------------

class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=False)


# ---------------------------------------------------------------------------
# Pack-loaded types (plan §4 — pack-loaded)
# ---------------------------------------------------------------------------

NodeName = Literal["overall_model", "inputs", "calculations", "results"]
HeuristicStatus = Literal["active", "experimental", "deprecated", "subsumed_by_ml"]


class HeuristicFrontMatter(_Strict):
    """Front-matter parsed from a heuristic chunk in <node>.heuristics.md."""

    id: str
    applies_to: NodeName
    concept: str
    trigger: str
    priority: int
    last_updated: str  # date string as written (YYYY-MM-DD); not parsed in v0
    status: HeuristicStatus


class Heuristic(_Strict):
    """A single heuristic chunk: front-matter + prose body."""

    id: str
    applies_to: NodeName
    concept: str
    trigger: str
    priority: int
    last_updated: str
    status: HeuristicStatus
    body_text: str


class NodeIdentity(_Strict):
    """Parsed §1 IDENTITY block from <node>.txt."""

    ontology_pack: str
    ontology_node: NodeName
    version: str
    model_family: str
    expected_scale: str
    llm_budget_share: str


class OntologyNode(_Strict):
    """One of the four node bundles in a pack."""

    node_name: NodeName
    txt_path: Path
    context_md_path: Path
    heuristics_md_path: Path
    txt_text: str
    context_text: str
    identity: NodeIdentity
    heuristics: List[Heuristic]


class PackBudgets(_Strict):
    """Optional pack-overview front-matter overriding spec defaults.

    Defaults match REVISION 1 (spec §11): 8 LLM calls per workbook
    in seed phase, 5 per xl_ask query in enrichment. The class
    default must agree with config/stages.yaml — agreement enforced
    by tests in xl_ontology/tests/test_pack_loader.py and
    xl_ontology/tests/test_config_loading.py.
    """

    seed_llm_budget_max: int = 8
    enrichment_llm_budget_max: int = 5


class PackWarning(_Strict):
    """A non-fatal issue surfaced by the pack loader. Examples:
    unknown extra files in the pack directory, deprecated heuristics
    encountered, etc. The runner converts each into a
    ``WarningEvent`` trace entry (plan §5.7 / §12.10)."""

    category: str  # e.g. "unknown_pack_file"
    message: str
    file_path: Optional[str] = None  # absolute or relative path string


class OntologyPack(_Strict):
    """Top-level loaded pack."""

    id: str
    version: str
    pack_dir: Path
    pack_overview_text: str
    budgets: PackBudgets
    nodes: Dict[NodeName, OntologyNode]
    warnings: List[PackWarning] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Per-table classification types (plan §4 — per-table)
# ---------------------------------------------------------------------------

EvidenceMatchType = Literal[
    "lexicon_match",
    "format_match",
    "dropdown_value",
    "llm_inferred",
    "cross_reference",
    "structural_fingerprint",
]


class Evidence(_Strict):
    sheet: str
    cell: str
    table_id: Optional[str] = None
    snippet: str
    match_type: EvidenceMatchType
    matched_term: Optional[str] = None


class SearchLogEntry(_Strict):
    tool: str
    args_summary: Union[str, Dict[str, Any]]
    result_summary: Union[str, Dict[str, Any]]
    hits: Optional[int] = None


EvidenceSignalType = Literal[
    "title_match",
    "formula_density",
    "dag_fanin",
    "dag_fanout",
    "format_match",
    "structural_fingerprint",
]


class EvidenceSignal(_Strict):
    type: EvidenceSignalType
    value: Any
    matched_term: Optional[str] = None


# Extracted-payload sub-shapes (one per ontology node).
ParameterFormatRole = Literal["input_value", "derived_value", "label", "header"]


class ParameterRecord(_Strict):
    name: str
    value: Any
    unit: Optional[str] = None
    source_cell: str
    is_formula: bool
    format_role: ParameterFormatRole


class InputPayload(_Strict):
    parameters: List[ParameterRecord] = Field(default_factory=list)


class KeyFormulaRecord(_Strict):
    cell: str
    formula: str
    depends_on_tables: List[str] = Field(default_factory=list)


class CalcPayload(_Strict):
    time_indexed: bool
    time_columns: Optional[List[str]] = None
    treatment_dimension: Literal["intervention", "comparator", "both", "n/a"]
    key_formulas: List[KeyFormulaRecord] = Field(default_factory=list)
    feeds_into: List[str] = Field(default_factory=list)
    fed_by: List[str] = Field(default_factory=list)


ResultFormatRole = Literal["headline", "subtotal", "label"]


class ResultMetric(_Strict):
    name: str
    values_by_period: Dict[str, float] = Field(default_factory=dict)
    unit: Optional[str] = None
    source_cells: List[str] = Field(default_factory=list)
    is_formula: bool
    format_role: ResultFormatRole


class ResultPayload(_Strict):
    time_indexed: bool
    time_columns: Optional[List[str]] = None
    has_cumulative_column: bool
    result_metrics: List[ResultMetric] = Field(default_factory=list)
    fed_by: List[str] = Field(default_factory=list)


EvidenceQuality = Literal["high", "partial", "inferred", "absent"]
EvidenceQualityOrPending = Literal["high", "partial", "inferred", "absent", "pending"]
OntologyRoleClassified = Literal["input", "calculation", "result"]
OntologyRoleAny = Literal[
    "input",
    "calculation",
    "result",
    "overall_evidence",
    "unclassified",
    # v1.A: tables whose table_id has no node in dag.json. Pass 1 skips
    # them (no upstream/downstream signal); they are not unclassified.
    "out_of_scope_for_pass_1",
]
UpstreamNodeType = Literal[
    "input", "calc", "output", "mixed", "data_table", "metadata",
    # v6 finalize settings-harvest synthetic leaves (named-range targets
    # not covered by any other leaf's core_region).
    "setting",
]


class CandidateTable(_Strict):
    table_id: str
    ontology_role: OntologyRoleClassified
    ontology_subtype: Optional[str] = None
    confidence: Optional[float] = None
    evidence_quality: EvidenceQuality
    why_selected: str
    evidence_signals: List[EvidenceSignal] = Field(default_factory=list)
    upstream_node_type: UpstreamNodeType
    node_type_override_proposed: bool = False
    parent_table_id: Optional[str] = None
    parent_path: List[str] = Field(default_factory=list)
    sibling_count: int = 0
    parent_split_appears_artificial: bool = False
    applied_heuristics: List[str] = Field(default_factory=list)
    extracted_payload: Union[InputPayload, CalcPayload, ResultPayload]
    flags: List[str] = Field(default_factory=list)


class ParentGroup(_Strict):
    parent_table_id: str
    parent_title: Optional[str] = None
    members: List[str] = Field(default_factory=list)
    all_in_this_node: bool
    cross_node_members: List[Dict[str, str]] = Field(default_factory=list)


class ConceptRecord(_Strict):
    """One entry in overall_model.concepts."""

    value: Any = None
    evidence: List[Evidence] = Field(default_factory=list)
    search_log: List[SearchLogEntry] = Field(default_factory=list)
    confidence: Optional[float] = None
    evidence_quality: EvidenceQuality


SummarySource = Literal["template", "llm_synthesised"]


class OverallModelNode(_Strict):
    present: bool
    evidence_quality: EvidenceQuality
    summary: Optional[str] = None
    summary_source: SummarySource
    concepts: Dict[str, ConceptRecord] = Field(default_factory=dict)
    applied_heuristics: List[str] = Field(default_factory=list)
    confidence: Optional[float] = None


ConceptStatus = Literal["found", "partial", "inferred", "absent"]


class OntologyClassificationNode(_Strict):
    """Generic shape for inputs / calculations / results agents."""

    present: bool
    evidence_quality: EvidenceQuality
    summary: Optional[str] = None
    summary_source: SummarySource
    candidate_tables: List[CandidateTable] = Field(default_factory=list)
    parent_groups: List[ParentGroup] = Field(default_factory=list)
    expected_concepts_status: Dict[str, ConceptStatus] = Field(default_factory=dict)
    confidence: Optional[float] = None


class UnclassifiedTable(_Strict):
    table_id: str
    priority_score: float
    reason: str


class ProposedCorrection(_Strict):
    table_id: str
    issue: str
    proposed_correction: str
    confidence: Optional[float] = None
    evidence: str
    consumer: Literal["advisory_only"] = "advisory_only"


class LlmBudgetSnapshot(_Strict):
    seed_calls_used: int
    seed_calls_max: int
    enrichment_calls_used: int


class SummaryModel(_Strict):
    """Top-level summary_model_v{N}.json."""

    model_id: str
    ontology_id: str
    ontology_version: str
    schema_version: Literal["v0"] = "v0"
    seed_timestamp: str
    last_enriched_timestamp: Optional[str] = None
    workbook_file_hash: str
    overall_model: OverallModelNode
    inputs: OntologyClassificationNode
    calculations: OntologyClassificationNode
    results: OntologyClassificationNode
    unclassified_tables: List[UnclassifiedTable] = Field(default_factory=list)
    proposed_table_corrections: List[ProposedCorrection] = Field(default_factory=list)
    llm_budget: LlmBudgetSnapshot
    run_notes: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Per-agent record types (plan §4 — per-agent)
# ---------------------------------------------------------------------------

AgentStatus = Literal["complete", "partial", "failed"]


class AgentOutputRecord(_Strict):
    status: AgentStatus
    evidence_quality: EvidenceQuality
    summary: Optional[str] = None
    summary_source: SummarySource
    concepts_resolved: Optional[int] = None
    concepts_absent: Optional[int] = None
    tables_classified: Optional[int] = None
    node_type_overrides_proposed: int = 0
    expected_concepts_found: List[str] = Field(default_factory=list)
    expected_concepts_absent: List[str] = Field(default_factory=list)
    applied_heuristics: List[str] = Field(default_factory=list)
    tools_called: List[str] = Field(default_factory=list)
    llm_calls_used: int = 0
    elapsed_seconds: float = 0.0
    key_decisions: List[str] = Field(default_factory=list)
    human_readable_output: str = ""


class AgentOutputsFile(_Strict):
    model_id: str
    ontology_id: str
    run_timestamp: str
    agents: Dict[NodeName, AgentOutputRecord] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Per-table tag types (plan §4 — per-table tag)
# ---------------------------------------------------------------------------

class TableTagRecord(_Strict):
    table_id: str
    sheet: str
    title: Optional[str] = None
    upstream_node_type: UpstreamNodeType
    ontology_role: OntologyRoleAny
    ontology_subtype: Optional[str] = None
    node_type_override_proposed: bool = False
    in_top_candidates: bool
    confidence: Optional[float] = None
    evidence_quality: Optional[EvidenceQuality] = None
    agent_owner: Optional[NodeName] = None
    applied_heuristics: List[str] = Field(default_factory=list)
    flags: List[str] = Field(default_factory=list)


class TagSummary(_Strict):
    total_tables: int
    classified_input: int
    classified_calculation: int
    classified_result: int
    classified_overall_evidence: int
    unclassified: int
    out_of_scope_for_pass_1: int = 0  # v1.A — DAG-only filter result
    node_type_overrides_proposed: int


class TableTagsFile(_Strict):
    model_id: str
    ontology_id: str
    run_timestamp: str
    tables: List[TableTagRecord] = Field(default_factory=list)
    tag_summary: TagSummary


# ---------------------------------------------------------------------------
# Trace event types (plan §4 — trace events; spec §12.10)
#
# Discriminated by the `event` field. v0 keeps these as separate models so
# the writer can serialise a heterogeneous list and a future loader can
# discriminate. Each model carries `ts: str` (ISO-8601) and `event: str`.
# ---------------------------------------------------------------------------

class _TraceBase(_Strict):
    ts: str
    event: str

    @staticmethod
    def now_ts() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
            "+00:00", "Z"
        )


class AgentStart(_TraceBase):
    event: Literal["agent_start"] = "agent_start"
    agent: NodeName
    files_loaded: List[str]


class PlannerContextBuilt(_TraceBase):
    event: Literal["planner_context_built"] = "planner_context_built"
    agent: NodeName
    context_size_chars: int
    node_index_summary_size_chars: int


class LlmCall(_TraceBase):
    event: Literal["llm_call"] = "llm_call"
    agent: NodeName
    phase: Literal[
        "planner",
        "concept_synthesis",
        "defect_procedure",
        "summary_synthesis",
        # v1 Pass 2 phases:
        "extraction",
        "synthesis",
        "completion_criteria",
        "narration",
        # v1.I Pass 2 ReAct phase (one ReAct turn = one LLM call):
        "react",
    ]
    prompt: str
    prompt_size_chars: int
    response: str
    response_size_chars: int
    tokens_used: int
    elapsed_ms: int


class PlanProduced(_TraceBase):
    event: Literal["plan_produced"] = "plan_produced"
    agent: NodeName
    plan_steps: int
    plan: List[Dict[str, Any]]


class ToolCall(_TraceBase):
    event: Literal["tool_call"] = "tool_call"
    agent: NodeName
    tool: str
    args: Dict[str, Any]
    result_summary: Union[str, Dict[str, Any]]
    elapsed_ms: int
    concept_in_focus: Optional[str] = None


class HeuristicTriggered(_TraceBase):
    event: Literal["heuristic_triggered"] = "heuristic_triggered"
    agent: NodeName
    heuristic_id: str
    concept: str
    trigger_state: str


class HeuristicApplied(_TraceBase):
    event: Literal["heuristic_applied"] = "heuristic_applied"
    agent: NodeName
    heuristic_id: str
    outcome: str
    value: Optional[Any] = None


class ConceptRecorded(_TraceBase):
    event: Literal["concept_recorded"] = "concept_recorded"
    agent: NodeName
    concept: str
    value: Optional[Any] = None
    evidence_quality: EvidenceQuality
    confidence: Optional[float] = None


class ConceptAbsent(_TraceBase):
    event: Literal["concept_absent"] = "concept_absent"
    agent: NodeName
    concept: str
    search_log_size: int


class TableClassified(_TraceBase):
    event: Literal["table_classified"] = "table_classified"
    agent: NodeName
    table_id: str
    ontology_role: OntologyRoleClassified
    ontology_subtype: Optional[str] = None
    confidence: Optional[float] = None


class NodeTypeOverride(_TraceBase):
    event: Literal["node_type_override"] = "node_type_override"
    agent: NodeName
    table_id: str
    from_type: str = Field(alias="from")
    to_type: str = Field(alias="to")
    reason: str

    model_config = ConfigDict(populate_by_name=True, extra="forbid")


class DefectProcedureInvoked(_TraceBase):
    event: Literal["defect_procedure_invoked"] = "defect_procedure_invoked"
    agent: NodeName
    table_id: str
    defect_type: Literal["title_missing", "header_missing", "rowname_missing", "multi_core_detected"]
    procedure: Literal["10.1", "10.2", "10.3", "10.4"]


class BudgetCheck(_TraceBase):
    event: Literal["budget_check"] = "budget_check"
    phase: Literal["seed", "enrichment", "pass2"]
    calls_used: int
    calls_max: int


class BudgetExhausted(_TraceBase):
    event: Literal["budget_exhausted"] = "budget_exhausted"
    phase: Literal["seed", "enrichment", "pass2"]
    agent: Optional[NodeName] = None
    skipped_concept: Optional[str] = None
    skipped_table_id: Optional[str] = None


class AgentComplete(_TraceBase):
    event: Literal["agent_complete"] = "agent_complete"
    agent: NodeName
    status: AgentStatus
    elapsed_seconds: float


class RunStart(_TraceBase):
    event: Literal["run_start"] = "run_start"
    run_id: str
    ontology_id: str
    phase: Literal["seed", "enrichment"]


class RunComplete(_TraceBase):
    event: Literal["run_complete"] = "run_complete"
    status: AgentStatus
    agents_run: int
    total_elapsed_seconds: float
    total_llm_calls: int


class TraceError(_TraceBase):
    event: Literal["error"] = "error"
    agent: Optional[NodeName] = None
    table_id: Optional[str] = None
    error_type: str
    message: str


class DefectProcedureSkipped(_TraceBase):
    """REVISION 1 first-class event. Emitted by every defect stub
    (spec §10) so the §13.6 dashboard can quantify defect frequency
    in real workbooks for sizing the future xl_table_repair."""

    event: Literal["defect_procedure_skipped"] = "defect_procedure_skipped"
    agent: NodeName
    table_id: str
    flag_type: Literal[
        "title_missing", "header_missing", "rowname_missing", "multi_core_detected"
    ]
    would_have_been_procedure_id: Literal["10.1", "10.2", "10.3", "10.4"]
    agent_owner: NodeName


class WarningEvent(_TraceBase):
    """Non-fatal warning surfaced during the run. The pack loader
    populates ``OntologyPack.warnings`` for things like unknown extra
    files; the runner converts each into a WarningEvent so the UI's
    Inspect-run view (and a future Warnings filter) can display them
    next to errors. Plan §5.7 / §13.2."""

    event: Literal["warning"] = "warning"
    category: str  # e.g. "unknown_pack_file"
    message: str
    file_path: Optional[str] = None
    agent: Optional[NodeName] = None


# ---------------------------------------------------------------------------
# Build-error log type (plan §4 — error log)
# ---------------------------------------------------------------------------

class BuildErrorRecord(_Strict):
    """One line in ontology_build_errors.jsonl."""

    ts: str
    agent: Optional[NodeName] = None
    table_id: Optional[str] = None
    error_type: str
    message: str
    partial_state: Optional[Dict[str, Any]] = None


# ---------------------------------------------------------------------------
# Override-log type (REVISION 3 — plan §4)
# ---------------------------------------------------------------------------

class OverrideEvidence(_Strict):
    """One evidence row inside a NodeTypeOverrideRecord."""

    signal: str  # e.g. "lexicon_match_whole_table", "structural_sanity_low_formula_high_fanout"
    value: Any
    matched_term: Optional[str] = None


class NodeTypeOverrideRecord(_Strict):
    """One line in node_type_override_log.jsonl (spec §12.11).

    Captures finalize/ontology disagreements about node_type. Each
    line is a labelled training example for a future finalize ML
    classifier and a row in the UI's "Upstream label disagreements"
    view (spec §13.2).
    """

    ts: str
    table_id: str
    sheet: str
    title: Optional[str] = None
    upstream_label: UpstreamNodeType
    ontology_label: Literal["input", "calculation", "result", "overall_evidence"]
    ontology_subtype: str
    evidence: List[OverrideEvidence] = Field(default_factory=list)
    confidence: Optional[float] = None
    applied_heuristic: Optional[str] = None


# ---------------------------------------------------------------------------
# v1 (Pass 2) — comprehension layer artifacts (spec_xl_ontology_v1.md §11)
# ---------------------------------------------------------------------------

Pass2StageName = Literal[
    "overall", "inputs", "results", "calculations", "pre_overall",
]
CompletionStatus = Literal[
    "complete", "partial", "iteration_cap_reached", "budget_exhausted", "absent"
]


class Pass2EvidenceItem(_Strict):
    """A single evidence pointer inside a Pass-2 finding."""

    sheet: Optional[str] = None
    cell: Optional[str] = None
    table_id: Optional[str] = None
    snippet: Optional[str] = None
    matched_term: Optional[str] = None


class CompletionCriterion(_Strict):
    """One required-items entry from the LLM-derived criteria step (spec §5.1).

    Each criterion has a free-form ``rationale`` and a ``source_signal``
    pointing back to the upstream context that motivated it (e.g.
    "overall_context.intervention + overall_context.comparators").
    """

    required_items: List[str] = Field(default_factory=list)
    rationale: str = ""
    source_signal: str = ""


class StageFinding(_Strict):
    """Per-concept rollup of what the loop discovered (spec §8.1)."""

    tables_found: List[str] = Field(default_factory=list)
    items_satisfied: List[str] = Field(default_factory=list)
    items_missing: List[str] = Field(default_factory=list)
    notes: str = ""


class Pass2OverrideProposed(_Strict):
    """Pass-2 LLM-driven override (spec §7) — wraps the existing
    NodeTypeOverrideRecord with v1-specific signal labelling."""

    table_id: str
    new_role: Literal["input", "calculation", "result"]
    new_subtype: Optional[str] = None
    rationale: str = ""


class StageOutputBase(_Strict):
    """Shared shape across Pass-2 stages (spec §11.3)."""

    stage: Pass2StageName
    completion_status: CompletionStatus
    iterations_run: int = 0
    llm_calls_used: int = 0
    elapsed_seconds: float = 0.0
    prose: str = ""
    overrides_proposed: List[Pass2OverrideProposed] = Field(default_factory=list)
    applied_heuristics: List[str] = Field(default_factory=list)
    # D.1 — post-narration evidence audit.  ``None`` when the stage
    # didn't run a narrator (e.g. pre_overall).  When present, carries:
    #   {n_sentences, n_claim_sentences, n_claim_with_citation,
    #    claim_sentences_without_citation: List[str],
    #    all_claims_cited: bool}
    evidence_check: Optional[Dict[str, Any]] = None


class Stage21Output(StageOutputBase):
    """Stage 2.1 (overall) — fixed criteria, prose narrative + concept map.

    The goal-concepts list (intervention, comparator, population, time
    horizon, perspective, indication, country, model structure, key
    assumptions) is populated as a flat dict keyed by concept name.
    """

    stage: Literal["overall"] = "overall"
    concepts: Dict[str, Any] = Field(default_factory=dict)
    overview_sheets_read: List[str] = Field(default_factory=list)


class Stage22Output(StageOutputBase):
    """Stage 2.2 (inputs) — derived criteria + per-concept findings."""

    stage: Literal["inputs"] = "inputs"
    completion_criteria: Dict[str, CompletionCriterion] = Field(default_factory=dict)
    findings: Dict[str, StageFinding] = Field(default_factory=dict)


class Stage23Output(StageOutputBase):
    """Stage 2.3 (results) — derived criteria + cost-component findings."""

    stage: Literal["results"] = "results"
    completion_criteria: Dict[str, CompletionCriterion] = Field(default_factory=dict)
    findings: Dict[str, StageFinding] = Field(default_factory=dict)


class Stage24Output(StageOutputBase):
    """Stage 2.4 (calculations) — engine-table identification."""

    stage: Literal["calculations"] = "calculations"
    completion_criteria: Dict[str, CompletionCriterion] = Field(default_factory=dict)
    findings: Dict[str, StageFinding] = Field(default_factory=dict)
    important_calc_tables: List[str] = Field(default_factory=list)


class ModelUnderstandingFile(_Strict):
    """``model_understanding.json`` (spec §11.3 shape)."""

    schema_version: Literal["v1"] = "v1"
    model_id: str
    ontology_id: str
    v1_run_timestamp: str
    v1_pack_overview_text_hash: str
    summary_prose: str = ""
    stages: Dict[Pass2StageName, Any] = Field(default_factory=dict)


CrossAgentFindingType = Literal[
    "result_component_without_input",
    "input_without_downstream_use",
    "criterion_unmet",
    "stage_failed",
]


class CrossAgentFinding(_Strict):
    """One line in ``cross_agent_findings.jsonl`` (spec §11.4)."""

    ts: str
    type: CrossAgentFindingType
    stage_origin: Pass2StageName
    details: Dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# v1 (Pass 2) — trace event types (appended to ontology_trace.jsonl)
# ---------------------------------------------------------------------------

class Pass2StageStart(_TraceBase):
    """Stage 2.x began."""

    event: Literal["pass2_stage_start"] = "pass2_stage_start"
    stage: Pass2StageName


class Pass2StageComplete(_TraceBase):
    """Stage 2.x finished (cleanly or with a recorded gap)."""

    event: Literal["pass2_stage_complete"] = "pass2_stage_complete"
    stage: Pass2StageName
    completion_status: CompletionStatus
    iterations_run: int
    llm_calls_used: int
    elapsed_seconds: float


class CompletionCriteriaDerived(_TraceBase):
    """LLM-derived completion criteria written for traceability (spec §5.2)."""

    event: Literal["completion_criteria_derived"] = "completion_criteria_derived"
    stage: Pass2StageName
    criteria: Dict[str, CompletionCriterion] = Field(default_factory=dict)


class Pass2ToolCall(_TraceBase):
    """One tool invocation by Pass-2's executor.

    Distinct from any v0 ``tool_call`` event so the UI can filter Pass-2
    activity separately if/when v0 introduces its own.
    """

    event: Literal["pass2_tool_call"] = "pass2_tool_call"
    stage: Pass2StageName
    iteration: int
    tool: str
    args_summary: Dict[str, Any] = Field(default_factory=dict)
    result_summary: Optional[str] = None
    # Full JSON-serialised tool result (capped — see runner).  Useful
    # for debugging the agent's view: the LLM only sees the 1-line
    # summary in ``result_summary`` / ``observation_text``, but the
    # viewer can render the full payload so the user can see exactly
    # what the tool returned.  None on error / unknown-tool / duplicate
    # paths.
    result_full: Optional[str] = None


class Pass2ToolCallFailed(_TraceBase):
    """Tool name unknown or args invalid — emitted instead of crashing."""

    event: Literal["pass2_tool_call_failed"] = "pass2_tool_call_failed"
    stage: Pass2StageName
    iteration: int
    tool: str
    error_type: str
    message: str


class IterationCapReached(_TraceBase):
    """Stage exhausted its iteration budget without meeting all criteria."""

    event: Literal["iteration_cap_reached"] = "iteration_cap_reached"
    stage: Pass2StageName
    iterations: int
    items_missing_count: int


# v1.I — ReAct loop trace events.  Each iteration of a Pass-2 ReAct
# stage emits the trio (thought → action → observation), or a final
# stop event.  Lets the orchestrator render the agent's reasoning as
# a per-stage timeline.

class Pass2ReactThought(_TraceBase):
    """Free-text reasoning the LLM emitted before deciding an action."""

    event: Literal["pass2_react_thought"] = "pass2_react_thought"
    stage: Pass2StageName
    iteration: int
    thought: str


class Pass2ReactAction(_TraceBase):
    """The action the LLM chose this iteration (tool + args + rationale)."""

    event: Literal["pass2_react_action"] = "pass2_react_action"
    stage: Pass2StageName
    iteration: int
    tool: str
    args_summary: Dict[str, Any] = Field(default_factory=dict)
    rationale: str = ""


class Pass2ReactObservation(_TraceBase):
    """One-line summary of what the tool returned, fed back into the
    next iteration's prompt as the agent's observation.

    ``result_full`` carries the full JSON-serialised tool result
    (capped — see runner for the size cap).  The agent only sees
    ``observation_text`` in its ReAct prompt; ``result_full`` is for
    the orchestrator viewer / debug trace so the user can audit what
    the tool actually returned.
    """

    event: Literal["pass2_react_observation"] = "pass2_react_observation"
    stage: Pass2StageName
    iteration: int
    tool: str
    observation_text: str
    result_full: Optional[str] = None


class Pass2ReactStop(_TraceBase):
    """LLM-driven termination — agent decided it has enough comprehension."""

    event: Literal["pass2_react_stop"] = "pass2_react_stop"
    stage: Pass2StageName
    iteration: int
    thought: str = ""
    summary: str = ""
