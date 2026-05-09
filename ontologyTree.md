# Ontology Tree — Structure and Node Contents

## 1. Purpose

The ontology tree is xl_ask's validated semantic layer. It is produced by Pass 2 (separate ontology repo) by interpreting the workbook through the lens of HEOR/BIM domain knowledge. It captures **meaning** that the mechanical extraction layers (table map, DAG) cannot express.

The tree is not exhaustive. It captures the main interpreted concepts a senior modeler would care about. Tables that don't map to a meaningful concept (auxiliary lookups, intermediate constants, formatting helpers) may not be linked. This is **partial coverage by design**, and xl_ask's mechanical layers handle the rest.

Critically: the ontology tree is the **first source of truth** for any question about model meaning. The DAG and table map are mechanical substrates; the ontology tree is what was understood.

## 2. The Six Branches

The tree has six top-level branches. Each represents a different lens on the model:

| Branch | What it captures | Typical node count |
|---|---|---|
| `overview` | Model framing: disease, population, intervention, comparators, country, perspective, time horizon, model type | 6–10 |
| `inputs` | Input concepts: market share, drug cost, population, utilities, resource use, mortality, etc. | 8–15 |
| `calculations` | Calculation concepts: eligible population, treated population, cost calculations, market share allocation | 5–10 |
| `results` | Result concepts: budget impact, cost by year, cost by component, scenario outputs, dashboard outputs | 4–8 |
| `dashboard` | Dashboard areas: scenario selector, results display, sensitivity view, control panel | 3–6 |
| `defaults` | Workbook conventions: user input format, formula format, scenario override format, named ranges | 3–5 |

A typical workbook produces 30–50 ontology nodes total. Small enough to fit in memory, traversable by deterministic dict lookups, no need for vector search or embeddings.

## 3. What Each Branch Contains — Prose Intent

This section describes what Pass 2 should be capturing in each branch. This is the editorial brief for the ontology repo, expressed at the conceptual level.

### Overview Branch

One node per major model-framing concept. Each node's `description` should be 1–3 paragraphs explaining how Pass 2 interpreted that aspect of the model in this specific workbook.

Concepts:
- **Indication / disease area** — what the model evaluates clinically
- **Population** — eligible patient population, sources, key inclusion criteria
- **Intervention** — the modeled drug/technology being evaluated
- **Comparators** — one node per comparator drug; description names the role and any methodological notes
- **Country / setting** — geographic scope and payer perspective implications
- **Perspective** — payer, societal, healthcare system; how this is reflected in cost categories
- **Time horizon** — duration of the model and rationale for the choice
- **Model type** — incidence-based vs prevalence-based, BIM vs cost-effectiveness, cohort vs individual

For each, the description records what Pass 2 found *and* how confident the interpretation is. If a concept is implicit in the workbook (e.g., perspective never explicitly declared but inferable from cost categories), the description should say so.

### Inputs Branch

