# xl_ask — Approach and Architecture

## 1. Purpose

xl_ask is a horizontal Q&A tool that sits over a parsed BIM (budget impact model) workbook and answers natural-language questions with grounded, cited, table-anchored responses. Every answer ships with the prose, the cell-level citations, the table-level highlights for the main Excel view, and the subgraph for the side panel.

The system is designed for HEOR review use cases where reproducibility, auditability, and source-traceability are not optional. xl_ask is read-only against the workbook and against all upstream artifacts.

## 2. The Three Layers of Truth

Every xl_ask answer is constructed from three layers, each with a specific role and a clear precedence:

**Layer 1 — Ontology Tree (semantic, validated).** Pass 2's curated interpretation of the model: what concepts exist, what they mean, how they relate, which tables represent them. Partial coverage by design — captures main interpreted structure, not every table. Includes prose descriptions written by Pass 2 for human-readable concept explanation. **Authoritative for meaning.**

**Layer 2 — Table Map and DAG (structural, mechanical).** `final_table_map.json` is the exhaustive list of every table in the workbook with bounding boxes. `dag.json` + `node_index.json` is every formula relationship. These are mechanically extracted from the workbook, complete and reliable for what they capture, but unable to express meaning beyond `node_type` and `subcategory` tags. **Authoritative for structure and computation.**

**Layer 3 — Raw Workbook (substrate, ground truth).** The original `.xlsx` file, accessed read-only via openpyxl. Used as fallback when extraction layers (1 or 2) are incomplete or wrong. **Authoritative for source truth.**

The architectural rule: **reason from validated semantics first, fall through to mechanical layers when needed, verify against raw cells when extraction is suspect.**

## 3. The Probe-Then-Plan Architecture

xl_ask uses a five-stage runtime per question. Stage 0 is the addition that makes ontology the planner's primary context.

```
Stage 0 — Ontology Probe (deterministic, no LLM)
   Runtime extracts concept candidates from the question and queries the
   ontology tree. Produces OntologyContext: a focused slice of the tree
   containing matched concepts, branch coverage, and a question-shape hint.

Stage 1 — Plan Generation (one LLM inference)
   LLM receives: question + OntologyContext + system prompt + Tier 1 tool
   catalog + 12 worked examples. Produces structured QueryPlan in one
   forward pass. Plan declares: question class, target concepts, evidence
   needed, tool choice, composition strategy, stop condition, and whether
   ontology alone suffices.

Stage 2 — Execute (deterministic Tier 1 pipeline OR direct ontology
   answer)
   If plan.tier1_tool == "answer_directly_from_ontology", runtime
   constructs envelope from matched ontology nodes' descriptions. No
   Tier 1 pipeline runs.
   Otherwise, runtime executes the chosen Tier 1 tool. Tier 1 composes
   Tier 2 primitives internally. Returns AnswerEnvelope.

Stage 3 — Observe and Decide (one LLM inference if escalation needed)
   LLM observes envelope. Applies decision table from system prompt:
     - confidence:high → proceed to compose
     - confidence:low + extraction warning → call verify_against_workbook
     - confidence:medium + multiple candidates → ask user to disambiguate
     - composition:sequential_drilldown not yet complete → call next tool
     - budget exhausted → mark partial and proceed

Stage 4 — Compose (one LLM inference)
   LLM writes answer_text from accumulated envelopes (and ontology
   descriptions, if used). Picks inline citations. Quotes ontology
   descriptions verbatim with citation when answering concept questions.

Stage 5 — Validate (deterministic, always-on)
   Runtime calls validate_grounding against the QueryPlan contract:
     - every claim in answer_text has a matching citation
     - table_refs is non-empty
     - all referenced node_ids/edge_ids/concept_ids exist
     - required_evidence declared in plan was actually gathered
     - composition matches what was called
     - stop_condition was met or budget exhausted
   If validation fails: one retry with explicit feedback. Second failure
   terminates with status='failed'.
```

Per-question budget: max 4 LLM hops, max 8 Tier 1 calls, max 30s wall clock.

## 4. The Two-Tier Tool Architecture

This is xl_ask's central design rule. **Thick verbs over thin nouns.**

**Tier 1 — Answer Tools (LLM-facing).** Each tool maps to one question class. Each is *answer-shaped*: it runs a deterministic pipeline of Tier 2 primitives internally and returns a structured AnswerEnvelope. Every Tier 1 tool is a pure function of `(DAG, table_map, ontology_tree, model_overview, workbook) → AnswerEnvelope`. Two runs of the same tool against the same artifacts produce byte-identical envelopes — only prose may vary.

The Tier 1 catalog (final):

