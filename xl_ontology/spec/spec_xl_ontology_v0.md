# spec_xl_ontology_v0.md — Pluggable Ontology Mapping Layer (MVP)

## Status

This is the **specification** for the `xl_ontology` repo. It is the downstream
consumer of `xl_sheetagent` / `finalize` / DAG and the upstream producer of an
ontology map that `xl_ask` reads.

**Implementation note for Claude CLI:** this document is a **specification**,
not an implementation plan. Claude CLI is expected to derive the implementation
plan from this spec. The spec describes *what must be true*; the plan describes
*how to build it*. Do not start coding before producing an implementation plan
and having it reviewed.

**Companion documents:**
- `xl_ontology_tools.md` — formal tool list and signatures (separate file)
- `bim_v0/` directory — the first ontology pack, containing per-node `.txt`,
  `.context.md`, and `.heuristics.md` files plus a pack-level
  `pack_overview.context.md`

---

## 1. Purpose

`xl_ontology` produces a **lightweight ontology map** of a workbook. For an
HEOR budget impact model (BIM), the map answers questions like:

- What is this model about overall?
- What are the candidate input tables, and what parameters do they contain?
- What are the candidate calculation tables, and what do they compute?
- What are the candidate result tables, and what metrics do they report?

The map is **not** the final question-answering system. `xl_ask` answers user
questions using this map, the sheet map, the DAG, and tools. `xl_ontology`
only gives `xl_ask` an *oriented starting point*.

The ontology applied is **pluggable**. The first ontology shipped is `bim_v0`
(budget impact model). Future packs (`cem_v0`, `markov_cea_v0`) plug into the
same pipeline without code changes.

---

## 2. Position in Pipeline

```
xl_init
  → xl_parse
  → xl_sheetagent
  → finalize (produces final_table_map.json)
  → DAG (produces dag.json, node_index.json, formulas.json)
  → xl_ontology   ← THIS REPO
  → xl_ask
```

`xl_ontology` runs **after** finalize and DAG. It treats their outputs as
read-only sources of truth. It never modifies upstream artifacts.

---

## 3. Operating Model: Seed + Lazy Enrichment

The architectural commitment for v0.

### 3.1 Seed phase (runs once, after upload)

`xl_ontology` produces a **seed map** quickly. The seed contains:

- A best-effort overall model summary
- Top-N candidate tables per ontology node (10–20 inputs, 1–3 calc, 1–3
  results)
- All other tables marked `unclassified` with a priority score
- A bounded LLM budget (Section 11)

The seed is written to `summary_model_v0.json`. It is the artifact `xl_ask`
reads on startup.

### 3.2 Lazy enrichment phase (runs at query time)

When `xl_ask` receives a question that touches an `unclassified` table, or a
question that expands the scope of a node ("show me *all* the inputs, not
just the top 10"), it calls back into `xl_ontology` with:

```
classify_table(table_id) → ontology_node + extracted_payload
expand_node(ontology_node, [concept]) → fully-classified table list
```

The result is appended to the map. The on-disk artifact moves from `v0` to
`v1`, `v2`, …

### 3.3 Versioning

Simple integer monotone for v0. Each enrichment writes a full snapshot, not a
diff. `xl_ask` always loads the highest version available. No merging
required.

---

## 4. What `xl_ontology` Does and Does Not Do

### Does

- Trust upstream `node_type` from finalize/DAG (Section 5).
- Apply a chosen ontology pack to rank, classify, and extract content from
  candidate tables.
- For each ontology node, run a **per-node agent** with planner-executor-
  validator architecture (Section 8).
- Send tables to LLM **whole** when defects require it (Section 10). Never
  send fragments.
- Produce a versioned `summary_model.json` with structured evidence
  (Section 12).
- Flag (but never fix) defects observed during classification.
- Preserve parent-child table relationships when classifying (Section 7).

### Does not

- Re-derive structural classification (input vs calc vs output) from scratch.
- Modify the workbook, the sheet map, the DAG, or any upstream artifact.
- Answer user questions.
- Repair multi-core tables, reassign headers, or rewrite finalize entries.
- Build a deep semantic ontology. The ontology is intentionally minimal.

---

## 5. Trust Contract with Upstream

`xl_ontology` consumes the following from upstream and treats them as
authoritative:

- `final_table_map.json` — table identities, bboxes, titles, headers, row
  names, parent/child structure, `node_type`, finalize flags
- `dag.json` — node-level dependencies, fan-in / fan-out, terminal nodes
- `node_index.json` — fast lookup by node_type, sheet, search text, named
  range
- `formulas.json` — representative formulas per node (lazy-loaded)
- `xl_sheetagent` tags — formula/value density, structural type, ambiguity
  flags

### 5.1 `node_type` is trusted but not absolute

Upstream `node_type` (input / calc / output / mixed / data_table) is the
**starting point** for ontology classification, not the final word.

Concretely:
- Tables with `node_type=input` are the **first candidates** for the ontology
  Inputs node.
- The Inputs ontology agent is then expected to:
  1. Pull all `node_type=input` tables.
  2. Read the ontology's expected concept list (population, market share,
     drug cost, etc.).
  3. Search across all tables for those concepts using the lexicon — including
     tables tagged `calc` or `mixed`.
  4. If an expected concept is found in a non-input-tagged table, propose an
     ontology classification override and flag
     `node_type_override_proposed: true`.
  5. The override lives only in the ontology map, never propagates upstream.

### 5.2 Reading upstream flags

For every candidate table, the ontology agent **must read all upstream flags
first** before deciding how to handle it. In v0 each defect flag dispatches
to a **stub procedure** (see Section 10) that records a
`defect_procedure_skipped` trace event. The agent then continues
classification using whatever metadata exists, marking
`evidence_quality: partial` or `absent` on affected concepts. Real
recovery is deferred to a future `xl_table_repair` pre-stage.

- `title_missing` → call the title-recovery stub (Section 10.1); record
  the skip event; continue classification using whatever title metadata
  exists.
