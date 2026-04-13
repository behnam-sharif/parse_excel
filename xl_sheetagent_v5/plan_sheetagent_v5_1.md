# Sheet Agent v5.1 — Child-centric hierarchy & coverage

**Context:** `xl_sheetagent_v5` (v5) is **implemented** — enricher, `ResolvedTable`, `table_resolver`, core regions, row tagging, exporter, etc. This document is the **follow-on plan (v5.1)** for structural correctness on compound tables (e.g. BIM calculation blocks), multi-core regions without V3a cuts, and consumer-facing hierarchy. It does not restate the full v5 module map; see `Plan_sheetagent_v5.md` for the shipped design.

---

## 1. Observed failures (drives v5.1)

### 1.1 BIM-style calculation table (example: Table 1, BIM calculation, `B3:H105`)

**What works today**

- Multiple children are detected in some cases.
- Header is often correct.

**What is wrong**

| Symptom | Impact |
|--------|--------|
| First child (e.g. `B3:H4`) ignored | Section / workbook title text lost |
| Parent uses entire first column as `row_names` | Wrong semantics; not per-section row labels |
| Child-level structure lost | Titles like “BIM calculation”, “Number of patients eligible…” missing from structured output |
| Section lines misclassified as notes | “Drug costs”, “Costs of adverse events”, “Subsequent treatment costs” end up in **notes** instead of **child titles** |
| Parent absorbs children | Hierarchy flattened; one blob |

**Core structural gap**

- The pipeline does **not** enforce that **children jointly explain the parent bbox** (modulo shared header envelope), which leads to missing children, bad row names, wrong notes, and lost hierarchy.

### 1.2 Multi-core, no children (example table id `1f634d7d13c5e37c_t57_t25_t17_b8`)

**What is wrong**

- Several core regions are detected.
- No children are created; structure is not resolved.

**Expected**

- Core regions **drive** structure: each region becomes a child (or regions are **grouped** into meaningful children via LLM).
- If there are **no row cuts** but **multiple core regions**, regions (plus enriched grid/formula context) go to the **LLM** to split into children with title, header inheritance, row names.
- **Overlapping** regions: formula / dependency enrichment first; LLM separates matrices (e.g. left vs right) and grouping.

---

## 2. Target behavior (rules)

### Rule 1 — Every child preserved; coverage

- No structural child may be silently dropped.
- The **sum of child bboxes** (+ declared shared header / optional parent title band) **covers** the parent table body. Uncovered area triggers **coverage recovery** (§4).

### Rule 2 — Child-centric output

- **Parent:** container — shared **header**; optional coarse **title** or explicit “not found”.
- **`child_titles`:** dedicated field for ordered subsection titles (**not** encoded in parent `row_names`).
- **Each child:** **title**, **header** (inherited from parent where applicable), **row names** (local), **core region(s)** (numeric/formula block), **notes** (local, strict).

### Rule 3 — Notes (strict)

- **Notes** = remaining text that is **not** part of structure (footnotes, sources, annotations).
- **Section labels** = **child titles**, not notes.
- Heuristics **propose**; **LLM** **decides** on borderline rows — no heuristic-only assignment that forces structural lines into `notes`.

### Rule 4 — Core regions drive structure

- If **multiple core regions** exist, each becomes a child **or** is merged/grouped into children with explicit rationale (in `signals` / LLM trace).

### Rule 5 — LLM when cuts fail

- If **no row cuts** but **multiple core regions**, send **regions + context** to LLM; LLM returns children with title, header rows, row names, and region membership.

### Rule 6 — Overlaps

- **Numeric-cell ownership** rule (§5) + optional formula-aware enrichment before LLM.

### One-line summary

Shift from **flattened parent output** to **child-driven structure**: each child is a logical subtable (title, local row names, core); parent is **only** a container with **shared headers** and **`child_titles`**.

---

## 3. Schema / API — `child_titles` vs parent `row_names`

- Add **`child_titles: List[str]`** (or `List[Optional[str]]`) on the **parent / container** resolved object.
- **Do not** overload parent **`row_names`** for subsection titles. Parent **`row_names`** = labels for the parent’s **own** data slice when it has a single body, or **empty / null** when the parent is a **pure container**.
- Each **child** has its own **`row_names`** aligned to that child’s core rows.

