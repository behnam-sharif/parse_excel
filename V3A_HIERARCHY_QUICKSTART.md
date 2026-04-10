# V3A Hierarchical Splitting - Quick Start Guide

## What Changed?

### Before (v3a.0.1)
- Split detected → creates 2 **new sibling tables** with `_top` and `_bottom` suffixes
- Only **1 split** executed (highest score)
- Row names with formulas → **rejected** (empty candidates)
- All tables collect all candidates (TITLE + HEADER + ROW_NAME + FOOTNOTE)

### After (v3a.1.0)
- Split detected → creates **parent-child hierarchy**
- **ALL valid splits** executed (multi-split)
- Row names with formulas → **allowed** (flagged in metadata)
- **Hierarchy-aware** candidate collection:
  - Parent: TITLE + HEADER only
  - Child (numeric): TITLE + ROW_NAME + FOOTNOTE
  - Child (0% numeric): TITLE + HEADER (metadata)

## Example: Your `Table2_Inputs_B3:N48`

### Before
```
xl_table output:
  9814fc53f594ba29 (B3:N122)
  
Split at row 47:
  ├─ 9814fc53f594ba29_t45 (B3:N48)    ← new sibling table
  └─ 9814fc53f594ba29_b45 (B49:...)   ← new sibling table

sheetagent detects 5 more splits in _t45:
  - Row 3 (score=0.92)  ← ONLY THIS ONE EXECUTED
  - Row 6 (score=0.92)  ← NOT EXECUTED
  - Row 20 (score=0.92) ← NOT EXECUTED
  - Row 34 (score=0.92) ← NOT EXECUTED
  - Core sparsity        ← NOT EXECUTED
  
Result:
  ├─ 9814fc53f594ba29_t45_top (B3:N3)      ← single row
  └─ 9814fc53f594ba29_t45_bottom (B4:N48)  ← rest (4 splits missed)
```

### After (Hierarchical)
```
xl_table output:
  9814fc53f594ba29 (B3:N122) → becomes PARENT
  
First split at row 47:
  Parent: 9814fc53f594ba29 (B3:N122)
    ├─ Child: 9814fc53f594ba29_top (B3:N48)
    └─ Child: 9814fc53f594ba29_bottom (B49:...)
    
Re-detect on children → finds 5 splits in _top:
  ALL 5 EXECUTED (multi-split enabled):
  
  Child: 9814fc53f594ba29_top (B3:N48) → becomes PARENT
    ├─ Grandchild: ..._top_a (B3:N3)    ← 0% numeric → METADATA
    ├─ Grandchild: ..._top_b (B4:N6)    ← DATA CHILD
    ├─ Grandchild: ..._top_c (B7:N20)   ← DATA CHILD
    ├─ Grandchild: ..._top_d (B21:N34)  ← DATA CHILD
    └─ Grandchild: ..._top_e (B35:N48)  ← DATA CHILD
```

## Key Differences

| Feature | Before (v3a.0.1) | After (v3a.1.0) |
|---------|-----------------|-----------------|
| **Structure** | Flat siblings | Parent-child tree |
| **Splits per table** | 1 (top-1) | All valid |
| **Max depth** | 1 level | 2 levels (configurable) |
| **Row names with formulas** | Rejected | Allowed |
| **0% numeric tables** | Same as others | Metadata candidates |
| **Candidate collection** | All tables → all types | Role-based |

## Candidate Collection Rules

### Parent Table (has children)
**Collects:** TITLE + HEADER (column names)  
**Why:** Children inherit header, so parent only needs to define columns

**Example:**
```
Parent: Table1 (B3:N48)
  Candidates:
    ✅ Title: "Patient Population" (row 3)
    ✅ Header: "Annual % | Value | Notes" (row 4)
    ❌ Row Names: (not collected - children have their own)
    ❌ Footnotes: (not collected - children have their own)
```

### Child (>0% numeric data)
**Collects:** TITLE + ROW_NAME + FOOTNOTE  
**Why:** Inherits header from parent, needs own row labels

**Example:**
```
Child: Table1_child_b (B7:N20)
  Candidates:
    ✅ Title: "Market Share Without Drug" (row 6)
    ✅ Row Names: "Product A | Product B | ..." (col A)
    ✅ Footnotes: "Source: IMS Data" (row 21)
    ❌ Header: (inherited from parent)
```

### Child (0% numeric - METADATA)
**Collects:** TITLE + HEADER  
**Why:** Not a data table - it's a header/title for siblings

**Example:**
```
Child: Table1_child_a (B3:N3) [0% numeric]
  Candidates:
    ✅ Title: "Table 1: Patient Data" (single cell)
    ✅ Header: "Year | 2020 | 2021 | 2022" (if multi-row)
    → FLAGGED as metadata candidate for parent/siblings
```

## Configuration

