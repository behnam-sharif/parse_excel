# Setting sheet (dashboard + settings merged)

A setting sheet is where the user toggles scenarios, selects time horizons, picks discount rates, or otherwise controls how the model runs.  It is the LAST fan-out point before results — every downstream calc reads from cells on this sheet.

## Detection signals

- Sheet name matches one of the lexicon terms above.
- Cells with **data validation dropdowns**.
- Named ranges defined here (model_horizon, discount_rate,   scenario_id, etc.).
- High DAG fan-out per cell (a single setting cell is   referenced by many downstream calcs).
- Often visually styled (borders, colors, descriptive labels).

## What lives here

Scenario switches, time-horizon control, discount rate, perspective selector, country selector, sensitivity flags.  Pass-3's Setting Agent identifies these as named controls and writes prose explaining each control's role.
