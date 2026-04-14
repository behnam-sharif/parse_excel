# xl_sheetagent_v5

Deterministic semantic enrichment layer for structural tables produced by the upstream pipeline (xl_parse -> xl_table). V5 takes bounding boxes and cut scores from parse, calls V3a for hierarchical splitting, then enriches every table node with row-level tags, headers, row names, notes, core regions, and table roles — all without LLM.

## Current state

**Fully deterministic. No LLM calls.** `config.use_llm` defaults to `false`. All fields on every table are populated by heuristics. LLM code paths exist but are gated behind config flags and are inactive in production.

## How it works

### Input acquisition

1. **Bboxes** — loaded from V3a results (`sheetagent_output/v3a_results.json`) or xl_table output (`table_output/tables_per_sheet.json`, `structural_tables.json`). Each bbox carries a `hierarchy_role` (`parent`, `child_numeric`, `child_metadata`, `standalone`), `parent_table_id`, and `split_depth` from V3a's structural splitting.

2. **Cut scores** — loaded from `parse_output/cut_scores.jsonl` (produced by xl_parse). Used optionally for column-cut-based row name detection.

3. **Workbook** — the cleaned workbook from `init_output/cleaned_workbook.xlsx`, opened `data_only=True` per thread.

### Pipeline (per sheet, parallel)

Sheets are dispatched to a `ThreadPoolExecutor` (each thread opens its own openpyxl workbook for thread safety). Within a sheet, tables are processed **sequentially in parent-before-child order** (sorted by `split_depth`, then `r1`, then `c1`).

For each table bbox, the runner reads the cell grid (values + types + bold + merges) and calls `analyze_table()`:

```
Step 1  Row Tagging (Layer 1)           row_tagger.tag_rows()
        Every row gets a heuristic tag:
          title_candidate       — sparse text in top rows
          title_header_candidate — mixed title+column-label row at top
          header_candidate      — high text ratio, low numbers
          rowname_candidate     — leftmost TEXT, right side NUMBERs
          note_candidate        — keyword-led (source/note/footnote), sparse
          data_candidate        — default fallback
        Each tag carries a confidence score (0.3–0.95).

Step 2  Header Detection                header_detector.detect_headers()
        Finds consecutive header_candidate rows before the first data row.
        Expands merged cells across spanned columns.

Step 3  Header Inheritance              header_detector.find_restricted_header_inheritance()
        If no local headers found, inherit from direct parent or
        parent-role donors above. Column overlap must exceed tolerance;
        distance penalty applied (0.9 within 2 rows, down to 0.5 beyond 10).

Step 4  Candidate Collection            candidate_collector.collect_candidates()
        Role-aware extraction — V3a hierarchy_role constrains what is collected:
          parent:          title + header + notes (section headers excluded)
          child_numeric:   title + row_names + footnotes
          child_metadata:  title + header only
          standalone:      title + header + row_names + footnotes
        Produces a ConsumedMap tracking which rows/cols are assigned.

Step 4.5  Composite Row-0 Detection
        If row 0 looks like [title_cell, col1, col2, ...], split the first
        cell as the table title and blank it out of the header.

Step 5  Core Region Detection           core_region_detector.detect_core_regions()
        BFS-based connected-component labeling on NUMBER + FORMULA cells.
        Consumed rows/cols are zeroed out first. Components are:
          - expanded with structurally relevant blanks (density gate)
          - merged if within gap threshold
          - filtered by minimum area
        Each CoreRegion gets a role_hint:
          input   — mostly NUMBER, few FORMULA
          calc    — formula-heavy
          output  — formula-heavy + bottom third of table
          mixed   — everything else

Step 5.5  Column-Cut Row Name Refinement (optional)
        If config.use_column_cuts_for_row_names is true, override heuristic
        row names with cut-score-aligned column detection. Identifies leftmost
        columns with high TEXT ratio, left of the first core region.

Step 6  Layer 2 Tag Resolution          row_tagger.assign_final_tags()
        Marks every row with its final semantic tag based on the consumed map:
          consumed_title, consumed_header, consumed_rowname,
          consumed_note, unassigned_context, data

Step 7  Table Role                      table_agent._determine_table_role()
        Deterministic classification from core region role_hints:
          input / calc / output / mixed / metadata

Step 8  Confidence Score                table_agent._compute_confidence()
        Weighted blend: row_tag_conf, header_conf, core_region_conf, role_consistency.

Step 9  Return V5TableResult
        Fully enriched table with: bbox, title, headers, row_names, notes,
        core_regions, table_role, row_tags (Layer 1+2), formatting, confidence,
        signals (provenance dict).
```

### Post-analysis (per sheet)

After all tables in a sheet are analyzed:

1. **Hierarchy reconstruction** — parent/child relationships from V3a are re-linked on the V5TableResult objects (children lists populated).

