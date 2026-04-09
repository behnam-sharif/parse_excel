# Assumptions & deferred items

## Nested `.git` in some upstream layouts

Some monorepo Excel pipelines historically used **nested git roots** under stage folders (for example `xl_init/`, `xl_parse/`, `xl_image/`). **This repository does not use or copy** nested `.git` folders.

## Hard-coded paths

Stage code is expected to route outputs through **`core/paths.py`** (`get_*_output_path`, `get_run_root`, `get_log_path`). Any remaining hard-coded `runs/` or `outputs/` defaults should be reported as bugs.

## Streamlit

- **Orchestrator:** `apps/orchestrator/app.py` (supported).
- **`xl_image/main.py`** is a **stub** that prints the orchestrator entry point; stage logic stays importable headless via `xl_image.run_image` / `xl_image.run` (no Streamlit dependency).

## xl_parse export layout

Parse artifacts for a run are written **directly under** `data/output/<run_id>/parse_output/` (flat directory). Downstream stages resolve files by basename (for example `candidate_rows.jsonl`, `cut_scores.jsonl`, `rejected_blocks.jsonl` when enabled), not via a nested timestamp subfolder.

## Track B — carry-forward & gaps

| Item | Status |
|------|--------|
| `rejected_blocks.jsonl`, dedupe, merge inflation in xl_parse | Implemented; validate on representative workbooks |
| `coverage_report.json` in xl_parse | Written by `xl_parse/run_export.py` under `parse_output/` when a run is exported |
| Density trim / sparse tail | Present in `Config` / pipeline; validate on edge workbooks |

Exact output parity with any **external baseline** is **not** guaranteed without regression fixtures; add baselines under `tests/fixtures/` when available.

## Configuration authority

- **`config/stages.yaml`** and **`config/general.yaml`** are the sources of truth for defaults.
- The Streamlit app applies **session-only** overrides (it does not write back to YAML by default).

## Ambiguous / illustrative paths in older prompts

Folders such as `xl_parse/vba_code/` or `xl_table/dependency_groups/` in high-level descriptions were **illustrative**; they are not required directories here unless code references them.
