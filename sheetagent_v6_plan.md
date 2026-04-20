# xl_sheetagent_v6 Plan — Core-First Metadata Algorithm

## Context

v5 is shipped and deterministic. Its per-bbox algorithm is: row-tag first → consume title/header/rownames/notes → core region is residual. Row-tagging is the anchor; core region is a byproduct.

v6 **changes only the per-bbox metadata algorithm**. Everything else — V3a upstream hierarchy, parent/child splitting, per-child processing order, role-aware collection constraints, final parent/child resolution and merging — stays identical to v5.

The new per-bbox algorithm is:

**detect core region → validate core region (strip header-like rows) → walk up for header → walk up further for title → walk left for row names → walk right and down for notes.**

Rationale: in HEOR models, the formula/numeric block is the anchor of each sub-table. Walking outward from it aligns metadata detection with the actual structure of the calculation. Parent/child hierarchy remains V3a's job; v6 only changes how metadata is extracted per node in that hierarchy.

v5 is frozen — no edits to `xl_sheetagent_v5/`.

---

## Packaging

- New package: `xl_sheetagent_v6/`
- Output directory: `v6_sheetagent_output/`
- Entry point: `run_v6_analysis` in `xl_sheetagent_v6/__init__.py`
- Schema names: `V6TableResult`, `V6ResolvedTable`, `V6RunResult`, `XlSheetAgentV6Config`
- Shared dataclasses (`RowTag`, `NoteEntry`, `FormattingInfo`, `ConsumedMap`, `CoreRegion`) duplicated into `xl_sheetagent_v6/schemas.py` — do not share with v5. `CoreRegion` has the **same semantics as v5** (NUMBER + FORMULA with density expansion).

---

## Hierarchy Is Preserved From v5

This is explicit because a previous draft implied otherwise:

- **V3a runs upstream.** Same as v5. Produces bboxes with `hierarchy_role` (`parent` / `child_numeric` / `child_metadata` / `standalone`), `parent_table_id`, `split_depth`.
- **V6 consumes the hierarchy the same way v5 does.** Processes tables in parent-before-child order (sorted by `split_depth`, `r1`, `c1`).
- **Per-bbox, v6 runs the new core-region + metadata algorithm** (see below).
- **After all bboxes are processed, v6 runs parent/child resolution identical to v5** — `table_resolver.py`, preservation/absorption logic, V6ResolvedTable with `children`, `child_titles`, `child_summaries`.
- **Role-aware collection is preserved.** Parent collects title + header + notes; child_numeric collects title + row_names + footnotes; child_metadata collects title + header; standalone collects all four. The mechanism of collection changes (core-first walks instead of top-down scans) but the role constraints stay.

---

## Phase P0 — Package Skeleton + Per-Bbox Algorithm (Deterministic)

### New files: `xl_sheetagent_v6/`

| File | Source | Changes from v5 |
|---|---|---|
| `__init__.py` | New | Export `run_v6_analysis` |
| `config.py` | Adapted from v5 | `XlSheetAgentV6Config` — keep hierarchy/resolution fields, add walk + gate + core-validation fields |
| `schemas.py` | Adapted from v5 | Duplicate shared dataclasses. `CoreRegion` retains v5 semantics. New `V6TableResult`, `V6ResolvedTable`, `V6RunResult` |
| `note_keywords.py` | Verbatim copy from v5 | None |
| `row_tagger.py` | Copy from v5 | Change imports. Layer 1 still runs as priors. |
| `core_region_detector.py` | Copy from v5 | Unchanged semantics (NUMBER + FORMULA, density expansion). Only the timing changes — runs first, before consumption. |
| `core_region_validator.py` | **NEW** | Deterministic checks to strip header-like top rows of a core region |
| `table_agent.py` | Rewrite | New pipeline order. Stubs for walks in P0, wired in P1. |
| `upward_walk.py` | **NEW** (stubbed) | Header + title detection walking up from validated core |
| `leftward_walk.py` | **NEW** (stubbed) | Row name detection walking left from core |
| `note_scanner.py` | **NEW** (stubbed) | Note detection right-of-core and below-last-core |
| `table_resolver.py` | Copy from v5 | **Unchanged.** Parent/child merging, preservation/absorption, V6ResolvedTable assembly |
| `runner.py` | Adapted from v5 | Same V3a hierarchy handling as v5. Parent-before-child order. Resolution stage preserved. |
| `llm_audit.py` | Copy from v5 | Update imports |
| `formatting_agent.py` | Copy from v5 | Update type hints |
| `exporter.py` | Adapted from v5 | V6 schemas. Keep child serialization. Add `llm_result` + `llm_gate_triggers` + `core_validation_result`. |

