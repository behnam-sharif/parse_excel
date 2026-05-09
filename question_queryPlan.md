# xl_ask — Question Taxonomy and QueryPlan Examples

## 1. Purpose

This document defines the universe of questions xl_ask answers, the structured plan that routes each question to the right tool, and 12 worked examples that anchor the planner's behavior. Every example is also a teaching artifact for the LLM — these are the in-prompt examples that calibrate routing.

## 2. The Three Dimensions of a Question

Every question xl_ask receives is characterized along three independent dimensions. Together they determine the QueryPlan.

### Dimension 1 — Question Class (9 values)

The structural shape of the question. Determines which Tier 1 tool is chosen.

| Class | What the user is asking | Tier 1 tool |
|---|---|---|
| `overview` | What is this model about, overall? | `answer_overview` or `answer_directly_from_ontology` |
| `what` | Enumerate something (list inputs, list outputs, list comparators) | `answer_what_inputs` or `answer_what_subcategory` |
| `concept` | Explain or source one specific concept | `explain_concept` |
| `where` | Trace upstream to find inputs of a target | `answer_where_provenance` |
| `how` | Show me the formula and direct precedents | `explain_calculation` |
| `methodology` | Explain how something is modeled across multiple branches | `explain_methodology` |
| `why` | Attribute a difference between cells or columns | `attribute_result_variance` |
| `gap` | Find or confirm absence of something expected | `answer_evidence_gap` |
| `compare` | Symmetric diff between two concepts/drugs/scenarios | `compare_concepts` |
| `audit` | Run a rule check (consistency, formatting, structure) | `audit_invariant` |

### Dimension 2 — Qualifier Type (5 values, including null)

A qualifier modifies the target concept. It dictates which patterns the Tier 1 tool looks for during analysis.

| Qualifier type | Meaning | Example phrase |
|---|---|---|
| `null` | No qualifier; concept itself is the question | "How is market share modeled?" |
| `temporal` | Behavior across years / time | "...for future years other than incident" / "...over the time horizon" |
| `scenario` | Behavior under specific scenarios | "...under the high-uptake scenario" / "...when persistence is set to 1.0" |
| `subgroup` | Behavior for specific row/cohort | "...for treatment-experienced patients" / "...for the over-65 cohort" |
| `methodology` | Methodological aspect itself | "...as a Markov vs decision-tree" / "...using cohort vs individual-level" |

For HEOR BIMs, `temporal` and `subgroup` are the most common. `scenario` is medium-frequency. `methodology` (as a qualifier, distinct from question class) is rare.

### Dimension 3 — Composition Strategy (5 values)

How multiple Tier 1 calls combine when one tool isn't enough.

| Composition | When | Number of LLM-visible Tier 1 calls |
|---|---|---|
| `single_tool` | Most questions; one tool answers | 1 |
| `methodology_synthesis` | Cross-branch methodology questions | 1 (deterministic synthesis inside `explain_methodology`) |
| `gap_confirmation` | Absence questions; exhaust ontology + DAG + raw | 1 (deterministic three-layer search inside `answer_evidence_gap`) |
| `parallel_concepts` | Compare A vs B; resolve both before diff | 1 (inside `compare_concepts`) |
| `sequential_drilldown` | Cheap tool first, escalate if insufficient | 2 (e.g., `answer_overview` → `explain_methodology`) |

The first four are bounded inside one Tier 1 tool. Only `sequential_drilldown` produces multiple LLM-visible Tier 1 calls.

## 3. The QueryPlan Schema

Every question produces one structured plan, emitted by the LLM in a single inference call after consuming the OntologyContext from Stage 0.

