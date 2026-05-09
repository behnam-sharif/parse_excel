# Input sheet

An input sheet contains the model's user-editable parameter tables.  These are the values a reviewer or analyst would want to modify to explore alternative cases — drug costs, market share projections, population counts, AE rates, etc.

## Detection signals

- Sheet name matches one of the lexicon terms above.
- Tables on the sheet have **low formula density** (<20% of   body cells contain formulas).
- High value density (cells contain numbers / text values).
- Distinctive formatting on editable cells (blue text on   yellow fill is the canonical 'user input' convention;   cell border + bold value is another).
- Tables fan OUT to calculation tables in the DAG (high   fan-out).
- May contain data-validation dropdowns.
- Tables reference footnotes or sources for their values.

## What lives here

Input concepts (each maps to an `inputs.<concept>` ontology node): population, market share (with/without intervention), drug cost, epidemiology rates, subsequent treatment, adverse events, hospitalization, SCT, end of life, monitoring, administration, dosing, scenario controls.

Pass-2 selects the *golden* input table per concept — the one users actually edit.  Default-value copies on a separate `default` sheet are tagged but not selected as golden.
