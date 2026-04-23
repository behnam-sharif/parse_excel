# spec_dag_v7.md — DAG Specification (MVP)

## Status

This document is the **contract** for the DAG layer (v7). It is given to Claude Code as **context only** — Claude Code should NOT implement DAG from this spec. It should implement only the finalize stage (see `spec_finalize_v6.md`). This spec tells Claude Code what finalize output must support.

---

## Purpose

The DAG represents **table-level formula dependencies** across a workbook. Each node is one finalized core region (from sheet map). Each edge B → A represents "core region B feeds core region A" via a formula reference.

xl_ask uses the DAG to answer questions about how values flow through the model: upstream sources, downstream effects, implementation paths.

---

## Node Model

### What is a DAG node?

One DAG node = one finalized core region. Standalone tables contribute one node; parent tables contribute zero nodes (parents are navigation-only); child_numeric tables contribute one node per core region.

### Node fields

| Field | Type | Source |
|---|---|---|
| `node_id` | string | Built: `{sheet_slug}__{table_id}__{core_idx}` |
| `table_id` | string | From finalize entry |
| `sheet_name` | string | From finalize entry |
| `bbox` | `{r1, c1, r2, c2}` | Core region bbox from finalize |
| `title` | string | From finalize entry (may be inherited from parent) |
| `headers` | list of lists | From finalize entry |
| `row_names` | list of strings | From finalize entry |
| `parent_path` | list of strings | Ancestry chain: `[sheet_name, parent_title, ...]` |
| `node_type` | string | `input` / `calc` / `output` / `mixed` / `data_table` — from v6 `table_role` |
| `representative_formulas` | list of AST objects | One per unique AST pattern in the core region (see below) |
| `referenced_cells` | list of `(sheet, row, col)` | Deduplicated, range-expanded cell references across all representative formulas |
| `referenced_ranges` | list of strings | Original range refs preserved for readability (e.g., `Assumptions!B5:B20`) |
| `referenced_named_ranges` | list of strings | Named ranges used in formulas |
| `search_text` | string | Concatenation of title + headers + row_names + parent_path + sheet_name + node_type for xl_ask lexical search |
| `flags` | list of strings | `low_context_node`, `no_representative_formula`, `heterogeneous_formulas_in_column`, `cross_workbook_ref_unresolved`, `dynamic_reference_unresolved`, `named_range_multi_region`, etc. |

### Representative formulas

Core regions in HEOR models often have one formula dragged across rows/columns. Storing every cell's formula is wasteful. Instead:

1. For each column of the core region, collect all formula strings.
2. Parse each into an AST.
3. Group by **AST shape** — structure + function calls + relative/absolute reference pattern. Two formulas with the same shape but different concrete references are one group (because `=B5*$C$2` and `=B6*$C$2` share the same shape).
4. Store one representative per unique shape, with the row range it applies to.

Each representative formula has:
- `ast`: the parsed AST (see AST section below)
- `column_index`: which column in the core region
- `row_range`: `[r1, r2]` range this pattern applies to
- `raw_formula_string`: the original text for human reference

If a column has heterogeneous formulas that don't group cleanly, store all distinct ASTs and flag `heterogeneous_formulas_in_column`.

### AST representation

The custom parser produces an AST with these node types:

| AST node type | Fields |
|---|---|
| `literal` | `value`, `dtype` (number, string, bool) |
| `cell_ref` | `sheet`, `row`, `col`, `row_absolute` (bool), `col_absolute` (bool) |
| `range_ref` | `sheet`, `r1`, `c1`, `r2`, `c2`, absoluteness flags |
| `named_range_ref` | `name` (kept unresolved; resolved at edge-build time) |
| `function_call` | `name` (SUM, IF, VLOOKUP, etc.), `args` (list of AST nodes) |
| `operator` | `op` (+, -, *, /, &, =, <, etc.), `left`, `right` |
| `unresolved` | `reason_code` (e.g., `indirect`, `offset`, `structured_ref`, `external_workbook`), `raw_string` |

The parser does NOT evaluate formulas. It only extracts structure and references.

### Minimum node quality

