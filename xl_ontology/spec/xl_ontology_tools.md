# xl_ontology_tools.md — Tool List and Signatures

Companion document to `spec_xl_ontology_v0.md`. Defines the tool API the
ontology agent uses. Tools are read-only and deterministic except where
noted.

---

## Sheet/table discovery

### `find_sheets_by_name_pattern(patterns: list[str]) → list[str]`
Returns sheet names matching any of the patterns (case-insensitive,
substring or fuzzy).

### `get_first_sheet() → str`
Returns the first sheet of the workbook regardless of name.

### `list_tables_on_sheet(sheet_name: str) → list[TableSummary]`
Returns table summaries (table_id, title, headers, bbox) on the sheet, in
spatial order top-to-bottom.

### `get_table_content(table_id: str) → TableContent`
Returns full table content: cells, headers, row names, formatting per
cell, formulas. Uses `final_table_map` and the underlying workbook.

---

## Cell-level access

### `get_cells_in_range(sheet: str, range: str) → list[Cell]`
Returns raw cells in a range. `range` is Excel-notation (e.g., `A1:F10`).
Used for things like rows 1-10 of a sheet where switches often live
outside any table.

### `get_cells_above(table_id: str, n_rows: int) → list[Cell]`
Returns cells in the n rows immediately above a table's bbox, on the same
sheet. Used by title recovery procedure.

### `get_cells_left(table_id: str, n_cols: int) → list[Cell]`
Returns cells in the n columns immediately left of a table's bbox, on
the same sheet. Used by row-name recovery procedure.

### `get_cell_format(sheet: str, cell: str) → CellFormat`
Returns formatting metadata: bold, fill, borders, number format, data
validation, font size, merge state.

---

## Lexicon search

### `search_workbook_lexicon(terms: list[str], scope: str) → list[Hit]`
Searches lexicon terms across the workbook. `scope` is one of:
- `text_cells_only` — only cells with text (no numbers, no formulas)
- `cell_values_and_formulas` — cell values and formula text
- `titles_and_headers_only` — only table titles and headers
- `row_labels_only` — only first column / row labels
- `whole_table` — for **every table** in the workbook, search across the
  union of: title + headers (all rows) + row_names + body cell values +
  formula text. One call covers everything inside table boundaries.
  Used by the cross-check decision rules (see ontology pack §6 in each
  node's `.txt` file) when the cheap-scope pass missed a concept and the
  agent needs to look harder before recording absent.
- `all` — everything (including loose cells outside any table)

Returns hits with `{sheet, cell, table_id, snippet, matched_term,
match_location}` where `match_location` is one of:
- `"title"` — hit is in a table title
- `"header"` — hit is in a header row
- `"row_label"` — hit is in a row label / first column
- `"body_cell_value"` — hit is in a body cell value
- `"formula_text"` — hit is in a formula's text

The `match_location` field lets the agent assign different evidence
weights per location (a title hit is stronger evidence than a formula-
text hit) and lets the per-agent cross-check decide which heuristic
priors apply (e.g., row-label hits trigger row-name-based heuristics
preferentially).

The cheap scopes (`titles_and_headers_only`, `row_labels_only`,
`text_cells_only`) remain available for the planner's initial pass.
Use `whole_table` only for cross-check fallback to keep planner-time
prompt budgets predictable.

### `search_named_ranges(terms: list[str]) → list[NamedRangeHit]`
Returns named ranges whose name matches any lexicon term.

---

## DAG access

### `get_dag_neighbours(node_id: str, direction: str, depth: int) → list[Node]`
Returns upstream / downstream / both neighbours from a node, up to
`depth` hops.

### `get_terminal_nodes() → list[Node]`
Returns nodes with zero or near-zero fan-out. Primarily used by the
results agent.

### `get_high_fanout_nodes(min_fanout: int) → list[Node]`
Returns nodes with fan-out ≥ `min_fanout`. Primarily used by the inputs
agent.

### `get_high_fanin_nodes(min_fanin: int) → list[Node]`
Returns nodes with fan-in ≥ `min_fanin`. Used by calculations and
results agents.

---

## Composite tools (overall_model agent)

### `find_overview_sheets(name_patterns: list[str]) → list[str]`
Composite of: `find_sheets_by_name_pattern` + `get_first_sheet` + sheets
with `data_table` or `orphan_metadata` tables. Deduplicated.

### `list_data_validation_dropdowns(sheets: list[str]) → list[Dropdown]`
Returns cells with data validation lists across the given sheets. Each
dropdown has `{sheet, cell, current_value, allowed_values}`.

### `extract_currency_symbols(sheets: list[str]) → dict[str, list[Cell]]`
Maps currency symbol (£, $, €, ¥) to the cells where it appears. Used
for inferring country when no explicit country cell exists.

---

## LLM tool (budget-enforced)

### `ask_llm(payload: LLMPayload, ontology_context: list[str]) → LLMResponse`
Single bounded LLM call. The tool enforces the budget defined in
`spec_xl_ontology_v0.md` Section 11.

`payload` is a structured object matching one of the defect-procedure
shapes (Section 10 of spec) or a planner shape or a synthesis shape.

`ontology_context` is a list of file contents (e.g.,
`pack_overview.context.md`, `<node>.context.md`, `<node>.txt`, plus
optional heuristic chunks) to include in the prompt.

Returns `{response, confidence, tokens_used, applied_heuristic_ids}`.

---

## Write tools (ontology map only — never mutate upstream)

### `record_concept(concept_name, value, evidence, confidence, evidence_quality)`
Appends to `overall_model.concepts[concept_name]`.

### `record_search_log(concept_name, search_log_entries)`
Appends search log entries for an absent concept.

### `record_summary(node, text, source)`
Records the node's summary. `source` is `template` or `llm_synthesised`.

### `record_classification(table_id, ontology_node, ontology_subtype, ...)`
Appends a candidate table classification to the appropriate node.

### `record_node_type_override(table_id, sheet, title, upstream_label, ontology_label, ontology_subtype, evidence, confidence, applied_heuristic)`
Appends one line to `ontology_output/node_type_override_log.jsonl` (see
spec §12.11). The agent calls this at the same point it sets
`node_type_override_proposed: true` on a candidate. The log is a
labelled-disagreement corpus for future finalize ML training.

### `record_parent_group(node, parent_group_entry)`
Appends a parent_groups entry.

### `flag_correction(table_id, flag_type, evidence)`
Adds an advisory flag to `proposed_table_corrections`. Never propagates
upstream.

---

## Tool guarantees

- All read tools are idempotent and deterministic.
- All read tools are side-effect-free.
- `ask_llm` is the only tool that calls an LLM. It enforces the budget
  and logs every call to `ontology_run_log.jsonl`.
- Write tools mutate only the in-memory ontology map. The map is written
  to disk once per run (seed phase) or once per enrichment round.
- No tool modifies `final_table_map.json`, `dag.json`, `node_index.json`,
  `formulas.json`, or the workbook itself.
