# xl_ontology Refactor Plan — F.1 → F.8

> **Status:** active plan, last updated 2026-05-08.
> **Scope:** restructure xl_ontology so Pass-2 + Pass-3 produce a verified ontology tree that xl_ask Tier-2 can consume directly.
> **Sequencing rule:** one PR per phase, tests stay green between phases, real-LLM verification on `streamlit_run_simple` after F.3 / F.4 / F.5 / F.6.

---

## 1. Why we're doing this

Today's ontology output stops at "tagged table catalogue + Pass-2 narration prose". xl_ask needs more:

- **Stable `concept_id`s** matching `<branch>.<concept>` (e.g. `inputs.market_share`)
- **Comprehensive aliases** per concept (lexical resolution depends entirely on this)
- **Golden tables** per concept — the user-editable parameter table, not the strongest text match
- **Per-table prose** describing structure / formatting / references — quoted verbatim by xl_ask's `answer_directly_from_ontology`
- **Sheet-level ontology** for `dashboard` / `defaults` / `settings` branches
- **Concept-level relationships** (`governs` / `governed_by`) for cross-branch methodology questions
- **Sheet categorization** so xl_ask knows which sheets are intro / input / calc / result / settings / control / default

The path from today to this contract is split into 8 phases. F.1 is foundational (file layout); F.2 fixes lexicon resolution; F.3 changes Pass-1 to multi-tag every potential input/calc/result; F.4 picks golden tables in Pass-2; F.5 OverallAgent two-tier + concept table + sheet categorization; F.6 introduces Pass-3 (Calculation Agent + sheet-conditional Default/Setting agents); F.7 codifies pack versioning (bim_v0 → bim_v1); F.8 parallelizes Pass-2 + Pass-3.

Each phase is independently shippable, behavior-preserving where possible, and improves the ontology output's xl_ask-readiness monotonically.

---

## 2. Confirmed decisions (locked-in scope for F.1 onward)

| # | Decision |
|---|---|
| 1 | **Multi-tag = Option B** — one row per table in `ontology_table_tags.json`, with `ontology_subtypes: [{name, score, source}]` ranked list. Preserves per-table uniqueness; aggregations and CHERI display unaffected. |
| 2 | **`concept_id` stays stable across regenerations.** Composite info (sheet ontology) lives in separate `flags: ["default", "control", ...]` and `subtypes` (ranked list) fields. Debug string `default-subsequent_drug-market_share` is non-canonical, audit-only. |
| 3 | **Sequential Pass-1 precedence**: input → calc → result. Same-role blocks: once a table is tagged input, the calc agent skips it. `default` / `control` flags **annotate** but do **not** block the role tag. |
| 4 | **Sheet types (7):** `intro`, `input`, `calculation`, `result`, `setting` (= dashboard + settings merged), `control` (fan-in=0 fixed-naming sheets), `default`. |
| 5 | **`bim.txt` = verbatim copy of current `pack_overview.context.md`.** No restructuring during F.1. |
| 6 | **Per-concept `.context.md` files dropped.** Today's `ontology_inputs.context.md` / `ontology_calculations.context.md` / `ontology_results.context.md` are removed; their content is absorbed into `bim.txt` if it carries cross-cutting framing, otherwise dropped. |
| 7 | **Folder rename in F.1**: `xl_ontology/agents/` → `xl_ontology/pass1_tag_agents/`. Subsumes the legacy P1.C task. |
| 8 | **Pack versioning (F.7):** packs are immutable once shipped. New version = new directory `packs/bim_v1/`. `pack_name` config switch. `pack_diff.py` shows concept-coverage delta. Frozen-fixture migration test catches regressions. |
| 9 | **Parallelism deferred to F.8.** Sequential through F.1–F.7. |

---

## 3. Target pack layout (after F.1)

