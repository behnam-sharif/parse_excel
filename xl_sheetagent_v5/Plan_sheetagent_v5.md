# xl_sheetagent_v5 Implementation Plan

## Context

**Retain** `xl_sheetagent/v3a/` unchanged as the structural hierarchy and splitting engine. **Deprecate** `xl_sheetagent_v4/` as a standalone production pipeline, but reuse its metadata heuristics, LLM prompt patterns, and formatting-analysis ideas. **Create** a new `xl_sheetagent_v5/` package that wraps V3a hierarchy generation, adds deterministic row-tagging and core-region detection, and uses selective LLM refinement only for ambiguous metadata extraction. V5 outputs enriched table structures for downstream DAG/KG layers, but does not generate summaries, ontology tags, or QA responses.

**Vision**: V5 = V3a hierarchical structure + V4 metadata heuristics/prompting + strict deterministic-first rules

### What V5 does
Per table: title, multi-row headers, row names, notes, core formula region(s), row tags, child hierarchy, coarse table role.

### What V5 does NOT do
- Natural language summaries
- Ontology/BIM tagging
- Question answering
- Knowledge graph construction
- Tree map visualization as core logic

### What V5 inherits

**From V3a — keep directly (not just split geometry):**
- Hierarchical splitting (recursive, multi-split)
- Parent → child structure
- Split-depth control
- Hierarchy roles: parent, child_numeric, child_metadata
- Role-based candidate collection:
  - parent = TITLE + HEADER
  - numeric child = TITLE + ROW_NAME + FOOTNOTE
  - metadata child = TITLE + HEADER

**From V4 — keep selectively:**
- Metadata detection heuristics
- Prompt patterns (domain-agnostic, no BIM)
- Adaptive truncation/sampling for LLM payloads
- Formatting analyzer ideas
- Enriched schema ideas (simplified)
- Modular breakdown: agents, tools, schemas, export, prompts

**Drop from V4:**
- Generated summaries
- Ontology tagging
- BIM semantic concepts (cost_analysis, etc.)
- QA layer
- Tree map as core requirement
- "LLM decides too much" pattern

### Non-Goal

V5 does not solve cross-table semantic linking across a sheet or workbook. It enriches each V3a hierarchy node and preserves hierarchy/context for later KG/DAG stages. Cross-table relationships (e.g., "Table A feeds Table B") are out of scope — that is a downstream DAG concern.

---

## Fallback Rules

Production resilience — every failure mode has a deterministic fallback.

| Failure | Fallback |
|---------|----------|
| LLM times out or returns error | Use deterministic output only (row tags, headers, core regions all computed before LLM). Log warning. |
| LLM returns invalid JSON | Attempt regex extraction (from V4 pattern). If that fails, use deterministic output. Log error. |
| Header detection conflicts with inheritance | Keep deterministic (local) header. Log warning with both candidates. |
| No core region found | Return empty `core_regions: []`. Never hallucinate a region. |
| Formatting extraction fails | Set `formatting: null`. Log warning. |
| Child enrichment fails | Keep structural child from V3a with minimal fields (bbox, hierarchy_role, split_depth). Set `confidence: 0.0`. Log error. |
| Row tagger produces no candidates | Default all rows to `data_candidate`. Log warning. |

**Principle**: A V5 run must never crash due to a single table failure. Each table is isolated — failures produce degraded output, not pipeline abort.

---

## Logging and Audit Artifacts

All written to `sheetagent_v5_output/audit/` for debugging.

| File | Content |
|------|---------|
| `row_tags.jsonl` | Per-table: table_id, sheet, all Layer 1 + Layer 2 tags |
| `llm_calls.jsonl` | Per-call: table_id, prompt length, response length, tokens used, latency_ms, success/failure, whether LLM overrode any tags |
| `core_regions.jsonl` | Per-table: table_id, detected regions with bbox/density/role_hint |
| `table_decisions.jsonl` | Per-table: table_id, hierarchy_role, LLM called (y/n), LLM overrides applied, final header rows, num core regions, final table_role, confidence |
| `errors.jsonl` | All warnings and errors with table_id, stage, message, traceback |

