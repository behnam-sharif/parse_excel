# parse_excel

**Turn a messy Excel workbook into clean, machine-readable tables.**

`parse_excel` finds the tables inside a workbook (even when several tables share one sheet, or a table has titles, notes and footnotes around it), works out where each table starts and ends, and writes the result as JSON. It was built for health-economics and financial models (budget-impact models, cost-effectiveness models), but it works on any workbook that is organised as tables.

It is the Excel-parsing core of **XLGEN**, a structure-first framework for understanding complex Excel models. To learn more about the approach and how it is used in HEOR (health economics and outcomes research), see the XLGEN documentation:

> **https://docs.benadyconsults.com/xlgen/**

---

## What it does

| Step | Stage | What happens | Needs an LLM? |
|------|-------|--------------|---------------|
| 1 | `xl_init` | Loads the workbook, strips VBA macros and shapes, saves a cleaned copy, lists the sheets | No |
| 2 | `xl_parse` | Scans each sheet for blocks of filled cells and proposes table candidates | No |
| 3 | `xl_table` | Splits, merges and QC-checks the candidates into final table ranges (e.g. `B3:H105`) | No |
| 4 | `xl_image` *(optional)* | Renders tables to images and extracts visual features | No |
| 5 | `xl_sheetagent` *(optional)* | Annotates each table: title, headers, row names, notes, core data region | Optional |
| 6 | `xl_dag` *(optional)* | Builds a formula dependency graph across tables | No |
| 7 | `xl_qc_repair`, `xl_ontology` *(optional)* | Repairs borderline tables and tags them with model concepts (inputs, costs, patient flow, …) | Optional |

Steps 1 to 3 are fully deterministic and run offline. The optional stages add richer semantics; some of them can call an LLM if you provide an API key, and fall back to heuristics if you do not.

---

## Requirements

- Python 3 (developed and tested with 3.13)
- macOS, Linux or Windows
- An `OPENAI_API_KEY` **only** if you want the optional LLM-assisted stages

---

## Installation

```bash
git clone https://github.com/behnam-sharif/parse_excel.git
cd parse_excel

# one-shot setup (creates .venv and installs everything)
bash setup_venv.sh
source .venv/bin/activate        # Windows: .venv\Scripts\activate
```

Or by hand:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

---

## Quick start

### 1. Parse a workbook from the command line

```bash
python scripts/run_pipeline.py --input "data/input/Example of simple BIM.xlsm" --run-id demo
```

This runs `xl_init -> xl_parse -> xl_table` and prints a short summary. Sample workbooks ship in `data/input/`.

> **Note on macro cleaning.** By default `xl_init` strips VBA macros and shapes using Microsoft Excel through `xlwings`. If Excel is not installed (or cannot be automated on your machine), the step logs a warning and the pipeline continues with an uncleaned copy of the workbook. Add `--no-vba-cleaning` to skip the step explicitly.

### 2. Look at the results

Everything for a run is written under `data/output/<run_id>/`:

```
data/output/demo/
├── init_output/
│   ├── cleaned_workbook.xlsx       # copy of your workbook (macros/shapes removed when Excel is available)
│   ├── approved_sheets.json        # sheets that were parsed
│   ├── sheet_classification.jsonl
│   └── sheet_metadata/             # hidden rows/cols, merged cells, text boxes per sheet
├── parse_output/
│   ├── candidate_rows.jsonl        # raw table candidates per sheet
│   ├── cut_scores.jsonl            # proposed split lines and their scores
│   ├── rejected_blocks.jsonl
│   ├── features.csv
│   ├── coverage_report.json
│   └── run_meta.json
├── table_output/
│   ├── accepted_tables.txt         # human-readable: one Excel range per table, per sheet
│   ├── accepted_tables.json        # same, as JSON
│   ├── structural_tables.json      # full table objects (bbox, metrics, provenance)
│   ├── tables_per_sheet.json
│   ├── decisions.jsonl             # every split/merge decision with its reason
│   ├── qc_report.json
│   └── run_summary.json
└── logs/
    └── pipeline_summary.json       # timings, counts, warnings
```

`accepted_tables.txt` is the quickest way to see what was found:

```
Sheet: Calculations
  B3:H105
  M12:AB19
  M20:AB28
  ...
```

### 3. Use it from Python

```python
import sys; sys.path.insert(0, "/path/to/parse_excel")

from core.config import load_config
from core.stage_config import parse_config_from_pipeline, xl_init_config_from_pipeline
from xl_init import run_init
from xl_parse import run_parse
from xl_table import run_table

cfg = load_config()

init = run_init("my_model.xlsx", "out/init_output",
                config=xl_init_config_from_pipeline(cfg))
run_parse(init.cleaned_workbook_path, "out/parse_output",
          config=parse_config_from_pipeline(cfg))
tables = run_table("out/parse_output", "out/table_output")

for sheet in tables.sheets:
    print(sheet.sheet_name, len(sheet.tables), "tables")
```

### 4. Use the web UI (Streamlit)

```bash
streamlit run apps/orchestrator/app.py
```

Upload a workbook, give the run an ID, and click through the stages. The UI also has viewers for the sheet-agent, DAG and ontology outputs.

