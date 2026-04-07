# Assumptions & deferred items

## Nested `.git` (xlgen_v2)

xlgen_v2 historically had nested git roots under `xl_init/`, `xl_parse/`, `xl_kg/`, `xl_image/`. **xlgen_v3 does not copy** nested `.git` folders.

## Hard-coded paths

Stage code is expected to route outputs through **`core/paths.py`**. Any remaining hard-coded `runs/` or `outputs/` defaults should be reported as bugs.

## Streamlit

- **Orchestrator:** `apps/orchestrator/app.py` (supported).
- **Stage packages:** `xl_image/main.py` is a **stub** that points to the orchestrator; stage logic must stay importable without Streamlit.

## Track B — carry-forward & gaps

| Item | Status |
|------|--------|
| `rejected_blocks.jsonl`, dedupe, merge inflation in xl_parse | Implemented in v2 logic; verify on representative workbooks in v3 |
| `coverage_report.json` in xl_parse | Often **deferred** — may exist in xl_image instead; document per-run outputs |
| Density trim / sparse tail | Present in `Config` / pipeline; validate on edge workbooks |

Exact parity with xlgen_v2 outputs is **not** guaranteed without regression fixtures; add baselines under `tests/fixtures/` when available.

## Configuration authority

- **`config/stages.yaml`** and **`config/general.yaml`** are the sources of truth for defaults.
- The Streamlit app applies **session-only** overrides (it does not rewrite YAML by default).

## Ambiguous / illustrative paths in the original prompt

Folders such as `xl_parse/vba_code/` or `xl_table/dependency_groups/` in the high-level prompt were **illustrative**; they are not required directories in xlgen_v3 unless code references them.