### Enable Hierarchical Splitting
```yaml
# config/stages.yaml
xl_sheetagent:
  v3a_hierarchical_splits: true   # ← Enable feature
  v3a_multi_split_enabled: true   # ← Execute all valid splits
  v3a_row_names_allow_formulas: true  # ← Don't reject formulas
  v3a_split_max_depth: 2          # ← Max recursion depth
```

### Disable (Rollback)
```yaml
v3a_hierarchical_splits: false  # ← Back to v3a.0.1 behavior
```

## Running the Pipeline

```bash
# Standard run (uses config setting)
python3 -c "
from xl_sheetagent.v3a.runner import run_v3a
result = run_v3a('streamlit_run_simple')
print(f'Version: {result.version}')
print(f'Tables: {result.n_tables_processed}')
"
```

## Output Structure

### v3a_results.json (Hierarchical)
```json
{
  "version": "3a.1.0",
  "sheets": [
    {
      "sheet_name": "Inputs",
      "table_results": [
        {
          "table_id": "9814fc53f594ba29_t45",
          "hierarchy_role": "parent",
          "split_depth": 0,
          "parent_table_id": null,
          "has_children": true,
          "children": [
            {
              "table_id": "9814fc53f594ba29_t45_top",
              "hierarchy_role": "child_metadata",
              "split_depth": 1,
              "parent_table_id": "9814fc53f594ba29_t45",
              "has_children": false,
              "title_candidates": [...],
              "header_row_candidates": [...],
              "row_name_candidates": [],
              "footnote_candidates": []
            },
            {
              "table_id": "9814fc53f594ba29_t45_bottom",
              "hierarchy_role": "child_numeric",
              "split_depth": 1,
              "parent_table_id": "9814fc53f594ba29_t45",
              "title_candidates": [...],
              "header_row_candidates": [],
              "row_name_candidates": [...],
              "footnote_candidates": [...]
            }
          ]
        }
      ]
    }
  ]
}
```

## Viewing Results

### Check Hierarchy
```bash
# Count root tables vs children
python3 -c "
import json
with open('data/output/streamlit_run_simple/sheetagent_output/v3a_summary.json') as f:
    data = json.load(f)
    print(f'Version: {data[\"version\"]}')
    for sheet in data['sheets']:
        for table in sheet['tables']:
            depth = table.get('split_depth', 0)
            role = table.get('hierarchy_role', 'unknown')
            n_children = table.get('n_children', 0)
            print(f'  {\"  \"*depth}{table[\"table_id\"]}: {role} ({n_children} children)')
"
```

### Compare with Original
```bash
# Before: count tables
jq '.n_tables_processed' data/output/streamlit_run_simple/sheetagent_output/v3a_summary.json

# After: count root + children
jq '[.sheets[].tables[] | recurse(.children[]?)] | length' data/output/streamlit_run_simple/sheetagent_output/v3a_summary.json
```

## Troubleshooting

### Issue: No children created
**Check:** `v3a_hierarchical_splits` is `true` in config  
**Check:** Tables have `split_candidates` with score ≥ `v3a_split_threshold` (default: 0.5)

### Issue: Only 1 split executed
**Check:** `v3a_multi_split_enabled: true`  
**Check:** Multiple candidates have score ≥ threshold

### Issue: Row names still empty
**Check:** `v3a_row_names_allow_formulas: true`  
**Check:** Formulas flagged in `decision_reason` field

### Issue: Too many splits (explosion)
**Lower:** `v3a_split_threshold` (e.g., 0.6 → 0.7)  
**Lower:** `v3a_split_max_depth` (e.g., 2 → 1)  
**Check:** Logs for split counts per table

## Performance

Expected impact:
- **Flat mode:** ~1-2s per sheet (baseline)
- **Hierarchical mode:** ~2-4s per sheet (1.5-2x)
- **With multi-split:** +10-20% per split executed

Optimization tips:
- Lower `v3a_split_threshold` to reduce candidates
- Set `v3a_split_max_depth: 1` for shallow trees
- Disable `v3a_multi_split_enabled` to execute only top-1

## Next Steps

1. **Run on your workbook:**
   ```bash
   python3 scripts/run_sheetagent.py --run-id streamlit_run_simple
   ```

2. **Check output:**
   ```bash
   cat data/output/streamlit_run_simple/sheetagent_output/v3a_summary.json | python3 -m json.tool | head -100
   ```

3. **Compare results:**
   - Disable flag → run again → compare JSON outputs
   - Check if children have correct candidates (row names, titles)

4. **Report issues:**
   - Missing row name candidates → check formula flag
   - Wrong hierarchy role → check numeric_ratio
   - Too many/few splits → adjust threshold

## References

- Full implementation details: `IMPLEMENTATION_SUMMARY.md`
- Config reference: `config/stages.yaml` (lines 289-297)
- Schema: `xl_sheetagent/v3a/schemas.py` (TableAgentResult)
- Split logic: `xl_sheetagent/v3a/split_agent.py` (decide_splits)
- Hierarchy logic: `xl_sheetagent/v3a/table_agent.py` (_collect_candidates_by_role)
