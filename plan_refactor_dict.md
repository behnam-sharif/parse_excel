# Refactor plan — shared WorkbookAccessor, V6 fast-path flag, xl_table parallelization, pipeline orchestrator

## Why

Three observed problems, one shared solution plus three enablers:

1. **V6 got slow.** Root cause: `_read_cell_grid` calls `ws_formula.cell(r, c)` per cell per bbox on a `read_only=True` worksheet. Random `.cell()` access on a streaming reader is O(row_scan) per call — a 50×20 bbox = 1000 slow seeks, and overlapping parent/child bboxes re-seek the same rows. Per-bbox bold and merge probing compound this.
2. **Every stage re-opens the workbook.** `xl_init`, `xl_parse`, `xl_table`, `xl_image`, `xl_sheetagent` (V3a / V5 / V6), and the future DAG v7 each open the file independently. On a 30-sheet workbook that is 6–8 ZIP parses per run plus N stages × N sheets worth of cell iteration.
3. **`xl_table` is sequential.** Parse, V3a, V5, V6 run per-sheet in parallel; `xl_table` drains one global heap single-threaded. On workbooks with many sheets this is now the slowest non-LLM stage.

The one shared solution is a `core/workbook_accessor.py` that owns both workbook modes (`data_only=True` values, `data_only=False` formulas), lazily builds a sparse dict snapshot per sheet via a single `iter_rows` scan, and serves every consumer with dict-fast lookups. The three enablers are (a) a flag to disable formula-view reads when we want pre-refactor V5-style speed, (b) per-sheet parallelization of `xl_table`, and (c) a single "Pipeline Run" orchestrator button covering init → parse → table → V3a → V6.

---

## Batch 1 — `core/workbook_accessor.py`

### Design

One module. One class. Used by every stage that reads cells.

```
core/workbook_accessor.py
├── WorkbookAccessor(workbook_path: str)
│     ├── _lock: threading.Lock                      # guards workbook opens + sheet scans
│     ├── _sheet_locks: Dict[sheet_name, Lock]       # per-sheet scan serialization
│     ├── _wb_values:  openpyxl Workbook | None      # lazy, data_only=True,  read_only=True
│     ├── _wb_formula: openpyxl Workbook | None      # lazy, data_only=False, read_only=True
│     ├── _snapshots:  Dict[sheet_name, SheetSnapshot]
│     │
│     ├── snapshot(sheet_name, *, need_formulas: bool = False) -> SheetSnapshot
│     ├── read_bbox(sheet_name, bbox) -> Tuple[grid, types]
│     ├── is_formula(sheet_name, r, c) -> bool
│     ├── get_formula(sheet_name, r, c) -> Optional[str]
│     ├── get_value(sheet_name, r, c) -> Any
│     ├── bold_rows_in(sheet_name, bbox) -> Set[int]
│     ├── merges_in(sheet_name, bbox) -> List[Tuple[int, int, int, int]]
│     ├── formula_refs_in(sheet_name, bbox) -> Dict[(r,c), List[(r,c)]]   # reuses cached formula dict
│     └── close()
│
└── SheetSnapshot  (frozen after build — lockless reads)
      ├── sheet_name: str
      ├── values:       Dict[(r, c), Any]                # sparse, non-blank only
      ├── data_types:   Dict[(r, c), str]                # "n" | "s" | "d" | "b" (from data_only=True)
      ├── formulas:     Dict[(r, c), str]                # formula text from data_only=False (optional)
      ├── is_formula:   FrozenSet[(r, c)]                # fast membership
      ├── bold_rows:    FrozenSet[int]                   # first non-empty cell is bold
      ├── merges:       Tuple[Tuple[int, int, int, int], ...]
      ├── bounds:       Tuple[int, int]                  # (max_row, max_col)
      └── has_formula_view: bool                         # False when formula view disabled/unavailable
```

### Warm pattern (one `iter_rows` pass per mode per sheet)

```python
# Under per-sheet lock; populates SheetSnapshot once.
for row_cells in ws_values.iter_rows():
    for cell in row_cells:
        r, c = cell.row - 1, cell.column - 1
        v = cell.value
        if v is not None and not (isinstance(v, str) and not v.strip()):
            values[(r, c)] = v
            data_types[(r, c)] = cell.data_type
        if cell.font and cell.font.bold and (r, c) not in bold_seen_row:
            bold_rows.add(r)
            bold_seen_row.add(r)

if need_formulas:
    for row_cells in ws_formula.iter_rows():
        for cell in row_cells:
            if cell.data_type == "f":
                formulas[(r, c)] = cell.value
                is_formula.add((r, c))
```

