# V3A Hierarchical Splitting - Quick Start

## 1-Minute Setup

### Enable Feature
```yaml
# config/stages.yaml (line 293)
v3a_hierarchical_splits: true  # ✅ Already enabled
```

### Run Pipeline
```bash
python3 -c "
from xl_sheetagent.v3a.runner import run_v3a
result = run_v3a('streamlit_run_simple')
print(f'✅ Done: {result.n_tables_processed} tables')
"
```

### Check Output
```bash
# View hierarchy
cat data/output/streamlit_run_simple/sheetagent_output/v3a_summary.json | python3 -m json.tool | grep -B 2 -A 2 'hierarchy_role'
```

---

## What Changed?

### Your Issue
> "5 potential splits, but none carried out"

### Solution
✅ All 5 splits now execute  
✅ Children under parent (not new tables)  
✅ Row names with formulas collected

---

## Quick Reference

### Config Flags
```yaml
v3a_hierarchical_splits: true      # Enable parent-child hierarchy
v3a_multi_split_enabled: true      # Execute ALL valid splits
v3a_row_names_allow_formulas: true # Allow formulas in row names
v3a_split_max_depth: 2             # Max recursion depth
```

### Hierarchy Roles
- **📦 Parent** - Table that was split (has children)
- **📊 Child (data)** - Numeric data child (>0% numeric)
- **📝 Child (metadata)** - Header/title child (0% numeric)
- **Standalone** - Table with no parent

### Candidate Collection Rules
| Role | Collects |
|------|----------|
| Parent | TITLE + HEADER |
| Child (numeric) | TITLE + ROW_NAME + FOOTNOTE |
| Child (metadata) | TITLE + HEADER |
| Standalone | ALL (TITLE + HEADER + ROW_NAME + FOOTNOTE) |

---

## Files Modified
- `xl_sheetagent/v3a/*.py` (5 files)
- `apps/orchestrator/v3a_viewer.py` (1 file)
- `config/stages.yaml` (1 file)
- `tests/test_v3a_*.py` (4 new test files)

---

## Verify It Works

### Check for Children
```python
import json
with open('data/output/streamlit_run_simple/sheetagent_output/v3a_results.json') as f:
    data = json.load(f)
    for sheet in data['sheets']:
        for table in sheet['table_results']:
            if table.get('children'):
                print(f"{table['table_id']}: {len(table['children'])} children")
```

### Check for Row Names
```bash
grep -A 5 '"row_name_candidates"' data/output/streamlit_run_simple/sheetagent_output/v3a_results.json | grep -c '"piece_type": "row_name"'
```

---

## Rollback
```yaml
# config/stages.yaml
v3a_hierarchical_splits: false  # Disable feature
```

---

## Documentation
- `IMPLEMENTATION_COMPLETE.md` - Full summary
- `V3A_HIERARCHY_QUICKSTART.md` - User guide
- `IMPLEMENTATION_SUMMARY.md` - Technical details

---

**Status: ✅ Ready to test**