- `header_missing` → call the header-recovery stub (Section 10.2); record
  the skip event; continue classification using whatever header metadata
  exists.
- `rowname_missing` → call the row-name-recovery stub (Section 10.3);
  record the skip event; continue classification using whatever row-label
  metadata exists.
- `multi_core_detected` → call the multi-core stub (Section 10.4); record
  the skip event; the table goes to `unclassified` with reason
  `defect_procedure_disabled_in_v0` unless other agents can resolve it
  via lexicon / structural sanity checks.
- `low_context_node` → propagate into ontology confidence; do not silently
  upgrade.

The agent's first action on any table is: read all upstream flags, decide
what to do based on them, then proceed.

---

## 6. Pluggable Ontology Pack — Three-File Structure per Node

An ontology pack is a directory of plain-text files. The first pack is
`bim_v0/`. Each ontology node has **three files**, plus the pack has one
shared overview file.

```
bim_v0/
├── pack_overview.context.md            (shared across all four agents)
│
├── ontology_overall_model.txt          (the contract — what to find)
├── ontology_overall_model.context.md   (the framing — what BIM/this node is)
├── ontology_overall_model.heuristics.md (the playbook — how to find it)
│
├── ontology_inputs.txt
├── ontology_inputs.context.md
├── ontology_inputs.heuristics.md
│
├── ontology_calculations.txt
├── ontology_calculations.context.md
├── ontology_calculations.heuristics.md
│
├── ontology_results.txt
├── ontology_results.context.md
└── ontology_results.heuristics.md
```

### 6.1 The three file types

**`<node>.txt` — the contract.** Imperative voice. Stable. Defines:

1. Identity (pack, node, version, model family, expected scale, LLM budget)
2. Purpose
3. Expected concepts
4. Structural fingerprints
5. Lexicon (synonyms, acronyms, language patterns)
6. Decision rules
7. Extraction schema (with structured `evidence` shape)
8. Format conventions (cell role conventions for this node type)
9. Confidence calibration
10. Unknown policy
11. Summary constraints (template vs LLM, length, voice)

**`<node>.context.md` — the framing.** Declarative voice. Stable. Explains:

- What this ontology node is for in the model family's context
- Why it always (or usually) exists
- Common variations across well-built and poorly-built workbooks
- What the agent should expect philosophically before reading the contract

**`<node>.heuristics.md` — the playbook.** Living document. Front-matter
parseable, prose body. Each heuristic is a named chunk (`H-OM-1`, `H-IN-3`,
…) with:

- Front-matter: `id`, `applies_to`, `concept`, `trigger`, `priority`,
  `last_updated`, `status` (active | experimental | deprecated |
  subsumed_by_ml)
- Body: trigger description, pattern, verification, recording

**`pack_overview.context.md` — the pack-level frame.** Loaded once per run,
shared across all four agents. Describes:

- What this model family is (e.g., what a BIM is, who builds it, why)
- Concepts that are always present and why
- The decision context the model exists to inform
- What this means for an agent reading the workbook

### 6.2 Injection into the agent

The three file types are injected at different points:

- `pack_overview.context.md` → loaded once at the start of the
  `xl_ontology` run; included in every agent's planner prompt
- `<node>.context.md` → loaded when the corresponding agent starts; included
  in that agent's planner prompt
- `<node>.txt` → loaded when the corresponding agent starts; included in the
  planner prompt and is the primary source of truth for decision rules
- `<node>.heuristics.md` → **not loaded by default**. The executor injects
  individual heuristic chunks on-demand, based on the heuristic's
  front-matter `trigger` matching the current execution state

This keeps planner context small while making the full heuristic library
available when needed.

### 6.3 Adding new heuristics

Heuristics are **append-only in v0**. To add a heuristic:

1. Choose a new ID (e.g., `H-OM-7` for the seventh overall_model heuristic).
2. Write the chunk with full front-matter, status=`experimental`.
3. Run on test workbooks. Check the run log to confirm it fires.
4. Promote to `status: active` once validated.
5. Never edit an existing heuristic's body in place — deprecate the old one
   (`status: deprecated`) and add a new one.

This keeps the heuristic library auditable across time.

### 6.4 Adding new ontology packs

A new pack (e.g., `cem_v0/` for cost-effectiveness models) is a new directory
with the same file structure. No code changes. The pack is selected at
runtime:

```
xl_ontology --ontology bim_v0 ./run_id/
xl_ontology --ontology cem_v0 ./run_id/
```

---

## 7. Parent-Child Table Handling

The DAG only contains children as nodes (parents are navigation-only). The
ontology map preserves the parent-child relationship.

### 7.1 Three cases

**Case A — All children share an ontology role.**
Example: parent "Drug Costs" with children "Drug Cost — Intervention" and
"Drug Cost — Comparator", both classified as `inputs / drug_cost`.

→ Classify each child as a candidate. All children carry the same
`parent_table_id`. Add one `parent_groups` entry to the inputs node.

**Case B — Children span ontology roles.**
Example: parent "Total Costs Section" with children classified as input,
calculation, and result respectively.

→ Each child is classified into its respective node. The parent is not
classified (parents are navigation-only). The `parent_groups` entry appears
in the *dominant* node (the one with the most children) with
`all_in_this_node: false` and cross-references to siblings in other nodes.

**Case C — Parent split appears artificial.**
Example: a single logical input table that finalize split into two children
because of a blank row in the middle. Children share headers and row label
structure.

→ Classify each child individually. Set
`parent_split_appears_artificial: true` flag (advisory; may inform a future
finalize improvement).

### 7.2 Schema additions per candidate

```
"parent_table_id": "Costs__t12__parent" | null,
"parent_path": ["Costs", "Drug Costs"],
"sibling_count": 3,
"parent_split_appears_artificial": false
```

### 7.3 Schema for `parent_groups` per ontology node

