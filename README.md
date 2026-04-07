# xlgen_v3

Excel-only structural pipeline: **xl_init → xl_parse → xl_table**, with optional **xl_image**.

This repository is a monorepo refactor of `xlgen_v2`. Stage logic is **headless** (importable without Streamlit). The **Streamlit** shell lives under `apps/orchestrator/`.

## Quick start

```bash
cd xlgen_v3
pip install -r requirements.txt
```

### Configuration

- `config/stages.yaml` — all stage parameters (`xl_init`, `xl_parse`, `xl_table`, `xl_image`).
- `config/general.yaml` — logging and orchestrator defaults.

`core/config.py` loads both files. Runtime entry points use these YAML sections unless you pass an explicit config object.

### CLI

```bash
py -3 scripts/run_init.py --input path/to/workbook.xlsx --run-id myrun --no-interactive
py -3 scripts/run_parse.py --run-id myrun
py -3 scripts/run_table.py --run-id myrun
py -3 scripts/run_pipeline.py --input path/to/workbook.xlsx --run-id myrun
```

Outputs are under `data/output/<run_id>/` (`init_output/`, `parse_output/`, `table_output/`, `logs/`).

### Streamlit orchestrator

```bash
py -3 -m streamlit run apps/orchestrator/app.py
```

Upload a workbook, set a run ID, then run **xl_init**, **xl_parse**, and **xl_table** in order. Session-only config overrides are available in the sidebar expander (they do not write back to YAML).

## Documentation

- `MIGRATION.md` — mapping from xlgen_v2 and rename notes.
- `ASSUMPTIONS.md` — deferred features and gaps.
- `docs/architecture.md` — stages, artifacts, paths policy.

## Future work

True dependency graphs, formula AST engines, and ontology layers are **out of scope** for this repo (see Claude Code prompt).
