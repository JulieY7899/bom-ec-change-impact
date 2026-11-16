from __future__ import annotations

import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path

from bom_change_assistant import BUILD_ID
from bom_change_assistant.demo import load_demo_snapshot
from bom_change_assistant.impact_checks import quantity_check, effectivity_comparison
from bom_change_assistant.impact_engine import ImpactEngine
from bom_change_assistant.models import ChangeRequest
from bom_change_assistant.web import AppState, Handler, _local_request_allowed


class CompletenessTests(unittest.TestCase):
    def test_partial_quantity_is_not_reported_as_total(self):
        paths = [
            {"top_material": "FG", "evidence": [{"quantity": 2}]},
            {"top_material": "FG", "evidence": [{"quantity": None}]},
        ]
        check = quantity_check(paths, 2, 3)
        self.assertFalse(check["by_top"])
        self.assertEqual(check["status"], "需人工复核")

    def test_alternative_or_truncated_paths_require_review(self):
        path = {"top_material": "FG", "evidence": [{"quantity": 2}]}
        check = quantity_check([path], 2, 3, warnings=[{"code": "PATH_LIMIT"}])
        self.assertFalse(check["by_top"])
        path["evidence"][0]["alternative_group"] = "ALT-1"
        self.assertFalse(quantity_check([path], 2, 3)["by_top"])

    def test_nonfinite_cumulative_usage_is_not_reported(self):
        path = {"top_material": "FG", "evidence": [
            {"quantity": 2}, {"quantity": 1e308}, {"quantity": 1e308}
        ]}
        self.assertFalse(quantity_check([path], 2, 3)["by_top"])

    def test_same_nodes_different_evidence_are_different_paths(self):
        def result(row):
            return {"paths": [{"direction": "upstream", "top_material": "FG",
                "nodes": ["COMP", "FG"], "evidence": [{"source_id": "SRC", "source_row": row}]}]}
        check = effectivity_comparison(result(1), result(2))
        self.assertEqual((check["added_paths"], check["removed_paths"]), (1, 1))


class LocalHTTPTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.state = AppState(Path(self.temp.name) / "test.sqlite3")
        load_demo_snapshot(self.state.repository)
        handler = type("TestHandler", (Handler,), {"state": self.state})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.port = self.server.server_port

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.temp.cleanup()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        try:
            conn.request(method, path, json.dumps(body) if body is not None else None,
                         headers or {})
            response = conn.getresponse()
            return response.status, response.read()
        finally:
            conn.close()

    def test_local_health_ai_disabled_and_cross_site_blocked(self):
        status, data = self.request("GET", "/api/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["build"], BUILD_ID)
        status, data = self.request("GET", "/api/status")
        self.assertFalse(json.loads(data)["ai_configured"])
        self.assertEqual(self.request("POST", "/api/ai", {})[0], 403)
        self.assertEqual(self.request("GET", "/api/status", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.request("POST", "/api/snapshot/start", {},
            {"Origin": "https://evil.example"})[0], 403)
        self.assertGreater(self.state.repository.stats()["bom_edges"], 0)
        self.assertFalse(_local_request_allowed("localhost:8765", "http://127.0.0.1:8765"))

    def test_analyze_report_and_batch_isolation_over_http(self):
        payload = {"change_type": "quantity", "old_component": "COMP-002",
                   "old_quantity": 1, "new_quantity": 3,
                   "effective_date": "2026-08-01", "bom_version": "BOM-000001"}
        for _ in range(2):
            status, raw = self.request("POST", "/api/analyze", payload)
            self.assertEqual(status, 200, raw)
            data = json.loads(raw)
            checks = data["result"]["change_checks"]
            self.assertEqual(len(checks), 1)
            self.assertEqual(checks[0]["quantity_impact"]["by_top"][0]["delta_per_top_unit"], 2)
            self.assertIn("单位成品理论差量", data["report"])
        status, report = self.request("GET", "/api/report.md")
        self.assertEqual(status, 200)
        self.assertIn("单位成品理论差量", report.decode())

    def test_engine_compares_old_and_new_effectivity_dates(self):
        repo = self.state.repository
        with repo.connect() as connection:
            connection.execute("UPDATE bom_edges SET valid_to='2026-08-01'")
        result = ImpactEngine(repo).analyze(ChangeRequest(
            change_type="effectivity", old_component="COMP-002",
            old_effective_date="2026-07-01", new_effective_date="2026-09-01"))
        check = result["effectivity_comparison"]
        self.assertGreater(check["removed_paths"], 0)
        self.assertEqual(check["added_paths"], 0)


if __name__ == "__main__":
    unittest.main()
