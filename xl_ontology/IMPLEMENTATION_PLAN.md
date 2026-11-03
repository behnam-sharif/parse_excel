# xl_ontology — Implementation Plan (v0)

Status: **Phase A complete (approved). Phase A.1 fix-ups required before
Phase B. Phases B–F awaiting review.**

This plan is downstream of `xl_ontology/spec/spec_xl_ontology_v0.md`
(the spec) and `xl_ontology/packs/bim_v0/` (the BIM v0 ontology pack;
moved from `xl_ontology/spec/ontalogy_bim_docs/` in Phase A). All
section references use the spec's numbering.

**Revision 1–4 history:** four spec revisions were applied after Phase A
completed:
- **REVISION 1** — Defect procedures stubbed only in v0; real recovery
  deferred to a future `xl_table_repair` pre-stage. Seed LLM budget
  drops 20 → 8.
- **REVISION 2** — `search_workbook_lexicon` gains `scope="whole_table"`
  + `match_location` on the Hit shape. Cross-check rules use it.
- **REVISION 3** — `node_type_override_log.jsonl` becomes a sixth
  required artifact (labelled-disagreement corpus for future finalize
  ML training).
- **REVISION 4** — Each agent's cross-check phase runs deterministic
  structural sanity checks (`formula_density`, `dag_fanout`,
  `dag_fanin`) that propose overrides feeding REVISION 3's log.

Phase A.1 (a small fix-up before Phase B) carries the schema +
artifact additions REVISIONS 1 and 3 imply for already-shipped Phase A
code (see §12 Phase A.1).

---

## 1. Understanding check

**What `xl_ontology` is.** A read-only stage that runs after V6 finalize
and the v7 DAG build. It applies a *pluggable* ontology pack (first one:
`bim_v0`) to the workbook and produces a "lightweight ontology map":
which tables play which role in this model family, what concepts are
present, what's missing, and what the model is about overall.

It runs in two phases. The **seed phase** runs once per upload and
classifies the top-N candidates per ontology node, writing
`summary_model_v0.json` plus three companion artifacts. The **lazy
enrichment phase** runs at query time on behalf of `xl_ask`, classifying
specific tables on demand and bumping the version (`v1`, `v2`, …).

