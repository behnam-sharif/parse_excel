# Calculation sheet

A calculation sheet contains the model's engine — formula tables that consume input parameters and produce intermediate or final cost figures.  In a BIM, calculation tables almost always exist in pairs: one for the intervention scenario and one for the comparator scenario.

## Detection signals

- Sheet name matches one of the lexicon terms above.
- **High formula density** (≥50% of body cells contain   formulas).
- High DAG fan-in (multiple input tables feed each calc).
- Non-zero DAG fan-out (feeds something downstream — usually   a result sheet).
- Time-indexed columns (Year 1 .. Year 5 or 2023..2027).
- Sits between input-tagged and result-tagged tables in the   DAG.

## What lives here

Patient-flow calc, market-share allocation, drug-cost calc, subsequent-treatment cost, AE cost, hospitalization cost, monitoring cost, administration cost, total-cost-with-intervention, total-cost-without-intervention, aggregators.

Pass-3's Calculation Agent walks DAG fan-out from input goldens through these tables to identify which inputs feed which calc and what each calc produces.