```
xl_ontology/packs/bim_v0/
│
├─ bim.txt                                 # verbatim from pack_overview.context.md
│
├─ ontology_bim_concepts/                  # one folder per concept_id
│   ├─ inputs/
│   │   ├─ population/
│   │   │   ├─ lexicon.json
│   │   │   ├─ rules_pass1.md              # Pass-1 detection rules
│   │   │   └─ rules_pass2.md              # Pass-2 golden criteria (stub in F.1, filled in F.4)
│   │   ├─ market_share/                   # same triple
│   │   ├─ drug_cost/                      # same triple
│   │   ├─ epidemiology/                   # same triple
│   │   ├─ subsequent_treatment/           # same triple
│   │   ├─ adverse_event/                  # same triple
│   │   ├─ hospitalization/                # same triple
│   │   ├─ SCT/                            # same triple
│   │   ├─ end_of_life/                    # same triple
│   │   ├─ monitoring/                     # same triple
│   │   ├─ administration/                 # same triple
│   │   ├─ dosing/                         # same triple
│   │   └─ scenario/                       # same triple
│   ├─ calculations/
│   │   ├─ total_cost_with_intervention/
│   │   ├─ total_cost_without_intervention/
│   │   ├─ patient_flow/
│   │   ├─ aggregator/
│   │   ├─ cost_component_calc/
│   │   └─ scenario_calc/
│   └─ results/
│       ├─ budget_impact_total/
│       ├─ budget_impact_by_component/
│       └─ budget_impact_per_patient/
│
└─ ontology_bim_sheets/                    # one folder per sheet type
    ├─ intro/        { lexicon.json, context.md }
    ├─ input/        { lexicon.json, context.md }
    ├─ calculation/  { lexicon.json, context.md }
    ├─ result/       { lexicon.json, context.md }
    ├─ setting/      { lexicon.json, context.md }
    ├─ control/      { lexicon.json, context.md }
    └─ default/      { lexicon.json, context.md }
```

**Concept count:** 13 inputs + 6 calculations + 3 results = 22 concepts × 3 files = 66 concept files.
**Sheet count:** 7 types × 2 files = 14 sheet files.
**Plus** `bim.txt`. **Total 81 files** (vs 13 today). Each is small and focused.

### Content migration (F.1 audit table)

