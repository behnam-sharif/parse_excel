# xlgen_v3

Excel-only structural pipeline: **xl_init → xl_parse → xl_table**, with optional **xl_image**.

Stage logic is **headless** (importable without Streamlit). The **Streamlit** shell lives under `apps/orchestrator/`.

## Quick start

```bash
cd xlgen_v3
pip install -r requirements.txt
```

### Configuration

- `config/stages.yaml` — all stage parameters (`xl_init`, `xl_parse`, `xl_table`, `xl_image`).
- `config/general.yaml` — logging and orchestrator defaults.

`core/config.py` loads both files. `core/stage_config.py` maps YAML onto stage dataclasses with **exact key matching** (unknown keys error). Runtime entry points use these YAML sections unless you pass an explicit config object.

### CLI

```bash
py -3 scripts/run_init.py --input path/to/workbook.xlsx --run-id myrun --no-interactive
py -3 scripts/run_parse.py --run-id myrun
py -3 scripts/run_table.py --run-id myrun
py -3 scripts/run_pipeline.py --input path/to/workbook.xlsx --run-id myrun
```

Optional image stage (after parse/table artifacts exist):

```bash
py -3 scripts/run_image.py --run-id myrun
# or full pipeline including image:
py -3 scripts/run_pipeline.py --input path/to/workbook.xlsx --run-id myrun --with-image
```

### Outputs

Artifacts for a run live under **`data/output/<run_id>/`**, using **`core/paths.py`** only:

| Folder | Stage |
|--------|--------|
| `init_output/` | xl_init |
| `parse_output/` | xl_parse (flat: `candidate_rows.jsonl`, `cut_scores.jsonl`, etc.) |
| `table_output/` | xl_table |
| `image_output/` | xl_image (when run) |
| `logs/` | pipeline / stage logs |

### Streamlit orchestrator

```bash
py -3 -m streamlit run apps/orchestrator/app.py
```

Upload a workbook, set a run ID, then run **xl_init**, **xl_parse**, and **xl_table** in order. Session-only config overrides are available in the sidebar (they do not write back to YAML).

### Tests

```bash
py -3 -m pytest tests/
```

## Documentation

- `ASSUMPTIONS.md` — deferred features and gaps.
- `docs/architecture.md` — stages, artifacts, paths policy.

## Future work

True dependency graphs, formula AST engines, and ontology layers are **out of scope** for this repo unless added explicitly.