**Per-table log fields** (always captured in `table_decisions.jsonl`):
- hierarchy_role
- whether LLM was called
- whether LLM overrode any tags (and which)
- final header rows used
- number of core regions
- final table_role
- confidence score

---

## Confidence Aggregation Rule

`V5TableResult.confidence` = weighted blend of component confidences:

```
confidence = (
    w_tags   * avg_row_tag_confidence     +   # 0.25
    w_header * header_detection_confidence +   # 0.25
    w_core   * core_region_confidence      +   # 0.20
    w_llm    * llm_agreement_score         +   # 0.15
    w_role   * role_consistency_score           # 0.15
)
```

| Component | How computed |
|-----------|-------------|
| `avg_row_tag_confidence` | Mean confidence across all row tags |
| `header_detection_confidence` | 1.0 if clear single/multi-row header; 0.7 if inherited; 0.3 if ambiguous |
| `core_region_confidence` | 1.0 if ≥1 region with density ≥0.5; 0.5 if sparse; 0.0 if no region |
| `llm_agreement_score` | 1.0 if LLM agrees with heuristics; 0.5 if LLM overrides; 0.0 if LLM skipped or failed |
| `role_consistency_score` | 1.0 if table_role matches core region role_hints; 0.5 if partial match; 0.0 if conflict |

Weights are configurable in `XlSheetAgentV5Config`. If LLM is disabled, its weight is redistributed proportionally to other components.

---

## Table Role Determination Rules

Deterministic-first assignment, LLM refines only ambiguous cases.

```
if hierarchy_role == "child_metadata" and len(core_regions) == 0:
    table_role = "metadata"

elif all core regions have role_hint == "input":
    table_role = "input"

elif any core region has role_hint == "calc" and position is lower half:
    if has summary/total row pattern (last rows, bold, "total"/"sum" keywords):
        table_role = "output"
    else:
        table_role = "calc"

elif core regions have mixed role_hints:
    table_role = "mixed"

else:
    table_role = "mixed"  # default
```

**role_hint logic** (per core region):
- `"input"`: ≥80% NUMBER cells, ≤5% FORMULA, low decimal variance (round numbers)
- `"calc"`: ≥20% FORMULA cells, or high decimal variance
- `"output"`: formula-heavy + positioned in bottom third of table + summary row pattern
- `"mixed"`: doesn't clearly fit above

LLM may refine when `table_role == "mixed"` and confidence is low.

---

## Parent-Child Inheritance Rules

Explicit rules for what flows between hierarchy levels.

| Property | Inherited? | Rule |
|----------|-----------|------|
| Headers | Yes, downward | Children may inherit headers from parent if they lack their own and column count matches (±1 tolerance) |
| Title | Partial | Children may inherit title as prefix only if parent explicitly provides context (e.g., "Drug A" parent → "Drug A - Revenue" child). Not automatic. |
| Row names | Never | Row names are always local to each table node |
| Notes | Never by default | Notes are scoped to the table where detected. Parent notes do not propagate to children. |
| table_role | Never | Each node determines its own role independently |
| Formatting | Never | Each node extracts its own formatting |

**Metadata child special rule**: Metadata children can provide header/title context to sibling tables (via `all_tables_in_sheet` lookups), but do not overwrite sibling-local candidates automatically. Sibling inheritance is opt-in, triggered only when a sibling has no detected header and the metadata child's header matches column structure.

---

## Consumed Rows/Cols Ledger

Internal bookkeeping object used during per-table analysis. Not exported, but powers core-region masking, formatting extraction, and Layer 2 tag assignment.

```python
@dataclass
class ConsumedMap:
    title_rows: List[int]       # row indices consumed as title
    header_rows: List[int]      # row indices consumed as header (multi-row)
    rowname_cols: List[int]     # column indices consumed as row names
    note_rows: List[int]        # row indices consumed as notes

    def all_consumed_rows(self) -> Set[int]:
        return set(self.title_rows + self.header_rows + self.note_rows)
```

