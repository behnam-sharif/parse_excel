# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

#

## What This Is

**xlgen_v3** is a monorepo Excel-to-Structural-Table pipeline for health economics and financial modeling. It deterministically converts multi-sheet Excel workbooks into structured table data for downstream ML/LLM processing.

**Pipeline stages:** `xl_init` -> `xl_parse` -> `xl_table` -> `xl_image` (optional) -> `xl_sheetagent` (optional: V3a via `run_sheetagent`, V6 enricher via `run_v6_analysis`) -> `xl_dag` (optional: v7 DAG via `run_dag_analysis`)

Single Python package **`xl_sheetagent`**: V6 modules at the package root (`runner.py`, `table_agent.py`, `v6_models.py`, …) and nested **`xl_sheetagent/xl_sheetagent/`** for V3a + legacy V1/V2 (`run_sheetagent`). Imports like `xl_sheetagent.v3a` resolve via submodule aliases (see package `__init__.py`).

| Stage | Package | Entry Point | Purpose |
|-------|---------|-------------|---------|
| 0-1 | `xl_init` | `run_init(input_path, output_path, config)` | VBA cleaning, sheet metadata, LLM triage, approve data sheets |
| 2 | `xl_parse` | `run_parse(input_path, output_path, config)` | Deterministic table candidate extraction (parallel per-sheet) |
| 3 | `xl_table` | `run_table(parse_output_path, output_path, config)` | Structural table assembly via split/merge heap |
| 4 | `xl_image` | `run_image(parse_output_path, output_path, config)` | Visual rendering, feature extraction, split candidates (optional) |
| 5 | `xl_sheetagent` | `run_sheetagent(...)` / `run_v6_analysis(...)` | V3a + legacy pipelines (nested); V6 core-first enricher (package root) |
| 6 | `xl_dag` | `run_dag_analysis(run_id, output_base, config, ...)` | v7 DAG build: reads v6 `final_table_map.json` + workbook; parses formulas; writes Cheri-facing `table_map_overview.json`, `dag.json`, `dag_summary.json` + backend `node_index.json`, `formulas.json` |

V6 emits a deterministic `final_table_map.json` (flat, DAG-consumable). The v7 DAG stage (`xl_dag/`) consumes it and produces Cheri-facing + xl_ask artifacts whose on-disk shapes match the TypeScript contracts in `cheri_v1_docs/*.schema.ts`.

The string `xl_kg` must not appear in any `.py` file under the stage directories (enforced by `TestNoLegacyKgSubstring`).

## Running

```bash
# Install
pip install -r requirements.txt

# CLI (individual stages)
py -3 scripts/run_init.py --input path/to/workbook.xlsx --run-id myrun --no-interactive
py -3 scripts/run_parse.py --run-id myrun
py -3 scripts/run_table.py --run-id myrun

# Full pipeline
py -3 scripts/run_pipeline.py --input path/to/workbook.xlsx --run-id myrun

# Full pipeline including image stage
py -3 scripts/run_pipeline.py --input path/to/workbook.xlsx --run-id myrun --with-image

# Full pipeline including sheetagent
py -3 scripts/run_pipeline.py --input path/to/workbook.xlsx --run-id myrun --with-sheetagent

# Full pipeline including xl_dag (v7 DAG build; requires v6 finalize output)
py -3 scripts/run_pipeline.py --input path/to/workbook.xlsx --run-id myrun --with-sheetagent --with-dag

# xl_dag alone (after v6 finalize artifacts exist)
py -3 scripts/run_dag.py --run-id myrun

# Image stage alone (after parse/table artifacts exist)
py -3 scripts/run_image.py --run-id myrun

# Sheet agent (V3a — LLM-driven)
py -3 scripts/run_sheetagent.py --run-id myrun --v3a

# Sheet agent (V6 — core-first + LLM gate)
py -3 scripts/run_sheetagent.py --run-id myrun --v6

# Streamlit orchestrator
py -3 -m streamlit run apps/orchestrator/app.py

# Tests
py -3 -m pytest tests/
py -3 -m pytest tests/test_smoke.py::TestImports   # single test class
```

## Architecture

### Run Root Policy

All artifacts for a run live under `data/output/<run_id>/` with subfolders:

