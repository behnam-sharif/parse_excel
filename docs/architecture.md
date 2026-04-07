# xlgen_v3 architecture

## Stages

1. **xl_init** — ingest, optional VBA cleaning, sheet triage/approval, metadata.
2. **xl_parse** — deterministic candidates, framing, grammar cuts, QC, exports.
3. **xl_table** — structural table assembly over parse artifacts (renamed from xl_kg).
4. **xl_image** (optional) — bbox rendering and image-derived features.

## Run root policy

All artifacts for a run live under:

`data/output/<run_id>/`

Subfolders:

- `init_output/` — cleaned workbook, `approved_sheets.json`, summaries, metadata.
- `parse_output/` — candidates, features, QC, cut scores, etc.
- `table_output/` — table assembly, `decisions.jsonl`, `structural_kg.json`, …
- `image_output/` — renders and image QC when enabled.
- `logs/` — `pipeline_summary.json`, stage logs.

**Stages must not invent alternate output roots** — use `core/paths.py`.

## Configuration

- `config/stages.yaml` — nested `xl_init`, `xl_parse`, `xl_table`, `xl_image`.
- `config/general.yaml` — `logging`, `run`, `orchestrator` sections (required keys).

`core/config.py` loads both; `core/stage_config.py` maps YAML dicts onto stage dataclasses with **exact key matching** (no silent defaults from Python for production loads).

## Provenance

Each run should be identifiable by `run_id` and paths recorded in `pipeline_summary.json` (CLI) or orchestrator UI.

## Streamlit

The orchestrator uses a **single-thread pool** and **Future polling** (see `apps/orchestrator/app.py`) so long-running stages do not block the Streamlit thread unsafely.
