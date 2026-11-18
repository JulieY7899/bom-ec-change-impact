"""B22-DRAFT01 synthetic checks through the real local HTTP and XLSX entry points."""
from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote

from bom_change_assistant.web import AppState, Handler
from tests.test_procurement import workbook

COLUMNS = (
    "engineering_record", "product", "plant", "engineering_version", "row_id", "parent",
    "component", "parent_row_id", "quantity", "unit", "valid_from", "valid_to",
    "order_kind", "order_id", "order_item", "order_status", "order_relevant_date",
    "purchased", "received", "row_application", "物料描述",
)
SCOPE = dict(product="FG-T1", plant="PLANT-T", engineering_version="ENG-V2")
QUERY = dict(SCOPE, analysis_mode="engineering", bom_version="BOM-000001",
             change_type="replace", target_bom_row="V2-L2", old_component="PART-OLD",
             new_component="PART-NEW", effective_date="2026-12-01")
OLD_BOM = [
    ["物料编码", "直接父物料", "组件物料", "Valid-from", "Valid-to", "用量", "单位", "工厂"],
    ["FG-T1", "FG-T1", "PART-OLD", "2026-01-01", "2026-12-31", 9, "EA", "PLANT-T"],
]


def record(kind, **fields):
    return {**SCOPE, "engineering_record": kind, **fields}


def row(row_id="V2-L2", **fields):
    return record("BOM_ROW", **{
        "row_id": row_id, "parent": "FG-T1", "component": "PART-OLD",
        "quantity": 1, "unit": "EA", **fields,
    })


def order(order_id="SO-22", order_item="00010", **fields):
    return record("ORDER_REFERENCE", **{
        "order_kind": "SALES", "order_id": order_id, "order_item": order_item,
        "order_status": "开放", "order_relevant_date": "2026-12-01",
        "purchased": False, "received": False, **fields,
    })


def application(order_id="SO-22", order_item="00010", state="APPLIES", **fields):
    return record("ORDER_ROW", **{
        "order_kind": "SALES", "order_id": order_id, "order_item": order_item,
        "row_id": "V2-L2", "row_application": state, **fields,
    })


def base(**fields):
    return [record("VERSION"), row(**fields), order(), application()]


def sheet(rows):
    return [list(COLUMNS), *[[item.get(key) for key in COLUMNS] for item in rows]]


def original_input(case):
    """Adapt the synthetic original case without filling absent business evidence."""
    version_for_order = {}
    records = []
    for version in case["engineering_versions"]:
        scope = dict(product=version["product"], plant="", engineering_version=version["id"])
        version_for_order[version["id"]] = scope
        records.append(record("VERSION", **scope))
        for edge in version["rows"]:
            records.append(record("BOM_ROW", **scope, **edge))
    for reference in case["order_references"]:
        records.append(record(
            "ORDER_REFERENCE", **version_for_order[reference["version"]], order_kind="ORDER_BOM",
            order_id=reference["id"], purchased=reference.get("purchased"),
        ))
    change = case["change"]
    return records, {
        **QUERY, **version_for_order[change["engineering_version"]],
        "change_type": change["type"], "old_component": change["material"],
        "new_component": change["new_material"], "effective_date": change["effective_date"],
        "target_bom_row": change["target_bom_row"],
    }


class EngineeringAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        state = AppState(self.root / "active.sqlite3")
        self.state = state
        self.addCleanup(state.abort_import)
        handler = type("EngineeringHandler", (Handler,), {"state": state})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=self.server.serve_forever, args=(0.02,), daemon=True)
        thread.start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.server.shutdown)
        self.sequence = 0

    def request(self, path, data=None, *, method="POST", status=200):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            body = None if method == "GET" else data if isinstance(data, bytes) else json.dumps(data or {}).encode()
            connection.request(method, path, body)
            response = connection.getresponse()
            raw = response.read()
            content_type = response.getheader("Content-Type", "")
            value = json.loads(raw) if "json" in content_type else raw.decode()
            self.assertEqual(response.status, status, value)
            return value
        finally:
            connection.close()

    def upload(self, *sheets):
        self.sequence += 1
        path = self.root / f"fixture-{self.sequence}.xlsx"
        workbook(path, sheets)
        return self.request("/api/import/file", path.read_bytes())

    def install(self, records, *other_sheets):
        self.request("/api/snapshot/start")
        self.upload(sheet(records), *other_sheets)
        self.request("/api/snapshot/finish")

    def run_query(self, **changes):
        result = self.request("/api/analyze", {**QUERY, **changes})
        saved = self.request("/api/report.md?analysis_id=" + quote(result["result"]["analysis_id"]), method="GET")
        self.assertEqual(saved, result["report"])
        return result["result"]["engineering_impact"]["checks"][0], saved

    def orders(self, result):
        return [item["order_id"] for item in result["version_reference_matches"]]

    def catalog(self):
        return self.request("/api/engineering-versions", method="GET")["versions"]

    def test_P22_01_exact_original_scope(self):
        version_1 = dict(engineering_version="ENG-V1", plant="")
        version_2 = dict(plant="")
        records = [record("VERSION", **version_1), record("VERSION", **version_2),
                   row("V1-L1", **version_1, quantity=2), row("V2-L1", **version_2, quantity=2),
                   row(**version_2)]
        for number, scope in enumerate((version_1, version_2), 1):
            records.append(record("ORDER_REFERENCE", **scope, order_kind="ORDER_BOM",
                                  order_id=f"ORDER-V{number}", purchased=number == 1))
        self.install(records)
        before = self.catalog()
        result, report = self.run_query(plant="")
        self.assertEqual(self.orders(result), ["ORDER-V2"])
        self.assertEqual([item["order_id"] for item in result["excluded_orders"]], ["ORDER-V1"])
        self.assertEqual(result["target"]["row_id"], "V2-L2")
        self.assertFalse(result["automatic_version_migration"])
        self.assertIsNone(result["approved_action"])
        self.assertIn("ORDER-V2 · 待核对", report)
        self.assertEqual(self.catalog(), before)

    def test_P22_02_baseline(self):
        self.install(base())
        result, report = self.run_query()
        self.assertEqual(self.orders(result), ["SO-22"])
        self.assertEqual([edge["row_id"] for edge in result["path"]], ["V2-L2"])
        self.assertEqual(result["target"]["quantity"], 1)
        self.assertEqual(result["version_reference_matches"][0]["review_status"], "待业务评审")
        self.assertIn("PART-NEW", report)

    def test_P22_03_same_revision_and_position_for_other_product(self):
        self.install(base() + [record("VERSION", product="FG-X"),
                               row(parent="FG-X", product="FG-X"), order("SO-X", product="FG-X")])
        result, _ = self.run_query()
        self.assertEqual(self.orders(result), ["SO-22"])
        self.assertEqual(result["excluded_orders"], [])

    def test_P22_04_factory_isolation(self):
        other = dict(plant="PLANT-X")
        self.install(base() + [record("VERSION", **other), row(**other),
                               order("SO-X", **other), order("SO-MISSING", plant="")])
        result, _ = self.run_query()
        self.assertEqual(self.orders(result), ["SO-22"])
        self.assertEqual(result["excluded_orders"][0]["order_id"], "SO-X")
        self.assertEqual(result["pending_references"][0]["order_id"], "SO-MISSING")

    def test_P22_05_same_material_multiple_rows(self):
        v1 = dict(engineering_version="ENG-V1")
        self.install(base() + [row("V2-L1", quantity=2), record("VERSION", **v1), row(**v1)])
        result, _ = self.run_query()
        self.assertEqual([(edge["row_id"], edge["quantity"]) for edge in result["path"]], [("V2-L2", 1)])

    def test_P22_06_exact_mode_refuses_invalid_targets(self):
        self.install(base(), OLD_BOM)
        cases = {
            "product": ("", "product"),
            "engineering_version": ("", "engineering_version"),
            "target_bom_row": ("", "目标 BOM 行号"),
            "old_component": ("OTHER", "原组件"),
            "target_parent": ("OTHER", "父件"),
        }
        for field, (value, message) in cases.items():
            with self.subTest(field=field):
                out = self.request("/api/analyze", {**QUERY, field: value}, status=400)
                self.assertIn(message, out["error"])
                self.assertNotIn("result", out)
        self.request("/api/analyze", {**QUERY, "target_bom_row": "X"}, status=400)

    def test_P22_07_inclusive_effectivity_entire_path(self):
        window = dict(valid_from="2026-12-01", valid_to="2026-12-31")
        upper = row("UP", component="SUB-1", **window)
        records = base(parent="SUB-1", parent_row_id="UP", **window)
        self.install(records + [upper])
        for date, expected in (("2026-11-30", False), ("2026-12-01", True),
                               ("2026-12-31", True), ("2027-01-01", False)):
            with self.subTest(date=date):
                result, _ = self.run_query(effective_date=date)
                self.assertEqual(bool(result["version_reference_matches"]), expected)
        self.install(records + [{**upper, "valid_from": None, "valid_to": "2026-11-30"}])
        self.assertIn("未生效", self.run_query()[0]["path_issue"])

    def test_P22_08_open_and_invalid_dates(self):
        self.install(base())
        self.assertEqual(self.orders(self.run_query()[0]), ["SO-22"])
        for dates in (dict(valid_from="BAD"), dict(valid_from="2026-12-10", valid_to="2026-11-30")):
            with self.subTest(dates=dates):
                self.install(base(**dates) + [row("OTHER")])
                out, report = self.run_query()
                self.assertEqual(self.orders(out), [])
                self.assertIn("无效或倒置生效日期", report)

    def test_P22_09_path_points_to_one_assembly_occurrence(self):
        self.install(base(parent="SUB-1", parent_row_id="UP-2") + [
            row("UP-1", component="SUB-1"), row("UP-2", component="SUB-1"),
        ])
        result, _ = self.run_query()
        self.assertEqual([edge["row_id"] for edge in result["path"]], ["UP-2", "V2-L2"])
        self.assertEqual(self.orders(result), ["SO-22"])

    def test_P22_10_unique_parent_inference(self):
        records = base(parent="SUB-1") + [row("UP-1", component="SUB-1")]
        self.install(records + [row("UP-2", component="SUB-1")])
        self.assertIn("位置不唯一", self.run_query()[0]["path_issue"])
        self.install(records)
        self.assertEqual(self.orders(self.run_query()[0]), ["SO-22"])

    def test_P22_11_orphan_and_cycle_are_review_items(self):
        self.install(base(parent="SUB-1") + [row("ROOT")])
        result, _ = self.run_query()
        self.assertIn("断链", result["path_issue"])
        self.assertEqual(result["path"], [])
        self.install(base(parent="SUB-1", parent_row_id="UP") + [
            row("UP", parent="PART-OLD", component="SUB-1", parent_row_id="V2-L2"),
            row("ROOT"),
        ])
        result, _ = self.run_query()
        self.assertIn("环路", result["path_issue"])
        self.assertEqual(self.orders(result), [])

    def test_P22_12_sales_items_and_production_are_independent(self):
        v1 = dict(engineering_version="ENG-V1")
        self.install(base() + [
            record("VERSION", **v1), order("SO-22", "00020", **v1),
            order("MO-1", "", order_kind="PRODUCTION", **v1),
            order("MO-2", "", order_kind="PRODUCTION"),
        ])
        result, _ = self.run_query()
        self.assertEqual(self.orders(result), ["SO-22", "MO-2"])
        self.assertEqual([item["order_item"] for item in result["version_reference_matches"]], ["00010", ""])

    def test_P22_13_bad_and_conflicting_references(self):
        v1 = dict(engineering_version="ENG-V1")
        self.install(base() + [record("VERSION", **v1),
                               order("SO-EMPTY", engineering_version=""),
                               order("SO-V9", engineering_version="ENG-V9"),
                               order("SO-DUP", **v1), order("SO-DUP")])
        result, report = self.run_query()
        self.assertEqual(self.orders(result), ["SO-22"])
        reasons = {item["order_id"]: item["reason"] for item in result["pending_references"]}
        self.assertIn("缺少", reasons["SO-EMPTY"])
        self.assertIn("未导入", reasons["SO-V9"])
        self.assertIn("多个", reasons["SO-DUP"])
        self.assertIn("ENG-V1、ENG-V2", report)
        self.assertEqual(len(result["pending_references"][-1]["evidence"]), 2)

    def test_P22_14_order_configuration_excludes_the_exact_row(self):
        self.install(base() + [row("V2-L1"), order("SO-X"), application("SO-X", state="EXCLUDED"),
                               application("SO-X", row_id="V2-L1")])
        out, _ = self.run_query()
        self.assertEqual(self.orders(out), ["SO-22", "SO-X"])
        self.assertEqual([item["line_candidate"] for item in out["version_reference_matches"]], [True, False])

    def test_P22_15_configuration_unknown_or_conflicting(self):
        self.install(base() + [order("SO-MISSING"), order("SO-UNKNOWN"), order("SO-CONFLICT"),
                               application("SO-UNKNOWN", state="UNKNOWN"),
                               application("SO-CONFLICT"), application("SO-CONFLICT", state="EXCLUDED")])
        result, report = self.run_query()
        for item in result["version_reference_matches"][1:]:
            self.assertEqual(item["review_status"], "待核对")
            self.assertFalse(item["line_candidate"])
        self.assertIn("适用性证据冲突", report)
        self.assertEqual(len(result["version_reference_matches"][-1]["application_evidence"]), 2)

    def test_P22_16_status_and_order_dates(self):
        inputs = [
            ("开放", "2026-12-01", "待业务评审"),
            ("已释放", "2026-12-02", "待业务评审"),
            ("执行中", "2026-11-30", "待核对"),
            ("完工待交付", "2026-12-02", "待业务评审"),
            ("未知", "2026-12-02", "待核对"),
            ("开放", None, "待核对"),
            ("已关闭", "2026-12-02", "仅保留引用证据"),
            ("已取消", "2026-12-02", "仅保留引用证据"),
            ("已拒绝", "2026-12-02", "仅保留引用证据"),
        ]
        records = [record("VERSION"), row()]
        for index, (status, day, _) in enumerate(inputs):
            key = f"SO-{index}"
            records += [order(key, order_status=status, order_relevant_date=day), application(key)]
        self.install(records)
        result, _ = self.run_query()
        self.assertEqual([m["review_status"] for m in result["version_reference_matches"]],
                         [expected for _, _, expected in inputs])

    def test_P22_17_procurement_review_and_conflict(self):
        records = base() + [
            order("SO-P", purchased=True), order("SO-R", purchased=None, received=True),
            order("SO-U", purchased=None, received=None), order("SO-D", purchased=False, received=True),
        ]
        self.install(records, [
            ["物料", "工厂", "库位", "采购总量", "已到货量", "剩余采购承诺", "可取消"],
            ["PART-OLD", "PLANT-T", "STORE-T", 50, 20, 30, "未知"],
        ])
        result, report = self.run_query(planning_plant="PLANT-T", storage_location="STORE-T")
        matches = {item["order_id"]: item for item in result["version_reference_matches"]}
        for key in ("SO-P", "SO-R", "SO-D"):
            self.assertEqual(matches[key]["review_owners"], ["工程", "采购", "计划"])
        self.assertEqual(matches["SO-U"]["purchased"], "未知")
        self.assertEqual(matches["SO-U"]["received"], "未知")
        self.assertIn("订单采购证据冲突", report)
        self.assertIn("累计净到货量：20", report)

    def test_P22_18_unit_delta_and_missing_inputs(self):
        records = base(parent="SUB-1", parent_row_id="UP")
        self.install(records + [row("UP", component="SUB-1", quantity=2)])
        request = dict(change_type="quantity", old_quantity=1, new_quantity=3)
        result, report = self.run_query(**request)
        self.assertEqual(result["quantity_impact"]["by_top"][0]["delta_per_top_unit"], 4)
        self.assertIn("每单位成品 4 EA", report)
        self.assertEqual(self.run_query(**{**request, "old_quantity": 2})[0]["quantity_impact"]["by_top"], [])
        self.install(records + [row("UP", component="SUB-1", quantity=None)])
        unknown, _ = self.run_query(**request)
        self.assertEqual(unknown["quantity_impact"]["by_top"], [])
        self.assertEqual(self.orders(unknown), ["SO-22"])
        self.install(records + [row("UP", component="SUB-1", quantity=2, unit="KG")])
        self.assertEqual(self.run_query(**request)[0]["quantity_impact"]["by_top"], [])
        self.install(base(parent="SUB-1", parent_row_id="UP", quantity=0) +
                     [row("UP", component="SUB-1", quantity=2)])
        zero, _ = self.run_query(**{**request, "old_quantity": 0})
        self.assertEqual(zero["quantity_impact"]["by_top"][0]["delta_per_top_unit"], 6)

    def test_P22_19_removal_and_unsupported_version_change_types(self):
        self.install(base())
        before = self.catalog()
        result, report = self.run_query(change_type="remove", new_component="")
        self.assertEqual(self.orders(result), ["SO-22"])
        self.assertIn("删除组件", report)
        self.assertEqual(self.catalog(), before)
        for kind in ("add", "effectivity"):
            out = self.request("/api/analyze", {**QUERY, "change_type": kind}, status=400)
            self.assertIn("仅支持现有行", out["error"])

    def test_P22_20_exact_text_numbers_and_duplicate_evidence(self):
        revision = dict(engineering_version="01")
        records = [record("VERSION", **revision), row("0010", **revision), order(**revision)]
        self.install(records + [record("VERSION", engineering_version="1"),
                                row("10", **revision), order("SO-X", engineering_version="1")],
                     sheet(records))
        check, _ = self.run_query(**revision, target_bom_row="0010")
        self.assertEqual(check["target"]["row_id"], "0010")
        self.assertEqual(check["version"]["engineering_version"], "01")
        self.assertEqual(self.orders(check), ["SO-22"])
        self.assertEqual(len(check["target"]["evidence"]), 2)
        self.assertEqual(len(check["version_reference_matches"][0]["evidence"]), 2)

    def test_P22_21_unordered_files_publish_a_version_only_snapshot(self):
        self.install(base())
        before = self.catalog()
        self.request("/api/snapshot/start", {"expected_files": 3})
        self.upload(sheet([order("SO-OTHER")]))
        self.upload(sheet([row(quantity=7)]))
        self.assertEqual(self.catalog(), before)
        self.upload(sheet([record("VERSION")]))
        self.request("/api/snapshot/finish")
        result, _ = self.run_query()
        self.assertEqual(result["target"]["quantity"], 7)
        self.assertEqual(self.orders(result), ["SO-OTHER"])
        self.assertEqual(len(list(self.root.glob("snapshot-backup-*.sqlite3"))), 2)

    def test_P22_22_old_report_survives_each_invalid_batch(self):
        self.install(base())
        _, report = self.run_query()
        versions = self.catalog()
        for outcome, invalid in (
            ("file", None), ("cancel", None), ("undefined", row(engineering_version="UNKNOWN")),
            ("parent", row(parent_row_id="UNKNOWN")), ("conflict", row(quantity=20)),
        ):
            with self.subTest(outcome=outcome):
                self.request("/api/snapshot/start", {"expected_files": 2})
                self.upload(sheet(base()))
                if outcome == "file":
                    self.request("/api/import/file", b"not an xlsx", status=400)
                elif outcome == "cancel":
                    self.request("/api/snapshot/abort")
                else:
                    self.upload(sheet([invalid]))
                    self.request("/api/snapshot/finish", status=400)
                self.assertEqual(self.request("/api/report.md", method="GET"), report)
                self.assertEqual(self.catalog(), versions)
        result, _ = self.run_query()
        self.assertEqual(self.orders(result), ["SO-22"])

    def test_P22_23_batch_keeps_each_scope_and_dedupes_orders(self):
        v1 = dict(engineering_version="ENG-V1")
        self.install(base() + [row("V2-L1"), record("VERSION", **v1), row("V1-L1", **v1),
                               order("SO-V1", **v1)])
        requests = [{**QUERY, **v1, "target_bom_row": "V1-L1"}, QUERY,
                    {**QUERY, "target_bom_row": "V2-L1"}]
        response = self.request("/api/analyze-batch", {"changes": requests})
        result = response["groups"][0]["result"]["engineering_impact"]
        self.assertEqual([item["target"]["row_id"] for item in result["checks"]],
                         ["V1-L1", "V2-L2", "V2-L1"])
        self.assertEqual(len(result["unique_orders"]), 2)
        self.assertEqual(len(result["unique_orders"][1]["ecns"]), 2)

    def test_P22_24_api_and_download_hide_ignored_sensitive_text(self):
        marker = "<script>private_descriptive_text</script>"
        self.install(base(**{"物料描述": marker}))
        result, report = self.run_query(note=marker)
        self.assertNotIn(marker, report + json.dumps(result))
        self.assertIn("工程版本", report)
        self.assertIn("V2-L2", report)
        self.assertIn("SO-22", report)
        bad = self.request("/api/analyze", {**QUERY, "new_component": marker}, status=400)
        self.assertNotIn(marker, json.dumps(bad))

    def test_P22_25_legacy_format(self):
        self.request("/api/snapshot/start")
        self.upload(OLD_BOM)
        self.request("/api/snapshot/finish")
        result = self.request("/api/analyze", {
            "bom_version": "BOM-000001", "change_type": "replace", "old_component": "PART-OLD",
            "new_component": "PART-NEW", "effective_date": "2026-12-01",
        })["result"]
        self.assertEqual(result["metrics"]["upstream_paths"], 1)
        self.assertNotIn("engineering_impact", result)
        self.assertEqual(self.catalog(), [])

    def test_P22_26_mixed_data_modes_keep_separate_scopes(self):
        self.install(base(), OLD_BOM)
        self.assertEqual(self.run_query()[0]["target"]["quantity"], 1)
        plain = {k: QUERY[k] for k in ("change_type", "old_component", "new_component", "bom_version", "effective_date")}
        result = self.request("/api/analyze-batch", {"changes": [plain, QUERY]})["groups"][0]["result"]
        self.assertEqual(result["metrics"]["upstream_paths"], 1)
        self.assertEqual(result["engineering_impact"]["checks"][0]["target"]["quantity"], 1)
        self.assertEqual(result["change_item_count"], 2)
        self.assertFalse(result["engineering_only"])

    def test_P22_27_repeat_history_and_structural_immutability(self):
        self.install(base())
        before = self.catalog()
        with self.state.repository.connect() as connection:
            references = connection.execute("SELECT content FROM engineering_evidence").fetchall()
        first = self.request("/api/analyze", QUERY)
        second = self.request("/api/analyze", QUERY)
        a, b = [item["result"]["engineering_impact"]["checks"][0] for item in (first, second)]
        self.assertEqual({k: v for k, v in a.items() if k != "ecn"},
                         {k: v for k, v in b.items() if k != "ecn"})
        for item in (first, second):
            path = "/api/report.md?analysis_id=" + quote(item["result"]["analysis_id"])
            self.assertEqual(self.request(path, method="GET"), item["report"])
        self.assertEqual(self.catalog(), before)
        with self.state.repository.connect() as connection:
            self.assertEqual(connection.execute("SELECT content FROM engineering_evidence").fetchall(), references)