**Used by:**
- `core_region_detector.py` — zeros out consumed rows/cols before connected-component scan
- `formatting_agent.py` — knows which rows to extract title/header/note formatting from
- `row_tagger.py` Layer 2 — converts candidates to consumed_* tags
- `exporter.py` — can optionally include consumed map for debugging

---

## Performance Guardrails

Caps to protect against messy Excel files.

| Guard | Default | Config key |
|-------|---------|-----------|
| Max child nodes per sheet sent to LLM | 20 | `max_llm_children_per_sheet` |
| Max cells sent per LLM prompt | 5000 | `max_cells_per_prompt` |
| Max recursion depth (from V3a) | Inherited from V3a config | `v3a_config.max_split_depth` |
| Max tables processed per sheet before warning | 50 | `max_tables_per_sheet_warn` |
| Max retries per LLM call | 5 | `max_retries` |
| Max total LLM calls per sheet | 30 | `max_llm_calls_per_sheet` |
| Thread pool size | 4 | `max_workers` |

**Behavior when limits are hit:**
- `max_llm_children_per_sheet` / `max_llm_calls_per_sheet`: remaining tables get heuristic-only enrichment, logged as warning
- `max_cells_per_prompt`: adaptive sampling kicks in harder (reduce head/tail/stride)
- `max_tables_per_sheet_warn`: log warning but continue processing

---

## Files to Create

```
xl_sheetagent_v5/
├── __init__.py              # Package init, expose run_v5_analysis
├── config.py                # XlSheetAgentV5Config (semantic layer only)
├── schemas.py               # V5 data models (EnrichedTable-lite)
├── row_tagger.py            # Layer 1 heuristic + Layer 2 resolved tags
├── core_region_detector.py  # Connected-component core regions per V3a node
├── header_detector.py       # Multi-row header detection + inheritance
├── candidate_collector.py   # Role-aware collection (V3a roles + V5 tags)
├── prompt_builder.py        # Selective LLM prompt construction
├── table_agent.py           # Per-table orchestrator
├── formatting_agent.py      # Heuristic-only formatting extraction
├── exporter.py              # JSON serialization
└── runner.py                # Entry point, wraps V3a hierarchy + V5 enrichment
```

## Files to Modify

- `config/stages.yaml` — add `xl_sheetagent_v5:` section (V5 semantic config only)
- `core/paths.py` — add `get_v5_sheetagent_output_path()`
- `core/stage_config.py` — add `sheetagent_v5_config_from_pipeline()` with `_require_exact_keys()`
- `scripts/run_sheetagent.py` — add `--v5` CLI flag

## V3a Files Imported Directly (never modified)

- `xl_sheetagent/v3a/split_agent.py` → `decide_splits()`
- `xl_sheetagent/v3a/runner.py` → `_recursive_split_and_detect()`, `_partition_bbox_by_rows()`
- `xl_sheetagent/v3a/table_agent.py` → `run_table_agent()`, `_detect_splits()`, `_collect_candidates_by_role()`
- `xl_sheetagent/v3a/cell_analysis.py` → `find_core_data_region()`, `read_cell_grid()`, `classify_grid()`
- `xl_sheetagent/v3a/schemas.py` → `CoreDataRegion`, `SplitCandidate`, `SplitDecision`, `CandidatePiece`
- `xl_sheetagent/config.py` → `XlSheetAgentConfig` (V3a config, kept separate from V5 config)

---

## Pipeline Design

### Stage 0 — Inputs

V5 consumes:
- Upstream xl_table / final table bboxes (does NOT recompute structural bboxes)
- Workbook access for cell values + formulas + merges + formatting
- Cut scores from xl_parse / V3a-compatible inputs

### Stage 1 — Row Tagging on Parent Bbox

