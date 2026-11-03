# xl_user_feedback — design plan

> **Status:** Future work. Do not implement until `xl_ask v0.1` ships and ontology is stable.
>
> **Position in pipeline:** runs AFTER `xl_ontology` (and optionally `xl_qc_repair`) and BEFORE `xl_ask`. Captures human corrections to the auto-derived BIM ontology and re-emits a user-validated version of the model.
>
> **One-line summary:** Pre-Ask validation layer where the user reviews the ontology summary and corrects mistagged / missing tables; the repo LLM-parses the prose into structured patches and applies them with full audit.

---

## Context

By the time `xl_ontology` finishes, the workbook has been auto-tagged: every leaf in `final_table_map.json` has an ontology role / subtype, the `summary_model.json` enumerates inputs / calc / results / overall, and `model_understanding.md` carries a 2-3 paragraph narrative per stage. Quality is high but not perfect — the LLM mistags some tables, misses others, and occasionally invents values from cover-sheet noise.

`xl_ask` (the planned BIM Q&A repo) will retrieve answers from these artifacts. If the ontology has errors, the Ask LLM hallucinates with confidence. **Pre-validation > post-correction in every retrieval system.** That's what `xl_user_feedback` exists for.

The user opens the orchestrator's ontology viewer, reviews the auto-derived summary, sees errors, and types corrections like:

> *"The table on sheet `BI calculation_Alberta` at `E2:R5` is missing from inputs — it's a market_share table."*
>
> *"You tagged `94c8c58d611b9e70_t103_child3` as `total_cost_with` but it's actually a comparator-arm sub-calculation."*
>
> *"The intervention is Inotuzumab Ozogamicin per Description!B5, not 'Drug X'."*
>
> *"You missed the sheet 'Sensitivity Analysis' — it has 3 input tables for budget perturbation scenarios."*

The repo LLM-parses each piece of feedback into a structured patch, asks clarifying questions when the input is ambiguous, applies accepted patches to a deep-copied `summary_model.json`, and writes a versioned user-corrected artifact alongside the auto-derived original.

---

## Goals

1. **Capture free-form user prose** corrections without forcing the user to learn the schema.
2. **Resolve ambiguity** by asking the user clarifying questions instead of guessing.
3. **Apply corrections deterministically** with full audit trail. The original auto-derived map is preserved.
4. **Surface user provenance to downstream consumers** so `xl_ask` can weight user-validated tables higher.
5. **No silent acceptance** — vague feedback ("this is wrong") triggers a clarification, not a guess.
6. **Versioned outputs** — every round of corrections produces `summary_model_v{N+1}_user_corrected.json`. Original `v0` is never overwritten.

## Non-goals (v0)

- Multi-user merging / conflict resolution — single user per run.
- Persistent learning across runs — corrections live with the run, not in the pack lexicons.
- Automatic re-running of QC repair / Pass 2 against the corrected map. (Possible v1 enhancement: trigger a re-narrate of `model_understanding.md` after corrections.)
- Real-time collaborative editing.
- Feedback on QC repair verdicts (separate stage if needed).

---

## Architecture

```
xl_init → xl_parse → xl_table → xl_sheetagent → xl_dag → xl_ontology
                                                            ↓
                                              xl_qc_repair  (already exists)
                                                            ↓
                                              xl_user_feedback  ← NEW
                                                            ↓
                                              xl_ask  (future)
```

User feedback runs AFTER ontology + QC repair (so the user reviews a cleaned-up ontology) and BEFORE Ask (so Ask sees the corrected map).

### Module layout

```
xl_user_feedback/
  __init__.py              # exports run_user_feedback(...)
  config.py                # XlUserFeedbackConfig — budget, max_clarification_rounds, ...
  schemas.py               # UserFeedbackSession, ParsedCorrection, FeedbackVerdict,
                           # ClarificationQuestion, UserCorrectionRecord
  parser.py                # LLM call: free-form text → List[ParsedCorrection]
  resolver.py              # Resolve "sheet X range Y" → table_id; flag ambiguity
  questions.py             # Generate ClarificationQuestion when ambiguity detected
  applier.py               # Apply ParsedCorrection to summary_model deep-copy + audit
  validators.py            # Run Pass-1 deterministic checks on user-added tables
                           # (anchor lexicon, formula density, table_has_value_cells)
  llm_review.py            # Tolerant JSON parsing + server-side coercion (mirrors QC pattern)
  runner.py                # run_user_feedback orchestrator
  prompts/
    parse_correction.txt   # User prose → structured corrections
    clarify.txt            # Generate follow-up questions
    validate_addition.txt  # Validate user-added table against the ontology rules

apps/orchestrator/
  user_feedback_viewer.py  # Streamlit panel — chat-style input + diff preview
                           # Loaded via importlib.util like the other viewers.
```

