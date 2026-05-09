# Control sheet

A control sheet provides the fixed naming vocabulary the rest of the workbook references.  Contains drug names, comparator labels, year labels, scenario names — strings the model uses for headers and dropdown options.

## Detection signals

- Sheet name matches one of the lexicon terms above.
- Cells contain mostly TEXT (drug names, labels), not   numbers or formulas.
- **DAG fan-in = 0** — these are leaves with no upstream.
- High fan-out — many sheets read these labels for headers.
- May carry named ranges keyed to specific cells.

## What lives here

Drug name lists, scenario name lists, year labels, comparator order, dropdown source ranges.  Pass-3 does NOT have a dedicated Control Agent today; control sheets are tagged but not interpreted.