| Folder | Stage | Key Outputs |
|--------|-------|-------------|
| `init_output/` | xl_init | `cleaned_workbook.xlsx`, `approved_sheets.json`, `sheet_classifications.json` |
| `parse_output/` | xl_parse | `candidate_rows.jsonl`, `cut_scores.jsonl`, `coverage_report.json` (flat, no timestamp subdirs) |
| `table_output/` | xl_table | Structural table objects |
| `image_output/` | xl_image | `images/`, `features/`, `features_index.jsonl`, `qc/` |
| `sheetagent_output/` | xl_sheetagent (V3a) | `v3a_results.json` |
| `v6_sheetagent_output/` | xl_sheetagent (V6) | `tables.json`, `resolved_tables.json`, `final_table_map.json` (when `enable_finalize=true`), `summary.json`, `audit/llm_calls.jsonl` |
| `dag_output/` | xl_dag (v7) | `table_map_overview.json`, `dag.json`, `dag_summary.json`, `node_index.json`, `formulas.json`, `dag_build_errors.jsonl` |
| `logs/` | all | `pipeline.log`, `xl_image.log` |

Stages must use `core/paths.py` for all path construction -- never invent alternate output roots.

### Configuration (Two-File YAML)

- `config/stages.yaml` -- all stage parameters (nested under `xl_init`, `xl_parse`, `xl_table`, `xl_image`, `xl_sheetagent`, `xl_sheetagent_v6`)
- `config/general.yaml` -- logging level/filename, output_base, orchestrator defaults

`core/config.py` loads both files into `PipelineConfig` (with `GeneralConfig` + raw dicts per stage). `core/stage_config.py` provides typed builders (`xl_init_config_from_pipeline`, `parse_config_from_pipeline`, `sheetagent_config_from_pipeline`, `sheetagent_v6_config_from_pipeline`, etc.) that map YAML dicts onto stage dataclasses with **exact key matching** -- unknown keys raise errors, no silent Python defaults in production. YAML is the single source of truth for all thresholds.

### Environment Bootstrap

`core/env_bootstrap.py` loads `.env` files with no-override semantics (existing `os.environ` vars are not replaced).

### Shared Types

`core/visual_types.py` holds `VisualSplitCandidate` and `VisualSplitConfig`, shared between `xl_table` and `xl_image` to prevent circular imports. These are the only types that cross stage boundaries.

### Shared Workbook Reader

`core/workbook_accessor.py` (`WorkbookAccessor`) is a hybrid reader used across V6:
- **Values workbook** opens once per run in `read_only=False` mode; cell reads use `ws.cell(r, c)` (O(1)) and preserve native access to `merged_cells.ranges` and cell fonts. No sheet-wide pre-scan.
- **Formula workbook** opens lazily in `read_only=True`; each requested sheet is scanned once via `iter_rows`, producing a frozen per-sheet cache (`is_formula_set` + formula text dict). All subsequent `is_formula` / `get_formula` / `formula_refs_in` calls are dict lookups.

The pipeline runner constructs one `WorkbookAccessor` and hands it to V6 so formula snapshots stay warm across sheets. V6 falls back to auto-constructing its own accessor when called standalone.

### Import Conventions

All stage packages use **relative imports** within their package:
- `from .parse_tools.models import BBox` (xl_parse)
- `from .schemas import BBoxInput` (xl_table)
- `from ..config import XlImageConfig` (xl_image subpackages use `..` for parent)

Cross-package shared types use absolute imports from core: `from core.visual_types import VisualSplitConfig`

### Key Invariants