```
"parent_groups": [
  {
    "parent_table_id": "Costs__t12__parent",
    "parent_title": "Drug Costs",
    "members": ["Costs__t12__c0", "Costs__t12__c1"],
    "all_in_this_node": true,
    "cross_node_members": []
  }
]
```

### 7.4 One-table-one-role constraint

In v0, a `table_id` appears in **at most one** ontology node's
`candidate_tables`. If a table genuinely spans roles, classify by primary
role and add `mixed_role_observed` flag. Reason: lazy enrichment becomes
intractable if classifications must be merged across nodes.

---

## 8. Agent Architecture: Planner — Executor — Validator

Each ontology agent follows the same internal architecture.

```
   ┌────────────────────────────────────────┐
   │  Ontology Agent (one per node)         │
   │                                        │
   │  Job description: ontology_<node>.txt  │
   │  Framing: pack_overview + node.context │
   │                                        │
   │   ┌────────────┐                       │
   │   │  Planner   │  ← 1 LLM call         │
   │   │            │     (produces a       │
   │   │            │     structured plan)  │
   │   └─────┬──────┘                       │
   │         │                              │
   │         ▼                              │
   │   ┌────────────┐                       │
   │   │  Executor  │  ← runs tools         │
   │   │            │     deterministically │
   │   │            │     in plan order;    │
   │   │            │     pulls heuristics  │
   │   │            │     when triggered;   │
   │   │            │     calls LLM only    │
   │   │            │     for defect        │
   │   │            │     procedures        │
   │   └─────┬──────┘                       │
   │         │                              │
   │         ▼                              │
   │   ┌────────────┐                       │
   │   │ Validator  │  ← coverage check;    │
   │   │            │     evidence_quality  │
   │   │            │     grading; flags    │
   │   └────────────┘                       │
   └────────────────────────────────────────┘
```

### 8.1 Why planner + executor + validator

- **Without a planner**: brittle hard-coded sequences, or wasteful per-tool
  LLM routing.
- **With a planner**: one LLM decision at the start; the executor runs
  deterministically.
- **Validator**: ensures coverage, calibrates confidence, applies the unknown
  policy.

This matches the project's doctrine: agents are controlled compilers, not
chatbots.

### 8.2 Planner inputs

The planner LLM call receives:

- `pack_overview.context.md`
- `<node>.context.md`
- `<node>.txt`
- A condensed view of the workbook from `node_index` (sheet names, table
  titles, search tokens — not table contents)

The planner outputs a **structured plan**: an ordered list of tool calls
with arguments. The executor runs the plan as-is.

### 8.3 Executor behaviour

The executor:

1. Runs each tool call in order.
2. After each call, checks whether any heuristic in
   `<node>.heuristics.md` has a `trigger` that matches the current state.
3. If yes, injects that heuristic's body into the next relevant tool call
   (typically into an `ask_llm` call for inferred concepts) and logs
   `applied_heuristic: <id>` in the run log.
4. Calls `ask_llm` only when:
   - A defect procedure (Section 10) is invoked
   - An expected concept has no deterministic evidence and the LLM must
     infer it
5. Never re-plans on its own. If the plan fails to find expected concepts,
   the validator flags it.

### 8.4 Validator behaviour

The validator:

1. For each expected concept in `<node>.txt`, checks whether the executor
   produced evidence.
2. Assigns `evidence_quality` (`high | partial | inferred | absent`) based
   on the rules in `<node>.txt` Section 9 (Confidence Calibration).
3. Builds the structured `evidence` array (Section 12).
4. For absent concepts, builds the `search_log` array recording what was
   searched.
5. Applies the unknown policy from `<node>.txt`.
6. Writes the node entry into the in-memory ontology map.

---

## 9. Workflow per Ontology Node

For each ontology node:

1. **Load** the three node-specific files plus `pack_overview.context.md`.
2. **Plan** (1 LLM call). Planner produces an ordered tool plan.
3. **Execute** the plan:
   a. Read upstream flags for each candidate table first.
   b. Decide whether the table is handleable deterministically or requires
      LLM (defect procedure).
   c. Inject heuristics from `<node>.heuristics.md` when triggered.
   d. Extract per the ontology's extraction schema.
4. **Cross-check** against expected concepts. Search across `node_type`
   boundaries if a concept is missing from the starting set.
5. **Validate**. Build structured evidence, search_log for absent concepts,
   apply unknown policy.
6. **Generate summary**:
   - `overall_model`: 1 LLM call (Option A).
   - `inputs`, `calculations`, `results`: template (Option B). No LLM call.
7. **Write** the node entry into `summary_model_v0.json`.
8. **Stop** when expected scale is met or LLM budget is exhausted.

---

## 10. Defect Procedures

> **v0 ships with STUBS only. Full recovery procedures are deferred to a
> future `xl_table_repair` pre-stage that runs before `xl_ontology`.**
>
> The stubs exist to (a) preserve the architectural seam in the executor
> so adding the real procedures later is a drop-in change, and (b) emit
> `defect_procedure_skipped` trace events that quantify defect frequency
> in real workbooks. That data sizes and prioritises `xl_table_repair`.
>
> When `xl_table_repair` ships, the pipeline order becomes:
> `finalize → DAG → xl_table_repair → xl_ontology → xl_ask`. At that
> point the four stubs in this section are deleted and `xl_ontology`
> receives only repaired tables.
>
> Section numbers are preserved so the future `xl_table_repair` spec can
> reference them by name.

### Stub contract (applies to all four procedures below)

Each stub procedure is a small Python function with the signature its
full version would have (so callers do not need to change when the real
procedures land). The stub:

a. Reads the upstream flag(s) for its defect type.
b. Emits a `defect_procedure_skipped` trace event with fields:
   `{ table_id, flag_type, would_have_been_procedure_id, agent_owner, ts }`
   where `would_have_been_procedure_id` ∈ `"10.1" | "10.2" | "10.3" | "10.4"`.
c. Returns `DefectOutcome(value=None, evidence=[], confidence=None,
   evidence_quality="absent",
   flags_added=["defect_procedure_disabled_in_v0"])`.