### Data flow per session

```
1. User clicks "▶ User feedback on ontology" in orchestrator
2. Panel renders model_understanding.md + tagged tables (read-only review)
3. User types free-form correction text in a text area
4. Submit → run_user_feedback(run_id, output_base, user_input=text, session_id=...)

5. parser.py:parse_user_feedback(text, ctx) — 1 LLM call
   Returns: List[ParsedCorrection], each tagged with kind + payload + confidence

6. resolver.py — for each parsed correction:
   - If correction names a sheet+range, try to resolve → table_id via
     final_map.entries_for_sheet + bbox match
   - If no exact match: search for closest by Excel-range overlap
   - If multiple candidates (overlap or fuzzy match): generate ClarificationQuestion
   - If candidate doesn't exist at all: generate "table-not-found" clarification

7. validators.py — for add_*_table corrections:
   - Run anchor lexicon check on the proposed concept
   - Run table_has_value_cells gate
   - Run formula density check (must match the proposed role: input/calc/result)
   - On validation failure: emit a warning ClarificationQuestion ("the table you
     specified has formula_density=0.85 — this looks like a calculation, not an
     input. Confirm or revise.")

8. If clarifications exist:
   - Render to user, await response
   - Loop to step 5 with the additional context (cap at max_clarification_rounds=3)

9. If all clarifications resolved (or cap hit):
   - applier.py applies each accepted correction:
     * add_input_table → append CandidateTable to summary.inputs.candidate_tables
     * add_calc_table  → append to summary.calculations.candidate_tables
     * add_result_table → append to summary.results.candidate_tables
     * retag_table     → mutate existing CandidateTable.ontology_role/subtype
     * remove_table    → drop a candidate (mistagged)
     * correct_overall_concept → patch pre_overall.concepts.<concept> + cite source
     * add_concept_alias → extend session-scoped lexicon (NOT pack-level)
     * flag_uncertainty → annotate without changing data

10. Write artifacts to data/output/<run_id>/user_feedback_output/:
    - summary_model_v{N+1}_user_corrected.json  (versioned mutation)
    - user_corrections.jsonl                    (audit, append-only)
    - user_feedback_trace.jsonl                 (LLM events: parse, clarify, apply)
    - user_feedback_session_<id>.json           (session state for UI persistence)

11. Optionally trigger re-narration:
    - When config.regenerate_model_understanding is true, re-run Pass-2 OverallAgent
      against the corrected map → new model_understanding.md
    - Default: false (manual re-run; user can request via a sidebar button)
```

---

## Schema design

### `ParsedCorrection`

The structured output of `parser.py`. One per discrete correction in the user's prose.

```python
class ParsedCorrection(BaseModel):
    kind: Literal[
        "add_input_table",
        "add_calc_table",
        "add_result_table",
        "retag_table",
        "remove_table",
        "correct_overall_concept",
        "add_concept_alias",
        "flag_uncertainty",
    ]
    # Identifies WHICH table the correction targets
    target_table_id: Optional[str] = None      # set when LLM identified a known table_id
    target_sheet: Optional[str] = None         # set when correction names a sheet
    target_coordinates: Optional[str] = None   # set when correction gives Excel range (E2:R5)
    target_concept: Optional[str] = None       # for correct_overall_concept

    # The proposed change
    new_role: Optional[str] = None             # add_*_table / retag_table
    new_subtype: Optional[str] = None
    new_value: Optional[str] = None            # correct_overall_concept value
    new_source_cell: Optional[str] = None      # correct_overall_concept citation
    alias_term: Optional[str] = None           # add_concept_alias
    alias_concept: Optional[str] = None

    # Provenance
    user_quote: str = ""                       # exact text the user typed
    rationale: str = ""                        # LLM's reading of the user's intent
    confidence: float = 0.0                    # LLM's confidence in this parse

    # Resolution status (filled by resolver)
    resolved: bool = False
    resolution_error: Optional[str] = None     # "table_not_found", "ambiguous", etc.
    candidate_table_ids: List[str] = []        # populated when ambiguous
```

### `ClarificationQuestion`