- **All thresholds in config dataclasses** -- no magic numbers in heuristic code. `xl_parse` uses `Config` (in `parse_tools/models.py`), `xl_table` uses `XlTableConfig` (in `config.py`), `xl_image` uses `XlImageConfig` (in `config.py`), `xl_sheetagent` / V3a uses `XlSheetAgentConfig` (in `xl_sheetagent/xl_sheetagent/config.py`), V6 uses `XlSheetAgentV6Config` (in `xl_sheetagent/config.py`, ~68 fields incl. finalize + formula-view).
- **BBox convention**: All bounding boxes use 0-indexed inclusive coordinates. xl_parse: `(r1, c1, r2, c2)` via `BBox`. xl_table: `(r_top, c_left, r_bot, c_right)` via `BBoxInput`.
- **Thread safety**: `SheetAccessor` (in both xl_parse and xl_table) opens a separate openpyxl workbook per thread. Never share workbook objects across threads. xl_table's accessor caches per-sheet sparse cell data.
- **Provenance tracking**: `BlockCandidate.provenance` records the transformation chain (e.g., `"cc+bridge+expand+shrink"`). Append when mutating blocks.
- **`formula_ratio` is always 0.0** in xl_table -- workbook opened `data_only=True`; `c10_bonus_weight` must stay 0.0.
- **Cell classification** (xl_table): `_BLANK`, `_TEXT`, `_NUMBER`. Bool -> number, date -> text, `#REF!` -> text (Section 9.2 rules).
- **Loose coupling**: xl_table and xl_image never import each other directly; shared state goes through `core.visual_types`.
- **V6 is an enricher, not a splitter**: V6 consumes V3a structural hierarchy from `xl_table` (or `run_sheetagent` / V3a) and adds semantic annotations. It does not re-split tables.
- **Row tagging (Layer 1 / 2)**: Heuristic tags (`title_candidate`, `header_candidate`, `data_candidate`, etc.) feed V6; Layer 2 tags (`consumed_title`, `consumed_header`, etc.) are resolved after walks and candidate collection.
- **Core region is V6's anchor**: V6 detects core regions first (NUMBER + FORMULA connected components), validates them (strip header-like/note-like rows), then walks outward for metadata. Row tags are Layer 1 priors only; Layer 2 is resolved after walks. Headers are sliced to core column span. Title detection priority: above-header → beside-header (text left of core in header rows) → None.
- **V6 coordinate convention**: `CoreRegion.bbox` uses ABSOLUTE coordinates; `ConsumedMap` rows and grid/types arrays are LOCAL (0-indexed relative to parent bbox). Convert with `local_r = abs_r - bbox["r1"]`.

## Stage Details

### xl_init

Ingests raw workbook, strips VBA macros, samples cells, and classifies sheets via optional LLM triage.

**Config**: `XlInitConfig` -- VBA cleaning flag, LLM model (`gpt-3.5-turbo`), token limits, cell sampling, interactive mode, sheet approval flags.

**Data models** (`types_data.py`): `SheetSummary`, `SheetClassification` (class_label: `"table"|"text_only"|"references"|"unknown"`), `XlInitResult`.

**Tools** (`xl_init/tools/`): `cell_bounding_box`, `cell_extractor`, `cell_matrix_builder`, `formatting_extractor`, `image_cleaner`.

### xl_parse Pipeline (per sheet)

```
SheetAccessor -> Presence Matrix -> Block Generation (connected components)
  -> Table Framing Heuristics (multi-pass evaluate/shrink/split)
  -> Grammar Heuristics (multi-table splitting)
  -> Post-Split QC Gate -> Final Candidates -> Export
```

Processing is parallelized via `ThreadPoolExecutor`. "Shape_Info" sheets are excluded by default.

**Config**: `Config` in `parse_tools/models.py` -- bridging, framing density/size thresholds, grammar separator/header/changepoint detection, QC gate rules, merged-cell inflation, consolidation, export flags.

**Data models** (`parse_tools/models.py`): `BBox`, `BlockCandidate`, `TableDecision`, `CellFeatures`, `BlockFeatures`, `SheetResult`.

**Key modules**: `block_generation.py`, `table_framing_heuristics.py`, `grammar_heuristics.py`, `post_split_qc_gate.py`, `consolidation.py`, `ml_export.py`.

**Optional exports** (controlled by config flags): `training_data.jsonl`, `cut_scores.jsonl`, `rejected_blocks.jsonl`, `qc_events.jsonl`.

### xl_table Pipeline (per bbox)

```
MaxHeap (priority-ordered bboxes)
  -> propose_splits_structural()    [splitter.py]
  -> compute_all_metrics()          [metrics.py]
  -> qc_gate()                      [qc.py]
  -> if accepted: simulate_split() -> push children back to heap
  -> if rejected: visual fallback or finalize
  -> finalize() -> TableObject       [table_builder.py]
```

