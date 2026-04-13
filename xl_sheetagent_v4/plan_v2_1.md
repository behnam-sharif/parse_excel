# SheetAgent v4 - Implementation Plan v2.1

**Date:** 2026-04-13
**Based on:** `sheetagentv4_improv_plan.md` (v2.1, 2026-04-10)
**Status:** All core fixes coded. Validation and integration testing outstanding.

---

## 1. Current State: What Is Already Implemented

All 7 fixes from the improvement plan have been coded into the codebase. Here is the mapping:

| Fix | Status | Files Modified | Key Code |
|-----|--------|---------------|----------|
| **Fix 1:** Adaptive table sampling | DONE | `prompts/table_analysis.py` | `_build_sampled_indices()` (line 247) — small tables get all rows, large tables get head-20 + every-5th-middle + tail-10 |
| **Fix 2:** Child detection heuristics | DONE | `tools/child_detector.py` | Section headers now match up to 50% of columns (line 169), min child size = 6 rows (line 291) |
| **Fix 3:** Pattern recognition prompts | DONE | `prompts/table_analysis.py` | Prompt includes pattern types (scenario_comparison, cost_breakdown, entity_template) + 3-step recognition (lines 52-101) |
| **Fix 4:** Two-level hierarchy | DONE | `agents/table_intelligence_agent.py` | `_nest_children_under_parents()` (line 664) — parent sections contain nested children by row-range containment |
| **Fix 5:** Header inheritance | DONE | `agents/table_intelligence_agent.py` | `_has_header_row()` (line 439) + `_find_header_inheritance_candidate()` (line 465) — finds table above with matching columns |
| **Fix 6:** Validation with grammar cuts | DONE | `agents/table_intelligence_agent.py` | `_load_validation_inputs()` (line 525) loads `cut_scores.jsonl` + `rejected_splits.jsonl`; `_validate_children_with_cuts()` (line 601) boosts confidence when LLM child aligns with grammar cut |
| **Fix 7:** v3a header heuristic | DONE | `tools/metadata_detector.py` | `detect_header_candidates_v3a()` (line 415) — non-numerical rows in top 20%, boosts year-like values |

### Schema Updates (DONE)

- `schemas/enriched_table.py`: `TableMetadata` has `title_source`, `header_source`, `header_inherited_from`, `row_names_source`
- `PatternObservation` dataclass with `pattern_type`, `confidence`, `evidence`, `generalizable`
- `_build_metadata()` enforces: row names NEVER inherited (line 432)

### Runner Integration (DONE)

- `runner.py`: passes `all_tables_in_sheet` and `run_dir` to `analyze_table()` for header inheritance and validation input loading
- Tables are processed in order per sheet; previously analyzed tables are available for inheritance lookups

---

## 2. What Remains To Be Done

### 2.1 Integration Testing (NOT DONE) - P0

The fixes are coded but have NOT been validated end-to-end on real workbooks.

**Required tests:**

| Test | Input | Expected Outcome | Validates |
|------|-------|-------------------|-----------|
| **T1: Calculations sheet** | 226 rows, 2 scenarios, 14 subtables | 14 children detected, 2-level hierarchy (scenarios -> cost categories) | Fix 1 (sampling), Fix 3 (patterns), Fix 4 (hierarchy) |
| **T2: Results sheet (T3 problem)** | Table T3 with no header row, T1 above with same columns | T3 inherits header from T1, `header_source="inherited"` | Fix 5 (inheritance) |
| **T3: Small table (<=50 rows)** | 30 rows, simple structure | All rows shown to LLM, correct metadata | Fix 1 (no over-sampling small tables) |
| **T4: Grammar cut alignment** | Sheet with `cut_scores.jsonl` entries | Children at cut boundaries get confidence boost | Fix 6 (validation) |
| **T5: No hallucination** | Table with no title, no row names | `title_source="missing"`, `row_names_source="missing"`, `row_names=[]` | Schema enforcement |
| **T6: Bold section headers** | Headers spanning >2 columns | Section headers detected with `non_blank <= n_cols * 0.5` | Fix 2 (heuristics) |

