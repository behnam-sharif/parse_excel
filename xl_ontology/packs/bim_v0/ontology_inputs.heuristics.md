# ontology_inputs.heuristics.md — search patterns for inputs

Living document. Each heuristic is a named chunk with front-matter and
prose body. v0 ships with a small starter set; expect this file to grow
as more BIM workbooks are processed.

**Status conventions:** `experimental` | `active` | `deprecated` |
`subsumed_by_ml`. Append-only — never edit a heuristic body in place.

---

```yaml
---
id: H-IN-1
applies_to: inputs
concept: population
trigger: not_found_in_input_starting_set
priority: 1
last_updated: 2026-04-30
status: active
---
```

# H-IN-1: Population when not in input-tagged tables

**Triggered when:** the population concept is not found in tables tagged
`node_type=input` upstream.

**Search scope:** call `search_workbook_lexicon(terms=...,
scope="whole_table")` so the search covers row labels and body cells
in addition to titles/headers — population values often live as a row
in the engine table whose title does not mention "population". Use the
returned `match_location` to weight evidence (row_label and
body_cell_value are the high-signal locations for this concept).

**Pattern:** population is sometimes tagged `calc` upstream because it
contains a growth formula (e.g., previous year × growth rate). Search
across all tables for:

(a) A column of integers or large numbers that **grows or shrinks
monotonically** across year columns.

(b) Row labels matching: "eligible patients", "target population",
"covered lives", "indication population", "treated population".

(c) **High DAG fan-out** — the population is referenced by many
downstream tables (typically every per-cost-component calc table).

**Verification:** trace one downstream consumer and confirm the
referenced cell is the population value.

**Recording:** if found in a non-input-tagged table, set
`node_type_override_proposed: true`, classify as `inputs/population`,
mark `evidence_quality: partial` (because the override is the agent's
judgement, not finalize's).

**Note:** in some BIMs the population is computed from primitive inputs
(general population × prevalence × eligibility). When this is the case,
classify the primitive parameters as inputs and the eligible-population
table as a calc with high fan-out. Cross-reference both in evidence.

---

```yaml
---
id: H-IN-2
applies_to: inputs
concept: discount_rate
trigger: not_found_in_input_starting_set
priority: 1
last_updated: 2026-04-30
status: active
---
```

# H-IN-2: Discount rate as a single cell

**Triggered when:** discount_rate concept search returns no full-table
match (because discount rate is rarely a table — it's usually a single
cell).

**Pattern:** the discount rate is almost always:

- A **single cell** (not part of a larger table)
- **Percentage format**, value between 0.00 and 0.10 (typically 0.03
  or 0.035 in UK; 0.03 in US; 0.04 in France)
- Labelled in an adjacent cell with one of: "discount rate", "DR",
  "annual discount", "discounting", or just stating the value (e.g.,
  "3.5%")

Search locations in priority order:
(a) The Description / Cover / Assumptions sheet
(b) Cells with named ranges containing "discount" in the name
(c) Cells in rows 1-15 of any input-tagged sheet
(d) Cells referenced in time-discounting formulas downstream (look for
    formulas of the shape `(1 + DR)^year` or `1/(1+DR)^year`)

**Verification:** the cell value should be in the expected range
(0.00–0.10), percentage-formatted, with no formula (it's a hand-
entered assumption).

**Recording:** classify the **single cell** as a parameter inside an
ontology-synthesised "Scenario assumptions" entry. Cite the cell.
`format_role: input_value`.

**Caveat:** some BIMs do not discount (especially short-horizon BIMs
where discounting effect is negligible). Absence of a discount rate is
not a defect; record absent and move on.

---

```yaml
---
id: H-IN-3
applies_to: inputs
concept: market_share
trigger: not_found_in_input_starting_set
priority: 1
last_updated: 2026-04-30
status: experimental
---
```

# H-IN-3: Market share by row-sum-to-one

**Triggered when:** market_share concept not found via lexicon in input
set.

**Search scope:** the structural fingerprint (rows summing to ~1.0)
does not require a lexicon search at all — this heuristic walks every
table's body cells directly. If a lexicon prepass is used to narrow
the search space, call `search_workbook_lexicon(terms=...,
scope="whole_table")` and prefer hits with `match_location ∈
{header, row_label}` (column or row dimension labels are typically
treatment names like "Drug X" or "Standard of care").

**Pattern:** market share tables have a distinctive structural
fingerprint — rows or columns sum to ~1.0 (or ~100%) representing the
treatment mix at a point in time.

Search across all tables for:
(a) A table with **percentage-formatted cells** organised in a grid
(b) Rows (or columns) of percentages that **sum to within 1% of 1.0**
    or 100%
(c) Column or row labels matching drug names or treatment categories
(d) Time-indexed structure (years as one dimension, treatments as the
    other)

**Verification:** check at least 3 rows / columns sum correctly. A
single accidentally-summing row is not enough.

**Recording:** if found, classify as `inputs/market_share`. If found in
a non-input-tagged table, set `node_type_override_proposed: true`.
Cite the cells used for the sum verification in evidence.

---

```yaml
---
id: H-IN-4
applies_to: inputs
concept: drug_cost
trigger: weak_lexicon_match
priority: 2
last_updated: 2026-04-30
status: experimental
---
```

# H-IN-4: Drug cost disambiguation from currency-formatted cells

**Triggered when:** drug_cost lexicon match is weak (term appears but
context is ambiguous between input and calc).

**Pattern:** drug cost input tables typically have:
(a) **Currency format** on numeric cells
(b) **Drug names** in row labels or column headers
(c) **Low formula density** — cost values are usually hand-entered,
    occasionally with a simple formula for unit conversion (e.g.,
    cost per cycle = cost per vial × vials per cycle)
(d) Often a unit row/column ("per pack", "per dose", "per cycle",
    "per mg")

If a candidate matches all four, classify as input even if upstream
tagged it `calc` (formula presence is fine for inputs that compute
derived costs from primitive ones).

**Recording:** evidence should cite (a) the currency-formatted cells,
(b) the drug name labels, and (c) the unit row/column.

---

## Heuristic ordering

Run priority 1 heuristics first. If they produce no value, fall back to
priority 2. Lower-priority heuristics may still corroborate.

## Open patterns to codify (TODO list)

These are patterns observed but not yet codified. Add them as
heuristics as understanding solidifies:

- Population growth via formula: how to distinguish a population
  growing organically from one growing because of disease prevalence
  changes
- Time-varying inputs vs static inputs: when is a column-of-numbers an
  input vs a calculation
- Per-arm vs combined cost tables: when an arm-specific cost table
  should be classified as one input table or two
- Adverse event probability tables: incidence rates vs cost per event
  vs total AE cost — three different things often confused

These are placeholders. Add full heuristics with IDs H-IN-5 onwards as
the patterns are nailed down.