Each row gets heuristic priors. Row tagging is a **prior layer**, not the final collector — it feeds INTO V3a hierarchy logic, not replaces it.

### Stage 2 — V3a Hierarchical Splitting

Use V3a directly for:
- Recursive splitting
- Multi-split execution
- Parent/child creation
- Hierarchy role assignment

V5 uses V3a hierarchy generation as the structural substrate. V5 does NOT invent a second hierarchy system on top.

### Stage 3 — Role-Aware Candidate Collection

Hybrid of V3a roles + V5 row tags:
- V3a role decides what class of candidates are allowed
- V5 row tags improve where to look and confidence
- LLM can refine only ambiguous cases

Base rules from V3a:
- Parent: TITLE + HEADER
- Child numeric: TITLE + ROW_NAME + FOOTNOTE
- Child metadata: TITLE + HEADER

### Stage 4 — Deterministic Extraction Before LLM

Before any LLM call, compute ALL of:
- Candidate title rows
- Candidate header rows (including multi-row)
- Candidate row-name columns/rows
- Candidate note rows
- Candidate core regions (NUMBER/FORMULA mask → exclude consumed rows → connected components → optional merge of near-touching components)

LLM can confirm or adjust, but should not invent geometry.

### Stage 5 — Selective LLM Refinement

LLM is a **refiner**, not the main engine.

**Use LLM only for:**
- Ambiguous title/header boundaries
- Multi-row header confirmation
- Note-type classification
- Role refinement
- Edge cases in core region interpretation

**Do NOT use LLM for:**
- Splitting
- Full hierarchy creation
- Primary core-region geometry
- Title invention
- Summary generation

**Child LLM policy (based on V3a hierarchy role):**
- child_numeric → yes, if ambiguous or important
- child_metadata → usually no, heuristic only unless unclear
- small trivial children → no LLM
- mixed/unclear children → yes

---

## Phase 0: Infrastructure

### 0A. Config (`xl_sheetagent_v5/config.py`)
- `XlSheetAgentV5Config` dataclass with `_require_exact_keys()` validation
- V5 owns only its semantic layer config; V3a config stays separate
- Key v5_* fields:
  - Row tagging thresholds: `text_ratio_header: 0.7`, `number_ratio_data: 0.5`, `note_fill_max: 0.2`
  - Multi-row header detection thresholds: `header_zone_pct: 0.2`, `header_col_tolerance: 1`
  - LLM refinement settings: `use_llm: bool`, `llm_model: str`, `llm_max_tokens: int`, `llm_temperature: float`, `max_retries: int`, `base_delay: float`
  - Core region rules: `min_component_area: int`, `component_merge_gap: int`
  - Child rules: `min_child_rows: 6`, `iou_merge_threshold: 0.3`
  - Formatting extraction toggles: `extract_formatting: bool`
  - Parallelism: `max_workers: int` (thread pool size for sheet-level parallelism)
  - Performance guardrails: `max_llm_children_per_sheet`, `max_cells_per_prompt`, `max_tables_per_sheet_warn`, `max_llm_calls_per_sheet`
  - Confidence weights: `w_tags`, `w_header`, `w_core`, `w_llm`, `w_role`
  - Audit: `emit_audit: bool` (write audit JSONL files)

### 0B. YAML (`config/stages.yaml`)
- Add `xl_sheetagent_v5:` section with all v5_* fields
- Existing `xl_sheetagent:` section (V3a) stays untouched
- YAML is source of truth

### 0C. Paths (`core/paths.py`)
- `get_v5_sheetagent_output_path(run_root) -> Path` → `run_root / "sheetagent_v5_output"`

### 0D. Stage config builder (`core/stage_config.py`)
- `sheetagent_v5_config_from_pipeline(pipeline_config) -> XlSheetAgentV5Config`
- Uses `_require_exact_keys()` — unknown keys raise errors

### 0E. Package init + CLI
- `xl_sheetagent_v5/__init__.py`: expose `run_v5_analysis`
- `scripts/run_sheetagent.py`: add `--v5` flag