d. **Does NOT call the LLM.**
e. **Does NOT implement any rule-based recovery.**

The agent then continues classifying the table with whatever metadata
upstream did emit, marking affected concepts `partial` or `absent`. If
no useful metadata exists at all, the table goes to
`unclassified_tables` with `reason = defect_procedure_disabled_in_v0`.

### 10.1 Title missing — STUB

Stub for `title_missing`. Records skip event with
`would_have_been_procedure_id="10.1"`. The full procedure (deferred to
`xl_table_repair`) would walk cells above the bbox and, if necessary,
ask the LLM to propose a title from sibling-table context.

### 10.2 Header missing — STUB

Stub for `header_missing`. Records skip event with
`would_have_been_procedure_id="10.2"`. The full procedure would inspect
the first 1–3 rows for formatting + column-wise data type, and fall back
to an LLM call with the whole table.

### 10.3 Row names missing — STUB

Stub for `rowname_missing` (or `row_names is null` in the finalize
entry). Records skip event with `would_have_been_procedure_id="10.3"`.
The full procedure would look at columns to the left of the bbox and
fall back to the LLM.

### 10.4 Multi-core detected — STUB

Stub for `multi_core_detected`. Records skip event with
`would_have_been_procedure_id="10.4"`. Multi-core tables go to
`unclassified` with `reason = defect_procedure_disabled_in_v0`. The
full procedure (deferred) would send the whole table plus all
representative formulas to the LLM to identify sub-table boundaries —
the only place v0+repair would send formulas in bulk.

### 10.5 No defect

When all upstream flags are clean, the agent extracts deterministically
per the ontology's extraction schema. No LLM call. No stub invoked.

---

## 11. LLM Budget

### Seed phase
- **Maximum 8 LLM calls per workbook.** (Reduced from 20: defect
  recovery is stubbed in v0, see Section 10. The 12 calls previously
  reserved for defect procedures (3 result + 3 calc + 6 input title /
  header recovery + 3 multi-core resolution) are no longer needed.)
- Priority order:
  1. Planner calls (1 per agent → 4 calls)
  2. `overall_model` summary synthesis (1 call)
  3. Inferred-concept synthesis via LLM-fallback heuristics (≤ 3)
- On budget exhaustion: stop, log unclassified tables, write
  `summary_model_v0.json`, defer remaining classification to lazy
  enrichment.

### Lazy enrichment
- **Maximum 5 LLM calls per `xl_ask` query** that touches `xl_ontology`.

Both budgets are configurable per pack via front-matter in
`pack_overview.context.md` but default to the values above. When
`xl_table_repair` ships and the defect stubs are removed, this cap
stays at 8 — repair takes recovery LLM calls out of `xl_ontology`'s
budget entirely.

---

## 12. Output Schema

### 12.1 Files produced per workbook

Under `{run_id}/ontology_output/`, the run produces **five required
artifacts** plus a build-error log. All five required artifacts must
exist after every successful run; the orchestrator UI relies on them
for tracing and inspection (Section 13). The `node_type_override_log.jsonl`
artifact is required-on-disk but may be empty when no overrides occurred.

| File | Purpose | Consumer |
|---|---|---|
| `summary_model_v{N}.json` | The ontology map. Final classification results, structured for `xl_ask`. | `xl_ask`, UI "Final output" view |
| `ontology_agent_outputs.json` | Per-agent final output: plan, evidence summary, decisions, applied heuristics. One section per agent. | UI "Per-agent output" view |
| `ontology_table_tags.json` | Per-table classification record: every `table_id` from finalize and how this run tagged it. | UI "All tagged tables" view |
| `ontology_trace.jsonl` | Append-only step-by-step trace. Every planner call, tool call, executor decision, LLM prompt, LLM response, heuristic firing. | UI "Inspect run" view |
| `node_type_override_log.jsonl` | Per-override row capturing every finalize/ontology disagreement about `node_type`. Side effect of existing override logic — the file is a labelled-disagreement corpus for future finalize ML training. | Future finalize ML training; UI "Upstream label disagreements" view |
| `ontology_build_errors.jsonl` | Per-table failures during seed phase that did not abort the run. | UI "Errors" tab; debugging |

Versioning of `summary_model_v{N}.json` follows Section 3.3 (integer
monotone, full snapshots). The other artifacts are overwritten on each
run; lazy enrichment appends to `ontology_trace.jsonl` and
`node_type_override_log.jsonl` and updates `ontology_agent_outputs.json`
and `ontology_table_tags.json` in place.

### 12.2 `summary_model.json` shape