Every finalized core region becomes a node. No filtering. Flags are used instead:
- `low_context_node` — no title AND no headers AND no row_names
- `no_representative_formula` — pure `data_table` with no formulas
- `cross_workbook_ref_unresolved` — references point outside the workbook
- `dynamic_reference_unresolved` — INDIRECT, OFFSET, or similar functions present
- `heterogeneous_formulas_in_column` — fallback when AST grouping fails
- `named_range_multi_region` — named range resolves to multiple non-contiguous ranges

xl_ask ranks low-context nodes last but never hides them.

---

## Edge Model

### What is a DAG edge?

One edge B → A when a representative formula of core region A references a cell inside core region B's bbox.

Edge granularity is **node-to-node, not cell-to-cell.** If A's formulas reference 5 cells in B, that's one edge with 5 reference cells stored in the edge's detail.

### Edge fields

| Field | Type |
|---|---|
| `edge_id` | string: `{source_node_id}__TO__{target_node_id}` |
| `source_node_id` | string (B — the feeder) |
| `target_node_id` | string (A — the consumer) |
| `reference_cells` | list of `(sheet, row, col)` cells in B that A's formulas reference |
| `reference_type` | `direct_cell` / `range` / `named_range` / `cross_sheet` |
| `formula_context` | list of which representative formulas in A contain the reference (column indices) |
| `named_range_name` | optional string — if edge is via named range, the name (for explain_edge) |

### Unresolved reference edges

If a formula references a cell that does NOT fall inside any known core region, create a virtual edge:
- `source_node_id = "__UNRESOLVED__{reason_code}"`
- `target_node_id` = the consuming node
- `reference_cells` = the unresolvable cells (if known) or raw string
- `reference_type = "unresolved"`

These surface in xl_ask as "this formula depends on data outside tracked tables." Don't silently drop them — the alternative is "why does the DAG miss this edge?" debugging for days.

Reason codes for unresolved:
- `indirect` — INDIRECT function
- `offset` — OFFSET function
- `structured_ref` — Table1[Col] references
- `external_workbook` — [OtherFile.xlsx]
- `outside_core_regions` — reference is valid but not in any known core region
- `array_formula_spill` — dynamic array spill range

### Cross-sheet edges

Node IDs are global across the workbook. A reference from `Sheet2` to `Sheet1!B5` creates an edge between whichever core regions live on each sheet. No special handling beyond the node_id format including the sheet slug.

---

## Named Range Handling

Named ranges are in scope for MVP. Algorithm:

1. **Build phase:** at DAG construction, read all named ranges from the workbook. Store `{name: [list of (sheet, r1, c1, r2, c2) ranges]}`.
2. **Parse phase:** the custom formula parser emits `named_range_ref` AST nodes with the name preserved (not resolved).
3. **Edge phase:** when walking representative formulas to build edges, resolve each `named_range_ref` to its cell range(s). Check which core regions overlap. Create an edge with `reference_type="named_range"` and `named_range_name` populated.
4. **Multi-region names:** flag with `named_range_multi_region` on the node. Emit one edge per region.

Explain_edge output preserves the original name: "feeds via named range `DiscountRate`" reads better than "feeds via Assumptions!$B$5."

---

## Tool API (xl_ask calls these)

All tools are deterministic. No LLM. All return structured data.

### Discovery tools

- `find_node(query_text, filters={node_type?, sheet_name?, parent_path?})` — lexical + ontology-expanded search over `search_text`. Returns ranked list of node_ids with match snippets.
- `list_inputs()` — all nodes where `node_type == "input"`
- `list_outputs()` — all nodes where `node_type == "output"`
- `list_nodes_by_sheet(sheet_name)` — all nodes on a sheet
- `list_nodes_by_parent(parent_title)` — all nodes under a parent

### Graph traversal tools

- `upstream(node_id, depth=1)` — nodes feeding this node (follow incoming edges). Depth > 1 recursively traverses.
- `downstream(node_id, depth=1)` — nodes fed by this node.
- `shortest_path(source_id, target_id)` — list of node_ids. Returns empty if unreachable.
- `ancestors(node_id)` — transitive upstream closure.
- `descendants(node_id)` — transitive downstream closure.

### Inspection tools

