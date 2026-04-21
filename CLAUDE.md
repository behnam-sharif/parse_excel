# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

#

## What This Is

**xlgen_v3** is a monorepo Excel-to-Structural-Table pipeline for health economics and financial modeling. It deterministically converts multi-sheet Excel workbooks into structured table data for downstream ML/LLM processing.

**Pipeline stages:** `xl_init` -> `xl_parse` -> `xl_table` -> `xl_image` (optional) -> `xl_sheetagent` (optional, V3a, V5, or V6)

| Stage | Package | Entry Point | Purpose |
|-------|---------|-------------|---------|
| 0-1 | `xl_init` | `run_init(input_path, output_path, config)` | VBA cleaning, sheet metadata, LLM triage, approve data sheets |
| 2 | `xl_parse` | `run_parse(input_path, output_path, config)` | Deterministic table candidate extraction (parallel per-sheet) |
| 3 | `xl_table` | `run_table(parse_output_path, output_path, config)` | Structural table assembly via split/merge heap |
| 4 | `xl_image` | `run_image(parse_output_path, output_path, config)` | Visual rendering, feature extraction, split candidates (optional) |
| 5 | `xl_sheetagent` | `run_sheetagent(run_id, output_base, config)` | V3a: LLM-driven semantic enrichment of structural tables (optional) |
| 5 | `xl_sheetagent_v5` | `run_v5_analysis(run_id, output_base, config, ...)` | V5: deterministic row tagging, header detection, core regions, optional LLM refinement (optional) |
| 5 | `xl_sheetagent_v6` | `run_v6_analysis(run_id, output_base, config, ...)` | V6: core-first metadata algorithm, directional walks, LLM gate, optional LLM per-bbox (optional) |

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

# Image stage alone (after parse/table artifacts exist)
py -3 scripts/run_image.py --run-id myrun

# Sheet agent (V3a — LLM-driven)
py -3 scripts/run_sheetagent.py --run-id myrun --v3a

# Sheet agent (V5 — deterministic + optional LLM)
py -3 scripts/run_sheetagent.py --run-id myrun --v5

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
| `v5_sheetagent_output/` | xl_sheetagent_v5 | `tables.json`, `resolved_tables.json`, `summary.json`, `audit/llm_calls.jsonl` |
| `v6_sheetagent_output/` | xl_sheetagent_v6 | `tables.json`, `resolved_tables.json`, `summary.json`, `audit/llm_calls.jsonl` |
| `logs/` | all | `pipeline.log`, `xl_image.log` |

Stages must use `core/paths.py` for all path construction -- never invent alternate output roots.

### Configuration (Two-File YAML)

- `config/stages.yaml` -- all stage parameters (nested under `xl_init`, `xl_parse`, `xl_table`, `xl_image`, `xl_sheetagent`, `xl_sheetagent_v5`, `xl_sheetagent_v6`)
- `config/general.yaml` -- logging level/filename, output_base, orchestrator defaults

`core/config.py` loads both files into `PipelineConfig` (with `GeneralConfig` + raw dicts per stage). `core/stage_config.py` provides typed builders (`xl_init_config_from_pipeline`, `parse_config_from_pipeline`, `sheetagent_config_from_pipeline`, `sheetagent_v5_config_from_pipeline`, `sheetagent_v6_config_from_pipeline`, etc.) that map YAML dicts onto stage dataclasses with **exact key matching** -- unknown keys raise errors, no silent Python defaults in production. YAML is the single source of truth for all thresholds.

### Environment Bootstrap

`core/env_bootstrap.py` loads `.env` files with no-override semantics (existing `os.environ` vars are not replaced).

### Shared Types

`core/visual_types.py` holds `VisualSplitCandidate` and `VisualSplitConfig`, shared between `xl_table` and `xl_image` to prevent circular imports. These are the only types that cross stage boundaries.

### Import Conventions

All stage packages use **relative imports** within their package:
- `from .parse_tools.models import BBox` (xl_parse)
- `from .schemas import BBoxInput` (xl_table)
- `from ..config import XlImageConfig` (xl_image subpackages use `..` for parent)

Cross-package shared types use absolute imports from core: `from core.visual_types import VisualSplitConfig`

### Key Invariants