```python
class ClarificationQuestion(BaseModel):
    question: str
    correction_id: str                         # which ParsedCorrection this clarifies
    candidates: List[Dict[str, Any]]           # MCQ options, when applicable
    free_form_ok: bool = False                 # accept free-form answer too
    asked_at: str                              # ISO timestamp
```

### `UserCorrectionRecord`

One line of `user_corrections.jsonl`. Audit-grade.

```python
class UserCorrectionRecord(BaseModel):
    ts: str
    session_id: str
    correction_id: str
    user_input: str                            # raw user text
    parsed: ParsedCorrection
    resolved_table_id: Optional[str]
    clarifications_asked: List[ClarificationQuestion] = []
    clarifications_answered: List[Dict[str, str]] = []   # question_id → user answer
    applied: bool                              # True iff applier mutated the map
    rejection_reason: Optional[str] = None     # when applied=False
    summary_model_version_before: int
    summary_model_version_after: int
```

### `CandidateTable.evidence_quality` extension

Add a new literal to the existing `EvidenceQuality` enum in `xl_ontology/schemas.py`:

```python
EvidenceQuality = Literal[
    "high",
    "partial",
    "inferred",
    "absent",
    "user_provided",   # NEW — table added or re-tagged via xl_user_feedback
]
```

Downstream (`xl_ask`) can weight `user_provided` rows higher in retrieval ranking. The orchestrator viewer can render them with a 👤 badge so reviewers see at a glance which tables came from user feedback.

---

## Correction taxonomy detail

### Mutations

| Kind | Effect | Example user input |
|---|---|---|
| `add_input_table` | Append `CandidateTable` to `summary.inputs.candidate_tables` with `ontology_role="input"`, `evidence_quality="user_provided"`. Flip `expected_concepts_status[<concept>]` to `"found"`. | *"BI calculation_Alberta!E2:R5 is a market_share table; you missed it."* |
| `add_calc_table` | Same for calculations. | *"There's an aggregator at Calculations!B25:F45 — sum of cost components."* |
| `add_result_table` | Same for results. | *"Results!H59:L64 is the per-patient breakdown — add it."* |
| `retag_table` | Mutate existing candidate's `ontology_role` and/or `ontology_subtype`. | *"94c8c58d611b9e70_t103_child3 is total_cost_without, not total_cost_with."* |
| `remove_table` | Drop a candidate from the relevant list. Re-flip `expected_concepts_status` if no other candidate remains for that concept. | *"a9f916bc0a3ee753_t19_b5 was wrongly flagged as input — it's a header, not a data table."* |
| `correct_overall_concept` | Patch `summary.pre_overall.concepts.<concept>` + record `source_cell`. | *"The intervention is Inotuzumab, not Drug X (per Description!B5)."* |

### Annotations (no map mutation)

| Kind | Effect | Example user input |
|---|---|---|
| `add_concept_alias` | Extend a SESSION-scoped lexicon (not pack files). Future user-feedback rounds in the same session use the alias when parsing. | *"For this workbook, 'BNP' means 'comparator name', so any cell mentioning BNP is the comparator."* |
| `flag_uncertainty` | Stamp the table with `user_uncertainty: <text>` in its CandidateTable.flags. Advisory only. | *"I'm not sure if Inputs!B12:F30 is drug_cost or treatment_cost — leave it for now but flag it."* |

The session-scoped alias mechanism deliberately doesn't write back to pack files. Lexicon evolution at the pack level is a separate change-management concern.

---

## Clarification question generation

When the resolver flags ambiguity, `questions.py` emits an LLM-generated clarification. Patterns:

| User said | Resolver output | Clarification |
|---|---|---|
| "sheet A" with multiple sheets starting with "A" | `target_sheet=None, candidates=["BI calculation_Alberta", "Adverse events", "Annual incremental cost"]` | "Did you mean 'BI calculation_Alberta', 'Adverse events', or 'Annual incremental cost'?" (MCQ) |
| "E2:R5" with no V6 table at exactly that range | `target_table_id=None, error="no_table_at_range"` plus closest candidates | "There's no table at exactly E2:R5. The closest are BI calculation_Alberta!E2:R12 (table_id=...) and BI calculation_BC!E5:R20 (table_id=...). Did you mean one of these, or is this a missed table?" |
| "market share table" but Pass 1 already tagged a market_share | `existing_candidate_for_concept=<id>` | "Pass 1 already tagged BI calculation_Alberta!B5:G20 as market_share. Are you proposing a different table, retagging that one, or removing it?" |
| "this is wrong" without specifics | `target_table_id=None, target_sheet=None` | "Can you point to a specific table_id or cell range? Or tell me which concept is wrong (intervention, comparator, country, etc.)." |
| Validation fails: user added a high-formula-density table as input | `validator_warning="formula_density=0.85 looks like calc"` | "The table you specified has formula_density=0.85 — this looks like a calculation rather than an input. Confirm input, or change to calc/result?" |