**What it does not do.** It does not modify upstream artifacts. It does
not answer user questions (that's `xl_ask`). It does not re-derive
`node_type` from scratch — it trusts upstream as the *starting point*
and only proposes overrides at the ontology layer. It does not repair
defective tables (multi-core / missing title / missing header / missing
row names); it flags them and either skips or sends the **whole** table
to the LLM for ontology-only decisions. It never sends fragments.

**The five required artifacts** (Section 12.1) — written under
`data/output/{run_id}/ontology_output/`:

| File | Consumer |
|---|---|
| `summary_model_v{N}.json` | `xl_ask` runtime; UI "Final output (prose)" view |
| `ontology_agent_outputs.json` | UI "Per-agent output" view |
| `ontology_table_tags.json` | UI "All tagged tables" view |
| `ontology_trace.jsonl` | UI "Inspect run" view |
| `node_type_override_log.jsonl` | UI "Upstream label disagreements" view; future finalize ML training |

Plus `ontology_build_errors.jsonl` for per-table failures that didn't
abort the run (required-on-disk but may be empty).

**Operating doctrine** (from spec + pack):
- Trust upstream `node_type` as the starting set, then cross-check
  using both lexicon (whole-table scope per REVISION 2) and structural
  sanity predicates (REVISION 4); propose ontology-layer overrides
  (never propagated upstream) and log every override to the override
  log (REVISION 3).
- Whole-table LLM only — no fragments.
- One `table_id` → at most one ontology node in v0.
- Three-file pack structure stays separate; `<node>.txt`,
  `<node>.context.md`, `<node>.heuristics.md`. Heuristics injected
  on-demand by trigger match, not loaded eagerly into the planner
  prompt.
- **LLM budget: ≤ 8 calls per workbook in seed phase** (down from 20
  per REVISION 1; defects are stubbed and consume zero LLM calls),
  ≤ 5 per `xl_ask` query in lazy enrichment.
- **Defect procedures are stubs in v0** (spec §10 banner). They emit
  `defect_procedure_skipped` trace events that quantify defect
  frequency for sizing the future `xl_table_repair` pre-stage.
- Atomic artifact writes; trace events emitted line-by-line as they
  occur (not batched at end of run); read-only on upstream JSON and
  the workbook itself.

---

## 2. Open questions

These are ambiguities or apparent inconsistencies in the spec/pack/code
landscape. Each has a default I would adopt if you don't reply, called
out so we can move fast where the call is small.

### 2.0 BLOCKER FOR PHASE E — REAL BIM WORKBOOK (RESOLVE BEFORE PHASE A IF POSSIBLE)

> **This is the #1 thing to decide in the review meeting. Everything
> else in §2 is secondary.**

Phase E (real-LLM integration + first end-to-end run) cannot start
without a real BIM workbook that has run cleanly through `xl_init` →
`xl_parse` → `xl_table` → `xl_sheetagent` (V6 finalize) → `xl_dag`,
producing a `final_table_map.json` and a `dag.json` we can read.

The Explore agent confirmed that no such run currently exists under
`data/output/`. Phases A–D can proceed against synthetic fixtures
without one, but:

- Without a real BIM, every "agent works correctly" claim rests on
  pack-author intuition encoded in synthetic JSON. Trigger predicates,
  prompt sizes, heuristic firing patterns, and budget consumption
  cannot be validated against reality.
- The §11.3 success criteria (≥ 5 input tables classified, exactly 1
  incremental_budget_impact, LLM calls ≤ 14) are unverifiable.
- Shipping v0 without one means the release ships with a known
  unvalidated claim — i.e. v0 is not actually v0, it's a beta.

**What I need from you:**
1. The path (or upload) of a real BIM workbook to use as the canonical
   first case. Ideally well-built (Description sheet, clear inputs /
   engine / results), ~30 tables.
2. Optionally, a second poorly-built BIM and a third with multi-core
   tables (per §11.3 priority list).
3. Confirmation that I can run `xl_init` → … → `xl_dag` on the supplied
   workbook(s), or that you'll do this and hand me the run_id.

**If the answer is "we don't have one yet":** Phases A–D proceed and
Phase E is paused. We do not ship v0 until E completes. §13 R1 echoes
this; §11.3 e2e tests stay stubbed and explicitly marked as such.

### 2.1 Where does the ontology pack live at runtime?
The spec (Section 6) shows packs as `bim_v0/`, but the existing pack
files live under `xl_ontology/spec/ontalogy_bim_docs/` (note the typo
`ontalogy` in the directory name).

**Adopted model: (b) — built-in default plus config override.**

- **Built-in default location:** `xl_ontology/packs/bim_v0/`. This is
  where `bim_v0` ships in the repo. Future packs (`cem_v0`, etc.)
  ship as siblings under the same directory.
- **`packs_root` is config-overridable** via the `xl_ontology` section
  of `config/stages.yaml` (see §10.4). Setting `packs_root:
  /some/user/dir` causes the loader to look for `pack_name` under that
  directory instead. Useful for: pack authors iterating on a working
  copy outside the repo; downstream consumers shipping their own
  packs without forking parse_excel.
- **Resolution rule:** the loader resolves `pack_dir = packs_root /
  pack_name`. If the resolved directory does not contain
  `pack_overview.context.md`, raise `PackNotFound` with the attempted
  path so the user sees exactly where it looked.

**Move details (done in Phase A, not a follow-up):**
1. Move all 13 pack files from
   `xl_ontology/spec/ontalogy_bim_docs/` → `xl_ontology/packs/bim_v0/`.
2. The `spec/` directory keeps `spec_xl_ontology_v0.md` only; the
   misspelt `ontalogy_bim_docs/` directory is deleted in the same
   commit. No follow-up. No alias. No fallback to the old path.
3. Filenames stay identical (`pack_overview.context.md`,
   `ontology_<node>.{txt,context.md,heuristics.md}`).
4. The spec doc references to `ontalogy_bim_docs/` are updated to
   `packs/bim_v0/` in the same commit.

**Phase A test coverage (added to §11.1 / §12 Phase A):**
- `test_pack_loader.py::test_loads_default_packs_root` — loads
  `bim_v0` from the built-in location.
- `test_pack_loader.py::test_loads_custom_packs_root` — copies the
  pack to a `tmp_path`, sets `packs_root=tmp_path`, loads
  successfully. Confirms (b) actually works end-to-end.

### 2.2 `final_table_map.json` `node_type` includes `metadata` — how do we map it?
Spec Section 5 enumerates `input / calc / output / mixed / data_table`.
The actual finalize output (per Explore agent) emits `node_type` values
including `metadata` for orphan-metadata standalones. The
ontology_overall_model node *uses* metadata-tagged tables as
fingerprints (Section 4 of `ontology_overall_model.txt`).

**Default:** treat `metadata` and `data_table` interchangeably for the
overall_model agent's fingerprint search; for the inputs / calcs /
results agents, metadata-tagged tables are not in the starting set
(they only get pulled in via cross-check).

### 2.3 Does finalize actually emit a `rowname_missing` flag?
Spec Section 5.2 lists `title_missing`, `header_missing`,
`rowname_missing`, `multi_core_detected`, `low_context_node` as flags
the agent reads. The Explore agent observed `title_missing`,
`header_missing`, `low_context_node`, and `multi_core_*` in
`source_flags` but did not confirm `rowname_missing`. If finalize
doesn't emit it, the row-name defect procedure (10.3) never fires.

**Default:** implement the procedure as if the flag exists; treat
`row_names == null` as the trigger if the flag is absent. Log the
choice as a build-time decision so we can revisit when finalize is
extended.

### 2.4 Reconciling `ontology_run_log.jsonl` vs `ontology_trace.jsonl`
The tools doc (`xl_ontology_tools.md`) and the heuristics docs refer to
`ontology_run_log.jsonl`. The spec's required artifacts list (12.1)
calls it `ontology_trace.jsonl`. They appear to be the same file under
two names.

**Default:** `ontology_trace.jsonl` (per spec 12.1). Update tool-doc
references to match in a follow-up; do not change the spec.

### 2.5 LLM-budget exhaustion semantics
Section 11 says the seed phase has a hard cap of 20 LLM calls. It does
not specify what `ask_llm` does when the budget is already at zero.

**Default:** `ask_llm` raises `BudgetExhausted` internally; the executor
catches, marks the *current* concept/table as `evidence_quality:
absent` with `search_log` describing the budget skip, emits a
`budget_exhausted` trace event, and continues. The agent does not
abort — the user gets a partial map plus a clear "skipped due to
budget" trail.

### 2.6 `pack_overview.context.md` budget front-matter
Section 11 says budgets are configurable per pack via front-matter in
`pack_overview.context.md`. The current file has no front-matter.

**Default:** if front-matter is absent, fall back to spec defaults
(20 / 5). Schema for the optional front-matter:
```yaml
---
seed_llm_budget_max: 20
enrichment_llm_budget_max: 5
---
```

### 2.7 No example BIM run exists yet under `data/output/`
This is the field-shape-validation half of the §2.0 blocker. The
Explore agent confirmed there are no run directories with both
`v6_sheetagent_output/final_table_map.json` and
`dag_output/dag.json`, so the upstream loaders' field assumptions are
unvalidated against a real run.

**Default:** rely on the dataclass definitions in `xl_sheetagent/` and
`xl_dag/schemas.py` as the contract for Phases A–D. Add an integration
test that exercises a generated synthetic fixture (committed under
`tests/fixtures/`). Phase E is where this gets reconciled against
reality — see §2.0 for the resolution path.

### 2.8 CLI surface
Spec Section 6.4 shows `xl_ontology --ontology bim_v0 ./run_id/` as a
CLI. The codebase convention is `scripts/run_<stage>.py --run-id …`.

**Default:** ship `scripts/run_ontology.py --run-id <id> [--ontology
bim_v0]`. The package's importable entry point is
`xl_ontology.run_ontology(run_id, output_base, config)`, matching the
existing stage convention.

### 2.9 `record_classification` and the one-table-one-role constraint
Section 7.4 forbids a `table_id` from appearing in more than one
ontology node's `candidate_tables`. The four agents run in parallel
(implied) and may both want to claim the same table.

**Default:** agents are run **sequentially in this order** —
`overall_model`, `inputs`, `calculations`, `results`. Each agent
records into a single in-memory map; `record_classification` raises if
a `table_id` is already classified. The agent that lost the race
records `mixed_role_observed` on its own log and does not duplicate
the classification. Sequential is fine for v0 — we're talking about
~4 LLM calls + tool execution per agent, not a perf-critical path.

### 2.10 `get_high_fanin_nodes` is in tools doc but not in spec Section 14
Tools doc lists it (`get_high_fanin_nodes`); spec Section 14 summary
omits it. Treat the tools doc as authoritative since it's the formal
signature list — confirm.

### 2.11 Streamlit orchestrator: new page or tab?
Spec Section 13.1 says "An XL Ontology page or navigation item.
Distinct from the sheetagent and DAG pages." The current orchestrator
is tab-based.

**Default:** add a top-level tab "XL Ontology" in `apps/orchestrator/
app.py` (matching the tab convention) with a sibling viewer module
`apps/orchestrator/ontology_viewer.py` loaded via `importlib.util`,
matching the `v6_viewer.py` / `dag_viewer.py` pattern.

### 2.12 `ontology_id` vs `ontology_version` consistency
Spec example shows `"ontology_id": "bim_v0"` and `"ontology_version":
"0.1"`. Pack files (`<node>.txt`) say `version: 0.2`. Confirm the
intended mapping: `ontology_id` = pack directory name, `ontology_version`
= max version across all node `.txt` files in the pack? Or a separate
pack-level version?

**Default:** `ontology_id` = pack directory name (`bim_v0`).
`ontology_version` = a single value declared in `pack_overview.context.md`
front-matter (Section 2.6). If absent, fall back to the max `version`
across the pack's `<node>.txt` files. Default `0.2` for current state.

---

## 3. Module / file layout

Flat package layout, mirroring parse_excel's stage convention. Subpackages
only where they earn their keep.

```
xl_ontology/
├── __init__.py                    # exports run_ontology, run_enrichment
├── runner.py                      # top-level seed + enrich entry points
├── config.py                      # XlOntologyConfig dataclass + loader
├── schemas.py                     # Pydantic types — see §4
├── pack_loader.py                 # parses .txt / .context.md / .heuristics.md
├── budget.py                      # LlmBudget tracker + BudgetExhausted
├── trace.py                       # JSONL trace writer (line-by-line, atomic)
├── output_writers.py              # writes the four required artifacts
├── llm_client.py                  # thin adapter over xl_sheetagent.llm_dispatch
│
├── upstream/
│   ├── __init__.py
│   ├── final_map.py               # loads + indexes final_table_map.json
│   ├── dag.py                     # loads dag.json + node_index.json
│   ├── formulas.py                # lazy-loads formulas.json
│   └── workbook.py                # WorkbookAccessor wrapper for ontology tools
│
├── tools/
│   ├── __init__.py                # registry of tool name → callable
│   ├── discovery.py               # find_sheets_by_name_pattern, get_first_sheet,
│   │                              # list_tables_on_sheet, get_table_content
│   ├── cells.py                   # get_cells_in_range, get_cells_above,
│   │                              # get_cells_left, get_cell_format
│   ├── lexicon.py                 # search_workbook_lexicon, search_named_ranges
│   ├── dag_tools.py               # get_dag_neighbours, get_terminal_nodes,
│   │                              # get_high_fanout_nodes, get_high_fanin_nodes
│   ├── composite.py               # find_overview_sheets,
│   │                              # list_data_validation_dropdowns,
│   │                              # extract_currency_symbols
│   ├── llm.py                     # ask_llm (budget-enforced)
│   └── writers.py                 # record_concept, record_search_log,
│                                  # record_summary, record_classification,
│                                  # record_parent_group, flag_correction
│
├── agents/
│   ├── __init__.py
│   ├── base.py                    # PlannerExecutorValidator base
│   ├── overall_model.py
│   ├── inputs.py
│   ├── calculations.py
│   ├── results.py
│   └── prompts.py                 # planner / synthesis prompt templates
│
├── defects/
│   ├── __init__.py
│   ├── title_stub.py              # STUB for procedure 10.1
│   ├── header_stub.py             # STUB for procedure 10.2
│   ├── rowname_stub.py            # STUB for procedure 10.3
│   └── multi_core_stub.py         # STUB for procedure 10.4
│   # The `_stub` suffix is intentional (REVISION 1). When
│   # xl_table_repair ships and these files are deleted, the
│   # suffix makes the deprecation obvious in PR diffs.
│
├── packs/
│   └── bim_v0/                    # MOVED from spec/ontalogy_bim_docs/
│       ├── pack_overview.context.md
│       ├── ontology_overall_model.{txt,context.md,heuristics.md}
│       ├── ontology_inputs.{txt,context.md,heuristics.md}
│       ├── ontology_calculations.{txt,context.md,heuristics.md}
│       └── ontology_results.{txt,context.md,heuristics.md}
│
├── spec/                          # unchanged — spec doc + raw pack source
│
└── tests/
    ├── test_pack_loader.py
    ├── test_upstream_loaders.py
    ├── test_tools.py
    ├── test_budget.py
    ├── test_defects.py
    ├── test_agents_<node>.py      # one per agent
    ├── test_output_writers.py
    └── test_e2e_bim.py            # against a real BIM run (when available)
```

Key import contracts:
- Nothing in `xl_ontology` imports anything mutable from upstream stages.
- `runner.py` imports the four agents, the loaders, the writers.
- The tools subpackage knows nothing about agents — agents call tools.
- `agents/base.py` is the only place that knows about planner /
  executor / validator structure; agents subclass it.
- `pack_loader.py` is the only place that parses pack files.
- `output_writers.py` is the only place that touches the on-disk
  artifacts.

---

## 4. Data model

Pydantic v2 throughout. Shapes match the JSON in spec §12 verbatim.
Listed by purpose, not in alphabetical order.

### Pack-loaded types

- **`OntologyPack`** — top-level loaded pack.
  Fields: `id` (str), `version` (str), `pack_dir` (Path),
  `pack_overview_text` (str), `budgets` (`PackBudgets`), `nodes`
  (dict[NodeName, `OntologyNode`]), **`warnings`
  (list[`PackWarning`])** — the loader populates `warnings` for
  non-fatal issues (unknown extra files, etc.); the runner converts
  each into a `WarningEvent` trace entry. See §5.7 / Issue 7.

- **`PackWarning`** — a non-fatal pack-loader issue.
  Fields: `category` (str — e.g. `"unknown_pack_file"`),
  `message` (str), `file_path` (Optional[str]).

- **`OntologyNode`** — one of the four ontology-node bundles.
  Fields: `node_name`, `txt_path`, `context_md_path`,
  `heuristics_md_path`, `txt_text` (str — full body, injected as-is
  into LLM prompts), `context_text`, `identity` (parsed from §1 of
  `<node>.txt` — `ontology_pack`, `ontology_node`, `version`,
  `model_family`, `expected_scale`, `llm_budget_share`),
  `heuristics` (list[`Heuristic`]).

- **`Heuristic`** — a single heuristic chunk.
  Fields: `id`, `applies_to`, `concept`, `trigger`, `priority` (int),
  `last_updated`, `status` (`active|experimental|deprecated|
  subsumed_by_ml`), `body_text` (str — prose body, injected when
  fired).

- **`PackBudgets`** — defaults override (Pydantic class defaults
  match REVISION 1 spec §11 caps).
  Fields: `seed_llm_budget_max` (default **8**, was 20 pre-REVISION 1
  — Item 6 fix),
  `enrichment_llm_budget_max` (default 5). The class defaults must
  agree with `config/stages.yaml` and the spec §11 caps; drift is
  caught by `test_config_loading.py` and `test_pack_loader.py`.

### Per-table classification types

- **`Evidence`** (matches spec §12.3 exactly):
  `sheet`, `cell`, `table_id` (Optional), `snippet`, `match_type`
  (Literal: `lexicon_match | format_match | dropdown_value |
  llm_inferred | cross_reference | structural_fingerprint`),
  `matched_term` (Optional).

- **`SearchLogEntry`** (matches spec §12.4):
  `tool`, `args_summary`, `result_summary`, `hits` (Optional[int]).

- **`EvidenceSignal`** — used inside `candidate_tables` (per spec §12.2
  inputs example): `type` (Literal: `title_match | formula_density |
  dag_fanout | format_match | structural_fingerprint`), `value`,
  `matched_term` (Optional).

- **`ParameterRecord`** — for `extracted_payload.parameters` in input
  candidates: `name`, `value`, `unit`, `source_cell`, `is_formula`,
  `format_role` (Literal: `input_value | derived_value | label |
  header`).

- **`KeyFormulaRecord`** — for calculations
  `extracted_payload.key_formulas`: `cell`, `formula`,
  `depends_on_tables` (list[str]).

- **`ResultMetric`** — for results
  `extracted_payload.result_metrics`: `name`, `values_by_period`
  (dict[str, float]), `unit`, `source_cells` (list[str]),
  `is_formula`, `format_role` (Literal: `headline | subtotal |
  label`).

- **`CandidateTable`** — generic shape used by inputs / calc / results
  candidates. Fields exactly match spec §12.2 examples:
  `table_id`, `ontology_role`, `ontology_subtype`, `confidence`,
  `evidence_quality`, `why_selected`, `evidence_signals`,
  `upstream_node_type`, `node_type_override_proposed`,
  `parent_table_id`, `parent_path`, `sibling_count`,
  `parent_split_appears_artificial`, `applied_heuristics`,
  `extracted_payload` (Union[InputPayload | CalcPayload |
  ResultPayload]), `flags`.

- **`ParentGroup`** (spec §7.3): `parent_table_id`, `parent_title`,
  `members`, `all_in_this_node`, `cross_node_members`.

- **`ConceptRecord`** — used inside `overall_model.concepts`:
  `value`, `evidence` (list[Evidence]), `search_log`
  (list[SearchLogEntry]), `confidence` (Optional[float]),
  `evidence_quality`.

- **`OverallModelNode`**: `present`, `evidence_quality`, `summary`,
  `summary_source`, `concepts` (dict[str, ConceptRecord]),
  `applied_heuristics`, `confidence`.

- **`OntologyClassificationNode`** — generic shape for inputs /
  calcs / results: `present`, `evidence_quality`, `summary`,
  `summary_source`, `candidate_tables` (list[CandidateTable]),
  `parent_groups` (list[ParentGroup]),
  `expected_concepts_status` (dict[str, Literal[`found | partial |
  inferred | absent`]]), `confidence`.

- **`UnclassifiedTable`**: `table_id`, `priority_score`, `reason`.

- **`ProposedCorrection`**: `table_id`, `issue`,
  `proposed_correction`, `confidence`, `evidence`, `consumer`.

- **`LlmBudgetSnapshot`**: `seed_calls_used`, `seed_calls_max`,
  `enrichment_calls_used`.

- **`SummaryModel`** — top of `summary_model_v{N}.json`:
  `model_id`, `ontology_id`, `ontology_version`, `schema_version`
  (str = `"v0"`), `seed_timestamp`, `last_enriched_timestamp`,
  `workbook_file_hash`, `overall_model`, `inputs`, `calculations`,
  `results`, `unclassified_tables`, `proposed_table_corrections`,
  `llm_budget`, `run_notes`.

### Per-agent record types

- **`AgentOutputRecord`** — one entry under `agents.{node}` in
  `ontology_agent_outputs.json` (spec §12.8):
  `status`, `evidence_quality`, `summary`, `summary_source`,
  `concepts_resolved` (Optional — overall_model only),
  `concepts_absent` (Optional — overall_model only),
  `tables_classified` (Optional — others), `node_type_overrides_proposed`,
  `expected_concepts_found`, `expected_concepts_absent`,
  `applied_heuristics`, `tools_called`, `llm_calls_used`,
  `elapsed_seconds`, `key_decisions` (list[str]),
  `human_readable_output` (str — multi-paragraph prose, generated by
  validator from a fixed template, no LLM call).

- **`AgentOutputsFile`**: `model_id`, `ontology_id`, `run_timestamp`,
  `agents` (dict[str, AgentOutputRecord]).

### Per-table tag types

- **`TableTagRecord`** (spec §12.9):
  `table_id`, `sheet`, `title`, `upstream_node_type`,
  `ontology_role` (Literal: `overall_evidence | input | calculation |
  result | unclassified`), `ontology_subtype` (Optional),
  `node_type_override_proposed`, `in_top_candidates`, `confidence`,
  `evidence_quality`, `agent_owner` (Optional), `applied_heuristics`,
  `flags`.

- **`TagSummary`**: `total_tables`, `classified_input`,
  `classified_calculation`, `classified_result`,
  `classified_overall_evidence`, `unclassified`,
  `node_type_overrides_proposed`.

- **`TableTagsFile`**: `model_id`, `ontology_id`, `run_timestamp`,
  `tables` (list[TableTagRecord]), `tag_summary`.

### Override-log type (REVISION 3)

- **`NodeTypeOverrideRecord`** — one line in
  `node_type_override_log.jsonl` (spec §12.11). Fields:
  `ts: str`, `table_id: str`, `sheet: str`, `title: Optional[str]`,
  `upstream_label: Literal[input | calc | output | mixed | data_table | metadata]`,
  `ontology_label: Literal[input | calculation | result | overall_evidence]`,
  `ontology_subtype: str`, `evidence: list[OverrideEvidence]`,
  `confidence: Optional[float]`, `applied_heuristic: Optional[str]`.

- **`OverrideEvidence`** — sub-record:
  `signal: str` (e.g., `"lexicon_match_whole_table"`,
  `"structural_sanity_low_formula_high_fanout"`),
  `value: Any`, `matched_term: Optional[str]`.

### Trace-event types

One discriminated union per `event` field. Matches spec §12.10
exactly. Concrete event classes: `AgentStart`,
`PlannerContextBuilt`, `LlmCall` (the only event that carries the
full `prompt` and `response` strings — non-negotiable), `PlanProduced`,
`ToolCall`, `HeuristicTriggered`, `HeuristicApplied`,
`ConceptRecorded`, `ConceptAbsent`, `TableClassified`,
`NodeTypeOverride`, `DefectProcedureInvoked`,
`DefectProcedureSkipped` (REVISION 1 — first-class event in v0;
fields: `agent`, `table_id`, `flag_type`, `would_have_been_procedure_id`,
`agent_owner`), `BudgetCheck`, `BudgetExhausted` (added per §2.5),
`AgentComplete`, `RunComplete`, `Error`,
**`WarningEvent`** (Issue 7 — emitted for every `PackWarning` and
for any other non-fatal runtime warning; fields: `category`,
`message`, `file_path` Optional, `agent` Optional).

`DefectProcedureInvoked` is retained for forward compatibility — when
`xl_table_repair` ships and the stubs are removed, the future
recovery procedures will emit `DefectProcedureInvoked` events instead
of `DefectProcedureSkipped`.

Each event has `ts: str` (ISO-8601) and `event: str` (literal).

### Error-log type

- **`BuildErrorRecord`** for `ontology_build_errors.jsonl`:
  `ts`, `agent`, `table_id` (Optional), `error_type`, `message`,
  `partial_state` (Optional dict). Append-only JSONL.

### Upstream-loaded types (read-only)

These mirror upstream — we don't redefine them, we type the loaders'
return values. Concretely the loaders return `pydantic.TypeAdapter`-
validated `dict`s plus typed accessor classes:

- **`FinalMapEntry`** (Union[StandaloneEntry | ParentEntry]) and
  **`ChildEntry`** — match the `xl_sheetagent` finalize dataclasses.
- **`DagNode`**, **`DagEdge`**, **`DagFile`** — match
  `xl_dag.schemas`. Imported directly when convenient; mirrored as
  Pydantic where a fresh validator is needed at the upstream boundary.

---

## 5. Ontology pack loader

`pack_loader.py` is the single entry point for reading a pack from
disk. The loader is invoked once per `xl_ontology` run.

### 5.1 Discovery and structural validation

`load_pack(packs_root: Path, pack_name: str) -> OntologyPack`:

1. Resolve `pack_dir = packs_root / pack_name`. Raise
   `PackNotFound` if missing.
2. Require `pack_overview.context.md` to exist. Raise
   `PackMalformed` if missing.
3. For each of the four ontology nodes (`overall_model`, `inputs`,
   `calculations`, `results`), require all three files to exist:
   `ontology_<node>.txt`, `ontology_<node>.context.md`,
   `ontology_<node>.heuristics.md`. Missing → `PackMalformed` with
   the missing-file list.
4. **Unknown extra files surface as `PackWarning`s** (Issue 7
   resolution). For every file in `pack_dir` that is not in the
   expected-files set, the loader appends a `PackWarning(
   category="unknown_pack_file", message=..., file_path=...)` to
   `OntologyPack.warnings`. The runner converts each into a
   `WarningEvent` trace entry (§5.7). The UI displays warnings in
   the Inspect-run view next to errors. The load itself does not
   fail. This replaces the original "log a warning but do not fail"
   text — `WarningEvent` is now the explicit surface, not an
   undefined `logger.warning` call.

### 5.2 Parsing `<node>.txt`

The `.txt` files are not YAML. They use numbered ALL-CAPS section
headers (`1. IDENTITY`, `2. PURPOSE`, …). The body is prose — the
agent's planner gets the **full text injected as-is**. We only parse
the IDENTITY block to extract structured fields:

- `ontology_pack`, `ontology_node`, `version`, `model_family`,
  `expected_scale`, `llm_budget_share`.

Parser approach: read the file, locate the line `1. IDENTITY` (or
similar), take subsequent `key: value` lines until the next
ALL-CAPS-numbered section heading. Validate that
`identity.ontology_pack == pack_name` and
`identity.ontology_node` matches the file basename.

Everything else stays in `txt_text` for prompt injection.

### 5.3 Parsing `<node>.context.md`

Plain markdown. No structural parsing required — load as one string
into `context_text`. The full string is injected into the planner
prompt.

### 5.4 Parsing `<node>.heuristics.md`

The most awkward format in the pack. Each heuristic is a fenced YAML
block followed by markdown body. The fenced block contains a YAML
document delimited by `---`/`---` *inside* the fence:

```text
```yaml
---
id: H-OM-1
applies_to: overall_model
...
---
```

# H-OM-1: Time horizon when no explicit statement
... prose body ...
```

Parser approach:
1. Split file by lines.
2. Find each opening ```` ```yaml ```` fence.
3. The next `---` … `---` block inside the fence is the front-matter
   document — parse with `yaml.safe_load`.
4. After the closing ```` ``` ```` fence, accumulate prose lines
   until the next ```` ```yaml ```` fence or end-of-file. That's
   the body.
5. Build `Heuristic(id=..., applies_to=..., concept=..., trigger=...,
   priority=..., last_updated=..., status=..., body_text=...)`.

Validation:
- All required front-matter keys present.
- `status` in the allowed enum.
- `id` unique within the file.
- Skip (with warning) any heuristic whose `status == "deprecated"` —
  it's loaded but never injected.

### 5.5 Parsing `pack_overview.context.md`

Two steps:
1. Read the optional YAML front-matter (between leading `---` /
   `---` if present at file head). Parse into `PackBudgets`. If
   absent, default to (20, 5).
2. The rest of the file becomes `pack_overview_text`, injected into
   every agent's planner prompt.

### 5.6 Heuristic indexing

After load, build a per-node index:
`{(node_name, trigger): list[Heuristic]}`, sorted by `priority`
ascending. The executor uses this for trigger matching (§7.3).

### 5.7 Failure modes and warnings

All loader **failures** raise typed exceptions surfaced to the
orchestrator: `PackNotFound`, `PackMalformed`, `IdentityMismatch`,
`HeuristicMalformed`, `UnregisteredTrigger`. The orchestrator
displays them in the "Errors" tab and refuses to start the run.

Non-fatal **warnings** are returned via `OntologyPack.warnings`
(Issue 7 resolution). The runner emits one `WarningEvent` trace
entry per warning before dispatching to agents. v0 sources of
warnings:

- `unknown_pack_file` — extra file in the pack directory (§5.1).
- (Future) deprecated heuristic encountered, missing optional
  pack-overview front-matter key, etc.

Warnings do not block the run. The Inspect-run view displays them
chronologically alongside trace events.

---

## 6. Tool implementations

One paragraph per tool. All return Pydantic models (or list[Model]).
All tools take a `ToolContext` (run_id, workbook_path, upstream
loaders, write API) so individual tool functions stay stateless and
testable.

### Sheet/table discovery

- **`find_sheets_by_name_pattern(patterns)`** — case-insensitive
  substring + RapidFuzz partial-ratio (threshold ~80). Reads sheet
  names from the workbook accessor. No upstream dependency.
- **`get_first_sheet()`** — reads workbook accessor's sheet order;
  returns the first.
- **`list_tables_on_sheet(sheet_name)`** — reads
  `final_table_map.json` for that sheet; returns
  `[TableSummary(table_id, title, headers, bbox)]` in
  spatial-top-to-bottom order. Both `standalone` and child entries
  are returned (parent nav-only entries are skipped).
- **`get_table_content(table_id)`** — looks up the entry in the
  loaded final-map index; returns full content: per-cell value via
  `WorkbookAccessor.get_worksheet`, headers, row_names from the
  finalize entry, plus formulas via the formula cache.

### Cell-level access

- **`get_cells_in_range(sheet, range)`** — parses Excel range,
  iterates `ws.cell(r, c)` from the values workbook. Returns list
  of `Cell(sheet, cell, value, is_formula, formula)` — formula
  pulled from the formula cache, not the data_only sheet.
  Edge case: range outside used range → return cells with
  `value=None`. Edge case: invalid range → raise `InvalidRange`.
- **`get_cells_above(table_id, n_rows)`** — reads bbox from
  finalize entry; clamps to `r1 >= 0`; returns rows
  `[r1 - n_rows, r1)` across `[c1, c2]`. Used by 10.1.
- **`get_cells_left(table_id, n_cols)`** — analogous; columns
  `[c1 - n_cols, c1)` across `[r1, r2]`. Used by 10.3.
- **`get_cell_format(sheet, cell)`** — reads openpyxl cell
  attributes via the values workbook: `font.bold`, `fill.fgColor`,
  `border.{top,bottom,left,right}`, `number_format`,
  `data_validation` (looked up from `ws.data_validations`),
  `font.size`, merge state from `ws.merged_cells`. Returns
  `CellFormat`. **Edge case:** the workbook accessor only exposes
  `get_worksheet()` for native openpyxl — confirm read-only access
  patterns don't break anything; values workbook is opened in
  `read_only=False` so this works.

### Lexicon search

- **`search_workbook_lexicon(terms, scope)`** — iterates the
  appropriate cell-set per scope:
  - `text_cells_only`: filter cells where `is_formula == False` and
    `value` is `str`. Hits emit `match_location="body_cell_value"`.
  - `cell_values_and_formulas`: include formula text too. Hits emit
    `match_location="body_cell_value"` or `"formula_text"`.
  - `titles_and_headers_only`: read titles + flattened headers from
    final-map entries; cell coordinates derived from bbox row 0.
    Hits emit `match_location="title"` or `"header"`.
  - `row_labels_only`: read row_names from final-map entries; cell
    coords = (bbox.r1+row_idx, bbox.c1). Hits emit
    `match_location="row_label"`.
  - **`whole_table` (REVISION 2)**: for every table in the workbook,
    search across the union of title + headers (all rows) +
    row_names + body cell values + formula text. Each hit carries
    `match_location ∈ {title, header, row_label, body_cell_value,
    formula_text}`. Used by the cross-check decision rules in each
    pack §6.B/D when the cheap-scope pass missed a concept and the
    agent must look harder before recording absent.
  - `all`: union of the above (including loose cells outside any
    table; loose-cell hits emit `match_location="body_cell_value"`).
  Match: case-insensitive substring. Hits returned with `Hit(sheet,
  cell, table_id, snippet, matched_term, match_location)`. The
  `match_location` field lets agents weight evidence (title >
  header > row_label > body_cell_value > formula_text) and lets
  heuristic priors target specific locations (e.g., H-IN-3 prefers
  row_label / header hits per its updated guidance).
  Edge case: extremely large workbooks — iterate sheet-by-sheet
  with early-exit on `>200` hits per call (the planner doesn't need
  more). For `whole_table` scope, the cap applies per-table to
  prevent any single dense formula sheet from dominating the result
  set.
- **`search_named_ranges(terms)`** — reads `dag.json
  named_ranges`; case-insensitive substring match on name; returns
  `(name, definition)`.

### DAG access

- **`get_dag_neighbours(node_id, direction, depth)`** — BFS from
  `node_id` over `dag.json edges`. `direction ∈ {upstream,
  downstream, both}`. Returns list of `DagNode` (deduplicated, in
  BFS order). Edge case: cycle → visited-set protects.
- **`get_terminal_nodes()`** — `[node for node in dag.nodes if
  fan_out(node) == 0]` (or `<= terminal_threshold`,
  configurable). Used by results agent.
- **`get_high_fanout_nodes(min_fanout)`** — by-source
  edge-count from `dag.json`. Used by inputs agent.
- **`get_high_fanin_nodes(min_fanin)`** — analogous, by-target.

### Composite tools

- **`find_overview_sheets(name_patterns)`** — runs
  `find_sheets_by_name_pattern(name_patterns)`, plus
  `get_first_sheet()`, plus sheets that have at least one
  final-map entry with `node_type ∈ {data_table, metadata}`.
  Deduplicate, preserve sheet order.
- **`list_data_validation_dropdowns(sheets)`** — for each sheet,
  iterate `ws.data_validations.dataValidation` entries; for each,
  expand the `sqref` ranges to individual cells; yield
  `Dropdown(sheet, cell, current_value, allowed_values)`. Edge
  case: list-type validation only — skip date/decimal validation.
- **`extract_currency_symbols(sheets)`** — for each sheet,
  scan `ws.cell.number_format` for symbols `[£$€¥]`, plus
  text-cells beginning with one of those. Returns
  `{symbol: [Cell(sheet, cell, value)]}`. Used by H-OM-2.

### LLM tool (budget-enforced)

- **`ask_llm(payload, ontology_context)`** — wraps
  `xl_sheetagent.llm_dispatch.complete_text`. Behaviour:
  1. Check `LlmBudget.can_call(phase)`; if False, raise
     `BudgetExhausted` (caller catches per §2.5).
  2. Build prompt: concatenate `ontology_context` items, then the
     payload's structured request. Order: `pack_overview.context.md`
     → `<node>.context.md` → `<node>.txt` → optional heuristic
     chunks → payload.
  3. Call `complete_text(client, model, prompt, max_tokens,
     temperature)`. Model + tokens from `XlOntologyConfig`.
  4. On success, increment budget; emit `LlmCall` trace event with
     **full prompt and full response** (spec 13.4 — non-negotiable).
  5. Return `LLMResponse(response, confidence, tokens_used,
     applied_heuristic_ids)`. `confidence` and
     `applied_heuristic_ids` extracted from the response if it
     follows a JSON-with-confidence shape; else None / [].
- The "ontology_context" param mechanism is what would enable
  whole-table-only sending if defect-recovery procedures called
  `ask_llm` directly. **In v0 they don't** — the four defect
  procedures are stubs (REVISION 1, spec §10) and never invoke
  `ask_llm`. The mechanism remains in place because the future
  `xl_table_repair` pre-stage's recovery procedures will use it
  when they reinstate full LLM-backed recovery upstream of
  `xl_ontology`. Item 8 fix.

### Write tools

All mutate only the in-memory ontology map. The map is a single
`SummaryModel` Pydantic instance constructed at run start; tools
mutate via methods on a `SummaryModelBuilder`.

- **`record_concept(concept_name, value, evidence, confidence,
  evidence_quality)`** — appends to
  `summary.overall_model.concepts[concept_name]`. Idempotent: if
  called twice for same concept, the second call merges evidence.
- **`record_search_log(concept_name, search_log_entries)`** —
  appends; only legal when value is None.
- **`record_summary(node, text, source)`** — sets
  `summary.<node>.summary` and `summary_source`.
- **`record_classification(table_id, ontology_node,
  ontology_subtype, ...)`** — appends a `CandidateTable` to the
  appropriate node. Raises `AlreadyClassified` per §2.9 if
  `table_id` already in another node.
- **`record_node_type_override(table_id, sheet, title,
  upstream_label, ontology_label, ontology_subtype, evidence,
  confidence, applied_heuristic)` (REVISION 3)** — appends one
  `NodeTypeOverrideRecord` line to
  `node_type_override_log.jsonl` via `NodeTypeOverrideWriter`
  (§9.7). Called automatically by `record_classification` whenever
  `node_type_override_proposed=True`; agents do not call it
  directly. The two write paths are deliberately coupled so an
  override always lands in the log.
- **`record_parent_group(node, parent_group_entry)`** — appends to
  `summary.<node>.parent_groups`.
- **`flag_correction(table_id, flag_type, evidence)`** — appends
  to `summary.proposed_table_corrections`. Advisory only.

### Structural-sanity helpers (REVISION 4)

These are **not tools** in the Section 14 sense — they are
read-only helpers in `xl_ontology.upstream` that the cross-check
phase calls inline. They live with the upstream loaders because they
derive from upstream artifacts only:

- **`compute_formula_density(table_id) → float`** — counts formula
  cells in the table's bbox (via the formula cache in
  `WorkbookAccessor.is_formula`) divided by total cells in the
  bbox. Returns 0.0 for empty tables. Cached per table_id within a
  single agent run.
- **`get_dag_fanout(table_id) → int`** — looks up the table's
  node_id(s) in `node_index.by_table_id`, sums outgoing edges in
  `dag.json`. (A table with `kind="parent"` returns 0 since
  parents have no DAG nodes — the structural sanity check is moot
  for them.)
- **`get_dag_fanin(table_id) → int`** — analogous, incoming edges.
- **`sheet_name_matches_lexicon(sheet_name, lexicon) → bool`** —
  simple case-insensitive substring match used by the results
  agent's structural check (E. in `ontology_results.txt §6`).

---

## 7. Agent runner

The four ontology agents share a base class and differ only in (a)
which node files they load, (b) which expected-concepts they cover,
(c) whether they generate summary via LLM or template.

### 7.1 Sequential ordering

Run order: `overall_model` → `inputs` → `calculations` → `results`
(per §2.9, to enforce one-table-one-role atomically).

### 7.2 Planner

For each agent:

1. Build planner context, in this order:
   - `pack_overview.context.md` (loaded once per run)
   - `<node>.context.md`
   - `<node>.txt`
   - **Condensed workbook view**: a JSON blob built from
     `node_index.json` plus `final_table_map.json`. Includes:
     - sheet names + sheet → table count
     - per-table: `table_id`, `sheet`, `title`, `node_type`,
       `flags`, fan-in / fan-out, headers (truncated at 5 cols)
     - top 50 lexicon-relevant tokens per sheet (sampled from
       titles + headers + first column)
     This view is built by `agents/base.py` once and cached for the
     run; under ~10–20KB so all four planner calls share it.
2. Call `ask_llm` with `phase="planner"`, expecting a structured
   plan: an ordered list of tool calls. Plan schema (validated
   with Pydantic):
   ```
   { "steps": [
       { "tool": "string",
         "args": { ... },
         "rationale": "string" } ] }
   ```
3. Emit `PlanProduced` trace event.

### 7.3 Executor

For each step in the plan:

1. Look up the tool callable in the registry.
2. Call it with the args; emit `ToolCall` trace event with `args` and
   `result_summary`. Update `agent_state` with the result (see §7.3.1).
3. Evaluate triggers (see §7.3.2). Each trigger fires per-concept,
   not per-agent.
4. For each fired `(heuristic, concept)` pair: dispatch via
   `HeuristicHandler` (see §7.3.3), emit `HeuristicTriggered` and
   `HeuristicApplied`, fold the result back into `agent_state`.
5. The executor never re-plans on its own (per spec 8.3.5). If the
   plan exhausts without finding all expected concepts, the validator
   (§7.4) marks the residue absent.

After the planner's tool steps complete, the executor runs the
**cross-check phase** (driven by each pack's `<node>.txt §6` rules,
not by the planner). The phase has three deterministic sub-steps in
this order:

1. **Lexicon-based cross-check (REVISION 2)** — for each expected
   concept the starting set didn't cover, call
   `search_workbook_lexicon(scope="whole_table")`. Score hits using
   `match_location` priority (title > header > row_label >
   body_cell_value > formula_text). On hit, classify and (if upstream
   label disagrees) propose an override.
2. **Structural sanity checks (REVISION 4)** — apply the agent's
   `<node>.txt §6.C/E` predicates to every candidate table:
   - inputs: `formula_density < 0.20 AND dag_fanout > 5`
   - calculations: `formula_density > 0.50 AND dag_fanin > 0 AND
     dag_fanout > 0`
   - results: `fanout == 0 AND fanin > 1 AND
     (sheet_name_matches_lexicon OR title_matches_lexicon)`
   When the predicate fires AND upstream label disagrees with the
   agent's role, propose an override. These are pure functions over
   upstream artifacts; no LLM, no I/O beyond cached upstream loaders.
3. **Defect-flag dispatch (REVISION 1)** — for each candidate
   carrying an upstream defect flag, invoke the corresponding stub
   procedure (see §8). The stub records a `defect_procedure_skipped`
   trace event and returns `evidence_quality="absent"`. The agent
   continues with whatever metadata exists.

Every override (from sub-step 1 or 2) flows through
`record_classification(node_type_override_proposed=True)`, which
internally calls `record_node_type_override` (REVISION 3) so the
override log line is written. The two write paths are coupled
because both must always happen together — there is no path that
records an override without writing the log line.

`ask_llm` is called only for: (a) inferred-concept synthesis (via an
LLM-fallback HeuristicHandler), (b) the overall_model summary, (c)
the four planner calls. Defect procedures **do not call `ask_llm` in
v0** (per REVISION 1). Per spec 8.3.4 + spec §10 banner.

#### 7.3.1 `agent_state` shape

Single mutable Pydantic model (`agents/base.py:AgentState`) passed by
reference through the agent's lifetime. Concrete fields:

- `agent_name: str` — `"overall_model" | "inputs" | "calculations" |
  "results"`.
- `expected_concepts: list[str]` — populated from `<node>.txt §3` at
  agent start.
- `concept_results: dict[str, ConceptResult]` — keyed by concept name.
  `ConceptResult(value, evidence_list, search_log_list, confidence,
  evidence_quality, source: Literal["pending" | "tool" |
  "heuristic_deterministic" | "heuristic_llm" | "defect_procedure"])`.
  Concepts default to `ConceptResult(value=None, ...,
  evidence_quality="pending")` at agent start.
- `tool_call_log: list[ToolCallRecord]` — every tool call run in this
  agent, with `(tool_name, args, result_summary, hits, concept_in_focus)`.
  `concept_in_focus` is set by the executor when a tool call is run as
  part of a concept-specific search step (the planner's plan step
  carries `for_concept: str | None`).
- `candidate_tables: dict[str, CandidateTable]` — for inputs / calc /
  results agents. Keyed by `table_id`.
- `applied_heuristics: list[str]` — heuristic IDs that fired.
- `flags: list[str]` — workbook-level flags emitted during the run
  (e.g., `two_scenario_structure: not_found`).
- `is_top_priority(table_id) -> bool` — helper for procedure 10.4.

`agent_state.evidence_for(concept) -> list[Evidence]` and
`agent_state.search_log_for(concept) -> list[SearchLogEntry]` are
helper accessors used by the validator.

#### 7.3.2 `TriggerEvaluator` interface

Every trigger string declared in any pack heuristic must map to a
predicate in `agents/base.py:TRIGGER_REGISTRY`. The loader
(§5.4 / §13 R2) refuses to load a pack that references an
unregistered trigger.

**Naming (Issue 2 resolution):** `TRIGGER_REGISTRY` is the **single**
canonical name. There is no separate `KNOWN_TRIGGERS` set — earlier
drafts referenced both, which created a drift hazard. The pack
loader checks `trigger in TRIGGER_REGISTRY`. Phase A.1 fix-up
removed the duplicate symbol from `agents/base.py` and from the
`agents/__init__.py` exports.

```
class TriggerEvaluator(Protocol):
    name: str  # e.g. "lexicon_search_failed"

    def fire_for(
        self,
        agent_state: AgentState,
        last_tool_call: ToolCallRecord | None,
    ) -> list[str]:
        """Return the list of concept names for which this trigger
        is currently satisfied. Empty list = does not fire.
        Multiple concepts fire independently — each gets its own
        heuristic chain."""
```

**Per-concept granularity is mandatory.** A trigger like
`lexicon_search_failed` does not fire as a global agent flag; it
returns `["time_horizon", "country", "intervention"]` if all three of
those concepts had a lexicon search that produced zero hits. The
executor then runs each `(heuristic, concept)` chain independently,
in `(heuristic.priority, concept)` deterministic order.

**Concrete v0 trigger predicates** (seven; one file each in
`agents/triggers/` when Phase C lands them — Phase A ships stubs).
**Issue 1 + Issue 6 resolution:** `cross_check_complete` was dropped
from the registry. H-CA-1 and H-CA-3 now fire per-concept on
`aggregator` and `total_cost_with_intervention` respectively, both
keyed to `not_found_via_lexicon`. There are no agent-scoped triggers
in v0 — the Protocol's `list[str]` return value is always concept
names, never `[""]`. (Item 9 was a holdover request to document
*why* `cross_check_complete` was calculations-only — moot now that
it's gone. The original ordering concern, that aggregator detection
needs the calc starting set in place before it runs, is satisfied
by the executor's cross-check-phase ordering: starting-set selection
happens before lexicon search, which happens before
`not_found_via_lexicon` triggers fire.)

| Name | Fires for concept C when |
|---|---|
| `lexicon_search_failed` | `agent_state.tool_call_log` has a `search_workbook_lexicon` call with `concept_in_focus=C` and `hits=0`, and `concept_results[C].value is None`. |
| `not_found_in_input_starting_set` | Concept C from `inputs.expected_concepts` was searched in tables tagged `node_type=input` (initial pass) with zero matches, and `concept_results[C].value is None`. |
| `weak_lexicon_match` | A `search_workbook_lexicon` call for concept C returned ≥ 1 hit but the top hit's matched-term ratio is < 0.75 (configurable). |
| `lexicon_match_failed` | Alias of `lexicon_search_failed` — kept because the heuristic pack uses both names; both map to the same predicate. |
| `not_found_via_lexicon` | C was searched (any scope) and produced zero hits, OR C was never searched but is on the agent's expected-concepts list and the cross-check phase has run. Used by H-CA-1 (`aggregator`), H-CA-2 (`patient_flow`), H-CA-3 (`total_cost_with_intervention`). |
| `result_table_has_two_scenarios_no_difference` | A candidate result table has rows matching both intervention-scenario and comparator-scenario lexicon, but no row matches incremental lexicon. Fires for concept `incremental_budget_impact`. |
| `standalone_table_with_alternative_assumptions` | A terminal table contains scenario / sensitivity lexicon hits and result-style metrics. Fires for concept `scenario_results`. |

The executor calls `TriggerEvaluator.fire_for(...)` after every
`ToolCall` and after every `HeuristicApplied`. To prevent the same
`(heuristic, concept)` from firing twice, the executor maintains a
`fired_set: set[tuple[heuristic_id, concept]]` per agent run.

#### 7.3.3 `HeuristicHandler` interface

Each heuristic ID may have an optional deterministic handler
registered in `agents/heuristic_handlers/`. If no handler is
registered, the LLM-fallback handler runs (which injects the
heuristic's `body_text` into an `ask_llm` call's `ontology_context`).

```
class HeuristicHandler(Protocol):
    heuristic_id: str  # e.g. "H-IN-3"

    def apply(
        self,
        agent_state: AgentState,
        concept: str,
        tools: ToolRegistry,
    ) -> ConceptResult | None:
        """Run the heuristic for the given concept. Return a
        ConceptResult to record, or None if the heuristic could not
        produce a value (executor falls back to the LLM handler in
        that case, IF budget allows; otherwise concept stays
        pending and the validator records absent)."""
```

A `HeuristicHandler` is "deterministic" if it never calls `ask_llm`
internally. In v0, deterministic handlers exist for:

- **H-IN-3** (market share by row-sum-to-one): walks tables looking
  for percentage-formatted grids whose rows sum to within 1% of
  100%; returns the first match.
- **H-RE-1** (incremental row by formula pattern): walks formulas in
  candidate result tables looking for `=A{r1} - A{r2}` style
  subtractions across adjacent scenario rows.

Other heuristics (H-OM-1 through H-OM-5, H-IN-1, H-IN-2, H-IN-4,
H-CA-1, H-CA-2, H-CA-3, H-RE-2, H-RE-3, H-RE-4) ship without
deterministic handlers in v0; they fall through to the LLM-fallback
handler. As patterns solidify, more deterministic handlers can be
added without changing the trigger evaluator.

The fallback handler is always available and lives in
`agents/heuristic_handlers/llm_fallback.py`:

```
class LlmFallbackHandler(HeuristicHandler):
    def __init__(self, heuristic: Heuristic, llm_budget: LlmBudget):
        ...

    def apply(self, agent_state, concept, tools) -> ConceptResult | None:
        # Build payload referencing concept + agent_state evidence so far.
        # ontology_context = [pack_overview, node.context, node.txt,
        #                     heuristic.body_text].
        # Call ask_llm; parse response into ConceptResult.
        # Returns None on BudgetExhausted (caller emits BudgetExhausted
        # trace event and continues).
```

Registration:
- `agents/heuristic_handlers/__init__.py` exports
  `HEURISTIC_HANDLERS: dict[str, type[HeuristicHandler]]`.
- The pack loader cross-checks at load time that every active /
  experimental heuristic ID is either in `HEURISTIC_HANDLERS` or
  resolved to the LLM fallback. No silent "heuristic loaded but
  unreachable" state.

This is the contract §7.3 step 4 referred to. Adding a new heuristic
is now: (a) write the chunk in `<node>.heuristics.md`, (b) optionally
register a deterministic handler, (c) ensure the trigger string is
registered in `TRIGGER_REGISTRY` (else the loader rejects the pack).

### 7.4 Validator

For each expected concept in the agent's `<node>.txt`:

1. Check `agent_state.evidence_for(concept)`:
   - Has structured Evidence entries → `evidence_quality = high`
     (or `partial` if confidence < threshold from `<node>.txt §9`).
   - Has `llm_inferred` evidence → `evidence_quality = inferred`.
   - Has only search_log entries → `evidence_quality = absent`.
2. Build the `evidence` array for `record_concept` (overall_model)
   or the `extracted_payload` for `record_classification` (others).
3. For absent concepts, build `search_log` from the trace
   ToolCall events emitted while searching for the concept.
4. Apply unknown policy (per `<node>.txt §10`).
5. Generate the agent's summary:
   - **overall_model**: 1 LLM call, prompt assembled per
     `<node>.txt §11`; the response is the summary string. Truncate
     to 3 sentences if longer.
   - **inputs / calculations / results**: pure template per
     `<node>.txt §11`. No LLM call.
6. Build `AgentOutputRecord` (spec §12.8). The
   `human_readable_output` field is generated by templating from
   the structured fields — multi-paragraph, no LLM call. Template
   text lives in `agents/prompts.py`.
7. Write the agent's section into `summary_model` and the agent
   record into `agent_outputs` via writers.

### 7.5 Budget enforcement

`LlmBudget` is a single per-run object passed everywhere. Methods:

- `can_call(phase: Literal["seed", "enrichment"]) -> bool`
- `record_call(phase, tokens_used)`
- `snapshot() -> LlmBudgetSnapshot`

The budget object emits a `BudgetCheck` trace event before every
`ask_llm` call and a `BudgetExhausted` event when refused. The seed
phase shares a single budget across all four agents; the enrichment
phase has a fresh budget per `xl_ask` query.

The per-agent quotas in each `<node>.txt §1.llm_budget_share`
(planner + per-defect counts) are advisory — the executor uses them
to prioritise *which* defect calls to make if budget is tight. They
are not enforced as hard sub-budgets.

### 7.6 Budget accounting (sanity check, not a contract)

This is the expected LLM-call profile for a *typical* well-built BIM
seed run **under the post-REVISION 1 cap of 8 calls**. It is a sanity
check on the §11.3 "≤ 8 calls" target, not a hard contract — actual
runs will vary. With defects stubbed (REVISION 1), the entire defects
column drops to zero; the nominal and worst-case columns converge
because there is no longer a runaway path.

| Agent | Planner | Defects (always 0 in v0) | Inferred concepts via LLM-fallback heuristic | Summary | Subtotal |
|---|---|---|---|---|---|
| `overall_model` | 1 | 0 | up to 2 (e.g. country via H-OM-2 if currency-only; perspective via H-OM-5) | 1 (synthesised) | 2–4 |
| `inputs` | 1 | 0 | 0–1 (e.g. discount_rate via H-IN-2 if pure-LLM) | 0 (template) | 1–2 |
| `calculations` | 1 | 0 | 0–1 | 0 (template) | 1–2 |
| `results` | 1 | 0 | 0–1 (e.g. H-RE-2 if no incremental row and LLM-fallback fires) | 0 (template) | 1–2 |
| **Run total (nominal)** | **4** | **0** | **~3** | **1** | **~8** |
| **Run total (worst case under 8 cap)** | **4** | **0** | **3** | **1** | **8** |

Read this against the new spec §11 seed-phase priority order
(planners → overall_model summary → inferred-concept synthesis): the
nominal column hits ~8 calls, the worst case hits exactly 8. There
is no longer a separate runaway path.

Implications:
- The §11.3 success criterion "LLM calls ≤ 8" matches the nominal
  column. If observed runs come in higher with no defects firing
  (defects can't fire in v0 anyway), the most likely cause is
  heuristic LLM-fallbacks running for too many concepts. That's a
  sign the lexicon is too narrow or the workbook is genuinely thin
  — both are useful signals.
- If ANY agent hits the cap before the planner phase finishes (e.g.,
  three planner calls already done + three inferred-concept LLM
  fallbacks for overall_model would leave 1 call total for inputs +
  calculations + results combined), the budget is too tight. Lift
  `seed_llm_budget_max` in `pack_overview.context.md` front-matter
  rather than removing the cap.
- When `xl_table_repair` ships and the stubs are removed entirely,
  this table stays the same — the recovery LLM calls move to
  `xl_table_repair`'s budget, not back into `xl_ontology`'s.

This table lives in the plan, not in code. Agents do not enforce
per-row caps; the global `LlmBudget` is the only hard cap.

---

## 8. Defect procedures (STUBS in v0 — REVISION 1)

Per spec §10 banner, v0 ships **stubs only**. Real recovery is
deferred to a future `xl_table_repair` pre-stage. The stubs exist
to (a) preserve the architectural seam in the executor so adding
the real procedures later is a drop-in change, and (b) emit
`defect_procedure_skipped` events that quantify defect frequency
in real workbooks (feeds spec §13.6 dashboard).

All four stubs share one signature and one body shape. They live
in `defects/` and total roughly half a day of work (≈ 10 lines of
Python each plus a shared helper).

### Shared stub signature + body

```text
def <stub_name>(table_id, agent_state, upstream, trace) -> DefectOutcome:
    flag_type = "<title_missing|header_missing|rowname_missing|multi_core_detected>"
    procedure_id = "<10.1|10.2|10.3|10.4>"
    trace.emit(DefectProcedureSkipped(
        ts=now_ts(),
        agent=agent_state.agent_name,
        table_id=table_id,
        flag_type=flag_type,
        would_have_been_procedure_id=procedure_id,
        agent_owner=agent_state.agent_name,
    ))
    return DefectOutcome(
        value=None,
        evidence=[],
        confidence=None,
        evidence_quality="absent",
        flags_added=["defect_procedure_disabled_in_v0"],
    )
```

The stub does **NOT** call `ask_llm`. The stub does **NOT** read the
workbook. The stub does **NOT** implement any rule-based recovery.

### 8.1 `recover_title_stub(...)` — procedure 10.1

`flag_type="title_missing"`, `procedure_id="10.1"`. Caller is the
executor's defect-flag dispatch sub-step (§7.3 cross-check phase).

### 8.2 `recover_header_stub(...)` — procedure 10.2

`flag_type="header_missing"`, `procedure_id="10.2"`.

### 8.3 `recover_rownames_stub(...)` — procedure 10.3

Trigger: finalize flag `rowname_missing` OR `row_names is None` in
the entry (per §2.3). `procedure_id="10.3"`.

### 8.4 `resolve_multi_core_stub(...)` — procedure 10.4

Trigger: finalize source_flags contain a multi_core marker.
`procedure_id="10.4"`. The agent additionally adds the table to
`unclassified_tables` with `reason="defect_procedure_disabled_in_v0"`
because no other recovery path exists for multi-core tables in v0.

### 8.5 No-defect path

When no defect flag fires, the executor extracts deterministically
per the ontology's extraction schema. No stub invoked. No LLM call.

### 8.6 When `xl_table_repair` ships

The four stubs are deleted. The executor's defect-flag dispatch
sub-step becomes a no-op (no defect flags reach `xl_ontology`
because `xl_table_repair` repairs them upstream). The
`defect_procedure_skipped` event type becomes obsolete; the
`defect_procedure_invoked` type remains and is emitted by the
new pre-stage's procedures.

---

## 9. Output writers

Located in `output_writers.py`. One function per artifact. All
writes are atomic: write to `path.tmp`, `os.replace(path.tmp,
path)`. Atomicity satisfies spec 13.4.1 (UI mid-run safety).

### 9.1 `write_summary_model(summary, run_id, version) -> Path`

Serialises the `SummaryModel` Pydantic instance to JSON with
`indent=2`, sorted keys. Writes to
`ontology_output/summary_model_v{version}.json`. The `version` is
controlled by the runner: seed phase always writes `v0`; enrichment
bumps to `v1`, `v2`, etc. The runner reads existing files and
picks `max(versions) + 1` when enriching.

### 9.2 `write_agent_outputs(agent_outputs_file, run_id) -> Path`

Single atomic write. Always overwritten. Path:
`ontology_output/ontology_agent_outputs.json`.

### 9.3 `write_table_tags(table_tags_file, run_id) -> Path`

Single atomic write. Always overwritten. Path:
`ontology_output/ontology_table_tags.json`. Built by walking the
final-map for every `table_id` and looking up the in-memory
classification record.

### 9.4 `_JsonlWriter` base + `TraceWriter` (Item 10 — shared base)

All three JSONL writers (`TraceWriter`, `BuildErrorWriter`,
`NodeTypeOverrideWriter`) share a single `_JsonlWriter` base in
`xl_ontology/trace.py`. The base owns the file handle, mode
(`'w'` vs `'a'`), parent-dir creation, and `emit(event)` semantics
(JSON-encode → write → flush → fsync). Per-event flush is what
makes the on-disk artifact progressively loadable from the UI side
(spec §13.4.5). The three concrete classes are thin subclasses
that exist mostly for type clarity at call sites.

**`TraceWriter`** owns `ontology_output/ontology_trace.jsonl`.
Methods:

- `__init__(path, append=False)` — open in `'a'` mode for
  enrichment runs (append-only per spec 13.4); `'w'` for seed.
- `emit(event_obj)` — inherited from `_JsonlWriter`; serialises
  the event Pydantic model to a JSON line, writes + flushes +
  fsyncs. Per-event flush is non-negotiable.

The seed-phase runner calls `TraceWriter(path, append=False)` at
start, emits `RunStart`, runs all four agents, emits `RunComplete`,
closes the writer.

The enrichment-phase runner opens with `append=True` and emits the
new events; the existing seed trace is preserved.

### 9.5 `BuildErrorWriter`

Subclass of the `_JsonlWriter` base introduced in §9.4. Writes to
`ontology_output/ontology_build_errors.jsonl`. Always created (may
be empty) so the UI Errors tab has a stable file to read. Same
per-event flush + fsync semantics as `TraceWriter`.

### 9.6 Path helpers (in `core/paths.py`)

Add:
- `get_ontology_output_path(run_id, base) -> Path`
- `get_summary_model_path(run_id, version, base) -> Path`
- `get_agent_outputs_path(run_id, base) -> Path`
- `get_table_tags_path(run_id, base) -> Path`
- `get_ontology_trace_path(run_id, base) -> Path`
- `get_ontology_build_errors_path(run_id, base) -> Path`
- **`get_node_type_override_log_path(run_id, base) -> Path` (REVISION 3)** —
  resolves to `ontology_output/node_type_override_log.jsonl`.

These match the existing `get_v6_*_path` and `get_dag_*_path`
patterns. Phase A.1 (§12) adds the override-log helper to the
already-shipped Phase A code.

### 9.7 `NodeTypeOverrideWriter` (REVISION 3)

Subclass of the `_JsonlWriter` base introduced in §9.4. Writes to
`ontology_output/node_type_override_log.jsonl`. Per-line
flush + fsync (inherited). Always created (may be empty if no
overrides occurred). Append-only across enrichment rounds (the
seed phase opens with `append=False`; enrichment opens with
`append=True`).

The writer is wired into `record_classification`: whenever a
candidate sets `node_type_override_proposed=True`, the builder
also constructs a `NodeTypeOverrideRecord` and the writer emits a
line. Agents do not call the writer directly — the coupling lives
in the builder so an override cannot be recorded without writing
the log line.

---

## 10. Orchestrator integration

Two surfaces: package boundary + Streamlit page.

### 10.1 Package boundary

`xl_ontology/__init__.py` exports:

- **`run_ontology(run_id: str, output_base: str = "data/output",
  config: XlOntologyConfig | None = None) -> OntologyRunResult`** —
  the seed phase entry point. Symmetric with `run_v6_analysis` and
  `run_dag_analysis`.
- **`run_enrichment(run_id, table_ids: list[str], output_base,
  config) -> OntologyRunResult`** — lazy enrichment for the named
  tables. Bumps version, appends to trace, updates agent_outputs
  and table_tags.
- **`expand_node(run_id, ontology_node, concept, output_base,
  config) -> OntologyRunResult`** — expands a node beyond the
  top-N seed candidates. Uses the cross-check rule to find more
  candidates for the named concept.
- **`OntologyRunResult`** dataclass: `run_id`, `version_written`,
  `artifacts: list[Path]`, `errors: list[str]`,
  `llm_calls_used`, `elapsed_seconds`.

Error contract: any failure raises
`OntologyRunError(reason, partial_artifacts)`. The orchestrator
catches and renders. Builder-error-writer events do *not* raise —
they're per-table failures the run survives.

### 10.2 Pipeline runner

`scripts/run_ontology.py` — symmetric with `run_dag.py`. Args:
`--run-id`, `--ontology bim_v0` (default `bim_v0`), `--output-base`
(default `data/output`).

`scripts/run_pipeline.py` — extend to accept `--with-ontology`
(implies `--with-dag` and `--with-sheetagent`); when set, run
`run_ontology` after `run_dag`.

### 10.3 Streamlit page

Add a tab "XL Ontology" in `apps/orchestrator/app.py`. The tab:

1. Shows **prereq state**: green/red indicators for v6 finalize and
   DAG outputs being present. Disables the run button if either is
   missing.
2. **"Run XL Ontology"** button — calls
   `xl_ontology.run_ontology(run_id, ...)` in a
   ThreadPoolExecutor (matches existing convention). Shows live
   progress by tailing `ontology_trace.jsonl`.
3. **"Re-run with latest upstream"** button — same call, but
   first deletes the previous seed-phase artifacts (preserves
   summary_model_v{N>0}.json — those are versioned per §3.3).
4. The six UI views (spec §13.2) are loaded by a sibling viewer
   module `apps/orchestrator/ontology_viewer.py` via
   `importlib.util`, matching the v6_viewer pattern. The viewer
   reads the five required artifacts and renders:
   - **Final output (prose)** — render `summary_model.json`
     summaries + concepts.
   - **Per-agent output** — one sub-tab per agent;
     `ontology_agent_outputs.json`.
   - **All tagged tables** — `st.dataframe` of
     `ontology_table_tags.json`, filterable.
   - **Inspect run (trace)** — streamed JSONL render with
     expandable rows for `LlmCall` (showing full prompt + response)
     and `ToolCall`. **One-click filter for
     `defect_procedure_skipped`** events (spec §13.2 explicit
     requirement).
   - **Upstream label disagreements (REVISION 3)** — `st.dataframe`
     of `node_type_override_log.jsonl` with columns: `table_id`,
     `sheet`, `title`, `upstream_label`, `ontology_label`,
     `ontology_subtype`, `confidence`, `applied_heuristic`. Expand
     row to see `evidence`. Export-to-CSV button for the future
     finalize ML labelling pipeline.
   - **Errors** — `ontology_build_errors.jsonl` row view.

5. **Defect frequency dashboard** (spec §13.6) — small surface above
   the Inspect-run view. Counts `defect_procedure_skipped` events
   from `ontology_trace.jsonl`, grouped by `flag_type`. Static read
   per refresh; no aggregation in `xl_ontology` itself.

The orchestrator does no business logic — it only renders the five
JSON artifacts plus the trace-derived dashboard count.

### 10.4 Configuration

Add `xl_ontology:` section to `config/stages.yaml`. Initial fields:

- `pack_name` (default: `bim_v0`)
- `packs_root` (default: `xl_ontology/packs` — the built-in location;
  overridable to any user-supplied directory per §2.1's (b) model;
  the loader resolves `pack_dir = packs_root / pack_name`)
- `seed_llm_budget_max` (default: 8 per REVISION 1; falls back from
  pack front-matter. Phase A.1 fix-up: change the value in
  `config/stages.yaml` from 20 to 8.)
- `enrichment_llm_budget_max` (default: 5)
- `llm_model` (default: `gpt-4o-mini`)
- `llm_max_tokens` (default: 4096)
- `llm_temperature` (default: 0.0)
- `dag_terminal_threshold` (default: 0; nodes with fan-out ≤
  threshold are "terminal")
- `parallelism` (default: 1; sequential per §2.9)

Loaded via `core/stage_config.py:ontology_config_from_pipeline()`
matching the existing pattern. Strict exact-key matching — unknown
keys raise per the project's "YAML is authority" doctrine.

---

## 11. Test strategy

Three layers: unit, component, end-to-end.

### 11.1 Unit tests (fast, no I/O)

- `test_pack_loader.py` — load the shipped `bim_v0` pack; verify
  identity matches; verify all heuristics parse with correct front-
  matter; verify deprecated heuristics are skipped from the index;
  verify every heuristic's `trigger` is in `TRIGGER_REGISTRY` (per
  §13 R2). Includes `test_loads_default_packs_root` (built-in
  location) AND `test_loads_custom_packs_root` (copy pack to
  `tmp_path`, set `packs_root=tmp_path`, load — proves §2.1 (b)
  works). Plus `TestPerConceptCalculationsHeuristics` (Issue 1
  resolution: confirms H-CA-1 fires on `aggregator` and H-CA-3
  fires on `total_cost_with_intervention`; both via
  `not_found_via_lexicon`; no heuristic still uses
  `cross_check_complete`). Plus `TestPackWarnings` (Issue 7
  resolution: extra files in pack dir surface as `PackWarning`
  entries with `category="unknown_pack_file"`). Negative cases:
  missing file, bad front-matter, identity mismatch, unregistered
  trigger.
- `test_budget.py` — counter increments, `can_call` semantics,
  exhaustion behaviour, snapshot shape.
- `test_defects.py` — each STUB with a synthetic `agent_state`
  fixture; verify it (a) emits exactly one
  `defect_procedure_skipped` event with the correct
  `would_have_been_procedure_id`, (b) returns
  `DefectOutcome(value=None, evidence_quality="absent",
  flags_added=["defect_procedure_disabled_in_v0"])`, (c) does NOT
  call `ask_llm` (mocked, asserted-not-called).
- `test_output_writers.py` — atomicity (`tmp` then rename),
  versioning logic for `summary_model_v{N}.json`, idempotent
  overwrite for `agent_outputs` and `table_tags`, append for
  trace and build_errors, and append for the new
  `node_type_override_log.jsonl` (REVISION 3).

### 11.2 Component tests (against synthetic fixtures)

- `test_upstream_loaders.py` — build a synthetic
  `final_table_map.json` + `dag.json` + `node_index.json` +
  `formulas.json` (committed under `tests/fixtures/`) covering:
  one standalone, one parent with two children, one multi-core
  flagged table, one orphan-metadata table, one named range, two
  cross-sheet edges, one circular edge. Verify each tool returns
  expected results against this fixture.
- `test_tools.py` — exercise each tool against the synthetic
  fixture above plus a tiny generated `.xlsx` (created via
  openpyxl in `conftest.py`).
- `test_agents_<node>.py` — one test per agent: feed a synthetic
  workbook + upstream artifacts, mock the planner LLM to return a
  fixed plan, run the executor + validator, assert on the
  produced `OverallModelNode` / `OntologyClassificationNode`.
  Heuristic firing covered with a parameterised test per
  heuristic.
- **`test_structural_sanity.py` (REVISION 4)** — one test per
  predicate, against a synthetic fixture exercising both fire and
  no-fire cases:
  - **inputs**: a table with `formula_density=0.10, dag_fanout=8`
    upstream-tagged `calc` → fires; another with
    `formula_density=0.10, dag_fanout=2` → does not fire (fan-out
    too low); another with `formula_density=0.05, dag_fanout=10`
    upstream-tagged `input` → does not propose override (already
    correct).
  - **calculations**: a table with `formula_density=0.70,
    dag_fanin=2, dag_fanout=2` upstream-tagged `data_table` →
    fires; another with `formula_density=0.70, dag_fanin=0,
    dag_fanout=2` → does not fire (no fan-in).
  - **results**: a terminal table on a "Results" sheet
    upstream-tagged `mixed` → fires; another terminal table on
    "Notes" sheet → does not fire (sheet name + title both miss
    lexicon).
  Each test asserts that the override (when fired) is also written
  to `node_type_override_log.jsonl` via the coupled
  `record_classification` path.
- **`test_lexicon_whole_table.py` (REVISION 2)** — fixture has a
  table whose row label contains "drug cost" but whose title does
  not. `scope="titles_and_headers_only"` returns 0 hits;
  `scope="whole_table"` returns 1 hit with
  `match_location="row_label"`. Confirms the new scope works and
  the field is populated.

### 11.3 End-to-end tests (real BIM workbooks)

**Blocker — see §2.0.** No real BIM workbook lives in the repo yet;
without one, this section's success criteria are aspirational. Phases
A–D do not depend on it; Phase E does. What I'd want, in priority
order:

1. A canonical well-built BIM with a Description sheet, clear
   inputs / engine / results sheets, ~30 tables. End-to-end
   success criterion: all four nodes produce summaries with
   `evidence_quality ∈ {high, partial}`, ≥ 5 input tables
   classified, exactly 1 incremental_budget_impact result table
   identified, **LLM call count ≤ 8 per the new budget cap
   (REVISION 1)**.
2. A poorly-built BIM with no Description sheet, scattered
   inputs, ambiguous results. Success criterion: heuristics fire
   (H-OM-2 currency inference, H-IN-1 population-in-calc,
   H-CA-2 patient_flow embedded, H-RE-2 missing incremental row);
   at least 2 `node_type_override_proposed: true` records appear
   in `node_type_override_log.jsonl` (REVISION 3); structural
   sanity checks (REVISION 4) propose at least one override that
   the lexicon cross-check missed; **LLM budget approaches but
   does not exceed 8 (REVISION 1)**.
3. A defective BIM with at least one multi-core table marked
   upstream. Success criterion: stub 10.4 fires (REVISION 1);
   `defect_procedure_skipped` events appear in
   `ontology_trace.jsonl` with `would_have_been_procedure_id="10.4"`
   for every multi-core table; the §13.6 dashboard (when built)
   shows non-zero counts for `multi_core_detected`.

Manual inspection steps per real-workbook test:
- Open `summary_model_v0.json` and verify the overall_model
  summary reads sensibly.
- Open the `ontology_table_tags.json` table view in Streamlit and
  scan for surprising classifications.
- Open the trace view and confirm every `LlmCall` shows full
  prompt + response.
- Confirm every "absent" concept has a non-empty `search_log`.

If no real BIM workbook is supplied for testing, the e2e tests stay
stubbed against synthetic fixtures and v0 does not ship — see §2.0
for the resolution path.

### 11.4 What's intentionally not tested in v0

- Cross-pack behaviour (only `bim_v0` exists).
- Concurrent runs against the same `run_id` (single-user assumed
  per spec §13.5).
- Performance / large-workbook stress.

---

## 12. Implementation order

Seven phases (was six; Phase A.1 added by the four revisions). Each
is ~1 day of focused work with natural review checkpoints between
phases.

### Phase A — Foundations (1 day) — COMPLETE
Moved all 13 pack files into `xl_ontology/packs/bim_v0/`; renamed
the typo'd source directory and deleted it; added paths /
config / schemas / pack loader / budget / trace / output writers /
agent registries. 282 tests passing (49 new + 233 existing).

### Phase A.1 — Revision fix-ups (partially done; ~0.25 days remaining)

Strict fix-ups required by REVISIONS 1 + 3 and Issues 1 / 2 / 6 / 7
before Phase B starts.

**Done in the issue-fix-ups pass (this turn):**
- Issue 1: refactored H-CA-1 (`aggregator`) and H-CA-3
  (`total_cost_with_intervention`) to fire per-concept on
  `not_found_via_lexicon`. Removed `cross_check_complete` from the
  registry. New tests in
  `TestPerConceptCalculationsHeuristics`.
- Issue 2: collapsed `KNOWN_TRIGGERS` into `TRIGGER_REGISTRY`.
  `KNOWN_TRIGGERS` no longer exists in `agents/base.py` or in
  `agents/__init__.py` exports; pack loader and tests updated.
- Issue 6: subsumed by Issue 1 — no agent-scoped triggers in v0.
- Issue 7: added `PackWarning` to `OntologyPack` and `WarningEvent`
  to the trace event types. Pack loader populates warnings for
  unknown extra files. New tests in `TestPackWarnings`.

**Still TODO before Phase B:**
- **REVISION 1 (budget cap):** edit
  `config/stages.yaml xl_ontology.seed_llm_budget_max: 20 → 8`. Add
  a regression test in `xl_ontology/tests/test_config_loading.py`.
- **REVISION 1 (defect-skipped event type):** `DefectProcedureSkipped`
  was added to `schemas.py` in this turn; still need a smoke test
  that round-trips one through `TraceWriter`.
- **REVISION 3 (override log):**
  - Add `get_node_type_override_log_path` to `core/paths.py`.
  - Add `NodeTypeOverrideRecord` and `OverrideEvidence` Pydantic
    types to `xl_ontology/schemas.py`.
  - Add `NodeTypeOverrideWriter` to `xl_ontology/trace.py` (it
    shares the `_JsonlWriter` base).
  - Add a path test (`test_paths.py`) and a writer test
    (`test_output_writers.py::TestNodeTypeOverrideWriter`).
- Update `tests/test_smoke.py` `test_xl_ontology_phase_a_imports`
  to cover the override-log types and writer (the
  `WarningEvent` / `PackWarning` symbols are already covered after
  this turn).

**Review checkpoint after the remaining TODO**: smoke tests +
ontology test suite green; total test count up by ~3 from current
60 (≈63); no behavioural changes elsewhere.

### Phase B — Upstream loaders + tools (1.5 days)
- `upstream/final_map.py`, `upstream/dag.py`, `upstream/formulas.py`,
  `upstream/workbook.py` — read-only adapters around upstream
  artifacts and `WorkbookAccessor`.
- `upstream/sanity_helpers.py` (REVISION 4) —
  `compute_formula_density`, `get_dag_fanout`, `get_dag_fanin`,
  `sheet_name_matches_lexicon`. Cached per agent run.
- All read-only tools: `discovery.py`, `cells.py`, `lexicon.py`
  (with the new `whole_table` scope + `match_location` field per
  REVISION 2), `dag_tools.py`, `composite.py`. Tests against
  synthetic fixtures (§11.2). Includes
  `test_lexicon_whole_table.py`.
- `tools/llm.py` (`ask_llm` with the new 8-call budget enforcement,
  full prompt/response trace emission).
- `tools/writers.py` (write tools mutating `SummaryModelBuilder`;
  `record_classification` internally calls
  `record_node_type_override` when `node_type_override_proposed=True`).
**Review checkpoint**: every tool returns the documented shape;
synthetic-fixture tests pass; whole_table scope returns
match_location correctly.

### Phase C — Agents (~1.5 days, was 2 days; reduced by REVISION 1)
- `agents/base.py` — planner / executor / validator skeleton,
  trigger evaluator (the 8 trigger predicates), heuristic injection
  mechanism, **cross-check phase** (REVISIONS 2 + 4: lexicon
  whole-table → structural sanity → defect-stub dispatch, in that
  order).
- `agents/prompts.py` — planner prompt template, summary prompt
  template, human_readable_output templates per agent.
- `agents/overall_model.py`, `inputs.py`, `calculations.py`,
  `results.py` — each is thin (90% of behaviour in `base.py`).
- **`defects/title_stub.py`, `header_stub.py`, `rowname_stub.py`,
  `multi_core_stub.py` (REVISION 1)** — each is ~10 lines: emit
  `defect_procedure_skipped`, return `DefectOutcome` per §8 stub
  contract. No LLM, no recovery. Total: half a day for all four
  (down from ~1 day for the full procedures).
- Tests per agent (§11.2 `test_agents_<node>.py`).
- `test_structural_sanity.py` (REVISION 4) and `test_defects.py`
  (REVISION 1 stub semantics) covered in §11.1 / §11.2.
- Override coupling test: assert that any `record_classification`
  with `node_type_override_proposed=True` writes a line to
  `node_type_override_log.jsonl`.
**Review checkpoint**: each agent end-to-ends against a synthetic
workbook + fixed-plan LLM mock; defect stubs always emit
`defect_procedure_skipped`; structural sanity overrides land in
the override log.

### Phase D — Runner + orchestrator wiring (1 day)
- `runner.py` — `run_ontology`, `run_enrichment`, `expand_node`.
- `scripts/run_ontology.py` CLI.
- `scripts/run_pipeline.py` `--with-ontology` flag.
- `apps/orchestrator/app.py` "XL Ontology" tab; sibling
  `ontology_viewer.py` rendering all **six** UI views (REVISION 3
  added the override-disagreements view; spec §13.6 dashboard sits
  above the trace view).
- Add `tests/test_smoke.py` import for `xl_ontology`.
**Review checkpoint**: full pipeline runs against synthetic fixture
with no real LLM (config flag bypasses); **all five required
artifacts written** (REVISION 3 added the override log).

### Phase E — Real-LLM integration + first BIM (1 day)
**GATED on §2.0 resolution.** Do not start until a real BIM workbook
exists with a complete upstream run (`final_table_map.json` +
`dag.json`) under `data/output/<run_id>/`.
- Wire real OpenAI client via `xl_sheetagent.llm_dispatch`.
- Run against the first real BIM workbook supplied per §2.0.
- Walk through each artifact in the orchestrator UI; tighten
  prompt templates and trigger predicates based on observed
  behaviour. Inspect the §13.6 dashboard for defect-skip
  frequencies — feed back to `xl_table_repair` sizing
  conversations.
- Compare actual LLM-call profile against §7.6's nominal column;
  **if observed >8 under nominal conditions**, escalate per §7.6's
  notes (likely cause: heuristic LLM-fallback over-firing).
- Verify budget enforcement under real load.
**Review checkpoint**: a real BIM workbook produces a sensible
`summary_model_v0.json`; trace contains every LLM prompt/response;
LLM calls ≤ 8 (the new cap per REVISION 1); override log contains
plausible disagreements.

### Phase F — Lazy enrichment + polish (1 day)
- `run_enrichment` and `expand_node` against the same workbook.
- Versioning — verify `summary_model_v1.json` appears, trace and
  override-log append rather than overwrite, table_tags +
  agent_outputs update in place.
- Optional Streamlit "Enrich" button per unclassified table.
- Documentation pass: README, CLAUDE.md additions, ontology pack
  authoring guide referencing §6.3 (append-only heuristics).
**Review checkpoint**: lazy enrichment works end-to-end; versioned
artifacts inspectable in UI.

**Total project estimate:** ~7 working days (was 7; REVISION 1
shaves ~0.5d off Phase C and adds ~0.5d as Phase A.1 — net zero).
The shape changes (one extra phase boundary) but the work doesn't.

---

## 13. Open risks

### R1. Real BIM workbook availability — see §2.0
This risk is elevated to a banner blocker at the top of §2 (§2.0).
It is the single most important decision in the review meeting.
Phases A–D proceed without it; Phase E is gated. Do not treat this
as a footnote.

### R2. Heuristic-trigger evaluation drift
Seven hand-coded trigger predicates live in `agents/base.py` (was
eight; `cross_check_complete` was dropped per Issue 1). If a pack
author adds a new heuristic with a new trigger string, the system
silently never fires it. **Mitigation**: pack-loader validates that
every heuristic's `trigger` is in
`agents/base.py:TRIGGER_REGISTRY` (the single canonical name per
Issue 2; the previous `KNOWN_TRIGGERS` symbol no longer exists),
and raises `UnregisteredTrigger` if not. Adding a new trigger
means a code change *and* a pack change.

### R3. LLM prompts ballooning past context window — DOWNGRADED (REVISION 1)
The largest payload risk in the original plan was procedure 10.4
sending whole tables + all formulas. With multi-core stubbed in v0
(REVISION 1), that path no longer exists. The remaining LLM call
shapes are all small: planner contexts (~20–30KB of pack prose +
condensed workbook view) and inferred-concept synthesis (a few
cells of context per call). Comfortably within `gpt-4o-mini`.
**Mitigation retained but lower priority**: keep the 100KB-per-call
serialised payload cap as a safety net; it should never trigger in
v0. When `xl_table_repair` ships, R3 returns to its original weight
since the recovery procedures will live there instead.

### R4. `node_type` taxonomy mismatch (§2.2)
The spec mentions five `node_type` values; finalize emits
`metadata` too. If the executor's switch on `node_type` doesn't
handle metadata, those tables become invisible. **Mitigation**:
treat any unknown `node_type` as "ineligible for inputs/calc/result
starting set" and surface it in the overall_model agent's
metadata-fingerprint pool. Test fixture covers `metadata`.

### R5. `record_classification` race / sequencing surprises (§2.9)
Sequential ordering avoids a true race, but the **first** agent
(overall_model) does not classify tables — it records concepts. The
**inputs** agent sees an empty classification map and gets first
pick. If a population table is structurally a calc and gets claimed
by inputs, the calculations agent later cannot reclassify. v0 says
this is acceptable. **Mitigation**: include `mixed_role_observed`
flag emission so the loss is visible in the table_tags view; the
spec already designs for this.

### R6. Atomic write on Windows (§2 of project, OS Version: Windows 11)
`os.replace` is atomic on Windows for same-volume targets. As long
as the temp file lives in the same directory as the final, this
holds. **Mitigation**: writer always creates `tmp` siblings of the
target.

### R7. Trace file growth
A real BIM run with the full prompt + response per LLM call could
hit ~1MB of trace. Streamlit row-rendering may chug. **Mitigation**:
viewer paginates by event type and only inflates `LlmCall` rows on
click; the full file is still on disk for debugging.

### R8. Pack-author cycle is not enforced by code
Heuristic append-only is a documentation rule, not a code rule. A
contributor could edit a heuristic body in place. **Mitigation**:
add a simple lint script `scripts/lint_pack.py` that warns when an
existing `id` has a `last_updated` newer than git's last-modified
of the file. v0 ships the warning, not enforcement.

### R9. UI cohesion with v6_viewer / dag_viewer
The orchestrator's three viewers will each have evolved their own
conventions. Adding a fourth that respects spec §13 verbatim may
require refactoring the others, or accepting visual inconsistency.
**Mitigation**: in Phase D, audit existing viewers; if a small
shared helper module emerges naturally, factor it; otherwise live
with cosmetic divergence in v0.

### R10. Stub telemetry without a dashboard is dead weight (REVISION 1)
The defect stubs only earn their keep if the §13.6 dashboard reads
the `defect_procedure_skipped` events and surfaces frequencies.
Without the dashboard, the stubs are noise in the trace and the
v0 → `xl_table_repair` sizing argument never gets data.
**Mitigation**: spec §13.6 explicitly calls this out ("If this
dashboard is not built, the v0 stub procedures should be
revisited"). Phase D builds the dashboard alongside the override
view.

### R11. Override-log + classification coupling (REVISION 3)
`record_classification` and `record_node_type_override` are
deliberately coupled in the builder so an override always lands in
the log. If a future refactor splits them, the corpus will silently
drift incomplete and the future finalize ML-training pipeline will
miss labels. **Mitigation**: a coupling test in `test_tools.py`
asserts that any classification with
`node_type_override_proposed=True` produces exactly one
override-log line.

### R12. Structural sanity false positives (REVISION 4)
The structural predicates are simple and they will fire on
genuinely-correct upstream labels in some cases — e.g., a
formula-heavy input table that does aggregation, or a terminal
node that's actually a temp scratch table. Each false positive
becomes an override-log line that future ML training would treat
as ground truth. **Mitigation**: keep the predicates conservative
in v0 (the thresholds are tight: fan-out > 5, formula-density >
0.50, etc.). In Phase E, audit the override log for obvious false
positives and tighten thresholds before treating the corpus as
training data.

---

## End of plan

The plan is at `xl_ontology/IMPLEMENTATION_PLAN.md`. Open questions
ranked by how much they affect the build:

- **§2.0 — BLOCKER for Phase E:** real BIM workbook. Phases A–D
  (including A.1) proceed without; Phase E cannot start without it.
- **Decisions safe to defer:** §2.2, §2.3, §2.5, §2.6, §2.7, §2.9,
  §2.10 — all have working defaults documented inline.

### New open questions surfaced by REVISIONS 2 + 4

These are the ~2–3 edge cases the user predicted at revision time.

**OQ-R2.1:** for `scope="whole_table"`, what's the canonical
treatment of `formula_text` matches when the formula references a
named range (e.g., `=Population_2025`)? The named-range name itself
may match the lexicon (population). Should the hit have
`match_location="formula_text"` (literal) or
`"named_range_in_formula"` (semantic)? **Default:** literal
`formula_text` with the named-range name in the snippet; agents can
post-process if needed. Cleaner now, harder later. Confirm.

**OQ-R4.1:** the inputs structural-sanity predicate uses
`dag_fanout > 5` as the threshold. Real BIMs vary widely in DAG
density; 5 might be too low for sparse models or too high for dense
ones. **Default:** keep 5 in v0 and audit in Phase E (R12). If the
override log shows obvious false positives in Phase E,
parameterise the thresholds in `XlOntologyConfig` rather than
re-editing pack files.

**OQ-R4.2:** the calculations sanity predicate fires on `data_table`
upstream label but the spec's `node_type` taxonomy treats
`data_table` more like loose metadata than computational state.
Should the override target `data_table` aggressively, or should
calculations only override `input` → `calculation`? **Resolved
(Issue 5):** keep the aggressive default — override both `input`
and `data_table` per the original REVISION 4 spec. Audit in Phase E
per §13 R12. If the override log shows a noisy
`data_table → calculation` override pattern, tighten the predicate
in v0.1 (e.g., add a minimum bbox area gate). v0 ships aggressive.

**OQ-R4.3:** the results sanity predicate uses
`sheet_name_matches_lexicon OR title_matches_lexicon`. What if
neither matches but the table is genuinely a result (orphan-named
sheet, no title)? **Default:** the predicate doesn't fire — these
tables fall back to the lexicon-based cross-check (rule D in
`ontology_results.txt §6`) or stay unclassified. This is intentional
under-coverage in v0; results agents tend to find headlines via
incremental-row lexicon, not structural alone.
