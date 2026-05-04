# xl_ontology v0.1 — Implementation Plan (patch on v0)

## Context

Phase E uncovered two systematic gaps in v0:

- **Bucket A — one concept, one table (bug).** Real BIMs have multiple tables per concept (three market-share tables, separate drug-cost tables per arm, etc.). The pack already documents in `ontology_inputs.txt:135–137` *"Rule D. DEDUPLICATION: keep both as separate candidates."* But the implementation in each agent (`inputs.py:54`, `calculations.py:55`, `results.py:53`) collapses every concept's hit list with `best = max(hits, ...)` and classifies only the single top-weighted table. Result: valid candidates fall through into `unclassified_tables`. `record_classification`/`builder.record_classification` (`builder.py:137–158`) only enforces *one table_id → one role*, which is correct and stays.
- **Bucket C — taxonomy too coarse.** Subtypes for market_share, hospitalization, adverse_event, monitoring conflate dimensions (with-/without-intervention; rate vs unit cost). Subsequent treatment is missing entirely. End-of-life / SCT / palliative items have no home.

Spec: `xl_ontology/spec/spec_xl_ontology_v0_1.md`. v0.1 is purely additive — no architectural changes, no LLM behaviour change, no new artifacts.

## Effort estimate

~5–6 hours total. **Well under the 1-day flag-for-review threshold.**

| Block | Time |
|---|---|
| Bucket A code change (3 agent files) | ~1 h |
| Bucket C pack file additions (`ontology_inputs.txt` + companions) | ~2 h |
| Tests (5 new + 1 regression assertion) | ~2 h |
| Manual verification + smoke test | ~0.5 h |

If pack-file polish balloons (e.g. extensive heuristic rewrites), stop and flag.

---

## Recommended approach

### 1. Bucket A — multi-table-per-concept (code)

Three sibling agents, identical pattern. The change is replacing a single-best-hit with an iterate-all-unique-table-hits loop, *while keeping the existing table-id uniqueness guard* (`is_classified` early-return inside `_classify`).

**Files:**
- `xl_ontology/agents/inputs.py:48–58` — `_cross_check_phase` lexicon block
- `xl_ontology/agents/calculations.py:49–60` — same pattern
- `xl_ontology/agents/results.py:48–60` — same pattern

**Change shape (per file):**

```python
# before
hits = search_workbook_lexicon(self.ctx, terms, "whole_table")
if not hits:
    continue
best = max(hits, key=lambda h: _LOCATION_WEIGHT.get(h.match_location, 0.0))
entry = self.ctx.final_map.get(best.table_id) if best.table_id else None
if entry is None:
    continue
self._classify(entry, concept, best, in_starting=entry in starting_set)

# after
hits = search_workbook_lexicon(self.ctx, terms, "whole_table")
if not hits:
    continue
# Group hits by table_id; pick the highest-weighted hit per table so we
# attach the strongest evidence to each classification.
hits_by_table: Dict[str, Hit] = {}
for h in hits:
    if not h.table_id:
        continue
    cur = hits_by_table.get(h.table_id)
    if cur is None or _LOCATION_WEIGHT.get(h.match_location, 0.0) > _LOCATION_WEIGHT.get(cur.match_location, 0.0):
        hits_by_table[h.table_id] = h
for table_id, hit in hits_by_table.items():
    entry = self.ctx.final_map.get(table_id)
    if entry is None:
        continue
    self._classify(entry, concept, hit, in_starting=entry in starting_set)
```

**Why this is safe:**
- `_classify`'s guard at `inputs.py:81` (`if self.ctx.builder.is_classified(entry.table_id): return`) keeps one-table-one-role intact. The cross-agent guard in `builder.record_classification` (`builder.py:145–147`) is unchanged.
- Override-log emission (`tools/writers.py:87–107`) emits one line per `record_classification` call. Multi-table now naturally produces multiple override-log lines for the same concept when each comes from a different upstream label — exactly what spec §3.4 specifies; **no override-log code change needed**.
- `unclassified_tables` shrinks because more tables now match. No code change needed there either.

**Note for `calculations.py` & `results.py`:** the `_LOCATION_WEIGHT` constant is defined in each agent file. Use whichever local symbol exists.

