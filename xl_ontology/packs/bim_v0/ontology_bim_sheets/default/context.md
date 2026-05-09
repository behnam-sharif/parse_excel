# Default sheet

A default sheet stores the factory-default values for each input parameter.  When a user modifies an input on the Input sheet and then wants to revert, they copy back from the Default sheet.  Default tables mirror the structure of Input tables 1:1 but are typically read-only and may be hidden in the workbook.

## Detection signals

- Sheet name matches one of the lexicon terms above.
- Tables structurally MIRROR tables on the Input sheet   (same headers, same row labels, similar shape).
- Cells contain VALUES (not formulas).
- DAG fan-out is typically zero or one (sometimes a   'reset to default' button copies values across).
- May be hidden by default in the workbook.

## What lives here

One default table per input concept — drug cost defaults, market share defaults, AE rate defaults, etc.  Pass-3's Default Agent runs only when this sheet exists; it writes prose like *"Default values for [drug_cost] stored in (Default!B12:F30)"*.
