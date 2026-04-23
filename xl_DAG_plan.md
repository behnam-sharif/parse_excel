# spec_dag_v7.md — DAG for Cheri (Product-First Spec)

## Status

This is the **specification** for the v7 DAG layer, written from the Cheri user's perspective. It is a spec, not an implementation plan — Claude Code should **plan the implementation from this spec, prioritizing the Cheri-facing contracts first**, and then implement phase by phase.

Upstream: `final_table_map.json` + `summary.json` (from v6 finalize).
Downstream: Cheri UI + xl_ask tool layer.

---

## 1. Cheri Big Picture — What Happens After Parsing

When a user uploads a workbook and clicks Play, Cheri must walk them through a clear lifecycle:

```
uploading → parsing → table_map_ready → dag_building → dag_ready → ask_ready
```

Each state transition is visible to the user. The product experience is **track → done → inspect → ask**, not "JSON produced."

**On `table_map_ready`, Cheri shows the Model Tree** (per sheet, parents with children, standalones) driven by `final_table_map.json`. Users can already browse structure at this point. DAG has not been built yet.

**On `dag_ready`, Cheri shows the DAG Summary overlay** on top of the Model Tree — aggregate badges per sheet (node counts, edge counts, unresolved count, flags). Users can see at a glance what was extracted.

**On `ask_ready`, the OUTCOME button becomes enabled** under the parsed file. Clicking it opens the Ask window. This is the only point where full query-scoped subgraphs are rendered.

The DAG is never shown as a full graph in the workbook overview. It surfaces only in two places:

1. **Table Detail panel** (shown on Model Tree node click) — a small "Depends on / Used by" expander rendering 5–10 direct neighbors.
2. **Ask window** (opened from OUTCOME) — a query subgraph rendered next to each LLM answer (5–20 nodes).

---

## 2. Cheri Status Pipeline (canonical)

| State | Meaning | Artifact present |
|---|---|---|
| `uploading` | User is uploading a workbook | — |
| `parsing` | xl_parse → xl_sheetagent v6 → finalize in flight | — |
| `table_map_ready` | Finalize complete; Model Tree can render | `final_table_map.json`, `summary.json` |
| `dag_building` | DAG construction in flight | — |
| `dag_ready` | DAG built; overview badges can render; table detail subgraphs work | `dag.json`, `dag_summary.json`, `table_map_overview.json`, `node_index.json`, `formulas.json` |
| `ask_ready` | xl_ask backend is warm; OUTCOME enabled | All above + ontology loaded + xl_ask service live |

Cheri may render a small linear progress indicator in the left panel next to the file name as states advance. Failures at any stage surface as a clear error badge with a "why did this fail?" link that reveals the server's error message verbatim — no silent failures.

For MVP v1 (student fixtures): all states transition synchronously after a simulated delay. Real async streaming is v2.

---

## 3. MVP Deliverables to Cheri

These are the **contract artifacts** Cheri consumes. Each has a dedicated TypeScript schema (see `src/contracts/`).

| Artifact | Purpose | Consumer |
|---|---|---|
| `final_table_map.json` | Per-sheet tree of tables (standalones + parents with children) | Model Tree (middle panel) |
| `summary.json` | Run stats: total tables, per-sheet counts, role distribution | Left panel under filename |
| `table_map_overview.json` | Condensed UI-facing summary of the table map for overview cards | Overview badges + sheet headers |
| `dag.json` | Full graph: nodes + edges + named ranges | Loaded once; subgraph extracted client-side |
| `dag_summary.json` | UI-facing DAG overview (aggregates only) | Overview badges, warnings banner |
| `node_index.json` | Lookup indices for fast retrieval | xl_ask tools |
| `formulas.json` | Representative formulas + ASTs (lazy-loaded) | Opens only when a subgraph edge is clicked |
| (xl_ask) `AskResponse` | Answer text + citations + query subgraph | Ask window |

---

## 4. Not Shown in Cheri by Default

