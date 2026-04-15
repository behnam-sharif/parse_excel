# SPEC: LLM Rules Engine for xl_sheetagent_v5

## Objective

Refactor `xl_sheetagent_v5/table_agent.py` to replace the generic, always-off LLM refinement path with a **rule-triggered, narrow LLM arbitration system**. The LLM is called only when specific structural anomalies are detected — with a focused question, constrained context, and expected JSON output.

---

## Scope

### Package: `xl_sheetagent_v5/`

Files in scope (all inside `xl_sheetagent_v5/`):

| File | Action |
|------|--------|
| `table_agent.py` | MODIFY — remove generic LLM path, add rules engine call point |
| `config.py` | MODIFY — remove old LLM flags, add new rule-specific flags |
| `schemas.py` | MODIFY — add `LLMRuleResult` dataclass, add `llm_flags` to `V5TableResult` |
| `prompt_builder.py` | MODIFY — remove old `build_v5_prompt` / `parse_v5_response`, replace with rule-specific builders |
| `llm_audit.py` | KEEP AS-IS — reuse for new rule-specific audit recording |
| `llm_rules.py` | CREATE — new file: rule definitions, detection functions, context builders |
| All other files | NO CHANGES — `row_tagger.py`, `header_detector.py`, `candidate_collector.py`, `core_region_detector.py`, `table_resolver.py`, `runner.py`, `exporter.py`, `formatting_agent.py`, `note_keywords.py` |

### What to KEEP (do NOT remove)

- `use_llm_metadata_resolution` in config — this is for reconciliation in `table_resolver.py` and is a separate concern
- `llm_audit.py` and `record_llm_call()` — reuse for new rules
- The V4 LLM dispatch import mechanism (`from xl_sheetagent_v4.llm_dispatch import complete_text`) — reuse as the LLM transport
- All rate-limiting logic (`max_llm_calls_per_sheet`, `llm_call_counter`)

---

## Phase 1: REMOVE — Clean Out Generic LLM Path

### 1.1 In `table_agent.py`, remove these functions entirely

```
_should_call_llm()        # The generic gating function
_call_llm()               # The generic LLM call
_apply_llm_overrides()    # The generic override applicator
```

### 1.2 In `table_agent.py` `_analyze_table_inner()`, remove Step 6 block

Remove the entire "Step 6: Selective LLM refinement" block (lines that set `llm_called`, `llm_overrides`, `llm_tokens` and the `if _should_call_llm(...)` branch). Replace with a call to the new rules engine (see Phase 3).

### 1.3 In `table_agent.py` `_determine_table_role()`, remove LLM fallback

Remove all references to `llm_overrides.get("table_role")`. The table role must be determined purely deterministically for now. If the role is ambiguous, return `"mixed"` — do NOT fall back to LLM.

### 1.4 In `config.py`, remove these fields

```python
use_llm: bool                    # REMOVE — replaced by per-rule flags
use_inherit_header_amend_llm: bool  # REMOVE — replaced by rules engine
llm_children_fallback: bool      # REMOVE — not used
```

### 1.5 In `config.py`, keep these fields (they serve the LLM transport, not the old path)

```python
llm_model: str                   # KEEP — used by rules engine
llm_max_tokens: int              # KEEP — used by rules engine
llm_temperature: float           # KEEP — used by rules engine
max_retries: int                 # KEEP
base_delay: float                # KEEP
max_llm_calls_per_sheet: int     # KEEP — rate limit for rules engine
max_cells_per_prompt: int        # KEEP — safety guard
use_llm_metadata_resolution: bool  # KEEP — separate concern (reconciliation)
```

### 1.6 In `prompt_builder.py`, remove

```python
build_v5_prompt()        # REMOVE entirely
parse_v5_response()      # REMOVE entirely
```

Keep any helper functions that format cell values or grid data — these will be reused by the new rule-specific prompt builders. If `prompt_builder.py` becomes empty after removal, repurpose it for the new rule prompt builders (see Phase 2).

### 1.7 In `config.py`, remove confidence weight for LLM

```python
w_llm: float             # REMOVE — LLM agreement is no longer a confidence component
```

### 1.8 In `table_agent.py` `_compute_confidence()`, remove LLM component