**Cap on clarification rounds:** `max_clarification_rounds=3` (config). After the cap, accepted corrections apply; unresolved ones become `flag_uncertainty` records.

---

## How `xl_ask` uses the output

When `xl_ask` lands, its retrieval consults — in priority order:

1. `summary_model_v{latest}_user_corrected.json` if it exists (user-validated)
2. `summary_model_v{N}.json` (auto-derived, latest version)
3. `final_table_map_repaired.json` (QC-repaired)
4. `final_table_map.json` (V6 raw)

For each candidate retrieval target, `xl_ask` reads `evidence_quality`:
- `"user_provided"` → boost in retrieval ranking
- `"high"` → standard auto-derived
- `"partial"` / `"inferred"` → de-prioritise

The Ask LLM also sees the user's `rationale` strings from `user_corrections.jsonl` so it can quote the user's own framing when answering. Example: user wrote *"market_share is in BI calculation_Alberta because each province sheet rolls up to a national share"* — that exact phrasing surfaces when Ask answers a question about market_share.

---

## Risks + mitigations

| Risk | Mitigation |
|---|---|
| User says "X is wrong" without a fix → LLM hallucinates one | Parser MUST require concrete additions/removals/retags. Vague input → `flag_uncertainty` advisory only, NEVER a silent map mutation. |
| User provides a sheet name / range that doesn't exist | Resolver checks against `final_map` + `workbook.sheet_names()` first. Mismatch → clarification. Never guess. |
| User feedback contradicts auto-derived audit trail | Original `summary_model_v0.json` is preserved. Corrections write to `_user_corrected.json` versioned files. Admin can revert by reading from a lower version. |
| Ask repo confused by user-corrected vs auto-tagged tables | New `EvidenceQuality.user_provided` literal + `correction_provenance` field on `CandidateTable`. Ask weights user-validated rows higher. Orchestrator viewer renders 👤 badge. |
| User introduces an inconsistency (e.g., adds a market_share concept on a sheet with no value cells) | `validators.py` runs the same Pass-1 deterministic checks. Failures surface as clarification warnings BEFORE the applier writes. User decides to override or revise. |
| LLM cost balloons with iterative clarification | Hard cap on clarification rounds (default 3). Per-session LLM budget. After cap, accept what's clear, flag rest as uncertainty. |
| Two users giving conflicting feedback | Out of scope for v0. Per-run versioning preserves history; multi-user merge is a v1 concern. |
| Feedback over-fits the ontology to one user's mental model | Audit log is append-only. Original auto-derived map preserved. Admin can revert. The trace is reviewable. |
| Concept alias leaks into pack files / future runs | Aliases are SESSION-SCOPED. They affect only the parser's lookup table for the current session. Pack lexicon files are immutable from feedback. |
| User's correction triggers a chain of dependent updates that the applier doesn't compute | v0 stays single-shot per correction. Cross-table dependencies (e.g., adding a market_share concept implies updating expected_concepts_status) are computed by the applier. Multi-step inferences are out of scope. |

---

## Effort estimate

| Phase | Scope | Time |
|---|---|---|
| F.A | Schema + config + path helpers | 0.25 d |
| F.B | LLM parser (`parser.py` + `parse_correction.txt` prompt) | 0.5 d |
| F.C | Resolver (sheet+range → table_id, ambiguity detection) | 0.5 d |
| F.D | Applier (mutate summary_model deep-copy with audit) | 0.5 d |
| F.E | Validators (Pass-1 deterministic checks on user additions) | 0.5 d |
| F.F | Clarification question generation (`questions.py`) | 0.5 d |
| F.G | Runner orchestration (parse → resolve → ask → apply → write) | 0.25 d |
| F.H | Orchestrator UI panel (review + chat input + diff preview) | 0.75 d |
| F.I | Tests (parser round-trips, resolver edge cases, applier mutation correctness) | 0.5 d |
| F.J | Real-LLM validation against `streamlit_run_enco` with curated prose corrections | 0.5 d |
| **Total** | | **~5 days** |