`export_mode` under `xl_sheetagent_v5`: default **`hierarchical`**; set **`flat`** only for legacy consumers.

---

## 4. Deterministic “do not absorb child” rule set

When evaluating metadata-only **absorption** (`table_resolver` / `_is_metadata_only_child` style logic), **do not absorb** if **any** of the following holds (evaluate in order; first match → keep as explicit child):

1. Child has **non-empty** `title` or **section-title** signals (tagger, bold, short label above numeric block).
2. Child bbox lies in rows **not** covered by the union of other substantive children’s bboxes.
3. **`hierarchy_role`** / tags indicate a **structural** child (including `child_metadata` when it carries visible section text — exact mapping to `schemas.py` in implementation).
4. Child has **`core_regions`** **or** is the **only** text band **between** two detected core regions (bridge).
5. Removing the child would leave **uncovered** cells inside `parent.bbox` after **reconcile** (§5).

If **none** apply, absorption may be considered — still prefer an **explicit** child node with clear `table_role` over silent merge until hierarchical export is stable.

---

## 5. Coverage recovery (strict order)

When child bboxes + header envelope do not cover `parent.bbox`:

1. **Reconcile** — Deterministic fixes: off-by-one overlaps, align to parent edges, attach leading/trailing title rows to nearest child, dedupe duplicate V3a child nodes.
2. **Heuristic gap child** — Uncovered row ranges → new minimal child nodes; `title` from first text row if present; re-run core detection inside gap.
3. **LLM gap pass** — If gaps remain: LLM receives gap coordinates, row texts, core region map; returns row → child assignments. **No** invented geometry outside `parent.bbox`.

Emit **`signals`** such as: `coverage_reconcile_ok`, `coverage_gaps_after_reconcile`, `heuristic_gap_children_added`, `llm_gap_pass_used`, `uncovered_cells` (empty when OK).

---

## 6. Numeric-cell ownership (overlap handling)

For **NUMBER** / **FORMULA** cells in the **intersection** of two candidate child bboxes (or two core regions):

1. **Owner** = child whose **core connected component** (pre-expansion mask) contains the cell.
2. If still tied: prefer child whose **row-name / label column** has the **leftmost** non-blank text on that row in the label band.
3. If still tied: **LLM** assigns ownership.
4. Duplicated **display** of read-only labels in both children is allowed **only** if flagged in `signals`; **never** drop the cell from both children.

---

## 7. Consumer-facing report (single top-level parent)

- Each **parent** table is **one** top-level item in the report / export list.
- If the parent **has children**, render **only** that parent and nest **children inside** it — children are **not** separate top-level tables.
- If the parent **has no children**, render it as a **normal standalone** table: **title**, **headers**, **row names**, **notes**, **core regions** on that single item.

*(Orchestrator / Streamlit / JSON export must follow this nesting contract.)*

---

## 8. Implementation map (v5 codebase)

| Area | Likely modules |
|------|----------------|
| Nested resolved model + `child_titles` | `schemas.py`, `table_resolver.py`, `exporter.py` |
| Absorption rules + coverage pipeline | `table_resolver.py`, `candidate_collector.py` / `table_agent.py` |
| Multi-core → children early | `runner.py`, `table_agent.py`, `core_region_detector.py` |
| Notes vs title / LLM adjudication | `row_tagger.py`, `prompt_builder.py`, `table_agent.py` |
| Report nesting | `exporter.py`, `apps/orchestrator` (v5 viewer if any) |

---

## 9. Phased rollout

| Phase | Focus |
|-------|--------|
| **P0** | `child_titles` + nested `children[]` in resolved/export; report nesting; optional flat fallback flag |
| **P1** | Resolver: stop wrongful absorption; preserve leading/title band child; parent `row_names` semantics |
| **P2** | Coverage: reconcile → heuristic gap → LLM gap |
| **P3** | Multi-core materialization + LLM structure pass when cuts missing |
| **P4** | Overlap ownership + formula-aware enrichment for LLM |

---

## 10. Verification

- **BIM-style:** first vertical band present; section strings in **`child_titles`** / child **`title`**, not **`notes`**.
- **Row names:** parent is not “entire first column” when multi-child; children have local **`row_names`**.
- **Multi-core fixture** (`1f634d7d13c5e37c_t57_t25_t17_b8`): children exist or documented merge in **`signals`**.
- **Coverage:** `uncovered_cells` empty after pipeline (or explicit failure mode).
- **Report:** no duplicate top-level entries for nested children.