- `iter_rows` is the *fast path* for `read_only=True`; `.cell()` random access is the slow path we are eliminating.
- Snapshots freeze after build (tuple/FrozenSet). Lockless reads forever after.
- `need_formulas=False` never builds the formula half → never opens `_wb_formula` → zero cost when the V6 flag is off or DAG isn't running.

### Thread model

- **One accessor per run.** Owned by `run_pipeline` (or any stage entry-point called standalone).
- **Per-sheet lock guards the scan.** The first thread to touch a sheet builds the snapshot; other threads wait on the lock, then return the frozen snapshot. Warm scan per sheet per run: at most once per mode.
- **openpyxl workbook objects are not thread-safe.** Because all reads go through `iter_rows` inside the scan (which is serialized by the sheet lock), and lookups after the scan are pure dict reads on a frozen `SheetSnapshot`, we only need *one* workbook pair for the whole run. No `threading.local` needed.

### What consumers look like (replaces today's patterns)

| Today | Replaced with |
|---|---|
| `ws.cell(r+1, c+1).value` loops in `_read_cell_grid` | `accessor.read_bbox(sheet, bbox)` |
| `ws_formula.cell(r+1, c+1).data_type == "f"` per cell | `accessor.is_formula(sheet, r, c)` |
| `_detect_bold_rows` cell loop | `accessor.bold_rows_in(sheet, bbox)` |
| `_get_merge_info` iterate `ws.merged_cells.ranges` | `accessor.merges_in(sheet, bbox)` (pre-intersected) |
| `build_formula_refs(ws_formula, bbox)` | `accessor.formula_refs_in(sheet, bbox)` (reuses formula dict, no re-tokenize) |
| `xl_parse` SheetAccessor + presence matrix | thin adapter over `accessor.snapshot(sheet).values` |
| `xl_table` SheetAccessor + `get_row_features` | thin adapter over the same snapshot |

### Files to add / modify

