# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Shell Configuration

The Bash tool on this machine is broken (exit code 3221225477).
DO NOT use the Bash tool. Use Write/Edit tools only, or write scripts for the user to run manually.

## What This Is

**xlgen_v3** is a monorepo Excel-to-Structural-Table pipeline for health economics and financial modeling. It deterministically converts multi-sheet Excel workbooks into structured table data for downstream ML/LLM processing.

**Pipeline stages:** `xl_init` -> `xl_parse` -> `xl_table` -> `xl_image` (optional)

| Stage | Package | Entry Point | Purpose |
|-------|---------|-------------|---------|
| 0-1 | `xl_init` | `run_init(input_path, output_path, config)` | VBA cleaning, sheet metadata, LLM triage, approve data sheets |
| 2 | `xl_parse` | `run_parse(input_path, output_path, config)` | Deterministic table candidate extraction (parallel per-sheet) |
| 3 | `xl_table` | `run_table(parse_output_path, output_path, config)` | Structural table assembly via split/merge heap |
| 4 | `xl_image` | `run_image(parse_output_path, output_path, config)` | Visual rendering, feature extraction, split candidates (optional) |

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

# Image stage alone (after parse/table artifacts exist)
py -3 scripts/run_image.py --run-id myrun

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
| `logs/` | all | `pipeline.log`, `xl_image.log` |

Stages must use `core/paths.py` for all path construction -- never invent alternate output roots.

### Configuration (Two-File YAML)

- `config/stages.yaml` -- all stage parameters (nested under `xl_init`, `xl_parse`, `xl_table`, `xl_image`)
- `config/general.yaml` -- logging level/filename, output_base, orchestrator defaults

`core/config.py` loads both files into `PipelineConfig` (with `GeneralConfig` + raw dicts per stage). `core/stage_config.py` provides typed builders (`xl_init_config_from_pipeline`, `parse_config_from_pipeline`, etc.) that map YAML dicts onto stage dataclasses with **exact key matching** -- unknown keys raise errors, no silent Python defaults in production. YAML is the single source of truth for all thresholds.

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

- **All thresholds in config dataclasses** -- no magic numbers in heuristic code. `xl_parse` uses `Config` (in `parse_tools/models.py`), `xl_table` uses `XlTableConfig` (in `config.py`), `xl_image` uses `XlImageConfig` (in `config.py`).
- **BBox convention**: All bounding boxes use 0-indexed inclusive coordinates. xl_parse: `(r1, c1, r2, c2)` via `BBox`. xl_table: `(r_top, c_left, r_bot, c_right)` via `BBoxInput`.
- **Thread safety**: `SheetAccessor` (in both xl_parse and xl_table) opens a separate openpyxl workbook per thread. Never share workbook objects across threads. xl_table's accessor caches per-sheet sparse cell data.
- **Provenance tracking**: `BlockCandidate.provenance` records the transformation chain (e.g., `"cc+bridge+expand+shrink"`). Append when mutating blocks.
- **`formula_ratio` is always 0.0** in xl_table -- workbook opened `data_only=True`; `c10_bonus_weight` must stay 0.0.
- **Cell classification** (xl_table): `_BLANK`, `_TEXT`, `_NUMBER`. Bool -> number, date -> text, `#REF!` -> text (Section 9.2 rules).
- **Loose coupling**: xl_table and xl_image never import each other directly; shared state goes through `core.visual_types`.

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

## Dependencies

Core: `openpyxl`, `pandas`, `numpy`, `pyyaml`, `python-dotenv`
LLM: `openai` (xl_init triage)
Image: `xlwings` (COM), `opencv-python`, `Pillow`, `matplotlib`, `scikit-image`, `scipy`
UI: `streamlit` (orchestrator)

## Streamlit Orchestrator

`apps/orchestrator/app.py` -- upload workbook, set run ID, run stages in tabs. Session-state config overrides (not persisted to YAML). ThreadPoolExecutor-based async execution with polling/rerun.

## Test Infrastructure

- `conftest.py` at repo root adds xlgen_v3 to `sys.path`
- `tests/test_smoke.py` validates: stage imports work, YAML loads into dataclasses, no `xl_kg` substring in source files
- No pytest.ini -- run with `py -3 -m pytest tests/`

## Design Principles

1. **YAML is authority** -- no silent Python dataclass defaults override YAML values
2. **No hard-coded paths** -- all routed via `core/paths.py`
3. **Headless-importable stages** -- Streamlit UI is optional; all stages work as library calls
4. **Per-stage typed configs** -- each stage has its own dataclass + YAML section
5. **Flat parse export** -- no timestamp subdirectories in parse_output
6. **Graceful degradation** -- xl_image falls back from COM to matplotlib
