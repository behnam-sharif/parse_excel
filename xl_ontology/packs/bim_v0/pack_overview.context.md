# pack_overview.context.md — BIM v0 framing

This document is loaded once per `xl_ontology` run and is shared across
all four ontology agents. It establishes the frame in which the agents
should interpret a workbook.

---

## What is a Budget Impact Model?

A budget impact model (BIM) estimates the financial consequence to a
**payer** of introducing a new drug into a population that already has
access to other treatments for the same indication. The payer is
typically a national health system (NHS, CMS, Medicare), a regional
health authority, or a private insurer.

BIMs are typically built by pharmaceutical companies after a drug is
approved by the regulator (FDA, EMA, MHRA, PMDA) and **before** pricing
and reimbursement negotiations. The pharma company building the BIM is
asking the payer:

> "If our drug enters the market and gradually displaces existing
> treatments for the same indication, how does your annual spend on this
> indication change over the next 3 to 5 years?"

The decision the BIM informs is whether the payer should reimburse the
new drug, and at what price.

---

## Why every BIM has the same skeleton

This decision context determines the model's structure. **Every BIM has
the same components for the same reason.** A reviewer asking "does your
model have a population component?" expects the answer to be yes — if
it's no, the model is broken.

| Component | Why it must exist |
|---|---|
| Intervention | The new drug whose budget impact is being assessed. |
| Comparators | The existing drugs the new drug will displace. Without comparators, no displacement, no budget impact. |
| Indication | The disease or condition the drug treats. Determines which comparators count. |
| Population | The eligible patient count over time. Determines the size of the market. |
| Time horizon | Usually 3 or 5 years. Long enough to capture market uptake, short enough to be relevant for budgeting. |
| Market share / uptake | How fast the new drug captures the market. Drives the year-by-year displacement curve. |
| Drug costs | Unit cost of intervention and each comparator. |
| Other costs | Administration, monitoring, AE management, hospitalisation. Sometimes negligible, sometimes dominant. |
| Total cost — intervention scenario | Sum of all costs if the new drug is reimbursed. |
| Total cost — comparator scenario | Sum of all costs if it isn't (current practice). |
| Incremental impact | The headline. Difference between the two scenarios, by year and cumulative. |

This is not a list to memorise — it is the model's logical structure. If
any of these is missing from a workbook, **either the agent has not
looked hard enough, or the workbook is incomplete.**

---

## The intervention is always the new drug

This is worth stating explicitly because it is the single most useful
heuristic in the entire pack:

> **In a BIM, the intervention is the drug whose budget impact is being
> assessed. It is almost always the new drug. It is almost always the
> drug the workbook is named after, or named most prominently on a
> cover sheet, or listed first in cost tables.**

When the agent finds multiple drug names in cost tables, the
intervention is usually the one that:
- Appears in the workbook filename or sheet names
- Has its own dedicated cost table (rather than being one row in a
  shared table)
- Has market share growing over time (comparators usually have market
  share shrinking)
- Is mentioned by brand name rather than INN or generic

---

## Time horizon is short and discrete

BIMs almost never run longer than 5 years. This is because:
- Budgets are set annually; payers care about near-term commitments.
- Uncertainty grows fast beyond 5 years.
- Market dynamics (competitors entering, patent expiry) make long
  horizons unreliable.

Typical horizons: 3 years, 5 years. Occasionally 4. Very rarely 10.

Time is **always discrete and annual** in BIMs (unlike cost-effectiveness
models, which often use monthly or quarterly cycles). Year columns may be
labelled:
- Calendar years: 2023, 2024, 2025, …
- Year offsets: Year 0, Year 1, Year 2, …
- Or "Yr 1", "Yr 2", "Y1", "Y2"

Anything that looks like a "Cycle 1", "Cycle 2" suggests the workbook may
not be a pure BIM — possibly a cost-effectiveness model masquerading as
one.

---

## Two parallel scenarios is the BIM's defining feature

Every BIM compares two worlds:

1. **Intervention scenario** (also called "with new drug", "scenario A",
   "future market", "post-launch"): a world where the new drug is
   reimbursed and uptakes over time.

2. **Comparator scenario** (also called "without new drug", "scenario B",
   "current practice", "pre-launch"): a world where it isn't, and
   patients continue receiving existing treatments.

Almost every calculation table in a BIM exists in two versions, one per
scenario. The result table compares them. **If an agent finds calculation
tables but cannot identify two parallel scenarios, the workbook may be
non-standard — flag it.**

---

## Perspective is almost always payer

In BIM, the perspective is almost always the payer's. Other perspectives
(societal, hospital, employer) are rare. If perspective is not stated
explicitly, the agent can infer it from cost categories: if costs are
drug + admin + monitoring + AE + hospitalisation, with no productivity
loss or caregiver cost, the perspective is payer.

---

## What this means for an agent reading a BIM

Three operating principles for every agent in this pack:

1. **The expected concepts are not optional.** If an expected concept
   appears absent on first pass, search harder before recording absence.
   Specifically: check inputs *and* calculations (concepts can hide in
   either), check across all sheets, check inside named ranges, and
   apply heuristics from `<node>.heuristics.md`.

2. **Always record provenance.** Every value the agent extracts must
   point back to a sheet, cell, and (if applicable) table. If the value
   was inferred rather than directly extracted, the inference path must
   be recorded. The system's auditability story depends on this.

3. **Unknown is a valid answer.** If a concept genuinely cannot be
   found, mark it absent and record what was searched. Never guess from
   prior probability ("most BIMs are about oncology" → do not write
   "oncology"). Inventing values destroys trust.

---

## What this is not

This document does not describe **all** of HEOR. It describes BIM only.
Cost-effectiveness models, Markov models, partitioned-survival models,
and discrete-event simulation models have different structures and will
get their own ontology packs (`cem_v0`, `markov_cea_v0`, etc.). The
agents in this pack should not try to handle those — they should flag
any workbook that does not look like a BIM and let `xl_ask` route it
appropriately.