One node per major input concept. Description explains:
- *What the input represents conceptually* (clinical or economic meaning)
- *How it's structured in this workbook* (one table or several, by drug or by year, with or without scenarios)
- *Which sources or assumptions back it* (Pass 2's reading of the footnotes and notes)
- *How it feeds into calculations* (qualitative — DAG handles the precise links)

Common nodes: market_share, drug_cost, subsequent_treatment, utilities, resource_use, population_size, incidence_rate, prevalence_rate, mortality_rate, persistence, adherence, treatment_duration.

### Calculations Branch

One node per major calculation concept. Description explains:
- *The calculation's role in the model logic* (what it produces and why)
- *The structural shape of the formula* (year-conditional, drug-by-drug, scenario-dependent, IF/CHOOSE-driven)
- *What governs its behavior* (which inputs are the levers; which are constants)
- *Any non-obvious modeling choices* (e.g., "Year 1 uses incident dose; Years 2+ apply persistence rate")

Common nodes: eligible_population, treated_population, market_share_allocation, drug_cost_calculation, subsequent_treatment_cost, total_cost_per_year, incremental_cost.

### Results Branch

One node per result concept. Description explains:
- *What the result represents* (clinical outcome, budget figure, scenario comparison)
- *What dimensions it's broken down by* (year, drug, scenario, cohort)
- *Which downstream consumers use it* (dashboard tile, output table, comparison view)

Common nodes: budget_impact_total, cost_by_year, cost_by_component, scenario_comparison, dashboard_summary.

### Dashboard Branch

One node per dashboard area. Description is mostly prose — what the user sees, what they can change, what gets recalculated. Often `linked_table_ids` is empty because dashboard cells aren't structured as tables in the BIM sense.

The dashboard branch is *primarily prose-answered*. Questions like "what does the dashboard control?" are answered directly from these descriptions. Nodes here often have `coverage: full` even with empty linked_table_ids — the description IS the answer.

### Defaults Branch

One node per formatting convention. Description explains the visual/structural convention used in this specific workbook:

- "User inputs are blue text on yellow fill"
- "Formula cells are black text on white fill"
- "Scenario overrides are red text"
- "Named ranges are used for [list of ranges]"
- "Locked cells include [list]"

This branch answers questions like "how do I tell which cells are user inputs vs formulas?" — answered from prose, not from DAG traversal.

## 4. The OntologyNode Schema (JSON / TypeScript)

```typescript
export type OntologyBranch =
  | 'overview'
  | 'inputs'
  | 'calculations'
  | 'results'
  | 'dashboard'
  | 'defaults';

export interface OntologyNode {
  // ---- Identity ----
  concept_id: string;              // "inputs.market_share", "overview.population"
  parent_id: string | null;        // "inputs", or null for branch roots
  branch: OntologyBranch;
  concept_label: string;           // human-readable: "Market Share"
  
  // ---- Resolution support ----
  aliases: string[];               // ["uptake", "share of treatment", "MS"]
                                   // primary lever for query resolution quality
  
  // ---- Linkage to mechanical layers (partial coverage by design) ----
  linked_table_ids: string[];      // table_id from final_table_map.json
  linked_node_ids: string[];       // node_id from node_index.json (DAG)
  linked_sheets: string[];         // sheet names where this concept lives
  coverage: 'full' | 'partial' | 'mentioned' | 'absent';
  
  // ---- The interpretive payload (Pass 2's prose) ----
  description: string;             // 1-3 paragraphs, the semantic answer
  key_assumptions?: string;        // optional prose on assumptions
  notes?: string;                  // optional Pass 2 commentary
  
  // ---- Concept-level relationships (cross-branch methodology) ----
  governs?: string[];              // concept_ids this one feeds/affects
  governed_by?: string[];          // concept_ids that govern this one
  
  // ---- Generation metadata ----
  source: 'pass2_llm' | 'pass2_rule' | 'manual';
  confidence: 'high' | 'medium' | 'low';
  reviewer_validated?: boolean;    // true if a human reviewed this node
}

export interface OntologyTree {
  workbook_name: string;
  generated_at: string;
  generator_version: string;       // ontology repo version that produced this
  
  root_concept_ids: string[];      // ["overview", "inputs", "calculations",
                                   //  "results", "dashboard", "defaults"]
  
  nodes: Record<string, OntologyNode>;  // keyed by concept_id
  
  // Derived indices (built at load time, not stored in JSON)
  // - by_branch: Record<OntologyBranch, concept_id[]>
  // - by_alias:  Map<alias, concept_id[]>
  // - by_table:  Map<table_id, concept_id[]>  (reverse index)
  // - by_dag_node: Map<node_id, concept_id[]> (reverse index)
}
```

## 5. Field-by-Field Rationale

### Identity fields

**`concept_id`** — Stable hierarchical identifier. Format: `<branch>.<concept_name>` for direct branch children, `<branch>.<parent>.<concept>` for nested. Used as the canonical reference everywhere — by the planner's `concept_id_hint`, by Tier 1 tools, by validation. Must not change across regenerations of the tree, even if `concept_label` is rewritten.

**`parent_id`** — Direct parent's concept_id, or null for the six branch roots. Enables tree traversal via `ontology_parent` and `ontology_children` Tier 2 primitives.

**`branch`** — Top-level branch this node belongs to. Redundant with the prefix of `concept_id`, but explicit for fast filtering.

**`concept_label`** — Human-readable name. What appears in the UI and in answer prose. Can be rewritten between regenerations without breaking references because `concept_id` is the stable handle.

### Resolution support

**`aliases`** — The most important field for query resolution quality. Pass 2 must populate this generously. For "market share" the aliases should include "uptake", "share of treatment", "MS", and any abbreviation Pass 2 saw used in the workbook. Lexical resolution depends entirely on aliases being comprehensive; without them, the planner's ontology probe will miss matches.

### Linkage to mechanical layers

**`linked_table_ids`** — IDs from `final_table_map.json` representing tables that materialize this concept in the workbook. May be empty for purely-prose concepts (dashboard nodes, defaults nodes, abstract overview concepts).

**`linked_node_ids`** — DAG node IDs corresponding to the linked tables. Provided when the concept maps to specific computational nodes. May be empty.

**`linked_sheets`** — Sheets where this concept lives, derived from linked tables. Useful for branch-level questions ("show me all inputs on the Mortality sheet").

**`coverage`** — Pass 2's self-assessment of how completely this concept is represented:
- `full` — all aspects of the concept have linked tables and a confident description
- `partial` — concept is real but some aspects (e.g., source documentation) are missing or inferred
- `mentioned` — concept appears in the workbook but isn't structurally modeled (e.g., a comparator named in a footnote but not given parameters)
- `absent` — concept was expected but not found; a deliberate negative entry, useful for gap questions

### The interpretive payload — the prose

**`description`** — 1–3 paragraphs in plain English. This is Pass 2's interpretation of the concept *in this specific workbook*. It is the answer to "what is this?" and "how is it handled?" questions when the concept is the entire question. Tier 1 tools (specifically `answer_directly_from_ontology` and `explain_concept`) quote this verbatim with citation.

The description should:
- Name the concept and its role in the model
- Describe how it's structured in the workbook (tables, dimensions, scenario coverage)
- Note the source/assumption pattern (where the values come from)
- Flag any methodological notes a reviewer would care about

The description should NOT:
- Restate generic HEOR theory (the user knows what market share is)
- Speculate beyond what's in the workbook
- Include cell coordinates or specific numbers (those come from linked tables when needed)

**`key_assumptions`** — Optional. When the concept involves a non-obvious modeling assumption (e.g., "persistence rate of 0.85 applied uniformly across comparators"), this captures it as separate prose. Useful for methodology answers.

**`notes`** — Optional. Pass 2's commentary about the node — caveats, ambiguities, places where extraction was uncertain. Used by xl_ask warnings, surfaced in answers when relevant.

### Concept-level relationships

**`governs`** — Other concept_ids this concept feeds or affects, at the *concept* level. For `inputs.market_share`, this might be `["calculations.market_share_allocation", "calculations.treated_population"]`. Distinct from DAG edges (which are cell-level); these are *meaning-level* relationships Pass 2 has asserted.

**`governed_by`** — The reverse: concept_ids that govern this one. For `calculations.drug_cost_calculation`, this might be `["inputs.drug_cost", "inputs.market_share", "inputs.population_size", "calculations.treated_population"]`.

These two fields are what make `explain_methodology` tractable. When the user asks "how is subsequent treatment modeled?", the methodology tool walks `governs` from `inputs.subsequent_treatment` to `calculations.subsequent_treatment_cost` to `results.budget_impact_total` — the concept-level chain. The DAG confirms it at cell level; ontology declares it at meaning level.

### Generation metadata

**`source`** — How this node was created:
- `pass2_llm` — Pass 2 LLM identified and described this concept
- `pass2_rule` — A deterministic rule populated this node (e.g., comparator nodes derived from a known comparator list)
- `manual` — A human added or edited this node

**`confidence`** — Pass 2's self-assessment of the node's correctness. `low` means the LLM was uncertain about either the concept's identity or its description. xl_ask uses this to weight answers — `low` confidence ontology nodes don't get quoted verbatim without a hedge.

**`reviewer_validated`** — Optional flag indicating a human reviewer signed off on the node. When true, xl_ask treats the description as authoritative.

## 6. Worked Example — A Single OntologyNode

Here is what a populated `inputs.market_share` node looks like:

```json
{
  "concept_id": "inputs.market_share",
  "parent_id": "inputs",
  "branch": "inputs",
  "concept_label": "Market Share",
  
  "aliases": ["uptake", "share of treatment", "MS", "market uptake", 
              "treatment share"],
  
  "linked_table_ids": [
    "tbl_inputs_market_share_base",
    "tbl_inputs_market_share_scenarios"
  ],
  "linked_node_ids": ["dag_node_47", "dag_node_48"],
  "linked_sheets": ["Inputs"],
  "coverage": "full",
  
  "description": "Market share captures the projected uptake of rivaroxaban relative to comparator NOACs (apixaban, dabigatran, edoxaban) over the 5-year time horizon. The model uses two structurally separate tables: a base-case projection (Inputs!B12:G19) holding annual market share by drug, and a scenarios table (Inputs!B25:G32) for high-uptake and low-uptake variants. Pass 2 read footnotes attributing Year 1-2 values to observed IQVIA MIDAS Canadian data (2022 Q4) and Year 3-5 values to a logistic projection methodology described in Smith et al. 2021. Market share rows sum to 100% within each year column, suggesting a closed comparator set assumption.",
  
  "key_assumptions": "Logistic projection assumes maximum saturation at observed peak. Comparator set is treated as closed (no new entrants assumed during horizon). Treatment-experienced and treatment-naive populations share the same market share schedule.",
  
  "notes": "Market share scenarios table appears partially unused — the scenario column reference goes through a control-sheet switch that may not be wired into all downstream calculations. Worth verifying via DAG.",
  
  "governs": [
    "calculations.market_share_allocation",
    "calculations.treated_population",
    "calculations.drug_cost_calculation"
  ],
  "governed_by": [
    "inputs.population_size"
  ],
  
  "source": "pass2_llm",
  "confidence": "high",
  "reviewer_validated": false
}
```

This single node is the answer to several questions xl_ask might receive:
- "What is market share in this model?" → quote `description`
- "Where does market share live?" → `linked_sheets` + `linked_table_ids`
- "What feeds into market share?" → `governed_by`
- "What does market share affect?" → `governs`
- "What are the references for market share?" → `description` + `linked_table_ids` for footnote extraction
- "How is market share modeled?" → `description` + `key_assumptions` + linked formulas via DAG

## 7. How xl_ask Uses the Tree

The tree is loaded once per session and held in memory with derived indices. xl_ask queries it through Tier 2 primitives:

- `find_ontology_nodes(query)` — lexical match against `concept_label` + `aliases` + `description`. Returns ranked OntologyNodes with `match_type` provenance.
- `get_ontology_node(concept_id)` — direct fetch.
- `ontology_children(concept_id)` / `ontology_subtree(concept_id, depth)` — structural traversal.
- `ontology_parent(concept_id)` — upward traversal.
- `ontology_linked_tables(concept_id)` — resolve to TableMap entries.
- `ontology_linked_dag_nodes(concept_id)` — resolve to DAG nodes.
- `ontology_coverage_check(query)` — `{found, coverage_level, candidate_concept_ids}` for planner probe use.

These primitives are deterministic, fast, and used by:
- The Stage 0 ontology probe in xl_ask runtime.
- Tier 1 tools that need concept resolution.
- The `answer_directly_from_ontology` runtime function for ontology-sufficient questions.

## 8. Design Constraints — What the Ontology Repo Must Honor

For xl_ask to function correctly, the ontology repo's output must satisfy:

1. **Stable concept_ids across regenerations.** If `inputs.market_share` exists in version N, it should still be `inputs.market_share` in version N+1. Renaming concepts (changing `concept_label`) is fine; renaming `concept_id` breaks references.

2. **Comprehensive aliases.** Every concept must have aliases covering common phrasings, abbreviations, and any workbook-specific shorthand. Resolution quality is alias quality.

3. **Honest coverage flags.** `coverage: full` should mean what it says. Overstating coverage causes xl_ask to skip mechanical fallback when it shouldn't.

4. **Concept-level relationships populated for non-leaf nodes.** `governs` and `governed_by` must be filled in for input and calculation concepts. Without these, methodology questions cannot walk the concept graph.

5. **Descriptions written in plain English at the right altitude.** Not too generic ("market share is the share of market"), not too specific (cell coordinates and numbers). The right altitude is: a senior modeler explaining this workbook's handling of the concept to a colleague.

6. **Negative entries for `coverage: absent` concepts when asked about.** If a comparator was expected but not modeled (e.g., Edoxaban was named as a candidate but no parameters were captured), an `absent` node should exist. This is what makes gap-question answers honest.

## 9. What the Ontology Tree is NOT

To keep the design clean, several things are explicitly out of scope for the ontology tree:

- **Not a complete catalog of every table.** That's the table map's job. The ontology captures what was *meaningfully understood*; the table map captures what *exists*.
- **Not a graph of cell-level dependencies.** That's the DAG's job. Ontology has *concept-level* relationships (`governs`, `governed_by`).
- **Not a database of values.** Numbers and cells live in the table map and DAG. Ontology has prose interpretation.
- **Not generated by xl_ask.** xl_ask consumes the tree; production happens in the separate ontology repo (Pass 2).
- **Not vector-indexed.** Resolution is lexical + alias-based. The tree is small enough that this is fast and deterministic.
- **Not exhaustive.** Partial coverage is by design. xl_ask falls through to mechanical layers when ontology has no match.

## 10. Why This Shape

The ontology tree's design is driven by three needs:

**Need 1 — A semantic index that scales with reviewer judgment, not extraction completeness.** The DAG and table map are bounded by what the parser captures. The ontology tree is bounded by what Pass 2 understands. As Pass 2 improves, the tree gets richer without changing the schema.

**Need 2 — Prose that is the answer, not a summary.** When a user asks "how is market share modeled?", the right answer is what a modeler would write. Pass 2 writes that, once, into `description`. xl_ask quotes it with citation. No re-derivation, no information loss.

**Need 3 — Stable handles for the planner to use.** `concept_id` is the planner's currency. The QueryPlan's `target_concepts` and `concept_id_hint` fields refer to ontology nodes by these IDs. The DAG and table map can change as extraction improves; ontology IDs stay stable so plans remain valid.

The schema is the minimum that satisfies these three needs. Each field earns its place. None is optional except `key_assumptions`, `notes`, and `reviewer_validated`, which are genuine optionals.