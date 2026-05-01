# ontology_overall_model.context.md — node-specific framing

This document is loaded when the `overall_model` agent starts. It
supplements `pack_overview.context.md` with node-specific framing.

---

## What the overall_model node is for

The overall_model node is the **front page** of the workbook. In a real
BIM, this is what a reviewer reads first to know whether the model is
relevant to their decision.

A reviewer wants to know, in 30 seconds:
- What drug is this about?
- For what indication, in which country?
- Compared against what?
- Over what time horizon?

If the agent can populate these, `xl_ask` can answer 80% of orientation
questions without touching the rest of the workbook.

---

## Where this information lives in well-built BIMs

A well-built BIM has a dedicated description sheet — typically named
"Description", "Cover", "Front", "About", "Read Me", or "Introduction".
This sheet contains free text or labelled cells stating each of the
expected concepts.

Sometimes the description sheet contains data validation dropdowns
(country, currency, indication selectors) that drive the rest of the
model. These dropdowns are gold for the agent: they are explicit,
unambiguous, and machine-readable.

Sometimes the description sheet has a small assumptions table listing
key parameters: time horizon, discount rate, perspective, currency. This
table is also a good source for the overall_model concepts.

---

## Where this information hides in poorly-built BIMs

Many BIMs do not have a dedicated description sheet. The information is
scattered:

- **Intervention** appears only on the Inputs sheet, in cost tables,
  named most prominently or first
- **Country** is implicit only — inferable from currency symbol (£ → UK,
  $ → US/Canada/Australia, € → various Eurozone, ¥ → Japan/China) or
  from payer references (NHS, CMS) in titles
- **Time horizon** is present only as column headers (Year 1 — Year 5)
  on the calculation or result sheets
- **Indication** is never stated explicitly, only implied by drug names
  and AE categories
- **Comparators** are inferable only from cost table structure (which
  drugs have their own cost rows alongside the intervention)
- **Perspective** is never stated; inferable from cost categories
  (payer-relevant costs only)
- **Objective** is inferable only from the workbook title or filename

The agent's job is to find this information whether stated or implied,
record what was found and what was inferred, and grade the
evidence_quality accordingly.

---

## What the agent should always record

For each concept, the agent should record:

- The **value** if found (or null)
- The **evidence path**: which cell, which table, which sheet, what
  match type (lexicon, format, dropdown, inferred, cross-reference)
- The **search log** if the concept is absent: what was searched, what
  came back, so a reviewer can see the agent looked

This is not optional. The system's auditability story rests on it.

---

## A note on the summary

The summary for overall_model is the only summary in the entire
ontology pack that is LLM-generated rather than template-generated. The
reason: weaving together intervention + indication + country + horizon
into one sentence that reads naturally is something humans do well and
templates do badly.

That said, the summary must follow strict constraints (Section 11 of
`ontology_overall_model.txt`):
- Maximum 3 sentences
- Facts only, no judgements
- No claims about quality or methodology
- No duplication of fields already in `concepts`
- If evidence_quality is partial, summary must say what's missing
- If evidence_quality is absent, summary is null

The summary is not a place for prose flourish. It is a one-paragraph
abstract of a research paper.

---

## What this agent should *not* do

- Classify any tables into ontology roles. That's the job of the
  inputs, calculations, and results agents.
- Infer concepts from prior probability. ("Most BIMs are about oncology"
  is not evidence.)
- Guess country from author name, file location, or other non-workbook
  context.
- Generate prose that sounds like marketing.