**Decision logic** uses three tiers: hard gates (size/density), soft gate (entropy/divergence criteria, >=2 of 3 required), and bonus scoring (for ranking among accepted proposals).

**Config**: `XlTableConfig` -- 150+ parameters covering size gates, structural split thresholds, bimodality, header/footer guards, top-K evaluation, bonus weights (c5-c10), gap-separator path, scan-separator path, dual C3 boundary, visual refine window, progressive visual, block type-pattern scan, windowed visual scan, adaptive scan threshold, grammar cut toggle, `other_tools` toggle.

**Key modules**: `splitter.py`, `metrics.py`, `qc.py`, `table_builder.py`, `prioritizer.py`, `block_scan.py`, `confirmation.py`, `loader.py`, `schemas.py`, `coords.py`, `agent.py`, `logger.py`.

**Schemas** (`schemas.py`): `RawCutEntry`, `MatchedCut`, `BBoxInput` (74 fields), `SplitProposal`, `MetricsResult`.

**Coordinates** (`coords.py`): `col_letter()`, `bbox_to_excel_range()`, `excel_to_bbox()`, `n_rows()`, `n_cols()` -- the only place for bbox/range conversion.

### xl_image Pipeline

Two render paths: **Excel COM** (preferred via `xlwings`) -> **matplotlib fallback**.

```
Load candidates -> Adaptive params per bbox -> Render (COM or fallback)
  -> Feature extraction (grid_score, header_band, row/col profiles)
  -> Split candidate detection (visual analysis)
  -> QC reporting (coverage, failures)
```

**Config**: `XlImageConfig` -- adaptive tuning, COM chart dimensions, fallback matplotlib DPI/cell sizing/font, edge detection (Canny + Hough), saturation/border/symmetry thresholds, split candidate rules (floor values, valley detection, top_k, min_gap), custom split rules YAML path.

**Module structure**: `render/` (COMRenderer, fallback_renderer), `io/` (candidate_loader), `features/` (extractor, split_candidates, rule_engine), `qc/` (reporter), `utils/` (paths), `adaptive/` (auto_params), `on_demand.py` (visual split scoring API).

**Outputs**: `images/<sheet>/<id>.png` + `<id>__context.png`, `features/<sheet>/<id>.json`, `features_index.jsonl`, `qc/coverage_report.json`, `qc/failures.jsonl`.

### xl_sheetagent (V3a)

LLM-driven semantic enrichment on top of xl_table's structural hierarchy. Uses agentic LLM calls for table interpretation, split refinement, and enrichment.

**Config**: `XlSheetAgentConfig` (in `xl_sheetagent/xl_sheetagent/config.py`) -- V3a routing flag, LLM model, agent parameters.

**Module structure** (under `xl_sheetagent/xl_sheetagent/`): `steps/` (step0_prefilter, step1_boundary, step2_splits, step3_headers, step4_interpretation), `modules/` (grammar_builder, section_detector, visual_skeleton, table_enricher, sheet_coordinator, text_dominant_handler), `agents/` (table_agent, split_agent, sheet_coordinator_agent, grammar_workbook_agent), `assembly/` (sheet_assembler, relationship_builder, tree_renderer, exporter), `schemas/` (agent_types, enriched_table, grammar_profile, grammar_snapshot, run_manifest, sheet_object_tree, signals), `tools/` (llm_client, payload_builder, ontology_map, provenance_tools, debug_trace, v1c_review), `v3a/` (runner, table_agent, split_agent, sheet_coordinator, cell_analysis, formatting_utils, schemas, exporter, llm_ownership, detection_agent_poc).

**Outputs**: `sheetagent_output/v3a_results.json`.

### xl_sheetagent — V6 enricher (per sheet, parallel)

V6 is a **core-first, deterministic + LLM-gated** enricher. It inverts V5's per-bbox algorithm: detect core regions first, validate them, then walk outward for metadata. V3a hierarchy, parent/child resolution, and ResolvedTable assembly are identical to V5.

