# ontology_calculations.heuristics.md — search patterns for calculations

Living document. Each heuristic is a named chunk with front-matter and
prose body. v0 ships with a small starter set; expect this file to grow.

**Status conventions:** `experimental` | `active` | `deprecated` |
`subsumed_by_ml`. Append-only.

---

```yaml
---
id: H-CA-1
applies_to: calculations
concept: aggregator
trigger: not_found_via_lexicon
priority: 1
last_updated: 2026-04-30
status: active
---
```

# H-CA-1: Aggregator detection

**Triggered when:** the `aggregator` concept has not been resolved by
the lexicon-based cross-check (it almost never is — aggregators have
no lexical signature; their cells are `=SUM(...)` not titled
"aggregator"). Fires per-concept on `aggregator`.

**Pattern:** aggregators are small tables with a distinctive signature:

- Formula density > 80%
- Bbox area < 50 cells (typically 2-5 rows × 5-10 columns)
- DAG fan-in > 3 (sums across multiple calc tables)
- Direct edge to an output-tagged node
- Formulas dominated by SUM, SUMPRODUCT, or simple addition across
  external references

**Search:** scan all tables (regardless of upstream node_type) matching
these criteria. Aggregators are often tagged `mixed` or `calc` upstream.

**Verification:** trace the formulas — they should reference cells in
multiple other calc tables, with the aggregator's output cells feeding
directly into a result.

**Recording:** classify as `calculations/aggregator`. Always include
the aggregator in the candidate set even if it is the 4th or 5th
candidate (override the 3-candidate cap for aggregators specifically).
Cite the SUM-style formulas in `key_formulas`.

---

```yaml
---
id: H-CA-2
applies_to: calculations
concept: patient_flow
trigger: not_found_via_lexicon
priority: 1
last_updated: 2026-04-30
status: active
---
```

# H-CA-2: Patient flow embedded in engine table

**Triggered when:** patient_flow concept not found as a standalone
table.

**Pattern:** in many BIMs, patient flow (treated patient count = pop ×
share × duration) is not a separate table — it's a row or block within
the main engine table.

Search for:
(a) A row labelled "treated patients", "patients on treatment",
    "patient numbers"
(b) The row's formula pattern: `population_cell *
    market_share_cell * duration_or_eligibility_cell`
(c) The row sits within a larger time-indexed table (the engine)

**Recording:** if patient_flow is embedded in the engine, do not
classify it as a separate calc table. Instead, note in the engine
table's `extracted_payload.key_formulas` that the patient_flow
calculation lives at specific cells. Set
`expected_concepts_status.patient_flow = "found"` with evidence
pointing to the engine table.

---

```yaml
---
id: H-CA-3
applies_to: calculations
concept: total_cost_with_intervention
trigger: not_found_via_lexicon
priority: 1
last_updated: 2026-04-30
status: experimental
---
```

# H-CA-3: Two-scenario calc detection

**Triggered when:** the `total_cost_with_intervention` concept has not
been resolved by the lexicon-based cross-check. The two-scenario
structure is the expected fingerprint and may identify the concept
even when no lexical hit occurs. Fires per-concept on
`total_cost_with_intervention`.

**Pattern:** every BIM should have parallel calculations for
intervention vs comparator scenarios. The agent should find this
structure in one of three forms:

(a) **Two separate tables** (most common): one labelled "with
intervention" / "scenario A" / "future market", another labelled
"without intervention" / "scenario B" / "current practice".

(b) **One table with two columns or row-groups**: a single calc
table with explicit scenario dimension.

(c) **One table for intervention, comparator implicit**: the
comparator scenario is computed implicitly (e.g., total population ×
existing-treatment-cost without any "scenario" label).

**Verification:**
- For (a): expect both labels to be findable; the headline result
  should subtract one from the other
- For (b): expect a column or row labelled with scenario lexicon
- For (c): expect comparator costs to appear in the same engine table
  as intervention costs, sharing population

**Recording:** flag `two_scenario_structure: a | b | c | not_found`.
If `not_found`, the workbook may be non-standard — flag at workbook
level.

---

## Open patterns to codify (TODO list)

- Per-component cost calculation patterns (drug, admin, AE,
  monitoring, hospitalisation each have characteristic formula
  shapes worth codifying)
- Discounting formula detection (helps locate discount_rate cell
  and verify it's applied correctly across all cost streams)
- "Year 0 vs Year 1" indexing styles (different modellers use
  different conventions; affects how time horizon and cumulative
  totals are interpreted)
- Cohort cost vs population cost (per-patient calcs scaled by
  population vs population-level totals — different DAG structures)

Add as H-CA-4 onwards.