- `get_node(node_id)` — returns full node record.
- `get_edge(source_id, target_id)` — returns edge record including reference_cells and formula_context.
- `explain_edge(source_id, target_id)` — human-readable explanation: "Cost.Total references DiscountRate at cell Assumptions!B5 via named range `DiscountRate`."
- `get_formula(node_id, column_index?)` — returns representative formula(s) as raw string + AST. If column_index omitted, returns all.
- `get_ast(node_id, column_index)` — AST only.
- `node_cached_value(node_id, row, col)` — returns the last-saved computed value from the workbook file (via openpyxl `data_only=True`). Flag `cached_value_may_be_stale` if uncertain.

### Out of scope for MVP

- `node_value(...)` — true formula evaluation (requires recalc engine). Post-MVP.
- Vector search / embeddings. xl_ask uses lexical + ontology.
- Write tools. DAG is read-only.

---

## DAG Build Algorithm (summary)

Input: `final_table_map.json` from finalize, plus the workbook file.

1. **Load named ranges** from workbook.
2. **For each map entry that is a leaf (standalone or child with core_region):**
   a. Compute `node_id`.
   b. Collect formulas in the core region bbox.
   c. Parse each formula to AST (custom parser).
   d. Group ASTs by shape per column → representative formulas.
   e. Extract `referenced_cells`, `referenced_ranges`, `referenced_named_ranges` from ASTs.
   f. Build `search_text`.
   g. Emit node.
3. **Build `cell_to_node` index** mapping every cell inside any core region to its node_id.
4. **For each node A, for each representative formula, for each reference:**
   a. Resolve named ranges to cell ranges.
   b. Look up referenced cells in `cell_to_node`.
   c. If found → emit edge from referenced node to A.
   d. If not found → emit unresolved edge with appropriate reason code.
5. **Deduplicate edges** between the same (source, target) pair by merging reference_cells.
6. **Emit** `dag.json` with nodes and edges.

---

## Output: `dag.json`

```json
{
  "workbook_name": "...",
  "build_version": "v7-mvp",
  "nodes": [ {node fields as above}, ... ],
  "edges": [ {edge fields as above}, ... ],
  "named_ranges": { "DiscountRate": [{"sheet": "Assumptions", "r1": 4, "c1": 1, "r2": 4, "c2": 1}] },
  "build_stats": {
    "total_nodes": N,
    "total_edges": M,
    "unresolved_edges": K,
    "nodes_by_type": {...},
    "flags_frequency": {...}
  }
}
```

---

## What finalize MUST provide (contract)

This is the key section for Claude Code when implementing finalize:

1. **Every leaf entry (standalone or child_numeric) must have `core_region` populated** — at minimum the bbox. This is the source of DAG nodes.
2. **Every leaf entry must have `sheet_name`, `title`, `headers`, `row_names`, `parent_path`, `node_type`** (node_type = finalize's `table_role` field). These populate node metadata.
3. **Parents must have `title` and `coordinates`** — used for `parent_path` reconstruction on children.
4. **Every entry must have a stable `table_id`** — used to build `node_id`.
5. **Flags on finalize entries pass through to DAG nodes** — e.g., `title_missing` becomes `low_context_node` signal.
6. **Parents contribute zero DAG nodes.** Finalize must not emit core_region at the parent level.
7. **`parent_path` reconstruction:** for a child, parent_path = `[sheet_name, parent.title]`. For a standalone, parent_path = `[sheet_name]`. For a grandchild (if any), parent_path = `[sheet_name, grandparent.title, parent.title]`.

---

## Out of Scope for MVP

- Formula evaluation / recalc engine
- Cross-workbook reference resolution
- Structured table references (`Table1[Col]`)
- Circular reference detection (HEOR models shouldn't have them; flag if found)
- Time-indexed DAG semantics (cycle-indexed core regions are one node, not N)
- Vector embeddings / RAG
- Write tools / DAG mutation
- Incremental DAG rebuild

---

## Implementation Order (when DAG is implemented)

1. Custom formula parser (reference-extractor only) + AST schema
2. Named range loader
3. Node builder (consume finalize output)
4. Cell-to-node index
5. Edge builder with named range resolution
6. Unresolved edge bookkeeping
7. Tool API implementation
8. JSON serialization
9. Tests on 3 real HEOR workbooks

Estimated: 3–5 days once finalize is done.