```
Load V3a hierarchy (from xl_table or xl_sheetagent V3a)
  -> Per-sheet parallel (ThreadPoolExecutor)
    -> Per-table sequential (depth-first, parent before child)
      -> Step 0: Row Tagging (Layer 1 priors only)         [row_tagger.py]
      -> Step 1: Core Region Detection (connected components) [core_region_detector.py]
      -> Step 1b: Zero cores → return empty metadata
      -> Step 2: Core Region Validation (strip top/bottom)    [core_region_validator.py]
      -> Step 3: Upward Walk (header + title from core)       [upward_walk.py]
      -> Step 3b: Beside-header title fallback (if no above-header title) [upward_walk.py]
      -> Step 4: Leftward Walk (row names from core)          [leftward_walk.py]
      -> Step 5: Note Scanner (right + below core)            [note_scanner.py]
      -> Step 6: ConsumedMap + Layer 2 tag resolution
      -> Step 7: Table Role Assignment
      -> Step 7b: LLM Gate evaluation (6 triggers)            [llm_gate.py]
      -> Step 7c: Optional LLM call (consolidated per-bbox)   [prompt_builder.py]
      -> Step 7d: Merge LLM result into heuristic output
      -> Step 8: Confidence computation + Formatting           [formatting_agent.py]
    -> Reconstruct V3a hierarchy tree
    -> ResolvedTable assembly (parent + children)              [table_resolver.py]
    -> (optional) Finalize: flat DAG-ready map per sheet       [finalize.py, final_map_exporter.py]
  -> Export (tables.json, resolved_tables.json, final_table_map.json, summary.json)  [exporter.py]
```

**Config**: `XlSheetAgentV6Config` (~68 fields) -- row tagging thresholds, core region detection, core validation (top/bottom row checks, sequence detection for years/months/cycles/quarters, formula divergence, ambiguity threshold), directional walk limits (upward max rows, leftward max cols, note scan bounds), beside-header title detection (`detect_beside_header_title`, `beside_header_title_max_nonempty`), LLM gate (6 toggle flags per trigger, payload max cells, context max rows, per-sheet call limit), LLM transport (model, max tokens, temperature, retries), confidence weights (w_tags=0.25, w_header=0.25, w_core=0.20, w_llm=0.15, w_role=0.15), resolution, formatting, parallelism, audit, export mode, formula-view typing (`use_formula_view_for_types`, `core_formula_expansion_enabled`, `core_formula_expansion_ring`), finalize (`enable_finalize`, `finalize_title_max_chars`, `finalize_note_keywords`, `finalize_flatten_depth`, `finalize_emit_stats`).

**Data models** (`schemas.py`): `RowTag`, `NoteEntry`, `CoreRegion` (bbox, density, role_hint, validation_result), `FormattingInfo`, `ConsumedMap`, `V6TableResult` (adds `llm_result`, `llm_gate_triggers` vs V5), `V6ResolvedTable` (adds `metadata_source`, `llm_metadata`), `V6RunResult` (adds `final_map: Optional[FinalTableMap]`). Finalize models: `FinalMapChildEntry`, `FinalMapEntryStandalone` (kind=`"standalone"`), `FinalMapEntryParent` (kind=`"parent"`, navigation-only — contributes zero DAG nodes), `FinalTableMap` (per-sheet entry list + `finalize_version` + stats).

**Key modules**: `runner.py` (entry point + sheet-level parallelization, `WorkbookAccessor` reuse, per-sheet formula_refs built once over bbox union), `table_agent.py` (per-table orchestrator — core-first pipeline), `core_region_detector.py` (NUMBER + FORMULA connected components, density expansion, optional formula-dependency ring expansion), `core_region_validator.py` (4 top-row checks + 3 bottom-row checks), `upward_walk.py` (header + title detection walking up from validated core; headers sliced to core column span; beside-header title fallback for HEOR composite rows), `leftward_walk.py` (row name detection walking left), `note_scanner.py` (right-of-core + below-last-core), `llm_gate.py` (6 gate triggers + should_call_llm), `prompt_builder.py` (per-bbox + metadata resolution prompts, response parsing, merge_llm_result), `row_tagger.py` (Layer 1+2), `table_resolver.py` (ResolvedTable assembly — identical to V5), `formatting_agent.py`, `llm_audit.py`, `exporter.py`, `note_keywords.py`, `config.py`, `coordinates.py` (bbox ↔ Excel range helpers for finalize), `formula_refs.py` (formula-dependency graph helpers), `finalize.py` (deterministic 6-rule finalize — standalone/parent entries, flag set, footnote scan), `final_map_exporter.py` (JSON serializer for `FinalTableMap`).

