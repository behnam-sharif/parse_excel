# approach_sheetagent.md — Approach & Outcomes of `xl_sheetagent`

This document describes **what `xl_sheetagent` does, how it does it, and what it produces**. It complements the algorithm-level specs already in this folder:

- `spec_finalize_v6.md` — finalize transformation rules (V6 → flat DAG-attachable map)
- `spec_dag_v7.md` — DAG-build contract that finalize must satisfy

If you want the *whats* and *whys* of the package as a whole, read this file first; if you want the *exact rules* for finalize or DAG, read the specs.

---

## 1. What `xl_sheetagent` is

`xl_sheetagent` is the **semantic-enrichment stage** of the parse_excel pipeline. It turns the structural table candidates produced by `xl_parse` / `xl_table` into **fully described tables** that downstream consumers (the v7 DAG builder, xl_ask, Cheri viewers) can attach to without re-reading the workbook.

The package ships **two coexisting pipelines** under one Python package:

| Pipeline | Location | Role | Public entry point |
|---|---|---|---|
| **V3a** (+ legacy V1/V2) | `xl_sheetagent/xl_sheetagent/` (nested) | Splitter + hierarchy builder. Per-table agents that find a core region, scan four sides, and decide candidate ownership (with optional LLM). Recursive splitting using `xl_parse` cut scores. | `run_sheetagent(run_id, …)` |
| **V6** | `xl_sheetagent/` (package root) | Core-first enricher. Consumes V3a hierarchy and produces title / headers / row_names / notes / role / confidence per table; optional LLM gate; optional deterministic finalize for DAG consumption. | `run_v6_analysis(run_id, …)` |

V3a is the *structural* layer (where do tables live, who owns which candidate). V6 is the *semantic* layer (what does each table mean, what are its parts). The two are usually run in sequence — V6 reads V3a's output via `_load_bboxes` in `runner.py`.

---

## 2. The two pipelines

### 2.1 V3a — structural splitter + per-table agents

**Goal:** turn `xl_table`'s bbox candidates into a clean hierarchy of tables, with parents / children resolved and four kinds of candidate pieces extracted: `title`, `header_row`, `row_name`, `footnote`.

**Per-table flow** (`xl_sheetagent/xl_sheetagent/v3a/table_agent.py`):

1. Read the cell grid for the bbox (`data_only=False` so formulas are visible).
2. Optionally trim empty outer margins.
3. Find the **core data region** (numeric/formula-heavy block).
4. Scan above the core → title and header-row candidates.
5. Scan left of the core → row-name candidates.
6. Scan below / right of the core → footnote candidates.
7. (Optional) call an LLM to decide ownership of each candidate: `this_table` / `other_table` / `sheet_level`. When LLM is disabled (`v3a_skip_llm=true`) all candidates default to `this_table`.

**Per-sheet flow** (`v3a/runner.py`):

- Sheets run in parallel via `ThreadPoolExecutor` (one openpyxl workbook per thread).
- Within a sheet, tables run **sequentially** so the sheet coordinator and recursive splitter see prior decisions.
- If `v3a_hierarchical_splits=true`, after the first detection pass the runner does a **breadth-first recursive split-and-detect** up to `v3a_split_max_depth`, using the `cut_scores.jsonl` produced by `xl_parse` to drive split decisions. Each split assigns a `parent_table_id` and bumps `split_depth`. The result is a tree of `TableAgentResult`s.

**Outcomes (V3a):**

- `data/output/<run_id>/sheetagent_output/v3a_results.json` — per-sheet `TableAgentResult` trees with: `table_id`, `original_bbox`, `trimmed_bbox`, `core_data_region`, `candidates` (the four kinds above), `decision_records` (LLM ownership rationale), and the hierarchy fields (`parent_table_id`, `split_depth`, `hierarchy_role`).

V3a is *consumed by* V6: `runner._load_bboxes` flattens the V3a tree into `(r1, c1, r2, c2)` bboxes annotated with `hierarchy_role` and `parent_table_id`, then hands them to the V6 enricher.

### 2.2 V6 — core-first semantic enricher

**Goal:** for each V3a table, deterministically derive a complete semantic description, falling back to a single consolidated LLM call only when the heuristics are demonstrably ambiguous.

**Design principle (core-first):** V6 inverts V5's per-bbox algorithm. Instead of guessing where the title/header/row_name *might* be and consuming rows top-down, V6 first locates the **numeric core**, validates it, and walks **outward** from there. Row tags become Layer 1 priors only; Layer 2 (`consumed_*`, `data`) is resolved after the walks.

**Per-table pipeline** (`xl_sheetagent/table_agent.py::analyze_table`):