Out-of-scope for MVP UI rendering:

- Raw ASTs
- Full `formulas.json` content
- Full `node_index.json` content
- Every flag on every node (shown only when a specific node is inspected)
- All unresolved reference fragments (shown only via the warnings drawer — see §8)
- Full workbook DAG as an interactive graph

Data still persists to disk; Cheri just does not surface it in the default UI. Power-user "inspect" tools can expose these post-MVP.

---

## 5. UI Contracts (Cheri-facing JSON)

### 5.1 Table Map Overview (`table_map_overview.json`)

A condensed summary of `final_table_map.json` for rendering overview cards. One per workbook.

```ts
// src/contracts/tableMapOverview.schema.ts

export interface TableMapSheetOverview {
  sheet_name: string;
  n_tables: number;              // total leaves (standalones + children)
  n_parents: number;             // parent containers
  n_standalones: number;
  n_children: number;
  n_with_core_region: number;    // i.e. future DAG nodes
  n_without_core_region: number; // metadata-only tables
  table_titles_preview: string[]; // first 5 non-null titles for the card preview
  node_types: Record<string, number>; // {"calc": 5, "input": 2, "metadata": 8, ...}
  flag_counts: Record<string, number>; // aggregated source_flags across entries
}

export interface TableMapOverview {
  workbook_name: string;
  total_sheets: number;
  total_tables: number;
  total_with_core_region: number;
  sheets: TableMapSheetOverview[];
}
```

This is the **only** thing Cheri reads to render the overview. No walking `final_table_map.json` client-side for aggregate rendering.

### 5.2 DAG Summary (`dag_summary.json`)

Aggregate DAG statistics, rendered as overview badges and the warnings banner.

```ts
// src/contracts/dagSummary.schema.ts

export interface DagSheetSummary {
  sheet_name: string;
  node_count: number;
  node_types: Record<string, number>;
  outgoing_edges: number;     // edges where source is on this sheet
  incoming_edges: number;     // edges where target is on this sheet
  cross_sheet_edges: number;  // edges with source and target on different sheets
}

export interface DagSummary {
  workbook_name: string;
  build_version: string;
  total_nodes: number;
  total_edges: number;
  total_unresolved_edges: number;
  nodes_by_type: Record<string, number>;
  named_ranges_count: number;

  // Top sheets by node count, for the overview card ordering
  top_sheets_by_nodes: string[];

  // Sheet-level aggregates
  sheets: DagSheetSummary[];

  // Rollup counts for the warnings banner (see §8)
  warnings: {
    low_context_nodes: number;
    dynamic_references: number;
    cross_workbook_references: number;
    heterogeneous_formulas: number;
    circular_references: number;
  };

  // Aggregated inter-sheet edge bundles for the overview visualization
  sheet_edge_bundles: Array<{
    source_sheet: string;
    target_sheet: string;
    edge_count: number;
  }>;
}
```

**MVP boundary: the DAG overview in Cheri is aggregate-only.** Sheet cards with counts, inter-sheet edge bundles as simple lines, warnings count as a single banner. **Do not render a full interactive graph of the whole workbook.** That's post-MVP.

### 5.3 Query Subgraph

Already defined in `subgraph.schema.ts`. Used by:
- Ask window (subgraph next to each answer)
- Table Detail panel (depends-on / used-by expander)

Both share the exact same shape. Student extracts query subgraphs client-side from `dag.json` using BFS from a focus node.

### 5.4 Warnings Summary

Emitted as part of `dag_summary.warnings`. Cheri renders **one warnings banner** (not a flood of flags). Example: "DAG built with 3 unresolved links and 2 low-context nodes. [View details]". Clicking opens a drawer listing the nodes/edges with their flags and coordinates.

No raw flags spread across UI. One banner, one drawer, per workbook.

---

## 6. Relationship Between xl_sheetagent v6 Output and DAG

Keep this distinction sharp in the UI and in docs:

- **Table Map (from xl_sheetagent v6 + finalize)** = the **structural layer**. How the workbook is organized: sheets, tables, parents, children, standalones. What each table "is" — title, headers, row names, notes, node type.

- **DAG (from v7)** = the **computational layer**. How tables feed each other via formulas. What flows into what.

The table map is complete without the DAG. A user can browse the Model Tree and read table metadata even before DAG is built. The DAG adds **dependency inspection** — "where does this value come from?" and "what does this affect?" — on top of the same leaf regions the table map already identified.

Both layers share `table_id`s. Every DAG node links back to a table map leaf entry. No DAG node exists without a table map entry.

---

## 7. Node and Edge Model (backend contract)

This section is the authoritative source for `dag.schema.ts`. Kept short on purpose. Full TypeScript types are in `src/contracts/dag.schema.ts`.

### 7.1 What is a node?

One DAG node = one finalized core region from the table map.

- Standalone entries with non-null `core_region` → one node.
- Parent entries → **zero nodes**. Parents are navigation-only.
- Child entries with non-null `core_region` → one node.
- Entries with null `core_region` (metadata-only, pure data tables) → one node with `no_representative_formula` flag. Kept for completeness but hidden by default in the DAG overview (see §9).

### 7.2 Node fields (mirrored from finalize where applicable)

```ts
export interface DagNode {
  node_id: string;               // `{sheet_slug}__{table_id}__{core_idx}`
  table_id: string;              // links back to the tableMap entry
  sheet_name: string;
  sheet_slug: string;
  bbox: Bbox;
  coordinates: string;

  // Mirrored from the tableMap entry (denormalized for self-sufficiency)
  title: string | null;
  headers: string[][];
  row_names: string[] | null;
  footnotes_notes_refs: Array<{ row: number; col: number; text: string }>;
  parent_path: string[];
  node_type: string; // "input" | "calc" | "output" | "mixed" | "data_table" | "metadata"

  // Derived at DAG build time
  referenced_cells: Array<{ sheet: string; row: number; col: number }>;
  referenced_ranges: string[];
  referenced_named_ranges: string[];

  search_text: string; // concatenated for xl_ask lexical search

  flags: string[]; // source_flags from finalize + DAG-specific flags
}
```

Field names **match the finalize output exactly** (`footnotes_notes_refs`, not `notes`). Denormalization is deliberate — Cheri and xl_ask should never have to re-join node data with the table map at query time.

### 7.3 What is an edge?

Edge B → A exists when a representative formula of A references a cell inside B's core region bbox. Granularity is **node-to-node**, not cell-to-cell. If A's formulas reference 5 cells in B, that is one edge with 5 `reference_cells` attached.

```ts
export interface DagEdge {
  edge_id: string;
  source_node_id: string;
  target_node_id: string;
  reference_type: 'direct_cell' | 'range' | 'named_range' | 'cross_sheet' | 'unresolved';
  reference_cells: Array<{ sheet: string; row: number; col: number }>;
  named_range_name?: string;
  formula_context: number[]; // columns in the target where this ref appears
  reference_count: number;
  unresolved_reason?: string; // when reference_type === 'unresolved'
}
```

### 7.4 Unresolved references

When a formula references a cell NOT inside any known core region, emit an edge with `source_node_id = "__UNRESOLVED__{reason_code}"`.

Reason codes: `indirect`, `offset`, `structured_ref`, `external_workbook`, `outside_core_regions`, `array_spill`.

Unresolved edges surface in Cheri via the warnings banner (§8), **never silently dropped**.

### 7.5 Flags on nodes

DAG build adds to the flags inherited from finalize:

- `no_representative_formula` — no formulas in the core region
- `heterogeneous_formulas_in_column` — AST grouping fell back
- `cross_workbook_ref_unresolved`
- `dynamic_reference_unresolved` — INDIRECT/OFFSET present
- `named_range_multi_region`
- `circular_reference`

Flags never block DAG build. Warnings summary aggregates them for Cheri.

---

