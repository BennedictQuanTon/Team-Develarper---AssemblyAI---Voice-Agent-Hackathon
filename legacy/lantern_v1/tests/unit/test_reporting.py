from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from eval.reporting import create_run, write_results, write_summary


class BenchmarkReportingTests(unittest.TestCase):
    def test_run_artifacts_are_namespaced_and_immutable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_root = Path(tmp)
            run = create_run(
                product="restaurant",
                suite="unit-suite",
                output_root=output_root,
                run_id="2026-09-16T010203Z-test",
                metadata={"dataset_version": "v1"},
            )
            write_results(run, {"passed": True})
            write_summary(run, "# Unit suite")

            manifest = json.loads(run.manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(manifest["product"], "restaurant")
            self.assertEqual(manifest["metadata"]["dataset_version"], "v1")
            self.assertTrue(run.results_path.exists())
            self.assertTrue(run.summary_path.exists())

            with self.assertRaises(FileExistsError):
                create_run(
                    product="restaurant",
                    suite="unit-suite",
                    output_root=output_root,
                    run_id="2026-09-16T010203Z-test",
                )