```json
{
  "model_id": "...",
  "ontology_id": "bim_v0",
  "ontology_version": "0.1",
  "schema_version": "v0",
  "seed_timestamp": "...",
  "last_enriched_timestamp": "...",
  "workbook_file_hash": "sha256:...",

  "overall_model": {
    "present": true,
    "evidence_quality": "partial",
    "summary": "5-year UK budget impact model comparing Drug X against standard of care in adults with metastatic NSCLC.",
    "summary_source": "llm_synthesised",
    "concepts": {
      "intervention": {
        "value": "Drug X",
        "evidence": [
          {
            "sheet": "Description",
            "cell": "B7",
            "table_id": "Description__t1__c0",
            "snippet": "Intervention: Drug X (50mg tablet, oral)",
            "match_type": "lexicon_match",
            "matched_term": "intervention"
          }
        ],
        "search_log": [],
        "confidence": 0.92,
        "evidence_quality": "high"
      },
      "perspective": {
        "value": null,
        "evidence": [],
        "search_log": [
          {
            "tool": "find_overview_sheets",
            "result_sheets": ["Description", "Front"]
          },
          {
            "tool": "search_workbook_lexicon",
            "terms": ["perspective", "viewpoint", "payer", "societal"],
            "scope": "text_cells_only",
            "hits": 0
          }
        ],
        "confidence": null,
        "evidence_quality": "absent"
      }
    },
    "applied_heuristics": ["H-OM-2"],
    "confidence": 0.78
  },

  "inputs": {
    "present": true,
    "evidence_quality": "high",
    "summary": "6 input tables identified covering population, market share, drug cost, and admin cost. AE costs and monitoring costs not located.",
    "summary_source": "template",
    "candidate_tables": [
      {
        "table_id": "Costs__t15__c0",
        "ontology_role": "input",
        "ontology_subtype": "drug_cost",
        "confidence": 0.86,
        "evidence_quality": "high",
        "why_selected": "lexicon match in title; low formula density; high DAG fan-out",
        "evidence_signals": [
          {
            "type": "title_match",
            "value": "Drug Acquisition Cost",
            "matched_term": "drug cost"
          },
          {
            "type": "formula_density",
            "value": 0.05
          },
          {
            "type": "dag_fanout",
            "value": 8
          }
        ],
        "upstream_node_type": "input",
        "node_type_override_proposed": false,
        "parent_table_id": null,
        "parent_path": ["Costs"],
        "sibling_count": 1,
        "parent_split_appears_artificial": false,
        "applied_heuristics": [],
        "extracted_payload": {
          "parameters": [
            {
              "name": "Drug X — list price per pack",
              "value": 1240.00,
              "unit": "GBP",
              "source_cell": "Costs!C12",
              "is_formula": false,
              "format_role": "input_value"
            }
          ]
        },
        "flags": []
      }
    ],
    "parent_groups": [],
    "expected_concepts_status": {
      "population": "found",
      "market_share": "found",
      "drug_cost": "found",
      "administration_cost": "found",
      "ae_cost": "absent",
      "monitoring_cost": "absent"
    },
    "confidence": 0.78
  },

  "calculations": { /* same shape */ },
  "results": { /* same shape */ },

  "unclassified_tables": [
    {
      "table_id": "Notes__t3__c0",
      "priority_score": 0.4,
      "reason": "low_context"
    }
  ],

  "proposed_table_corrections": [
    {
      "table_id": "Costs__t14__c0",
      "issue": "header_missing",
      "proposed_correction": "row above table looks like header",
      "confidence": 0.72,
      "evidence": "row Costs!10 contains text, row Costs!11+ contains numbers",
      "consumer": "advisory_only"
    }
  ],

  "llm_budget": {
    "seed_calls_used": 14,
    "seed_calls_max": 20,
    "enrichment_calls_used": 0
  },

  "run_notes": []
}
```

### 12.3 Evidence shape (consistent across all nodes)

Every `evidence` array contains objects of this shape:

```
{
  "sheet": "string",
  "cell": "string (Excel notation)",
  "table_id": "string or null (null if cell is loose metadata)",
  "snippet": "string (actual cell text)",
  "match_type": "lexicon_match | format_match | dropdown_value | llm_inferred | cross_reference | structural_fingerprint",
  "matched_term": "string or null"
}
```

### 12.4 Search log shape (for absent concepts)

```
{
  "tool": "string (tool name)",
  "args_summary": "string or object (key arguments)",
  "result_summary": "string or object (what came back)",
  "hits": "integer or null"
}
```

### 12.5 Confidence calibration

- `null` = not computed
- `0.0–1.0` = numeric confidence (LLM- or rule-derived)
- `evidence_quality` is categorical: `high | partial | inferred | absent`

### 12.6 Summary generation rules

- **`overall_model.summary`**: LLM-synthesised, 1 call, `summary_source:
  llm_synthesised`. Constraints: max 3 sentences (~60 words); facts only;
  must not duplicate fields already in `concepts`; on absent
  evidence_quality, summary = null.
- **`inputs.summary`, `calculations.summary`, `results.summary`**:
  template-generated, no LLM call, `summary_source: template`. Templates
  fill slots like `"{N} input tables identified covering {found_concepts}.
  {missing_concepts} not found."`

### 12.7 Proposed corrections

Advisory only. Never propagate upstream. Stored in
`proposed_table_corrections` for human review or future finalize
improvement.

### 12.8 `ontology_agent_outputs.json` shape

One section per ontology agent. Captures the **per-agent final output**
in a form the UI can render directly without traversing the trace.

```json
{
  "model_id": "...",
  "ontology_id": "bim_v0",
  "run_timestamp": "...",
  "agents": {
    "overall_model": {
      "status": "complete | partial | failed",
      "evidence_quality": "high | partial | inferred | absent",
      "summary": "5-year UK budget impact model comparing Drug X against standard of care in adults with metastatic NSCLC.",
      "summary_source": "llm_synthesised",
      "concepts_resolved": 6,
      "concepts_absent": 1,
      "applied_heuristics": ["H-OM-2", "H-OM-5"],
      "tools_called": ["find_overview_sheets", "list_data_validation_dropdowns", "search_workbook_lexicon", "ask_llm"],
      "llm_calls_used": 2,
      "elapsed_seconds": 3.4,
      "key_decisions": [
        "Inferred country from currency symbol (£ → UK) via H-OM-2",
        "Inferred payer perspective from cost categories via H-OM-5",
        "Could not locate explicit perspective statement (recorded absent)"
      ],
      "human_readable_output": "..."
    },
    "inputs": {
      "status": "complete",
      "evidence_quality": "high",
      "summary": "6 input tables identified covering population, market share, drug cost, and admin cost. AE costs and monitoring costs not located.",
      "summary_source": "template",
      "tables_classified": 6,
      "node_type_overrides_proposed": 1,
      "expected_concepts_found": ["population", "market_share", "drug_cost", "administration_cost"],
      "expected_concepts_absent": ["ae_cost", "monitoring_cost"],
      "applied_heuristics": ["H-IN-1"],
      "tools_called": [...],
      "llm_calls_used": 3,
      "elapsed_seconds": 8.1,
      "key_decisions": [
        "Population found in calc-tagged table (override proposed) via H-IN-1",
        "Drug cost recovered after title-missing defect procedure"
      ],
      "human_readable_output": "..."
    },
    "calculations": { ... },
    "results": { ... }
  }
}
```