---

## Running individual stages

Each stage has its own script under `scripts/`. They all take `--run-id` and read the previous stage's output from `data/output/<run_id>/`.

```bash
python scripts/run_init.py  --input my_model.xlsx --run-id demo --no-interactive
python scripts/run_parse.py --run-id demo
python scripts/run_table.py --run-id demo
```

Optional stages (run after the three core stages):

```bash
python scripts/run_image.py      --run-id demo            # visual rendering
python scripts/run_sheetagent.py --run-id demo --v6       # titles / headers / row names
python scripts/run_dag.py        --run-id demo            # formula dependency graph
python scripts/run_qc_repair.py  --run-id demo            # repair borderline tables
python scripts/run_ontology.py   --run-id demo            # concept tagging (BIM pack)
```

Or chain everything in one command:

```bash
python scripts/run_pipeline.py --input my_model.xlsx --run-id demo \
    --with-sheetagent --with-dag --with-qc-repair --with-ontology
```

Useful flags on `run_pipeline.py`:

| Flag | Effect |
|------|--------|
| `--with-image` | also run `xl_image` |
| `--with-sheetagent` | also annotate tables (titles, headers, row names) |
| `--with-dag` | also build the formula dependency graph |
| `--with-qc-repair` | also run QC repair on the table map |
| `--with-ontology` | also tag tables with model concepts |
| `--no-vba-cleaning` | keep macros; skip the cleaning step |
| `--output-base DIR` | write outputs somewhere other than `data/output` |

---

## Optional: enabling LLM-assisted stages

Only `xl_init` sheet triage, `xl_sheetagent`, `xl_qc_repair` and `xl_ontology` can use an LLM, and all of them have deterministic fallbacks. To enable the LLM paths:

1. Copy `.env.example` to `.env` and add your key:
   ```
   OPENAI_API_KEY=sk-...
   ```
2. Check it is picked up:
   ```bash
   python scripts/test_openai_key.py
   ```
3. Check the flags in `config/stages.yaml`. `xl_ontology` and `xl_qc_repair` have `use_llm: true` by default and simply fall back to deterministic mode when no key is found. LLM sheet triage in `xl_init` is off by default; set `skip_llm_triage: false` to enable it.

The `.env` file is git-ignored and is never committed.

---

## Configuration

All thresholds live in two YAML files. There are no hidden defaults in the Python code.

- `config/stages.yaml` - one section per stage (`xl_init`, `xl_parse`, `xl_table`, `xl_image`, `xl_sheetagent`, `xl_dag`, `xl_qc_repair`, `xl_ontology`). Comments in the file explain each parameter.
- `config/general.yaml` - logging and default input/output folders.

Unknown keys raise an error, so typos are caught early. The Streamlit UI lets you override values for a single session without editing the files.

---

## Project layout

```
parse_excel/
├── scripts/            # command-line entry points (run_pipeline.py, run_parse.py, ...)
├── apps/orchestrator/  # Streamlit UI
├── config/             # stages.yaml, general.yaml
├── core/               # config loading, output paths, shared workbook reader
├── xl_init/            # stage 1: clean workbook, list sheets
├── xl_parse/           # stage 2: table candidates
├── xl_table/           # stage 3: final table ranges
├── xl_image/           # optional: rendering + visual features
├── xl_sheetagent/      # optional: titles, headers, row names, notes
├── xl_dag/             # optional: formula dependency graph
├── xl_qc_repair/       # optional: QC repair of the table map
├── xl_ontology/        # optional: concept tagging (ontology packs under packs/)
├── data/input/         # sample workbooks
├── data/output/        # one folder per run_id
├── docs/               # architecture notes
└── tests/              # pytest suite
```

---

## Tests

```bash
python -m pytest tests/
```

---

## Troubleshooting

- **`ModuleNotFoundError: core`** - run the scripts from the repository root, or add the root to `sys.path` as in the Python example above.
- **A stage prints `FAILED` but the pipeline continues** - optional stages never stop the run. Check `data/output/<run_id>/logs/pipeline_summary.json` for the error text.
- **Tables are merged or split wrongly** - adjust the `xl_table` thresholds in `config/stages.yaml` (`min_rows`, `min_cols`, `min_density`, `bimodality_threshold`). The docs explain what each one controls.
- **`.xlsm` with macros** - macros and shapes are removed by `xl_init` when Microsoft Excel is available; otherwise the workbook is copied as-is. The original file is never modified.
- **`OSERROR: -609 Connection is invalid`** or similar `xlwings` errors on macOS - Excel could not be automated. Open Excel once and grant automation permission, or run with `--no-vba-cleaning`.

---

## Learn more

- **XLGEN documentation (recommended starting point):** https://docs.benadyconsults.com/xlgen/
  Covers the overall structure-first approach, each pipeline stage, configuration, outputs, and the white paper on applying this to health economic models.
- `docs/architecture.md` - stage boundaries, output path policy, configuration rules.
- `ASSUMPTIONS.md` - known gaps and deferred features.
- `CLAUDE.md` - detailed developer notes on every module (useful if you plan to extend the code).