Remove the `llm_score` computation and the `w_llm` weight redistribution logic. Confidence should be computed from: `w_tags`, `w_header`, `w_core`, `w_role` only. Also remove `llm_called` and `llm_overrides` parameters from the function signature.

### 1.9 Update `stages.yaml`

Remove entries for: `use_llm`, `use_inherit_header_amend_llm`, `llm_children_fallback`, `w_llm`. Add entries for new rule flags (see Phase 2).

---

## Phase 2: ADD — New Config Flags and Schemas

### 2.1 New config fields in `config.py`

Add to `XlSheetAgentV5Config`:

```python
# LLM Rules Engine
enable_llm_rules: bool                      # Master switch for all rules
rule_parent_header_formula: bool            # Enable Rule 1
rule_parent_header_composite: bool          # Enable Rule 2
rule_header_title_collision: bool           # Enable Rule 3
llm_rule_max_context_rows: int              # Max rows sent per rule prompt (default: 6)
```

### 2.2 New stages.yaml entries

```yaml
xl_sheetagent_v5:
  # ... existing entries ...
  
  # LLM Rules Engine
  enable_llm_rules: false          # Master switch (off by default for safety)
  rule_parent_header_formula: true
  rule_parent_header_composite: true
  rule_header_title_collision: true
  llm_rule_max_context_rows: 6
```

### 2.3 New schema in `schemas.py`

```python
@dataclass
class LLMRuleResult:
    """Result of a single LLM rule evaluation."""
    rule_name: str                          # e.g., "parent_header_formula"
    triggered: bool                         # Did the detection function fire?
    llm_called: bool                        # Was LLM actually called? (gates may block)
    llm_response: Optional[Dict[str, Any]]  # Parsed JSON from LLM
    applied: bool                           # Was the LLM suggestion applied?
    reason: str                             # Why triggered / why not applied
    tokens_used: int = 0
    elapsed_ms: int = 0
```

### 2.4 Add `llm_rule_results` to `V5TableResult`

Add to `V5TableResult` dataclass:

```python
llm_rule_results: List[LLMRuleResult] = field(default_factory=list)
```

### 2.5 Replace `llm_flags` in signals

Instead of the old `llm_called` / `llm_tag_overrides` signals, the new system writes:

```python
signals["llm_rules_triggered"] = ["parent_header_formula"]  # list of rule names that fired
signals["llm_rules_applied"] = ["parent_header_formula"]     # list of rules whose results were applied
```

---

## Phase 3: BUILD — The Rules Engine

### 3.1 Create `xl_sheetagent_v5/llm_rules.py`

This is the core new file. Structure:

```python
"""
xl_sheetagent_v5/llm_rules.py — Rule-triggered LLM arbitration.

Each rule has:
  1. detect(...)  → bool + evidence dict   (pure deterministic check)
  2. build_context(...)  → dict            (assembles prompt payload)
  3. build_prompt(context) → str           (renders the LLM prompt)
  4. parse_response(raw) → dict            (parses LLM JSON response)
  5. apply(result, ...)  → mutations       (modifies table_agent state)
"""

from __future__ import annotations
import json
import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Callable

from .schemas import LLMRuleResult, CoreRegion, RowTag, ConsumedMap
from .config import XlSheetAgentV5Config
from .llm_audit import record_llm_call

logger = logging.getLogger(__name__)
```

### 3.2 Rule Registry Pattern

```python
@dataclass
class LLMRule:
    """Definition of a single LLM rule."""
    name: str
    config_flag: str                        # Field name in config to check
    detect: Callable[..., tuple[bool, Dict[str, Any]]]
    build_context: Callable[..., Dict[str, Any]]
    build_prompt: Callable[[Dict[str, Any]], str]
    parse_response: Callable[[str], Dict[str, Any]]
    apply: Callable[..., None]

# Registry — add new rules here
RULE_REGISTRY: List[LLMRule] = []

def register_rule(rule: LLMRule):
    RULE_REGISTRY.append(rule)
```

### 3.3 Main entry point (called from table_agent)