## 8. Warnings Panel Concept (UI rule)

Cheri v1 shows **one warnings banner** per workbook, driven by `dag_summary.warnings`. Example:

> ⚠ DAG built with 3 unresolved links, 2 dynamic references, and 1 cross-workbook reference. [View details]

Clicking opens a drawer listing each warning with context:
- For unresolved edges: the target node's title + coordinates + reason code + raw formula fragment
- For low-context nodes: the node's sheet + bbox + which fields are missing
- For dynamic references: the formula string and the node it belongs to

This panel is the only place raw flag detail appears in MVP. Nodes in the tree show at most a single warning dot; users expand the drawer to see details.

---

## 9. Non-Computational Tables (display rule)

Nodes with `no_representative_formula` (metadata-only, pure data tables) are real nodes but are NOT rendered in the DAG overview inter-sheet edge visualization by default. Reason: they have no outgoing edges and would visually clutter the overview without adding signal.

They still appear in:
- The Model Tree (structural layer — always shown)
- Node counts in `dag_summary.sheets[].node_types.data_table` (as a separate category)
- xl_ask tool responses (`find_node`, `list_*`) as needed

Rule: show computational nodes in the graph overview; count data tables separately in the side panel.

---

## 10. MVP Cheri Is Not a Formula Browser

Formulas exist in `formulas.json` but are NOT rendered in the workbook overview.

Formula details appear **only** when:
- User clicks an edge in a query subgraph (Ask window or Table Detail expander) → tooltip shows the representative formula string
- User clicks "Show formula" on a node's detail panel → opens a small overlay with the formula text

No dedicated formula browser view. No AST visualization. No formula diff. MVP UI shows formulas only as edge tooltips and node detail overlays.

---

## 11. xl_ask Integration (kept short)

xl_ask consumes the DAG via a deterministic tool API. For MVP it answers two question types:

- **Type 1 (what/where):** `find_node`, `list_inputs`, `list_outputs`, `list_nodes_by_sheet`, `get_node`, `find_cell_in_node`, `node_cached_value`
- **Type 2 (how/why):** `upstream`, `downstream`, `shortest_path`, `ancestors`, `descendants`, `get_edge`, `explain_edge`, `get_formula`

Every xl_ask answer returns `AskResponse` (see `ask.schema.ts`) which includes:
- `answer_text` (markdown)
- `citations` (table_ids, coordinates, snippets)
- `subgraph` (Subgraph shape, rendered next to the answer)

Every claim in `answer_text` must cite a node_id or edge_id from the retrieved substructure. Ungrounded answers are rejected and retried by xl_ask before returning to Cheri.

Types 3 (what-if) and 4 (add/modify) are post-MVP skills. Not in this spec.

---

## 12. Persistence and Runtime

- JSON files only. No Neo4j. No graph database.
- Per workbook, files written under `{run_id}/dag_output/`:
  - `dag.json`
  - `dag_summary.json`
  - `table_map_overview.json`
  - `node_index.json`
  - `formulas.json`
  - `dag_build_errors.jsonl` (if any)
- xl_ask runtime: load `dag.json` + `node_index.json` into an in-memory `networkx.DiGraph` + dicts. Lazy-load `formulas.json` only when a Type 2 question needs it.
- Rebuild on workbook upload. Never mutate. No concurrency requirements.

Rationale: MVP graphs are 50–500 nodes. Query patterns are fixed. A graph DB adds operational cost that buys nothing.

Escape hatch: the tool API returns structured data, so migrating the backing store in v2 is a backend swap, not a rewrite.

---

## 13. DAG Build Algorithm (summary — backend-facing)

Input: `final_table_map.json` + workbook file.

1. Load named ranges from workbook.
2. For each map entry that is a leaf (standalone or child) with non-null `core_region`:
   - Build `node_id`.
   - Collect formula cells in the core region bbox.
   - Parse each formula via custom parser → AST.
   - Group ASTs by shape per column → representative formulas.
   - Extract `referenced_cells`, `referenced_ranges`, `referenced_named_ranges`.
   - Emit node.