### 2.2 Edge Cases to Verify (NOT DONE) - P1

1. **Empty `cut_scores.jsonl`** — does `_load_validation_inputs` return empty lists gracefully?
2. **Missing `parse_output/` or `table_output/`** — does validation degrade gracefully when upstream data absent?
3. **Single table per sheet** — does inheritance skip correctly (no candidates above)?
4. **Very large table (500+ rows)** — does adaptive sampling stay within token limits?
5. **Recursive depth limit** — does `max_depth=3` prevent infinite recursion on deeply nested BIM models?
6. **Non-BIM workbooks** — do pattern prompts work on non-BIM tables (should not hallucinate BIM patterns)?

### 2.3 Phase 2 Enhancements (OPTIONAL - Future)

Per the improvement plan, these are explicitly out of scope for now:

| Enhancement | Effort | Status |
|-------------|--------|--------|
| Pattern observations schema (already coded in `PatternObservation`) | Done | DONE |
| Post-QA pattern aggregator (`aggregate_patterns_post_qa`) | 2h | NOT DONE |
| Pattern library caching | 8h | NOT DONE |
| Cross-sheet pattern matching | 6h | NOT DONE — explicitly rejected for error propagation risk |

---

## 3. Implementation Steps (Remaining Work)

### Step 1: Smoke Test Existing Code

```bash
py -3 -m pytest tests/test_smoke.py -v
```

Verify all imports work and YAML loads correctly with the new code.

### Step 2: Unit Test the New Functions

Create `xl_sheetagent_v4/test_v2_1.py` with focused tests:

1. **`test_build_sampled_indices`** — verify sampling for tables of size 10, 50, 100, 226, 500
2. **`test_has_header_row`** — tables with/without text-heavy first rows
3. **`test_find_header_inheritance_candidate`** — mock two tables, verify closest-above selection
4. **`test_validate_children_with_cuts`** — children aligned/not-aligned with grammar cuts
5. **`test_nest_children_under_parents`** — flat list with parent/leaf sections -> nested hierarchy
6. **`test_detect_section_headers_wide`** — section headers spanning >2 cols
7. **`test_row_names_never_inherited`** — `_build_metadata` rejects `row_names_source="inherited"`

### Step 3: End-to-End Test on Real Workbook

Run full pipeline on the BIM Calculations workbook:

```bash
py -3 scripts/run_pipeline.py --input <path_to_bim_workbook.xlsx> --run-id test_v2_1
py -3 -c "
from pathlib import Path
from xl_sheetagent_v4.runner import run_v4_analysis
result = run_v4_analysis('test_v2_1', Path('data/output'), llm_client=<client>, model='gpt-4o')
print(f'Tables: {result.n_tables_analyzed}, Children: {result.n_children_detected}')
"
```

Validate against Claude's manual baseline:
- Calculations sheet: expect 14 children (was 2 before fixes)
- Results sheet T3: expect `header_source="inherited"`
- Pattern observations: expect `scenario_comparison` in output

### Step 4: QA Review Output JSON

Inspect `data/output/test_v2_1/v4_sheetagent_output/v4_output/v4_results.json`:

- Every table has `metadata.title_source` set
- Every table has `metadata.header_source` set
- No table has `row_names_source="inherited"`
- Children have `validation.source` field (`grammar_cut` or `llm_only`)
- Parent sections have `nested_children` with correct containment

### Step 5: Optional - Post-QA Pattern Aggregator

If QA passes, implement lightweight aggregator:

```python
# patterns/pattern_aggregator.py
def aggregate_patterns(v4_result: V4RunResult) -> dict:
    """Collect pattern_observations from all tables post-QA."""
    ...
```

---

## 4. Workflow AFTER Implementation

This is the exact end-to-end workflow once all testing is complete and the code is validated:

### 4.1 Full Pipeline Workflow