---

## Phase 1: Schemas (`xl_sheetagent_v5/schemas.py`)

EnrichedTable-lite — structural richness of V3a + metadata richness of V4, without V4's extra semantic layers.

```python
@dataclass
class RowTag:
    row_index: int
    layer1_tag: str   # title_candidate | header_candidate | rowname_candidate | note_candidate | data_candidate
    layer2_tag: str   # consumed_title | consumed_header | consumed_rowname | consumed_note | unassigned_context | data
    confidence: float
    signals: Dict[str, Any]

@dataclass
class NoteEntry:
    row_index: int
    text: str
    note_type: str    # "footnote" | "source" | "annotation"
    source: str       # "heuristic" | "llm"

@dataclass
class CoreRegion:
    bbox: Dict[str, int]         # {"r1": 5, "c1": 2, "r2": 18, "c2": 7}
    n_cells: int
    cell_type_distribution: Dict[str, int]  # {"NUMBER": 90, "FORMULA": 30}
    density: float
    role_hint: str               # "input" | "calc" | "output" | "mixed"

@dataclass
class FormattingInfo:
    bold: Dict[str, bool]        # per-element: title, headers, row_names, notes, core
    fill: Dict[str, Any]         # background colors per element
    borders: Dict[str, str]      # border style per element
    alignment: Dict[str, str]    # alignment per element
    header_merges: List[Dict]    # merges relevant to header structure

@dataclass
class V5TableResult:
    table_id: str
    hierarchy_role: str          # "parent" | "child_numeric" | "child_metadata"
    parent_table_id: Optional[str]
    split_depth: int
    bbox: Dict[str, int]         # {"r1": 0, "c1": 0, "r2": 20, "c2": 8}
    title: Optional[str]
    headers: List[List[str]]     # [[row1_cells], [row2_cells]] for multi-row
    row_names: Optional[List[str]]
    notes: List[NoteEntry]
    core_regions: List[CoreRegion]
    table_role: str              # "input" | "calc" | "output" | "mixed" | "metadata"
    row_tags: List[RowTag]
    formatting: Optional[FormattingInfo]
    children: List["V5TableResult"]
    confidence: float
    signals: Dict[str, Any]

@dataclass
class ConsumedMap:
    title_rows: List[int]
    header_rows: List[int]
    rowname_cols: List[int]
    note_rows: List[int]

@dataclass
class V5RunResult:
    run_id: str
    sheet_name: str
    tables: List[V5TableResult]
    token_count: int
    errors: List[str]
```

---

## Phase 2: Row Tagger (`xl_sheetagent_v5/row_tagger.py`) — THE BACKBONE

Row tags are priors that feed into V3a's role-based candidate collection.