The `human_readable_output` field is a multi-paragraph prose rendering
of the agent's findings, suitable for direct display in Streamlit. The
agent's validator generates this at end of run by templating from the
structured fields. No LLM call required.

### 12.9 `ontology_table_tags.json` shape

A flat per-table record covering **every** `table_id` from finalize.
The UI uses this to render the "all tagged tables" inspection view —
the user can see at a glance how each table was treated.

```json
{
  "model_id": "...",
  "ontology_id": "bim_v0",
  "run_timestamp": "...",
  "tables": [
    {
      "table_id": "Costs__t15__c0",
      "sheet": "Costs",
      "title": "Drug Acquisition Cost",
      "upstream_node_type": "input",
      "ontology_role": "input",
      "ontology_subtype": "drug_cost",
      "node_type_override_proposed": false,
      "in_top_candidates": true,
      "confidence": 0.86,
      "evidence_quality": "high",
      "agent_owner": "inputs",
      "applied_heuristics": [],
      "flags": []
    },
    {
      "table_id": "Engine__t3__c0",
      "sheet": "Engine",
      "title": "Population — Eligible Patients",
      "upstream_node_type": "calc",
      "ontology_role": "input",
      "ontology_subtype": "population",
      "node_type_override_proposed": true,
      "in_top_candidates": true,
      "confidence": 0.71,
      "evidence_quality": "partial",
      "agent_owner": "inputs",
      "applied_heuristics": ["H-IN-1"],
      "flags": ["override_from_calc"]
    },
    {
      "table_id": "Notes__t3__c0",
      "sheet": "Notes",
      "title": null,
      "upstream_node_type": "data_table",
      "ontology_role": "unclassified",
      "ontology_subtype": null,
      "node_type_override_proposed": false,
      "in_top_candidates": false,
      "confidence": null,
      "evidence_quality": null,
      "agent_owner": null,
      "applied_heuristics": [],
      "flags": ["low_context_inherited"]
    }
  ],
  "tag_summary": {
    "total_tables": 47,
    "classified_input": 6,
    "classified_calculation": 2,
    "classified_result": 1,
    "classified_overall_evidence": 0,
    "unclassified": 38,
    "node_type_overrides_proposed": 1
  }
}
```

`ontology_role` is the union of the four ontology nodes plus
`unclassified`. Unclassified tables still appear in this file with
`ontology_role: "unclassified"` so the UI can render the complete
table inventory in one view.

### 12.10 `ontology_trace.jsonl` shape

Append-only JSONL. One line per traced event. The UI uses this to
render the step-by-step execution view — every planner context
construction, tool call, executor decision, heuristic firing, LLM
prompt, and LLM response.

Event types and their shapes:

```json
{"ts": "...", "event": "agent_start", "agent": "overall_model", "files_loaded": ["pack_overview.context.md", "ontology_overall_model.context.md", "ontology_overall_model.txt"]}

{"ts": "...", "event": "planner_context_built", "agent": "overall_model", "context_size_chars": 8421, "node_index_summary_size_chars": 1203}

{"ts": "...", "event": "llm_call", "agent": "overall_model", "phase": "planner", "prompt": "...", "prompt_size_chars": 9624, "response": "...", "response_size_chars": 1840, "tokens_used": 2145, "elapsed_ms": 1820}

{"ts": "...", "event": "plan_produced", "agent": "overall_model", "plan_steps": 5, "plan": [...]}

{"ts": "...", "event": "tool_call", "agent": "overall_model", "tool": "find_overview_sheets", "args": {"name_patterns": ["Description", "Cover", "About"]}, "result_summary": "2 sheets matched: Description, Front", "elapsed_ms": 3}

{"ts": "...", "event": "heuristic_triggered", "agent": "overall_model", "heuristic_id": "H-OM-2", "concept": "country", "trigger_state": "lexicon_search_failed"}

{"ts": "...", "event": "heuristic_applied", "agent": "overall_model", "heuristic_id": "H-OM-2", "outcome": "value_inferred", "value": "UK"}

{"ts": "...", "event": "llm_call", "agent": "overall_model", "phase": "concept_synthesis", "prompt": "...", "response": "...", "tokens_used": 612, "elapsed_ms": 940}

{"ts": "...", "event": "concept_recorded", "agent": "overall_model", "concept": "intervention", "value": "Drug X", "evidence_quality": "high", "confidence": 0.92}

{"ts": "...", "event": "concept_absent", "agent": "overall_model", "concept": "perspective", "search_log_size": 3}

{"ts": "...", "event": "table_classified", "agent": "inputs", "table_id": "Costs__t15__c0", "ontology_role": "input", "ontology_subtype": "drug_cost", "confidence": 0.86}

{"ts": "...", "event": "node_type_override", "agent": "inputs", "table_id": "Engine__t3__c0", "from": "calc", "to": "input", "reason": "H-IN-1 matched population pattern"}

{"ts": "...", "event": "defect_procedure_invoked", "agent": "inputs", "table_id": "Costs__t14__c0", "defect_type": "title_missing", "procedure": "10.1"}

{"ts": "...", "event": "defect_procedure_skipped", "agent": "inputs", "table_id": "Costs__t14__c0", "flag_type": "title_missing", "would_have_been_procedure_id": "10.1", "agent_owner": "inputs"}

{"ts": "...", "event": "budget_check", "phase": "seed", "calls_used": 5, "calls_max": 8}

{"ts": "...", "event": "agent_complete", "agent": "overall_model", "status": "complete", "elapsed_seconds": 3.4}

{"ts": "...", "event": "run_complete", "status": "complete", "agents_run": 4, "total_elapsed_seconds": 17.2, "total_llm_calls": 14}
```

**Constraints on trace events:**
- Every `llm_call` event MUST include the full `prompt` and `response`.
  This is non-negotiable — the UI's "Inspect LLM" view depends on it.