- **new**: `core/workbook_accessor.py` — `WorkbookAccessor`, `SheetSnapshot`, tokenizer helper for `formula_refs_in` (reuses logic from `xl_sheetagent_v6/formula_refs.py::_extract_refs`).
- **new**: `tests/test_workbook_accessor.py` — sparse dict correctness, `iter_rows` vs `.cell()` parity, per-sheet lock, lazy formula workbook, `has_formula_view=False` path.
- **delete**: `xl_sheetagent_v6/formula_view.py` (superseded).
- **modify**: `xl_sheetagent_v6/runner.py` — construct or receive `WorkbookAccessor`, replace all per-bbox cell reads with `accessor.*`.
- **modify**: `xl_sheetagent_v6/table_agent.py` — `formula_peek` becomes `accessor.is_formula`-backed closure; otherwise unchanged.
- **modify**: `xl_sheetagent_v6/core_region_detector.py` — `formula_refs` now comes from accessor, no change to algorithm.
- **modify**: `xl_sheetagent_v6/formula_refs.py` — keep `_extract_refs` as a pure helper (move into accessor's `formula_refs_in`), drop the top-level `build_formula_refs` that opens worksheets.
- **modify**: `xl_parse/sheet_accessor.py` and `xl_parse/pipeline.py` — optional kwarg `accessor: Optional[WorkbookAccessor] = None`; when provided, the parse SheetAccessor defers `iter_rows`/cell reads to it. Existing standalone `run_parse(workbook_path, ...)` path builds its own accessor.
- **modify**: `xl_table/sheet_accessor.py` and `xl_table/agent.py` — same pattern: accept optional `accessor`, otherwise self-construct. `get_row_features` reads from `snapshot.values` / `snapshot.data_types`.
- **modify**: `xl_sheetagent/v3a/runner.py`, `xl_sheetagent_v5/runner.py` — same optional-accessor injection; behavior unchanged when not provided.
- **modify**: stage entry-points (`xl_parse/run.py`, `xl_table/run.py`, `xl_sheetagent/runner.py`, `xl_sheetagent_v6/runner.py`) — add `accessor: Optional[WorkbookAccessor] = None` param; construct if `None`.

### Invariants preserved

- `xl_table` still needs `formula_ratio == 0.0`. The accessor's values snapshot comes from `data_only=True`, so `data_types` contain no `"f"`. xl_table reads *only* `snapshot.values` / `snapshot.data_types` — never `snapshot.is_formula`. Contract intact.
- V3a / V5 unchanged in behavior — they don't ask for formulas.
- V6 FORMULA typing and formula-dependency expansion unchanged when the new flag (Batch 2) is enabled.

---

## Batch 2 — V6 `use_formula_view_for_types` flag (fast-mode escape hatch)

### Config

Add one field to `XlSheetAgentV6Config`:

```yaml
xl_sheetagent_v6:
  # When false, V6 never opens the data_only=False workbook and never peeks
  # formula typing. Formulas classify as NUMBER (pre-refactor V5 behavior).
  # Implicitly forces core_formula_expansion_enabled=false.
  use_formula_view_for_types: true
```

### Behavior matrix

| `use_formula_view_for_types` | `core_formula_expansion_enabled` | Effect | Speed |
|---|---|---|---|
| false | false | V5-like; formulas counted as NUMBER; no expansion | **fastest** — only values snapshot built |
| true  | false | FORMULA typing on; no expansion | one extra sheet scan (formula snapshot) |
| false | true  | Invalid — warn and force expansion off | n/a |
| true  | true  | Full correctness (today's behavior) | current baseline |

### Implementation

- `runner.py` → `snapshot(sheet, need_formulas=config.use_formula_view_for_types)`. When `false`, the formula workbook never opens.
- `_read_cell_grid` → when `snapshot.has_formula_view` is False, classify cells purely from `data_only=True` types.
- `build_formula_refs` call site → short-circuits when `use_formula_view_for_types=False`, regardless of `core_formula_expansion_enabled`. Emit a `logger.warning` on the invalid combo.
- Validator's `formula_peek` callable → returns empty list when formula view is off (callers already tolerate an empty peek).

### Files

- `xl_sheetagent_v6/config.py` + `config/stages.yaml` — add the field.
- `xl_sheetagent_v6/runner.py` — gate `need_formulas` + the formula_refs build.
- `xl_sheetagent_v6/table_agent.py` — pass a no-op `formula_peek` when disabled.
- `tests/test_v6_fast_mode.py` (new) — sheet with formulas, flag off: no `_wb_formula` opened, role_hint reflects `num_ratio` without FORMULA counts; flag on: FORMULA typing recovered.

---

## Batch 3 — Parallelize `xl_table` by sheet

### What stays sequential (must run once, before fork)

```
load_candidates(...)           # I/O
load_cuts(...) + attach_cuts
pre_validate → (valid, skipped)
per-sheet original count
global normalization denominators:
    size_norm_denom     = max area over all valid
    sep_norm_denom      = max separator score over all valid
    density_var_norm_denom = max std(row_density) over all valid
pre-fetch all_rd via SheetAccessor.get_row_features  ← reads accessor snapshots
compute per-candidate priority
```

These read data across sheets; they must complete before workers start. With the Batch 1 accessor, `pre-fetch all_rd` is dict-reads on already-warmed snapshots (parse warmed them), so this preamble is cheap.

### What runs in parallel

Group `valid` candidates by `sheet_name`. Each sheet becomes one worker task:

```python
def _run_sheet_heap(sheet_name, sheet_candidates, priorities, accessor, cfg)
    -> (finalized_tables_local, split_qc_map_local, decision_log_local)
```

Inside each worker:

- Build a **per-sheet MaxHeap** over just this sheet's candidates.
- Drain it exactly as `_run_structural_pass` does today. Children stay on the same sheet → pushed onto the same per-sheet heap.
- Write finalized tables to a **local list**.
- Write QC summaries to a **local dict** (keys are `candidate_id`; globally unique, no collision risk).
- Write decision log entries to a **local buffer** (list of records).

After all workers complete:

```python
finalized_tables = concat(per-sheet lists)
split_qc_map     = merge(per-sheet dicts)             # assert no key collision
decision_log.flush(per-sheet buffers, ordered by sheet_name then row)
```

### DecisionLogger changes

`xl_table/logger.py::DecisionLogger` today opens `decisions.jsonl` and writes live. Convert to **buffered mode**:

- `DecisionLogger` stays the single writer, but gains:
  - `DecisionLogger.spawn_buffer() -> BufferedDecisionLog` — thread-local scratch that logs into a list.
  - `DecisionLogger.flush(buffers: List[BufferedDecisionLog])` — writes all records in one pass, deterministic order (sort by `(sheet_name, r_top, c_left, candidate_id)`).
- Live-write mode can stay for non-parallel callers; pipeline path uses buffered mode.

### Config

Extend `XlTableConfig`:

```yaml
xl_table:
  max_workers: 4        # new; 1 = sequential (back-compat)
  parallel_by_sheet: true
```

When `max_workers <= 1` or `parallel_by_sheet = false`, fall through to today's sequential single-heap code path. Regression-safe.

### Correctness argument

- Each bbox's decision (propose/accept/reject/finalize) depends only on cells within that bbox and global scalars (norm denominators, config). No cross-bbox state is read or written during decision-making.
- Children of a bbox are on the same sheet. Per-sheet heap ordering is preserved.
- Cross-sheet priority ordering never affected outcomes — it only affected wall-clock ordering in logs. Output determinism is recovered by sorting on flush.

### Files

- `xl_table/agent.py` — refactor `_run_structural_pass` into a sheet-scoped function; add `run_xl_table` parallel dispatch branch.
- `xl_table/logger.py` — `BufferedDecisionLog` + `DecisionLogger.flush(buffers)`.
- `xl_table/config.py` + `config/stages.yaml` — `max_workers`, `parallel_by_sheet`.
- `xl_table/sheet_accessor.py` — accept optional shared `WorkbookAccessor`; when parallel, each worker still calls the same accessor (which is thread-safe per Batch 1).
- `tests/test_xl_table_parallel.py` (new) — determinism: sequential and parallel runs produce byte-identical `tables_per_sheet.json` and `decisions.jsonl` (after canonical sort).
- `tests/test_xl_table_parallel.py` — 1-worker path equals today's output exactly (regression guard).

---

## Batch 4 — Orchestrator "Pipeline Run" button

### UX

`apps/orchestrator/app.py` today has one tab per stage. Add a new top-level control:

- **Pipeline Run** panel at the top of the sidebar (or its own tab).
  - File upload (same widget as today's `xl_init` tab).
  - Run-ID input (default generated).
  - Checkboxes: `with V3a` (default on), `with V6` (default on), `with image` (default off).
  - Button: **Run full pipeline** → runs init → parse → table → (V3a) → (V6) in sequence, streaming logs.
  - Progress bar per stage; red banner on failure, green check on success.
  - On completion: summary card with output paths + links into existing per-stage viewer tabs.

### Execution

One background `ThreadPoolExecutor` task (same pattern as today's per-stage buttons) runs:

```python
def run_full_pipeline(upload_path, run_id, *, with_v3a, with_v6, with_image):
    accessor = WorkbookAccessor(upload_path)         # one, shared across stages
    try:
        run_init(upload_path, output_path, config)       # writes cleaned_workbook.xlsx
        accessor = WorkbookAccessor(cleaned_path)        # rebind to cleaned file
        run_parse(cleaned_path, ..., accessor=accessor)  # warms snapshots
        run_table(parse_dir, ..., accessor=accessor)     # now parallel (Batch 3)
        if with_image: run_image(parse_dir, ..., accessor=accessor)
        if with_v3a:   run_sheetagent(run_id, ..., accessor=accessor)
        if with_v6:    run_v6_analysis(run_id, ..., accessor=accessor)
    finally:
        accessor.close()
```

The accessor stays alive across stages so warm snapshots are reused — parse populates them, table/V3a/V6 read them for free.

### Files

- `apps/orchestrator/app.py` — new panel/tab; session-state plumbing for progress + logs.
- `apps/orchestrator/pipeline_runner.py` (new) — `run_full_pipeline(...)` helper, isolates the orchestration glue from Streamlit widgets for testability.
- `scripts/run_pipeline.py` — already exists; align flags (`--with-v6`, `--with-image`) so the CLI and orchestrator trigger the same `pipeline_runner.run_full_pipeline`.
- `tests/test_pipeline_runner.py` (new) — smoke test on a small fixture: init → parse → table → V6 with one shared accessor; assert zero workbook re-opens after init's cleaned output is produced.

---

## Migration order

1. **Batch 1a** — ship `core/workbook_accessor.py` + tests (no consumer changes yet). Green bar.
2. **Batch 1b** — port V6 first (current pain point). Delete `xl_sheetagent_v6/formula_view.py`. Rerun `tests/test_v6_*`. Measure speedup on `streamlit_run_simple`.
3. **Batch 2** — add `use_formula_view_for_types` flag. A/B with flag off vs on; confirm flag-off matches pre-refactor V5-style speed.
4. **Batch 3** — parallelize `xl_table` with `max_workers=1` default. Run existing xl_table tests. Flip default to 4, add determinism regression test.
5. **Batch 1c** — retrofit `xl_parse` SheetAccessor over WorkbookAccessor. Verify parse outputs byte-identical.
6. **Batch 1d** — retrofit `xl_table` SheetAccessor + `get_row_features` over WorkbookAccessor. Verify xl_table outputs byte-identical.
7. **Batch 1e** — retrofit V3a / V5 (lowest priority; both work fine today). Optional.
8. **Batch 4** — orchestrator Pipeline Run button.
9. **Follow-up (DAG v7)** — start DAG on the accessor from day one. `get_formula(sheet, r, c)` is a direct dict lookup; no per-bbox opens ever.

Each batch is independently shippable and reversible.

---

## Verification

### Unit tests

```
py -3 -m pytest tests/test_workbook_accessor.py ^
                tests/test_v6_fast_mode.py ^
                tests/test_xl_table_parallel.py ^
                tests/test_pipeline_runner.py ^
                tests/test_v6_core_expansion.py ^
                tests/test_v6_finalize_rules.py ^
                tests/test_smoke.py -v
```

Target: all new tests pass; existing 173 V6 tests and all parse/table/V3a tests stay green through every batch.

### Integration (diff outputs byte-by-byte)

Two known workbooks as canaries:

```
py -3 scripts/run_pipeline.py --input ... --run-id streamlit_run_simple --with-sheetagent --with-v6
py -3 scripts/run_pipeline.py --input ... --run-id streamlit_run_enco   --with-sheetagent --with-v6
```

After each batch, diff:

- `data/output/<run>/parse_output/candidate_rows.jsonl`
- `data/output/<run>/table_output/tables_per_sheet.json`
- `data/output/<run>/sheetagent_output/v3a_results.json`
- `data/output/<run>/v6_sheetagent_output/{tables,resolved_tables,final_table_map}.json`

Expected: identical through Batches 1–3 (determinism); Batch 2 with `use_formula_view_for_types=false` will legitimately differ on role_hint / FORMULA-bearing cells and that's the whole point of the flag.

### Wall-clock targets

| Scenario | Today | Target after Batch 1b | Target after Batch 3 |
|---|---|---|---|
| `streamlit_run_simple`, V6 only | baseline (slow) | <= 30% of baseline | <= 25% |
| Full pipeline, 30-sheet workbook | baseline | ~60% of baseline | **~25–30%** |
| V6 with `use_formula_view_for_types=false` | n/a | matches pre-refactor speed | same |

Measured via `time py -3 scripts/run_pipeline.py ...`.

---

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| `iter_rows` sparse dict misses edge cases (e.g. cells with only formatting but no value) | Snapshot's `values` is sparse on purpose — consumers that need "cell exists" check `data_types` or iterate over `merges` / `bold_rows`. Port tests for any consumer relying on blank-but-styled cells. |
| Accessor memory on huge workbooks | Sparse dict is typically <10% of a dense grid. If a single sheet blows up, add a `max_cells_per_snapshot` ceiling that falls back to on-demand `iter_rows` streaming. Not needed day one. |
| `xl_table` parallel output non-determinism | Canonical sort on flush (`sheet_name`, `r_top`, `c_left`, `candidate_id`). Regression test compares byte-for-byte against a sequential run. |
| `DecisionLogger` buffered mode hiding crashes | On worker exception, flush whatever buffer was populated, append a synthetic `"worker_crashed"` record, re-raise. Preserve today's fail-loud behavior. |
| Accessor opened against `cleaned_workbook.xlsx` before `xl_init` finishes | Pipeline runner constructs the accessor **after** `run_init` returns the cleaned path. Orchestrator enforces ordering. |
| openpyxl version differences in `iter_rows` signature | Pin `openpyxl` or add a capability check in accessor constructor; current `requirements.txt` already pins a known-good version. |
| Streamlit button re-runs on rerender (duplicate pipeline launches) | Use `st.session_state` run-token + idempotent "already running" guard, same pattern as today's per-stage buttons. |

---

## Open questions (need a user decision before implementation)

1. **Accessor ownership when a stage is run standalone** — should each stage entry-point auto-construct an accessor when `accessor=None` (simple, a bit wasteful if user chains stages by hand), or should we require explicit construction from a script (stricter, requires user cooperation)? Recommend: auto-construct with a `logger.info` noting the standalone open.
2. **Snapshot warm-strategy for xl_image** — image stage is sequential and mostly COM/matplotlib. Worth retrofitting, or leave it off the accessor? Recommend: leave it off for now; `xl_image` isn't a hot path and COM rendering dominates.
3. **DecisionLogger historical order vs canonical order** — today `decisions.jsonl` is in chronological-of-processing order. Keeping that under parallelism means a post-hoc `timestamp` column; canonical sort drops chronology. Recommend: canonical sort + keep a per-entry `seq` field for chronology if anyone downstream needs it.
4. **Backwards compat for the old `build_formula_refs(ws, bbox)` free function** — any external caller? Recommend: keep `_extract_refs` as a pure helper; delete the ws-opening wrapper (only called from V6 runner today).

Confirm these, then implement in the order above.