**LLM gate triggers** (at most 1 consolidated LLM call per bbox):
1. `core_validation_ambiguous` — any core region flagged ambiguous by validator
2. `multi_core_ambiguity` — multiple cores with inconsistent role hints
3. `missing_header` — no headers found (local or inherited)
4. `ambiguous_inter_core_rows` — rows between cores not consumed, not clearly data
5. `coverage_gap` — >30% of bbox rows not covered by metadata or core
6. `overlap` — core regions or metadata claims overlap

**LLM dispatch**: Uses `xl_sheetagent.llm_dispatch.complete_text(client, model, prompt, max_tokens, temperature)` → `(raw_text, tokens)`.

**Thread safety**: Sheets processed in parallel; all reads flow through the shared `core/workbook_accessor.py` `WorkbookAccessor` (one values workbook + lazy per-sheet formula cache). Formula-cache build is serialized per sheet; lookups after are lockless.

**Formula typing**: `use_formula_view_for_types=true` classifies formula cells as `FORMULA` via the accessor's per-sheet formula cache (built once with a single `iter_rows` pass); false treats them as `NUMBER` from cached values and forces `core_formula_expansion_enabled=false`. Core expansion uses `formula_refs.py` to grow core regions along formula dependencies (gated by `_bbox_has_empty_column` — dense tables skip the cost).

**Hierarchy processing**: Same as V5 — parent-before-child order, V3a relationships preserved.

**Finalize**: When `enable_finalize=true`, after resolution the runner calls `finalize_sheet(sheet_name, resolved, config, grid_reader)` which produces a `FinalTableMap` of `standalone` and `parent` entries following 6 deterministic rules (no LLM). Leaves (`kind="standalone"` or any `parent` child with non-null `core_region`) are DAG-attachable; parents are navigation-only. `export_final_map` writes `final_table_map.json` beside `tables.json`.

**Outputs**: `v6_sheetagent_output/tables.json`, `resolved_tables.json`, `final_table_map.json` (when `enable_finalize=true`), `summary.json`, `audit/llm_calls.jsonl`.

**Downstream contract**: `xl_sheetagent/spec_finalize_v6.md` is the finalize implementation spec; `xl_sheetagent/spec_dag_v7.md` is the v7 DAG spec (implemented by the `xl_dag/` stage described below). V6 finalize must satisfy the node contract that `xl_dag` consumes.

**Orchestrator**: `apps/orchestrator/v6_viewer.py` — selective viewer with sheet picker, Raw V6 Nodes view (gate trigger badges), Resolved Tables view (metadata_source badges), core validation display.

### xl_dag (v7 DAG)

Deterministic DAG build layered on top of v6 finalize. Reads `final_table_map.json` + `summary.json` + the cleaned workbook, parses every formula inside a core region, resolves references to nodes, and writes five JSON artifacts matching `cheri_v1_docs/*.schema.ts` (for Cheri) plus two backend-only artifacts (for xl_ask).

```
Load final_table_map.json + summary.json (readers.py)
  → Resolve workbook_name from xl_init artifacts (workbook_name.py)
  → Build table_map_overview.json from entries (overview_builder.py)
  → Load named ranges from workbook (named_ranges.py)
  → Build DagNodes per leaf (node_builder.py)
        - group formulas per column by AST-shape (formula_parser.py)
        - emit representative_formulas, referenced_cells/ranges/named_ranges
        - null-core leaves → 'no_representative_formula' flag
  → Resolve edges (edge_builder.py)
        - direct_cell / range / cross_sheet / named_range / unresolved
        - unresolved reasons: indirect / offset / structured_ref / external_workbook
          / outside_core_regions / array_spill
  → Flag circular references via networkx.simple_cycles (circular_detection.py)
  → Assemble DagFile with build_stats (dag_assembler.py)
  → Build dag_summary.json aggregates + warnings (summary_builder.py)
  → Build node_index.json + formulas.json (indices_builder.py / formulas_builder.py)
  → Strict-order JSON writers (exporter.py)
```