3. Build `cell_to_node` index.
4. Walk representative formulas; resolve named ranges; emit edges (or unresolved edges).
5. Deduplicate edges per (source, target) pair; merge reference cells.
6. Build indices for `node_index.json`.
7. Build `dag_summary.json` and `table_map_overview.json` from the above.
8. Write all artifacts.

Error handling: each node isolated. Failures produce a degraded node with `parse_error` flag; never abort the build. Log to `dag_build_errors.jsonl`.

The custom formula parser is reference-extraction only. It does NOT evaluate formulas. Handles cell refs, range refs, named ranges, cross-sheet refs, function calls (by name, args unparsed-as-needed), operators, precedence. Flags INDIRECT / OFFSET / structured refs / external workbooks as unresolved.

---

## 14. Out of Scope for MVP

- Formula evaluation / recalc engine (Type 3 questions)
- Workbook mutation / VBA injection (Type 4 questions)
- Cross-workbook reference resolution
- Structured table references (`Table1[Col]`) — flag only
- Circular reference resolution (flag and keep)
- Time-indexed DAG semantics (cycle-indexed regions remain one node)
- Vector embeddings / RAG
- Neo4j or any graph database
- Full interactive DAG view (workbook overview is aggregate-only)
- Formula browser / AST visualization
- Incremental DAG rebuild

---

## 15. Success Criteria (MVP)

Claude Code's work is done when the following are true on a real HEOR workbook (e.g. sample_workbook_A):

1. `final_table_map.json` + `summary.json` exist from the upstream pipeline.
2. Running DAG build produces all artifacts in §3.
3. `table_map_overview.json` renders cleanly when Cheri loads it into the Model Tree overview.
4. `dag_summary.json` produces sensible aggregate badges (node counts, edge counts, warnings).
5. The warnings banner shows correct counts; clicking "View details" lists the expected unresolved edges / low-context nodes.
6. A sample query subgraph extraction (via `subgraph_for_query(focus_node_ids=[...], upstream_depth=2, downstream_depth=1)`) returns a valid `Subgraph` JSON matching `subgraph.schema.ts`.
7. All xl_ask tools return correctly shaped responses against the in-memory graph.
8. Tests in §16 pass.

---

## 16. Testing

- `tests/test_dag_parser.py` — custom formula parser: refs, named ranges, nested calls, unresolved detection
- `tests/test_dag_node_builder.py` — nodes built from `final_table_map.json`; representative formulas grouped correctly
- `tests/test_dag_edge_builder.py` — edges resolved, named ranges resolved, unresolved flagged
- `tests/test_dag_indices.py` — `node_index.json` lookups correct
- `tests/test_dag_summary.py` — `dag_summary.json` aggregates match the underlying graph
- `tests/test_table_map_overview.py` — `table_map_overview.json` aggregates match `final_table_map.json`
- `tests/test_dag_subgraph.py` — client-side subgraph extraction produces valid `Subgraph` JSON
- `tests/test_dag_tools.py` — every tool returns expected structure
- `tests/test_dag_pipeline.py` — end-to-end on 3 real HEOR workbooks

---

## 17. Instruction to Claude Code

**Prioritize the Cheri-facing contracts first.** Order:

1. Produce `table_map_overview.json` + `dag_summary.json` shape (UI contracts — §5).
2. Build the DAG (nodes + edges) that feeds them.
3. Build indices and formulas artifacts.
4. Wire xl_ask tools.
5. Tests + integration on real workbooks.

**Plan the implementation from this spec before writing code.** Produce a phased plan, decompose modules, name files, then implement phase by phase.

**When in doubt, match the shapes declared in `src/contracts/*.ts` exactly.** Those are authoritative. The finalize output shape (`final_table_map.json`, `summary.json`) is also authoritative — mirror its field names and conventions (e.g. `footnotes_notes_refs`, not `notes`).
