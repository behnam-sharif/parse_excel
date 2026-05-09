# Pass-1 detection rules — `calculations.total_cost_with_intervention`

> **Status:** F.1 stub.  Pass-1 reads `lexicon.json` for term matching during F.2.  Rules below are placeholders extracted from the legacy flat-file pack and will be expanded during F.3 (multi-tag scoring + structural fingerprints).

## Lexicon match

Terms live in `lexicon.json`.  Match case-insensitively across titles, headers, row labels, body cells, and formula text (scope = `whole_table`).  Rank hits by location weight: title (1.0) > header (0.8) > row_label (0.7) > body_cell_value (0.4) > formula_text (0.2).

## Structural fingerprints (filled in F.3)

- Upstream V6 `node_type` preference: `input` for inputs concepts; `calc` / `mixed` for calculations; `output` / `mixed` for results.
- Formula density threshold per role (inputs ≤20%, calcs ≥50%).
- DAG fan-in / fan-out fingerprints (filled in F.3).

## Backstop fan-in tagging

In F.3 we add a 'no-potential-input-unclassified' rule: tables matching this concept's structural fingerprint get tagged even when no lexicon term matched.