**Config**: `XlDagConfig` (9 fields) — `build_version`, `include_null_core_nodes`, `emit_formulas_json`, `emit_node_index`, `emit_errors_jsonl`, `low_context_missing_threshold`, `max_representative_formulas_per_col`, `sheet_slug_lowercase`, `max_workers`. All loaded from `config/stages.yaml xl_dag:` with strict exact-key matching.

**Data models** (`xl_dag/schemas.py`): mirror the `cheri_v1_docs/*.schema.ts` interfaces 1:1. Output: `DagNode`, `DagEdge`, `DagFile`, `DagBuildStats`, `DagSummary`, `DagSheetSummary`, `DagSummaryWarnings`, `SheetEdgeBundle`, `NamedRangeDefinition`, `TableMapOverview`, `TableMapSheetOverview`, `FootnoteNoteRef`, `CellAddress`, `DagBbox`. Internal-only: `FormulaAst`, `ExtractedRefs`, `RepresentativeFormula`.

**Key invariants**:
- Field names on disk match the TS contract verbatim (`footnotes_notes_refs`, `source_flags` from finalize merged into node `flags`, `node_type`, `parent_path`).
- `node_id = {sheet_slug}__{table_id}__0` (core_idx always 0 in MVP).
- `edge_id = {source_node_id}__TO__{target_node_id}`.
- Unresolved edges use `source_node_id = "__UNRESOLVED__{reason}"`; edges are never silently dropped.
- `referenced_ranges` preserves the raw Excel string (e.g. `"Inputs!B5:B20"`), never normalized.
- Every leaf (including null-core) becomes a node (spec §7.1, §9); parents yield zero nodes.
- The extra per-sheet `stats` block in real `final_table_map.json` is NOT in `tableMap.schema.ts` — `xl_dag` ignores it and recomputes overview counts from `entries`.

**Outputs** (`data/output/<run_id>/dag_output/`):
- `table_map_overview.json` — Cheri overview cards (`tableMapOverview.schema.ts`)
- `dag.json` — nodes + edges + named_ranges + build_stats (`dag.schema.ts`)
- `dag_summary.json` — aggregate badges + warnings banner (`dagSummary.schema.ts`)
- `node_index.json` — backend-only; `by_id`, `by_sheet`, `by_node_type`, `by_table_id`, `cell_to_node`
- `formulas.json` — backend-only; representative formulas + shallow ASTs per node
- `dag_build_errors.jsonl` — per-node failures (empty when clean)

**Out of scope for xl_dag** (handled elsewhere or post-MVP): `subgraph.schema.ts` (Cheri extracts client-side via BFS over `dag.json`), `ask.schema.ts` (xl_ask output), interactive DAG visualization, formula evaluation, cross-workbook resolution, structured table refs (flagged only).

## Dependencies

Core: `openpyxl`, `pandas`, `numpy`, `pyyaml`, `python-dotenv`
LLM: `openai` (xl_init triage, xl_sheetagent V3a agents, optional V6 LLM gate / resolution)
Image: `xlwings` (COM), `opencv-python`, `Pillow`, `matplotlib`, `scikit-image`, `scipy`
UI: `streamlit` (orchestrator)

## Streamlit Orchestrator

`apps/orchestrator/app.py` -- upload workbook, set run ID, run stages in tabs. Session-state config overrides (not persisted to YAML). ThreadPoolExecutor-based async execution with polling/rerun. Supports V3a and V6 sheetagent runs + saved-result viewers. Viewer modules loaded via `importlib.util` (sibling files: `v3a_viewer.py`, `v6_viewer.py`).

## Test Infrastructure

