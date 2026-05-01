"""Import, config, and rename-policy smoke tests."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


class TestImports(unittest.TestCase):
    def test_stage_entrypoints_import(self) -> None:
        from xl_init import run_init
        from xl_image import run_image
        from xl_parse import run_parse
        from xl_table import run_table
        from xl_sheetagent import run_sheetagent, run_v6_analysis

        self.assertTrue(callable(run_init))
        self.assertTrue(callable(run_parse))
        self.assertTrue(callable(run_table))
        self.assertTrue(callable(run_image))
        self.assertTrue(callable(run_sheetagent))
        self.assertTrue(callable(run_v6_analysis))

    def test_sheetagent_v1b_schemas_import(self) -> None:
        from xl_sheetagent.schemas import (
            TreeNode,
            SheetObjectTree,
            RelationshipEdge,
            RelationshipGraph,
        )

        self.assertTrue(TreeNode is not None)
        self.assertTrue(SheetObjectTree is not None)
        self.assertTrue(RelationshipEdge is not None)
        self.assertTrue(RelationshipGraph is not None)

    def test_sheetagent_v1d_imports(self) -> None:
        from xl_sheetagent.assembly.tree_renderer import render_tree_png
        from xl_sheetagent.tools.debug_trace import (
            TraceAccumulator,
            ToolCallRecorder,
        )

        self.assertTrue(callable(render_tree_png))
        self.assertTrue(TraceAccumulator is not None)
        self.assertTrue(ToolCallRecorder is not None)

    def test_v6_finalize_imports(self) -> None:
        from xl_sheetagent.coordinates import bbox_to_coords
        from xl_sheetagent.finalize import finalize_sheet
        from xl_sheetagent.final_map_exporter import (
            export_final_map,
            serialize_final_map,
        )
        from xl_sheetagent.formula_refs import _extract_refs
        from xl_sheetagent.v6_models import (
            FinalMapChildEntry,
            FinalMapEntryParent,
            FinalMapEntryStandalone,
            FinalTableMap,
        )
        from core.paths import get_v6_final_table_map_path

        self.assertTrue(callable(bbox_to_coords))
        self.assertTrue(callable(finalize_sheet))
        self.assertTrue(callable(export_final_map))
        self.assertTrue(callable(serialize_final_map))
        self.assertTrue(callable(_extract_refs))
        self.assertTrue(callable(get_v6_final_table_map_path))
        self.assertTrue(FinalMapChildEntry is not None)
        self.assertTrue(FinalMapEntryParent is not None)
        self.assertTrue(FinalMapEntryStandalone is not None)
        self.assertTrue(FinalTableMap is not None)

    def test_xl_ontology_phase_a_imports(self) -> None:
        from xl_ontology import (
            OntologyRunError,
            OntologyRunResult,
            XlOntologyConfig,
            run_ontology,
        )
        from xl_ontology.agents import (
            CalculationsAgent,
            InputsAgent,
            OntologyAgent,
            OverallModelAgent,
            ResultsAgent,
        )
        from xl_ontology.defects import (
            DefectOutcome,
            recover_header_stub,
            recover_rownames_stub,
            recover_title_stub,
            resolve_multi_core_stub,
        )
        from xl_ontology.pack_loader import load_pack
        from xl_ontology.budget import BudgetExhausted, LlmBudget
        from xl_ontology.trace import (
            BuildErrorWriter,
            NodeTypeOverrideWriter,
            TraceWriter,
        )
        from xl_ontology.output_writers import (
            next_summary_model_version,
            write_summary_model,
        )
        from xl_ontology.agents import (
            HEURISTIC_HANDLERS,
            TRIGGER_REGISTRY,
        )
        from xl_ontology.schemas import (
            DefectProcedureSkipped,
            NodeTypeOverrideRecord,
            OntologyPack,
            OverrideEvidence,
            PackWarning,
            SummaryModel,
            WarningEvent,
        )
        from core.paths import (
            get_node_type_override_log_path,
            get_ontology_output_path,
            get_summary_model_path,
            get_ontology_trace_path,
        )

        self.assertTrue(XlOntologyConfig is not None)
        self.assertTrue(callable(load_pack))
        self.assertTrue(callable(LlmBudget))
        self.assertTrue(callable(BudgetExhausted))
        self.assertTrue(callable(TraceWriter))
        self.assertTrue(callable(BuildErrorWriter))
        self.assertTrue(callable(NodeTypeOverrideWriter))
        self.assertTrue(callable(write_summary_model))
        self.assertTrue(callable(next_summary_model_version))
        self.assertGreater(len(TRIGGER_REGISTRY), 0)
        # cross_check_complete was dropped after H-CA-1/H-CA-3 became
        # per-concept (REVISION 1 follow-up). Confirm the registry no
        # longer carries it.
        self.assertNotIn("cross_check_complete", TRIGGER_REGISTRY)
        self.assertEqual(HEURISTIC_HANDLERS, {})
        self.assertTrue(OntologyPack is not None)
        self.assertTrue(PackWarning is not None)
        self.assertTrue(SummaryModel is not None)
        self.assertTrue(WarningEvent is not None)
        self.assertTrue(DefectProcedureSkipped is not None)
        self.assertTrue(NodeTypeOverrideRecord is not None)
        self.assertTrue(OverrideEvidence is not None)
        self.assertTrue(callable(get_ontology_output_path))
        self.assertTrue(callable(get_summary_model_path))
        self.assertTrue(callable(get_ontology_trace_path))
        self.assertTrue(callable(get_node_type_override_log_path))
        self.assertTrue(callable(run_ontology))
        self.assertTrue(callable(OntologyRunError))
        self.assertTrue(OntologyRunResult is not None)
        self.assertTrue(OntologyAgent is not None)
        self.assertTrue(OverallModelAgent is not None)
        self.assertTrue(InputsAgent is not None)
        self.assertTrue(CalculationsAgent is not None)
        self.assertTrue(ResultsAgent is not None)
        self.assertTrue(DefectOutcome is not None)
        self.assertTrue(callable(recover_title_stub))
        self.assertTrue(callable(recover_header_stub))
        self.assertTrue(callable(recover_rownames_stub))
        self.assertTrue(callable(resolve_multi_core_stub))


class TestConfig(unittest.TestCase):
    def test_load_yaml_maps_to_dataclasses(self) -> None:
        from core.config import load_config
        from core.stage_config import (
            xl_table_config_from_pipeline,
            parse_config_from_pipeline,
            xl_image_config_from_pipeline,
            xl_init_config_from_pipeline,
            sheetagent_config_from_pipeline,
            sheetagent_v6_config_from_pipeline,
            ontology_config_from_pipeline,
        )

        pc = load_config()
        xl_init_config_from_pipeline(pc)
        parse_config_from_pipeline(pc)
        xl_table_config_from_pipeline(pc)
        xl_image_config_from_pipeline(pc)
        sheetagent_config_from_pipeline(pc)
        sheetagent_v6_config_from_pipeline(pc)
        ontology_config_from_pipeline(pc)


class TestNoLegacyKgSubstring(unittest.TestCase):
    def test_python_sources_exclude_xl_kg(self) -> None:
        roots = [
            _ROOT / "xl_init",
            _ROOT / "xl_parse",
            _ROOT / "xl_table",
            _ROOT / "xl_image",
            _ROOT / "xl_sheetagent",
            _ROOT / "xl_ontology",
            _ROOT / "core",
            _ROOT / "apps",
            _ROOT / "scripts",
        ]
        bad: list[Path] = []
        for root in roots:
            if not root.is_dir():
                continue
            for p in root.rglob("*.py"):
                text = p.read_text(encoding="utf-8")
                if "xl_kg" in text:
                    bad.append(p)
        self.assertEqual(
            bad,
            [],
            "xl_kg substring remains in: "
            + ", ".join(str(x) for x in bad),
        )


if __name__ == "__main__":
    unittest.main()
