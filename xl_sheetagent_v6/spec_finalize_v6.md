# spec_finalize_v6.md — Simple Finalize (Deterministic, No LLM)

## Status

This is the **implementation spec** for the next stage of v6. Claude Code should implement this in full. The DAG layer (v7) is specified separately in `spec_dag_v7.md` — **read it for context, but DO NOT implement DAG in this task.** DAG is the downstream consumer; finalize's output must satisfy DAG's contract (see "Contract with DAG" section below).

---

## Purpose

Finalize consumes the v6 reconciliation output (V6ResolvedTable tree) and produces a **flat final table map** per sheet: a list of entries that are either standalone tables (DAG-attachable leaves) or parent tables with children (navigation containers).

No LLM. No tool calls. Pure deterministic transformation. ~6 rules.

---

## Inputs

- `resolved_tables.json` from v6 reconciliation — list of `V6ResolvedTable` objects per sheet
- The workbook file (read-only, for `sheet_name` normalization and coordinate formatting)

---

## Outputs

- `final_table_map.json` per sheet — flat list of map entries with flags
- Existing `resolved_tables.json` remains unchanged (additive artifact)

---

## Final Table Map — Entry Types

### Standalone entry

Produced when the table has no children, OR when finalize collapses a parent+metadata+numeric-child trio into a single entry.

Required fields:

| Field | Type | Notes |
|---|---|---|
| `kind` | `"standalone"` | discriminator |
| `table_id` | string | stable, from reconciliation |
| `coordinates` | string | Excel-style range, e.g. `"Sheet1!B49:N60"` |
| `bbox` | `{r1, c1, r2, c2}` | |
| `sheet_name` | string | |
| `title` | string or null | single string, NOT a list |
| `headers` | list of lists of strings | multi-row headers; single header = list of one list |
| `row_names` | list of strings or null | |
| `core_region` | `{r1, c1, r2, c2}` or null | null only for pure data tables with no formulas |
| `node_type` | string | from reconciliation's `table_role`: `input` / `calc` / `output` / `mixed` / `data_table` |
| `parent_path` | list of strings | `[sheet_name]` for standalones |
| `notes` | list of note objects | from reconciliation |
| `source_flags` | list of strings | see "Flags" section |

### Parent entry

Required fields:

| Field | Type | Notes |
|---|---|---|
| `kind` | `"parent"` | discriminator |
| `table_id` | string | stable |
| `coordinates` | string | |
| `bbox` | `{r1, c1, r2, c2}` | |
| `sheet_name` | string | |
| `title` | string or null | overall parent title |
| `parent_path` | list of strings | `[sheet_name]` for top-level parents |
| `children` | list of child entries | see child fields below |
| `source_flags` | list of strings | |

Parent entries have NO `headers`, `row_names`, `core_region`, `node_type`, or `notes` at the parent level. These live on children only.

### Child entry (inside a parent's `children` list)

| Field | Type | Notes |
|---|---|---|
| `table_id` | string | |
| `coordinates` | string | |
| `bbox` | `{r1, c1, r2, c2}` | |
| `sheet_name` | string | |
| `title` | string or null | |
| `headers` | list of lists of strings | |
| `row_names` | list of strings or null | |
| `core_region` | `{r1, c1, r2, c2}` or null | |
| `node_type` | string | |
| `parent_path` | list of strings | `[sheet_name, parent.title]` |
| `source_flags` | list of strings | |

Children do NOT have a `notes` field in MVP. Deferred.

### Map-level container

Per sheet:

```json
{
  "sheet_name": "...",
  "finalize_version": "v6-finalize-mvp",
  "entries": [ ...standalone and parent entries in document order by r1, c1... ],
  "stats": {
    "standalone_count": N,
    "parent_count": M,
    "total_children": K,
    "flag_frequency": { "title_missing": 2, ... }
  }
}
```

---

## Finalize Rules (Deterministic)

Run the rules below on each V6ResolvedTable in document order. Emit one map entry per top-level table.

### Rule 1 — Standalone passthrough

If the V6ResolvedTable has no children (V3a said standalone, reconciliation confirmed), emit a standalone entry directly. Copy fields as-is. No merging. No inference.

### Rule 2 — 1 metadata child + 1 numeric child → collapse to standalone

