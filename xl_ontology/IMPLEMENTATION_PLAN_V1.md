# xl_ontology v1 — Implementation Plan (sequential LLM-driven comprehension layer)

> **Companion:** `sheetagent_plan_v0_patch.md` (v0.1 Bucket A + C) is the Pass 1 prerequisite.

## Context

v0 of `xl_ontology` is a **labeller**: it tags each table's role in the workbook against an ontology pack. v0 does not understand the model. The v1 spec (`xl_ontology/spec/spec_xl_ontology_v1.md`) layers a **comprehension** stage on top: four sequential LLM-driven agents that plan, invoke deterministic tools, check completion criteria, and produce a coherent prose narrative of the BIM. v1 ships in the existing `xl_ontology/` package as two passes:

- **Pass 1** = v0.1 patch (Bucket A + C — already planned in `sheetagent_plan_v0_patch.md`) **plus** removal of v0's inert LLM scaffolding **plus** a DAG-only candidate filter.
- **Pass 2** = entirely new. Four agents (overall → inputs → results → calculations) each running their own bounded planner-executor loop, sharing prose context forward, emitting overrides and gap reports.

Phase E (real-LLM validation against `streamlit_run_enco`) just shipped. The plumbing v1 needs (`LlmBudget`, `ask_llm`, `BudgetCheck`/`BudgetExhausted` events, override-log writers, `llm_complete` parameter) is already proven in production.

### What exploration confirmed

- **`_planner_phase`** at `xl_ontology/agents/runner.py:205–240` is called from `OntologyAgent.run():148`. Its output (`PlanProduced`) is consumed only by `OverallModelAgent._execute_planner_tools_phase` at `agents/overall_model.py:57–90`. Inputs/Calculations/Results inherit a no-op default — confirming v0's claim that the plan is "logged but not consumed" for those agents.
- **`OverallModelAgent`** lives at `agents/overall_model.py:49–294` and is the only v0 agent that runs an executor loop. v1 Pass 1 removes it (overall is a Pass 2 stage); helpful sub-pieces (`_currency_country_fallback`, overview discovery) move into Pass 2 as deterministic helpers.
- **`LlmFallbackHandler` does not exist in code.** The v1 spec's mention is shadow-design from `IMPLEMENTATION_PLAN.md:1156` that never landed. **Nothing to remove for it** — drop the bullet from the spec or note "no-op" in the plan.
- **All Pass 2 reuse primitives exist:** `LlmBudget`/`BudgetExhausted` (`budget.py:38–64` / `:20–34`), `ask_llm` (`tools/llm.py:33–96`), `LlmCall`/`BudgetCheck`/`BudgetExhausted` events (`schemas.py:435–444` / `:524–528` / `:531–536`), `llm_complete` (`runner.py:84+`), `NodeTypeOverrideWriter` (`trace.py:73–82`), and `record_classification` override coupling (`tools/writers.py:87–107`).
- **Tool registry** is "standalone functions imported by callers" (no dict registry). Eight files in `tools/`. `find_overview_sheets` already exists at `composite.py:43–65` (and a duplicate stub at `discovery.py:43–65` — confirm during cleanup). `get_table_content`, `get_dag_neighbours`, `search_workbook_lexicon`, `extract_currency_symbols`, `get_cells_in_range/above/left` all present. The only **new tool** v1 needs is `read_overview_sheets`.
- **Trace writers support `append=True`** (`trace.py:26–61`), so Pass 2 can append into the same `ontology_trace.jsonl` and `node_type_override_log.jsonl` after Pass 1 closes them.
- **Pack loader explicitly enumerates files** at `pack_loader.py:130–144`; new Pass-2 prompt files must be added to `expected_files` or they'll generate `unknown_pack_file` warnings.
- **DAG-only filter:** `node_index.json.by_table_id` lets us answer "is this table_id in the DAG?" in O(1). `node_id = "{sheet_slug}__{table_id}__0"` per CLAUDE.md.

## Effort estimate

