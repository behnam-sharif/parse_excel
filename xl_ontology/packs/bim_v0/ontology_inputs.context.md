# ontology_inputs.context.md — node-specific framing

This document is loaded when the `inputs` agent starts. It supplements
`pack_overview.context.md` with node-specific framing.

---

## What the inputs node is for

The inputs node is the **assumption layer** of the BIM. Inputs are the
parameter values that downstream calculations consume: how many
patients, how much each drug costs, how the market evolves.

`xl_ask` uses the inputs node to answer questions like:
- "What is the drug cost?"
- "What's the eligible population in year 3?"
- "What's the assumed market share for the intervention in year 5?"
- "Which inputs would I need to change to model a 10% price reduction?"

If the inputs are well-classified and well-extracted, these questions
become near-deterministic lookups.

---

## What "input" means in a BIM

An input is a parameter that:
- Is **declared**, not computed (in spirit, even if some cells use
  formulas for currency conversion or aggregation)
- **Feeds** the calculation engine but is not itself driven by it
- Is **user-editable** in the modeller's mental model — modellers think
  of inputs as the knobs you turn to run different scenarios

The structural signature is high fan-out (many downstream consumers),
low fan-in (few or no upstream producers), low formula density, often
visually highlighted (yellow/blue cells, borders, data validation).

---

## Why finalize sometimes mis-classifies inputs as calculations

Real BIMs frequently have input tables with formulas for legitimate
reasons:
- **Currency conversion**: a list price in USD with a formula converting
  to GBP using a single exchange rate cell
- **Unit aggregation**: a per-vial price with a formula computing per-
  cycle cost based on dose
- **Lookup**: a price table with a VLOOKUP pulling from a fuller list
- **Time-varying inputs**: a population that grows year over year using
  an explicit growth formula rather than hand-entered values

These tables are still **semantically inputs** — the modeller thinks of
them as parameters — but `xl_sheetagent` may tag them `calc` or `mixed`
because of the formula presence. The inputs agent must catch these.

The cross-check rule (Decision Rule B in `ontology_inputs.txt`) does
exactly this: for every expected concept, if it isn't found in the
`node_type=input` set, search across all tables. When found in a
non-input table, propose an override.

---

## Concepts that are always present in BIM inputs

From `pack_overview.context.md`: every BIM must have population, market
share, drug cost, and time horizon at minimum. These four are
non-negotiable.

If population is genuinely absent, the model cannot compute treated
patient counts. If market share is absent, the model cannot compute
displacement. If drug cost is absent, the model cannot compute cost.

So when these appear absent on first pass, the agent's prior should be
**"I have not looked hard enough"**, not "the model is broken". Apply
the heuristics in `ontology_inputs.heuristics.md` before recording
absence.

---

## Concepts that are sometimes present

These vary by therapeutic area and by modeller style:

- **Adverse event costs**: usually present in oncology BIMs (severe AEs
  drive cost), often absent in chronic disease BIMs (mild AEs aren't
  costed)
- **Hospitalisation costs**: present when the disease has acute
  exacerbations (cardiology, COPD); absent for mostly-outpatient
  conditions
- **Monitoring costs**: present when the drug requires ongoing tests
  (CBC for chemotherapy, LFTs for hepatotoxic drugs); absent for simple
  oral medications
- **Indirect costs**: rare in BIM (the perspective is payer); present
  when the model has a societal sub-perspective

Absence of these is not a defect. Record absent and move on.

---

## Concepts that hide in non-obvious places

Some concepts the agent should specifically look for outside the
"obvious" location:

- **Discount rate**: usually a single cell, not a table. May be on the
  Description sheet, the Assumptions sheet, or buried in a settings
  block. Format: percentage, value 0.00 — 0.10. Often labelled
  "discount rate", "DR", "annual discount", or just "3.5%".
- **Time horizon**: rarely an "input" in the sense of having its own
  table. Often inferable from year columns in calculation/result
  tables. The overall_model agent owns this concept; the inputs agent
  should reference it but not duplicate.
- **Currency**: same as time horizon — usually a single switch cell or
  inferable from format.
- **Population**: in some BIMs the eligible population is computed from
  more primitive inputs (general population × prevalence × eligibility
  rate). The "input" is then the primitive parameters, and the
  eligible population is technically a calc table. Both should be
  classified — the primitive parameters as inputs, the eligible
  population as a calc with high fan-out.

---

## What this agent should *not* do

- Re-derive `node_type` from scratch. Trust upstream as the starting
  point.
- Classify a calculation table as input just because it has low formula
  density. The decision rule (Section 6 of the txt file) requires
  formula density < 30% AND DAG fan-in < 3 to override.
- Extract every cell value from every input table. Extract per the
  ontology's extraction schema only.
- Try to interpret what a parameter means in clinical terms ("this
  efficacy is quite high"). Just record the value, unit, and source.