If a parent has **exactly one** `child_metadata` and **exactly one** `child_numeric` as its children, collapse:

- `kind`: `"standalone"`
- `bbox`: parent.bbox (covers everything)
- `coordinates`: computed from parent.bbox
- `title`: `parent.title if parent.title else metadata_child.title`
  - If both present AND they differ → keep `parent.title`, add flag `title_differs_from_metadata_child`
- `headers`: `parent.headers if parent.headers else metadata_child.headers if metadata_child.headers else numeric_child.headers`
  - If parent.headers differ from combined metadata+numeric → keep parent.headers, add flag `header_conflict`
- `row_names`: `numeric_child.row_names`
- `core_region`: `numeric_child.core_region`
- `node_type`: `numeric_child.node_type`
- `parent_path`: `[sheet_name]`
- `notes`: `numeric_child.notes`
- `source_flags`: accumulated from checks above + `merged_from_metadata_and_numeric_child`

Metadata child is **dropped** (its content now lives in the standalone's title/headers).

### Rule 3 — Parent with multiple numeric children (metadata siblings interleaved or absent) → keep as parent + children

Children ordered vertically (by `r1`). For each child:

- **If child is `child_numeric`:**
  - Emit as child entry.
  - Apply positional metadata inheritance:
    - Look at the immediately preceding sibling in document order.
    - If preceding sibling is a `child_metadata`:
      - child.title = numeric_child.title if present else metadata_sibling.title
      - child.headers = numeric_child.headers if present else metadata_sibling.headers
      - Mark metadata sibling as "consumed" (do not emit as standalone child entry)
      - Add flag `metadata_propagated_positional` to the numeric child
    - If preceding sibling is NOT metadata (or no preceding sibling):
      - child.title = numeric_child.title (may be null)
      - child.headers = numeric_child.headers if present else parent.headers (v5 inheritance)
  - row_names = numeric_child.row_names (no propagation from metadata, ever)
  - core_region, node_type from numeric_child
  - parent_path = `[sheet_name, parent.title]`

- **If child is `child_metadata` and NOT consumed by a following numeric sibling:**
  - Emit as child entry with whatever title/headers/row_names it has.
  - `core_region`: null
  - `node_type`: "metadata"
  - Add flag `orphan_metadata`

Parent entry fields:
- `kind`: `"parent"`
- `title`: `parent.title`
- `coordinates`, `bbox`, `sheet_name`, `parent_path` as expected
- `children`: the emitted child entries in document order

### Rule 4 — Parent with only metadata children (no numeric children)

Emit parent entry with all metadata children as child entries. Add flag `all_metadata_no_numeric` at the parent level.

Each child entry:
- `core_region`: null
- `node_type`: "metadata"
- Flag: `all_metadata_no_numeric` passed through

### Rule 5 — Nested parents (parent of parents) → flatten one level

If a child is itself a parent, flatten: treat its children as direct children of the top-level parent. Preserve their titles and headers. Do NOT lose structural information.

Flag on the top-level parent: `nested_parent_flattened`.

Only one level of flattening. If there's a parent-of-parent-of-parent, flag it as `deep_nesting_unflattened` and emit the intermediate parent as a child entry with its own children nested (one level of nesting allowed in emission).

### Rule 6 — Flag violations (no resolution in MVP)

After Rules 1–5, scan every emitted entry and add flags for these conditions:

| Flag | Condition |
|---|---|
| `title_missing` | standalone or leaf child has null title |
| `header_missing` | standalone or leaf child has null/empty headers |
| `rowname_missing` | standalone or numeric child has null/empty row_names |
| `title_looks_like_footnote` | detected title starts with known note keywords (`source`, `note`, `*`, `†`, `ref`) OR title length > configurable char limit (default 200) |
| `multiple_header_candidates` | reconciliation produced 2+ header rows that don't form a coherent multi-row header (column span mismatch, non-contiguous) |
| `low_context_node` | leaf has no title AND no headers AND no row_names (all three missing) |
| `no_core_region` | leaf expected to be numeric but core_region is null |

Flags are additive. An entry can have multiple flags. Flags never cause finalize to fail — they surface downstream for LLM resolution or consumer warnings.

---

## Coordinates Format

Excel-style range: `"{sheet_name}!{r1_letter}{c1_num}:{r2_letter}{c2_num}"`

Where:
- Row numbers are 1-indexed (internal bbox uses 0-indexed; convert on emit)
- Column letters are uppercase Excel columns (A, B, ..., Z, AA, AB, ...)
- Sheet names with spaces or special characters are quoted with single quotes: `"'My Sheet'!A1:B5"`

Helper function needed: `bbox_to_coords(sheet_name, bbox) -> str`.

---

## Flags Summary

Full flag list (for reference — not exhaustive, may grow):

| Flag | Meaning | Emitted by |
|---|---|---|
| `merged_from_metadata_and_numeric_child` | Rule 2 collapse happened | Rule 2 |
| `title_differs_from_metadata_child` | Parent and metadata child had different titles | Rule 2 |
| `header_conflict` | Parent and metadata/numeric child headers disagreed | Rule 2 |
| `metadata_propagated_positional` | Numeric child got title/headers from preceding metadata sibling | Rule 3 |
| `orphan_metadata` | Metadata child with no following numeric sibling | Rule 3 |
| `all_metadata_no_numeric` | Parent has zero numeric children | Rule 4 |
| `nested_parent_flattened` | One level of nesting flattened | Rule 5 |
| `deep_nesting_unflattened` | More than one level of nesting present | Rule 5 |
| `title_missing` | Entry has null title | Rule 6 |
| `header_missing` | Entry has null/empty headers | Rule 6 |
| `rowname_missing` | Entry has null/empty row_names | Rule 6 |
| `title_looks_like_footnote` | Title matches note keyword or is too long | Rule 6 |
| `multiple_header_candidates` | Header disambiguation failed | Rule 6 |
| `low_context_node` | All three of title, headers, row_names are missing | Rule 6 |
| `no_core_region` | Leaf expected to be numeric but core_region is null | Rule 6 |

---

## Contract with DAG (v7)

**Claude Code: this is what DAG expects. Finalize must satisfy these conditions.**

1. Every leaf entry (standalone OR child with `core_region` non-null) is a future DAG node. DAG builds `node_id = {sheet_slug}__{table_id}__{core_idx}`, so `table_id` must be stable.
2. DAG node metadata comes entirely from finalize fields. DAG does not re-run heuristics.
3. `parent_path` drives search and explain-edge output. Must be correctly reconstructed per Rules 2–5.
4. Flags pass through to DAG as signals (e.g., `low_context_node` in finalize → same flag on DAG node).
5. Parents contribute ZERO DAG nodes. Finalize must never emit `core_region` at the parent level.
6. `sheet_name` must be the actual workbook sheet name (exact match), not a slug or normalized form.
7. `coordinates` format above is what DAG emits in explain-edge output. Match it.

---

## Config Additions

New fields in `XlSheetAgentV6Config` (and `config/stages.yaml`):

```yaml
# Finalize stage
enable_finalize: true
finalize_title_max_chars: 200              # title above this → title_looks_like_footnote
finalize_note_keywords: ["source", "note", "*", "†", "ref", "footnote"]
finalize_flatten_depth: 1                  # only flatten 1 level of parent-of-parent
finalize_emit_stats: true
```

Deliberately minimal — no LLM config in this spec. LLM finalize is a separate future spec.

---

## Schema Additions

New dataclasses in `xl_sheetagent_v6/schemas.py`:

- `FinalMapEntryStandalone`
- `FinalMapEntryParent`
- `FinalMapChildEntry`
- `FinalTableMap` — per-sheet container with `entries`, `stats`
- Update `V6RunResult` to include `final_map: Optional[FinalTableMap]`

---

## Files

### New files

- `xl_sheetagent_v6/finalize.py` — implements Rules 1–6
- `xl_sheetagent_v6/coordinates.py` — `bbox_to_coords` helper
- `xl_sheetagent_v6/final_map_exporter.py` — serialization to JSON

### Updated files

- `xl_sheetagent_v6/schemas.py` — new dataclasses
- `xl_sheetagent_v6/config.py` — new config fields
- `xl_sheetagent_v6/runner.py` — call finalize after reconciliation; write `final_table_map.json`
- `config/stages.yaml` — new keys under `xl_sheetagent_v6:`
- `core/paths.py` — path helper for `final_table_map.json`
- Tests for Rules 1–6, coordinate formatting, flag emission, schema round-trips

---

## Orchestrator Integration

Add a radio button toggle to the orchestrator's table view:

1. **Reconcile** (current behavior, unchanged) — reads `resolved_tables.json`
2. **Finalize** (new) — reads `final_table_map.json`. Renders:
   - Standalone entries as single panels with all fields
   - Parent entries as a header panel (title, coordinates) with children rendered indented below
   - Flags shown as colored badges (yellow for soft flags, orange for structural flags, red for `low_context_node`)

Switching views does NOT trigger re-computation. If `final_table_map.json` is missing for a run, the Finalize radio is disabled with tooltip "Finalize not available for this run."

Files to update:
- `apps/orchestrator/v6_viewer.py` — add finalize panel
- `apps/orchestrator/app.py` — route radio button state

---

## Testing

### Unit tests

- `tests/test_v6_finalize_rules.py`:
  - Rule 1: standalone passthrough preserves all fields
  - Rule 2: collapse 1-meta + 1-numeric into standalone, with and without title conflict
  - Rule 3: positional metadata propagation to numeric siblings
  - Rule 3: orphan metadata (no following numeric sibling)
  - Rule 4: all-metadata parent
  - Rule 5: nested parent flattening (one level)
  - Rule 5: deep nesting flagged

- `tests/test_v6_finalize_flags.py`:
  - `title_missing`, `header_missing`, `rowname_missing` correctly emitted
  - `title_looks_like_footnote` with note keyword and with long title
  - `multiple_header_candidates` when reconcile emitted incoherent multi-row headers
  - `low_context_node` when all three are absent

- `tests/test_v6_finalize_coordinates.py`:
  - `bbox_to_coords` handles A–Z, AA–AZ, sheet names with spaces
  - 1-indexed vs 0-indexed conversion

- `tests/test_v6_finalize_schema.py`:
  - Round-trip: V6RunResult → JSON → V6RunResult

### Integration tests

- `tests/test_v6_finalize_pipeline.py`:
  - End-to-end on simple single-table sheet → standalone entry
  - End-to-end on parent with 1-meta + 1-numeric → collapsed standalone
  - End-to-end on parent with 3 numeric children + 2 interleaved metadata siblings → parent with 3 child entries, metadata consumed
  - End-to-end on nested parent → flattened with flag

### Smoke test additions

- `tests/test_smoke.py`:
  - Finalize imports
  - Config validates
  - `final_table_map.json` written to expected path

---

## What Stays the Same (DO NOT Redesign)

- V3a upstream — unchanged
- v6 per-bbox algorithm — unchanged
- v6 reconciliation — unchanged
- `resolved_tables.json` — unchanged, still emitted
- v5 — frozen
- Header / title / rowname / notes detection rules — unchanged

Finalize is **purely additive**. It reads reconciliation's output, writes a new artifact, does not modify existing behavior.

---

## Out of Scope for This Task

- LLM finalize (sheet-level tool-calling LLM) — future spec
- DAG construction — see `spec_dag_v7.md`, NOT for this task
- xl_ask — future
- Child notes field — deferred
- Value resolution — post-MVP

---

## Phasing (Claude Code's Call)

Suggested but not mandatory:

- **Phase P0:** schemas, config, coordinates helper, Rule 1 (standalone passthrough), exporter. Skeleton end-to-end.
- **Phase P1:** Rules 2–5 (merging and parent/child logic).
- **Phase P2:** Rule 6 (all flags), stats aggregation, test coverage.
- **Phase P3:** Orchestrator radio button + finalize view.

Claude Code decides the actual breakdown.

---

## Verification

1. `py -3 -m pytest tests/test_v6_finalize_rules.py tests/test_v6_finalize_flags.py tests/test_v6_finalize_coordinates.py`
2. `py -3 -m pytest tests/test_v6_finalize_pipeline.py`
3. `py -3 scripts/run_sheetagent.py --v6 --run-id test` on real HEOR workbook → `final_table_map.json` produced
4. Orchestrator → radio button toggles between Reconcile and Finalize views without error
5. Contract check: every leaf entry in `final_table_map.json` has `table_id`, `sheet_name`, `bbox`, `core_region` (or null with flag), `node_type`, `parent_path`