```typescript
export interface QueryPlan {
  // ---- What the user wants ----
  intent: 'explain' | 'find' | 'enumerate' | 'audit' | 'compare' 
        | 'trace' | 'verify' | 'attribute';
  question_class: 'overview' | 'what' | 'concept' | 'where' | 'how' 
                | 'methodology' | 'why' | 'gap' | 'compare' | 'audit';
  
  // ---- What the question is about ----
  target_concepts: string[];           // primary subject(s)
  secondary_concepts: string[];        // related context concepts
  qualifier: string | null;            // the modifier phrase, verbatim
  qualifier_type: 'temporal' | 'scenario' | 'subgroup' | 'methodology' | null;
  investigation_type: 'reference' | 'method' | 'implementation' 
                    | 'impact' | 'formatting_rule' | 'exclusion_reason' 
                    | 'enumeration' | 'verification' | 'consistency';
  
  // ---- What the ontology probe found (Stage 0 output) ----
  ontology_grounding: {
    matched_concepts: string[];        // concept_ids the probe matched
    branches_touched: string[];        // ontology branches involved
    ontology_sufficient: boolean;      // can ontology alone answer this?
    reason: string;                    // one-line probe summary
  };
  
  // ---- What evidence the planner expects to need ----
  required_evidence: {
    ontology_prose: boolean;
    table_metadata: boolean;
    formulas: boolean;
    dag_traversal: boolean;
    formatting: boolean;
    raw_cells: boolean;
    external_context: boolean;         // always false in MVP
  };
  
  // ---- Which ontology branches are likely relevant ----
  expected_branches: string[];
  
  // ---- The tool choice ----
  tier1_tool: string;
  tool_args: Record<string, unknown>;
  
  // ---- How calls compose, when to stop ----
  composition: 'single_tool' | 'methodology_synthesis' 
             | 'gap_confirmation' | 'parallel_concepts' 
             | 'sequential_drilldown';
  stop_condition: 'envelope_complete' | 'all_branches_covered' 
                | 'gap_confirmed' | 'disambiguation_needed' 
                | 'budget_exhausted';
  
  // ---- Planner self-assessment ----
  confidence_in_route: 'high' | 'medium' | 'low';
  reason_for_route: string;            // one sentence, ≤ 200 chars
}
```

## 4. The Coverage Matrix — 12 In-Prompt Examples

The 12 examples span 9 of 10 class variants, all 4 qualifier types (plus null), and all 5 composition strategies. Imbalance is deliberate: most real questions are `single_tool`, so the example distribution reflects that.

| # | Question | Class | Qualifier | Composition |
|---|---|---|---|---|
| 1 | What does this model evaluate, in which population, country, and time horizon? | overview | null | single_tool |
| 2 | What are all the input parameters in this BIM? | what | null | single_tool |
| 3 | What are the references used for the market share inputs? | concept | null | single_tool |
| 4 | How is the eligible population defined? | concept | null | single_tool |
| 5 | Where does the unit drug cost come from? | where | null | single_tool |
| 6 | How is total annual drug cost calculated for each comparator? | how | null | single_tool |
| 7 | How is drug cost for future years (other than incident year) modeled? | methodology | temporal | methodology_synthesis |
| 8 | How is subsequent treatment cost modeled across the model? | methodology | null | methodology_synthesis |
| 9 | How does scenario switching propagate from dashboard to results? | methodology | scenario | methodology_synthesis |
| 10 | Why is the 2025 incremental budget impact higher than 2024? | why | temporal | single_tool |
| 11 | Why is Edoxaban not included as a comparator? | gap | null | gap_confirmation |
| 12 | Is this model incidence-based or prevalence-based? | overview | null | sequential_drilldown |

## 5. The 12 Worked QueryPlan Examples

### Example 1 — Overview, ontology-sufficient

**Q: "What does this model evaluate, in which population, country, and time horizon?"**