| Phase | Work | Time |
|---|---|---|
| **V1.A** | Pass 1 cleanup + DAG-only filter + new tests | ~0.75 day |
| **V1.B** | Output-writer + path scaffolding for the three new artifacts | ~0.5 day |
| **V1.C** | `read_overview_sheets` tool + composite cleanup | ~0.25 day |
| **V1.D** | Pass 2 framework (`xl_ontology/v1/`): config, runner, agent base, planner-executor loop, completion-criteria derivation | ~1 day |
| **V1.E** | Stage 2.1 (overall agent) — fixed criteria, per-sheet extraction + synthesis | ~0.75 day |
| **V1.F** | Stages 2.2 (inputs), 2.3 (results), 2.4 (calculations) — derived criteria, planner-executor loop, override emission, mismatch detection | ~1.25 days |
| **V1.G** | Orchestrator integration: 3 new viewer tabs, run button, CLI flag, pipeline flag, LLM-on/off plumbing | ~0.5 day |
| **V1.H** | Phase E-equivalent real-LLM validation against `streamlit_run_enco` + iteration on observed output | ~0.5–1 day |
| | **Total** | **~5–6 days** |

This is **clearly more than 1 day**, so per the spec's gate the plan is being surfaced for review before any coding starts.

If we want to land smaller deliveries, V1.A + V1.B + V1.C ship as a "Pass 1 cleanup-only" PR (~1.5 days) and Pass 2 is a follow-up.

## Phased build order

Each phase ends with a review checkpoint and green tests.

### V1.A — Pass 1 cleanup (prerequisite: v0.1 patch from `sheetagent_plan_v0_patch.md` is merged)

**Goal:** strip dead LLM scaffolding from Pass 1, add the DAG-only filter, keep all reusable primitives intact.

**Removals:**
- `xl_ontology/agents/runner.py:205–240` — `_planner_phase` method body. Replace with a no-op (or delete the call at line 148). The state field `state.planner_plan` becomes unused for Pass 1 — keep it on the dataclass since Pass 2 will repurpose it.
- `xl_ontology/agents/overall_model.py` — entire file. The agent will be reimplemented as Stage 2.1 in `v1/`. Move the deterministic helpers `_currency_country_fallback` (`overall_model.py:152–187`) and any overview-sheet discovery logic into a new module `xl_ontology/heuristics_helpers.py` so Stage 2.1 can call them as deterministic fallbacks (Section 10 of the spec).
- `xl_ontology/runner.py:223–228` — drop `OverallModelAgent` from the agent loop. Pass 1 now runs only `InputsAgent`, `CalculationsAgent`, `ResultsAgent`.
- `xl_ontology/agents/runner.py:78–106` (`dispatch_defect_stub`) — **keep**. v1 spec says "LLM-fallback heuristic dispatch path" is removed, but exploration confirmed this path is purely deterministic; only the LLM-fallback part is the spec phantom. Document in the plan that there's nothing to remove here.

**Additions:**
- `xl_ontology/upstream/dag.py` — extend `DagIndex` with `is_in_dag(table_id) → bool` using the `by_table_id` index from `node_index.json`. If `node_index.json` not present, fall back to scanning `dag.json.nodes`.
- `xl_ontology/agents/inputs.py` / `calculations.py` / `results.py` — at the top of `_cross_check_phase`, filter `final_map.all_entries()` through `ctx.dag.is_in_dag(entry.table_id)`. Tables without DAG nodes are recorded into `ontology_table_tags.json` with `ontology_role: "out_of_scope_for_pass_1"` and `agent_owner: null`. **Do not** add them to `unclassified_tables`.
- `xl_ontology/schemas.py` — add `"out_of_scope_for_pass_1"` to the `ontology_role` Literal (or relax it to `str` if Literals start to fight us).

**Tests** (`xl_ontology/tests/test_v1_pass1_cleanup.py`, new file):
- `_planner_phase` no longer fires (no `PlanProduced` events).
- `OverallModelAgent` is gone; `run_ontology()` runs 3 agents instead of 4.
- DAG-only filter: a synthetic table with no DAG node lands in `ontology_table_tags.json` with role `out_of_scope_for_pass_1`.
- Existing Pass 1 tests still pass (regression).

**Review checkpoint:** Pass 1 runs cleanly on `streamlit_run_enco` with **0 LLM calls**, 3 agents, summary_model_v0.json populated.

### V1.B — Output-writer + path scaffolding