```python
def run_llm_rules(
    grid: List[List[Any]],
    types: List[List[str]],
    row_tags: List[RowTag],
    core_regions: List[CoreRegion],
    headers: List[List[str]],
    header_indices: List[int],
    consumed: ConsumedMap,
    candidates: Dict[str, Any],
    bbox: Dict[str, int],
    hierarchy_role: str,
    merge_info: List,
    bold_rows: Set[int],
    table_id: str,
    sheet_name: str,
    config: XlSheetAgentV5Config,
    llm_client: Any,
    llm_call_counter: Optional[List[int]],
    audit_records: Optional[Dict[str, List]],
    signals: Dict[str, Any],
    formatting_data: Optional[Dict[str, Any]] = None,
) -> List[LLMRuleResult]:
    """
    Run all registered LLM rules against the current table state.
    
    Called from table_agent AFTER core_region detection, BEFORE reconciliation.
    Returns list of LLMRuleResult (one per rule that was evaluated).
    
    Mutations: rules may modify core_regions, headers, header_indices,
    consumed, candidates, row_tags IN PLACE.
    """
    if not config.enable_llm_rules:
        return []
    
    results = []
    triggered_names = []
    applied_names = []
    
    for rule in RULE_REGISTRY:
        # Check per-rule config flag
        if not getattr(config, rule.config_flag, False):
            continue
        
        # Rate limit check
        if llm_call_counter is not None and llm_call_counter[0] >= config.max_llm_calls_per_sheet:
            logger.warning("LLM call limit reached; skipping rule %s", rule.name)
            break
        
        # Step 1: Detect
        triggered, evidence = rule.detect(
            grid=grid, types=types, row_tags=row_tags,
            core_regions=core_regions, headers=headers,
            header_indices=header_indices, consumed=consumed,
            candidates=candidates, bbox=bbox,
            hierarchy_role=hierarchy_role, merge_info=merge_info,
            bold_rows=bold_rows, config=config,
        )
        
        if not triggered:
            results.append(LLMRuleResult(
                rule_name=rule.name, triggered=False,
                llm_called=False, llm_response=None,
                applied=False, reason="detection_negative",
            ))
            continue
        
        triggered_names.append(rule.name)
        
        # Step 2: Build context
        context = rule.build_context(
            grid=grid, types=types, row_tags=row_tags,
            core_regions=core_regions, headers=headers,
            header_indices=header_indices, consumed=consumed,
            candidates=candidates, bbox=bbox,
            merge_info=merge_info, bold_rows=bold_rows,
            evidence=evidence, config=config,
            formatting_data=formatting_data,
        )
        
        # Step 3: Build prompt
        prompt = rule.build_prompt(context)
        
        # Step 4: Call LLM
        # (use existing _llm_complete transport + audit)
        # ... LLM call with retry, audit recording ...
        # Step 5: Parse response
        # Step 6: Apply if valid
        # ... (see implementation notes below)
    
    signals["llm_rules_triggered"] = triggered_names
    signals["llm_rules_applied"] = applied_names
    return results
```

---

## Phase 4: IMPLEMENT — The Three Initial Rules

### Rule 1: `parent_header_formula`

**Trigger condition (detect):**
- `hierarchy_role == "parent"`
- First row of bbox (row 0 in local grid) is currently inside a `core_region`
- That row contains at least one FORMULA cell

**What it means:** The first row of the parent bbox has formulas and was classified as data, but it's likely a header row. The core region start needs to be adjusted downward.

**Context to send (build_context):**
- All merged cell ranges for the parent bbox (including children bboxes)
- Cell values and types for: first row + next 3 rows of bbox (i.e., rows 0–3)
- Formatting info (bold, fill, borders) for those rows — same structure as xl_init
- The current `core_region.bbox` coordinates
- The current `headers` and `header_indices`

**Prompt (build_prompt):**
```
You are analyzing an Excel table region. The first row of this table contains formulas 
and may be a header row rather than data.

RULE: The first row of a parent table region CANNOT be inside the core data region.
If the first row contains formulas and looks like a header, the core region must start 
below it.

Here is the table data (rows 0-3):
[cell values grid, formatted as TSV with types annotated]

Formatting:
[bold/fill/border info per cell]

Merged cells: [list]

Current core region: rows {r1} to {r2}
Current headers: {headers}

Respond with ONLY valid JSON:
{
  "is_header": true/false,
  "title_row": null or row_index,
  "header_rows": [row_indices],
  "core_start_row": row_index,
  "row_names_start": null or row_index,
  "reasoning": "brief explanation"
}

DO NOT invent values. Only report what you see in the data.
```

