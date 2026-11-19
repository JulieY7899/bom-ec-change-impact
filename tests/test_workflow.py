"""Workflow HTTP contracts and local PPT retrieval, using invented data only."""
from __future__ import annotations

import hashlib
import html
import http.client
import io
import json
import os
import secrets
import sqlite3
import tempfile
import threading
import unittest
from copy import deepcopy
from dataclasses import replace
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from zipfile import ZipFile

from bom_change_assistant.accounts import Accounts, password_key, set_account
from bom_change_assistant.knowledge import A, P, R, RELS
from bom_change_assistant.web import AppState, Handler
from tests.test_engineering import OLD_BOM, QUERY, base, order, record, sheet
from tests.test_procurement import BASE, COLUMNS, receipt, workbook

QUESTION = "2026-12-01 把 PART-OLD 换成 PART-NEW，采购承诺和到货怎么核查？"
REVIEWED = "工程版本替换与 BOM 行生效复核。工程确认版本引用和组件路径。"
SCREENSHOT = "我是采购，我需要识别Comp-001无法及时采购，替代采购件为comp-998有什么影响"
KNOWLEDGE = "/api/engineering-knowledge"


def presentation():
    stream = io.BytesIO()
    with ZipFile(stream, "w") as deck:
        deck.writestr("ppt/presentation.xml", f"""
          <p:presentation xmlns:p="{P}" xmlns:r="{R}"><p:sldIdLst>
          <p:sldId id="256" r:id="b"/><p:sldId id="257" r:id="a"/>
          </p:sldIdLst></p:presentation>""")
        deck.writestr("ppt/_rels/presentation.xml.rels", f"""
          <Relationships xmlns="{RELS}">
          <Relationship Id="a" Type="{R}/slide" Target="slides/slide1.xml"/>
          <Relationship Id="b" Type="{R}/slide" Target="slides/slide2.xml"/>
          </Relationships>""")
        for position, text in ((2, "待人工删去的内容"), (1, "旅行天文与远方星系")):
            deck.writestr(f"ppt/slides/slide{position}.xml", f"""
              <p:sld xmlns:p="{P}" xmlns:a="{A}"><a:p><a:r><a:t>{html.escape(text)}</a:t></a:r></a:p></p:sld>""")
        deck.writestr("ppt/notesSlides/notesSlide1.xml", "RESTRICTION_TEST_NOTES")
    return stream.getvalue()


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.accounts = self.root / "accounts.sqlite3"
        self.token = ""
        self.state = AppState(self.root / "workflow.sqlite3")
        self.server = ThreadingHTTPServer(("127.0.0.1", 0),
            type("WorkflowHandler", (Handler,), {"state": self.state}))
        thread = threading.Thread(target=self.server.serve_forever, args=(0.02,), daemon=True)
        thread.start()
        self.addCleanup(self.state.abort_import)
        self.addCleanup(self.server.server_close)
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.server.shutdown)

    def request(self, path, payload=None, method="POST", status=200):
        client = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=10)
        try:
            body = None if method == "GET" else payload if isinstance(payload, bytes) else json.dumps(payload or {})
            headers = {"Authorization": "Bearer " + self.token} if self.token else {}
            client.request(method, path, body, headers)
            response = client.getresponse()
            raw = response.read()
            data = json.loads(raw) if "json" in response.getheader("Content-Type", "") else raw.decode()
            self.assertEqual(response.status, status, data)
            return data
        finally:
            client.close()

    def import_bom(self, *, engineering=False):
        path = self.root / "fixture.xlsx"
        other_version = dict(engineering_version="ENG-V1")
        version_rows = base() + [
            record("VERSION", **other_version),
            order("SO-OLD", engineering_version="ENG-V1"),
            order("SO-NO-REV", engineering_version=""),
        ]
        purchases = [[name for _, name in COLUMNS]]
        purchases += [[entry.get(name) for name, _ in COLUMNS] for entry in (BASE, receipt())]
        workbook(path, [OLD_BOM, purchases, sheet(version_rows)] if engineering else [OLD_BOM, purchases])
        self.request("/api/snapshot/start", {"expected_files": 1})
        self.request("/api/import/file", path.read_bytes())
        self.request("/api/snapshot/finish")

    def preflight(self, status=200, **payload):
        return self.request("/api/workflow/preflight", {"question": QUESTION, **payload}, status=status)

    def run_review(self, prepared, status=200):
        return self.request("/api/workflow/run", {
            "confirmed": True, "context": prepared["context"],
            "confirmation_token": prepared["confirmation_token"],
        }, status=status)

    def login_as(self, group, roles=None):
        password = secrets.token_urlsafe(24)
        set_account(self.accounts, group, group, password, roles)
        self.state.accounts.path = self.accounts
        self.token = self.request("/api/account/login", {"username": group, "password": password})["token"]

    def switch(self, role):
        response = self.request("/api/account/role", {"role": role})
        self.token = response["token"]
        self.assertEqual(response["identity"]["active_role"], role)

    def engineering_run(self):
        return self.run_review(self.preflight(role="engineering", question="2026-12-01 把 PART-OLD 换成 PART-NEW"))["run_id"]

    def knowledge_url(self, run_id, action=""):
        return KNOWLEDGE + action + "?analysis_id=" + run_id

    def index_document(self, run_id, text=REVIEWED, *, deck=None):
        parsed = self.request(self.knowledge_url(run_id, "/preview"), deck if deck is not None else presentation())
        self.assertEqual(parsed["page_count"], 2)
        self.assertEqual(parsed["excerpts"][0]["text"], "待人工删去的内容")
        self.assertEqual(parsed["excerpts"][1]["slide"], 2)
        self.assertNotIn("RESTRICTION_TEST_NOTES", json.dumps(parsed))
        reviewed = {k: parsed[k] for k in ("digest", "slides", "page_count", "review_token")}
        reviewed["excerpts"] = [{"slide": 1, "text": text}]
        self.request(self.knowledge_url(run_id, "/index"), reviewed, status=400)
        self.assertEqual(self.request(self.knowledge_url(run_id), method="GET")["excerpts"], 0)
        return self.request(self.knowledge_url(run_id, "/index"), {**reviewed, "approved": True})

    def test_sales_intake_custom_reference_reuses_the_evidence_workflow(self):
        self.import_bom(engineering=True)
        messages = ["销售订单 SO-22 的成品 BOM 改了，能下单吗？",
                    "成品 FG-T1 是客户定制 BOM", "订单行引用工程版本 ENG-V2，工厂 PLANT-T，BOM 行 V2-L2",
                    "2026-12-01 把 PART-OLD 换成 PART-NEW"]
        answer = self.request("/api/workflow/dialogue", {"messages": messages})
        self.assertEqual(answer["intake"]["status"], "impact_review")
        self.assertFalse(answer["missing"])
        draft = dict(answer["fields"])
        role = draft.pop("role")
        prepared = self.request("/api/workflow/preflight", {"role": role, "draft": draft})
        self.assertTrue(prepared["ready"], prepared)
        run = self.run_review(prepared)
        self.assertEqual(run["answer"]["role"], "销售")
        self.assertEqual(run["context"]["draft"]["engineering_version"], "ENG-V2")
        self.assertIn("SO-22", run["report"])
        self.assertNotIn(messages[0], run["report"])

    def test_added_departments_can_run_the_existing_workflow(self):
        self.import_bom()
        for label in ("计划部", "仓储部", "质量部", "售后部"):
            message = f"{label}，2026-12-01 把 PART-OLD 换成 PART-NEW"
            with self.subTest(label=label):
                answer = self.request("/api/workflow/dialogue", {"messages": [message]})
                self.assertFalse(answer["missing"], answer)
                draft = dict(answer["fields"])
                role = draft.pop("role")
                prepared = self.request("/api/workflow/preflight", {
                    "role": role, "draft": draft, "focus": answer["focus"],
                })
                self.assertTrue(prepared["ready"], prepared)
                result = self.run_review(prepared)
                self.assertEqual(result["answer"]["role"], label.removesuffix("部"))
                self.assertTrue(result["answer"]["review_actions"])
                self.assertIn("PART-NEW", result["report"])

    def test_authenticated_dialogue_uses_granted_role_and_ignores_claimed_department(self):
        self.import_bom()
        cases = (
            ("planning", "什么时候齐套", "齐套与切换"),
            ("warehouse", "批次 LOT-1 待检", "库存状态与可用量"),
            ("quality", "新料验证待验证", "新料验证与适用性"),
            ("service", "已交付设备 SN-1 的维修备件", "已交付产品与备件"),
        )
        self.login_as("other", tuple(role for role, _, _ in cases))
        for role, message, topic in cases:
            with self.subTest(role=role):
                self.switch(role)
                answer = self.request("/api/workflow/dialogue", {"messages": [message]})
                self.assertEqual(answer["department_intent"]["role"], role)
                self.assertIn(topic, answer["department_intent"]["topics"])
                answer = self.request("/api/workflow/dialogue", {
                    "messages": ["我是工程，2026-12-01 把 PART-OLD 换成 PART-NEW"],
                })
                self.assertEqual(answer["fields"]["role"], role)
                self.assertIn("按当前账号", answer["reply"])
                draft = {key: value for key, value in answer["fields"].items() if key != "role"}
                prepared = self.request("/api/workflow/preflight", {"draft": draft, "focus": answer["focus"]})
                self.assertTrue(prepared["ready"], prepared)
                self.assertEqual(self.run_review(prepared)["context"]["role"], role)
        self.request("/api/account/role", {"role": "engineering"}, status=403)

    def test_confirmed_workflow_selects_the_same_analysis_for_views_and_reload(self):
        self.login_as("engineering", ("engineering", "procurement"))
        self.import_bom()
        for role, new_part in (("engineering", "PART-NEW"), ("procurement", "PART-FINAL")):
            self.switch(role)
            prepared = self.preflight(role=role, draft={"new_component": new_part})
            run = self.request("/api/workflow/run", {
                "context": prepared["context"], "confirmation_token": prepared["confirmation_token"],
                "confirmed": True, "include_analysis": True,
            })
            selected = run["analysis"]
            self.assertEqual(selected["result"]["change"]["new_component"], new_part)
            self.assertEqual(selected["result"]["analysis_id"], run["run_id"])
            self.assertEqual(selected["result"]["engineering_knowledge_available"], role == "engineering")
            for endpoint in ("/api/analysis/last", "/api/analysis/by-id?analysis_id=" + run["run_id"]):
                self.assertEqual(self.request(endpoint, method="GET"), {"ok": True, **selected})
            self.assertEqual(self.request("/api/report.md", method="GET"), selected["report"])
            self.assertNotIn("analysis", self.request("/api/workflow/run", method="GET"))

    def test_legacy_ppt_preview_preserves_source_and_requires_engineering_account(self):
        self.login_as("engineering")
        self.import_bom()
        run = self.engineering_run()
        upload = bytes.fromhex("D0CF11E0A1B11AE1") + b"synthetic test input"
        url = self.knowledge_url(run, "/preview") + "&kind=ppt"
        self.request(url, b"not a legacy PPT", status=400)

        def convert(command, **options):
            (options["cwd"] / "material.pptx").write_bytes(presentation())

        self.state.legacy_ppt_converter = self.root / "synthetic-soffice"
        with patch("bom_change_assistant.knowledge.subprocess.run", side_effect=convert) as converter:
            parsed = self.request(url, upload)
            converter.assert_called_once()
            self.login_as("procurement", ("engineering",))
            self.request(url, upload, status=403)
            converter.assert_called_once()

        self.assertEqual(parsed["kind"], "ppt")
        self.assertEqual(parsed["digest"], hashlib.sha256(upload).hexdigest())
        self.assertEqual(parsed["page_count"], 2)
        self.login_as("engineering")
        indexed = self.request(self.knowledge_url(run, "/index"),
                               {**parsed, "excerpts": [parsed["excerpts"][0]], "approved": True})
        self.assertTrue(indexed["source_id"].startswith("PPT-"))
        self.assertEqual(indexed["indexed"], 1)

    def test_knowledge_is_separate_from_standard_workflow_and_download(self):
        self.login_as("engineering", ("procurement", "engineering"))
        self.import_bom()
        before = self.preflight()
        self.assertTrue(before["ready"], before)
        self.assertEqual(before["context"]["draft"]["effective_date"], "2026-12-01")
        self.assertEqual(before["context"]["role"], "procurement")
        run = self.run_review(before)
        self.assertTrue(any("PO-P19-001 / 00010" in fact and "剩余承诺 30" in fact for fact in run["answer"]["facts"]))
        self.assertNotIn("knowledge", run)
        self.assertNotIn("knowledge_references", run["answer"])
        self.assertNotIn(REVIEWED, run["report"])
        self.assertNotIn("PPT 知识参考", run["report"])
        self.assertEqual(self.request(
            "/api/workflow/report.md?run_id=" + run["run_id"], method="GET"), run["report"])
        self.assertEqual(self.request("/api/workflow/run?run_id=" + run["run_id"], method="GET"), run)
        self.assertEqual(self.request("/api/workflow/run", method="GET"), run)
        self.switch("engineering")
        engineering_id = self.engineering_run()
        self.assertEqual(self.request(self.knowledge_url(engineering_id, "/references"), method="GET")["status"], "empty")
        doc = self.index_document(engineering_id)
        outcome = self.request(self.knowledge_url(engineering_id, "/references"), method="GET")
        self.assertEqual(outcome["status"], "retrieved")
        hit, = outcome["matches"]
        self.assertEqual((hit["source_id"], hit["slide"], hit["text"]), (doc["source_id"], 1, REVIEWED))
        for run_id in ("", run["run_id"], "AN-UNKNOWN"):
            for route, method in (("", "GET"), ("/references", "GET"), ("/preview", "POST"), ("/index", "POST")):
                self.request(self.knowledge_url(run_id, route), method=method, status=403)
        self.request("/api/knowledge", method="GET", status=404)
        for run_id, permitted in ((engineering_id, True), (run["run_id"], False)):
            result = self.request("/api/analysis/by-id?analysis_id=" + run_id, method="GET")
            self.assertEqual(result["result"]["engineering_knowledge_available"], permitted)

    def test_nine_department_accounts_use_own_role_evidence_and_knowledge_permissions(self):
        self.import_bom()
        self.login_as("engineering")
        engineer_run = self.engineering_run()
        self.index_document(engineer_run)
        analysis_route = f"/api/analysis/by-id?analysis_id={engineer_run}"
        report_route = f"/api/report.md?analysis_id={engineer_run}"
        workflow_route = f"/api/workflow/report.md?run_id={engineer_run}"
        shared_report = self.request(report_route, method="GET")
        assessment = self.knowledge_url(engineer_run, "/assessment")
        engineering_report = self.knowledge_url(engineer_run, "/report.md")
        hit = self.request(self.knowledge_url(engineer_run, "/references"), method="GET")["matches"][0]
        self.request(assessment, {"references": [hit["reference"]]})
        self.assertTrue(self.request(workflow_route, method="GET").endswith(shared_report))
        for section in ("计划与备料影响", "制造执行影响", "分工与待办事项"):
            self.assertIn(section, shared_report)
        self.assertNotIn(REVIEWED, shared_report)
        cases = {
            "engineering": ("工程", "bom", "上溯路径"),
            "rd": ("研发", "bom", "上溯路径"),
            "procurement": ("采购", "purchases", "剩余承诺 30"),
            "planning": ("计划物控", "po_overlap", "计划结论待核"),
            "warehouse": ("仓储物流", "warehouse_stock", "1 条有到货事件明细"),
            "manufacturing": ("制造", "production_orders", "生产工单"),
            "quality": ("质量", "quality_release", "质量验证和放行证据待补"),
            "sales": ("销售", "sales_order_rows", "销售订单行"),
            "service": ("售后服务", "installed_base", "备件互换性待核"),
        }
        for role, (label, preparation_key, evidence) in cases.items():
            with self.subTest(role=role):
                self.login_as(role)
                identity = self.request("/api/account", method="GET")["identity"]
                self.assertEqual((identity["department"], identity["active_role"], identity["roles"]),
                                 (role, role, [role]))
                other_role_analysis = self.request(analysis_route, method="GET")
                self.assertEqual(other_role_analysis["result"]["analysis_id"], engineer_run)
                self.assertEqual(other_role_analysis["report"], shared_report)
                self.assertEqual(self.request(report_route, method="GET"), shared_report)
                shared_workflow = self.request(workflow_route, method="GET")
                self.assertTrue(shared_workflow.endswith(shared_report))
                self.assertNotIn(REVIEWED, shared_workflow)
                if role == "engineering":
                    self.assertIn(REVIEWED, self.request(engineering_report, method="GET"))
                else:
                    self.request(assessment, method="GET", status=403)
                    self.request(engineering_report, method="GET", status=403)
                ungranted = "procurement" if role == "engineering" else "engineering"
                self.request("/api/account/role", {"role": ungranted}, status=403)
                self.preflight(role=ungranted, status=403)
                prepared = self.preflight()
                self.assertEqual(prepared["context"]["role"], role)
                self.assertIn(preparation_key, {entry["key"] for entry in prepared["data_preparation"]["sources"]})
                result = self.run_review(prepared)
                answer = result["answer"]
                self.assertEqual(answer["role"], label)
                self.assertTrue(any(evidence in line for line in answer["facts"]), answer)
                self.assertTrue(answer["review_actions"])
                if role == "engineering":
                    self.assertTrue(any("临时替代条件" in action for action in answer["review_actions"]))
                if role == "rd":
                    self.assertTrue(any("设计验证" in action for action in answer["review_actions"]))
                if role == "planning":
                    self.assertTrue(any("库位 待补" in item for item in answer["facts"]))
                    self.assertTrue(any("可用库存 待补数据" in item for item in answer["facts"]))
                    self.assertFalse(any("剩余承诺" in item for item in answer["facts"]))
                for suffix, method in (("", "GET"), ("/references", "GET"), ("/references", "POST"),
                                       ("/preview", "POST"), ("/index", "POST")):
                    if role != "engineering":
                        self.request(self.knowledge_url(engineer_run, suffix), method=method, status=403)
                if role == "engineering":
                    self.assertEqual(self.request(self.knowledge_url(engineer_run, "/references"), method="GET")
                                     ["matches"][0]["text"], REVIEWED)
                key = result["run_id"]
                downloaded = self.request("/api/workflow/report.md?run_id=" + key, method="GET")
                self.assertEqual(downloaded, result["report"])
                self.assertIn(f"## {label}核查", downloaded)
                self.assertNotIn(REVIEWED, downloaded)
                self.assertNotIn(REVIEWED, self.request("/api/report.md?analysis_id=" + key, method="GET"))

    def test_planning_retains_source_values_without_assuming_po_and_transit_are_disjoint(self):
        path = self.root / "planning.xlsx"
        purchases = [[label for _, label in COLUMNS], [BASE.get(key) for key, _ in COLUMNS]]
        supply = [
            ["场景编号", "数据属性", "旧物料", "新物料", "工厂", "库位",
             "旧料可用库存", "旧料在途", "旧料 Open PO", "切换前旧料可消耗需求",
             "新料可用库存", "新料在途", "新料 Open PO", "切换后新料需求"],
            ["SC-ROLE-01", "DEMO_ONLY", "PART-OLD", "PART-NEW", "PLANT-T", "STORE-T",
             15, 20, 30, 25, 0, None, 12, 30],
        ]
        workbook(path, [OLD_BOM, purchases, supply])
        self.request("/api/snapshot/start", {"expected_files": 1})
        self.request("/api/import/file", path.read_bytes())
        self.request("/api/snapshot/finish")
        self.login_as("planning")
        prepared = self.preflight(draft={"planning_plant": "PLANT-T", "storage_location": "STORE-T"})
        sources = {item["key"]: item["status"] for item in prepared["data_preparation"]["sources"]}
        self.assertEqual(sources["planning_supply"], "待核")
        self.assertEqual(sources["po_overlap"], "需补资料")
        facts = self.run_review(prepared)["answer"]["facts"]
        self.assertTrue(any("工厂 PLANT-T · 库位 STORE-T" in line for line in facts))
        self.assertTrue(any("可用库存 15 · 在途 20 · 未交采购 30" in line for line in facts))
        self.assertTrue(any("可用库存 0 · 在途 待补数据 · 未交采购 12" in line for line in facts))
        self.assertTrue(any("计划结论待核" in line for line in facts))
        self.assertFalse(any("供应合计" in line or "Run-in Change" in line for line in facts))
        no_scope = self.preflight(draft={"planning_plant": "PLANT-T", "storage_location": "STORE-X"})
        self.assertEqual(next(item["status"] for item in no_scope["data_preparation"]["sources"]
                              if item["key"] == "planning_supply"), "需补资料")

    def test_department_dialogue_infers_viewpoint_without_changing_authenticated_role(self):
        for phrase, role in (("计划物控", "planning"), ("仓储物流", "warehouse"),
                             ("质量", "quality"), ("售后", "service"), ("制造工程", "manufacturing")):
            with self.subTest(phrase=phrase):
                request = {"messages": [f"我是{phrase}，2026-12-01 把 PART-OLD 换成 PART-NEW"]}
                self.assertEqual(self.request("/api/workflow/dialogue", request)["fields"]["role"], role)
        self.login_as("planning")
        dialogue = self.request("/api/workflow/dialogue", {"messages": [f"我是采购，{QUESTION}"]})
        self.assertEqual(dialogue["fields"]["role"], "planning")
        self.assertNotIn("role", [row["field"] for row in dialogue["rows"]])

    def test_reviewed_fragments_supplement_engineering_result_and_private_report(self):
        self.import_bom(engineering=True)
        self.login_as("engineering", ("engineering", "procurement"))
        self.switch("engineering")
        run = self.run_review(self.preflight(role="engineering", draft=QUERY))
        run_id = run["run_id"]
        assessment_url = self.knowledge_url(run_id, "/assessment")
        report_url = self.knowledge_url(run_id, "/report.md")
        standard_url = "/api/report.md?analysis_id=" + run_id
        before = self.request(standard_url, method="GET")
        self.assertEqual(self.request(assessment_url, method="GET")["findings"], [])
        self.request(report_url, method="GET", status=404)
        deck = presentation()
        document = self.index_document(run_id, "按采购行复核在途与未到货，避免重复计量。", deck=deck)
        hit, = self.request(self.knowledge_url(run_id, "/references"), {"query": "在途未到货"})["matches"]
        reference = hit["reference"]
        self.request(assessment_url, {"references": [reference, reference]}, status=400)
        self.request(assessment_url, {"references": [reference[:-64] + "0" * 64]}, status=400)
        self.assertEqual(self.request(assessment_url, method="GET")["findings"], [])
        saved = self.request(assessment_url, {"references": [reference]})
        self.assertEqual(saved, self.request(assessment_url, method="GET"))
        self.assertEqual(saved["facts"], run["answer"]["facts"][:2])
        self.assertIn("ENG-V2", saved["scope"])
        finding, = saved["findings"]
        self.assertEqual(finding["excerpt"], hit["text"])
        self.assertIn(document["source_id"], finding["citation"])
        self.assertIn("去重", finding["review"])
        private = self.request(report_url, method="GET")
        self.assertTrue(private.startswith(before.rstrip()))
        for value in (saved["title"], saved["scope"], finding["citation"], finding["excerpt"], finding["review"]):
            self.assertIn(value, private)
        self.assertEqual(self.request(standard_url, method="GET"), before)
        self.assertEqual(self.request("/api/workflow/run?run_id=" + run_id, method="GET"), run)

        self.switch("procurement")
        for route, method in ((assessment_url, "GET"), (assessment_url, "POST"), (report_url, "GET")):
            self.request(route, method=method, status=403)
        self.login_as("procurement")
        self.request("/api/account/role", {"role": "engineering"}, status=403)
        self.request(report_url, method="GET", status=403)
        self.assertEqual(self.request(standard_url, method="GET"), before)
        self.login_as("procurement", ("procurement", "engineering"))
        self.switch("engineering")
        self.assertFalse(self.request("/api/analysis/by-id?analysis_id=" + run_id, method="GET")
                         ["result"]["engineering_knowledge_available"])
        self.request(assessment_url, method="GET", status=403)
        self.request(assessment_url, {"references": [reference]}, status=403)
        self.request(report_url, method="GET", status=403)
        self.token = ""
        self.request(report_url, method="GET", status=403)

        self.login_as("engineering")
        second = self.engineering_run()
        self.assertNotEqual(run_id, second)
        self.assertEqual(self.request(self.knowledge_url(second, "/assessment"), method="GET")["findings"], [])
        preview = self.request(self.knowledge_url(run_id, "/preview"), deck)
        self.request(self.knowledge_url(run_id, "/index"), {
            **{key: preview[key] for key in ("digest", "slides", "page_count", "review_token")},
            "approved": True, "excerpts": [{"slide": 1, "text": "修订后的工程条件"}],
        })
        revised = self.request(assessment_url, method="GET")
        self.assertEqual(revised["findings"], [])
        self.assertIn("重新选择", revised["notice"])
        self.assertNotIn(finding["excerpt"], self.request(report_url, method="GET"))
        self.request(assessment_url, {"references": [reference]}, status=400)
        self.assertEqual(self.request(assessment_url, {"references": []})["notice"], "")
        self.request(report_url, method="GET", status=404)

    def test_provisioning_never_writes_to_the_business_database(self):
        db = self.state.repository.database_path
        before = db.read_bytes()
        with self.assertRaisesRegex(ValueError, "独立的账号库"):
            set_account(db, "operator", "engineering", secrets.token_urlsafe(24))
        self.assertEqual(db.read_bytes(), before)

    def test_engineering_membership_and_engineering_run_are_both_required(self):
        self.import_bom()
        self.login_as("engineering")
        engineer_run = self.engineering_run()
        self.index_document(engineer_run)
        self.login_as("procurement")
        self.request("/api/account/role", {"role": "engineering"}, status=403)
        self.preflight(role="engineering", status=403)
        prepared = self.preflight()
        self.assertEqual(prepared["context"]["role"], "procurement")
        chosen = self.request("/api/workflow/run", {
            "context": prepared["context"], "confirmation_token": prepared["confirmation_token"],
            "confirmed": True, "include_analysis": True,
        })
        self.assertFalse(chosen["analysis"]["result"]["engineering_knowledge_available"])
        for run_id in (chosen["run_id"], engineer_run):
            result = self.request("/api/analysis/by-id?analysis_id=" + run_id, method="GET")
            self.assertFalse(result["result"]["engineering_knowledge_available"])
            for route, method in (("", "GET"), ("/references", "GET"), ("/preview", "POST"), ("/index", "POST")):
                denied = self.request(self.knowledge_url(run_id, route), method=method, status=403)
                self.assertNotIn(REVIEWED, str(denied))

    def test_account_mode_requires_session_for_business_reads(self):
        self.import_bom(engineering=True)
        run_id = self.engineering_run()
        routes = (
            "/api/status",
            "/api/engineering-versions",
            "/api/materials",
            "/api/material-effectivity?material=PART-OLD",
            "/api/draft-defaults",
            "/api/change-log",
            "/api/analysis/last",
            f"/api/analysis/by-id?analysis_id={run_id}",
            "/api/report.md",
            f"/api/report.md?analysis_id={run_id}",
            "/api/workflow/run",
            f"/api/workflow/run?run_id={run_id}",
            f"/api/workflow/report.md?run_id={run_id}",
        )
        standalone = {route: self.request(route, method="GET") for route in routes}

        self.login_as("procurement")
        for route, expected in standalone.items():
            with self.subTest(session="procurement", route=route):
                self.assertEqual(self.request(route, method="GET"), expected)
        self.request(self.knowledge_url(run_id, "/report.md"), method="GET", status=403)

        post_routes = (
            "/api/analyze", "/api/analyze-batch", "/api/workflow/dialogue", "/api/workflow/preflight",
            "/api/workflow/run", "/api/snapshot/start", "/api/import/file",
            "/api/snapshot/finish", "/api/snapshot/abort", "/api/demo",
        )

        def assert_locked(session):
            for method, endpoints in (("GET", routes), ("POST", post_routes)):
                for route in endpoints:
                    with self.subTest(session=session, method=method, route=route):
                        response = self.request(route, method=method, status=401)
                        self.assertEqual(response, {"ok": False, "error": "请从右上角登录账号"})

        self.token = ""
        self.assertTrue(self.request("/api/health", method="GET")["ok"])
        self.assertIn('id="account-login"', self.request("/", method="GET"))
        self.assertEqual(self.request("/api/account", method="GET"),
                         {"ok": True, "configured": True, "identity": None})
        assert_locked("missing")
        self.token = "invalid"
        assert_locked("invalid")

        self.login_as("procurement")
        self.request("/api/account/logout")
        assert_locked("logged-out")

        self.login_as("procurement")
        session = self.state.accounts.sessions[self.token]
        self.state.accounts.sessions[self.token] = replace(session, deadline=0)
        assert_locked("expired")

        self.login_as("procurement")
        set_account(self.accounts, "procurement", "planning", secrets.token_urlsafe(24))
        assert_locked("membership-changed")

    def test_revoked_upload_session_preserves_the_active_import(self):
        self.login_as("procurement")
        batch_id = self.request("/api/snapshot/start", {"expected_files": 1})["batch_id"]
        path = self.root / "fixture.xlsx"
        workbook(path, [OLD_BOM])
        read_body = Handler._read_body

        def read_then_revoke(handler, length, output):
            read_body(handler, length, output)
            with self.state.import_lock:
                self.state.accounts.logout(self.token)

        with patch.object(Handler, "_read_body", read_then_revoke):
            self.request("/api/import/file", path.read_bytes(), status=401)
        self.assertEqual(self.state.staging_batch_id, batch_id)
        self.assertEqual(self.state.imported_files, 0)

        self.login_as("procurement")
        self.request("/api/import/file", path.read_bytes())
        self.assertTrue(self.request("/api/snapshot/finish")["stats"]["ready"])

    def test_sessions_fail_closed_after_logout_expiry_and_account_change(self):
        self.import_bom()
        run = self.engineering_run()
        route = self.knowledge_url(run)
        self.request(route, method="GET", status=403)
        self.request("/api/account/login", {"username": "unknown", "password": "invalid"}, status=401)
        self.login_as("engineering")
        self.request(route, method="GET")
        self.request("/api/account/logout")
        self.request(route, method="GET", status=403)
        self.login_as("engineering")
        session = self.state.accounts.sessions[self.token]
        self.state.accounts.sessions[self.token] = replace(session, deadline=0)
        self.request(route, method="GET", status=403)
        self.login_as("engineering")
        set_account(self.accounts, "engineering", "procurement", secrets.token_urlsafe(24))
        self.request(route, method="GET", status=403)
        self.assertIsNone(self.request("/api/account", method="GET")["identity"])

    def test_roles_are_session_scoped_and_never_inferred_from_the_question(self):
        self.import_bom()
        self.login_as("engineering", ("engineering", "procurement"))
        prepared = self.preflight()
        self.assertEqual(prepared["context"]["role"], "engineering")
        self.assertTrue(prepared["ready"])
        old_token = self.token
        run_id = self.run_review(prepared)["run_id"]
        route = self.knowledge_url(run_id, "/references")
        self.index_document(run_id)
        engineer_report = self.request("/api/report.md?analysis_id=" + run_id, method="GET")

        self.switch("procurement")
        current_token = self.token
        self.assertNotEqual(old_token, current_token)
        self.assertFalse(self.request("/api/analysis/by-id?analysis_id=" + run_id,
                                      method="GET")["result"]["engineering_knowledge_available"])
        self.request(route, method="GET", status=403)
        self.run_review(prepared, 403)
        self.preflight(role="engineering", status=403)
        self.token = old_token
        self.request(route, method="GET", status=403)
        self.request("/api/report.md?analysis_id=" + run_id, method="GET", status=401)
        self.preflight(status=401)
        self.token = current_token

        self.switch("engineering")
        self.run_review(prepared, 400)
        self.assertEqual(self.request(route, method="GET")["status"], "retrieved")
        self.login_as("procurement", ("procurement", "engineering"))
        self.switch("engineering")
        self.request(route, method="GET", status=403)
        self.assertEqual(self.request("/api/report.md?analysis_id=" + run_id, method="GET"), engineer_report)
        self.assertNotIn(REVIEWED, engineer_report)

    def test_legacy_accounts_are_read_only_and_keep_their_department_defaults(self):
        salt = secrets.token_bytes(16)
        password = secrets.token_urlsafe(24)
        digest = password_key(password, salt)
        with sqlite3.connect(self.accounts) as db:
            db.execute("CREATE TABLE accounts (name TEXT PRIMARY KEY, department TEXT, salt BLOB, digest BLOB)")
            db.executemany("INSERT INTO accounts VALUES (?, ?, ?, ?)", [
                (group, group, salt, digest) for group in ("engineering", "procurement", "other")
            ])
        db.close()
        before = self.accounts.read_bytes()
        store = Accounts(self.accounts)
        for group, expected in (("engineering", "engineering"), ("procurement", "procurement"), ("other", "rd")):
            identity = store.identity(store.login(group, password))
            self.assertEqual((identity["roles"], identity["active_role"]), ((expected,), expected))
        self.assertEqual(self.accounts.read_bytes(), before)

    def test_legacy_workflow_reports_hide_stored_knowledge_without_rewriting_rows(self):
        self.import_bom()
        run = self.run_review(self.preflight())
        legacy = deepcopy({key: value for key, value in run.items() if key not in {"ok", "report"}})
        legacy["context"]["knowledge_revision"] = "legacy"
        legacy["answer"]["knowledge_references"] = [{"text": REVIEWED}]
        legacy["knowledge"] = {"matches": [{"text": REVIEWED}]}
        legacy["steps"].insert(3, {"name": "知识检索", "status": "已引用", "detail": "1 页"})
        with self.state.repository.connect() as connection:
            connection.execute("UPDATE workflow_runs SET result_json=?, report_markdown=? WHERE run_id=?",
                               (json.dumps(legacy), REVIEWED, run["run_id"]))
        self.assertEqual(self.request("/api/workflow/run", method="GET"), run)
        self.assertEqual(self.request(
            "/api/workflow/report.md?run_id=" + run["run_id"], method="GET"), run["report"])
        with self.state.repository.connect() as connection:
            self.assertEqual(connection.execute("SELECT report_markdown FROM workflow_runs").fetchone()[0], REVIEWED)

    def test_preflight_missing_fields_no_engineering_fallback(self):
        missing = self.preflight(question="这个组件会影响哪些订单？")
        self.assertFalse(missing["ready"])
        self.assertTrue({"role_required", "missing_field", "no_snapshot"} <=
                        {issue["code"] for issue in missing["context"]["issues"]})
        self.import_bom()
        strict = self.preflight(role="engineering", draft={**QUERY, "engineering_version": ""})
        self.assertFalse(strict["ready"])
        self.assertIn("no_ebom", {i["code"] for i in strict["context"]["issues"]})
        self.assertIn("engineering_version", {i["field"] for i in strict["context"]["issues"]})
        self.assertIn("先预检", self.run_review(strict, 400)["error"])
        with self.state.repository.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM analysis_runs").fetchone()[0], 0)

    def test_strict_scope_counts_and_role_order_evidence(self):
        self.import_bom(engineering=True)
        prepared = self.preflight(question=(
            "从销售角度，成品 FG-T1，工厂 PLANT-T，工程版本 ENG-V2，"
            "BOM 行 V2-L2，2026-12-01 把 PART-OLD 换成 PART-NEW，哪些订单行要复核？"
        ))
        self.assertTrue(prepared["ready"], prepared)
        self.assertEqual(prepared["context"]["role"], "sales")
        self.assertEqual(prepared["context"]["draft"]["target_bom_row"], "V2-L2")
        self.assertIn("order_references_pending", {i["code"] for i in prepared["context"]["issues"]})
        result = self.run_review(prepared)
        self.assertIn("命中 1 个，排除 1 个，引用待核 1 个", result["answer"]["facts"][0])
        for identifier in ("SO-22", "SO-OLD", "SO-NO-REV", "V2-L2"):
            self.assertIn(identifier, result["report"])

    def test_reject_changed_input_and_snapshot_preserve_index(self):
        self.login_as("engineering")
        self.import_bom()
        self.index_document(self.engineering_run())
        prepared = self.preflight()
        prepared["context"]["draft"]["old_component"] = "ANOTHER-PART"
        self.run_review(prepared, 400)
        pending = self.preflight()
        run = self.run_review(pending)
        self.import_bom()
        self.run_review(pending, 400)
        self.assertIsNone(self.request("/api/workflow/run", method="GET")["run_id"])
        self.request("/api/workflow/run?run_id=" + run["run_id"], method="GET", status=404)
        self.assertEqual(self.request(self.knowledge_url(self.engineering_run()), method="GET")["documents"], 1)

    def test_no_related_slide_and_invalid_file(self):
        self.login_as("engineering")
        self.import_bom()
        run_id = self.engineering_run()
        self.request(self.knowledge_url(run_id, "/preview"), b"NOT A PRESENTATION", status=400)
        self.index_document(run_id, "距离银河中心数千光年，隧道示意。")
        found = self.request(self.knowledge_url(run_id, "/references"), method="GET")
        self.assertEqual(found["status"], "no_match")
        self.assertEqual(found["matches"], [])

    def test_explicit_search_requires_scope_and_requested_values(self):
        self.login_as("engineering")
        self.import_bom()
        run_id = self.engineering_run()
        negatives = [
            "北厂 V4 L73 使用 PART-403。东厂 V5 L74 使用同料。东厂 V4 L72 换料须评审。",
            "PART-403 绝缘电阻需要测量。PART-403 接地电阻大于 20 MΩ。",
            "PART-403 绝缘电阻下限 20 mΩ。PART-404 绝缘电阻下限 20 MΩ。",
            "撤单赔偿金额为 800 元，合同编号待补。",
            "合同号 CT-53。采购承诺已登记，具体撤单赔偿金额待确认。",
            "振动筛查需保留最大加速度记录。试验砝码不得超过 8 g。",
            "振动筛查的加速度上限为 18 m/s²。运输跌落加速度上限为 6 g。",
        ]
        checks = [
            ("东厂 V4 L73 使用 PART-403 如何换料？", "东厂 V4 L73 使用 PART-403，换料前核对订单状态。"),
            ("PART-403 绝缘电阻合格阈值是多少兆欧？", "PART-403 绝缘电阻合格标准为至少 45 MΩ。"),
            ("采购承诺的撤单赔偿金额和合同号", "未交采购承诺对应合同号 CT-71，撤单赔偿金额为 1,200 元。"),
            ("振动筛查容许几倍重力加速度？", "振动筛查的峰值加速度不得超过 3.5 g。"),
        ]

        def approve(paragraphs):
            source = "\n\n".join(paragraphs).encode()
            preview = self.request(self.knowledge_url(run_id, "/preview") + "&kind=text", source)
            self.request(self.knowledge_url(run_id, "/index"), {**preview, "approved": True, "section": "validation"})

        def search(query):
            return self.request(self.knowledge_url(run_id, "/references"), {"query": query, "section": "validation"})

        approve(negatives)
        for query, _ in checks:
            with self.subTest(query=query, evidence="insufficient"):
                response = search(query)
                self.assertEqual((response["status"], response["matches"]), ("no_match", []))

        approve([excerpt for _, excerpt in checks])
        for query, excerpt in checks:
            with self.subTest(query=query, evidence="sufficient"):
                self.assertEqual([hit["text"] for hit in search(query)["matches"]], [excerpt])

    def test_spoken_queries_find_approved_evidence_in_the_selected_partition(self):
        self.login_as("engineering")
        self.import_bom()
        run_id = self.engineering_run()
        checks = [
            ("送到仓库后有一批退给供应商，实际入库怎么算？",
             "累计净到货量依据收货数量扣除退货，再按有效冲销记录复核。"),
            ("工单已经开工，要不要返工前先看什么？",
             "在制工单先核对实际领料、投料和当前工序，再评估是否隔离。"),
        ]
        source = "\n\n".join(excerpt for _, excerpt in checks).encode()
        preview = self.request(self.knowledge_url(run_id, "/preview") + "&kind=text", source)
        self.request(self.knowledge_url(run_id, "/index"), {**preview, "approved": True, "section": "standards"})
        route = self.knowledge_url(run_id, "/references")
        references = []
        for query, excerpt in checks:
            with self.subTest(query=query):
                result = self.request(route, {"query": query, "section": "standards"})
                self.assertEqual(result["matches"][0]["text"], excerpt)
                references.append(result["matches"][0]["reference"])
                self.assertEqual(self.request(route, {"query": query, "section": "changes"})["matches"], [])
                self.assertEqual(self.request(route, {"query": f"MAT-913 {query}"})["matches"], [])

        unrelated = self.request(route, {"query": "网购商品退回来，运费由谁出？"})
        self.assertEqual((unrelated["status"], unrelated["matches"]), ("no_match", []))
        assessment = self.knowledge_url(run_id, "/assessment")
        self.assertEqual(self.request(assessment, method="GET")["findings"], [])
        self.request(assessment, {"references": references[:1]})
        report = self.request(self.knowledge_url(run_id, "/report.md"), method="GET")
        self.assertIn(checks[0][1], report)
        self.assertNotIn(checks[1][1], report)

    def test_explicit_procedure_and_record_queries_require_matching_evidence(self):
        self.login_as("engineering")
        self.import_bom()
        run_id = self.engineering_run()
        route = self.knowledge_url(run_id, "/references")
        checks = (
            ("validation", "在途量和未到货量怎样去重？",
             "在运数量和未到货量须按采购行比对，覆盖范围重叠时只登记一次。"),
            ("standards", "这笔未交货采购承诺可按哪份发票冲抵，金额是多少？",
             "未交货采购承诺按发票号 FP-294 冲抵 480 元，并核对采购行。"),
            ("changes", "已批准的切换日期和会签在哪里看？",
             "改线切换已批准，切换日期定于 2027-02-14 08:30。工程会签见签核单 SW-241 第 4 页。"),
        )

        def approve(section, *paragraphs):
            source = "\n\n".join(paragraphs).encode()
            preview = self.request(self.knowledge_url(run_id, "/preview") + "&kind=text", source)
            self.request(self.knowledge_url(run_id, "/index"), {**preview, "approved": True, "section": section})

        standards_procedure = "同一采购行的在途量与未到货量不得重复计入，以正式收货凭证逐笔对照。"
        approve("standards", standards_procedure,
                "未交货采购承诺必须按订单行查余额。发票号 FP-117 的总金额为 480 元。",
                "未交货采购承诺不得用发票号 FP-109 冲抵 480 元。",
                "未交货采购承诺核对发票号 FP-205 合计 480 元，冲抵金额待补。",
                "设备维修费用按发票号 FP-103 冲抵 480 元。")
        approve("validation", "待检库存由质量签发放行单，放行后重新计算仓位可用量。",
                "在途量和未到货量另行记录。验证工单不得重复签发。",
                "在途量和未到货量如何防止重复？")
        approve("changes", "切换日期须核对上下层清单和生效窗口，审批通过后在第 2 页签字。",
                "切换已批准，切换日期为 2027-02-12，签字待补，签字位置在第 2 页。",
                "审批样例中的切换已批准，切换日期为 2027-02-13。签字见审批单 EX-141 第 2 页。",
                "若审批通过，切换日期定于 2027-02-16，签字见审批单 EX-142 第 2 页。",
                "切换审批状态：通过。切换日期：2027-02-15。会签人：待填写。")
        signed_form = "切换审批状态：通过。切换日期：2027-02-21。会签人：工程值班岗。"

        modes = [("keyword", None)]
        if model := os.environ.get("BOM_TEST_MODEL"):
            from bom_change_assistant.semantic_index import SemanticIndex
            modes.append(("bge", SemanticIndex(self.root / "records.qdrant", model)))
        for sufficient in (False, True):
            if sufficient:
                for section, _, excerpt in checks:
                    approve(section, excerpt)
                approve("changes", signed_form)
            for mode, index in modes:
                with self.subTest(mode=mode, sufficient=sufficient), patch.object(
                    self.state.repository, "semantic_index", index, create=True
                ):
                    report_hit = None
                    for section, question, excerpt in checks:
                        result = self.request(route, {"query": question, "section": section})
                        self.assertEqual(result["status"], "retrieved" if sufficient else "no_match", question)
                        expected = [excerpt] if sufficient else []
                        if sufficient and section == "changes":
                            expected.append(signed_form)
                        self.assertCountEqual([hit["text"] for hit in result["matches"]], expected, question)
                        if sufficient and section == "changes":
                            report_hit = next(hit for hit in result["matches"] if hit["text"] == excerpt)
                    policy = self.request(route, {"query": checks[0][1], "section": "standards"})
                    self.assertEqual([hit["text"] for hit in policy["matches"]], [standards_procedure])
                    if report_hit:
                        self.assertFalse(self.request(self.knowledge_url(run_id, "/assessment"),
                                                      method="GET")["findings"])
                        self.request(self.knowledge_url(run_id, "/assessment"), {"references": [report_hit["reference"]]})
                        report = self.request(self.knowledge_url(run_id, "/report.md"), method="GET")
                        self.assertIn(report_hit["text"], report)
                        self.assertNotIn("EX-141", report)
                        self.request(self.knowledge_url(run_id, "/assessment"), {"references": []})

    def test_ordinary_role_facts_use_the_existing_rule_result(self):
        self.request("/api/demo")
        question = "2026-08-01 删除 COMP-001"
        expected = {
            "rd": "涉及成品 FG-A、FG-B",
            "engineering": "3 条上溯路径",
            "sales": "SALES-DEMO-ALPHA / 10",
            "manufacturing": "PRODUCTION-DEMO-ALPHA",
            "procurement": "当前快照未匹配采购证据",
        }
        for role, evidence in expected.items():
            with self.subTest(role=role):
                run = self.run_review(self.preflight(role=role, question=question))
                self.assertTrue(any(evidence in fact for fact in run["answer"]["facts"]))

    def test_dialogue_collects_across_turns_and_drives_confirmed_run(self):
        self.import_bom(engineering=True)
        messages = ["从销售角度，想看订单行交期。",
                    "成品 FG-T1，工厂 PLANT-T，工程版本 ENG-V2，BOM 行 V2-L2，把 PART-OLD 换成 PART-NEW。"]
        partial = self.request("/api/workflow/dialogue", {"messages": messages})
        self.assertEqual(partial["missing"], ["effective_date"])
        self.assertIn("年份", partial["reply"])
        self.assertEqual(partial["fields"]["role"], "sales")
        complete = self.request("/api/workflow/dialogue", {
            "messages": [*messages, "2026 年 12 月 1 日", "拟生效日改成 2026年12月2日。",
                         "不是 PART-NEW，是 PART-NEXT。"],
        })
        self.assertEqual(complete["missing"], [])
        self.assertEqual(complete["fields"]["effective_date"], "2026-12-02")
        self.assertEqual(complete["fields"]["new_component"], "PART-NEXT")
        self.assertIn("交期", complete["focus"])
        draft = dict(complete["fields"])
        role = draft.pop("role")
        prepared = self.request("/api/workflow/preflight", {
            "draft": draft, "role": role, "focus": complete["focus"],
        })
        self.assertTrue(prepared["ready"], prepared)
        run = self.run_review(prepared)
        self.assertIn("PART-NEXT", run["report"])
        self.assertIn("SO-22", run["report"])
        self.assertNotIn(messages[0], run["report"])

    def test_dialogue_declines_outside_scope_without_fields_prompts_or_evidence(self):
        self.login_as("engineering")
        for question in (
            "明天会下雨吗",
            "BOM 助手，明天会下雨吗",
            "帮我看看这段 Python 代码",
            "采购这边，物料 MAT-1 的报价是多少",
            "请在 SAP 中发布工程版本 ENG-V1",
            "帮我把 BOM 行 L-10 正式发布",
            "请审批 PART-A 换成 PART-B 的 BOM 变更",
            "什么是 BOM",
        ):
            with self.subTest(question=question):
                answer = self.request("/api/workflow/dialogue", {"messages": [question]})
                self.assertEqual(answer["status"], "outside_scope")
                self.assertEqual(answer["reply"], "这个问题超出 BOM 变更影响核查的范围，我无法处理。")
                self.assertEqual(answer["fields"], {})
                for key in ("rows", "cleared", "missing", "focus"):
                    self.assertEqual(answer[key], [])
                self.assertNotIn("department_intent", answer)

    def test_outside_scope_turn_does_not_change_a_valid_draft(self):
        start = "采购角度，COMP-001换成COMP-998会有什么影响"
        interrupt = "请审批 COMP-001 换成 COMP-999 的工程变更"
        messages = [start, interrupt]
        rejected = self.request("/api/workflow/dialogue", {"messages": messages})
        self.assertEqual(rejected["status"], "outside_scope")
        recovered = self.request("/api/workflow/dialogue", {"messages": [*messages, "2026/12/1"]})
        self.assertEqual(recovered["fields"]["new_component"], "COMP-998")
        self.assertEqual(recovered["fields"]["effective_date"], "2026-12-01")
        self.assertFalse(recovered["missing"])

    def test_declined_commercial_order_can_be_followed_by_a_new_bom_question(self):
        messages = ["销售角度，信用额度冻结了，SO-55 能下单吗"]
        rejected = self.request("/api/workflow/dialogue", {"messages": messages})
        self.assertEqual(rejected["status"], "outside_scope")
        recovered = self.request("/api/workflow/dialogue", {
            "messages": [*messages, "销售角度，PART-A 换成 PART-B，2026/12/1 生效，影响哪些订单行"],
        })
        self.assertNotIn("intake", recovered)
        self.assertEqual(recovered["fields"]["new_component"], "PART-B")
        self.assertFalse(recovered["missing"])

    def test_dialogue_drops_stale_row_and_uses_prompted_bare_id(self):
        messages = [
            "从销售角度，2026-12-01 把 PART-OLD 换成 PART-NEW，成品 FG-T1、"
            "工厂 PLANT-T、工程版本 ENG-V2、BOM 行 V2-L2。",
            "版本改为 ENG-V1。",
        ]
        corrected = self.request("/api/workflow/dialogue", {"messages": messages})
        self.assertEqual(corrected["fields"]["engineering_version"], "ENG-V1")
        self.assertEqual(corrected["cleared"], ["target_bom_row"])
        self.assertEqual(corrected["missing"], ["target_bom_row"])
        filled = self.request("/api/workflow/dialogue", {"messages": [*messages, "V1-L1"]})
        self.assertEqual(filled["fields"]["target_bom_row"], "V1-L1")
        self.assertEqual(filled["cleared"], [])
        self.assertEqual(filled["missing"], [])

    def test_dialogue_keeps_missing_data_and_zero_explicit(self):
        partial = self.request("/api/workflow/dialogue", {"messages": [
            "从采购角度，采购工厂 PLANT-P，库位 STORE-A，想调整用量。",
            "把 PART-OLD 的用量从 2 改成 0。", "12月1日",
        ]})
        self.assertEqual(partial["fields"]["planning_plant"], "PLANT-P")
        self.assertNotIn("plant", partial["fields"])
        self.assertEqual(partial["fields"]["new_quantity"], 0)
        self.assertNotIn("effective_date", partial["fields"])
        self.assertIn("effective_date", partial["missing"])
        self.request("/api/workflow/dialogue", {"messages": ["x" * 501]}, status=400)


    def test_screenshot_dialogue_collects_replacement_without_reasking_for_parts(self):
        partial = self.request("/api/workflow/dialogue", {"messages": [SCREENSHOT]})
        expected = {"role": "procurement", "change_type": "replace",
                    "old_component": "COMP-001", "new_component": "COMP-998"}
        self.assertEqual(partial["fields"], expected)
        self.assertEqual(partial["missing"], ["effective_date"])
        for messages in (
            [SCREENSHOT, "2026/12/1", "采购工厂 S001"],
            ["我是采购，2026/12/1 起用 comp-998 替代 Comp-001，采购工厂 S001。"],
        ):
            with self.subTest(messages=messages):
                complete = self.request("/api/workflow/dialogue", {"messages": messages})
                self.assertEqual(complete["fields"], {**expected, "effective_date": "2026-12-01", "planning_plant": "S001"})
                self.assertEqual(complete["missing"], [])
        ambiguous = self.request("/api/workflow/dialogue", {"messages": [
            "我是采购，Comp-001 到货晚，改用 COMP-998 或 COMP-999，2026/12/1 生效。",
        ]})
        self.assertEqual(ambiguous["missing"], ["new_component"])
        self.assertNotIn("new_component", ambiguous["fields"])

    def test_delayed_delivery_with_replacement_across_clauses_and_turns(self):
        screenshot = "我是采购，COMP-001今天到不了货，换成COMP-998会有什么影响"
        target = {"role": "procurement", "change_type": "replace",
                  "old_component": "COMP-001", "new_component": "COMP-998"}
        for messages in (
            [screenshot],
            ["我是采购，COMP-001今天到不了货", "换成COMP-998会有什么影响"],
            ["我是采购，COMP-001买不到，想用COMP-998代替"],
            ["采购：COMP-001目前缺货，准备换成物料COMP-998"],
        ):
            with self.subTest(messages=messages):
                partial = self.request("/api/workflow/dialogue", {"messages": messages})
                self.assertEqual(partial["fields"], target)
                self.assertEqual(partial["missing"], ["effective_date"])
                for tail in (["2026/12/1"], ["替换", "2026/12/1"]):
                    finished = self.request("/api/workflow/dialogue", {"messages": [*messages, *tail]})
                    self.assertEqual(finished["fields"], {**target, "effective_date": "2026-12-01"})
                    self.assertEqual(finished["missing"], [])
        ready = self.request("/api/workflow/dialogue", {"messages": [screenshot + "，拟于2026/12/1生效"]})
        self.assertEqual(ready["missing"], [])

    def test_receipt_date_and_engineering_field_corrections_do_not_change_the_replacement(self):
        message = "我是采购，COMP-001今天到不了货，换成COMP-998，交期是2026/11/29"
        partial = self.request("/api/workflow/dialogue", {"messages": [message]})
        self.assertEqual(partial["missing"], ["effective_date"])
        self.assertNotIn("effective_date", partial["fields"])
        prep = self.request("/api/workflow/dialogue", {"messages": [
            message + "，2026/12/1生效", "生效日改成2026年12月2日",
        ]})
        self.assertEqual(prep["fields"]["new_component"], "COMP-998")
        self.assertEqual(prep["fields"]["effective_date"], "2026-12-02")
        shortage = self.request("/api/workflow/dialogue", {"messages": ["我是采购，COMP-001今天到不了货"]})
        self.assertEqual(shortage["fields"], {"role": "procurement", "old_component": "COMP-001"})

    def test_spoken_procurement_uses_supply_date_only_as_arrival_evidence(self):
        terms = "我们采购这边，供方说 MAT-704 要到2026/12/9才能到货，想改采 MAT-806"
        expected = dict(role="procurement", change_type="replace", old_component="MAT-704", new_component="MAT-806")
        for messages in ([terms], [terms, "拟生效日为2026/12/1"]):
            answer = self.request("/api/workflow/dialogue", {"messages": messages})
            if len(messages) == 1:
                self.assertEqual(answer["fields"], expected)
                self.assertEqual(answer["missing"], ["effective_date"])
            else:
                self.assertEqual(answer["fields"], {**expected, "effective_date": "2026-12-01"})
                self.assertEqual(answer["missing"], [])

    def test_procurement_corrections_update_only_the_named_component(self):
        messages = ["我是采购，采购工厂 S001，库位 0008，COMP-001 换成 COMP-998，2026/12/1生效。"]
        expected = dict(role="procurement", change_type="replace", effective_date="2026-12-01",
                        planning_plant="S001", storage_location="0008", old_component="COMP-001", new_component="COMP-998")
        for message, field, value in (
            ("刚才替代件说错了，应该是 COMP-997。", "new_component", "COMP-997"),
            ("原料号不是 COMP-001，是 COMP-011。", "old_component", "COMP-011"),
            ("原组件改成 COMP-012。", "old_component", "COMP-012"),
            ("新料号从 COMP-997 改成 COMP-996。", "new_component", "COMP-996"),
            ("原料号由 COMP-012 改为 COMP-013。", "old_component", "COMP-013"),
            ("替代件重新选，改用 ALT-11 或 ALT-12，还没定。", "new_component", None),
            ("最终用 ALT-12。", "new_component", "ALT-12"),
        ):
            with self.subTest(message=message):
                messages.append(message)
                result = self.request("/api/workflow/dialogue", {"messages": messages})
                if value is None:
                    del expected[field]
                    self.assertIn(field, result["cleared"])
                    self.assertEqual(result["missing"], [field])
                else:
                    expected[field] = value
                    self.assertNotIn(field, result["cleared"])
                    self.assertFalse(result["missing"])
                self.assertEqual(result["fields"], expected)

    def test_data_preparation_uses_the_same_parameters_before_and_after_import(self):
        before = self.preflight()
        self.import_bom()
        after = self.preflight()
        self.assertFalse(before["ready"])
        self.assertTrue(after["ready"])
        self.assertEqual(before["data_preparation"]["fields"], after["data_preparation"]["fields"])
        fields = {item["key"]: item["value"] for item in after["data_preparation"]["fields"]}
        self.assertEqual(fields["role"], "采购")
        self.assertEqual(fields["old_component"], "PART-OLD")
        self.assertEqual(fields["new_component"], "PART-NEW")
        self.assertEqual(fields["effective_date"], "2026-12-01")
        empty = {item["key"]: item["status"] for item in before["data_preparation"]["sources"]}
        self.assertEqual(empty, {"bom": "需补资料", "purchases": "需补资料", "receipts": "需补资料"})
        imported = {item["key"]: item["status"] for item in after["data_preparation"]["sources"]}
        self.assertEqual(imported, {"bom": "已找到目标物料", "purchases": "待核", "receipts": "待核"})
        other_plant = self.preflight(draft={"planning_plant": "UNRELATED-PLANT"})
        isolated = {item["key"]: item["status"] for item in other_plant["data_preparation"]["sources"]}
        self.assertEqual(isolated["purchases"], "需补资料")
        engineering = self.preflight(role="engineering", draft=QUERY)
        self.assertFalse(engineering["ready"])
        self.assertEqual([item["key"] for item in engineering["data_preparation"]["sources"]], ["ebom", "references"])