- **All thresholds in config dataclasses** -- no magic numbers in heuristic code. `xl_parse` uses `Config` (in `parse_tools/models.py`), `xl_table` uses `XlTableConfig` (in `config.py`), `xl_image` uses `XlImageConfig` (in `config.py`), `xl_sheetagent_v5` uses `XlSheetAgentV5Config` (in `config.py`, 91 fields), `xl_sheetagent_v6` uses `XlSheetAgentV6Config` (in `config.py`, 62 fields).
- **BBox convention**: All bounding boxes use 0-indexed inclusive coordinates. xl_parse: `(r1, c1, r2, c2)` via `BBox`. xl_table: `(r_top, c_left, r_bot, c_right)` via `BBoxInput`.
- **Thread safety**: `SheetAccessor` (in both xl_parse and xl_table) opens a separate openpyxl workbook per thread. Never share workbook objects across threads. xl_table's accessor caches per-sheet sparse cell data.
- **Provenance tracking**: `BlockCandidate.provenance` records the transformation chain (e.g., `"cc+bridge+expand+shrink"`). Append when mutating blocks.
- **`formula_ratio` is always 0.0** in xl_table -- workbook opened `data_only=True`; `c10_bonus_weight` must stay 0.0.
- **Cell classification** (xl_table): `_BLANK`, `_TEXT`, `_NUMBER`. Bool -> number, date -> text, `#REF!` -> text (Section 9.2 rules).
- **Loose coupling**: xl_table and xl_image never import each other directly; shared state goes through `core.visual_types`.
- **V5/V6 are enrichers, not splitters**: `xl_sheetagent_v5` and `xl_sheetagent_v6` consume V3a structural hierarchy from `xl_table` (or `xl_sheetagent` V3a) and add semantic annotations. They do not re-split tables.
- **Row tagging is V5's backbone**: Layer 1 heuristic tags (`title_candidate`, `header_candidate`, `data_candidate`, etc.) feed into all downstream V5 logic; Layer 2 tags (`consumed_title`, `consumed_header`, etc.) are resolved after hierarchy + candidate collection.
- **Core region is V6's anchor**: V6 inverts V5's order — detects core regions first (NUMBER + FORMULA connected components), validates them (strip header-like/note-like rows), then walks outward for metadata. Row tags are Layer 1 priors only; Layer 2 is resolved after walks. Headers are sliced to core column span. Title detection priority: above-header → beside-header (text left of core in header rows) → None.
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

**Config**: `XlSheetAgentConfig` (in `xl_sheetagent/config.py`) -- V3a routing flag, LLM model, agent parameters.

**Module structure**: `steps/` (step0_prefilter, step1_boundary, step2_splits, step3_headers, step4_interpretation), `modules/` (grammar_builder, section_detector, visual_skeleton, table_enricher, sheet_coordinator, text_dominant_handler), `agents/` (table_agent, split_agent, sheet_coordinator_agent, grammar_workbook_agent), `assembly/` (sheet_assembler, relationship_builder, tree_renderer, exporter), `schemas/` (agent_types, enriched_table, grammar_profile, grammar_snapshot, run_manifest, sheet_object_tree, signals), `tools/` (llm_client, payload_builder, ontology_map, provenance_tools, debug_trace, v1c_review), `v3a/` (runner, table_agent, split_agent, sheet_coordinator, cell_analysis, formatting_utils, schemas, exporter, llm_ownership, detection_agent_poc).

**Outputs**: `sheetagent_output/v3a_results.json`.

### xl_sheetagent_v5 Pipeline (per sheet, parallel)

V5 is a **deterministic-first, LLM-optional** enricher. It wraps V3a structural hierarchy and adds semantic row-level tagging, header detection, core region discovery, formatting extraction, and ResolvedTable assembly.

```
Load V3a hierarchy (from xl_table or xl_sheetagent V3a)
  -> Per-sheet parallel (ThreadPoolExecutor)
    -> Per-table sequential (depth-first, parent before child)
      -> Row Tagging (Layer 1 heuristic priors)    [row_tagger.py]
      -> Header Detection + Inheritance              [header_detector.py]
      -> Candidate Collection (role-aware)            [candidate_collector.py]
      -> Core Region Detection (connected components) [core_region_detector.py]
      -> Optional LLM Refinement                      [prompt_builder.py]
      -> Table Role Assignment
      -> Formatting Extraction                        [formatting_agent.py]
    -> Reconstruct V3a hierarchy tree
    -> ResolvedTable assembly (parent + children)     [table_resolver.py]
  -> Export (tables.json, resolved_tables.json, summary.json) [exporter.py]
```