| Step | What it does | Module |
|---|---|---|
| 0 | Layer-1 row tagging (priors: title / header / rowname / note / data candidates) | `row_tagger.py` |
| 1 | Detect core regions: connected components over `NUMBER` ∪ `FORMULA` cells, with optional formula-dependency ring expansion | `core_region_detector.py` |
| 1b | Zero cores → V5-style header detection + restricted inheritance, return early with a metadata-only result | `header_detector.py` |
| 2 | Validate cores: strip header-like top rows (4 checks) and note-like bottom rows (3 checks); flag `ambiguous` if undecidable | `core_region_validator.py` |
| 3 | Upward walk: derive headers (sliced to core column span) + title above the core | `upward_walk.py` |
| 3b | If no above-header title found, try beside-header title (text left of core in header rows — common in HEOR composite rows) | `upward_walk.py` |
| 4 | Leftward walk: derive row names from columns left of the core | `leftward_walk.py` |
| 4b | If row names are still empty and the core touches the bbox left edge, optionally peel the first core column as labels (text-like / sequence-like) and shrink the core | `table_agent.py` |
| 5 | Note scan: right-of-core and below-last-core | `note_scanner.py` |
| 6 | Build `ConsumedMap` from walk results; resolve Layer-2 row tags | `row_tagger.py` |
| 7 | Assign deterministic table role: `input` / `calc` / `output` / `mixed` / `metadata` | `table_agent.py` |
| 7b | Evaluate LLM gate triggers (6 of them) | `llm_gate.py` |
| 7c | At most **one** consolidated LLM call per bbox if any trigger fires (gated by `enable_llm`, per-sheet call limit, LLM client available) | `prompt_builder.py`, `llm_dispatch.py` |
| 7d | Merge the LLM result back into the heuristic outputs (LLM can override title / headers / row_names / table_role) | `prompt_builder.py::merge_llm_result` |
| 8 | Compute weighted confidence (`w_tags` + `w_header` + `w_core` + `w_llm` + `w_role`) and (optionally) extract cell-level formatting | `formatting_agent.py` |

**LLM gate triggers** (each toggleable; at most one consolidated call per bbox):

1. `core_validation_ambiguous` — validator flagged any core ambiguous
2. `multi_core_ambiguity` — multiple cores with inconsistent `role_hint`s
3. `missing_header` — no headers found locally or via inheritance
4. `ambiguous_inter_core_rows` — rows between cores not consumed and not clearly data
5. `coverage_gap` — > threshold % of bbox rows neither metadata nor core
6. `overlap` — core regions or metadata claims overlap

**Per-sheet flow** (`runner.py::run_v6_analysis` → `_run_v6_sheet`):

- Sheets run in parallel via `ThreadPoolExecutor`. Tables within a sheet run **sequentially in parent-before-child order** (sorted by `split_depth`, `r1`, `c1`) so header inheritance can see donor tables.
- A single shared `core/workbook_accessor.py::WorkbookAccessor` is used for all reads:
  - **Values workbook** — `data_only=True`, `read_only=False`; `ws.cell(r, c)` is O(1) and `merged_cells.ranges` and cell fonts are available natively.
  - **Formula workbook** — `data_only=False`, `read_only=True`; opened lazily and scanned **once per sheet** with `iter_rows` to build a frozen `(is_formula_set, formula_text_dict)` cache.
  - When `use_formula_view_for_types=true`, the per-sheet `is_formula_set` is consulted to type formula cells as `FORMULA`; otherwise they classify as `NUMBER` from cached values and `core_formula_expansion_enabled` is forced off.
  - When `core_formula_expansion_enabled=true`, `formula_refs` is built **once per sheet** over the union of all bbox extents and filtered per-bbox; the bbox-level expansion is gated by `_bbox_has_empty_column` so dense tables skip the cost.
- After per-table analysis, V6 reconstructs the V3a hierarchy tree (populating `children` lists) and runs the **resolution stage** (`table_resolver.py`): each parent + its children are merged into a single `V6ResolvedTable` (default rule is *preserve children*, with a 5-rule "do not absorb" check).
- If `enable_finalize=true`, the runner then calls `finalize.py::finalize_sheet`, which applies 6 deterministic rules to the resolved tree and emits a flat `FinalTableMap` of `standalone` / `parent` entries — the leaf-attachable shape consumed by `xl_dag` (see `spec_finalize_v6.md` for the full rule list).

**Outcomes (V6):**

| Artifact | Path | Shape | Consumer |
|---|---|---|---|
| Raw V6 nodes | `data/output/<run_id>/v6_sheetagent_output/tables.json` | per-sheet `V6TableResult` lists (1 per V3a bbox); includes `core_regions`, `row_tags`, `consumed`, `llm_result`, `llm_gate_triggers`, `signals` | Streamlit `v6_viewer.py`, debugging |
| Resolved tables | `…/resolved_tables.json` | per-sheet `V6ResolvedTable` lists — parents merged with children into consumer-facing units; carries `metadata_source` (`heuristic` / `llm_per_bbox` / `llm_reconciliation`) | xl_ask, downstream display |
| Final map | `…/final_table_map.json` (only when `enable_finalize=true`) | per-sheet `FinalTableMap`: flat list of `standalone` and `parent` entries with `coordinates`, `bbox`, `core_region`, `node_type`, `parent_path`, `source_flags`, `footnotes_notes_refs` | `xl_dag` (v7) |
| Sheet summary | `…/summary.json` | per-sheet stats (table counts, role distribution, LLM-call counts, error counts) | orchestrator dashboard |
| LLM audit | `…/audit/llm_calls.jsonl` (only when `emit_audit=true`) | one row per LLM call: `call_kind`, `table_id`, `bbox`, `prompt`, `raw_response`, `tokens`, `elapsed_ms`, `success` | post-hoc review |