- Every `tool_call` event MUST include `args` and a `result_summary`
  (full result optional; for very large results, summarise and reference
  by ID).
- `defect_procedure_skipped` is a first-class event type in v0 — it is
  the only signal of how often each defect type occurs in real
  workbooks, and the data feeds Section 13.6 (defect-frequency
  dashboard). Fields:
  `{ ts, event: "defect_procedure_skipped", agent, table_id, flag_type,
    would_have_been_procedure_id, agent_owner }`.
- Events are written in the order they occur. The UI relies on this
  ordering for step-by-step rendering.
- Each event has a `ts` (ISO-8601 timestamp) and an `event` type.

### 12.11 `node_type_override_log.jsonl` shape

Append-only JSONL. One line per finalize/ontology disagreement on
`node_type`. The writer appends a line whenever an agent records
`node_type_override_proposed: true` on a candidate table — this file
is a **side effect** of existing override logic, not a separate
decision.

```json
{
  "ts": "ISO-8601",
  "table_id": "string",
  "sheet": "string",
  "title": "string or null",
  "upstream_label": "input | calc | output | mixed | data_table | metadata",
  "ontology_label": "input | calculation | result | overall_evidence",
  "ontology_subtype": "string",
  "evidence": [
    { "signal": "string", "value": "any", "matched_term": "string or null" }
  ],
  "confidence": 0.0,
  "applied_heuristic": "string or null"
}
```

Use cases:
- Future finalize ML-classifier training: each row is a labelled example
  where `upstream_label` was wrong (per the ontology layer's structural
  + lexicon evidence).
- UI "Upstream label disagreements" view (Section 13.2).
- Manual spot-checking: filter by `confidence > 0.8` to find the
  high-confidence disagreements worth reviewing first.

---

## 13. Orchestrator Integration

`xl_ontology` runs as a stage inside the orchestrator app. The
orchestrator's UI exposes a dedicated **XL Ontology** page where the
user triggers, monitors, and inspects ontology runs. The contract
between this repo and the orchestrator UI is defined here so Claude CLI
implements the backend in a way the UI can consume directly.

### 13.1 Orchestrator entry points

The orchestrator app must expose the following user actions:

- **An "XL Ontology" page or navigation item.** Distinct from the
  sheetagent and DAG pages. Contains the actions and views below.
- **A "Run XL Ontology" button.** Clicking this triggers the seed
  phase against the **latest available** sheetagent / finalize / DAG
  outputs for the current workbook. The button is disabled if those
  upstream artifacts are missing.
- **A "Re-run with latest upstream" button.** Visible after a previous
  ontology run exists. Re-runs the seed phase against the most recent
  upstream artifacts, overwriting the four required artifacts (Section
  12.1) and resetting trace + agent_outputs.
- **(Optional) An "Enrich" button per unclassified table.** Triggers
  lazy enrichment for that specific table. Updates
  `summary_model_v{N}.json` to a new version, appends to trace, updates
  agent_outputs and table_tags in place.

### 13.2 Required UI views

The orchestrator UI must support six inspection views, each backed by
one of the artifacts in Section 12.1:

| View name | Backed by | Purpose |
|---|---|---|
| **Final output (prose)** | `summary_model_v{N}.json` | Render the four ontology nodes' `summary` fields plus the overall_model `concepts` block as readable prose. The user sees a one-screen narrative answer to "what does this model contain?" |
| **Per-agent output** | `ontology_agent_outputs.json` | One tab per agent (overall_model / inputs / calculations / results). Each tab shows status, evidence_quality, key_decisions, applied_heuristics, and the agent's `human_readable_output`. |
| **All tagged tables** | `ontology_table_tags.json` | A table view (literally — a Streamlit dataframe) of every table_id with columns: sheet, title, upstream_node_type, ontology_role, ontology_subtype, confidence, evidence_quality, applied_heuristics, flags. Filterable by ontology_role and by overrides. Clicking a row opens the table's full classification record from `summary_model.json`. |
| **Inspect run (trace)** | `ontology_trace.jsonl` | A timeline view of the run. Each trace event is a row in chronological order. Expandable rows for `llm_call` events showing the full prompt and full response. Expandable rows for `tool_call` events showing args and result_summary. Filterable by agent and by event type. **Filtering on `defect_procedure_skipped` must be a one-click operation** — these events are the v0 substitute for defect-recovery telemetry and the user must be able to isolate them quickly. |
| **Upstream label disagreements** | `node_type_override_log.jsonl` | A filterable table showing each ontology-vs-finalize override. Columns: `table_id`, `sheet`, `title`, `upstream_label`, `ontology_label`, `ontology_subtype`, `confidence`, `applied_heuristic`, `evidence` (expandable). Lets the user spot-check whether ontology is being too aggressive or too conservative. Future use: export to CSV for the finalize ML labelling pipeline. |
| **Errors** | `ontology_build_errors.jsonl` | Per-table failure records. Each row shows table_id, error reason, and (if available) the partial state at failure. |

### 13.3 What the UI must be able to inspect

For each `llm_call` event in the trace, the user must be able to view:

- The full **prompt** sent to the LLM (including all ontology files
  that were injected — `pack_overview.context.md`,
  `<node>.context.md`, `<node>.txt`, any heuristic chunks)
- The full **response** from the LLM
- Tokens used
- Elapsed time
- Which agent and which phase (`planner`, `concept_synthesis`,
  `defect_procedure`, `summary_synthesis`)

For each `tool_call` event, the user must be able to view:

- The tool name
- The arguments passed
- A summary of the result (full result available on demand for large
  payloads)
- Elapsed time

For each `heuristic_triggered` and `heuristic_applied` event, the user
must be able to view:

- The heuristic ID
- The trigger state
- The concept the heuristic addressed
- The outcome (value_inferred, no_match, etc.)
- A link to the heuristic's source in the relevant
  `ontology_<node>.heuristics.md` file

### 13.4 Backend contract for the orchestrator