```json
{
  "intent": "explain",
  "question_class": "overview",
  "target_concepts": ["model purpose", "population", "country", "time horizon"],
  "secondary_concepts": ["intervention", "comparators"],
  "qualifier": null,
  "qualifier_type": null,
  "investigation_type": "method",
  "ontology_grounding": {
    "matched_concepts": ["overview", "overview.indication", 
                         "overview.population", "overview.country", 
                         "overview.time_horizon"],
    "branches_touched": ["overview"],
    "ontology_sufficient": true,
    "reason": "Overview branch fully populated with all requested aspects."
  },
  "required_evidence": {
    "ontology_prose": true, "table_metadata": false, "formulas": false,
    "dag_traversal": false, "formatting": false, "raw_cells": false,
    "external_context": false
  },
  "expected_branches": ["overview"],
  "tier1_tool": "answer_directly_from_ontology",
  "tool_args": {
    "concept_ids": ["overview", "overview.indication", "overview.population",
                    "overview.country", "overview.time_horizon",
                    "overview.intervention", "overview.comparators"]
  },
  "composition": "single_tool",
  "stop_condition": "envelope_complete",
  "confidence_in_route": "high",
  "reason_for_route": "All requested model framing aspects fully captured in ontology overview branch."
}
```

### Example 2 — What (broad enumeration)

**Q: "What are all the input parameters in this BIM?"**

```json
{
  "intent": "enumerate",
  "question_class": "what",
  "target_concepts": ["input parameters"],
  "secondary_concepts": [],
  "qualifier": null,
  "qualifier_type": null,
  "investigation_type": "enumeration",
  "ontology_grounding": {
    "matched_concepts": [],
    "branches_touched": ["inputs"],
    "ontology_sufficient": false,
    "reason": "Branch-level question; full enumeration requires node index."
  },
  "required_evidence": {
    "ontology_prose": false, "table_metadata": true, "formulas": false,
    "dag_traversal": false, "formatting": false, "raw_cells": false,
    "external_context": false
  },
  "expected_branches": ["inputs"],
  "tier1_tool": "answer_what_inputs",
  "tool_args": { "scope": null },
  "composition": "single_tool",
  "stop_condition": "envelope_complete",
  "confidence_in_route": "high",
  "reason_for_route": "Exhaustive enumeration; ontology enriches but cannot replace node-index sweep."
}
```

### Example 3 — Concept (sources/references)

**Q: "What are the references used for the market share inputs?"**

```json
{
  "intent": "explain",
  "question_class": "concept",
  "target_concepts": ["market share"],
  "secondary_concepts": [],
  "qualifier": null,
  "qualifier_type": null,
  "investigation_type": "reference",
  "ontology_grounding": {
    "matched_concepts": ["inputs.market_share"],
    "branches_touched": ["inputs"],
    "ontology_sufficient": false,
    "reason": "Concept resolved; references require footnote extraction beyond description."
  },
  "required_evidence": {
    "ontology_prose": true, "table_metadata": true, "formulas": false,
    "dag_traversal": false, "formatting": false, "raw_cells": true,
    "external_context": false
  },
  "expected_branches": ["inputs"],
  "tier1_tool": "explain_concept",
  "tool_args": {
    "concept_query": "market share",
    "concept_id_hint": "inputs.market_share",
    "focus": "sources"
  },
  "composition": "single_tool",
  "stop_condition": "envelope_complete",
  "confidence_in_route": "high",
  "reason_for_route": "Ontology pinpointed concept; explain_concept extracts sources from linked tables, raw fallback if footnotes sparse."
}
```

### Example 4 — Concept (implementation)

**Q: "How is the eligible population defined?"**

