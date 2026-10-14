from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bom_change_assistant.demo import load_demo_snapshot
from bom_change_assistant.impact_engine import ImpactEngine
from bom_change_assistant.models import ChangeRequest
from bom_change_assistant.repository import Repository


class DemoSnapshotTests(unittest.TestCase):
    def test_synthetic_demo_supports_impact_analysis(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repository = Repository(Path(temp_dir) / "demo.sqlite3")
            stats = load_demo_snapshot(repository)

            self.assertEqual(stats["bom_edges"], 9)
            self.assertEqual(stats["sales_items"], 2)
            self.assertEqual(stats["production_orders"], 2)

            result = ImpactEngine(repository).analyze(
                ChangeRequest(
                    change_type="replace",
                    old_component="COMP-001",
                    new_component="COMP-NEW",
                    effective_date="2026-08-15",
                )
            )
            self.assertEqual(result["metrics"]["top_materials"], 2)
            self.assertEqual(result["metrics"]["upstream_paths"], 3)


if __name__ == "__main__":
    unittest.main()