### Layer 1: `tag_rows(grid, config) -> List[RowTag]`
Heuristic scan, one pass:
- **title_candidate**: row in top 3, mostly text, ≤3 non-empty cells, often bold/merged
- **header_candidate**: ≥70% text cells, <30% number cells (from V4's `_has_header_row`)
- **rowname_candidate**: leftmost 1-2 cells are text, rest are numbers
- **note_candidate**: ≤20% filled, or contains keywords ("note", "source", "footnote")
- **data_candidate**: ≥50% number cells, default

Confidence scores from signal strength. Tags are soft — LLM can override.

### Layer 2: `assign_final_tags(row_tags, table_context) -> List[RowTag]`
After V3a hierarchy + candidate collection resolves structure:
- `consumed_title` / `consumed_header` / `consumed_rowname` / `consumed_note` — claimed by a table
- `unassigned_context` — candidates not consumed
- `data` — data rows in finalized table

### Helper: `slice_row_tags(row_tags, r_start, r_end) -> List[RowTag]`

---

## Phase 3: Core Region Detector (`xl_sheetagent_v5/core_region_detector.py`)

Core regions are scoped within each final V3a node (especially numeric children).

### `detect_core_regions(grid, row_tags, config) -> List[CoreRegion]`
1. Build binary matrix: 1 where cell is NUMBER or FORMULA, 0 otherwise
2. Zero out consumed_title, consumed_header, consumed_note, consumed_rowname rows/cols
3. Connected-component labeling (scipy.ndimage.label or simple BFS)
4. Optional merge of near-touching components (gap ≤ `component_merge_gap`)
5. Per component:
   - bbox, n_cells, cell_type_distribution (NUMBER vs FORMULA counts)
   - density
   - role_hint: `"input"` (round numbers, no formulas), `"calc"` (formulas/high decimal variance), `"output"` (formula-heavy, bottom position), `"mixed"`
6. Return sorted by top-left position

Reuses V3a's `find_core_data_region()` as starting point, then decomposes into multiple regions.

---

## Phase 4: Multi-Row Header Detector (`xl_sheetagent_v5/header_detector.py`)

### `detect_headers(grid, row_tags, merge_info, config) -> List[List[str]]`
1. Find consecutive header_candidate rows at top of data region
2. Check merged cells spanning multiple rows (openpyxl merge info)
3. Check hierarchy pattern: row N fewer filled cells than row N+1 (category → subcategory)
4. Return `List[List[str]]` — each inner list is one header row

### `find_header_inheritance(tables_in_sheet, current_table, config) -> Optional[List[List[str]]]`
From V4's `_find_header_inheritance_candidate()`:
- Closest table above with ±1 column tolerance
- Return its headers if found

---

## Phase 5: Role-Aware Candidate Collector (`xl_sheetagent_v5/candidate_collector.py`)

This is the hybrid layer bridging V3a roles and V5 tags.

### `collect_candidates(v3a_node, row_tags, config) -> Dict[str, Any]`
Based on V3a hierarchy role:
- **parent**: collect title + header candidates from row_tags
- **child_numeric**: collect title + row_name + footnote candidates
- **child_metadata**: collect title + header candidates

Uses V3a's `_collect_candidates_by_role()` as the structural rule, enhanced by V5 row tag confidence scores to pick the best candidates.

---

## Phase 6: Prompt Builder (`xl_sheetagent_v5/prompt_builder.py`)

Domain-agnostic (no BIM/construction language).

### `build_v5_prompt(table_data, row_tags, core_regions, headers, candidates, config) -> str`
Includes:
1. Structural context: bbox, dimensions, V3a hierarchy role, split depth
2. Row tag summary: counts per tag, sequence pattern
3. Pre-computed candidates: title, headers, row names, notes (all deterministic)
4. Sampled data: adaptive sampling (from V4's `_build_sampled_indices()`)
5. Core region info: count, bboxes, densities, role hints
6. Ask LLM to: confirm/override tags, validate headers, classify note types, refine table_role

### `parse_v5_response(raw_text) -> Dict`
Parse JSON with fallback regex (from V4 pattern).

---

## Phase 7: Table Agent (`xl_sheetagent_v5/table_agent.py`) — PER-TABLE ORCHESTRATOR

### `analyze_table(v3a_node, sheet_accessor, all_tables, config, llm_client) -> V5TableResult`

Flow per V3a hierarchy node:
1. **Read grid** via V3a's `read_cell_grid()`
2. **Layer 1 row tagging** → `tag_rows(grid, config)`
3. **Detect headers** → `detect_headers(grid, row_tags, merge_info, config)`
4. **Header inheritance** → `find_header_inheritance(all_tables, current, config)`
5. **Role-aware candidate collection** → `collect_candidates(v3a_node, row_tags, config)`
6. **Deterministic extraction** → title, headers, row names, notes, core regions — ALL computed before LLM
7. **Selective LLM call** (only if `config.use_llm` AND meets complexity threshold):
   - Skip LLM if: small table (<5 rows), 100% data rows, clear single-header, trivial child
   - Child LLM policy by V3a role:
     - child_numeric → yes if ambiguous
     - child_metadata → usually no
     - small trivial → no
     - mixed/unclear → yes
   - Build prompt → call LLM (via `llm_dispatch.complete_text()` with retry) → parse
   - Apply overrides to tags (soft constraints)
8. **Layer 2 tag resolution** → `assign_final_tags()`
9. **Core region detection** → `detect_core_regions()` (scoped within this node)
10. **Determine table_role** from core region role_hints + LLM refinement
11. **Build V5TableResult** with children from V3a hierarchy (each child already processed)

---

## Phase 8: Formatting Agent (`xl_sheetagent_v5/formatting_agent.py`)

Heuristic-only, no LLM. Runs after structural + semantic assignment.

### `extract_table_formatting(sheet_accessor, bbox, table_result, config) -> FormattingInfo`
Per-element formatting extraction:
- **title**: bold, font size, fill
- **headers**: bold, fill, borders, merges
- **row names**: bold, alignment
- **notes**: italic, font size
- **core regions**: borders, number formats, alternating row fills

Reuses ideas from V4's `extract_formatting()`.

---

## Phase 9: Exporter (`xl_sheetagent_v5/exporter.py`)

### `export_results(results: List[V5RunResult], output_dir: Path, config)`
- `tables.json` — full V5TableResult tree per sheet (recursive children)
- `summary.json` — sheet-level stats (table count, types, token usage)
- If `config.emit_audit`: write `audit/row_tags.jsonl`, `audit/llm_calls.jsonl`, `audit/core_regions.jsonl`, `audit/table_decisions.jsonl`, `audit/errors.jsonl`

### Output shape (per table)
```json
{
  "table_id": "...",
  "hierarchy_role": "parent | child_numeric | child_metadata",
  "parent_table_id": null,
  "split_depth": 0,
  "bbox": {"r1": 0, "c1": 0, "r2": 20, "c2": 8},
  "title": "...",
  "headers": [["Year", "", ""], ["", "Q1", "Q2"]],
  "row_names": ["Drug A", "Drug B"],
  "notes": [
    {"row_index": 22, "text": "Source: internal", "note_type": "footnote"}
  ],
  "core_regions": [
    {
      "bbox": {"r1": 5, "c1": 2, "r2": 18, "c2": 7},
      "n_cells": 120,
      "cell_type_distribution": {"NUMBER": 90, "FORMULA": 30},
      "density": 0.93,
      "role_hint": "calc"
    }
  ],
  "table_role": "input | calc | output | mixed | metadata",
  "row_tags": [...],
  "formatting": {...},
  "children": [...]
}
```

---

## Phase 10: Runner (`xl_sheetagent_v5/runner.py`)

### Sheet-Level Parallelization

Sheets are processed in parallel via `ThreadPoolExecutor` (same pattern as V3a/xl_parse).

**Thread safety rules:**
- Each thread opens its own openpyxl workbook handle — never share workbook objects across threads
- LLM client is shared (HTTP-based, thread-safe) but rate-limit retry handles contention
- Config is read-only, safe to share
- Each thread writes to its own sheet subfolder under output dir

### `run_v5_sheet(workbook_path, sheet_name, bboxes, cut_scores, config, v3a_config, llm_client, run_dir) -> V5RunResult`

Per-sheet worker (runs inside thread):
1. Open own workbook handle (data_only=True)
2. Run V3a hierarchy generation on input bboxes → get hierarchy tree with roles
3. Walk hierarchy tree sequentially: for each node, call `analyze_table()` (depth-first, children first — sequential because children may inform parent refinement)
4. Run `extract_table_formatting()` on each table
5. Aggregate into V5RunResult

### `run_v5_analysis(workbook_path, sheets, all_bboxes, all_cut_scores, config, v3a_config, llm_client, run_dir) -> List[V5RunResult]`

Top-level entry point:
1. Build per-sheet work items (sheet_name → bboxes + cut_scores)
2. Dispatch sheets in parallel via `ThreadPoolExecutor(max_workers=config.max_workers)`
3. Each thread calls `run_v5_sheet()` with its own workbook handle
4. Collect results, export via exporter
5. Return aggregated results

V3a is the splitter + hierarchy builder. V5 is the enricher. Parallelism is at sheet level; within a sheet, tables are processed sequentially through the V3a hierarchy.

---

## Implementation Order

| Step | Phase | Module | Key Dependency |
|------|-------|--------|---------------|
| 1 | 0 | config.py, stages.yaml, paths.py, stage_config.py, __init__.py, CLI | None |
| 2 | 1 | schemas.py | config.py |
| 3 | 2 | row_tagger.py | schemas.py |
| 4 | 3 | core_region_detector.py | schemas.py, row_tagger |
| 5 | 4 | header_detector.py | schemas.py, row_tagger |
| 6 | 5 | candidate_collector.py | schemas.py, row_tagger, V3a roles |
| 7 | 6 | prompt_builder.py | schemas.py, candidate_collector |
| 8 | 7 | table_agent.py | all above + V3a imports |
| 9 | 8 | formatting_agent.py | schemas.py |
| 10 | 9 | exporter.py | schemas.py |
| 11 | 10 | runner.py | table_agent, formatting_agent, exporter, V3a runner |

---

## V4 Logic Ported (rewritten, not imported)

| V4 Function | Source | V5 Target |
|-------------|--------|-----------|
| `_has_header_row()` | `v4/agents/table_intelligence_agent.py` | `row_tagger.py` |
| `_find_header_inheritance_candidate()` | `v4/agents/table_intelligence_agent.py` | `header_detector.py` |
| `_validate_children_with_cuts()` | `v4/agents/table_intelligence_agent.py` | `candidate_collector.py` |
| `_nest_children_under_parents()` | `v4/agents/table_intelligence_agent.py` | `candidate_collector.py` |
| `_build_sampled_indices()` | `v4/prompts/table_analysis.py` | `prompt_builder.py` |
| `detect_section_headers()` | `v4/tools/child_detector.py` | `candidate_collector.py` |
| `detect_header_candidates_v3a()` | `v4/tools/metadata_detector.py` | `header_detector.py` |
| `compute_structural_summary()` | `v4/tools/table_reader.py` | `table_agent.py` |
| `extract_formatting()` | `v4/tools/formatting_analyzer.py` | `formatting_agent.py` |
| `parse_llm_response()` | `v4/prompts/table_analysis.py` | `prompt_builder.py` |
| `complete_text()` | `v4/llm_dispatch.py` | **Imported directly** (already has retry) |

---

## Verification Plan

### Unit Tests (per module)
- `test_row_tagger.py`: Layer 1 tags with synthetic grids, Layer 2 consumption
- `test_core_region_detector.py`: Connected components, role_hint, multi-region
- `test_header_detector.py`: Single-row, multi-row, merged cells, inheritance
- `test_candidate_collector.py`: Role-based collection with V3a roles + V5 tags
- `test_prompt_builder.py`: Prompt construction, response parsing
- `test_table_agent.py`: Integration with mock LLM
- `test_formatting_agent.py`: Per-element formatting extraction
- `test_schemas.py`: Serialization round-trip

### Integration Test
1. Run V3a on test workbook → hierarchy tree
2. Run V5 on same workbook with V3a hierarchy → enriched tables
3. Verify: table_role, multi-row headers, row_tags, core_regions, notes, children
4. Run with `use_llm=False` → verify heuristic-only mode produces complete output

### End-to-End
1. `py -3 scripts/run_sheetagent.py --v5 --input test.xlsx --run-id test_v5`
2. Check `data/output/test_v5/sheetagent_v5_output/tables.json`
3. Verify output matches schema shape above

### Regression
- V3a tests still pass (no V3a code modified)
- V4 tests still pass (no V4 code modified)
- Smoke tests: `py -3 -m pytest tests/test_smoke.py`