**New files** (just scaffolding, no Pass 2 logic yet):
- `xl_ontology/output_writers.py` — add `write_model_understanding_md`, `write_model_understanding_json`, `append_cross_agent_finding`. Same atomic-write idiom as existing writers.
- `core/paths.py` — add `get_model_understanding_md_path`, `get_model_understanding_json_path`, `get_cross_agent_findings_path`, all under `ontology_output/`.
- `xl_ontology/schemas.py` — add Pydantic models for `ModelUnderstandingFile`, `Stage21Output`, `Stage22Output`, `Stage23Output`, `Stage24Output`, `CrossAgentFinding`. Match the JSON shape in spec §11.3 / §11.4.
- New trace event types in `xl_ontology/schemas.py`: `Pass2StageStart`, `Pass2StageComplete`, `CompletionCriteriaDerived`, `ToolCall`, `ToolCallFailed`, `IterationCapReached`. Pass 2 emits these alongside the existing `LlmCall` / `BudgetCheck` events.

**Tests** (`xl_ontology/tests/test_v1_writers.py`):
- Round-trip for each new artifact: write a synthetic Pydantic instance, read back, deep-equal.
- Path helpers return the right strings.

**Review checkpoint:** scaffolding complete; nothing yet writes the artifacts in production paths, but the surface is ready.

### V1.C — `read_overview_sheets` tool

- `xl_ontology/tools/composite.py` — resolve the duplicate `find_overview_sheets` between `composite.py` and `discovery.py`. Keep one canonical impl; remove or re-export from the other.
- New function `read_overview_sheets(ctx, sheet_names) → List[OverviewSheetText]` (spec §9.1). Uses `core.workbook_accessor.WorkbookAccessor` (already in `ToolContext`) for O(1) cell reads.
- New schema `OverviewSheetText` with `sheet_name`, `text` (concatenated `[Sheet!A1] value` markers, spatial-ordered), `length_chars`.
- Hard cap on total text per sheet (configurable, default 10k chars) so the LLM prompt stays bounded.

**Tests:**
- Synthetic 3-sheet workbook → reads all visible cells in row-major, formats with `[Sheet!Cell]` location markers, truncates if oversize.

**Review checkpoint:** tool callable as a standalone unit before Pass 2 wires it.

### V1.D — Pass 2 framework

**Module skeleton:** `xl_ontology/v1/`

```
xl_ontology/v1/
  __init__.py              # exports run_pass2(...)
  config.py                # Pass2Config (max_iterations, llm_budget, llm_on/off)
  runner.py                # run_pass2(): orchestrates Stage 2.1 → 2.2 → 2.3 → 2.4
  agent_base.py            # Pass2Agent abstract class with the bounded loop
  completion_criteria.py   # derive_completion_criteria() — LLM-driven (Section 5)
  planner.py               # build_planner_prompt(), parse_plan(), execute_plan()
  narrator.py              # build_narration_prompt(), parse_narration(), structured_findings_to_json()
  overrides.py             # emit_pass2_override() — wraps existing record_node_type_override
  prompts/
    overall_extraction.txt
    overall_synthesis.txt
    inputs_criteria.txt
    inputs_planner.txt
    inputs_narrator.txt
    results_*.txt
    calculations_*.txt
  stages/
    overall.py             # Stage 2.1 — fixed criteria, per-sheet extraction
    inputs.py              # Stage 2.2 — derived criteria + planner-executor
    results.py             # Stage 2.3 — derived criteria + mismatch detection
    calculations.py        # Stage 2.4 — derived criteria + DAG backward traversal
  tests/
    fixtures.py
    test_completion_criteria.py
    test_planner_executor.py
    test_overrides.py
    test_e2e_synthetic.py
```

**Pass2Agent** (`agent_base.py`) — abstract class with the bounded loop (spec §6.1):

```python
class Pass2Agent(ABC):
    stage_name: str
    def run(self, ctx, upstream_findings) -> StageOutput:
        criteria = self.derive_criteria(ctx, upstream_findings)
        ctx.trace.emit(CompletionCriteriaDerived(...))
        findings = {}
        for iteration in range(ctx.config.max_iterations):
            plan = self.plan(ctx, criteria, findings)
            findings = self.execute(ctx, plan, findings)
            if self.criteria_met(criteria, findings):
                break
        else:
            ctx.trace.emit(IterationCapReached(...))
        narration = self.narrate(ctx, criteria, findings)
        return StageOutput(criteria=criteria, findings=findings, prose=narration.prose, ...)
```

