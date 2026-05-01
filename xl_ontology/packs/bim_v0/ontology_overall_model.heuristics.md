# ontology_overall_model.heuristics.md — search patterns for overall_model

Living document. Each heuristic is a named chunk with front-matter and
prose body. The executor parses front-matter to decide which heuristics
to inject into LLM calls or to apply directly during search.

**Status conventions:**
- `experimental` — newly added, not yet validated on multiple workbooks
- `active` — validated, in regular use
- `deprecated` — superseded by another heuristic; do not apply
- `subsumed_by_ml` — replaced by an ML model

**Append-only rule:** never edit a heuristic body in place. Deprecate
the old one and add a new one.

---

```yaml
---
id: H-OM-1
applies_to: overall_model
concept: time_horizon
trigger: lexicon_search_failed
priority: 1
last_updated: 2026-04-30
status: active
---
```

# H-OM-1: Time horizon when no explicit statement

**Triggered when:** time_horizon concept search via lexicon returns no hit.

**Pattern:** scan column headers across all sheets for year-like labels.
Three sub-patterns to look for:

(a) **Calendar years**: "2023", "2024", "2025" (or with quotes: "FY23",
"FY24"). Time horizon = max — min + 1.

(b) **Year offsets**: "Year 0", "Year 1", "Year 2", or "Yr 1", "Yr 2",
"Y1", "Y2". Time horizon = max year offset + 1 (if Year 0 present) or
max year offset (if starting from Year 1).

(c) **Cycle labels**: "Cycle 1", "Cycle 2" — uncommon in BIM, more
common in cost-effectiveness Markov models. If found, this is a yellow
flag: workbook may not be a pure BIM.

**Verification:** confirm the same year labels appear consistently
across input, calc, and result tables. If the input table has Years 1–5
and the result table has Years 1–3, the **headline horizon is 3** (the
result table is what the user reads).

**Recording:** cite the sheet and column where the time horizon is
established. If multiple sheets have year labels, cite the result
table's year columns as primary evidence.

---

```yaml
---
id: H-OM-2
applies_to: overall_model
concept: country
trigger: lexicon_search_failed
priority: 1
last_updated: 2026-04-30
status: active
---
```

# H-OM-2: Country when no explicit country cell

**Triggered when:** country concept search via lexicon returns no hit.

**Pattern:** infer from currency symbol used in cost tables.

| Currency symbol | Inference | Confidence |
|---|---|---|
| £ | UK | high |
| $ | US, Canada, Australia, NZ — needs disambiguation | low |
| € | Eurozone — needs specific country | low |
| ¥ | Japan or China — needs disambiguation | low |
| C$ / CAD | Canada | high |
| A$ / AUD | Australia | high |
| ₹ | India | high |
| R$ | Brazil | high |

**Disambiguation when ambiguous:** search for payer names (NHS, CMS,
Medicare, ANSM, G-BA, AIFA, AEMPS, PMDA, NMPA), regulator names, or
healthcare system terms. A model with £ + NHS references is UK with
high confidence. A model with $ + Medicare references is US.

**When still ambiguous:** check the workbook filename, sheet names, or
any text cell mentioning a country. Example: a sheet titled
"UK_Inputs" or a filename "BIM_Spain_v3.xlsx".

**Recording:** mark `match_type` as `format_match` when inferring from
currency, and include the actual currency-symbol cell in evidence.
Confidence reflects the table above.

---

```yaml
---
id: H-OM-3
applies_to: overall_model
concept: intervention
trigger: lexicon_search_failed
priority: 1
last_updated: 2026-04-30
status: active
---
```

# H-OM-3: Intervention when no explicit "intervention" label

**Triggered when:** intervention concept search via lexicon returns no
hit.

**Pattern:** when no cell explicitly says "intervention: X", infer the
intervention from cost table structure. The intervention is the drug
that:

(a) Has its **own dedicated cost table or cost block**, separate from
the comparators. (Comparators are often grouped into a single
"comparator costs" table.)

(b) Has **market share growing over time** (uptake curve). Comparators
typically have shrinking market share as the new drug uptakes.

(c) Is mentioned by **brand name** rather than INN or generic name.
(Pharma BIMs almost always brand the new drug; comparators are usually
INN.)

(d) Appears in the **workbook filename** or in **sheet names**.

If multiple drugs have these characteristics, prefer the one with the
strongest uptake curve and the most prominent positioning.

**Verification:** the intervention should also appear in the
intervention scenario but not (or with zero share) in the comparator
scenario.

**Recording:** mark `match_type` as `structural_fingerprint` when
inferred from cost table structure. Cite the specific cells used for
inference (e.g., the market share row showing uptake).

---

```yaml
---
id: H-OM-4
applies_to: overall_model
concept: indication
trigger: lexicon_search_failed
priority: 2
last_updated: 2026-04-30
status: experimental
---
```

# H-OM-4: Indication from drug names and AE categories

**Triggered when:** indication concept search via lexicon returns no
hit.

**Pattern:** when no cell explicitly states the indication, infer from:

(a) **Drug names**: if the cost tables list drugs known to be used for
a single therapeutic area (e.g., chemotherapy agents → oncology, DMARDs
→ rheumatoid arthritis), the indication can be inferred. Requires a
drug-to-indication knowledge base; in v0, only the most obvious cases
are inferred.

(b) **AE categories**: AE cost tables often list AEs specific to a
therapeutic area (febrile neutropenia → oncology, infusion reactions →
biologics, gastrointestinal AEs → various).

(c) **Population descriptors**: "patients with metastatic NSCLC", "RA
patients with inadequate response", "treatment-experienced HIV".

**Recording:** indication inference is `evidence_quality: inferred`
unless the population descriptor is explicit (then `partial`).

**Caveat:** v0 does not maintain a drug-to-indication knowledge base.
This heuristic is currently `experimental` and applies only when an
explicit population descriptor is found. Full drug-name inference is
deferred to a future heuristic.

---

```yaml
---
id: H-OM-5
applies_to: overall_model
concept: perspective
trigger: lexicon_search_failed
priority: 2
last_updated: 2026-04-30
status: active
---
```

# H-OM-5: Perspective inferred from cost categories

**Triggered when:** perspective concept search via lexicon returns no
hit.

**Pattern:** in BIM the perspective is almost always payer. If
perspective is not stated explicitly, infer from cost categories:

- If costs are: drug + administration + monitoring + AE +
  hospitalisation → **payer perspective** (high confidence)
- If costs additionally include productivity loss, caregiver burden, or
  patient-borne costs → **societal or sub-perspective** (note both)
- If costs are dominated by hospital-only items (bed days, theatre
  time) → **hospital perspective** (rare)

**Recording:** mark `match_type` as `structural_fingerprint`. Cite the
cost-category labels used for inference. Confidence is high for the
payer case (which is the default for BIM) and moderate for societal or
hospital cases.

**Note:** this heuristic should run only after H-OM-1 through H-OM-3.
Perspective inference is only useful once at least the country and
intervention are known.

---

## Heuristic ordering

When multiple heuristics apply to the same concept, run them in
priority order (priority 1 first, then priority 2). The first
heuristic to produce a high-confidence value wins. Lower-priority
heuristics may still contribute corroborating evidence to the
`evidence` array.

## Logging

The executor logs `applied_heuristic: <id>` to
`ontology_run_log.jsonl` whenever a heuristic fires, even if the
heuristic ultimately fails to produce a value. This lets us audit
which heuristics are useful in practice.