The **node contract** for `final_table_map.json` (every leaf with non-null `core_region` becomes a DAG node; parents contribute zero nodes) is what `spec_finalize_v6.md` enforces and what `xl_dag` reads.

---

## 3. Coordinate convention

V6 uses a hybrid convention deliberately:

- `CoreRegion.bbox`, the parent `bbox` on `V6TableResult` / `V6ResolvedTable`, and finalize's `bbox` / `core_region` are **absolute** 0-indexed inclusive `(r1, c1, r2, c2)`.
- `ConsumedMap.title_rows / header_rows / rowname_cols / note_rows`, and the per-bbox `grid` / `types` arrays, are **local** (0-indexed relative to the parent bbox).
- Conversion: `local_r = abs_r - bbox["r1"]`. Coordinate strings are formatted via `xl_table.coords.bbox_to_excel_range` and `xl_sheetagent.coordinates`.

---

## 4. Configuration & invariants

- **YAML is authority.** All thresholds for V3a (`xl_sheetagent`) and V6 (`xl_sheetagent_v6`) live in `config/stages.yaml` and load through `core/stage_config.py` with **strict exact-key matching** — unknown keys raise. There are no Python defaults that silently override YAML.
- **V6 is an enricher, not a splitter.** It must not re-split V3a tables; it only annotates them. Splitting happens upstream (V3a + xl_parse cut scores).
- **Parent-before-child.** Both pipelines process tables in a parent-before-child order so children can inherit headers from their donor parents.
- **One LLM call per bbox at most** in V6 (the consolidated per-bbox call). Per-sheet limits are enforced via `llm_call_counter`.
- **Determinism first.** Heuristics run unconditionally; LLM is opt-in and gated. Finalize is fully deterministic (no LLM).
- **No `xl_kg`.** The string `xl_kg` must not appear in any `.py` file under stage directories (enforced by `TestNoLegacyKgSubstring`).

---

## 5. Where to look in the code

| You want to … | Look at |
|---|---|
| Add a new V6 gate trigger | `llm_gate.py`, plus a config flag in `config.py` and a YAML key in `stages.yaml` |
| Change how cores are detected | `core_region_detector.py` (connected-components + density expansion); for formula-dependency ring expansion, `formula_refs.py` |
| Change how cores are validated | `core_region_validator.py` (4 top-row checks + 3 bottom-row checks; `_col_sequence_score` for years/months/cycles/quarters/enum patterns) |
| Change header / title detection | `upward_walk.py` (above-header + beside-header fallback) and `header_detector.py` (V5 fallback when no core) |
| Change row-name detection | `leftward_walk.py`; the "peel first core column" path is in `table_agent.py::_maybe_peel_first_core_col_as_row_names` |
| Change how parent + children become a single object | `table_resolver.py` |
| Change finalize rules | `finalize.py` (rules 1–6); see `spec_finalize_v6.md` for the contract |
| Change V3a per-table behaviour | `xl_sheetagent/xl_sheetagent/v3a/table_agent.py` |
| Change V3a hierarchical splitting | `xl_sheetagent/xl_sheetagent/v3a/runner.py::_recursive_split_and_detect` and `v3a/split_agent.py` |
| Inspect runs in the orchestrator | `apps/orchestrator/v6_viewer.py` (V6) and `apps/orchestrator/v3a_viewer.py` (V3a) |

---

## 6. End-to-end summary

Input: a workbook + the V3a / xl_table structural hierarchy + (optional) `xl_parse` cut_scores.

Processing: for each V3a bbox, V6 runs Steps 0–8 above; LLM is consulted at most once per bbox and only if at least one of the 6 gate triggers fires. Sheets run in parallel; within a sheet, tables run sequentially in parent-before-child order. After per-table analysis, parents and children are merged into `V6ResolvedTable`s, then optionally collapsed by 6 deterministic finalize rules into `FinalMapEntryStandalone` / `FinalMapEntryParent` entries.

Output: V3a writes `v3a_results.json`; V6 writes `tables.json`, `resolved_tables.json`, `summary.json`, optional `final_table_map.json`, and optional `audit/llm_calls.jsonl`. The final map is the contract surface for `xl_dag` (every leaf with `core_region != null` becomes one DAG node, parents contribute zero).