2. **Table resolution** (`table_resolver.resolve_sheet_tables()`) — merges parent + children into consumer-facing `ResolvedTable` objects:
   - Children are classified as **preserved** (kept as nested children) or **absorbed** (metadata merged into parent). Preservation is the default — a child is kept if it has a title, covers unique rows, has core regions, or its removal would leave gaps.
   - Parent becomes a **container**: shared headers, `child_titles` list, `row_names = None`.
   - Standalone tables pass through as leaf ResolvedTables.
   - Multi-core consistency: if multiple core regions exist but no children, stub children are inferred from the regions.

3. **Export** (`exporter.py`) — writes to `v5_sheetagent_output/`:
   - `tables.json` — full V5TableResult tree
   - `resolved_tables.json` — ResolvedTable objects (hierarchical by default, flat with `export_mode: "flat"`)
   - `summary.json` — sheet-level stats
   - `audit/table_decisions.jsonl` — per-table decision log

## Key data models (schemas.py)

| Dataclass | Purpose |
|-----------|---------|
| `RowTag` | Per-row: `layer1_tag`, `layer2_tag`, `confidence`, `signals` |
| `NoteEntry` | Row index, text, type (`footnote`/`source`/`annotation`), source |
| `CoreRegion` | Bbox, cell count, type distribution, density, `role_hint` |
| `FormattingInfo` | Bold, fill, borders, alignment, header merges |
| `ConsumedMap` | Internal bookkeeping: title_rows, header_rows, rowname_cols, note_rows |
| `V5TableResult` | Full enriched table (hierarchy_role, bbox, title, headers, row_names, notes, core_regions, table_role, row_tags, formatting, children, confidence, signals) |
| `ResolvedTable` | Consumer-facing merged table (child_titles, children, child_summaries, metadata_source) |
| `V5RunResult` | Per-sheet result: tables, resolved_tables, token_count, errors |

## Config (XlSheetAgentV5Config)

91 fields in `config.py`, all sourced from `config/stages.yaml` under `xl_sheetagent_v5`. Key groups:

- **Row tagging** — `text_ratio_header`, `number_ratio_data`, `note_fill_max`, `title_max_nonempty`
- **Header detection** — `header_zone_pct`, `header_col_tolerance`
- **Header inheritance** — `inherit_header_col_tolerance`
- **Core regions** — `min_component_area`, `component_merge_gap`, `blank_inclusion_density_threshold`
- **Child rules** — `min_child_rows`, `iou_merge_threshold`
- **Confidence weights** — `w_tags`, `w_header`, `w_core`, `w_llm`, `w_role`
- **Resolution** — `enable_resolution`, `resolve_row_names_with_parent`, `absorb_metadata_only_children`
- **Row names** — `use_column_cuts_for_row_names`, `row_name_text_ratio_threshold`
- **Multi-core** — `enforce_multi_core_children`
- **Export** — `export_mode` (`"hierarchical"` default, `"flat"` for legacy)
- **LLM (inactive)** — `use_llm`, `llm_model`, `llm_max_tokens`, `use_llm_metadata_resolution`

## Module map

```
xl_sheetagent_v5/
  __init__.py              exports run_v5_analysis
  config.py                XlSheetAgentV5Config (91 fields)
  runner.py                entry point, sheet-level parallelization, bbox/cut_score loading
  table_agent.py           per-table 9-step analysis pipeline
  row_tagger.py            Layer 1 heuristic + Layer 2 resolved row tagging
  header_detector.py       multi-row header detection + restricted inheritance
  candidate_collector.py   role-aware title/row_name/note collection
  core_region_detector.py  BFS connected-component core region detection
  table_resolver.py        parent+children -> ResolvedTable assembly
  formatting_agent.py      heuristic formatting extraction (bold, fill, borders)
  prompt_builder.py        LLM prompt construction (inactive, gated by use_llm)
  llm_audit.py             LLM call audit recording (inactive when no LLM)
  exporter.py              JSON serialization + audit JSONL
  note_keywords.py         shared footnote/note keyword set
  schemas.py               all dataclasses (RowTag, CoreRegion, V5TableResult, ResolvedTable, etc.)
```

## Running

```bash
# V5 standalone (after parse + table stages have run)
py -3 scripts/run_sheetagent.py --run-id myrun --v5

# Full pipeline with sheetagent
py -3 scripts/run_pipeline.py --input workbook.xlsx --run-id myrun --with-sheetagent
```

## v5.1 plan

`plan_sheetagent_v5_1.md` describes the next phase: child-centric hierarchy with coverage recovery, multi-core materialization, stricter notes classification, and numeric-cell ownership for overlapping regions. Phased P0 (schema + export) through P4 (overlap + formula enrichment).