```json
{
  "intent": "explain",
  "question_class": "concept",
  "target_concepts": ["eligible population"],
  "secondary_concepts": ["population", "incidence", "prevalence"],
  "qualifier": null,
  "qualifier_type": null,
  "investigation_type": "implementation",
  "ontology_grounding": {
    "matched_concepts": ["calculations.eligible_population"],
    "branches_touched": ["calculations"],
    "ontology_sufficient": false,
    "reason": "Concept matched in calculations branch; implementation needs formula and precedents."
  },
  "required_evidence": {
    "ontology_prose": true, "table_metadata": true, "formulas": true,
    "dag_traversal": true, "formatting": false, "raw_cells": false,
    "external_context": false
  },
  "expected_branches": ["calculations", "inputs"],
  "tier1_tool": "explain_concept",
  "tool_args": {
    "concept_query": "eligible population",
    "concept_id_hint": "calculations.eligible_population",
    "focus": "implementation"
  },
  "composition": "single_tool",
  "stop_condition": "envelope_complete",
  "confidence_in_route": "high",
  "reason_for_route": "Implementation question; explain_concept fetches formula and direct precedents to show definition logic."
}
```

### Example 5 — Where (provenance)

**Q: "Where does the unit drug cost come from?"**

```json
{
  "intent": "trace",
  "question_class": "where",
  "target_concepts": ["unit drug cost"],
  "secondary_concepts": ["drug cost"],
  "qualifier": null,
  "qualifier_type": null,
  "investigation_type": "verification",
  "ontology_grounding": {
    "matched_concepts": ["inputs.drug_cost"],
    "branches_touched": ["inputs"],
    "ontology_sufficient": false,
    "reason": "Provenance question requires upstream walk to input leaves."
  },
  "required_evidence": {
    "ontology_prose": false, "table_metadata": true, "formulas": false,
    "dag_traversal": true, "formatting": false, "raw_cells": false,
    "external_context": false
  },
  "expected_branches": ["inputs"],
  "tier1_tool": "answer_where_provenance",
  "tool_args": {
    "target_query": "unit drug cost",
    "concept_id_hint": "inputs.drug_cost"
  },
  "composition": "single_tool",
  "stop_condition": "envelope_complete",
  "confidence_in_route": "high",
  "reason_for_route": "Provenance trace via DAG upstream walk to input leaves."
}
```

### Example 6 — How (calculation)

**Q: "How is total annual drug cost calculated for each comparator?"**

```json
{
  "intent": "explain",
  "question_class": "how",
  "target_concepts": ["total annual drug cost"],
  "secondary_concepts": ["comparator", "drug cost calculation"],
  "qualifier": null,
  "qualifier_type": null,
  "investigation_type": "method",
  "ontology_grounding": {
    "matched_concepts": ["calculations.drug_cost_calculation"],
    "branches_touched": ["calculations"],
    "ontology_sufficient": false,
    "reason": "Calculation question; needs formula text and direct precedents."
  },
  "required_evidence": {
    "ontology_prose": false, "table_metadata": true, "formulas": true,
    "dag_traversal": true, "formatting": false, "raw_cells": false,
    "external_context": false
  },
  "expected_branches": ["calculations", "inputs"],
  "tier1_tool": "explain_calculation",
  "tool_args": {
    "target_query": "total annual drug cost",
    "concept_id_hint": "calculations.drug_cost_calculation"
  },
  "composition": "single_tool",
  "stop_condition": "envelope_complete",
  "confidence_in_route": "high",
  "reason_for_route": "Direct calculation explanation; formula + precedents with header context."
}
```

### Example 7 — Methodology with temporal qualifier

**Q: "How is drug cost for future years (other than the incident year) modeled?"**