### Integration modifications

1. **`core/config.py`**: add `xl_sheetagent_v6: Dict[str, Any]` to `PipelineConfig`, add to validation loop, add to constructor.
2. **`core/stage_config.py`**: add `sheetagent_v6_config_from_mapping(d)` and `sheetagent_v6_config_from_pipeline(pc, overrides)`.
3. **`core/paths.py`**: add `get_v6_sheetagent_output_path(run_id, base)`.
4. **`config/stages.yaml`**: add full `xl_sheetagent_v6:` section.
5. **`scripts/run_sheetagent.py`**: add `--v6` flag and dispatch block.
6. **`tests/test_smoke.py`**: v6 imports, v6 config validation, legacy-substring check.

### P0 Deliverable

`py -3 -m pytest tests/test_smoke.py tests/test_v6_core.py` passes. `py -3 scripts/run_sheetagent.py --v6 --run-id test` runs end-to-end with V3a hierarchy preserved and stub metadata from walks.

---

## Phase P1 — Core Validation + Directional Walks + Notes

### Core region validation (NEW — the main P1 addition)

**The problem:** the first row(s) of a detected core region are sometimes formulas that behave as **headers**, not data. Common HEOR patterns:

- `=2023+1`, `=2024+1`, `=2025+1` — year labels generated by formula
- `=B1+1`, `=C1+1`, `=D1+1` — cycle or period indexing
- `="Cycle " & ROW()` — textual headers built by formula
- First-row constants like `1, 2, 3, 4` with a stable delta, used as cycle/period headers

These are structurally headers even though they live in the formula/number mask and therefore got swept into the core region.

**`core_region_validator.py` checks (deterministic, in order):**

1. **Formula-string divergence.** Compare the formula text of the first row of the core region against rows 2..N. If row-1 formulas differ structurally (different referenced ranges, different operators, different constants) while rows 2..N are structurally homogeneous, row 1 is a header candidate.
2. **Sequence detection.** If row-1 values (after formula evaluation) form a monotonic sequence — years (2019, 2020, 2021...), months (Jan, Feb, Mar...), cycles (1, 2, 3... or Cycle 1, Cycle 2...), quarters (Q1, Q2...) — row 1 is a header candidate. Use regex + ordinal check.
3. **Text-vs-numeric asymmetry.** If row 1 evaluates to short text strings while rows 2..N are numeric, row 1 is a header candidate.
4. **Bold/formatting asymmetry.** If row 1 is bold and rows 2..N are not, promote the header candidacy confidence.

**Action on validation:**

- If any check fires with high confidence → **shrink the core region**: `core.r1 += 1` (or more if multiple top rows qualify). Record the stripped row(s) in `signals.core_validation_stripped_rows` for the upward walk to pick up as header.
- If checks are ambiguous (fired but low confidence, or conflict between checks) → **LLM gate rule fires** (see P2). Deterministic fallback is to NOT strip, log `core_validation_ambiguous`.
- If no checks fire → core region is already correct, proceed.

This is a single local decision per core region. No cascading — after shrinking, the upward walk naturally picks up the stripped row as a header (rules unchanged).

**Bottom-row validation (symmetric check):**

Because core detection runs before consumption (empty ConsumedMap), numeric cells in note-like bottom rows can get swept into the core region. The validator mirrors the top-row checks against the last row(s) of each core region:

1. **Note keyword match.** If row `core.r2` has text in left-adjacent columns matching note keywords (`source`, `note`, `*`, etc.), the row is a note candidate.
2. **Isolation test.** If row `core.r2` is separated from rows above by a blank row or formatting change (border, color shift), it's a note candidate.
3. **Sparse numeric signature.** If row `core.r2` has a different cell-type distribution than rows above (e.g. mostly blanks with one or two numbers, vs rows above being fully populated), it's a note candidate.