| Tool | Question class | Role |
|---|---|---|
| `answer_directly_from_ontology` | overview, simple concept | Constructs envelope from ontology descriptions; no Tier 1 pipeline |
| `answer_overview` | overview | Full model framing, anchoring claims |
| `answer_what_inputs` | what (broad) | Enumerates input nodes from node index, enriched with ontology |
| `answer_what_subcategory` | what (specific) | Filters by subcategory + ontology branch |
| `explain_concept` | concept | Concept resolution + prose + linked tables + sources extraction |
| `explain_methodology` | methodology | Cross-branch sweep with formula AST analysis |
| `answer_where_provenance` | where | Upstream walk to input leaves |
| `explain_calculation` | how | Formula + direct precedents with header context |
| `attribute_result_variance` | why | Column-comparison + variance attribution |
| `answer_evidence_gap` | gap | Three-layer absence search (ontology → DAG → raw) |
| `compare_concepts` | compare | Symmetric structural diff |
| `audit_invariant` | audit | Finite enumerated rule checkers |
| `verify_against_workbook` | escalation (any class) | Raw-cell reconciliation against extraction |
| `build_ask_response_artifacts` | runtime | Pure: dedupe table_refs, build subgraph |
| `validate_grounding` | runtime | Pure: contract enforcement |

**Tier 2 — Primitives (internal).** Discovery, retrieval, traversal, comparison, ontology, raw access. Never exposed to the LLM. Used by Tier 1 tools and by debug/eval interfaces. Examples: `list_inputs`, `get_node`, `upstream`, `find_ontology_nodes`, `read_around_node`, `compare_columns`, `attribute_variance`, `subgraph_for_query`.

**Why this matters.** Graph primitives stay deterministic. Variance attribution, source extraction, methodology synthesis — these are computed, not improvised. The LLM classifies and dispatches; it does not stitch graph walks. This is what makes the system audit-defensible and byte-stable.

## 5. The QueryPlan as Active Contract

The QueryPlan is not a logging artifact. It is the LLM's structured commitment to a routing decision, validated structurally before execution and validated semantically after.

The plan declares, in one inference:
- **What is being asked**: intent, question_class, target_concepts, qualifier
- **What evidence is needed**: ontology_prose, table_metadata, formulas, dag_traversal, formatting, raw_cells (declared upfront)
- **What ontology found**: matched_concepts, branches_touched, ontology_sufficient flag
- **How to answer it**: tier1_tool, tool_args, composition, stop_condition
- **Self-assessment**: confidence_in_route, reason_for_route

Validation enforces the plan as contract:
- Did the LLM call the tool it declared?
- Did the evidence declared as required appear in the citations?
- Was the composition strategy honored?
- Was the stop condition met or was the budget exhausted?

Plan-execution mismatches are validation failures. The LLM cannot promise one thing and deliver another without being caught.

## 6. The AnswerEnvelope Contract

Every Tier 1 tool returns the same shape:

```
AnswerEnvelope {
  summary: string                   // one-line deterministic summary
  structured_result: object         // class-specific
  citations_candidates: Citation[]  // every cell/edge/concept the pipeline touched
  table_refs_candidates: TableRef[] // every table touched, pre-classified by role
  confidence: 'high' | 'medium' | 'low'
  warnings: string[]                // specific actionable signals
  fallback_suggestion?: string      // optional pointer to next tool when low-conf
  trace: ToolCall[]                 // internal Tier 2 calls, fully audited
}
```

The LLM's job in Stage 4 is to wrap the envelope in prose and pick which citations to surface inline. It does not re-derive structured_result. It does not assemble table_refs (runtime does, in Stage 5 prep via build_ask_response_artifacts).

`fallback_suggestion` is the deterministic escalation hint: when a Tier 1 tool returns low-confidence, it names which tool the LLM should consider calling next. This makes sequential drilldown deterministic rather than improvised.

## 7. The AskResponse — What the User Gets

Every question produces one AskResponse:

```
AskResponse {
  question: string
  question_class: enum
  answer_text: string               // markdown, the prose
  citations: Citation[]             // cell/edge/concept level — drives inline footnotes
  table_refs: TableRef[]            // table level — drives main-window highlighting (≥1 required)
  subgraph: Subgraph                // graph level — drives Ask-window side panel
  query_plan: QueryPlan             // the plan that produced this answer
  tool_trace: ToolCall[]            // ordered Tier 1 calls + Tier 2 reconstruction
  warnings: string[]
  status: 'ok' | 'partial' | 'failed'
  error?: string
}
```

Three reference fields with distinct purposes:
- `citations` (cell/edge/concept) drives inline footnote markers in answer_text.
- `table_refs` (table-level, **required ≥1**) drives the main Excel window's highlight boxes.
- `subgraph` (graph-level) drives the Ask window's side panel render.