```json
{
  "intent": "explain",
  "question_class": "methodology",
  "target_concepts": ["drug cost"],
  "secondary_concepts": ["future years", "persistence", "extrapolation"],
  "qualifier": "future years other than incident year",
  "qualifier_type": "temporal",
  "investigation_type": "method",
  "ontology_grounding": {
    "matched_concepts": ["inputs.drug_cost", "calculations.drug_cost_calculation"],
    "branches_touched": ["inputs", "calculations"],
    "ontology_sufficient": false,
    "reason": "Cross-branch methodology with temporal qualifier; needs formula AST analysis."
  },
  "required_evidence": {
    "ontology_prose": true, "table_metadata": true, "formulas": true,
    "dag_traversal": true, "formatting": false, "raw_cells": false,
    "external_context": false
  },
  "expected_branches": ["inputs", "calculations"],
  "tier1_tool": "explain_methodology",
  "tool_args": {
    "concept_query": "drug cost",
    "concept_id_hints": ["inputs.drug_cost", "calculations.drug_cost_calculation"],
    "qualifier": "future years other than incident year",
    "qualifier_type": "temporal"
  },
  "composition": "methodology_synthesis",
  "stop_condition": "all_branches_covered",
  "confidence_in_route": "high",
  "reason_for_route": "Cross-branch methodology with temporal qualifier; explain_methodology runs formula AST scan for year-conditional logic."
}
```

### Example 8 — Methodology, no qualifier

**Q: "How is subsequent treatment cost modeled across the model?"**

```json
{
  "intent": "explain",
  "question_class": "methodology",
  "target_concepts": ["subsequent treatment cost"],
  "secondary_concepts": ["treatment pathway", "budget impact"],
  "qualifier": null,
  "qualifier_type": null,
  "investigation_type": "method",
  "ontology_grounding": {
    "matched_concepts": ["inputs.subsequent_treatment", 
                         "calculations.subsequent_treatment_cost"],
    "branches_touched": ["inputs", "calculations", "results"],
    "ontology_sufficient": false,
    "reason": "Cross-branch methodology; subsequent treatment exists as input parameters and calc logic feeding results."
  },
  "required_evidence": {
    "ontology_prose": true, "table_metadata": true, "formulas": true,
    "dag_traversal": true, "formatting": false, "raw_cells": false,
    "external_context": false
  },
  "expected_branches": ["inputs", "calculations", "results"],
  "tier1_tool": "explain_methodology",
  "tool_args": {
    "concept_query": "subsequent treatment",
    "concept_id_hints": ["inputs.subsequent_treatment", 
                         "calculations.subsequent_treatment_cost"],
    "qualifier": null,
    "qualifier_type": null
  },
  "composition": "methodology_synthesis",
  "stop_condition": "all_branches_covered",
  "confidence_in_route": "high",
  "reason_for_route": "Cross-branch methodology spanning inputs, calculations, and results; needs concept-level walk via governs/governed_by edges."
}
```

### Example 9 — Methodology with scenario qualifier

**Q: "How does scenario switching propagate from dashboard to results?"**

```json
{
  "intent": "explain",
  "question_class": "methodology",
  "target_concepts": ["scenario switching"],
  "secondary_concepts": ["dashboard", "results", "control sheet"],
  "qualifier": "from dashboard to results",
  "qualifier_type": "scenario",
  "investigation_type": "method",
  "ontology_grounding": {
    "matched_concepts": ["dashboard.scenario_selector", 
                         "results.scenario_results", 
                         "defaults.scenario_override_format"],
    "branches_touched": ["dashboard", "calculations", "results", "defaults"],
    "ontology_sufficient": false,
    "reason": "Multi-branch scenario propagation; needs DAG to confirm wiring from dashboard cells through calc to results."
  },
  "required_evidence": {
    "ontology_prose": true, "table_metadata": true, "formulas": true,
    "dag_traversal": true, "formatting": true, "raw_cells": false,
    "external_context": false
  },
  "expected_branches": ["dashboard", "calculations", "results", "defaults"],
  "tier1_tool": "explain_methodology",
  "tool_args": {
    "concept_query": "scenario switching",
    "concept_id_hints": ["dashboard.scenario_selector", 
                         "results.scenario_results"],
    "qualifier": "from dashboard to results",
    "qualifier_type": "scenario"
  },
  "composition": "methodology_synthesis",
  "stop_condition": "all_branches_covered",
  "confidence_in_route": "high",
  "reason_for_route": "Scenario propagation question spans dashboard, calc, and results; methodology synthesis traces concept chain plus DAG confirmation."
}
```