Phases F.A through F.D are the minimum viable surface. F.E (validators), F.F (clarifications), and F.H (UI) are the polish layers that make it usable for non-technical users.

---

## Implementation order recommendation

When the time comes:

1. **F.A → F.D (core)** — build the data path: schemas + parser + resolver + applier. CLI-only (`scripts/run_user_feedback.py --run-id X --input "user_text"`). No UI yet.
2. **F.I (tests)** before clarifications — locks in the parser/resolver/applier round-trip.
3. **F.E (validators)** — adds the safety net so user additions don't silently corrupt the map.
4. **F.F (clarifications)** — promotes the parser from "best-effort" to "ask when uncertain."
5. **F.G (runner)** — ties it all together.
6. **F.H (orchestrator UI)** — the user-facing surface. By now everything works headless; UI just renders + collects input.
7. **F.J (real-LLM validation)** — gate before declaring v0 done.

---

## Configuration

`config/stages.yaml` will gain a top-level `xl_user_feedback:` section:

```yaml
xl_user_feedback:
  use_llm: true
  llm_model: "gpt-4o-mini"
  llm_max_tokens: 4096
  llm_temperature: 0.0
  llm_budget_max: 30                          # per-session cap
  max_clarification_rounds: 3
  regenerate_model_understanding: false       # opt-in re-narrate after corrections
  validators_enabled: true                    # run Pass-1 deterministic checks on user additions
  validators_strict: false                    # when true, validation failure blocks; when false, warns only
```

`XlUserFeedbackConfig` dataclass mirrors these with strict-key matching like the other stage configs.

---

## CLI surface

```bash
# Apply a single correction from CLI
py -3 scripts/run_user_feedback.py --run-id myrun --input \
  "BI calculation_Alberta!E2:R5 is a market_share table; you missed it."

# Apply corrections from a file (newline-separated)
py -3 scripts/run_user_feedback.py --run-id myrun --input-file corrections.txt

# Show pending clarifications without applying
py -3 scripts/run_user_feedback.py --run-id myrun --input "..." --dry-run

# Answer a clarification from a prior session
py -3 scripts/run_user_feedback.py --run-id myrun --session-id <id> \
  --answer-clarification <correction_id>:"BI calculation_Alberta"
```

---

## Open questions for v0 implementation

1. **Should clarifications block or queue?** Today the design assumes synchronous clarification (user answers in the same session). For headless / CLI runs, queueing them as `pending_clarifications.jsonl` and resuming via `--answer-clarification` is the alternative. Recommend queueing — supports both UI and CLI ergonomically.

2. **Re-running QC repair after corrections — automatic or manual?** Some user corrections (e.g., adding a `market_share` table on a sheet QC didn't see) might benefit from a QC re-run. Default: manual (user clicks "▶ Re-run QC" after corrections land). Auto-rerun is opt-in via a config flag.

3. **Scope of `add_concept_alias`** — session-scoped is the default. Should there be a way for the user to promote an alias to pack-scope (writes to a `pack_user_overlays/<pack_name>.yaml` file that `pack_loader` reads alongside the canonical pack)? Probably yes for v1, not v0.

4. **How does the user identify a missed table?** Options:
   - Free-form sheet name + Excel range (E2:R5)
   - Click on a cell in the workbook viewer (requires deeper Streamlit integration)
   - Paste a screenshot region (out of scope, OCR overhead)
   v0: free-form sheet name + range only. Future: cell-click via Streamlit's image-coords.

5. **Conflict with QC repair's overrides** — QC may have already proposed a Pass-2 override for a table. Does the user's correction supersede QC's, or do they conflict? Recommend: user correction wins (records the QC override + user override side-by-side in audit; user's takes effect).

---

## Validation gates before declaring v0 complete

1. **Round-trip test** — feed 10 curated corrections (adds, retags, removes, concept patches) into the parser → resolver → applier; assert the resulting `summary_model_v1_user_corrected.json` has the expected mutations and no others.
2. **Ambiguity test** — feed deliberately ambiguous prose ("the cost table") → assert ≥1 clarification question fires and no map mutation occurs without resolution.
3. **Validator test** — feed a "this is an input" correction on a table with formula_density=0.9 → assert validator emits a warning clarification.
4. **Audit completeness** — every applied correction has a corresponding `UserCorrectionRecord` in `user_corrections.jsonl` with full provenance.
5. **`xl_ask` integration smoke** — `xl_ask` reads `evidence_quality="user_provided"` rows + boosts retrieval ranking. Ask answers a question about a user-added table correctly, citing the user's `rationale` string.
6. **Real-LLM run on `streamlit_run_enco`** — submit 5-10 plausible corrections via the orchestrator UI, verify the corrected `summary_model.json` reads cleanly and `xl_ask` can answer questions referencing user-added tables.

