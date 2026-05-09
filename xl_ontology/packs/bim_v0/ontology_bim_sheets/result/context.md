# Result sheet

A result sheet contains the model's headline outputs — the numbers a reviewer would cite.  In a BIM, the headline result is the incremental budget impact (year-by-year and cumulative).

## Detection signals

- Sheet name matches one of the lexicon terms above.
- Tables have **fan-out = 0** in the DAG (terminal nodes).
- Fan-in > 0 (consume calc-sheet outputs).
- Headers contain result-flavoured words: 'budget impact',   'incremental', 'delta', 'difference', 'vs comparator'.
- Often a single column for each year + a 'Total' /   'Cumulative' row or column.
- May contain charts or formatting for presentation.

## What lives here

Three required goldens (Pass-2 restricts to these): `budget_impact_total`, `budget_impact_by_component`, `budget_impact_per_patient`.  Other result-flavoured tables (scenarios, sensitivity) are noted but not golden.
