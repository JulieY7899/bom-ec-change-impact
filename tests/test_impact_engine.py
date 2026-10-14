from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from bom_change_assistant.demo import load_demo_snapshot
from bom_change_assistant.impact_engine import ImpactEngine, ImpactError
from bom_change_assistant.models import ChangeRequest
from bom_change_assistant.repository import Repository
from bom_change_assistant.summaries import markdown_report


class ImpactEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.database = Path(self.temp_dir.name) / "test.sqlite3"
        self.repository = Repository(self.database)
        load_demo_snapshot(self.repository)
        self.engine = ImpactEngine(self.repository)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_multilevel_trace_and_order_linkage(self) -> None:
        result = self.engine.analyze(
            ChangeRequest(
                change_type="replace",
                old_component="COMP-001",
                new_component="COMP-NEW",
                effective_date="2026-08-01",
            )
        )
        self.assertEqual(result["metrics"]["direct_parents"], 3)
        self.assertEqual(result["metrics"]["top_materials"], 2)
        self.assertEqual(result["metrics"]["paths"], 3)
        self.assertEqual(result["metrics"]["sales_order_items"], 2)
        self.assertEqual(result["metrics"]["production_orders"], 1)
        self.assertEqual(result["risk"]["level"], "高")
        self.assertTrue(all(path["evidence"] for path in result["paths"]))

    def test_completed_delivered_production_orders_are_excluded_from_manufacturing_scope(self) -> None:
        with self.repository.connect() as connection:
            connection.execute(
                "UPDATE production_orders SET status_category='已释放/执行中' WHERE status_category='已创建'"
            )
            row = connection.execute(
                "SELECT production_order_key FROM production_orders ORDER BY production_order_key LIMIT 1"
            ).fetchone()
            connection.execute(
                "UPDATE production_orders SET status_category='已完成' WHERE production_order_key=?",
                (row["production_order_key"],),
            )
            connection.execute(
                """
                INSERT INTO production_order_metrics(
                    production_order_key, order_quantity, delivered_quantity,
                    confirmed_quantity
                ) VALUES (?, 10, 10, 10)
                """,
                (row["production_order_key"],),
            )
        result = self.engine.analyze(
            ChangeRequest(
                change_type="replace",
                old_component="COMP-001",
                new_component="COMP-NEW",
                effective_date="2026-08-01",
            )
        )
        self.assertEqual(result["metrics"]["production_orders"], 1)
        self.assertNotIn("完工未交付", result["manufacturing_impact"]["stage_counts"])
        with self.repository.connect() as connection:
            connection.execute(
                "UPDATE production_order_metrics SET delivered_quantity=0 WHERE production_order_key=?",
                (row["production_order_key"],),
            )
        result = self.engine.analyze(
            ChangeRequest(
                change_type="replace",
                old_component="COMP-001",
                new_component="COMP-NEW",
                effective_date="2026-08-01",
            )
        )
        self.assertEqual(result["metrics"]["production_orders"], 2)
        self.assertEqual(
            result["manufacturing_impact"]["stage_counts"]["完工未交付"], 1
        )

    def test_effectivity_boundary_and_out_of_range(self) -> None:
        at_start = self.engine.analyze(
            ChangeRequest(
                change_type="remove",
                old_component="COMP-001",
                effective_date="2025-01-01",
            )
        )
        self.assertEqual(at_start["metrics"]["paths"], 3)
        expired = self.engine.analyze(
            ChangeRequest(
                change_type="remove",
                old_component="COMP-001",
                effective_date="2100-01-01",
            )
        )
        self.assertEqual(expired["metrics"]["paths"], 0)
        self.assertIn(
            "NO_EFFECTIVE_USE", {warning["code"] for warning in expired["warnings"]}
        )

    def test_downstream_trace_uses_source_material_numbers(self) -> None:
        result = self.engine.analyze(
            ChangeRequest(
                change_type="quantity",
                old_component="ASM-100",
                old_quantity=1,
                new_quantity=2,
                effective_date="2026-08-01",
            )
        )
        self.assertEqual(result["direction"]["value"], "both")
        downstream = result["directions"]["downstream"]
        self.assertEqual(downstream["metrics"]["direct_nodes"], 2)
        self.assertEqual(downstream["metrics"]["all_nodes"], 2)
        self.assertEqual(downstream["metrics"]["paths"], 2)
        self.assertTrue(
            all(
                node["alias"] in {"ASM-100", "COMP-001", "COMP-002"}
                for path in downstream["paths"]
                for node in path["node_details"]
            )
        )
        self.assertTrue(
            all(
                path["node_details"][-1]["role"] == "末端物料"
                for path in downstream["paths"]
            )
        )

    def test_upstream_path_roles_keep_every_parent_between_start_and_top(self) -> None:
        result = self.engine.analyze(
            ChangeRequest(
                change_type="replace",
                old_component="COMP-001",
                new_component="COMP-NEW",
                effective_date="2026-08-01",
            )
        )
        for path in result["directions"]["upstream"]["paths"]:
            roles = [node["role"] for node in path["node_details"]]
            self.assertEqual(roles[0], "变更料号")
            self.assertEqual(roles[-1], "顶层成品")
            self.assertTrue(all(role == "上层父件" for role in roles[1:-1]))

    def test_add_requires_known_target_parent(self) -> None:
        with self.assertRaises(ImpactError):
            self.engine.analyze(
                ChangeRequest(
                    change_type="add",
                    new_component="COMP-NEW",
                    effective_date="2026-08-01",
                )
            )

    def test_effectivity_change_records_old_and_new_dates(self) -> None:
        result = self.engine.analyze(
            ChangeRequest(
                change_type="effectivity",
                old_component="COMP-001",
                old_effective_date="2025-01-01",
                new_effective_date="2026-08-01",
            )
        )
        self.assertEqual(result["change"]["type_label"], "改变生效日期")
        self.assertEqual(result["change"]["old_effective_date"], "2025-01-01")
        self.assertEqual(result["change"]["new_effective_date"], "2026-08-01")
        self.assertEqual(result["direction"]["value"], "both")
        self.assertIn(
            "EFFECTIVITY_SHIFT",
            {warning["code"] for warning in result["warnings"]},
        )

    def test_component_effectivity_date_can_be_auto_filled(self) -> None:
        effectivity = self.repository.material_effectivity("COMP-001")
        self.assertEqual(effectivity["material"], "COMP-001")
        self.assertEqual(effectivity["default_date"], "2025-01-01")
        self.assertEqual(effectivity["dates"][0]["usage_count"], 3)
        self.assertFalse(effectivity["ambiguous"])

    def test_new_component_links_master_and_sourcing_evidence(self) -> None:
        result = self.engine.analyze(
            ChangeRequest(
                change_type="replace",
                old_component="COMP-001",
                new_component="COMP-NEW",
                effective_date="2026-08-01",
            )
        )
        validation = result["material_validation"]
        checks = {item["code"]: item["status"] for item in validation["checks"]}
        self.assertEqual(checks["GENERAL_MASTER"], "通过")
        self.assertEqual(checks["PLANT_MASTER"], "通过")
        self.assertEqual(checks["PROCUREMENT_ROUTE"], "通过")
        self.assertEqual(checks["SOURCE_LIST"], "通过")
        self.assertEqual(checks["QUALITY_CERTIFICATION"], "待补证")

    def test_change_log_preserves_per_item_ecn_and_bom_version(self) -> None:
        self.repository.save_change_log(
            {
                "log_id": "CL-TEST",
                "draft_id": "DR-TEST",
                "analysis_id": "AN-TEST",
                "created_at": "2026-07-25T00:00:00+00:00",
                "change_type": "remove",
                "change_type_label": "删除组件",
                "ecn": "ECN-TEST-001",
                "bom_version": "V03",
                "target_material": "MAT-0001",
                "effective_date": "2026-08-01",
                "old_effective_date": "",
                "new_effective_date": "",
                "risk_level": "中",
                "upstream_paths": 2,
                "downstream_paths": 0,
                "top_materials": 1,
                "material_validation_status": "不适用",
                "summary": "测试日志",
            }
        )
        log = self.repository.change_logs(1)[0]
        self.assertEqual(log["ecn"], "ECN-TEST-001")
        self.assertEqual(log["bom_version"], "V03")

    def test_ecn_sequence_starts_after_example_three(self) -> None:
        defaults = self.repository.draft_defaults()
        self.assertEqual(defaults["next_ecn"], "ECN-000004")
        self.assertEqual(defaults["default_bom_version"], "BOM-000001")
        self.assertEqual(
            self.repository.reserve_ecns(2),
            ["ECN-000004", "ECN-000005"],
        )
        self.assertEqual(
            self.repository.draft_defaults()["next_ecn"],
            "ECN-000006",
        )
        self.assertEqual(
            self.repository.draft_defaults()["default_bom_version"],
            "BOM-000001",
        )

    def test_report_contains_evidence_and_limits(self) -> None:
        result = self.engine.analyze(
            ChangeRequest(
                change_type="quantity",
                old_component="COMP-001",
                old_quantity=1,
                new_quantity=2,
                effective_date="2026-08-01",
            )
        )
        report = markdown_report(result)
        self.assertIn("UP-001", report)
        self.assertIn("涉及下层物料：变更料号 → 末端物料", report)
        upstream_path = result["directions"]["upstream"]["paths"][0]
        expected_upstream_display = " → ".join(
            f"{node['alias']}（"
            + (
                "顶层成品 · L0"
                if node["role"] == "顶层成品"
                else f"{node['role']} · L{index}"
            )
            + "）"
            for index, node in enumerate(reversed(upstream_path["node_details"]))
        )
        self.assertIn(expected_upstream_display, report)
        self.assertIn("待确认事项", report)
        self.assertIn("正式审批、发布和 ERP 维护仍需按业务流程完成", report)

    def test_planning_scenario_recommends_run_in_for_old_material_excess(self) -> None:
        result = self.engine.analyze(
            ChangeRequest(
                change_type="replace",
                old_component="COMP-001",
                new_component="COMP-NEW",
                effective_date="2026-08-01",
                old_on_hand=80,
                old_in_transit=10,
                old_open_po=0,
                old_liability=0,
                old_consumable_demand=60,
                new_on_hand=40,
                new_in_transit=30,
                new_open_po=30,
                new_required_demand=100,
            )
        )
        planning = result["planning_impact"]
        self.assertTrue(planning["scenario"]["complete"])
        self.assertEqual(planning["scenario"]["old_excess"], 30)
        self.assertEqual(planning["scenario"]["new_gap"], 0)
        self.assertEqual(planning["recommendation"], "Run-in Change")

    def test_manufacturing_impact_uses_disposition_for_action_list(self) -> None:
        result = self.engine.analyze(
            ChangeRequest(
                change_type="replace",
                old_component="COMP-001",
                new_component="COMP-NEW",
                effective_date="2026-08-01",
                disposition="需返工",
            )
        )
        manufacturing = result["manufacturing_impact"]
        self.assertEqual(manufacturing["disposition"], "需返工")
        self.assertEqual(
            len(manufacturing["actions"]),
            result["metrics"]["production_orders"],
        )
        self.assertTrue(
            all(
                action["disposition"] == "需返工"
                for action in manufacturing["actions"]
            )
        )
        self.assertTrue(
            any("返工" in action["action"] for action in manufacturing["actions"])
        )

    def test_formal_disposition_code_records_scope_owner_and_approval(self) -> None:
        result = self.engine.analyze(
            ChangeRequest(
                change_type="replace",
                old_component="COMP-001",
                new_component="COMP-NEW",
                effective_date="2026-08-01",
                disposition_code="REWORK",
                disposition_scope="WIP/完工品",
                disposition_owner="制造工程",
                disposition_status="质量会签中",
                disposition_note="等待返工路线确认",
            )
        )
        manufacturing = result["manufacturing_impact"]
        self.assertEqual(manufacturing["disposition_code"], "REWORK")
        self.assertEqual(manufacturing["disposition_scope"], "WIP/完工品")
        self.assertEqual(manufacturing["disposition_owner"], "制造工程")
        self.assertEqual(manufacturing["approval_status"], "质量会签中")
        self.assertTrue(manufacturing["approval_required"])
        self.assertEqual(manufacturing["note"], "等待返工路线确认")
        self.assertNotIn("manufacturing_constraint", result["planning_impact"])

    def test_use_up_disposition_is_read_by_planning_as_run_in_constraint(self) -> None:
        result = self.engine.analyze(
            ChangeRequest(
                change_type="replace",
                old_component="COMP-001",
                new_component="COMP-NEW",
                effective_date="2026-08-01",
                disposition_code="USE_UP",
                disposition_status="已批准",
            )
        )
        self.assertNotIn("manufacturing_constraint", result["planning_impact"])

    def test_manufacturing_recommends_code_but_marks_it_for_confirmation(self) -> None:
        result = self.engine.analyze(
            ChangeRequest(
                change_type="replace",
                old_component="COMP-001",
                new_component="COMP-NEW",
                effective_date="2026-08-01",
            )
        )
        recommendation = result["manufacturing_impact"]["recommended_disposition"]
        self.assertIn(
            recommendation["code"], {"HOLD_REVIEW", "USE_AS_IS", "USE_UP"}
        )
        self.assertTrue(recommendation["requires_confirmation"])
        self.assertEqual(recommendation["selection_status"], "待人工确认")

    def test_database_keeps_business_ids_but_not_sensitive_text(self) -> None:
        result = self.engine.analyze(
            ChangeRequest(
                change_type="remove",
                old_component="COMP-001",
                effective_date="2026-08-01",
                note="PRIVATE-NOTE-SHOULD-NOT-PERSIST",
            )
        )
        self.assertEqual(result["change"]["note"], "已提供（未保存）")
        database_bytes = self.database.read_bytes()
        for business_identifier in (
            b"COMP-001",
            b"SALES-DEMO-ALPHA",
            b"PRODUCTION-DEMO-ALPHA",
            b"CHANGE-DEMO-ALPHA",
        ):
            self.assertIn(business_identifier, database_bytes)
        for sensitive_text in (
            b"PRIVATE-NOTE-SHOULD-NOT-PERSIST",
            b"SUPPLIER-DEMO",
            b"CFG-DEMO-A",
        ):
            self.assertNotIn(sensitive_text, database_bytes)


if __name__ == "__main__":
    unittest.main()