### 2. Bucket C — taxonomy expansion (pack files only)

`pack_loader.py` does no closed-list validation on subtypes (Section 3 is parsed as prose bullets, lexicon is consumed at runtime by `tools/lexicon.search_workbook_lexicon`, which is concept-agnostic). So **all of §4 is pack edits**, no code change.

**File: `xl_ontology/packs/bim_v0/ontology_inputs.txt`**

- **Section 3 (EXPECTED CONCEPTS, lines 31–47):** add the new concepts. **Order matters** — list specific subtypes before parents, since the agent's concept-loop processes them sequentially and `_classify`'s table-id guard means whichever concept fires first wins. Recommended order: children before parent for `market_share`, rate before unit_cost, then subsequent_treatment block, then `other_input` block.
  - Replace `market_share / uptake` line with three entries: `market_share_with_intervention`, `market_share_without_intervention`, `market_share` (parent, fallback)
  - Split `adverse_event_cost` → `adverse_event_rate` + `adverse_event_unit_cost`
  - Split `hospitalisation_cost` → `hospitalization_rate` + `hospitalization_unit_cost` (note: spec uses US spelling `hospitalization_*`; existing pack uses UK `hospitalisation`. Add both spellings to lexicon, but emit subtype keys per spec — US.)
  - Split `monitoring_cost` → `monitoring_rate` + `monitoring_unit_cost`
  - Add `subsequent_treatment_rate`, `subsequent_treatment_unit_cost`, `subsequent_treatment_total_cost`
  - Add `other_input` parent + children: `SCT_rate`, `SCT_cost`, `end_of_life_rate`, `end_of_life_cost`, `terminal_care_cost`, `death_cost`, `palliative_care_cost`
- **Section 5 (LEXICON, lines 76–99):** matching new entries. Per spec §4.2, the rate vs unit-cost lexicon should pull on percentage vs currency wording (e.g. `hospitalization_rate`: "admission rate, hospitalization rate, hospitalization probability"; `hospitalization_unit_cost`: "cost per admission, inpatient cost, bed-day cost, ward cost"). For `market_share_with/without_intervention`: scenario keywords ("with intervention", "intervention scenario", "novel arm" / "without intervention", "comparator scenario", "current standard").
- **Section 7 (EXTRACTION SCHEMA, ~line 169):** comment-style enumeration update so the human-readable schema lists the new subtypes. Loader doesn't validate this — purely documentation.

