#!/usr/bin/env python
"""CLI wrapper for the full parse_excel pipeline: init -> parse -> table [-> image]."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

# Add project root to path
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT))

from core.config import load_config
from core.stage_config import parse_config_from_pipeline, xl_init_config_from_pipeline

from core.paths import (
    get_run_root,
    get_init_output_path,
    get_parse_output_path,
    get_table_output_path,
    get_image_output_path,
    get_log_path,
)


def main():
    parser = argparse.ArgumentParser(description="parse_excel full pipeline")
    parser.add_argument("--input", required=True, help="Path to the input Excel workbook")
    parser.add_argument("--run-id", required=True, help="Run identifier")
    parser.add_argument("--output-base", default="data/output", help="Base output directory")
    parser.add_argument("--with-image", action="store_true", help="Run optional xl_image stage")
    parser.add_argument("--with-sheetagent", action="store_true", help="Run optional xl_sheetagent stage")
    parser.add_argument("--with-dag", action="store_true", help="Run optional xl_dag stage (after v6 finalize)")
    parser.add_argument("--with-qc-repair", action="store_true", help="Run optional xl_qc_repair stage (after xl_dag, before xl_ontology)")
    parser.add_argument("--with-ontology", action="store_true", help="Run optional xl_ontology seed phase (after xl_dag / qc_repair)")
    parser.add_argument("--no-vba-cleaning", action="store_true", help="Skip VBA cleaning")

    args = parser.parse_args()

    base = args.output_base
    run_id = args.run_id

    # Ensure run root exists
    run_root = get_run_root(run_id, base)
    run_root.mkdir(parents=True, exist_ok=True)
    log_dir = get_log_path(run_id, base)
    log_dir.mkdir(parents=True, exist_ok=True)

    summary = {
        "input_workbook": str(Path(args.input).resolve()),
        "run_id": run_id,
        "stages": {},
        "vba_cleaning_ran": not args.no_vba_cleaning,
        "xl_init_mode": "minimal",
        "image_stage_ran": args.with_image,
        "warnings": [],
    }

    # -- Stage 1: xl_init --
    print("=" * 60)
    print("Stage 1: xl_init")
    print("=" * 60)
    t0 = time.time()
    try:
        from xl_init import run_init

        init_out = get_init_output_path(run_id, base)
        init_out.mkdir(parents=True, exist_ok=True)

        pc = load_config()
        init_base = xl_init_config_from_pipeline(pc)
        init_config = replace(
            init_base,
            run_vba_cleaning=not args.no_vba_cleaning,
            interactive=False,
        )
        init_result = run_init(args.input, str(init_out), config=init_config)

        summary["stages"]["xl_init"] = {
            "duration_s": round(time.time() - t0, 2),
            "output_path": str(init_out),
            "cleaned_workbook": init_result.cleaned_workbook_path,
            "approved_sheets": init_result.table_sheets,
        }
        print(f"  Done in {summary['stages']['xl_init']['duration_s']}s")
        print(f"  Approved sheets: {len(init_result.table_sheets)}")
    except Exception as exc:
        summary["stages"]["xl_init"] = {"error": str(exc)}
        summary["warnings"].append(f"xl_init failed: {exc}")
        print(f"  FAILED: {exc}")
        # Write summary and exit
        _write_summary(summary, log_dir)
        sys.exit(1)

    # -- Stage 2: xl_parse --
    print("\n" + "=" * 60)
    print("Stage 2: xl_parse")
    print("=" * 60)
    t0 = time.time()
    try:
        from xl_parse import run_parse

        parse_out = get_parse_output_path(run_id, base)
        parse_out.mkdir(parents=True, exist_ok=True)

        workbook_path = init_result.cleaned_workbook_path
        parse_cfg = parse_config_from_pipeline(load_config())
        parse_results = run_parse(workbook_path, str(parse_out), config=parse_cfg)

        summary["stages"]["xl_parse"] = {
            "duration_s": round(time.time() - t0, 2),
            "output_path": str(parse_out),
            "sheets_processed": len(parse_results),
        }
        print(f"  Done in {summary['stages']['xl_parse']['duration_s']}s")
        print(f"  Sheets processed: {len(parse_results)}")
    except Exception as exc:
        summary["stages"]["xl_parse"] = {"error": str(exc)}
        summary["warnings"].append(f"xl_parse failed: {exc}")
        print(f"  FAILED: {exc}")
        _write_summary(summary, log_dir)
        sys.exit(1)

    # -- Stage 3: xl_table --
    print("\n" + "=" * 60)
    print("Stage 3: xl_table")
    print("=" * 60)
    t0 = time.time()
    try:
        from xl_table import run_table

        table_out = get_table_output_path(run_id, base)
        table_out.mkdir(parents=True, exist_ok=True)

        struct_tables = run_table(str(parse_out), str(table_out))

        n_tables = (
            sum(len(tree.tables) for tree in struct_tables.sheets) if struct_tables else 0
        )
        summary["stages"]["xl_table"] = {
            "duration_s": round(time.time() - t0, 2),
            "output_path": str(table_out),
            "tables_detected": n_tables,
        }
        print(f"  Done in {summary['stages']['xl_table']['duration_s']}s")
        print(f"  Tables detected: {n_tables}")
    except Exception as exc:
        summary["stages"]["xl_table"] = {"error": str(exc)}
        summary["warnings"].append(f"xl_table failed: {exc}")
        print(f"  FAILED: {exc}")

    # -- Stage 4: xl_image (optional) --
    if args.with_image:
        print("\n" + "=" * 60)
        print("Stage 4: xl_image (optional)")
        print("=" * 60)
        t0 = time.time()
        try:
            from xl_image import run_image

            image_out = get_image_output_path(run_id, base)
            image_out.mkdir(parents=True, exist_ok=True)

            run_image(str(parse_out), str(image_out))

            summary["stages"]["xl_image"] = {
                "duration_s": round(time.time() - t0, 2),
                "output_path": str(image_out),
            }
            print(f"  Done in {summary['stages']['xl_image']['duration_s']}s")
        except Exception as exc:
            summary["stages"]["xl_image"] = {"error": str(exc)}
            summary["warnings"].append(f"xl_image failed: {exc}")
            print(f"  FAILED: {exc}")

    # -- Stage 5: xl_sheetagent (optional) --
    if args.with_sheetagent:
        print("\n" + "=" * 60)
        print("Stage 5: xl_sheetagent (optional)")
        print("=" * 60)
        t0 = time.time()
        try:
            from xl_sheetagent import run_sheetagent
            from core.paths import get_sheetagent_output_path

            sa_out = get_sheetagent_output_path(run_id, base)
            sa_out.mkdir(parents=True, exist_ok=True)

            enriched = run_sheetagent(run_id, str(sa_out))

            n_enriched = len(enriched) if enriched else 0
            summary["stages"]["xl_sheetagent"] = {
                "duration_s": round(time.time() - t0, 2),
                "output_path": str(sa_out),
                "tables_enriched": n_enriched,
            }
            print(f"  Done in {summary['stages']['xl_sheetagent']['duration_s']}s")
            print(f"  Tables enriched: {n_enriched}")
        except Exception as exc:
            summary["stages"]["xl_sheetagent"] = {"error": str(exc)}
            summary["warnings"].append(f"xl_sheetagent failed: {exc}")
            print(f"  FAILED: {exc}")

    # -- Stage 6: xl_dag (optional) --
    if args.with_dag:
        print("\n" + "=" * 60)
        print("Stage 6: xl_dag (optional)")
        print("=" * 60)
        t0 = time.time()
        try:
            from core.stage_config import dag_config_from_pipeline
            from xl_dag import run_dag_analysis

            dag_cfg = dag_config_from_pipeline(load_config())
            dag_result = run_dag_analysis(
                run_id=run_id,
                output_base=base,
                config=dag_cfg,
            )
            summary["stages"]["xl_dag"] = {
                "duration_s": round(time.time() - t0, 2),
                "artifacts": dag_result.artifacts,
                "errors": dag_result.errors,
            }
            print(f"  Done in {summary['stages']['xl_dag']['duration_s']}s")
            print(f"  Artifacts: {len(dag_result.artifacts)}")
            if dag_result.errors:
                print(f"  Errors:    {len(dag_result.errors)}")
        except Exception as exc:
            summary["stages"]["xl_dag"] = {"error": str(exc)}
            summary["warnings"].append(f"xl_dag failed: {exc}")
            print(f"  FAILED: {exc}")

    # -- Stage 6.5: xl_qc_repair (optional) --
    if args.with_qc_repair:
        print("\n" + "=" * 60)
        print("Stage 6.5: xl_qc_repair (optional)")
        print("=" * 60)
        t0 = time.time()
        try:
            from core.stage_config import qc_repair_config_from_pipeline
            from xl_qc_repair import run_qc_repair

            qc_cfg = qc_repair_config_from_pipeline(load_config())
            qc_result = run_qc_repair(run_id=run_id, output_base=base, config=qc_cfg)
            summary["stages"]["xl_qc_repair"] = {
                "duration_s": round(time.time() - t0, 2),
                "n_targets": qc_result.n_targets,
                "n_safe_auto": qc_result.n_safe_auto,
                "n_llm_reviewed": qc_result.n_llm_reviewed,
                "n_repaired": qc_result.n_repaired,
                "n_unresolved": qc_result.n_unresolved,
                "llm_calls_used": qc_result.llm_calls_used,
            }
            print(f"  Done in {summary['stages']['xl_qc_repair']['duration_s']}s")
            print(f"  Targets: {qc_result.n_targets}, repaired: {qc_result.n_repaired}, llm: {qc_result.llm_calls_used}")
        except Exception as exc:
            summary["stages"]["xl_qc_repair"] = {"error": str(exc)}
            summary["warnings"].append(f"xl_qc_repair failed: {exc}")
            print(f"  FAILED: {exc}")

    # -- Stage 7: xl_ontology (optional) --
    if args.with_ontology:
        print("\n" + "=" * 60)
        print("Stage 7: xl_ontology (optional)")
        print("=" * 60)
        t0 = time.time()
        try:
            from core.stage_config import ontology_config_from_pipeline
            from xl_ontology import run_ontology

            ont_cfg = ontology_config_from_pipeline(load_config())
            ont_result = run_ontology(
                run_id,
                output_base=base,
                config=ont_cfg,
            )
            summary["stages"]["xl_ontology"] = {
                "duration_s": round(time.time() - t0, 2),
                "version_written": ont_result.version_written,
                "llm_calls_used": ont_result.llm_calls_used,
                "artifacts": [str(p) for p in ont_result.artifacts],
                "errors": ont_result.errors,
            }
            print(f"  Done in {summary['stages']['xl_ontology']['duration_s']}s")
            print(f"  Version:   v{ont_result.version_written}")
            print(f"  Artifacts: {len(ont_result.artifacts)}")
            if ont_result.errors:
                print(f"  Errors:    {len(ont_result.errors)}")
        except Exception as exc:
            summary["stages"]["xl_ontology"] = {"error": str(exc)}
            summary["warnings"].append(f"xl_ontology failed: {exc}")
            print(f"  FAILED: {exc}")

    # -- Write summary --
    _write_summary(summary, log_dir)

    print("\n" + "=" * 60)
    print("Pipeline complete.")
    print(f"  Run root: {run_root}")
    print(f"  Summary:  {log_dir / 'pipeline_summary.json'}")
    if summary["warnings"]:
        print(f"  Warnings: {len(summary['warnings'])}")
        for w in summary["warnings"]:
            print(f"    - {w}")


def _write_summary(summary: dict, log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    summary_path = log_dir / "pipeline_summary.json"
    with open(summary_path, "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=2, default=str)


if __name__ == "__main__":
    main()