**Expected response schema:**
```json
{
  "is_header": true,
  "title_row": null,
  "header_rows": [0],
  "core_start_row": 1,
  "row_names_start": null,
  "reasoning": "Row 0 contains formula-derived column labels"
}
```

**Apply logic:**
- If `is_header == true`:
  - Update `headers` to include the identified header rows
  - Update `header_indices` 
  - Update `consumed.header_rows`
  - Adjust `core_regions[0].bbox["r1"]` to `core_start_row` (relative to bbox)
  - If `title_row` is set, update `candidates["title"]` and `consumed.title_rows`
  - Record in signals: `signals["llm_parent_header_formula_applied"] = True`

---

### Rule 2: `parent_header_composite`

**Trigger condition (detect):**
- `hierarchy_role in ("parent", "standalone")`
- `core_regions` exist
- Number of rows in core region does NOT match number of `row_names` (if row_names exist)
- OR: core region has more rows than expected based on row_names count

**What it means:** There may be a composite (multi-row) header where some header rows are being counted as data rows, creating the mismatch between core rows and row names.

**Context to send (build_context):**
- Title rows (if any)
- First 3 rows of core region
- Row names list
- For parent tables: combined parent + child metadata (child_metadata first, then child_numeric)
- Cell values + formatting for all context rows

**Prompt:**
```
You are analyzing an Excel table to determine if it has a composite (multi-row) header.

RULE: The number of data rows in the core region should match the number of row names.
If there is a mismatch, some rows currently classified as data may actually be 
additional header rows (composite header).

Title: {title or "None"}
Current header: {headers}
Current core region: rows {r1}-{r2} ({n} rows)
Row names: {row_names} ({m} names)
Mismatch: {n} core rows vs {m} row names

First 3 rows of core region:
[cell values grid]

Formatting:
[bold/fill info]

Respond with ONLY valid JSON:
{
  "has_composite_header": true/false,
  "additional_header_rows": [row_indices relative to bbox],
  "adjusted_core_start": row_index,
  "reasoning": "brief explanation"
}

DO NOT invent values.
```

**Apply logic:**
- If `has_composite_header == true`:
  - Extend `headers` with the additional header row values
  - Update `header_indices`
  - Adjust core region start row
  - Update `consumed.header_rows`

---

### Rule 3: `header_title_collision`

**Trigger condition (detect):**
- `candidates["title"]` is None (no title found)
- Headers exist and `header_indices` is non-empty
- The first header row has a cell that looks like a title (long text in first column) AND column headers in remaining columns
- Existing `_detect_composite_row0()` heuristic returned `(None, None)` — meaning the deterministic check was inconclusive

**What it means:** The title and header are merged into a single row. The deterministic `_detect_composite_row0` couldn't decide (e.g., all cells are TEXT, no bold signal).

**Context to send:**
- First header row (full cell values + formatting)
- Next 2 rows for context
- Merge info for the header region

**Prompt:**
```
You are analyzing the first row of an Excel table header.

RULE: Sometimes a table title and column headers share the same row.
For example: ["Drug costs", "2020", "2021", "2022"] where "Drug costs" is the title
and "2020", "2021", "2022" are column headers.

Row data: {row_values}
Types: {row_types}
Formatting: {formatting}

Is the first cell a title that should be separated from the column headers?

Respond with ONLY valid JSON:
{
  "is_composite": true/false,
  "title_text": "the title" or null,
  "title_col": column_index or null,
  "reasoning": "brief explanation"
}
```

---

## Phase 5: INTEGRATE — Wire Into table_agent.py

### 5.1 New Step 6 in `_analyze_table_inner()`

Replace the removed Step 6 with:

```python
    # Step 6: LLM Rules Engine (targeted structural arbitration)
    llm_rule_results = []
    if config.enable_llm_rules:
        from .llm_rules import run_llm_rules
        llm_rule_results = run_llm_rules(
            grid=grid, types=types, row_tags=row_tags,
            core_regions=core_regions, headers=headers,
            header_indices=header_indices, consumed=consumed,
            candidates=candidates, bbox=bbox,
            hierarchy_role=hierarchy_role, merge_info=merge_info,
            bold_rows=bold_rows, table_id=table_id,
            sheet_name=sheet_name, config=config,
            llm_client=llm_client,
            llm_call_counter=llm_call_counter,
            audit_records=audit_records, signals=signals,
            formatting_data=None,  # Pass if available
        )
```