The `execute` method invokes the deterministic tool registry. **This is the v1 fix for v0's "plan generated, never executed" bug** (§4.3 of spec).

**LLM budget:** Pass 2 has its own `LlmBudget` instance with cap from `config.pass2_llm_budget_max` (default 20). All LLM calls inside Pass 2 go through `ask_llm(ctx, ..., budget_phase="pass2")` — extending `tools/llm.py` to accept a `budget_phase` parameter so seed/enrichment/pass2 budgets are tracked separately. (Today `ask_llm` already accepts `budget_phase="seed"` — extend the existing parameter.)

**LLM-on/off** plumbing:
- `XlOntologyConfig` (`config.py`) gets `pass2_enabled: bool` (default true) and `pass2_max_iterations: int` (default 3).
- `runner.run_ontology()` runs Pass 1 always; Pass 2 only when `config.pass2_enabled and effective_llm is not None`. When skipped, write a single `WarningEvent` to the trace (`category="pass2_skipped"`) so the orchestrator UI surfaces the reason.

**Tests:** mock `llm_complete` to return canned responses; verify the loop terminates on (a) criteria-met, (b) iteration-cap, (c) budget-exhausted, all without exceptions.

**Review checkpoint:** framework + base agent run end-to-end with a mock LLM and a single canned tool call.

### V1.E — Stage 2.1 (overall)

**Fixed completion criteria** (spec §10 Stage 2.1 note): no LLM derivation; criteria are the goal-concepts list (intervention, comparator, population, time horizon, perspective, indication, country, model structure, key assumptions). Implemented as a constant in `stages/overall.py`.

**Flow** (spec §10):
1. `find_overview_sheets(ctx, OVERVIEW_PATTERNS)` — reuses existing tool
2. `read_overview_sheets(...)` — new tool from V1.C
3. **Per-sheet extraction LLM call**, prompt template `prompts/overall_extraction.txt`. One call per sheet (capped at 3 sheets to bound budget).
4. **Synthesis LLM call**, prompt template `prompts/overall_synthesis.txt`, takes the per-sheet extractions and produces unified `Stage21Output.concepts`.
5. **Heuristic fallbacks** (`xl_ontology/heuristics_helpers.py`): for any concept still null, run `_currency_country_fallback` etc. — deterministic, no LLM cost.
6. Narration LLM call (template `prompts/overall_narrator.txt`).

**Budget:** ~3–5 calls (2–3 extractions + 1 synthesis + 1 narration).

**Tests:**
- Canned multi-sheet workbook fixture; mock LLM returns a fixed extraction; assert `Stage21Output.concepts` has all goal concepts populated.
- `_currency_country_fallback` fires when the extraction returns `country=null` and the workbook has £ symbols.

### V1.F — Stages 2.2, 2.3, 2.4 (inputs / results / calculations)

These three stages share the bounded planner-executor pattern from V1.D. Differences are in:

- **Inputs** (`stages/inputs.py`):
  - **Derived criteria** via LLM (spec §5). Prompt input: overall_context + inputs ontology pack + structural hint (Pass 1's classification distribution from `summary_model_v0.json`).
  - Loop tools: `search_workbook_lexicon` (with concept-specific terms expanded by overall_context names like "Enco", "Drug3"), `get_table_content`, `get_dag_neighbours`.
  - Override emission: when the LLM identifies a Pass-1 mislabel, call `emit_pass2_override(ctx, table_id, new_role, new_subtype, evidence)` which wraps `record_node_type_override` with `signal: "pass_2_llm_correction"`.

- **Results** (`stages/results.py`):
  - Same loop shape.
  - **Mismatch detection** (spec §8.3): after the loop, compare cost components in classified result tables against `inputs_findings`. Flag any component without a corresponding classified input. Append `CrossAgentFinding(type="result_component_without_input", ...)` to `cross_agent_findings.jsonl`.

- **Calculations** (`stages/calculations.py`):
  - **Goal: identify 1–2 most important calc tables.** Not all calcs.
  - Special tool emphasis: `get_dag_neighbours(direction="in", depth=...)` for backward traversal from the headline `incremental_budget_impact` results table identified by Stage 2.3.
  - Narration ranks the top 1–2 explicitly; mentions others briefly (no full ranking — spec §15).

**Tests** for each stage: fixture-based tests with mock LLM, assert criteria-derivation produces the right shape, assert at least one tool call fires, assert narration prose populated, assert overrides land in `node_type_override_log.jsonl`.

**Review checkpoint:** end-to-end Pass 2 run on a synthetic fixture produces all three new artifacts, no crashes, all 4 stages run sequentially, override log appended.

### V1.G — Orchestrator integration

**Sidebar CLI** (`scripts/run_pipeline.py`):
- New flag `--with-ontology-v1` (or rename `--with-ontology` to imply v1 by default and add `--ontology-pass-1-only` for the legacy path). Recommendation: keep existing flag, add `--ontology-pass1-only` as opt-out so Pass 2 is on by default once it's stable.

**Standalone script:** extend `scripts/run_ontology.py` with `--pass1-only` flag. Default behaviour runs both passes when LLM is available.

**Streamlit orchestrator** (`apps/orchestrator/app.py`):
- Sidebar checkbox "Run Pass 2 (LLM comprehension)" defaulting to true; reflected via `cfg_ov["xl_ontology"]["pass2_enabled"]`.
- The existing ▶ Run xl_ontology button stays; the worker reads `pass2_enabled` from session config.

**Viewer tabs** (`apps/orchestrator/ontology_viewer.py`):
- Three new tabs after the existing six (spec §11.2):
  - **"Model understanding (prose)"** — render `model_understanding.md` via `st.markdown`.
  - **"Model understanding (structured)"** — JSON view, one expander per stage, dataframe for `concepts` + `findings`.
  - **"Cross-agent findings"** — filterable dataframe over `cross_agent_findings.jsonl` (reuse `_flatten_for_dataframe` helper at `ontology_viewer.py:303–320`).

**Tests** (`tests/test_v1_smoke.py` at repo root):
- Imports work
- New CLI flag accepted
- Three new viewer tabs render with empty fixtures (no crashes)

**Review checkpoint:** orchestrator runs end-to-end with Pass 2 against `streamlit_run_enco`; all 9 viewer tabs render; CLI flag honored.

### V1.H — Real-LLM validation (Phase-E-for-v1)

Mirror what we just did for Phase E. Run `scripts/run_ontology.py --run-id streamlit_run_enco`. Inspect the three new artifacts. Validate:

- Pass 2 LLM calls ≤ hard cap (20)
- `model_understanding.md` reads sensibly — overall agent identifies intervention, comparators, country, etc.
- Each stage's completion criteria appear in `model_understanding.json` with rationale + source_signal
- Each stage's findings include both `items_satisfied` and (where applicable) `items_missing` for traceability
- `cross_agent_findings.jsonl` contains plausible mismatches (or is empty if the BIM is well-formed)
- Overrides appended to existing `node_type_override_log.jsonl` with `signal: "pass_2_llm_correction"` for any Pass-2 corrections

If LLM calls overshoot the cap or quality is poor, iterate on the prompt templates in `v1/prompts/` until criteria pass. **Hard cap 20 is the gate** — same pattern as Phase E's "≤ 8 cap".

**Budget cap spot-check** (mirrors Phase E.5): temporarily set `pass2_llm_budget_max: 5`; rerun; confirm cap honored, run completes cleanly.

## Critical files (modify or create)

### Modify
| Path | Block | Change |
|---|---|---|
| `xl_ontology/agents/runner.py` | `:148`, `:205–240` | Remove `_planner_phase` call + body |
| `xl_ontology/agents/overall_model.py` | entire file | Delete (move helpers to `heuristics_helpers.py`) |
| `xl_ontology/agents/inputs.py` `:42`+ | `_cross_check_phase` head | Add DAG-only filter |
| `xl_ontology/agents/calculations.py` | same | same |
| `xl_ontology/agents/results.py` | same | same |
| `xl_ontology/runner.py` | `:223–228` | Remove `OverallModelAgent` from agent loop; orchestrate Pass 2 after Pass 1 |
| `xl_ontology/upstream/dag.py` | extend `DagIndex` | `is_in_dag(table_id)` |
| `xl_ontology/schemas.py` | add `ontology_role` literal + new event/output models |
| `xl_ontology/output_writers.py` | append three new writers |
| `xl_ontology/tools/composite.py` | resolve `find_overview_sheets` duplicate; add `read_overview_sheets` |
| `xl_ontology/tools/llm.py` | extend `ask_llm(...,budget_phase="pass2")` |
| `xl_ontology/pack_loader.py` | `:130–144` | Add Pass-2 prompt files to `expected_files` (or load `v1/prompts/` from package, not from pack — see decision below) |
| `xl_ontology/config.py` | `XlOntologyConfig` | Add `pass2_enabled`, `pass2_max_iterations`, `pass2_llm_budget_max` |
| `core/paths.py` | three new `get_*_path` helpers |
| `apps/orchestrator/app.py` | sidebar checkbox, worker passes pass2_enabled |
| `apps/orchestrator/ontology_viewer.py` | three new tabs |
| `scripts/run_ontology.py` | `--pass1-only` flag |
| `scripts/run_pipeline.py` | propagate `--with-ontology` + Pass 2 default |
| `config/stages.yaml` `xl_ontology:` | three new keys |

### Create
| Path | Purpose |
|---|---|
| `xl_ontology/heuristics_helpers.py` | Deterministic helpers extracted from `overall_model.py` |
| `xl_ontology/v1/__init__.py` | Pass 2 module entry |
| `xl_ontology/v1/config.py` | `Pass2Config` |
| `xl_ontology/v1/runner.py` | Pass 2 orchestrator |
| `xl_ontology/v1/agent_base.py` | `Pass2Agent` abstract class with bounded loop |
| `xl_ontology/v1/completion_criteria.py` | LLM-driven criteria derivation |
| `xl_ontology/v1/planner.py` | Plan build + parse + execute |
| `xl_ontology/v1/narrator.py` | Narration step |
| `xl_ontology/v1/overrides.py` | `emit_pass2_override` wrapper |
| `xl_ontology/v1/stages/{overall,inputs,results,calculations}.py` | One per stage |
| `xl_ontology/v1/prompts/*.txt` | LLM prompt templates (~12 files) |
| `xl_ontology/tests/test_v1_pass1_cleanup.py` | Pass 1 cleanup regression |
| `xl_ontology/tests/test_v1_writers.py` | New writers round-trip |
| `xl_ontology/v1/tests/test_completion_criteria.py` | Criteria derivation |
| `xl_ontology/v1/tests/test_planner_executor.py` | Bounded loop |
| `xl_ontology/v1/tests/test_overrides.py` | Pass-2 override emission |
| `xl_ontology/v1/tests/test_e2e_synthetic.py` | Full Pass 2 end-to-end |
| `tests/test_v1_smoke.py` | Imports + CLI + viewer smoke |

### Open decision: where do Pass 2 prompts live?

Two options:
- **(a) Pack-aware prompts:** put templates in `xl_ontology/packs/bim_v0/pass_2_*.md` and add to `pack_loader.expected_files`. **Pro:** ontology-specific tuning per pack (different BIMs per pack). **Con:** every new pack must ship 12+ prompt files.
- **(b) Package-level prompts:** put templates in `xl_ontology/v1/prompts/*.txt`. **Pro:** simpler, one source of truth. **Con:** non-bim packs would need to override.

**Recommendation: (b)** for v1. Add a hook for pack-level overrides later if needed (the pack's `.context.md` files already inject ontology-specific context into the prompt at runtime, which gets us most of the per-pack tuning without per-pack templates).

## Files reused (no change)

- `xl_ontology/budget.py` — `LlmBudget`, `BudgetExhausted`
- `xl_ontology/llm_complete.py` — `resolve_llm_complete`
- `xl_ontology/trace.py` — `TraceWriter`, `NodeTypeOverrideWriter` (already support `append=True`)
- `xl_ontology/tools/writers.py` — `record_node_type_override`, `record_classification`, et al.
- `xl_ontology/tools/lexicon.py`, `cells.py`, `dag_tools.py`, `discovery.py` — entire tool registry except the additions in V1.C
- `core/workbook_accessor.py` — used by `read_overview_sheets`

## Out of scope

- **Unclassified-table resolver** — Pass 2 may *narrate* unclassified tables but does not assign them roles (spec §7.3 / §15)
- **Open-ended ReAct (Solution 3)** — bounded loop only (spec §13)
- **Pass 2 ranking of important tables** — calc stage names 1–2 important; no scored ranking (spec §15)
- **Cross-workbook comparison** — single workbook per run
- **User-defined ontology packs for Pass 2 prompts** — bim_v0 prompts only; multi-pack template extension is a v1.1 question
- **Real-time progress streaming** — UI tails artifacts on disk; no socket protocol

## Risks

| Risk | Mitigation |
|---|---|
| **Pass 2 LLM cost overshoots cap.** | Hard cap 20 enforced via shared `LlmBudget`; budget-exhausted path produces partial findings rather than crashing (spec §6.5). Phase-E spot-check (V1.H) validates real load. |
| **Completion criteria are gibberish.** | Structural hint (spec §5.2) anchors derivation in workbook reality. Audit log records every criterion with `rationale` + `source_signal` for post-run review. |
| **Plan-execute drift** (LLM names a tool that doesn't exist or wrong args). | `parse_plan` validates against the standalone-functions registry at parse time; emit `ToolCallFailed` event and skip the step rather than crash; the next iteration's planner sees the failed tool and re-plans. |
| **Pack loader fails on Pass-2 prompt files.** | Decision (b) above — Pass-2 prompts live in `xl_ontology/v1/prompts/`, not in the pack. Pack loader is untouched. |
| **DAG-only filter incorrectly excludes valid tables.** | Pass 1 runs **identically** today on `streamlit_run_enco` after the filter — tests will assert the classified-table count drops by no more than the count of non-DAG tables observed in production. |
| **Spec/code drift on `LlmFallbackHandler`.** | Spec mentions it; code does not implement it. Plan calls this out — no removal needed. |
| **Pass 2 crashes interrupt Pass 1's already-written artifacts.** | Pass 1 is committed before Pass 2 starts (writes are atomic). If Pass 2 fails, Pass 1's artifacts remain valid; Pass 2's partial artifacts get a single `WarningEvent` and a stub `model_understanding.json` recording the failure. |

## Verification

Per phase, plus a global gate at the end:

1. **V1.A:** `pytest xl_ontology/tests/test_v1_pass1_cleanup.py xl_ontology/tests/ tests/test_smoke.py -q` green; rerun `scripts/run_ontology.py --run-id streamlit_run_enco --pass1-only`; **0 LLM calls**, 3 agents complete, summary_model_v0.json populated.
2. **V1.B:** `pytest xl_ontology/tests/test_v1_writers.py -q` green; new path helpers point to the right Path objects.
3. **V1.C:** `pytest xl_ontology/v1/tests/ -q` (just the read_overview_sheets test) green.
4. **V1.D:** `pytest xl_ontology/v1/tests/test_planner_executor.py -q` green with mocked LLM.
5. **V1.E:** Stage 2.1 fixture test green; `Stage21Output.concepts` populated.
6. **V1.F:** Stage 2.2 / 2.3 / 2.4 fixture tests green; mismatch detection produces a non-empty `cross_agent_findings.jsonl` for the synthetic-mismatch fixture.
7. **V1.G:** Orchestrator opens; new sidebar checkbox toggles Pass 2; three new viewer tabs render with empty + populated fixtures.
8. **V1.H (gate):** real-LLM run on `streamlit_run_enco`:
   - Pass 1 LLM calls = 0
   - Pass 2 LLM calls ≤ 20
   - `model_understanding.md` reads sensibly (intervention, comparators, country, time horizon all named)
   - `model_understanding.json` has `completion_criteria` for stages 2.2 / 2.3 / 2.4 with rationale + source_signal
   - `cross_agent_findings.jsonl` either empty or contains plausible rows
   - `node_type_override_log.jsonl` may have appended Pass-2 corrections (rows with `signal: "pass_2_llm_correction"`)
   - Run does not crash even if Stage 2.4 hits iteration cap or budget cap
9. **Final smoke:** `py -3 -m pytest xl_ontology/tests/ xl_ontology/v1/tests/ tests/ -q` all green.