```
                    INPUT
                      |
                      v
    +----------------------------------+
    |  xl_init                         |
    |  - Clean VBA macros              |
    |  - Classify sheets (LLM triage)  |
    |  - Approve data sheets           |
    +----------------------------------+
                      |
              init_output/
              cleaned_workbook.xlsx
              approved_sheets.json
                      |
                      v
    +----------------------------------+
    |  xl_parse                        |
    |  - Connected components          |
    |  - Table framing heuristics      |
    |  - Grammar heuristics            |
    |  - Post-split QC gate            |
    +----------------------------------+
                      |
              parse_output/
              candidate_rows.jsonl
              cut_scores.jsonl       <--- CONSUMED by sheetagent v4
                      |
                      v
    +----------------------------------+
    |  xl_table                        |
    |  - Structural split/merge heap   |
    |  - Metrics + QC gate             |
    |  - Table finalization            |
    +----------------------------------+
                      |
              table_output/
              tables_per_sheet.json  <--- PRIMARY INPUT to sheetagent v4
              rejected_splits.jsonl  <--- CONSUMED by sheetagent v4
                      |
                      v
    +==========================================+
    |  xl_sheetagent_v4  (NEW - this plan)     |
    |                                          |
    |  Per sheet, per table:                   |
    |                                          |
    |  1. Read table data (full grid + types   |
    |     + formatting from cleaned workbook)  |
    |                                          |
    |  2. Heuristic pre-analysis:              |
    |     - Title/header/row-name candidates   |
    |     - v3a header candidates              |
    |     - Structural summary (ALL rows:      |
    |       blank rows, bold rows, type        |
    |       transitions, section headers)      |
    |                                          |
    |  3. Header inheritance check:            |
    |     - If no header row detected in       |
    |       first 3 rows (>=70% text)          |
    |     - Find table above with same         |
    |       column alignment (+/- 1 col)       |
    |     - Inherit header, mark source        |
    |                                          |
    |  4. Load validation inputs:              |
    |     - Grammar cuts from xl_parse         |
    |     - Rejected splits from xl_table      |
    |                                          |
    |  5. LLM analysis (single call):          |
    |     - Adaptive sampled grid              |
    |       (small: all rows,                  |
    |        large: head + every-5th + tail)   |
    |     - Structural summary (covers all     |
    |       rows even those not sampled)       |
    |     - Pattern recognition prompts        |
    |     - Validation hints in context        |
    |     - Header inheritance context         |
    |     -> Returns: title, headers,          |
    |        row_names, summary, children,     |
    |        pattern_observations              |
    |                                          |
    |  6. Child detection merge:               |
    |     - LLM children (primary)             |
    |     - Heuristic children (supplemental,  |
    |       added only if IoU < 0.5)           |
    |     - Grammar cut validation (boost      |
    |       confidence by 1.15x if aligned)    |
    |                                          |
    |  7. Two-level nesting:                   |
    |     - Parent sections (is_parent=true)   |
    |       become containers                  |
    |     - Leaf children nested by row-range  |
    |       containment                        |
    |                                          |
    |  8. Recursive analysis:                  |
    |     - Each child bbox analyzed           |
    |       recursively (depth <= 3)           |
    |     - Produces EnrichedTable tree        |
    |                                          |
    |  9. Metadata enforcement:                |
    |     - row_names_source NEVER "inherited" |
    |     - title_source: detected/missing/    |
    |       inferred_from_parent               |
    |     - header_source: detected/inherited/ |
    |       missing                            |
    |                                          |
    +==========================================+
                      |
              v4_sheetagent_output/v4_output/
              v4_results.json
                      |
                      v
    +----------------------------------+
    |  QA / User Review                |
    |  - Review each table's metadata  |
    |  - Verify children accuracy      |
    |  - Check header inheritance      |
    |  - Validate pattern observations |
    +----------------------------------+
                      |
                      v
    +----------------------------------+
    |  (Optional) Post-QA Aggregation  |
    |  - Aggregate patterns across     |
    |    all sheets                    |
    |  - Feed to DAG / Ontology        |
    +----------------------------------+
```

### 4.2 Per-Table Analysis Flow (Detail)

For each table in `tables_per_sheet.json`:

```
table_id + bbox + sheet_name
          |
          v
[1] read_table_data(bbox, ws)
    -> grid[][], types[][], formatting{}, bbox_coords{}
          |
          v
[2] detect_metadata_zones(grid, types, formatting)
    -> title_candidates, header_candidates, row_name_candidates
          |
    detect_header_candidates_v3a(grid, types)
    -> v3a header candidates (non-numerical rows in top 20%)
          |
    compute_structural_summary(grid, types, formatting)
    -> blank rows, bold rows, type transitions, section headers (ALL rows)
          |
          v
[3] _has_header_row(grid, types)
    -> (False, None)?
          |
          YES: _find_header_inheritance_candidate(table_id, bbox, all_tables_in_sheet)
               -> {source_table_id, inherited_header, row_distance, confidence}
          |
          v
[4] _load_validation_inputs(sheet_name, bbox, table_id, run_dir)
    -> {grammar_cuts: [...], rejected_splits: [...]}
          |
          v
[5] build_table_analysis_prompt(table_data, sheet_name, bbox,
                                 validation_inputs, header_inheritance)
    -> Full LLM prompt with:
       - Sampled grid preview (adaptive)
       - Structural summary (all rows)
       - Pattern recognition instructions
       - Validation hints
       - Header inheritance context
          |
          v
[6] _call_llm(prompt) -> JSON response
          |
          v
[7] _parse_llm_response(response) -> parsed dict
          |
          v
[8] _build_metadata(parsed) -> TableMetadata
    - Enforces row_names never inherited
          |
          v
[9] detect_children(grid, types, bbox) -> heuristic_children
    _merge_children(llm_children, heuristic_children) -> merged
    _validate_children_with_cuts(merged, grammar_cuts, bbox_coords) -> validated
    _nest_children_under_parents(validated) -> hierarchical children
          |
          v
[10] For each child (if depth < max_depth):
     analyze_table(child_id, sheet, child_bbox, ws, depth+1)
          |
          v
[11] EnrichedTable(
       table_id, sheet_name, bbox, metadata,
       summary, children, pattern_observations
     )
```

### 4.3 Output Structure

After a successful run, the output JSON (`v4_results.json`) looks like:

```json
{
  "run_id": "test_v2_1",
  "run_ts": "2026-04-13T10:00:00",
  "version": "4.0.0",
  "sheets": [
    {
      "sheet_name": "Calculations",
      "n_tables": 1,
      "tables": [
        {
          "table_id": "calc_t1",
          "bbox": "A1:AB226",
          "metadata": {
            "title": "Budget Impact Analysis",
            "title_source": "detected",
            "header": ["BIM calculation", "", "2020", "2021", "2022", "2023", "2024"],
            "header_source": "detected",
            "header_inherited_from": null,
            "row_names": ["Inotuzumab", "FLAG-Ida", "..."],
            "row_names_source": "detected"
          },
          "summary": "Budget impact comparison of treatment scenarios...",
          "children": [
            {
              "section_name": "World without InO",
              "row_range": [5, 104],
              "confidence": 0.95,
              "metadata": { "..." },
              "children": [
                {
                  "section_name": "Market Shares",
                  "row_range": [7, 20],
                  "confidence": 0.92,
                  "metadata": { "..." }
                },
                {
                  "section_name": "Drug Costs",
                  "row_range": [21, 34],
                  "confidence": 0.88
                }
              ]
            },
            {
              "section_name": "World with InO",
              "row_range": [107, 206],
              "confidence": 0.93,
              "children": ["...same 7 subsections..."]
            }
          ],
          "pattern_observations": [
            {
              "pattern_type": "scenario_comparison",
              "confidence": 0.88,
              "evidence": "Two scenarios with 7 parallel subsections, 13-drug template",
              "generalizable": true
            }
          ]
        }
      ]
    }
  ],
  "statistics": {
    "n_tables_analyzed": 5,
    "n_children_detected": 16,
    "n_llm_calls": 8,
    "total_llm_tokens": 12000,
    "elapsed_seconds": 25.3
  }
}
```

