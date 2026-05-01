"""Verify ontology output paths resolve as documented in plan §9.6."""

from __future__ import annotations

import unittest
from pathlib import Path

from core.paths import (
    get_agent_outputs_path,
    get_node_type_override_log_path,
    get_ontology_build_errors_path,
    get_ontology_output_path,
    get_ontology_trace_path,
    get_run_root,
    get_summary_model_path,
    get_table_tags_path,
)


class TestOntologyPaths(unittest.TestCase):
    RUN_ID = "abc123"
    BASE = "data/output"

    def test_output_root_under_run_root(self) -> None:
        self.assertEqual(
            get_ontology_output_path(self.RUN_ID, self.BASE),
            get_run_root(self.RUN_ID, self.BASE) / "ontology_output",
        )

    def test_summary_model_path_embeds_version(self) -> None:
        self.assertEqual(
            get_summary_model_path(self.RUN_ID, 0, self.BASE).name,
            "summary_model_v0.json",
        )
        self.assertEqual(
            get_summary_model_path(self.RUN_ID, 7, self.BASE).name,
            "summary_model_v7.json",
        )

    def test_companion_artifact_filenames(self) -> None:
        out = get_ontology_output_path(self.RUN_ID, self.BASE)
        self.assertEqual(
            get_agent_outputs_path(self.RUN_ID, self.BASE),
            out / "ontology_agent_outputs.json",
        )
        self.assertEqual(
            get_table_tags_path(self.RUN_ID, self.BASE),
            out / "ontology_table_tags.json",
        )
        self.assertEqual(
            get_ontology_trace_path(self.RUN_ID, self.BASE),
            out / "ontology_trace.jsonl",
        )
        self.assertEqual(
            get_ontology_build_errors_path(self.RUN_ID, self.BASE),
            out / "ontology_build_errors.jsonl",
        )
        self.assertEqual(
            get_node_type_override_log_path(self.RUN_ID, self.BASE),
            out / "node_type_override_log.jsonl",
        )

    def test_returns_path_objects(self) -> None:
        for fn in (
            get_ontology_output_path,
            get_agent_outputs_path,
            get_table_tags_path,
            get_ontology_trace_path,
            get_ontology_build_errors_path,
            get_node_type_override_log_path,
        ):
            self.assertIsInstance(fn(self.RUN_ID, self.BASE), Path)
        self.assertIsInstance(
            get_summary_model_path(self.RUN_ID, 0, self.BASE), Path
        )


if __name__ == "__main__":
    unittest.main()
