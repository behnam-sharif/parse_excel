# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Shell Configuration

The Bash tool on this machine is broken (exit code 3221225477).
DO NOT use the Bash tool. Use Write/Edit tools only, or write scripts for the user to run manually.

## What This Is

**xlgen_v3** is a monorepo Excel-to-Structural-Table pipeline for health economics and financial modeling. It deterministically converts multi-sheet Excel workbooks into structured table data for downstream ML/LLM processing.

**Pipeline stages:** `xl_init` → `xl_parse` → `xl_table` → `xl_image` (optional)

| Stage | Package | Entry Point | Purpose |
|-------|---------|-------------|---------|
| 0-1 | `xl_init` | `run_init()` | VBA cleaning, sheet metadata, approve all data sheets |
| 2 | `xl_parse` | `run_parse()` | Deterministic table candidate extraction |
| 3 | `xl_table` | `run_table()` | Structural table assembly via split/merge |
| 4 | `xl_image` | `run_image()` | Visual rendering and image features (optional) |

`xl_table` was renamed from `xl_kg` in xlgen_v2. The string `xl_kg` must not appear in any `.py` file under the stage directories (enforced by `TestNoLegacyKgSubstring`).

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

# Streamlit orchestrator
py -3 -m streamlit run apps/orchestrator/app.py

# Tests
py -3 -m pytest tests/
py -3 -m pytest tests/test_smoke.py::TestImports   # single test class
```

## Architecture

### Run Root Policy

All artifacts for a run live under `data/output/<run_id>/` with subfolders: `init_output/`, `parse_output/`, `table_output/`, `image_output/`, `logs/`. Stages must use `core/paths.py` for all path construction — never invent alternate output roots.

### Configuration (Two-File YAML)

- `config/stages.yaml` — all stage parameters (nested under `xl_init`, `xl_parse`, `xl_table`, `xl_image`)
- `config/general.yaml` — logging and orchestrator defaults

`core/config.py` loads both files. `core/stage_config.py` maps YAML dicts onto stage dataclasses with **exact key matching** — unknown keys raise errors, no silent Python defaults in production.

### Shared Types

`core/visual_types.py` holds `VisualSplitCandidate` and `VisualSplitConfig`, shared between `xl_table` and `xl_image` to prevent circular imports.

### Import Conventions

All stage packages use **relative imports** within their package:
- `from .parse_tools.models import BBox` (xl_parse)
- `from .schemas import BBoxInput` (xl_table)
- `from ..config import XlImageConfig` (xl_image subpackages use `..` for parent)

Cross-package shared types use absolute imports from core: `from core.visual_types import VisualSplitConfig`

### Key Invariants

- **All thresholds in config dataclasses** — no magic numbers in heuristic code. `xl_parse` uses `Config` (in `parse_tools/models.py`), `xl_table` uses `XlTableConfig` (in `config.py`), `xl_image` uses `XlImageConfig`.
- **BBox convention**: All bounding boxes use 0-indexed inclusive coordinates. xl_parse: `(r1, c1, r2, c2)` via `BBox`. xl_table: `(r_top, c_left, r_bot, c_right)` via `BBoxInput`.
- **Thread safety**: `SheetAccessor` (in both xl_parse and xl_table) opens a separate openpyxl workbook per thread. Never share workbook objects across threads.
- **Provenance tracking**: `BlockCandidate.provenance` records the transformation chain (e.g., `"cc+bridge+expand+shrink"`). Append when mutating blocks.
- **`formula_ratio` is always 0.0** in xl_table — workbook opened `data_only=True`; `c10_bonus_weight` must stay 0.0.

### xl_parse Pipeline (per sheet)

```
SheetAccessor → Presence Matrix → Block Generation (connected components)
  → Table Framing Heuristics (multi-pass evaluate/shrink/split)
  → Grammar Heuristics (multi-table splitting)
  → Post-Split QC Gate → Final Candidates → Export
```

### xl_table Pipeline (per bbox)

```
MaxHeap (priority-ordered bboxes)
  → propose_splits_structural()    [splitter.py]
  → compute_all_metrics()          [metrics.py]
  → qc_gate()                      [qc.py]
  → if accepted: simulate_split() → push children back to heap
  → if rejected: visual fallback or finalize
  → finalize() → TableObject       [table_builder.py]
```

xl_table's decision logic uses three tiers: hard gates (size/density), soft gate (entropy/divergence criteria, ≥2 of 3 required), and bonus scoring (for ranking among accepted proposals).

## Test Infrastructure

- `conftest.py` at repo root adds xlgen_v3 to `sys.path`
- `tests/test_smoke.py` validates: stage imports work, YAML loads into dataclasses, no `xl_kg` substring in source files
- No pytest.ini — run with `py -3 -m pytest tests/`