### Example 10 — Why (variance attribution) with temporal qualifier

**Q: "Why is the 2025 incremental budget impact higher than 2024?"**

```json
{
  "intent": "attribute",
  "question_class": "why",
  "target_concepts": ["incremental budget impact"],
  "secondary_concepts": ["2024", "2025"],
  "qualifier": "higher in 2025 than 2024",
  "qualifier_type": "temporal",
  "investigation_type": "impact",
  "ontology_grounding": {
    "matched_concepts": ["results.budget_impact_total"],
    "branches_touched": ["results"],
    "ontology_sufficient": false,
    "reason": "Variance question requires column comparison and upstream attribution; ontology only locates target."
  },
  "required_evidence": {
    "ontology_prose": false, "table_metadata": true, "formulas": true,
    "dag_traversal": true, "formatting": false, "raw_cells": false,
    "external_context": false
  },
  "expected_branches": ["results", "calculations", "inputs"],
  "tier1_tool": "attribute_result_variance",
  "tool_args": {
    "target_query": "incremental budget impact",
    "concept_id_hint": "results.budget_impact_total",
    "row_label": null,
    "col_label_a": "2024",
    "col_label_b": "2025"
  },
  "composition": "single_tool",
  "stop_condition": "envelope_complete",
  "confidence_in_route": "high",
  "reason_for_route": "Year-over-year variance attribution; deterministic compare_columns + upstream variance walk."
}
```

### Example 11 — Gap (absence)

**Q: "Why is Edoxaban not included as a comparator?"**

```json
{
  "intent": "find",
  "question_class": "gap",
  "target_concepts": ["Edoxaban"],
  "secondary_concepts": ["comparator"],
  "qualifier": null,
  "qualifier_type": null,
  "investigation_type": "exclusion_reason",
  "ontology_grounding": {
    "matched_concepts": [],
    "branches_touched": [],
    "ontology_sufficient": false,
    "reason": "Edoxaban not found in ontology; absence question must exhaust all three search layers."
  },
  "required_evidence": {
    "ontology_prose": true, "table_metadata": true, "formulas": false,
    "dag_traversal": false, "formatting": false, "raw_cells": true,
    "external_context": false
  },
  "expected_branches": ["overview", "inputs"],
  "tier1_tool": "answer_evidence_gap",
  "tool_args": {
    "target_query": "Edoxaban",
    "expected_in": "comparator",
    "ontology_absent": true
  },
  "composition": "gap_confirmation",
  "stop_condition": "gap_confirmed",
  "confidence_in_route": "high",
  "reason_for_route": "Absence question; gap_confirmation pipeline searches ontology, DAG, and raw cells; ontology probe already confirmed absent at concept level."
}
```

### Example 12 — Overview-shaped question requiring sequential drilldown

**Q: "Is this model incidence-based or prevalence-based?"**

```json
{
  "intent": "explain",
  "question_class": "overview",
  "target_concepts": ["population approach"],
  "secondary_concepts": ["incidence", "prevalence", "eligible population"],
  "qualifier": null,
  "qualifier_type": null,
  "investigation_type": "method",
  "ontology_grounding": {
    "matched_concepts": ["overview.population", "overview.model_type"],
    "branches_touched": ["overview"],
    "ontology_sufficient": false,
    "reason": "Overview matched but population approach not explicitly declared; may need methodology drilldown."
  },
  "required_evidence": {
    "ontology_prose": true, "table_metadata": true, "formulas": true,
    "dag_traversal": false, "formatting": false, "raw_cells": false,
    "external_context": false
  },
  "expected_branches": ["overview", "inputs", "calculations"],
  "tier1_tool": "answer_overview",
  "tool_args": {
    "question": "Is this model incidence-based or prevalence-based?"
  },
  "composition": "sequential_drilldown",
  "stop_condition": "envelope_complete",
  "confidence_in_route": "medium",
  "reason_for_route": "Try cheap overview path; if approach not explicit, drill into eligible-population methodology to inspect formula structure."
}
```

