# ontology_calculations.context.md — node-specific framing

This document is loaded when the `calculations` agent starts. It
supplements `pack_overview.context.md` with node-specific framing.

---

## What the calculations node is for

The calculations node is the **engine** of the BIM. It is where inputs
are combined into per-year, per-treatment, per-component values that
ultimately feed the result tables.

`xl_ask` uses the calculations node to answer questions like:
- "How is total cost computed?"
- "What drives the drug cost in year 3?"
- "Show me the calculation chain from population to total cost."
- "Where does the AE cost calculation happen?"

These are "how" and "why" questions — they require formula tracing, not
just lookup. The calculations node tells `xl_ask` where to start
tracing.

---

## What "calculation" means in a BIM

A calculation table:
- Has **high formula density** (most body cells contain formulas)
- Has **high DAG fan-in** (multiple input tables feed it)
- Has **non-zero DAG fan-out** (it feeds something downstream)
- Sits **between** inputs and results in the dependency graph
- Often has **time-indexed columns** (Year 1 through Year N)

Calculation tables are usually less visually emphasised than inputs or
results. They are the "engine room" of the model — important
operationally but not the part the user looks at.

---

## The two-scenario structure

From `pack_overview.context.md`: every BIM compares an intervention
scenario against a comparator scenario. This means almost every
calculation table exists in two parallel versions, one per scenario:

- "Drug cost — with intervention" / "Drug cost — without intervention"
- "Total cost — scenario A" / "Total cost — scenario B"
- "Patient flow — new drug" / "Patient flow — current practice"

Sometimes the two scenarios are in **separate tables** (most common,
easier to read).
Sometimes they are in **separate columns of the same table** (less
common, more compact).
Sometimes they are on **separate sheets** (uncommon, but happens with
large models).

The calculations agent should expect to find these pairs. If only one
side of a pair is present, the other side may be:
- Embedded in a different sheet
- Computed implicitly (e.g., the comparator scenario assumes 100% of
  patients on existing treatments and the same total population, so the
  comparator total cost is just the population × cost-per-patient
  without any "scenario" framing)
- Missing entirely (defective workbook — flag it)

---

## The aggregator pattern

In well-built BIMs, between the per-component calc tables and the result
tables there is usually an **aggregator** — a small table (1-5 rows)
that sums or weights the component costs into a single total per
scenario per year.

Aggregators have a distinctive signature:
- Very high formula density (often 100%)
- Small bbox (few rows, often 2-3)
- High DAG fan-in (sums across many calc tables)
- Direct edge to a result table

The agent should look for these specifically. They are essential to
"how is total cost computed?" type questions because they are the
direct precursor to the headline result.

---

## Why we limit to 1–3 main calc tables in v0

A real BIM may have 5-15 calculation tables: per-component cost calcs,
per-scenario subtotals, per-year aggregations, etc. v0 only classifies
the top 1-3 explicitly because:

- The headline questions ("how is total cost computed?") almost always
  trace back through the engine and the aggregator. These two are
  always in the top 3.
- Per-component calculations are easier to classify lazily at query
  time. If a user asks "where is AE cost calculated?", `xl_ask` calls
  `expand_node("calculations", concept="ae_cost")` and the agent
  classifies on demand.
- Eagerly classifying all calc tables would consume the LLM budget
  without proportional benefit.

This is a deliberate v0 trade-off. v1 should expand this to recognise
per-component patterns explicitly.

---

## Concepts the agent should look for

From the txt file: total_cost_with, total_cost_without, cost_component
(one per cost type), patient_flow, scenario_calc, aggregator.

Of these, the most important are:

- **The main engine** — usually one large time-indexed table that
  contains both scenarios and per-component breakdowns. May be on a
  sheet called "Engine", "Calc", "Workings", or just a sheet whose name
  matches the indication.
- **The aggregator** — usually a small table near the result, summing
  components into totals.

If the agent finds these two, the calculations node is well-served for
v0.

---

## What this agent should *not* do

- Enumerate every formula-bearing cell. The granularity is table-level.
- Try to classify "minor" calc tables (per-component breakdowns,
  intermediate calcs) eagerly. Defer them to lazy enrichment.
- Repair multi-core tables. Use the multi-core defect procedure
  (Section 10.4 of the spec) — flag and skip, or send whole table +
  formulas to LLM if it's a top candidate.
- Try to evaluate formulas. The agent doesn't compute values; it
  identifies tables and records key formulas.

---

## A note on orphan calc tables

Sometimes a workbook contains formula-heavy tables that have **zero
fan-out** — they don't feed anything. These are usually:
- Deprecated calculations the modeller forgot to delete
- Scratch work
- Validation tables (the modeller's own sanity checks)

The agent should **flag** these but **not include** them in main
candidates. They will appear in `unclassified_tables` for query-time
enrichment if a user asks about them specifically.