**File: `xl_ontology/packs/bim_v0/ontology_inputs.context.md`**
- Add a short paragraph explaining the rate/unit-cost split rationale (percentage vs currency formatting fingerprints). Match existing instructional second-person tone (file is ~135 lines; addition ~10–15 lines).
- One short paragraph explaining the `other_input` catch-all — when to use, when *not* to use (e.g., don't dump every unclassifiable table here; reserve for the explicit list).

**File: `xl_ontology/packs/bim_v0/ontology_inputs.heuristics.md`**
- Per spec §4.5: "light updates". Add a new heuristic `H-IN-5` (rate-vs-unit-cost disambiguation by cell format) following the existing YAML-front-matter + Triggered-when/Pattern/Verification/Recording template (see existing H-IN-1 for shape). **Trigger must be one already in `pack_loader.TRIGGER_REGISTRY`** — adding a new trigger requires code, which v0.1 forbids. Use an existing trigger; if none fits, demote this heuristic to a TODO bullet rather than expand scope.

**Other three pack files (`ontology_calculations.txt`, `ontology_results.txt`, `ontology_overall_model.txt`):** unchanged per spec §4.5.

### 3. Tests

**File: `xl_ontology/tests/test_phase_d.py`** (existing end-to-end runner test) — extend `tests/fixtures.py` (`build_run_layout`/`build_synthetic_run`) so synthetic fixtures cover the new shapes, then add the five test cases per spec §6. Use the existing pattern: patch `final_table_map.json` + run `run_ontology` → assert on `summary_model_v0.json` `candidate_tables` and `expected_concepts_status`.

Five new tests:
1. Three market-share tables → all three classified under the appropriate `with_intervention` / `without_intervention` / parent subtypes.
2. Separate hospitalization rate + unit-cost tables → both classified, distinct subtypes.
3. Subsequent-treatment cost table → classified.
4. End-of-life cost table → classified as `other_input` / `end_of_life_cost`.
5. Same-table-different-node regression — confirm `AlreadyClassified` still raises (one-table-one-role intact). Likely just an assertion added to the existing `test_phase_b.py::TestWriterOverrideCoupling::test_double_classification_raises`.

### 4. Migration check

- Run the existing `tests/test_phase_d.py::TestRunnerEndToEnd::test_run_writes_all_five_required_artifacts` against the existing synthetic fixtures **without** updating fixtures. Output should still validate — additive subtypes don't break existing assertions.
- If any phase-B/C/D test starts failing because it asserted on the *single* top hit, those tests need to be updated to assert on a *set* of classifications. Audit during implementation.

---

## Critical files (modify)

| Path | Block | Change |
|---|---|---|
| `xl_ontology/agents/inputs.py` | `:48–58` | Replace `max(hits)` with per-table-id iteration |
| `xl_ontology/agents/calculations.py` | `:49–60` | Same |
| `xl_ontology/agents/results.py` | `:48–60` | Same |
| `xl_ontology/packs/bim_v0/ontology_inputs.txt` | Sections 3, 5, 7 | New subtypes + lexicon |
| `xl_ontology/packs/bim_v0/ontology_inputs.context.md` | append | Rate/unit-cost + other_input framing |
| `xl_ontology/packs/bim_v0/ontology_inputs.heuristics.md` | append | Optional H-IN-5 (rate vs unit cost), only if existing trigger fits |
| `xl_ontology/tests/fixtures.py` | extend | Synthetic tables for the new test cases |
| `xl_ontology/tests/test_phase_d.py` | append | 4 new end-to-end tests |
| `xl_ontology/tests/test_phase_b.py` | line ~647 | Strengthen the double-classification regression assertion |

## Files reused (no change)

- `xl_ontology/tools/writers.py` — `record_classification` and override-log coupling stay as-is (multi-table emission is automatic).
- `xl_ontology/builder.py:137–158` — `record_classification` table-id guard stays as-is.
- `xl_ontology/pack_loader.py` — no closed-list validation on subtypes, no change.
- `xl_ontology/tools/lexicon.py` — concept-agnostic, picks up new lexicon entries automatically.
- `xl_ontology/schemas.py` — `CandidateTable.ontology_subtype` and `NodeTypeOverrideRecord.ontology_subtype` are free-form strings.

## Out of scope (explicit non-goals)

- v1 understanding layer
- Agent runner / planner / executor / validator changes
- Defect-procedure stub changes
- Upstream artifact or DAG changes
- Orchestrator UI views (existing views render free-form subtypes verbatim)
- New trigger registration in `pack_loader.TRIGGER_REGISTRY` (would push scope past v0.1)

---

## Verification

1. **Unit tests:** `py -3 -m pytest xl_ontology/tests/ -q` — all phase-A through phase-D tests green, plus 5 new v0.1 tests.
2. **Smoke:** `py -3 -m pytest tests/test_smoke.py -q` — package imports unchanged.
3. **End-to-end on synthetic BIM:** run `scripts/run_ontology.py --run-id <existing fixture run>` and inspect `data/output/<run>/ontology_output/summary_model_v0.json`:
   - `inputs.candidate_tables` includes multiple entries with the same `ontology_subtype` for concepts that have multiple tables in the fixture.
   - `expected_concepts_status` shows the new subtypes (market_share_*, *_rate, *_unit_cost, subsequent_treatment_*, other_input children) as `found` or `absent` rather than missing keys.
   - `node_type_override_log.jsonl` — one line per `node_type_override_proposed=true` classification, even when several share a concept.
4. **Orchestrator UI smoke:** open the orchestrator, run xl_ontology against the existing run, click into "All tagged tables" — new subtypes render verbatim (free-form strings, no UI code change).
5. **Migration check:** rerun against the v0 fixture used in `test_phase_d.py`; existing `summary_model_v0.json` assertions still hold (additive change).
