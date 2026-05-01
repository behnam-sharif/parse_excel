# ontology_results.heuristics.md — search patterns for results

Living document. Each heuristic is a named chunk with front-matter and
prose body. v0 ships with a small starter set; expect this file to grow.

**Status conventions:** `experimental` | `active` | `deprecated` |
`subsumed_by_ml`. Append-only.

---

```yaml
---
id: H-RE-1
applies_to: results
concept: incremental_budget_impact
trigger: lexicon_match_failed
priority: 1
last_updated: 2026-04-30
status: active
---
```

# H-RE-1: Incremental row inferred from formula pattern

**Triggered when:** no row or column matches the incremental lexicon
("difference", "incremental", "net", "delta", etc.) but the agent has
candidate result tables.

**Search scope:** the lexicon prepass that triggers this heuristic
should run with `scope="whole_table"` so non-standard labels
("Cost change", "Net spend", "Impact £M") are considered before
falling through to the formula-pattern fallback below. A
`match_location="row_label"` hit on any of these alternative phrasings
is sufficient to short-circuit the formula scan. Use the returned
`match_location` to confirm the hit is in a row label, not a stray
body cell.

**Pattern:** in some result tables the incremental row exists but is
labelled non-standardly (e.g., "Cost change", "Net spend", "Impact
£M"). Identify it by the formula structure rather than the label:

- A row whose formulas **subtract** one cell from another, where the
  two cells are in adjacent rows (the two scenarios)
- The subtraction pattern is consistent across all year columns
- The row sits at the bottom of the table (after the two scenario
  rows)

**Verification:** check the formula at the year-1 cell. It should be
of the form `=A{row1} - A{row2}` or similar, where row1 and row2 are
the two scenario rows.

**Recording:** classify the row as the incremental row even if the
label is non-standard. Cite the formula in evidence with `match_type:
structural_fingerprint`.

---

```yaml
---
id: H-RE-2
applies_to: results
concept: incremental_budget_impact
trigger: result_table_has_two_scenarios_no_difference
priority: 1
last_updated: 2026-04-30
status: active
---
```

# H-RE-2: Missing incremental row in two-scenario result

**Triggered when:** a candidate result table contains both scenarios
(intervention totals + comparator totals) but no explicit difference /
incremental row.

**Pattern:** some modellers leave the subtraction for the reader. The
agent should:

(a) Still classify the table as a result.

(b) Add flag `incremental_missing` to the table's flags array.

(c) Compute the implicit incremental from the two scenario rows
(per year and total) and record this in
`extracted_payload.result_metrics` with `is_formula: false` and a note
that the values were computed by the agent, not extracted.

(d) Mark `evidence_quality: partial` because the result is incomplete
in the workbook.

**Verification:** before computing implicit values, confirm the two
scenario rows are aligned (same year columns, same units).

**Recording:** the added incremental metric should clearly indicate
agent-computed status to avoid misleading `xl_ask`.

---

```yaml
---
id: H-RE-3
applies_to: results
concept: scenario_results
trigger: standalone_table_with_alternative_assumptions
priority: 2
last_updated: 2026-04-30
status: experimental
---
```

# H-RE-3: Scenario / sensitivity result table detection

**Triggered when:** a candidate terminal table contains result-style
metrics but the lexicon match suggests scenario or sensitivity
analysis ("best case", "worst case", "alternative", "sensitivity",
"low estimate", "high estimate").

**Pattern:** scenario result tables typically have:
(a) Multiple "scenario" columns (base case, low, high, …) instead of
    or alongside year columns
(b) A small number of rows (the headline metrics: incremental cost,
    cumulative impact)
(c) Formulas referencing different sets of input assumptions per
    scenario column

**Recording:** classify as `results/scenario_results`. Cite the
scenario column headers in evidence.

**Caveat:** if the workbook contains a sensitivity analysis sheet
with many tables, the agent should classify only the **headline
sensitivity result** (the table that summarises across all
sensitivities) rather than every per-sensitivity table. Per-sensitivity
detail tables are deferred to lazy enrichment.

---

```yaml
---
id: H-RE-4
applies_to: results
concept: per_patient_impact
trigger: lexicon_match_failed
priority: 2
last_updated: 2026-04-30
status: experimental
---
```

# H-RE-4: Per-patient impact identified by formula structure

**Triggered when:** per_patient_impact concept not found via lexicon.

**Pattern:** per-patient impact tables have formulas dividing total
cost by patient count. Look for:
(a) Currency-formatted cells with **smaller magnitudes** than the
    headline total cost (typically 4-6 orders of magnitude smaller)
(b) Formulas of the shape `=total_cost_cell / patient_count_cell`
(c) Often labelled with terms like "average cost", "cost per case",
    "unit impact"

**Recording:** classify as `results/per_patient_impact`. Cite the
division formula in evidence.

---

## Open patterns to codify (TODO list)

- Multi-year aggregation patterns: distinguishing a "5-year total"
  column from a "year 5 only" column when labels are ambiguous
- Result tables that mix BIM and CEA outputs (rare but happens):
  workbooks that compute both budget impact and ICER on the same
  result sheet
- Charts as evidence: when an embedded chart anchors near a result
  table, the chart's title often clarifies what the table represents
- Currency conversion in result tables: when results are presented
  in two currencies (e.g., GBP and EUR), determining which is primary

Add as H-RE-5 onwards.