## 6. The Investigation Type Field — Why It Matters

`investigation_type` is the field that prevents same-class questions from routing identically. Within `concept` class:
- `reference` → `explain_concept` with `focus: 'sources'` → footnote extraction emphasis
- `implementation` → `explain_concept` with `focus: 'method'` → formula and precedent emphasis
- `enumeration` → `explain_concept` with `focus: 'tables'` → linked table listing emphasis
- `verification` → routes through `verify_against_workbook` — the user is asking us to confirm something

Within `audit` class:
- `consistency` → `audit_invariant` with `comparator_name_consistency`
- `formatting_rule` → `audit_invariant` with `inputs_are_blue`

Within `gap` class:
- `exclusion_reason` → `answer_evidence_gap` with full three-layer search
- `verification` → `answer_evidence_gap` with quick check, returns "found in DAG" redirect if exists

The investigation_type is the second-order classifier that makes thick tools precise.

## 7. The Stop Condition — Why It Matters

`stop_condition` is what tells the validator whether the LLM stopped too early or kept going past the answer.

| Stop condition | Meaning | Validation check |
|---|---|---|
| `envelope_complete` | First envelope returned `confidence: 'high'`; no further work needed | The envelope had `confidence: 'high'` and answered the question |
| `all_branches_covered` | Methodology answer touched every expected branch | All entries in `expected_branches` appear in `table_refs` |
| `gap_confirmed` | Gap confirmation searched ontology, DAG, and raw cells | All three layers were searched per the trace |
| `disambiguation_needed` | Multiple medium-confidence matches; user must choose | answer_text contains a disambiguation prompt; status='partial' |
| `budget_exhausted` | Hit max LLM hops or wall clock | Tool trace shows budget cap reached; status='partial' |

If the trace doesn't satisfy the declared stop_condition, validation flags it. This catches both "stopped too early" (declared `all_branches_covered` but only touched one branch) and "kept going" (declared `envelope_complete` but made three more Tier 1 calls).

## 8. The Confidence Field — Why It Matters

`confidence_in_route` is the LLM's self-assessment of route quality, declared *before* execution.

- `high` — clean concept resolution, single tool clearly suffices, no ambiguity. Validation applies standard checks.
- `medium` — likely tool but some uncertainty (overview that may need drilldown, ambiguous concept candidates). Validation applies stricter coverage checks; sequential_drilldown is allowed.
- `low` — planner is unsure; tool choice is best-guess. Validation applies the strictest checks; multi-tool composition is expected.

Patterns:
- `composition: sequential_drilldown` should usually pair with `confidence: medium` — the LLM is hedging.
- `composition: methodology_synthesis` should usually pair with `confidence: high` — methodology questions have a clear tool.
- `confidence: low` with `composition: single_tool` is suspicious; validation should flag it.

## 9. The Required Evidence Field — Why It Matters

`required_evidence` is the planner's promise about what evidence types will appear in the final citations. It serves two purposes:

**Forward-looking, for the executor.** Tools see what's expected and can adjust their internal pipelines. If `formulas: true`, `explain_concept` knows to pull formula text alongside descriptions.

**Backward-looking, for the validator.** After the answer is composed, validation checks that each `true` flag has a corresponding citation. If the plan declared `formulas: true` but no formula appears in `citations`, that's a violation — the LLM either skipped declared evidence or invented a need for it.

The flags are not aspirational; they are contractual.

## 10. Generating More Examples — The Process

The 12 in-prompt examples are a curated subset of a much larger gold corpus needed for evaluation. To build the corpus:

**Source 1 — HEOR domain expertise.** Sit with a notebook and write down every question you've been asked by payers, regulators, pharma reviewers, colleagues. Real questions have specificity that synthetic questions lack ("why is the 2023 cost 18% higher than 2022?"). Aim for 30-50 from this source.

**Source 2 — Reverse-engineering from review documents.** Use payer review letters, NICE/CADTH/ICER reports, internal QC checklists. The questions in those documents are the questions the tool needs to answer. They cluster around source documentation, methodology defensibility, parameter sensitivity, comparator selection rationale.

**Source 3 — Reference workbook decomposition.** For each ontology node in `sample_workbook_A`, ask "what would a reviewer want to know about this?" Each node generates 2-3 questions. A 40-node tree gives 80-120 questions for free.

**Categorize by class.** Bucket every collected question into the 9 classes. At least 10 questions per class is the minimum useful threshold.

**Categorize by qualifier_type.** Mark which questions have temporal, scenario, subgroup, or methodology qualifiers. Imbalance is fine but every type needs representation.

**Build expected QueryPlans.** For each gold-corpus question, manually produce the expected plan, the expected envelope shape, and the answer_text skeleton with required citations. This is the eval set.

**The 12 in-prompt examples are then chosen** to span the coverage matrix while representing the most common shapes. They teach the LLM by pattern, not by exhaustive coverage.

## 11. Twenty Seed Questions for the Corpus

Use these as starting points for corpus expansion, each tagged with class for reference:

1. What does this model evaluate, in which population, country, time horizon? **[overview]**
2. What is the intervention, and what are its comparators? **[overview]**
3. What are all input parameters, by sheet? **[what]**
4. What outputs feed the dashboard? **[what]**
5. What are the references for market share inputs? **[concept-reference]**
6. Where does the unit drug cost come from, by comparator? **[concept-reference]**
7. How is eligible population defined? **[concept-implementation]**
8. How is treatment discontinuation handled? **[concept-implementation]**
9. Where in the model does comparator drug cost get used? **[where]**
10. Where is the treatment duration assumption applied? **[where]**
11. How is total annual drug cost calculated for each comparator? **[how]**
12. How does the model translate market share into treated patients? **[how]**
13. How is drug cost for future years modeled? **[methodology-temporal]**
14. How are subsequent treatment costs handled after index treatment ends? **[methodology]**
15. How does scenario switching propagate from dashboard to results? **[methodology-scenario]**
16. Why is the 2025 incremental budget impact higher than 2024? **[why-temporal]**
17. Why does Drug A contribute disproportionately more to total cost than its market share suggests? **[why]**
18. Why is Edoxaban not included as a comparator? **[gap]**
19. Is there any source documentation for the persistence rate assumption? **[gap-verification]**
20. How is rivaroxaban modeled differently from apixaban? **[compare]**

## 12. Why This Taxonomy

The 9-class taxonomy claims that HEOR Q&A reduces to a finite, structurally distinct set of question shapes. Each shape has a deterministic algorithm. This is what justifies thick verbs over generic verbs.

The 5-value qualifier dimension claims that within a class, questions vary in well-defined ways that affect *what the tool looks for* but not *which tool runs*. Temporal qualifiers trigger year-conditional formula scans; scenario qualifiers trigger switch-detection; subgroup qualifiers trigger row-level filtering.

The 5-value composition dimension claims that LLM-visible orchestration is rare — most multi-step work happens deterministically inside Tier 1 tools. Only `sequential_drilldown` produces multiple LLM-visible Tier 1 calls, and it's reserved for genuine uncertainty cases.

These three dimensions, together, give the planner a small structured space to reason over. The 12 in-prompt examples teach the joint distribution. The corpus eval verifies generalization. The validation contract enforces commitments after the fact.

This is the design's central bet: with a tight taxonomy, thick deterministic tools, and contract-enforced validation, the LLM's job becomes routing — not reasoning — and the system becomes audit-defensible.