**Config**: `XlSheetAgentV5Config` (91 fields) -- row tagging thresholds, multi-row header detection, header inheritance, LLM refinement (model, tokens, retries -- disabled by default `use_llm=false`), core region detection, child rules, formatting extraction, parallelism, confidence weights, audit, resolution stage, export mode (`"flat"` | `"hierarchical"`, default `"hierarchical"`).

**Data models** (`schemas.py`): `RowTag` (Layer 1 + Layer 2 tags, confidence, signals), `NoteEntry`, `CoreRegion` (bbox, density, role_hint), `FormattingInfo`, `ConsumedMap`, `V5TableResult` (full enriched table with hierarchy_role, parent_table_id, split_depth, bbox, title, headers, row_names, notes, core_regions, table_role, row_tags, formatting, children), `ResolvedTable` (merged parent+children structure, child_titles, child_summaries, metadata_source), `V5RunResult`.

**Key modules**: `runner.py` (entry point + sheet-level parallelization), `table_agent.py` (per-table orchestrator), `row_tagger.py` (Layer 1+2 row tagging -- the backbone), `header_detector.py`, `candidate_collector.py`, `core_region_detector.py`, `table_resolver.py` (ResolvedTable assembly), `formatting_agent.py`, `prompt_builder.py` (LLM prompt + response parser), `llm_audit.py`, `exporter.py`, `note_keywords.py`.

**Thread safety**: Sheets processed in parallel; each thread opens its own openpyxl workbook (`data_only=True`). Audit records merged after per-sheet processing.

**Hierarchy processing**: Bboxes sorted parent-before-child (by split_depth, r1, c1). V3a parent/child relationships preserved in `V5TableResult`. `ResolvedTable` can flatten or preserve children based on `export_mode`.

**Outputs**: `v5_sheetagent_output/tables.json`, `resolved_tables.json`, `summary.json`, `audit/llm_calls.jsonl`.

**Active development plan**: `xl_sheetagent_v5/plan_sheetagent_v5_1.md` -- child-centric hierarchy, coverage recovery, multi-core materialization (phased P0-P4).

### xl_sheetagent_v6 Pipeline (per sheet, parallel)

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
  -> Export (tables.json, resolved_tables.json, summary.json)  [exporter.py]
