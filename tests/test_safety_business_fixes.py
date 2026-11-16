from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from bom_change_assistant.ai import AIUnavailable, explain_with_openai
from bom_change_assistant.demo import load_demo_snapshot
from bom_change_assistant.impact_checks import quantity_check, effectivity_comparison
from bom_change_assistant.impact_engine import ImpactEngine
from bom_change_assistant.models import ChangeRequest
from bom_change_assistant.repository import Repository
from bom_change_assistant.web import _local_request_allowed


class SecurityAndBusinessFixTests(unittest.TestCase):
    def test_external_ai_is_hard_disabled(self) -> None:
        with patch.dict("os.environ", {"OPENAI_API_KEY": "synthetic-test-key"}):
            with patch("urllib.request.urlopen") as outbound:
                with self.assertRaises(AIUnavailable):
                    explain_with_openai(
                        {"business_id": "SYNTHETIC-NEVER-SEND"}, None,
                        api_key="synthetic-test-key",
                    )
                outbound.assert_not_called()

    def test_local_origin_check(self) -> None:
        self.assertTrue(_local_request_allowed("127.0.0.1:8765", "http://127.0.0.1:8765"))
        self.assertTrue(_local_request_allowed("localhost:8765"))
        self.assertFalse(_local_request_allowed("evil.example:8765"))
        self.assertFalse(_local_request_allowed("localhost:8765", "https://evil.example"))
        self.assertFalse(_local_request_allowed("localhost:8765", "http://localhost:9999"))

    def test_quantity_deltas_propagate_via_parent_usage(self) -> None:
        paths = [
            {"path_id": "P-1", "top_material": "FG-X", "evidence": [{"quantity": 2}, {"quantity": 3}]},
            {"path_id": "P-2", "top_material": "FG-X", "evidence": [{"quantity": 2}, {"quantity": 4}]},
        ]
        result = quantity_check(paths, 2, 5)
        self.assertEqual(result["status"], "已完成理论测算")
        self.assertEqual(result["by_top"][0]["delta_per_top_unit"], 21)

    def test_quantity_conflicting_input_is_not_trusted(self) -> None:
        paths = [{"path_id": "P-1", "top_material": "FG-X", "evidence": [{"quantity": 4}]}]
        result = quantity_check(paths, 2, 5)
        self.assertEqual(result["status"], "需人工复核")
        self.assertEqual(len(result["pending_paths"]), 1)
        self.assertFalse(result["by_top"])

    def test_nonreplacement_cannot_trigger_hard_cut(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = Repository(Path(root) / "db.sqlite3")
            load_demo_snapshot(repo)
            result = ImpactEngine(repo).analyze(ChangeRequest(
                change_type="quantity", old_component="COMP-001",
                old_quantity=1, new_quantity=2, effective_date="2026-08-01",
                old_on_hand=0, old_in_transit=0, old_open_po=0,
                old_consumable_demand=0, new_on_hand=100,
                new_in_transit=0, new_open_po=0, new_required_demand=100,
            ))
            self.assertEqual(result["planning_impact"]["recommendation_code"], "NOT_APPLICABLE")

    def test_effectivity_diff_is_a_date_filter_not_version_migration(self) -> None:
        before = {"paths": [{"direction": "upstream", "nodes": ["A", "B"]}], "top_materials": ["B"], "warnings": []}
        after = {"paths": [{"direction": "upstream", "nodes": ["A", "C"]}], "top_materials": ["C"], "warnings": []}
        check = effectivity_comparison(before, after)
        self.assertEqual((check["added_paths"], check["removed_paths"]), (1, 1))
        self.assertIn("不等价于", check["boundary"])

    def test_unrelated_manufacturing_order_is_not_matched_by_sales_header_only(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = Repository(Path(root) / "db.sqlite3")
            load_demo_snapshot(repo)
            key = repo.privacy_key()
            with repo.connect() as connection:
                sales_order = connection.execute(
                    "SELECT sales_order_key FROM sales_items ORDER BY sales_order_key LIMIT 1"
                ).fetchone()["sales_order_key"]
                connection.execute(
                    """INSERT INTO production_orders
                       (production_order_key, source_id, source_row, material_key,
                        sales_order_key, sales_item_key, status_category)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        key.digest("production_order", "UNRELATED-999"), "SRC-001", 999,
                        key.digest("material", "NONMATCHING-999"),
                        sales_order, None, "已释放/执行中",
                    ),
                )
            result = ImpactEngine(repo).analyze(ChangeRequest(
                change_type="remove", old_component="COMP-001",
                effective_date="2026-08-01",
            ))
            self.assertNotIn(
                999,
                [row["source_row"] for row in result["production_orders"]],
            )

    def test_batch_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repo = Repository(Path(root) / "db.sqlite3")
            for draft in ("DR-OLD", "DR-NEW"):
                analysis_id = f"AN-{draft}"
                repo.save_analysis(analysis_id, {"draft_id": draft}, "report")
                repo.save_change_log({
                    "log_id": f"CL-{draft}", "draft_id": draft, "analysis_id": analysis_id,
                    "created_at": "2026-11-06T00:00:00+00:00", "change_type": "remove",
                    "change_type_label": "删除", "ecn": f"ECN-{draft}",
                    "bom_version": "BOM-000001", "target_material": "X",
                    "effective_date": "2026-11-06", "risk_level": "低",
                    "upstream_paths": 0, "downstream_paths": 0,
                    "top_materials": 0, "material_validation_status": "不适用", "summary": "test"
                })
            result = repo.analysis_results_for_bom_version("BOM-000001", "DR-NEW")
            self.assertEqual(len(result), 1)
            self.assertEqual(result[0]["draft_id"], "DR-NEW")


if __name__ == "__main__":
    unittest.main()
