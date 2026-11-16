from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from bom_change_assistant.demo import load_demo_snapshot
from bom_change_assistant.models import ChangeRequest
from bom_change_assistant.web import AppState, Handler


class QuantityCompletenessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.state = AppState(Path(self.temp.name) / "test.sqlite3")
        load_demo_snapshot(self.state.repository)
        self.server = None
        self.thread = None

    def tearDown(self) -> None:
        if self.server is not None:
            self.server.shutdown()
            self.server.server_close()
            self.thread.join()
        self.temp.cleanup()

    def set_quantities(self, order, confirmed, delivered, *, only_first=False) -> None:
        with self.state.repository.connect() as connection:
            connection.execute("DELETE FROM production_order_metrics")
            connection.execute("DELETE FROM sales_item_metrics")
            production_filter = " WHERE source_row=2" if only_first else ""
            connection.execute(
                "INSERT INTO production_order_metrics "
                "(production_order_key, order_quantity, confirmed_quantity, delivered_quantity) "
                "SELECT production_order_key, ?, ?, ? FROM production_orders"
                + production_filter,
                (order, confirmed, delivered),
            )
            sales_filter = " WHERE source_row=2" if only_first else ""
            connection.execute(
                "INSERT INTO sales_item_metrics (sales_item_key, order_quantity) "
                "SELECT sales_item_key, ? FROM sales_items" + sales_filter,
                (order,),
            )

    def engine_result(self, component="COMP-002"):
        return self.state.engine.analyze(ChangeRequest(
            change_type="remove", old_component=component,
            effective_date="2026-08-01",
        ))

    @staticmethod
    def payload(component="COMP-002"):
        return {
            "change_type": "remove", "old_component": component,
            "effective_date": "2026-08-01", "bom_version": "BOM-000001",
        }

    def request(self, method, path, payload=None):
        if self.server is None:
            handler = type("QuantityTestHandler", (Handler,), {"state": self.state})
            self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
            self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
            self.thread.start()
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.server.server_port, timeout=5,
        )
        try:
            body = json.dumps(payload) if payload is not None else None
            connection.request(method, path, body)
            response = connection.getresponse()
            data = response.read()
            self.assertEqual(response.status, 200, data)
            return data.decode()
        finally:
            connection.close()

    def assert_report_quantity(self, report, expected):
        line = next(
            line for line in report.splitlines()
            if line.startswith("- 未完工成品数量：")
        )
        if expected is None:
            self.assertEqual(line, "- 未完工成品数量：待补数据")
        else:
            self.assertEqual(float(line.split("：", 1)[1]), expected)

    def test_progress_metrics_agree_across_engine_http_and_batch(self):
        self.set_quantities(10, 4, 2)
        direct = self.engine_result()
        single = json.loads(self.request("POST", "/api/analyze", self.payload()))
        batch = json.loads(self.request("POST", "/api/analyze-batch", {
            "changes": [self.payload(), self.payload()],
        }))
        results = [direct, single["result"], batch["groups"][0]["result"]]
        results.extend(item["result"] for item in batch["items"])
        for result in results:
            metrics = result["planning_impact"]["metrics"]
            self.assertEqual(metrics["unfinished_production_quantity"], 6)
            self.assertEqual(metrics["undelivered_production_quantity"], 8)
            self.assertEqual(metrics["remaining_production_quantity"], 6)
            self.assertEqual(metrics["active_production_orders"], 1)
        for report in (single["report"], batch["groups"][0]["report"],
                       self.request("GET", "/api/report.md")):
            self.assertIn("- 未完工成品数量：6", report)
            self.assertIn("- 待交付成品数量：8", report)
            self.assertNotIn("待交付/未完工", report)

    def test_missing_progress_operand_does_not_erase_other_metric(self):
        for confirmed, delivered, unfinished, undelivered in (
            (None, 2, None, 8), (4, None, 6, None), (10, None, 0, None),
        ):
            with self.subTest(confirmed=confirmed, delivered=delivered):
                self.set_quantities(10, confirmed, delivered)
                response = json.loads(self.request("POST", "/api/analyze", self.payload()))
                metrics = response["result"]["planning_impact"]["metrics"]
                self.assertEqual(metrics["unfinished_production_quantity"], unfinished)
                self.assertEqual(metrics["undelivered_production_quantity"], undelivered)
                for label, value in (("未完工", unfinished), ("待交付", undelivered)):
                    text = "待补数据" if value is None else str(value)
                    self.assertIn(f"- {label}成品数量：{text}", response["report"])

    def test_derived_quantities_require_both_operands(self):
        cases = [
            (None, 0, 0, None, None),
            (10, None, 0, None, 10),
            (10, 0, None, 10, None),
            (0, 0, 0, 0, 0),
        ]
        for order, confirmed, delivered, remaining, undelivered in cases:
            with self.subTest(order=order, confirmed=confirmed, delivered=delivered):
                self.set_quantities(order, confirmed, delivered)
                result = self.engine_result()
                row = result["production_orders"][0]
                self.assertEqual(row["remaining_quantity"], remaining)
                self.assertEqual(row["undelivered_quantity"], undelivered)
                self.assertEqual(
                    result["planning_impact"]["metrics"]["remaining_production_quantity"],
                    remaining,
                )

    def test_no_associated_production_orders_has_zero_quantity(self):
        result = self.engine_result("COMP-003")
        self.assertEqual(result["production_orders"], [])
        metrics = result["planning_impact"]["metrics"]
        self.assertEqual(metrics["affected_production_quantity"], 0)
        self.assertEqual(metrics["remaining_production_quantity"], 0)

    def test_single_http_report_distinguishes_unknown_from_zero(self):
        for quantity in (None, 0):
            with self.subTest(quantity=quantity):
                self.set_quantities(quantity, quantity, quantity)
                response = json.loads(self.request("POST", "/api/analyze", self.payload()))
                metrics = response["result"]["planning_impact"]["metrics"]
                for key in (
                    "affected_production_quantity", "remaining_production_quantity",
                    "unfinished_production_quantity", "undelivered_production_quantity",
                    "affected_sales_quantity",
                ):
                    self.assertEqual(metrics[key], quantity)
                self.assert_report_quantity(response["report"], quantity)
                self.assert_report_quantity(self.request("GET", "/api/report.md"), quantity)

    def test_batch_http_partial_quantities_do_not_become_a_complete_total(self):
        self.set_quantities(10, 5, 5, only_first=True)
        with self.state.repository.connect() as connection:
            connection.execute("UPDATE production_orders SET status_category='已释放/执行中'")
        response = json.loads(self.request("POST", "/api/analyze-batch", {
            "changes": [self.payload("COMP-002"), self.payload("COMP-003")],
        }))
        first_metrics = response["items"][0]["result"]["planning_impact"]["metrics"]
        self.assertEqual(first_metrics["remaining_production_quantity"], 5)
        combined = response["groups"][0]
        metrics = combined["result"]["planning_impact"]["metrics"]
        for key in (
            "affected_production_quantity", "remaining_production_quantity",
                    "unfinished_production_quantity", "undelivered_production_quantity",
            "affected_sales_quantity",
        ):
            self.assertIsNone(metrics[key])
        coverage = {
            item["item"]: item["status"]
            for item in combined["result"]["planning_impact"]["coverage"]
        }
        self.assertEqual(coverage["生产需求"], "数量待补")
        self.assertEqual(coverage["销售需求"], "数量待补")
        self.assert_report_quantity(combined["report"], None)
        self.assert_report_quantity(self.request("GET", "/api/report.md"), None)

    def test_batch_http_preserves_real_zero(self):
        self.set_quantities(0, 0, 0)
        with self.state.repository.connect() as connection:
            connection.execute("UPDATE production_orders SET status_category='已释放/执行中'")
        response = json.loads(self.request("POST", "/api/analyze-batch", {
            "changes": [self.payload("COMP-002"), self.payload("COMP-003")],
        }))
        combined = response["groups"][0]
        self.assertEqual(
            combined["result"]["planning_impact"]["metrics"]["remaining_production_quantity"],
            0,
        )
        self.assert_report_quantity(combined["report"], 0)

    def test_completed_order_with_unknown_delivery_stays_pending(self):
        self.set_quantities(10, 10, None)
        with self.state.repository.connect() as connection:
            connection.execute("UPDATE production_orders SET status_category='已完成'")
        response = json.loads(self.request("POST", "/api/analyze", self.payload()))
        planning = response["result"]["planning_impact"]
        self.assertEqual(planning["metrics"]["active_production_orders"], 1)
        self.assertEqual(planning["metrics"]["remaining_production_quantity"], 0)
        self.assertEqual(planning["metrics"]["unfinished_production_quantity"], 0)
        self.assertIsNone(planning["metrics"]["undelivered_production_quantity"])
        self.assert_report_quantity(response["report"], 0)
        self.assertIn("- 待交付成品数量：待补数据", response["report"])
        recommendation = response["result"]["manufacturing_impact"]["recommended_dispositions"][0]
        self.assertIn(recommendation["code"], {"UNCONFIRMED", "HOLD_REVIEW"})
        self.assertNotEqual(recommendation["code"], "USE_AS_IS")
        self.assertIn("交付/执行状态", recommendation["rationale"])
        self.assertNotIn("均未开工", recommendation["rationale"])
        self.assertNotIn("均未开工", response["report"])


if __name__ == "__main__":
    unittest.main()