Action: if any bottom-row check fires with high confidence → shrink core: `core.r2 -= 1`. Stripped row(s) go to `signals.core_validation_stripped_bottom_rows` for the note scanner to pick up. If ambiguous → `core_validation_ambiguous` gate fires (already covered in P2). `core_validator_max_strip_rows` applies to both top and bottom.

### New files

| File | Purpose |
|---|---|
| `xl_sheetagent_v6/core_region_validator.py` | Top-row checks (4 rules) + bottom-row checks (3 rules). Returns `CoreValidationResult {top_strip_rows: int, bottom_strip_rows: int, confidence: float, reasons: List[str], ambiguous: bool}`. |
| `xl_sheetagent_v6/upward_walk.py` | Walk from `core.r1 - 1` upward within parent bbox, restricted to core's column span. Detect headers + title using v5's existing rules unchanged. Stops at bbox top, another core's bottom, or blank-after-title. |
| `xl_sheetagent_v6/leftward_walk.py` | Walk from `core.c1 - 1` leftward within parent bbox. Detect row names using v5's rules unchanged, with native column-cut integration. |
| `xl_sheetagent_v6/note_scanner.py` | Scan right-of-core (cols `core.c2+1..c2+N`, core row span) and below-last-core (rows `max_r2+1..max_r2+N`, full bbox width) using v5's note rules unchanged. |

### Files updated