Highlight roles on table_refs: `primary` (the answer is *about* this table), `supporting` (contributed evidence, e.g. upstream inputs), `context` (anchoring tables for model-level claims).

## 8. The Ontology-First Routing Principle

The probe-then-plan architecture operationalizes a single principle: **the ontology tree is the validated semantic layer, and the planner reasons from validated semantics first.**

This changes routing in three concrete ways:

**a) Ontology-sufficient questions skip Tier 1 entirely.** Overview questions, simple concept descriptions, dashboard/defaults explanations — when Pass 2's prose covers the answer fully, runtime constructs the envelope from ontology descriptions directly. No DAG traversal, no formula fetch. Single LLM hop, single ontology fetch, done.

**b) Concept questions arrive at Tier 1 with concepts already resolved.** When the probe matches `inputs.market_share` cleanly, the planner passes `concept_id_hint: "inputs.market_share"` to `explain_concept`. The Tier 1 tool skips its own resolution step. Mechanical layers extract footnotes and headers from a pre-identified table set.

**c) Mechanical questions still go to mechanical tools.** Enumeration questions ("list all inputs") cannot be answered from ontology alone because ontology is partial by design. These route to `answer_what_inputs` against the node index. Ontology enriches the result (concept labels, descriptions for each input) but doesn't gate it.

## 9. The Validation Loop — Always On

Stage 5 runs on every answer. No exceptions, no gating on confidence or status. Validation checks:

1. **Citation coverage.** Every claim-bearing sentence in `answer_text` has at least one matching `cited_as` substring in `citations`.
2. **Reference integrity.** `table_refs` is non-empty. Every node_id / edge_id / concept_id exists in loaded artifacts.
3. **Plan contract compliance.** `required_evidence` flags map to actual citations. `composition` matches what was called. `stop_condition` was met or budget exhausted.
4. **Grounding honesty.** Ontology `description` quotes are attributed to ontology nodes. Cell values are attributed to their coordinates. No invented row names or values.

One retry on first failure with explicit feedback ("claim X has no matching citation; please add or remove"). Second failure terminates with `status='failed'` and enumerated violations. The LLM cannot bypass validation.

## 10. Failure Modes — Designed In

xl_ask's behavior when things go wrong is part of the design:

| Mode | What happens |
|---|---|
| Ontology probe finds nothing | Plan proceeds without ontology hints; routes to mechanical tools |
| Concept absent from workbook entirely | `answer_evidence_gap` confirms across all three layers; status='ok', answer says "absent" |
| Extraction sparse (e.g. missing footnotes) | Tier 1 returns confidence:low + warning; LLM calls `verify_against_workbook`; raw cells reconcile |
| Multiple ambiguous concept matches | Tier 1 returns confidence:medium + candidates; LLM asks user to disambiguate; status='partial' |
| Unresolved DAG edge mid-chain | `__UNRESOLVED__` warning carried through; status='partial' |
| Cross-branch methodology, only one branch matched | `explain_methodology` warning; LLM may call `answer_what_subcategory` on missing branch |
| Validation fails twice | status='failed'; violations enumerated in error |
| Empty table_refs | Always status='failed' — bug, surface it |
| Out-of-scope question (what-if, modify) | status='failed'; error names scope |

## 11. What xl_ask Does NOT Do

Out of scope for v1:
- What-if / sensitivity analysis (Type 3 questions). Requires recalc engine.
- Workbook mutation (Type 4 questions). Requires xl_write layer.
- Generation of `model_overview.json` or `ontology_tree.json`. Both produced upstream by Pass 2 / ontology repo.
- Multi-workbook reasoning. One conversation = one workbook.
- Vector embeddings or semantic search. Lexical match + alias resolution only.
- Streaming partial answers. Atomic per turn.
- External literature / HTA / guideline retrieval. Workbook-only citations.
- Persistent conversation memory across sessions.
- Exposing Tier 2 primitives to the LLM.

## 12. Why This Approach

The architecture rests on three claims about HEOR Q&A:

1. **The space of meaningful questions is finite and classifiable.** Eight question classes cover the practical universe. Each maps to one thick deterministic tool. Generic verbs (`investigate_concept`, `search_concept_evidence`) push reasoning into the LLM and break replayability.

2. **Validated semantics outranks mechanical extraction.** When Pass 2 has interpreted a concept, that interpretation is more reliable than re-deriving meaning from raw structure. The probe-then-plan pattern operationalizes this.

3. **Determinism is not a constraint, it's a feature.** Byte-stability of structured results, deterministic escalation paths, contract-enforced validation — these are the properties that make xl_ask defensible in payer review and replayable in audit. The LLM provides language; the system provides truth.

The whole architecture is a series of moves to keep the LLM thin and the deterministic layer thick. Every design decision can be checked against this principle.