- `conftest.py` at repo root adds xlgen_v3 to `sys.path`
- `tests/test_smoke.py` validates: stage imports work (including V6), YAML loads into dataclasses, no `xl_kg` substring in source files
- `tests/test_v6_core.py` -- V6 P0: package skeleton, core region detection
- `tests/test_v6_core_validator.py` -- V6 P1: core region validation (top/bottom row checks)
- `tests/test_v6_walks.py` -- V6 P1: upward walk (header/title), leftward walk (row names)
- `tests/test_v6_notes.py` -- V6 P1: note scanner (right + below core)
- `tests/test_v6_hierarchy.py` -- V6 P1: hierarchy processing, parent-before-child order
- `tests/test_v6_llm_gate.py` -- V6 P2: LLM gate triggers, prompt builder, response parsing, merge logic
- `tests/test_v6_llm_gate_preservation.py` -- V6: gate trigger propagation through resolution + finalize
- `tests/test_v6_p3_integration.py` -- V6 P3: table_resolver+prompt_builder import, exporter serialization (llm_result/llm_gate_triggers/metadata_source/llm_metadata), config pipeline, paths, orchestrator viewer, round-trip export
- `tests/test_v6_core_expansion.py` -- V6: formula-dependency core expansion (ring, empty-column gate)
- `tests/test_v6_formula_typing_flag.py` -- V6: `use_formula_view_for_types` on/off behavior and interaction with core expansion
- `tests/test_v6_finalize_schema.py` -- V6 finalize: dataclass surface for `FinalMapEntryStandalone`/`Parent`/`FinalMapChildEntry`/`FinalTableMap`
- `tests/test_v6_finalize_rules.py` -- V6 finalize: 6 deterministic rules (standalone vs parent, child promotion, metadata absorption)
- `tests/test_v6_finalize_coordinates.py` -- V6 finalize: bbox ↔ Excel range serialization via `coordinates.py`
- `tests/test_v6_finalize_flags.py` -- V6 finalize: `source_flags` emission (low_context, no_representative_formula, etc.)
- `tests/test_v6_finalize_footnotes.py` -- V6 finalize: footnote/note scanning + `footnotes_notes_refs` attachment
- `tests/test_v3a_*.py` -- V3a candidate rules, hierarchy, multi-split, row names with formulas
- `tests/test_dag_config.py` -- xl_dag Phase 0: YAML round-trip, exact-key enforcement
- `tests/test_dag_overview.py` -- xl_dag Phase 1: `table_map_overview.json` against real streamlit_run_simple fixture; verifies `stats` field is ignored
- `tests/test_dag_parser.py` -- xl_dag Phase 2: formula parser; cells, ranges, named ranges, cross-sheet, unresolved reasons (indirect/offset/structured/external), AST grouping
- `tests/test_dag_nodes_edges.py` -- xl_dag Phase 3: node_builder (standalones/children/parents, null-core flags), edge_builder (direct/range/cross-sheet/unresolved), circular detection
- `tests/test_dag_summary.py` -- xl_dag Phase 4: `dag_summary.json` aggregates + warnings, sheet_edge_bundles, node_index round-trip, formulas.json serialization
- `tests/test_dag_pipeline_e2e.py` -- xl_dag Phase 5: end-to-end on real fixture; asserts all 5 artifacts exist with required TS-declared keys
- `xl_sheetagent/xl_sheetagent/tests/` -- internal V3a / legacy sheetagent tests (section assignment, tree assembly, coordinator, step3 row headers, scoring, viewer loads, e2e deterministic, v1d regression)
- No pytest.ini -- run with `py -3 -m pytest tests/`

## Design Principles

1. **YAML is authority** -- no silent Python dataclass defaults override YAML values
2. **No hard-coded paths** -- all routed via `core/paths.py`
3. **Headless-importable stages** -- Streamlit UI is optional; all stages work as library calls
4. **Per-stage typed configs** -- each stage has its own dataclass + YAML section
5. **Flat parse export** -- no timestamp subdirectories in parse_output
6. **Graceful degradation** -- xl_image falls back from COM to matplotlib
7. **Deterministic first, LLM optional** -- V6 runs heuristics deterministically; LLM fires only when `enable_llm=true` and at least one LLM-gate trigger fires (or when optional metadata-resolution LLM is enabled).
8. **V3a hierarchy preserved** -- V6 respects parent/child relationships from V3a; ResolvedTable merges for consumers
9. **Single `xl_sheetagent` package** -- legacy `xl_sheetagent_v4` / `xl_sheetagent_v5` / `xl_sheetagent_v6` names are removed; V3a ships in `xl_sheetagent/xl_sheetagent/`, V6 at package root (`v6_models.py`, `runner.py`, …).