| Current file | F.1 target |
|---|---|
| `pack_overview.context.md` | `bim.txt` (verbatim) |
| `ontology_inputs.txt` §5 LEXICON entries | per-concept `inputs/<concept>/lexicon.json` |
| `ontology_inputs.txt` §3 EXPECTED CONCEPTS list | drives `discover_concepts()` walker (deduplicated against folder names) |
| `ontology_inputs.txt` §4 STRUCTURAL FINGERPRINTS + §6 DECISION RULES + `ontology_inputs.heuristics.md` H-IN-* chunks (per concept) | per-concept `inputs/<concept>/rules_pass1.md` |
| `ontology_inputs.context.md` | dropped (decision #6) |
| `ontology_calculations.*` | mirrored into `calculations/<concept>/` |
| `ontology_results.*` | mirrored into `results/<concept>/` |
| `ontology_overall_model.*` | absorbed into `bim.txt`; OverallAgent is a Pass-2 ReAct stage and reads goal concepts from a constant in `stages/overall.py`, not from a pack file |
| (none today) | NEW `ontology_bim_sheets/<type>/{lexicon.json, context.md}` — written from scratch from BIM conventions |
| (none today) | NEW per-concept `rules_pass2.md` — stub in F.1, filled in F.4 |

---

## 4. Phase plan

Each phase: scope → files touched → schema changes → tests → verification gate.

### F.1 — Pack refactor + folder rename

**Scope:**
- (a) Audit current pack against new layout; produce a coverage matrix (above table).
- (b) Build `ontology_bim_concepts/{branch}/{concept}/{lexicon.json, rules_pass1.md, rules_pass2.md}` and `ontology_bim_sheets/{type}/{lexicon.json, context.md}` directory trees under `bim_v0/`.
- (c) Migrate content from existing flat files (lexicons → JSON, rules + heuristics → `rules_pass1.md`).
- (d) `bim.txt` = verbatim copy of `pack_overview.context.md`.
- (e) Drop per-concept `.context.md` files.
- (f) Replace `pack_loader.py`'s fixed-file enumeration with a directory walker. Output shape (`OntologyPack`) stays compatible with Pass-1/Pass-2 consumers.
- (g) `pack_loader` exposes `pack.concepts: Dict[concept_id, ConceptPackEntry]` and `pack.sheets: Dict[sheet_type, SheetPackEntry]` accessors.
- (h) **Rename `xl_ontology/agents/` → `xl_ontology/pass1_tag_agents/`.** Update all imports. Keep the `agents.runner` module path since it's referenced widely; only the package name changes. (Or keep `agents/` as a thin re-export shim during transition.)
- (i) **Tidy `xl_ontology/v1/` folder**: `stages/` keeps Pass-2 stages (overall, inputs, calculations, results, pre_overall). New `pass3/` directory will land in F.6.

**Files touched:** `xl_ontology/packs/bim_v0/**`, `xl_ontology/pack_loader.py`, `xl_ontology/agents/__init__.py` → `xl_ontology/pass1_tag_agents/__init__.py`, `xl_ontology/runner.py`, `xl_ontology/agents/*.py` → `xl_ontology/pass1_tag_agents/*.py`, `xl_ontology/v1/runner.py` (import path updates).

**Schema changes:** `OntologyPack` dataclass gets `concepts` + `sheets` accessors. `OntologyNode` (the per-concept pack representation) extended to carry `lexicon_terms`, `rules_pass1_md`, `rules_pass2_md`. Backward compat: legacy `pack.nodes[NodeName]` access keeps working as a shim.

**Tests:**
- `xl_ontology/tests/test_pack_loader.py` — directory walker discovers expected concepts; lexicon JSON parses; rule files load.
- `xl_ontology/tests/test_pack_layout.py` — NEW — asserts every concept folder has the required triple; asserts every sheet folder has the required pair.
- All existing Pass-1 + Pass-2 tests still pass via the compat shim.

**Verification gate:** `py -3 -m pytest xl_ontology/ tests/ -q` green. `py -3 scripts/run_ontology.py --run-id streamlit_run_simple` produces byte-identical `summary_model_v0.json` to the pre-refactor run.

---

### F.2 — Lexicon overhaul + `search_concept` tool

**Scope:**
- (a) Per-concept `lexicon.json` becomes the authoritative source. Today's `INPUTS_LEXICON` / `RESULTS_LEXICON` / `CALCULATIONS_LEXICON` / `OVERALL_MODEL_LEXICON` Python dicts in `prompts.py` are deleted.
- (b) New deterministic accessor: `get_concept_terms(pack, concept_id) -> List[str]`.
- (c) New Pass-2 tool: `search_concept(ctx, concept_id) -> List[Hit]` — calls `search_workbook_lexicon(ctx, get_concept_terms(...), scope="whole_table")`. Added to `PASS2_TOOL_REGISTRY`.
- (d) `search_workbook_lexicon._match_term` normalises both sides: lowercase, replace `-` and unicode dashes with space, collapse whitespace, strip punctuation, simple plural fold (`s` / `es` strip on last word).
- (e) Pass-1 agents read terms from `pack.concepts[concept_id].lexicon_terms` — single source of truth.

**Files touched:** `xl_ontology/tools/lexicon.py`, `xl_ontology/pass1_tag_agents/{inputs,calculations,results}.py`, `xl_ontology/agents/prompts.py` (lexicon dicts removed), `xl_ontology/v1/planner.py` (registry).

**Schema changes:** None new. Pass-2's tool registry gains `search_concept`.

**Tests:**
- `xl_ontology/tests/test_lexicon.py` — case-insensitive ✓, dash-tolerant ✓, unicode-dash ✓, plural fold ✓, punctuation strip ✓.
- `xl_ontology/v1/tests/test_search_concept.py` — NEW — tool registered; calls `search_workbook_lexicon` with terms from pack; returns hits.
- `xl_ontology/tests/test_pack_loader.py` — updated to verify `lexicon_terms` populates from `lexicon.json`.

**Verification gate:** Green tests. Real-LLM run on `streamlit_run_simple`: Pass-1 finds the same set of candidates as before (or strictly more — never fewer).

---

### F.3 — Pass-1 multi-tag (Option B) + sheet-ontology + sequential precedence

**Scope:**
- (a) `TableTagRecord.ontology_subtypes: List[OntologySubtype]` ranked list (Option B). Each entry: `{name, score, source}`.
- (b) `TableTagRecord.flags: List[str]` carries sheet-flags (`default` / `control`) and other annotations.
- (c) `TableTagRecord.sheet_type: Optional[SheetType]` — the sheet's classified type.
- (d) **Sheet-ontology pass** runs first per sheet: walks workbook sheets, reads `ontology_bim_sheets/<type>/{lexicon.json, context.md}` rules, classifies each sheet → `{sheet_name, sheet_type, evidence_terms}`. Output goes to `sheet_categorization.json` (used in F.5).
- (e) Per-table BIM-concept tagging: replace single-best-match with `top_k` ranked tags per concept; emit ALL matching tables for ALL concepts, score-ranked.
- (f) **Sequential precedence**: input agent runs first, claims; calc agent runs second, claims (skips already-input-tagged tables); result agent runs last, claims. Same-role blocks only — `default`/`control` flags do not.
- (g) "**No potential input/calc/result unclassified**" rule: fan-in/density/formula-density fingerprints force a role tag even when no lexicon hit. Backstop tagging at end of each agent.
- (h) Composite debug string `default-subsequent_drug-market_share` written to audit log only; `concept_id` stays stable as `inputs.market_share`.

**Files touched:** `xl_ontology/schemas.py` (TableTagRecord changes), `xl_ontology/pass1_tag_agents/*.py`, `xl_ontology/tools/writers.py` (multi-tag record_classification).

**Schema changes:**

```python
class OntologySubtype(_Strict):
    name: str
    score: float
    source: Literal["lexicon", "fingerprint", "fan_in_backstop", "fingerprint_density"]

class TableTagRecord(_Strict):
    table_id: str
    sheet: str
    title: Optional[str] = None
    upstream_node_type: UpstreamNodeType
    ontology_role: OntologyRoleAny      # primary (highest-score) role
    ontology_subtypes: List[OntologySubtype] = []  # ranked list
    sheet_type: Optional[SheetType] = None
    flags: List[str] = []
    # ... existing fields preserved
```

**Tests:**
- `xl_ontology/tests/test_pass1_multi_tag.py` — multiple subtypes per table; ranking; sequential precedence; default flag doesn't block; backstop fan-in tagging.
- `xl_ontology/tests/test_sheet_ontology.py` — sheet types classified correctly against synthetic fixture.
- `xl_ontology/v1/tests/test_pass1_apply.py` — updated to use new schema.

**Verification gate:** Real-LLM run shows ≥ same concept coverage as before; `summary_model_v0.json:inputs.candidate_tables` carries multi-subtype rows where applicable; no "lost" tables (every previously-classified table still classified).

---

### F.4 — Pass-2 golden-table identification

**Scope:**
- (a) Per-concept `rules_pass2.md` filled with golden-table criteria. Template:
  - User-editable formatting (blue text, yellow fill, etc.)
  - Low formula-density on input cells (or zero)
  - Header words ("user input", "drug", "year", "BIM concept")
  - Fan-out into calculation tables (first downstream consumer should be calc-tagged)
  - Per-concept overrides (e.g., market_share has "with intervention" / "without intervention" twin-table pattern)
- (b) Pass-2 Inputs agent: for each Pass-1-classified concept, fetch top-N candidates (N from `rules_pass2.md`, default 4); apply golden criteria; pick golden(s). Multiple goldens allowed for concepts where it makes sense (market_share with/without).
- (c) Pass-2 Inputs agent writes per-golden-table prose: structure / user-vs-formula cells / formatting / references / footnotes. Land in `Stage22Output.golden_tables: Dict[concept, List[GoldenTable]]`.
- (d) Pass-2 Results agent restricted to fixed golden set: `budget_impact_total`, `budget_impact_by_component`, `budget_impact_per_patient`.
- (e) Market-share with-vs-without column diff → infers `intervention` + `comparator` lists. Emits to `Stage22Output.intervention_inferred` + `comparators_inferred`.
- (f) Findings carry `relevant_overall_concepts` for F.5 tier-2 lift.
- (g) Flag missing structure / formatting / references / timeline / cost categories on each golden table.

**Files touched:** `xl_ontology/v1/stages/{inputs,results}.py`, `xl_ontology/schemas.py` (Stage22Output / Stage23Output extensions), new `xl_ontology/v1/golden_picker.py` (deterministic golden criteria evaluator).

**Schema changes:**

```python
class GoldenTable(_Strict):
    table_id: str
    coordinates: str                    # Sheet!Range
    confidence: float
    structure_prose: str                # 1-2 paragraphs
    formatting_prose: str               # user-editable cells, formula cells
    references_prose: str               # footnotes / sources
    flags: List[str]                    # missing_references, missing_timeline, etc.

class Stage22Output(StageOutputBase):
    # existing fields preserved
    golden_tables: Dict[str, List[GoldenTable]] = {}  # concept_id → goldens
    intervention_inferred: Optional[str] = None
    comparators_inferred: List[str] = []
```

**Tests:**
- `xl_ontology/v1/tests/test_golden_picker.py` — synthetic candidates + criteria → expected golden picks.
- `xl_ontology/v1/tests/test_market_share_diff.py` — with/without column diff infers intervention + comparators.
- `xl_ontology/v1/tests/test_stage_inputs_golden.py` — Stage22Output carries goldens.

**Verification gate:** Real-LLM run on `streamlit_run_simple`: every Pass-1-classified concept either has a golden table or an explicit `flag: golden_not_found` reason. Market-share inference produces non-empty `intervention_inferred` if the workbook has a with/without pair.

---

### F.5 — OverallAgent two-tier + concept table + sheet categorization

**Scope:**
- (a) **Tier-1 detectors** (Phase E from earlier sessions): after every successful tool observation in any Pass-2 stage, run a deterministic post-step that scans the result for evidence relevant to the 9 OverallAgent goal concepts. Captures `relevant_overall_concepts: Dict[concept, OverallEvidence]` on each `StageFinding`.
- (b) **Tier-2 lift** in `OverallAgent.pre_narrate_hook`: walks `ctx.upstream_reports[*].concepts[*].relevant_overall_concepts`, fills `self._concepts[concept]` with `{value, source: "Sheet!Range"}` BEFORE narration. Loop short-circuits when all 9 concepts filled.
- (c) **Concept table generator** (NEW): once concepts are matched, render a markdown table inside `model_understanding.md`:

  | Concept | Where found | Reference |
  |---|---|---|
  | intervention | Description!B5 | (cover sheet) |
  | population | Inputs!B4:F6 | source: WHO 2023 (footnote) |
  | … | … | … |

- (d) **Sheet categorization step** (NEW): reads `ontology_bim_sheets/<type>/{lexicon.json, context.md}`, classifies each workbook sheet, writes `sheet_categorization.json`:
  ```json
  {
    "sheets": [
      {"sheet_name": "Description", "sheet_type": "intro", "evidence_terms": ["Background"]},
      {"sheet_name": "Inputs",      "sheet_type": "input"},
      {"sheet_name": "Engine",      "sheet_type": "calculation"},
      {"sheet_name": "Results",     "sheet_type": "result"},
      {"sheet_name": "Dashboard",   "sheet_type": "setting"},
      ...
    ]
  }
  ```

**Files touched:** `xl_ontology/v1/concept_extraction.py` (NEW — Tier-1 detectors), `xl_ontology/v1/react_base.py` (post-observation hook), `xl_ontology/v1/stages/overall.py` (Tier-2 lift + short-circuit + concept table generator), `xl_ontology/v1/sheet_categorizer.py` (NEW — sheet classification logic), `xl_ontology/v1/runner.py` (writes `sheet_categorization.json`), `core/paths.py` (path helper for `sheet_categorization.json`).

**Schema changes:**

```python
class OverallEvidence(_Strict):
    value: str
    source_table_id: Optional[str] = None
    source_coordinates: Optional[str] = None  # Sheet!Range
    source_signal: str                        # detector name

class StageFinding(_Strict):
    # existing fields preserved
    relevant_overall_concepts: Dict[str, OverallEvidence] = {}

class SheetCategorizationEntry(_Strict):
    sheet_name: str
    sheet_type: SheetType
    evidence_terms: List[str] = []
    confidence: float = 1.0

class SheetCategorizationFile(_Strict):
    model_id: str
    run_timestamp: str
    sheets: List[SheetCategorizationEntry]
```

**Tests:**
- `xl_ontology/v1/tests/test_concept_extraction.py` — detectors per concept (year sequence → time_horizon; integer column → population; etc.).
- `xl_ontology/v1/tests/test_overall_tier2_lift.py` — synthetic upstream reports → OverallAgent fills `self._concepts` before narration.
- `xl_ontology/v1/tests/test_sheet_categorizer.py` — classifier hits expected types on synthetic sheets.
- `xl_ontology/v1/tests/test_concept_table_render.py` — markdown table emitted with expected rows.

**Verification gate:** Real-LLM run on `streamlit_run_simple`: OverallAgent's `concepts` dict is fully populated before narration; `model_understanding.md` carries the concept table; `sheet_categorization.json` exists with all sheets classified.

---

### F.6 — Pass 3: Calculation Agent + sheet-conditional Default/Setting agents

**Scope:**
- New top-level Pass-3 runs after Pass-2 closes, before `model_understanding.json` is written.
- **Calculation Agent (always runs):**
  - For each input-concept golden table from Pass-2 (e.g. market_share golden, population golden, drug_cost golden):
    - Walk one DAG fan-out hop using `get_dag_neighbours(direction="out", depth=1)`.
    - Spawn a sub-agent per fan-out branch (sequentially in F.6; parallelized in F.8).
    - Each sub-agent reads the downstream table via `read_table_full_content` and writes prose: *"Table at [Sheet!Range] consumes [market_share + population]. It computes the number of treated patients in the world with intervention by multiplying market share × eligible population × treatment duration. This is the 'population calculation with intervention'."*
    - Identify golden `population_calc_with_intervention` / `population_calc_without_intervention` pair by examining whether the table reads from "with" or "without" market-share columns.
    - Flag subpopulation if more than 2 such tables exist.
    - Continue downstream: walk one more fan-out hop, repeat. Stop when reaching a result-tagged table.
  - Repeat for every input concept (drug calcs, subsequent treatment calcs, AE calcs, etc.).
  - Output: `Stage25Output.calc_chains: Dict[input_concept, CalcChain]`.
- **Default Agent (only if `default` sheet exists):**
  - For each input concept, find the corresponding default-values table on the default sheet.
  - Write prose: *"Default values for [drug_cost] are stored in (Default!B12:F30). User-editable input table at (Inputs!B12:F30) initially copies from this default but may be modified."*
  - Output: `Stage26Output.default_locations: Dict[input_concept, DefaultLocation]`.
- **Setting Agent (only if `setting` sheet exists):**
  - Identify named variables / controls (data-validation dropdowns, named ranges, fan-in=0 cells with downstream control influence).
  - Write prose explaining each control's role: *"The 'time_horizon' named range at (Settings!E5) is referenced by Calculations!H10:H15 and controls the upper year limit of the simulation."*
  - Output: `Stage27Output.controls: List[ControlVariable]`.
- All Pass-3 outputs land in `model_understanding.json` as new top-level stages.
- All write Excel-range citations matching F.4's contract.

**Files touched:** `xl_ontology/v1/pass3/__init__.py` (NEW), `xl_ontology/v1/pass3/calculation_agent.py` (NEW), `xl_ontology/v1/pass3/default_agent.py` (NEW), `xl_ontology/v1/pass3/setting_agent.py` (NEW), `xl_ontology/v1/runner.py` (Pass-3 orchestration), `xl_ontology/schemas.py` (Stage25/26/27 schemas).

**Schema changes:** New `Stage25Output` / `Stage26Output` / `Stage27Output` Pydantic classes. `ModelUnderstandingFile.stages` Pass2StageName extended with `"calculation_chains"`, `"defaults"`, `"settings"`.

**Tests:**
- `xl_ontology/v1/tests/test_pass3_calc_agent.py` — fan-out walk; population_calc_with/without identification; subpopulation flag; chain termination at result.
- `xl_ontology/v1/tests/test_pass3_default_agent.py` — default-table location prose; runs only when default sheet present.
- `xl_ontology/v1/tests/test_pass3_setting_agent.py` — control variable identification; prose generation.

**Verification gate:** Real-LLM run on `streamlit_run_simple`: Pass-3 calc chains exist for every input golden; if `Default` sheet exists, default locations populated; if `Settings` sheet exists, controls populated.

---

### F.7 — Pack versioning machinery

**Scope:**
- (a) Copy `bim_v0/` → `bim_v1/` as the new editing target (no content changes; this is the migration scaffold).
- (b) **Pack metadata** at pack root: `bim.txt` gets a YAML front-matter block with `version`, `last_updated`, `change_log_link`. (Or a dedicated `pack.json` — pick one shape.)
- (c) **`pack_name` config switch**: `config/stages.yaml xl_ontology.pack_name` selects the active pack. Defaults to `bim_v0`.
- (d) **`xl_ontology/tools/pack_diff.py`** — CLI: `py -3 -m xl_ontology.tools.pack_diff bim_v0 bim_v1`. Emits markdown report:
  - Concepts added / removed
  - Lexicon term diffs per concept
  - Rule section changes (header diff)
  - Sheet types added / removed
- (e) **Pack migration test**: `xl_ontology/tests/test_pack_migration.py` — runs each pack (bim_v0, bim_v1) against a frozen reference workbook fixture (`tests/fixtures/migration_reference.xlsx`) and asserts: every concept in the reference's golden ontology output is still found; concept count doesn't drop; previously-found concept doesn't go absent.
- (f) Runner emits a `pack_used` field in `model_understanding.json` so consumers know which pack version produced the output.

**Files touched:** `xl_ontology/packs/bim_v1/**` (copied scaffold), `xl_ontology/pack_loader.py` (front-matter parsing + version field), `xl_ontology/tools/pack_diff.py` (NEW), `xl_ontology/tests/test_pack_migration.py` (NEW), `config/stages.yaml`, `xl_ontology/runner.py` (writes `pack_used` to summary).

**Tests:**
- `xl_ontology/tests/test_pack_versioning.py` — pack_name config switch; pack metadata parsing.
- `xl_ontology/tests/test_pack_diff.py` — diff between bim_v0 and synthetic bim_v0_modified produces expected report.
- `xl_ontology/tests/test_pack_migration.py` — both packs pass migration against reference workbook.

**Verification gate:** `pack_name=bim_v0` and `pack_name=bim_v1` both produce identical output on the reference workbook (since bim_v1 is a fresh copy with no edits). Migration test green.

---

### F.8 — Parallelism (Pass-2 + Pass-3 sub-agents)

**Scope:**
- (a) **Pass-2 parallelism**: Inputs / Results / Pre-Overall stages run concurrently via `ThreadPoolExecutor`. Each gets its own `LlmBudget` slice. Trace events keep their per-stage `stage` field; trace ordering becomes `(stage, ts, iteration)` instead of pure `ts`.
- (b) **Pass-3 sub-agent parallelism**: per-fan-out-branch sub-agents in the Calculation Agent run concurrently per input concept.
- (c) **Per-stage budget partitioning**: total `pass2_llm_budget_max` split across parallel stages (e.g. 6 each for 3 stages); per-budget `BudgetExhausted` events stay per-stage.
- (d) **Deterministic output ordering**: stages may finish in any order, but the `Stages` map in `model_understanding.json` is keyed and not order-sensitive; per-stage trace events sort deterministically.

**Files touched:** `xl_ontology/v1/runner.py` (orchestration), `xl_ontology/budget.py` (per-stage budget partitioning), `xl_ontology/trace.py` (thread-safe writes, already are).

**Tests:**
- `xl_ontology/v1/tests/test_pass2_parallel.py` — concurrent Pass-2 execution finishes faster than sequential; output is deterministic across runs.
- `xl_ontology/v1/tests/test_pass3_parallel.py` — sub-agent parallelism per fan-out branch; deterministic output.

**Verification gate:** Real-LLM wall-clock time drops by at least 30% on `streamlit_run_simple`; output JSON byte-identical to the F.7 sequential run.

---

## 5. Files reused (no change across any phase)

- `xl_ontology/budget.py` — `LlmBudget`, `BudgetExhausted` (F.8 extends per-stage partitioning)
- `xl_ontology/llm_complete.py` — `resolve_llm_complete`
- `xl_ontology/trace.py` — `TraceWriter` / `NodeTypeOverrideWriter` / `BuildErrorWriter`
- `xl_ontology/tools/writers.py` — `record_classification` (F.3 extends to multi-tag, internal change only)
- `xl_ontology/tools/cells.py`, `dag_tools.py`, `discovery.py`, `composite.py` — primitive tool registry
- `core/workbook_accessor.py` — used everywhere
- `xl_qc_repair/**` — orthogonal stage; no changes

---

## 6. Out of scope for this plan

- **Multi-pack inheritance.** F.7 supports multiple immutable packs but doesn't model "bim_v1 inherits from bim_v0". Each pack is a self-contained directory.
- **Vector embeddings or semantic search** — lexical + alias resolution only, per `xl_ask_approach.md`.
- **Editable pack format** (LLM-editable rules at runtime) — packs are filesystem-only, edited by humans.
- **Cross-workbook reasoning.** One run = one workbook.
- **xl_ask `ontology_tree.json` exporter.** F.4 / F.5 / F.6 produce all the data needed; the exporter that assembles `OntologyTree` matching `ontologyTree.md`'s schema is a presentation-layer follow-up after F.6 ships.
- **Sub-agent parallelism inside a single Pass-3 calculation chain.** Per-input-concept parallelism is in F.8; per-table-within-a-chain parallelism is not.

---

## 7. Risk + mitigation

| Risk | Mitigation |
|---|---|
| F.1 file moves break import chains | Compat shim re-exports old paths during transition; tests stay green per phase. |
| F.3 multi-tag schema breaks CHERI consumer | CHERI's `summary_model.json` reader needs a small update — coordinate via `cheri_v1_docs/summaryModel.schema.ts` revision in same PR. |
| F.4 golden-picker too restrictive → loses concepts | `flags: golden_not_found` + reason field surfaces the loss; `rules_pass2.md` thresholds tunable per concept. |
| F.5 Tier-1 detectors over-aggressive → false-positive concept fills | `OverallEvidence.confidence` field; OverallAgent prefers higher-confidence sources; LLM narration cites the source so reviewer can spot wrong fills. |
| F.6 Pass-3 explodes LLM budget | Per-input-concept budget cap; chain depth cap (e.g. 3 hops max); short-circuit when reaching result-tagged table. |
| F.7 pack switch causes silent regression | Migration test against frozen reference workbook + golden output; CI fails on coverage drop. |
| F.8 parallelism flakes intermittently | Deterministic output assertion in tests; thread-safe trace writers already exist; per-budget partitioning prevents cross-stage races. |

---

## 8. Sequencing rationale (one-line each)

- **F.1 first** — every later phase reads from the new pack layout. Doing it later means re-touching pack-loading code N times.
- **F.2 second** — small, independent, immediately improves match quality. Strict prerequisite for F.3 (multi-tag scoring needs reliable matches).
- **F.3 third** — Pass-1 multi-tag is the last piece of "input data" Pass-2 needs. Sheet ontology is needed by F.5.
- **F.4 fourth** — Pass-2 golden picks consume Pass-1 multi-tag candidates and per-concept `rules_pass2.md`.
- **F.5 fifth** — depends on F.4 golden-table prose + F.3 sheet ontology.
- **F.6 sixth** — Pass-3 consumes Pass-2 goldens + sheet categorization.
- **F.7 seventh** — pack versioning makes sense once content is stable enough to want bim_v1.
- **F.8 last** — parallelism is an optimization on top of correct sequential code.

---

## 9. Living document

This plan is updated in-place as phases ship. Each completed phase gets a ✅ stamp + a one-line "what landed" note. Phases discovered during implementation get appended as F.X.1 / F.X.2 sub-rows under the parent phase. If a phase splits into multiple PRs, the split is recorded here.

### Phase status

- [x] **F.1 — Pack refactor + folder rename.** Done 2026-05-08. New tree under `xl_ontology/packs/bim_v0/ontology_bim_concepts/{branch}/{concept}/{lexicon.json, rules_pass1.md, rules_pass2.md}` + `ontology_bim_sheets/{type}/{lexicon.json, context.md}` + `bim.txt` (verbatim from `pack_overview.context.md`). 22 concept folders + 7 sheet folders + bim.txt = 81 new files. `pack_loader.py` walks the new tree and populates `OntologyPack.concepts` + `OntologyPack.sheets` + `OntologyPack.bim_overview_text`. Legacy `OntologyPack.nodes` still loads from flat-file layout (backward-compat). `xl_ontology.pass1_tag_agents` introduced as a `sys.modules` alias of `xl_ontology.agents` — both import paths resolve to the same modules. **Physical directory rename deferred** — agents/ stays in place during the F.2 → F.6 transition; the alias gives consumers time to migrate. Once F.6 lands and no consumer references `xl_ontology.agents.*` directly, the directory will be physically renamed and the alias deleted. Tests: 15 new in `test_pack_layout.py`; 554/554 sweep green; ontology stage runs clean on `streamlit_run_simple`.
- [x] **F.2 — Lexicon overhaul + `search_concept` + normalization.** Done 2026-05-08. New `xl_ontology/concept_terms.py` with `get_concept_terms(pack, concept_id)` and `list_concepts_in_branch(pack, branch)` accessors. New `search_concept(ctx, concept_id)` Pass-2 tool registered in `PASS2_TOOL_REGISTRY` — resolves concept_id → terms via pack and runs `search_workbook_lexicon` with `scope="whole_table"`. `_match_term` / `_normalize_text` in `tools/lexicon.py` rewritten with NFKC fold + lowercase + unicode-dash → space + punctuation strip + whitespace collapse + plural fold (-ies/-y, -[sxz]es/-es, -s) gated by 4-char minimum so "AE" / "OS" / "loss" stay intact. Pass-1 agents (inputs / calculations / results) now read terms from `pack.concepts[id].lexicon_terms` first, falling back to legacy `INPUTS_LEXICON` / `CALCULATIONS_LEXICON` / `RESULTS_LEXICON` dicts only when pack lookup yields the bare-name fallback (preserves the safety net during transition). `ToolContext.pack: Optional[Any]` field added; production runner populates it; legacy test fixtures default to None. Tests: +33 new (`test_concept_terms.py` 11, `test_lexicon_normalization.py` 19, `test_search_concept.py` 3). 587/587 sweep green; ontology stage runs clean on `streamlit_run_simple`. Observed behavior change: `cost_component_calc` newly resolves to `found` thanks to richer pack lexicon; some legacy false-positive `total_cost_with` matches now correctly absent (recall gap to be closed by F.3 structural fingerprints).
- [ ] F.3 — Pass-1 multi-tag + sheet ontology
- [ ] F.4 — Pass-2 golden-table identification
- [ ] F.5 — OverallAgent two-tier + concept table + sheet categorization
- [ ] F.6 — Pass-3 (Calculation + Default + Setting agents)
- [ ] F.7 — Pack versioning machinery
- [ ] F.8 — Parallelism

---

## Appendix A — Concept inventory

Source: `xl_ontology/packs/bim_v0/ontology_inputs.txt` §3 + §5, mapped to F.1 folder names.

**Inputs (13 concepts):**
`population`, `market_share`, `drug_cost`, `epidemiology`, `subsequent_treatment`, `adverse_event`, `hospitalization`, `SCT`, `end_of_life`, `monitoring`, `administration`, `dosing`, `scenario`.

**Calculations (6 concepts):**
`total_cost_with_intervention`, `total_cost_without_intervention`, `patient_flow`, `aggregator`, `cost_component_calc`, `scenario_calc`.

**Results (3 concepts, F.4 restricts to these only):**
`budget_impact_total`, `budget_impact_by_component`, `budget_impact_per_patient`.

**Sheet types (7):**
`intro`, `input`, `calculation`, `result`, `setting`, `control`, `default`.

---

## Appendix B — `concept_id` → folder mapping (F.1 invariant)

| `concept_id` | Folder path |
|---|---|
| `inputs.population` | `ontology_bim_concepts/inputs/population/` |
| `inputs.market_share` | `ontology_bim_concepts/inputs/market_share/` |
| `calculations.aggregator` | `ontology_bim_concepts/calculations/aggregator/` |
| `results.budget_impact_total` | `ontology_bim_concepts/results/budget_impact_total/` |
| `sheet.intro` | `ontology_bim_sheets/intro/` |
| `sheet.input` | `ontology_bim_sheets/input/` |

Folder name = lowercase concept name with underscores. No spaces. No dashes (reserved for composite debug strings).

---

*End of plan.*