---

## 11. Reference

- Shipped v5 design and module list: `Plan_sheetagent_v5.md`
- Current resolver merge behavior: `table_resolver.py` (`_resolve_parent_table`, `_is_metadata_only_child`)

---

## 12. Decisions — answers to pre-implementation questions

Use these as the working contract for v5.1 (adjust only if product constraints change).

### 1. Export mode default (`export_mode`: flat vs hierarchical)

- **Ship:** both shapes supported (`"flat"` | `"hierarchical"`).
- **Repo default (post–P1):** **`hierarchical`** — `config/stages.yaml` → `xl_sheetagent_v5.export_mode`, `XlSheetAgentV5Config.export_mode`, exporter `getattr(..., "hierarchical")`, and `_serialize_resolved_table(..., default hierarchical)`.
- **Legacy opt-in:** set **`export_mode: "flat"`** in `stages.yaml` if an external consumer still requires a flat `resolved_tables.json` (no nested `children` / `child_titles`).

### 2. `ResolvedTable.children` type

- **`children: List[ResolvedTable]`** — one uniform type for export and UI recursion.
- **Semantics:** `ResolvedTable` is either a **leaf** (`children == []`) or a **container** (`children` non-empty). The parent holds **`child_titles`** (ordered parallel to `children` if you want stable pairing without scanning each child’s `title`; if `child_titles[i]` is always `children[i].title`, you may derive one from the other — but the plan still prefers an explicit `child_titles` on the parent for empty-title children and for audit).
- **Do not** expose raw `V5TableResult` in the consumer-facing resolved export; keep `V5TableResult` internal to the pipeline until resolution is complete.

### 3. `_infer_children_from_core_regions` — full `V5TableResult` vs stubs

- **Preferred:** each inferred child bbox gets a **full pass** through the same enrichment path as other tables (row tagging, header detection, core regions, etc.) so metadata matches the rest of v5.
- **Acceptable bootstrap:** **lightweight `V5TableResult` stubs** in the resolver only as a **temporary** step to unblock coverage; schedule a **follow-up pass** that runs `analyze_table` (or equivalent) on each child bbox before final export.
- **Rule of thumb:** anything that appears in **exported** `ResolvedTable.children` should be **fully enriched** before the user sees it.

### 4. `detect_section_headers` / `build_children_from_sections` wiring

- As of v5, these live in **`candidate_collector.py`** and are **not called** from `runner.py` / `table_agent.py` (dead along the main path).
- **P1:** either **wire them into the main flow** where V3a gives a single parent with no children but multiple vertical sections, **or** replace that path with the newer coverage / multi-core materialization plan — but do **not** leave them orphaned if they are still the best heuristic for section titles.

### 5. LLM gap pass — one call vs per-gap

- **Default: one LLM call per parent table** with a structured payload: list of gap intervals (row/col), row texts, core region summary, and current child ids.
- **Per-gap calls:** only as a **fallback** if the combined prompt exceeds token limits or the model returns invalid coverage; then chunk by gap or by row window.

### 6. Formula-aware enrichment (P4)

- **Hard requirement for P4:** the **numeric-cell ownership** ladder (component → row-name column → LLM) **without** full formula parsing.
- **Formula / dependency graph:** **aspirational enhancement** — add when overlap cases justify the cost; not a gate for shipping P0–P3.

### 7. Test fixtures (BIM `B3:H105`, table id `1f634d7d13c5e37c_t57_t25_t17_b8`)

- **Do not assume** golden expected JSON is committed; **add** minimal workbooks under `tests/fixtures/` (or document `data/input/...` paths) plus **assertions** on structure (`len(children)`, `child_titles`, notes vs titles), not necessarily full cell-by-cell golden files at first.
- If real BIM workbooks exist only under `data/input/`, reference them in tests via path relative to repo root and skip in CI if files are absent, **or** commit a stripped synthetic copy.

### 8. Phased delivery

- **Sequential phases, separate PRs (or commits)** with tests after P0, P1, P2, etc. The dependency chain (schema/export → absorption → coverage → multi-core → overlap) makes **big-bang riskier** than phase + verify.
