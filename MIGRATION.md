# Migration: xlgen_v2 → xlgen_v3

## Rename: `xl_kg` → `xl_table`

In xlgen_v2, the package **`xl_kg`** was **not** a general-purpose knowledge graph engine. It performed **structural table detection and assembly** on top of xl_parse candidates. In xlgen_v3 it is renamed to **`xl_table`** everywhere (imports, docs, artifact metadata such as `xl_table_version` in serialized outputs).

## Layout changes

| xlgen_v2 | xlgen_v3 |
|----------|----------|
| Per-stage `runs/` / `outputs/` habits | Single run root: `data/output/<run_id>/` via `core/paths.py` |
| `xl_kg/config_default.yaml` (standalone) | `config/stages.yaml` key `xl_table` |
| Multiple Streamlit `main.py` files in stages | `apps/orchestrator/app.py` |

## Import / path changes

- `from xl_shared.visual_types` → `from core.visual_types`
- `from xl_kg...` → `from xl_table...`
- Public entry points: `run_init`, `run_parse`, `run_table`, `run_image`

## Backward compatibility

- xl_parse writes per-run artifacts under a timestamped subfolder and **copies** ``candidate_rows.jsonl`` and ``cut_scores.jsonl`` to the parse output root for xl_table (canonical on-disk name: ``candidate_rows.jsonl`` only).
- JSON field rename: `xl_kg_version` → `xl_table_version` on serialized xl_table output (`StructuralTables`).

## Manual review

Nested `.git` directories were not copied into xlgen_v3. See `ASSUMPTIONS.md` for deferred Track B items (e.g. coverage_report parity).