### 5.2 Pass results to V5TableResult

```python
    return V5TableResult(
        # ... existing fields ...
        llm_rule_results=llm_rule_results,
        # confidence now computed WITHOUT llm component
    )
```

### 5.3 Update `_compute_confidence()`

New signature (no llm params):

```python
def _compute_confidence(
    row_tags: List[RowTag],
    header_conf: float,
    core_regions: List[CoreRegion],
    table_role: str,
    config: XlSheetAgentV5Config,
    llm_rule_results: List[LLMRuleResult] = None,
) -> float:
```

New logic: if any LLM rule was applied, add a small bonus (0.05) to confidence. If any rule was triggered but LLM was unavailable, penalize slightly (-0.05). The main confidence computation uses only `w_tags`, `w_header`, `w_core`, `w_role`.

---

## Implementation Notes for Claude Code

### Workflow

1. **Read first**: Read all files in `xl_sheetagent_v5/` to understand current state
2. **Remove**: Execute Phase 1 removals — verify no import errors after each removal
3. **Add schemas**: Execute Phase 2 — add new dataclasses and config fields
4. **Build rules engine**: Execute Phase 3 — create `llm_rules.py`
5. **Implement rules**: Execute Phase 4 — implement the three detection functions and prompt builders
6. **Integrate**: Execute Phase 5 — wire into table_agent
7. **Test**: Run existing tests to verify no regressions. The system should produce identical output with `enable_llm_rules: false` (default).

### Key Principles

- **No behavior change with rules disabled**: When `enable_llm_rules: false`, the output must be identical to current output (since LLM is already off)
- **Mutation in place**: Rules modify `core_regions`, `headers`, `consumed` etc. in place — same pattern as existing code
- **JSON-only LLM responses**: All prompts must instruct the LLM to respond with valid JSON only. Use `json.loads()` with try/except
- **Audit everything**: Every rule evaluation (triggered or not) must be recorded via `record_llm_call()` with `call_kind=f"rule_{rule.name}"`
- **Formatting data**: The `formatting_data` parameter is optional for now. If `formatting_agent.py` has already run and data is available, pass it. If not, the prompts should work without it (degrade gracefully)

### File Layout After Implementation

```
xl_sheetagent_v5/
  __init__.py          # unchanged
  config.py            # modified (removed 3 fields, added 4 fields)
  schemas.py           # modified (added LLMRuleResult, added field to V5TableResult)
  table_agent.py       # modified (removed generic LLM, added rules engine call)
  llm_rules.py         # NEW — rule registry + 3 initial rules
  prompt_builder.py    # modified (removed generic builders, kept helpers)
  llm_audit.py         # unchanged
  row_tagger.py        # unchanged
  header_detector.py   # unchanged
  candidate_collector.py # unchanged
  core_region_detector.py # unchanged
  table_resolver.py    # unchanged
  runner.py            # unchanged (may need minor update if config fields changed)
  exporter.py          # unchanged
  formatting_agent.py  # unchanged
  note_keywords.py     # unchanged
```

### Updating stages.yaml

The stages.yaml file that feeds config construction needs the new fields added under the v5 section and the removed fields deleted. Search for where V5 config values are defined in `stages.yaml` or in `core/config.py` loader.

### Testing Strategy

1. Run the pipeline with `enable_llm_rules: false` → output must be identical to before
2. Run with `enable_llm_rules: true` but no `llm_client` → rules should detect and log but not call LLM
3. Unit test each rule's `detect()` function with known BIM table patterns
4. Integration test with the BIM Excel file to verify rules trigger on expected tables

---

## Future Rules (not in this spec — for reference only)

These will be added later using the same registry pattern:

- `rule_merged_cell_header`: When merged cells span the header region and the merge pattern doesn't match the column structure
- `rule_orphan_rows`: When rows at the bottom of a table don't fit any role (not data, not notes, not footer)
- `rule_multi_table_confirmation`: When a bbox might contain multiple tables and deterministic splitting is ambiguous
- `rule_row_name_mismatch`: When detected row names don't align with core region structure

The registry pattern makes adding these trivial — define detect/context/prompt/parse/apply and register.