- `xl_sheetagent_v6/table_agent.py` — wire real walk calls after core validation.
- `xl_sheetagent_v6/runner.py` — add formula text reading via `data_only=False` workbook handle (needed for validator check #1).

### Per-bbox pipeline (P1 shape)

```
analyze_table_v6(bbox, hierarchy_role):
  Step 0: tag_rows()                      # Layer 1 priors (unchanged from v5)
  Step 1: detect_core_regions()           # v5 semantics unchanged (NUMBER + FORMULA, density)
  Step 1b: if zero cores AND role allows
             → return empty metadata (data-only node)
  Step 2: validate_core_regions()         # NEW — strip header-like top rows
             deterministic checks (formula divergence, sequences, text/num, bold)
             if ambiguous → flag for LLM (P2), fallback: no strip
  Step 3: upward_walk(core)               # header band, then title further up
             reuse v5 header + title rules UNCHANGED
             scope: core's column span, within parent bbox
             stop: bbox top / another core's bottom / blank-after-title
             DO NOT go outside parent bbox — if nothing found, report none (same as v5)
             fallback: v5 header inheritance from V3a parent (unchanged)
  Step 4: leftward_walk(core)             # row names
             reuse v5 rowname rules UNCHANGED
             stop: another region's right edge / parent bbox left edge
             DO NOT go outside parent bbox — report none if not found
  Step 5: scan_notes(cores, bbox)         # right-of-core + below-last-core
             right: cols c2+1..c2+N within core's row span
             below: rows max_r2+1..N across parent bbox width
             reuse v5 note rules UNCHANGED
  Step 6: ConsumedMap                     # built in new order (core → header → title → rownames → notes)
  Step 7: assign_final_tags()             # Layer 2 resolution (unchanged from v5)
  Step 8: table_role + confidence
  Return V6TableResult
```

**No LLM in P1.** All ambiguities (from core validation or elsewhere) are logged but not resolved.

### P1 Deliverable

Full deterministic per-bbox pipeline. Parent/child hierarchy preserved. Parent/child resolution runs unchanged from v5 after all bboxes processed.

---

## Phase P2 — LLM Gate + Consolidated Per-Bbox Call

### Gate triggers (`llm_gate.py`)

Same pattern as v5.1, but consolidated into **one call per bbox** when triggered. Triggers:

| # | Trigger | Fires When |
|---|---|---|
| 1 | `core_validation_ambiguous` | Core validator's checks fired but low confidence OR conflicting signals |
| 2 | `multi_core_ambiguity` | ≥2 core regions within the bbox AND ambiguous whether siblings or one block split by blanks |
| 3 | `missing_header` | Upward walk found no header AND no inheritable header from V3a parent |
| 4 | `ambiguous_inter_core_rows` | Rows between cores match both "section label" and "note" heuristics with similar confidence |
| 5 | `coverage_gap` | Claimed regions (core + header + title + rownames + notes) leave a large uncovered area in the bbox |
| 6 | `overlap` | A cell claimed by two cores, or by a core and a rowname column |

None fire → no LLM call. Deterministic output ships.

### Payload-size gate

Skip LLM if `n_rows * n_cols > llm_payload_max_cells`. Log `llm_skipped_oversize`.

### Single-call design (`llm_prompt.py`)

If invoked, **exactly one LLM call per bbox** with a consolidated prompt:

1. Cell grid (values + types + formula text + formatting flags, sampled/truncated if large).
2. Deterministic proposal: core regions, core-validation result, headers, title, row names, notes — each with confidence and which rule fired.
3. Enumerated ambiguities: which triggers fired, specific questions per trigger (e.g. "should row R be stripped from core A as a header?", "are cores A and B siblings or one block?", "is row R a note or a section label?").
4. Instruction: return a single JSON object with the final structure and per-decision rationale.

One answer for all ambiguities. No multi-turn. No per-issue LLM calls. No agentic loop. Invalid response → regex extraction → deterministic fallback. Same fallback rules as v5.

### Files updated

- `xl_sheetagent_v6/table_agent.py` — wire gate check + consolidated call into step 6 (after ConsumedMap, before final tags).
- `xl_sheetagent_v6/core_region_validator.py` — emit ambiguous flag for gate.

### P2 Deliverable

At most one LLM call per bbox, gated by six explicit triggers. Deterministic fallback always available.

---

## Phase P3 — Parent/Child Resolution + Reconciliation LLM (Same Scope as v5)

This phase is **deliberately identical to v5's resolution behavior**.

### Parent/child resolution (`table_resolver.py`, copied from v5)

After all bboxes in a sheet are processed:

1. **Hierarchy reconstruction** — re-link parent/child from V3a on V6TableResult objects.
2. **Resolution** — merge parent + children into V6ResolvedTable objects:
   - Children classified as **preserved** (kept nested) or **absorbed** (metadata merged into parent) using v5's existing rules.
   - Parent becomes a container: shared headers, `child_titles` list, `row_names = None`.
   - Standalone tables pass through as leaf V6ResolvedTables.
   - Multi-core consistency: if multiple cores exist but no V3a children, stub children inferred from the cores.

### Reconciliation LLM (same as v5)

A separate optional LLM call **at the resolution stage**, not the per-bbox stage, used to:

- Reconcile conflicting header inheritance between parent and children.
- Arbitrate child preservation vs absorption when deterministic rules are borderline.
- Split multi-core parents into child subtables when V3a did not.

Same triggers and same fallback behavior as v5's resolution LLM. Gated by `use_llm_metadata_resolution` config (default false). Not consolidated with the per-bbox LLM — that's a different scope.

### Files updated

- `xl_sheetagent_v6/table_resolver.py` — verbatim port from v5, import updates only.
- `xl_sheetagent_v6/exporter.py` — hierarchical export with children preserved.
- `apps/orchestrator/v6_viewer.py` — Streamlit viewer.
- `apps/orchestrator/app.py` — add v6 button, viewer loading.

### P3 Deliverable

Full v6 pipeline with per-bbox algorithm + parent/child resolution + optional reconciliation LLM + orchestrator UI. Behavioral parity with v5 at the hierarchy level; improved metadata extraction at the per-bbox level.

---

## XlSheetAgentV6Config

### Fields carried over from v5 (unchanged semantics)

```yaml
# Row tagging thresholds
text_ratio_header: 0.70
number_ratio_data: 0.50
note_fill_max: 0.20
title_max_nonempty: 3

# Multi-row header detection
header_zone_pct: 0.20
header_col_tolerance: 1

# Header inheritance
inherit_header_col_tolerance: 1

# Core region rules (v5 semantics UNCHANGED)
min_component_area: 4
component_merge_gap: 1
blank_inclusion_density_threshold: 0.30

# Child rules / hierarchy / resolution (KEPT from v5)
min_child_rows: 6
iou_merge_threshold: 0.30
enable_resolution: true
absorb_metadata_only_children: true
enforce_multi_core_children: true

# Confidence weights
w_tags: 0.25
w_header: 0.25
w_core: 0.20
w_llm: 0.15
w_role: 0.15

# Formatting, parallelism, audit, export
extract_formatting: true
max_workers: 4
emit_audit: true
export_mode: "hierarchical"
row_name_text_ratio_threshold: 0.70

# LLM transport
llm_model: "gpt-4o-mini"
llm_max_tokens: 2048
llm_temperature: 0.1
max_retries: 5
base_delay: 1.0

# LLM: reconciliation (resolution stage, same as v5)
use_llm_metadata_resolution: false
llm_metadata_max_tokens: 1500
llm_metadata_max_cells: 1500

# Performance guardrails
max_cells_per_prompt: 5000
max_tables_per_sheet_warn: 50
max_llm_calls_per_sheet: 30
max_llm_children_per_sheet: 10
```

### Fields DROPPED from v5

```
# Scattered per-rule LLM flags (replaced by consolidated per-bbox gate)
enable_llm_rules
rule_header_number_parent_standalone
rule_header_composite_all
rule_header_title_collision_parent_childnum_standalone
llm_rule_max_context_rows

# Column-cut toggle (now native to leftward_walk, always-on)
use_column_cuts_for_row_names

# Obsoleted by walk mechanics
resolve_row_names_with_parent

# Subsumed by consolidated prompt
include_cut_scores_in_prompt
```

### NEW fields for v6

```yaml
# Per-bbox LLM gate (Phase 2)
enable_llm: false                         # master switch, deterministic by default
llm_gate_core_validation: true
llm_gate_multi_core: true
llm_gate_missing_header: true
llm_gate_ambiguous_rows: true
llm_gate_coverage_gap: true
llm_gate_overlap: true
llm_payload_max_cells: 8000
llm_context_max_rows: 50

# Core region validation (Phase 1)
validate_core_top_rows: true
core_validator_check_bottom_rows: true
core_validator_max_strip_rows: 3
core_validator_detect_years: true
core_validator_detect_months: true
core_validator_detect_cycles: true
core_validator_detect_quarters: true
core_validator_formula_divergence_threshold: 0.6
core_validator_ambiguity_threshold: 0.5

# Walks (Phase 1)
upward_walk_max_rows: 10
upward_walk_blank_row_stop: true
leftward_walk_max_cols: 5
note_scan_right_max_cols: 3
note_scan_below_max_rows: 10
```

---

## Schema Design

### CoreRegion (v6 — same semantics as v5)

```python
@dataclass
class CoreRegion:
    bbox: Dict[str, int]
    n_cells: int
    cell_type_distribution: Dict[str, int]   # {"NUMBER": N, "FORMULA": M}
    density: float
    role_hint: str                           # "input" | "calc" | "output" | "mixed"
    validation_result: Optional[Dict]        # {"top_strip_rows": int, "bottom_strip_rows": int, "reasons": [...], "ambiguous": bool}
```

### V6TableResult

```python
@dataclass
class V6TableResult:
    table_id: str
    bbox: Dict[str, int]
    hierarchy_role: str                      # parent | child_numeric | child_metadata | standalone
    parent_table_id: Optional[str]
    split_depth: int
    title: Optional[str]
    headers: List[List[str]]
    row_names: Optional[List[str]]
    notes: List[NoteEntry]
    core_regions: List[CoreRegion]
    table_role: str
    row_tags: List[RowTag]
    consumed: ConsumedMap
    formatting: Optional[FormattingInfo]
    children: List['V6TableResult']
    confidence: float
    signals: Dict[str, Any]                  # walked_up_from_core, walked_left_from_core, core_validation_result, etc.
    llm_result: Optional[Dict[str, Any]]
    llm_gate_triggers: List[str]
```

**Retains from v5:** `hierarchy_role`, `parent_table_id`, `split_depth`, `children`.

### V6ResolvedTable

```python
@dataclass
class V6ResolvedTable:
    table_id: str
    bbox: Dict[str, int]
    title: Optional[str]
    headers: List[List[str]]
    row_names: Optional[List[str]]
    notes: List[NoteEntry]
    core_regions: List[CoreRegion]
    table_role: str
    row_tags: List[RowTag]
    formatting: Optional[FormattingInfo]
    child_titles: List[Optional[str]]
    children: List['V6ResolvedTable']
    child_summaries: List[Dict[str, Any]]
    confidence: float
    signals: Dict[str, Any]
    metadata_source: str                     # "heuristic" | "llm_per_bbox" | "llm_reconciliation"
```

**Retains v5's hierarchical structure.** `children`, `child_titles`, `child_summaries` all preserved.

### V6RunResult

```python
@dataclass
class V6RunResult:
    run_id: str
    sheet_name: str
    tables: List[V6TableResult]
    resolved_tables: List[V6ResolvedTable]
    token_count: int
    errors: List[str]
```

---

## What Stays the Same (DO NOT Redesign)

- V3a hierarchy upstream — unchanged.
- V3a hierarchy_role → role-aware collection constraints — unchanged.
- Parent-before-child processing order — unchanged.
- Parent/child resolution (`table_resolver.py`) — unchanged.
- Preservation/absorption logic — unchanged.
- All header detection rules (multi-row, merged-cell expansion, text-ratio, column-alignment, composite row-0) — unchanged.
- All title detection rules — unchanged.
- All header inheritance rules (column-overlap tolerance, distance penalty, parent donor) — unchanged.
- All row name detection rules — unchanged.
- All note detection rules (keyword-led, sparse) — unchanged.
- Core region detection (NUMBER + FORMULA, density expansion) — unchanged.
- ConsumedMap bookkeeping, confidence weighting, audit JSONL — unchanged.
- Reconciliation LLM at resolution stage — unchanged from v5.
- Deterministic-first principle (`enable_llm: false` default) — unchanged.

**What changes:** only the order of operations within a single bbox, the addition of core region validation, and the consolidation of any per-bbox LLM assistance into one call.

---

## Out of Scope

- DAG node extraction (deferred to v7 — v6's validated core regions feed v7).
- Cross-table semantic linking.

---

## Test Plan

### tests/test_v6_core_validator.py (P1)

- Years sequence: row 1 is `=A1+1, =B1+1, ...` → stripped
- Cycles: "Cycle 1", "Cycle 2", ... → stripped
- Months: formula-generated month names → stripped
- Homogeneous formulas → no strip
- Ambiguous (partial sequence) → flagged for LLM
- Bottom row with note keyword in adjacent column → stripped
- Bottom row sparse numeric vs dense rows above → stripped
- Bottom row isolated by blank/border → stripped

### tests/test_v6_walks.py (P1)

- Upward walk: header above validated core
- Multi-row header with merged cells
- Title above header
- Stop at bbox top (do not exit bbox)
- Leftward walk: text column → row names
- Stop at bbox left

### tests/test_v6_notes.py (P1)

- Right-of-core annotations
- Below-last-core footnotes
- Both present

### tests/test_v6_hierarchy.py (P1)

- V3a parent → children preserved in V6TableResult
- Role-aware collection respected (child_numeric gets no header; child_metadata gets no row_names)
- Parent/child merging into V6ResolvedTable works identically to v5

### tests/test_v6_llm_gate.py (P2)

- Each of 6 triggers individually
- Payload-size skip
- Consolidated single-call prompt build + parse

### tests/test_v6_pipeline.py (P3)

- End-to-end on simple table
- End-to-end on parent + multi-child BIM-style table
- Deterministic-only mode
- Reconciliation LLM at resolution stage

### tests/test_smoke.py additions

- v6 import
- v6 config validation
- Legacy substring check

---

## Verification

1. **P0**: `py -3 -m pytest tests/test_smoke.py tests/test_v6_core.py`
2. **P1**: `py -3 -m pytest tests/test_v6_core_validator.py tests/test_v6_walks.py tests/test_v6_notes.py tests/test_v6_hierarchy.py`
3. **P2**: `py -3 -m pytest tests/test_v6_llm_gate.py`
4. **P3**: `py -3 -m pytest tests/` — full suite
5. **Integration**: `py -3 scripts/run_sheetagent.py --v6 --run-id test` on real HEOR workbook
6. **Orchestrator**: v6 button renders hierarchical view with preserved parent/child