---

## How this composes with the rest of the pipeline

After `xl_user_feedback` lands:

- **Pass 1 (`xl_ontology` deterministic taggers)** — unchanged. Output is ALWAYS `summary_model_v0.json`.
- **Pass 2 (`xl_ontology` ReAct agents)** — unchanged. Output is `model_understanding.json` / `.md` plus the same `summary_model_v0.json`.
- **`xl_qc_repair`** — unchanged. Operates on `final_table_map.json` / `_repaired.json`. Doesn't read `summary_model.json`.
- **`xl_user_feedback` (NEW)** — reads `summary_model_v0.json`, writes `summary_model_v1_user_corrected.json` (and `v2`, `v3`, ... per session).
- **`xl_ask` (FUTURE)** — prefers latest `_user_corrected.json` over auto-derived; falls through gracefully when no corrections exist.

No upstream stage is modified by the addition of `xl_user_feedback`. It's a pure consumer of ontology output + producer of corrected output.

---

## Reusing existing machinery

Why this is cheap to build (when the time comes):

| Need | Existing piece | What carries over |
|---|---|---|
| Read current ontology output | `apps/orchestrator/ontology_viewer.py` | "All tagged tables" + "Model understanding" tabs are the review surface |
| Parse free-form prose via LLM | `xl_qc_repair/llm_review.py` patterns | Tolerant JSON parser, server-side coercion, code-fence stripping, `_strip_code_fence`, `_parse_verdict` |
| Verdict → mutation with audit | `xl_qc_repair/decision_applier.py` | Deep-copy + apply + JSONL audit pattern |
| Trace event stream | `xl_ontology/schemas.py` `_TraceBase` + writers | New `UserCorrectionProposed` / `UserCorrectionApplied` / `ClarificationAsked` events |
| LLM dispatch + budgeting | `xl_ontology.tools.llm.ask_llm` + `LlmBudget` | Already supports `budget_phase=` parameter; add `"feedback"` phase |
| Schema for table identity | `core/paths.py` + V6's `final_table_map.json` | Same `table_id` / `coordinates` / `bbox` everywhere |
| Excel-range parsing | `xl_table/coords.py` (`excel_to_bbox`, `bbox_to_excel_range`) | Used directly by the resolver |
| Validators | `xl_qc_repair/footnote_plausibility.py` (rule shape) + `xl_ontology/agents/inputs_helpers.py` (lexicon checks) | Adapt the helpers; same gates |

You're not building from scratch. The plumbing pattern is established by QC repair; this is "QC repair, but for ontology, with user-driven verdicts instead of trigger-driven verdicts."

---

## When to start

After:
1. `xl_ask v0.1` ships — so there's a real downstream consumer driving requirements.
2. Ontology Pass 2's narration quality stabilises — so the user has something coherent to review.
3. `xl_qc_repair` is deemed "done enough" — so you're not chasing two moving targets.

Until then: this document is the design. Read, critique, refine before implementation begins.

---

## Reference: where this fits in the broader roadmap

```
┌──────────────────────────────────────────────────────────────────────┐
│                          parse_excel pipeline                            │
├──────────────────────────────────────────────────────────────────────┤
│                                                                       │
│   Workbook ingest:    xl_init → xl_parse → xl_table                  │
│                                       ↓                               │
│   Structural enrichment:   xl_sheetagent (V3a + V6)                   │
│                                       ↓                               │
│   Formula DAG:          xl_dag                                        │
│                                       ↓                               │
│   QC + repair:          xl_qc_repair                                  │
│                                       ↓                               │
│   Ontology:             xl_ontology (Pass 1 deterministic +           │
│                                      Pass 2 ReAct comprehension)      │
│                                       ↓                               │
│   ──────────────────  USER VALIDATION GATE  ────────────────────────  │
│                                       ↓                               │
│   User corrections:     xl_user_feedback   ← THIS DOCUMENT            │
│                                       ↓                               │
│   Q&A:                  xl_ask                                        │
│                                                                       │
└──────────────────────────────────────────────────────────────────────┘
```

`xl_user_feedback` is the human-validation gate between auto-derived ontology and user-facing Q&A. It exists because no auto-derivation pipeline is perfect and pre-validation > post-correction in every retrieval system.
