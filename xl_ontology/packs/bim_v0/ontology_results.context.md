# ontology_results.context.md — node-specific framing

This document is loaded when the `results` agent starts. It supplements
`pack_overview.context.md` with node-specific framing.

---

## What the results node is for

The results node is the **headline** of the BIM. Result tables answer
the question the entire model exists to answer: **what is the budget
impact of reimbursing this drug?**

`xl_ask` uses the results node to answer questions like:
- "What is the budget impact in year 3?"
- "What's the cumulative 5-year cost difference?"
- "Show me the headline result."
- "How does the budget impact change between scenarios?"

If the model's purpose is to inform a payer's reimbursement decision,
the result table is the slide the payer looks at.

---

## What "result" means in a BIM

A result table:
- Is **terminal** in the DAG: nothing important downstream depends on
  it. The values flow in but nothing flows out except possibly into a
  chart.
- Has **high DAG fan-in**: multiple calculation tables feed it.
- Is **time-indexed**: Year 1 through Year N, plus often a "Total" or
  "Cumulative" column.
- Is **visually emphasised**: bold headers, larger font, fill colours,
  borders, sometimes an embedded chart nearby.
- Almost always contains an **incremental row or column**: the
  difference between intervention and comparator scenarios. This is the
  headline number.

---

## The incremental row is the agent's anchor

Every BIM result table the agent will encounter has a structure like:

| | Year 1 | Year 2 | Year 3 | Year 4 | Year 5 | Total |
|---|---|---|---|---|---|---|
| Total cost (intervention) | X | X | X | X | X | X |
| Total cost (comparator) | X | X | X | X | X | X |
| **Incremental impact** | **X** | **X** | **X** | **X** | **X** | **X** |

The incremental row (or column) is the headline. It is almost always
labelled with one of: "Difference", "Incremental", "Net impact",
"Change", "Delta", "vs comparator", "vs current practice".

If the agent finds this row pattern, it has found the headline result.
If it finds the two scenarios but no explicit incremental row, the
modeller may have left the subtraction for the reader — flag this
(`incremental_missing`) but still classify the table as a result.

---

## Multiple result tables are common

Well-built BIMs usually have several result tables:

- **Headline incremental impact** — by year + cumulative
- **Total cost summary** — side-by-side intervention vs comparator
  totals
- **Per-patient impact** — the incremental cost divided by the treated
  patient count (a sanity check on per-patient affordability)
- **Annual breakdown** — same as headline but with finer granularity
- **Scenario / sensitivity results** — best case / worst case /
  alternative assumptions

v0 classifies up to 3 result tables. The headline (incremental impact)
is always the priority.

---

## Why we look for terminal nodes specifically

The DAG terminal-check is the strongest single signal for results.
Inputs sit at the top of the DAG (high fan-out, low fan-in).
Calculations sit in the middle. Results sit at the bottom (high fan-in,
low fan-out).

When a candidate has `node_type=output` upstream **and** is terminal in
the DAG, the agent can be highly confident it's a result. When a
candidate has `node_type=output` but **fan-out > 0**, it's probably an
intermediate calculation table that finalize mis-tagged. The agent
should defer it to the calculations agent.

---

## Where results hide in poorly-built BIMs

Most BIMs have a clear results sheet ("Results", "Output", "Summary").
But some models put the headline result on a sheet finalize tagged
unusually:

- A "Dashboard" sheet that contains both inputs and results
- A "Validation" sheet that compares actual vs target
- A sheet named after the indication where everything lives

The cross-check rule (Decision Rule D in `ontology_results.txt`)
addresses this: if no `node_type=output` candidate covers
`incremental_budget_impact`, search across all terminal tables in the
workbook regardless of upstream node_type.

---

## Charts and visualisations

Result tables are often accompanied by charts (bar charts of annual
impact, line charts of cumulative impact, waterfall charts of
contribution by component). v0 does **not** extract these charts. It
only flags their presence so future versions can pick them up.

If the agent detects a chart object near a candidate result table, it
adds a flag `chart_present_nearby` and notes the chart's anchor cell.
That's it.

---

## What this agent should *not* do

- Try to interpret the magnitude of a result ("this is a high budget
  impact"). Just record the value.
- Compute the incremental impact when only the two scenarios are
  present. The agent records what's there; if the difference isn't in
  the workbook, it isn't in the workbook.
- Classify scenario-analysis tables as input even if they're labelled
  "scenario inputs". If the table contains result-style metrics (cost
  differences under alternative assumptions), it's a result, not an
  input.
- Extract chart data. Charts are out of scope for v0.