### 4.4 How to Run

```bash
# 1. Run upstream pipeline first (xl_init -> xl_parse -> xl_table)
py -3 scripts/run_pipeline.py --input workbook.xlsx --run-id myrun

# 2. Run sheetagent v4
py -3 -c "
from pathlib import Path
from xl_sheetagent_v4.runner import run_v4_analysis
import openai

client = openai.OpenAI()  # reads OPENAI_API_KEY from env
result = run_v4_analysis(
    run_id='myrun',
    output_base=Path('data/output'),
    llm_client=client,
    model='gpt-4o',
    max_recursion_depth=3,
)
print(f'Done: {result.n_tables_analyzed} tables, {result.n_children_detected} children')
print(f'Output: {result.json_export_path}')
"

# 3. Or via Streamlit orchestrator (if v4 tab is wired up)
py -3 -m streamlit run apps/orchestrator/app.py
```

### 4.5 Key Behavioral Differences (Before vs After)

| Aspect | Before | After |
|--------|--------|-------|
| **Rows seen by LLM** | First 20 only | Adaptive: all (<=50) or sampled head+middle+tail (>50) |
| **Structural context** | None | Full structural summary: blank rows, bold rows, section headers for ALL rows |
| **Section header detection** | Only 1-2 non-blank cells | Up to 50% of columns |
| **Min child size** | 3 rows | 6 rows |
| **Children hierarchy** | Flat list | Two-level: parent sections contain nested children |
| **Header inheritance** | Not supported | Tables without headers inherit from table above with matching columns |
| **Validation** | None | Grammar cuts + rejected splits from upstream stages |
| **Pattern recognition** | None | LLM prompted to detect scenario_comparison, entity_template, etc. |
| **Metadata provenance** | No source tracking | `title_source`, `header_source`, `row_names_source` on every table |
| **Row names inheritance** | Not enforced | Enforced: NEVER inherited, marked "missing" if absent |

---

## 5. Risk Checklist

| Risk | Mitigation | Status |
|------|-----------|--------|
| LLM hallucinates children | Validate row ranges, min 6 rows, confidence >= 0.7 | Coded |
| Pattern over-generalization | Independent per-sheet analysis, no cross-sheet sharing | Coded |
| Large table token overflow | Adaptive sampling, structural summary as compact proxy | Coded |
| Header inheritance from wrong table | Column alignment check (+/- 1 col), closest-above selection | Coded |
| Row names incorrectly inherited | Hard enforcement in `_build_metadata()` | Coded |
| Missing upstream files | `_load_validation_inputs` returns empty lists gracefully | Coded, needs test |
| Grammar cut row offset mismatch | Uses absolute rows with +/- 2 row tolerance | Coded, needs test |

---

## 6. Success Criteria

**Pass if:**
- Calculations sheet: >= 12 of 14 children detected (was 2)
- Results sheet T3: header_source = "inherited"
- No table has row_names_source = "inherited"
- >= 80% of children at grammar cut boundaries get `validation.source = "grammar_cut"`
- Pattern observations present for scenario-structured sheets
- Total LLM calls per table <= 2 (excluding recursive children)

**Fail if:**
- Any table has hallucinated children (row_range outside actual data)
- Header inheritance produces wrong columns
- Pattern recognition hallucinates BIM patterns on non-BIM data
- Runtime > 30 seconds per sheet

---

## 7. File Inventory

All changes are in `xl_sheetagent_v4/`:

```
agents/
  table_intelligence_agent.py  -- Core agent with all 7 fixes integrated
prompts/
  table_analysis.py            -- Adaptive sampling, pattern prompts, validation context
tools/
  child_detector.py            -- Improved heuristics (50% col threshold, 6-row min)
  metadata_detector.py         -- v3a header heuristic added
  table_reader.py              -- compute_structural_summary (ALL rows)
schemas/
  enriched_table.py            -- PatternObservation, source tracking fields
runner.py                      -- Passes all_tables_in_sheet + run_dir to agent
```

No new files were created. All changes are modifications to existing files.