```

**Config**: `XlSheetAgentV6Config` (62 fields) -- row tagging thresholds, core region detection, core validation (top/bottom row checks, sequence detection for years/months/cycles/quarters, formula divergence, ambiguity threshold), directional walk limits (upward max rows, leftward max cols, note scan bounds), beside-header title detection (`detect_beside_header_title`, `beside_header_title_max_nonempty`), LLM gate (6 toggle flags per trigger, payload max cells, context max rows, per-sheet call limit), LLM transport (model, max tokens, temperature, retries), confidence weights (w_tags=0.25, w_header=0.25, w_core=0.20, w_llm=0.15, w_role=0.15), resolution, formatting, parallelism, audit, export mode.

**Data models** (`schemas.py`): `RowTag`, `NoteEntry`, `CoreRegion` (bbox, density, role_hint, validation_result), `FormattingInfo`, `ConsumedMap`, `V6TableResult` (adds `llm_result`, `llm_gate_triggers` vs V5), `V6ResolvedTable` (adds `metadata_source`, `llm_metadata`), `V6RunResult`.

**Key modules**: `runner.py` (entry point + sheet-level parallelization), `table_agent.py` (per-table orchestrator — core-first pipeline), `core_region_detector.py` (NUMBER + FORMULA connected components, density expansion), `core_region_validator.py` (4 top-row checks + 3 bottom-row checks), `upward_walk.py` (header + title detection walking up from validated core; headers sliced to core column span; beside-header title fallback for HEOR composite rows), `leftward_walk.py` (row name detection walking left), `note_scanner.py` (right-of-core + below-last-core), `llm_gate.py` (6 gate triggers + should_call_llm), `prompt_builder.py` (per-bbox + metadata resolution prompts, response parsing, merge_llm_result), `row_tagger.py` (Layer 1+2), `table_resolver.py` (ResolvedTable assembly — identical to V5), `formatting_agent.py`, `llm_audit.py`, `exporter.py`, `note_keywords.py`, `config.py`.

**LLM gate triggers** (at most 1 consolidated LLM call per bbox):
1. `core_validation_ambiguous` — any core region flagged ambiguous by validator
2. `multi_core_ambiguity` — multiple cores with inconsistent role hints
3. `missing_header` — no headers found (local or inherited)
4. `ambiguous_inter_core_rows` — rows between cores not consumed, not clearly data
5. `coverage_gap` — >30% of bbox rows not covered by metadata or core
6. `overlap` — core regions or metadata claims overlap

**LLM dispatch**: Uses `xl_sheetagent_v4.llm_dispatch.complete_text(client, model, prompt, max_tokens, temperature)` → `(raw_text, tokens)`.

**Thread safety**: Same as V5 — sheets in parallel, per-thread openpyxl workbook.

**Hierarchy processing**: Same as V5 — parent-before-child order, V3a relationships preserved.

**Outputs**: `v6_sheetagent_output/tables.json`, `resolved_tables.json`, `summary.json`, `audit/llm_calls.jsonl`.

**Orchestrator**: `apps/orchestrator/v6_viewer.py` — selective viewer with sheet picker, Raw V6 Nodes view (gate trigger badges), Resolved Tables view (metadata_source badges), core validation display.

## Dependencies

Core: `openpyxl`, `pandas`, `numpy`, `pyyaml`, `python-dotenv`
LLM: `openai` (xl_init triage, xl_sheetagent V3a agents, xl_sheetagent_v5 optional refinement)
Image: `xlwings` (COM), `opencv-python`, `Pillow`, `matplotlib`, `scikit-image`, `scipy`
UI: `streamlit` (orchestrator)

## Streamlit Orchestrator

`apps/orchestrator/app.py` -- upload workbook, set run ID, run stages in tabs. Session-state config overrides (not persisted to YAML). ThreadPoolExecutor-based async execution with polling/rerun. Supports V3a, V4, V5, and V6 sheetagent runs + saved-result viewers. Viewer modules loaded via `importlib.util` (sibling files: `v3a_viewer.py`, `v4_viewer.py`, `v5_viewer.py`, `v6_viewer.py`).

## Test Infrastructure

- `conftest.py` at repo root adds xlgen_v3 to `sys.path`
- `tests/test_smoke.py` validates: stage imports work (including V5, V6), YAML loads into dataclasses, no `xl_kg` substring in source files
- `tests/test_v5_corrections.py` -- V5-specific tests: ResolvedTable schema, core region blank inclusion, table role rules, column-cut-based row names, multi-core children consistency, table resolver
- `tests/test_v6_core.py` -- V6 P0: package skeleton, core region detection
- `tests/test_v6_core_validator.py` -- V6 P1: core region validation (top/bottom row checks)
- `tests/test_v6_walks.py` -- V6 P1: upward walk (header/title), leftward walk (row names)
- `tests/test_v6_notes.py` -- V6 P1: note scanner (right + below core)
- `tests/test_v6_hierarchy.py` -- V6 P1: hierarchy processing, parent-before-child order
- `tests/test_v6_llm_gate.py` -- V6 P2: LLM gate triggers, prompt builder, response parsing, merge logic
- `tests/test_v6_p3_integration.py` -- V6 P3: table_resolver+prompt_builder import, exporter serialization (llm_result/llm_gate_triggers/metadata_source/llm_metadata), config pipeline, paths, orchestrator viewer, round-trip export
- `tests/test_v3a_*.py` -- V3a candidate rules, hierarchy, multi-split, row names with formulas
- `xl_sheetagent/tests/` -- internal V3a tests (section assignment, tree assembly, coordinator, step3 row headers, scoring, viewer loads, e2e deterministic, v1d regression)
- No pytest.ini -- run with `py -3 -m pytest tests/`

## Design Principles

1. **YAML is authority** -- no silent Python dataclass defaults override YAML values
2. **No hard-coded paths** -- all routed via `core/paths.py`
3. **Headless-importable stages** -- Streamlit UI is optional; all stages work as library calls
4. **Per-stage typed configs** -- each stage has its own dataclass + YAML section
5. **Flat parse export** -- no timestamp subdirectories in parse_output
6. **Graceful degradation** -- xl_image falls back from COM to matplotlib
7. **Deterministic first, LLM optional** -- xl_sheetagent_v5 runs all heuristics deterministically; LLM only refines if `use_llm=true`. V6 uses a 6-trigger LLM gate; LLM fires only when `enable_llm=true` and at least one trigger fires.
8. **V3a hierarchy preserved** -- V5 and V6 respect parent/child relationships from V3a; ResolvedTable merges for consumers
9. **V5 is frozen** -- no edits to `xl_sheetagent_v5/`. V6 is the active development version.