For Claude CLI to implement the backend correctly, the following
contract holds:

1. **Atomic artifact writes.** The five required artifacts (Section
   12.1) must be written atomically per stage completion (the JSON
   trio via temp+rename; the JSONL append-only files via per-line
   flush+fsync). A user refreshing the UI mid-run must see either the
   previous run's artifacts or the new run's artifacts, never a
   half-written state.
2. **No UI logic in `xl_ontology`.** The repo produces JSON files
   only. Streamlit rendering, filtering, expansion, and styling live
   in the orchestrator app, not here.
3. **Stable file paths.** All artifacts live under
   `{run_id}/ontology_output/`. The orchestrator can find them by
   `run_id` alone — no manifest required.
4. **Predictable schema.** The orchestrator UI is built against the
   shapes in Sections 12.2, 12.8, 12.9, 12.10, and 12.11. Schema
   changes require a `schema_version` bump and coordination with the UI.
5. **Trace must be loadable progressively.** `ontology_trace.jsonl`
   may grow large for big workbooks. The UI streams it line-by-line
   rather than loading the full file. The repo must write events as
   each occurs (not batched at end of run) so the UI can show progress
   while the run is still in flight.
6. **Final output prose must be self-contained.** The "Final output
   (prose)" view is rendered from `summary_model.json` summaries plus
   `overall_model.concepts` plus `expected_concepts_status`. No other
   artifact is required. This means the validator's summary generation
   (Section 12.6) must produce text that reads well in isolation — no
   references to "see the trace" or "see agent output".

### 13.5 What the orchestrator does NOT need from this repo

- No HTML, no CSS, no Streamlit components. UI is the orchestrator's
  responsibility.
- No real-time streaming protocol (WebSockets, SSE). The trace file
  on disk is the source of truth; the orchestrator polls or tails it.
- No authentication or multi-user concerns. Single-user, single-
  workbook in v0.
- No undo / rollback. A re-run overwrites; older versions are
  preserved only for `summary_model_v{N}.json`.

### 13.6 Defect frequency dashboard

A small UI surface backed by `ontology_trace.jsonl`. For each completed
run, the dashboard counts `defect_procedure_skipped` events grouped by
`flag_type`:

| Defect type | Skipped count |
|---|---|
| `title_missing` | … |
| `header_missing` | … |
| `rowname_missing` | … |
| `multi_core_detected` | … |

This is **the** value the v0 stub procedures deliver. Without it, the
stubs are dead weight; with it, the team can size and prioritise the
future `xl_table_repair` repo on real data — "we ship multi-core repair
first because 38 of 50 BIMs hit it" rather than guessing.

**Implementation note for the orchestrator:** the dashboard is a
read-of-trace-file operation. The repo emits the events; the
orchestrator counts them. No additional artifact is required.

If this dashboard is **not** built, the v0 stub procedures should be
revisited — there is no other consumer of their telemetry, and emitting
events nobody reads is just noise in the trace.

---

## 14. Tools (summary; full list in `xl_ontology_tools.md`)

All tools are read-only and deterministic. No tool calls an LLM except
`ask_llm`.

**Sheet/table discovery**: `find_sheets_by_name_pattern`, `get_first_sheet`,
`list_tables_on_sheet`, `get_table_content`

**Cell-level access**: `get_cells_in_range`, `get_cells_above`,
`get_cells_left`, `get_cell_format`

**Lexicon search**: `search_workbook_lexicon`, `search_named_ranges`

**DAG access**: `get_dag_neighbours`, `get_terminal_nodes`,
`get_high_fanout_nodes`

**Composite (overall_model)**: `find_overview_sheets`,
`list_data_validation_dropdowns`, `extract_currency_symbols`

**LLM tool (budget-enforced)**: `ask_llm`

**Write tools (ontology map only)**: `record_concept`, `record_search_log`,
`record_summary`, `record_classification`, `record_node_type_override`,
`flag_correction`

No tool mutates upstream artifacts.

---

## 15. Out of Scope for v0

- Cross-workbook ontology mapping
- Ontology learning from labelled examples
- Probabilistic ontology (single deterministic classification per table)
- Conflict resolution between two ontologies on the same workbook
- Ontology versioning beyond integer monotone
- UI for reviewing proposed corrections
- Automated upstream feedback (corrections never propagate)
- Heuristic editing in place (heuristics are append-only; deprecate + add
  new instead)

---

## 16. Implementation Plan (to be produced by Claude CLI)

This spec is the input. Claude CLI must produce an implementation plan
covering:

- File layout
- Ontology pack loader (parses `.txt`, `.context.md`, `.heuristics.md`
  with front-matter)
- Tool implementations matching `xl_ontology_tools.md` signatures
- Per-node agent runner (planner-executor-validator) executing Section 9
- Defect procedure **stubs** from Section 10 (real recovery deferred to
  `xl_table_repair`; stubs emit `defect_procedure_skipped` events)
- Heuristic injection mechanism (front-matter trigger matching)
- Budget enforcement from Section 11 (8 calls / seed phase)
- Output writers for all five required artifacts (Section 12.1):
  `summary_model_v{N}.json` (12.2), `ontology_agent_outputs.json`
  (12.8), `ontology_table_tags.json` (12.9), `ontology_trace.jsonl`
  (12.10), `node_type_override_log.jsonl` (12.11), plus
  `ontology_build_errors.jsonl`
- Trace event emitter — every planner call, tool call, executor
  decision, heuristic firing, LLM prompt/response, and budget check
  written to `ontology_trace.jsonl` line-by-line as it occurs (not
  batched at end of run; Section 13.4)
- Atomic artifact writes per stage completion (Section 13.4)
- Orchestrator integration contract (Section 13): all artifacts under
  `{run_id}/ontology_output/` with stable paths; no UI logic in this
  repo; trace must be progressively loadable
- Tests on at least 3 real BIM workbooks, including verification that
  every required artifact is produced and conforms to schema

The implementation plan must be reviewed before coding starts.
