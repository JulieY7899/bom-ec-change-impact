from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import tempfile
import threading
import webbrowser
from copy import deepcopy
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .ai import AIUnavailable, explain_with_openai
from .config import (
    DATABASE_PATH,
    HOST,
    MAX_UPLOAD_BYTES,
    OPENAI_API_KEY_ENV,
    OPENAI_MODEL,
    PORT,
)
from .demo import load_demo_snapshot
from .impact_engine import ImpactEngine, ImpactError
from .importers import WorkbookImporter
from .models import ChangeRequest
from .repository import Repository
from .summaries import markdown_report, role_checklists, rule_summary

ASSET_DIR = Path(__file__).resolve().parent / "web_assets"
TEMPLATE_PATH = (
    Path(__file__).resolve().parents[2]
    / "outputs"
    / "bom-import-template"
    / "BOM工程变更数据导入模板.xlsx"
)
class AppState:
    def __init__(self, database_path: Path):
        self.repository = Repository(database_path)
        self.importer = WorkbookImporter(self.repository)
        self.engine = ImpactEngine(self.repository)
        self.import_lock = threading.Lock()


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    state: AppState
    server_version = "BOMAssistant/0.1"

    def log_message(self, *_: Any) -> None:
        # Intentionally disabled: request paths and user inputs must not enter logs.
        return

    def _send(
        self,
        status: int,
        body: bytes,
        content_type: str = "application/json; charset=utf-8",
        *,
        attachment: str | None = None,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self'; script-src 'self'; "
            "img-src 'self' data:; connect-src 'self'",
        )
        if attachment:
            self.send_header(
                "Content-Disposition", f'attachment; filename="{attachment}"'
            )
        self.end_headers()
        self.wfile.write(body)

    def _json(self, status: int, payload: Any) -> None:
        self._send(status, _json_bytes(payload))

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"ok": False, "error": message})

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length > 2 * 1024 * 1024:
            raise ValueError("请求内容过大")
        body = self.rfile.read(length)
        return json.loads(body.decode("utf-8") or "{}")

    def do_GET(self) -> None:
        route = urlparse(self.path)
        if route.path == "/api/health":
            self._json(
                200,
                {
                    "ok": True,
                    "service": "bom-change-assistant",
                    "version": "0.1",
                },
            )
            return
        if route.path == "/api/status":
            payload = self.state.repository.stats()
            payload.update(
                {
                    "ok": True,
                    "privacy_mode": "敏感文字脱敏",
                    "ai_configured": bool(os.environ.get(OPENAI_API_KEY_ENV)),
                    "ai_model": OPENAI_MODEL,
                }
            )
            self._json(200, payload)
            return
        if route.path == "/api/materials":
            query = parse_qs(route.query).get("q", [""])[0]
            self._json(
                200,
                {
                    "ok": True,
                    "items": self.state.repository.material_catalog(query, 120),
                },
            )
            return
        if route.path == "/api/material-effectivity":
            material = parse_qs(route.query).get("material", [""])[0].strip()
            if not material:
                self._error(400, "请先输入原组件物料号")
                return
            try:
                payload = self.state.repository.material_effectivity(material)
            except ValueError as exc:
                self._error(404, str(exc))
                return
            self._json(200, {"ok": True, **payload})
            return
        if route.path == "/api/analysis/last":
            latest = self.state.repository.last_analysis()
            if not latest:
                self._error(404, "尚未完成影响核查")
                return
            result, _ = latest
            report = markdown_report(
                result, str(result.get("ai_explanation", ""))
            )
            self._json(200, {"ok": True, "result": result, "report": report})
            return
        if route.path == "/api/change-log":
            self._json(
                200,
                {"ok": True, "items": self.state.repository.change_logs()},
            )
            return
        if route.path == "/api/draft-defaults":
            self._json(
                200,
                {"ok": True, **self.state.repository.draft_defaults()},
            )
            return
        if route.path == "/api/report.md":
            analysis_id = parse_qs(route.query).get("analysis_id", [""])[0]
            latest = (
                self.state.repository.analysis_by_id(analysis_id)
                if analysis_id
                else self.state.repository.last_analysis()
            )
            if not latest:
                self._error(404, "尚未生成核查报告")
                return
            result, _ = latest
            report = markdown_report(
                result, str(result.get("ai_explanation", ""))
            )
            self._send(
                200,
                report.encode("utf-8"),
                "text/markdown; charset=utf-8",
                attachment="BOM-impact-report.md",
            )
            return
        if route.path == "/api/import-template.xlsx":
            try:
                body = TEMPLATE_PATH.read_bytes()
            except FileNotFoundError:
                self._error(404, "导入模板尚未生成")
                return
            self._send(
                200,
                body,
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                attachment="BOM-import-template.xlsx",
            )
            return
        self._serve_asset(route.path)

    def _serve_asset(self, route_path: str) -> None:
        asset_name = {
            "/": "index.html",
            "/index.html": "index.html",
            "/styles.css": "styles.css",
            "/app.js": "app.js",
        }.get(route_path)
        if not asset_name:
            self._error(404, "页面不存在")
            return
        path = ASSET_DIR / asset_name
        content_types = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
        }
        try:
            body = path.read_bytes()
        except FileNotFoundError:
            self._error(404, "静态资源不存在")
            return
        self._send(200, body, content_types.get(path.suffix, "application/octet-stream"))

    def do_POST(self) -> None:
        route = urlparse(self.path)
        try:
            if route.path == "/api/snapshot/start":
                with self.state.import_lock:
                    self.state.repository.start_snapshot()
                    self.state.importer = WorkbookImporter(self.state.repository)
                self._json(200, {"ok": True})
                return
            if route.path == "/api/snapshot/finish":
                with self.state.import_lock:
                    stats = self.state.repository.finish_snapshot()
                self._json(200, {"ok": True, "stats": stats})
                return
            if route.path == "/api/demo":
                with self.state.import_lock:
                    stats = load_demo_snapshot(self.state.repository)
                    self.state.importer = WorkbookImporter(self.state.repository)
                self._json(200, {"ok": True, "stats": stats})
                return
            if route.path == "/api/import/file":
                self._import_file()
                return
            if route.path == "/api/analyze":
                self._analyze()
                return
            if route.path == "/api/analyze-batch":
                self._analyze_batch()
                return
            if route.path == "/api/ai":
                self._ai_explain()
                return
            self._error(404, "接口不存在")
        except (ValueError, ImpactError) as exc:
            self._error(400, str(exc))
        except Exception:
            # Do not include raw exception details: they may contain local paths.
            self._error(500, "处理失败；请检查文件格式或数据映射后重试。")

    def _import_file(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            raise ValueError("没有收到文件内容")
        if length > MAX_UPLOAD_BYTES:
            raise ValueError("单个文件超过本地上传大小上限")
        suffix = ".xlsx"
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as temporary:
                temporary_path = Path(temporary.name)
                remaining = length
                while remaining:
                    chunk = self.rfile.read(min(1024 * 1024, remaining))
                    if not chunk:
                        break
                    temporary.write(chunk)
                    remaining -= len(chunk)
            with self.state.import_lock:
                result = self.state.importer.import_file(temporary_path)
            self._json(200, {"ok": True, "result": result.to_dict()})
        finally:
            if temporary_path:
                temporary_path.unlink(missing_ok=True)

    def _analyze(self) -> None:
        payload = self._read_json()
        response = self._analyze_changes([payload])
        item = response["groups"][0]
        self._json(
            200,
            {"ok": True, "result": item["result"], "report": item["report"]},
        )

    def _analyze_batch(self) -> None:
        payload = self._read_json()
        changes = payload.get("changes")
        if not isinstance(changes, list) or not changes:
            raise ValueError("本次核查至少需要一个变更项目")
        if len(changes) > 20:
            raise ValueError("单次核查最多支持 20 个变更项目")
        response = self._analyze_changes(changes)
        self._json(200, {"ok": True, **response})

    def _analyze_changes(self, changes: list[dict[str, Any]]) -> dict[str, Any]:
        draft_id = f"DR-{secrets.token_hex(5).upper()}"
        prepared_results: list[dict[str, Any]] = []
        for index, payload in enumerate(changes, start=1):
            bom_version = str(payload.get("bom_version", "")).strip()
            if not bom_version:
                raise ValueError("每个变更项目都必须填写核查版本")
            if not re.fullmatch(r"BOM-\d{6}", bom_version):
                raise ValueError("核查版本必须使用 BOM-000001 格式")
            prepared_results.append(
                self._analyze_change(
                    payload,
                    draft_id=draft_id,
                    item_no=index,
                    bom_version=bom_version,
                )
            )
        ecn_values = self.state.repository.reserve_ecns(len(prepared_results))
        items: list[dict[str, Any]] = []
        for result, ecn in zip(prepared_results, ecn_values):
            result["ecn"] = ecn
            result["summary"] = rule_summary(result)
            report = markdown_report(result)
            self.state.repository.save_analysis(
                result["analysis_id"], result, report
            )
            self.state.repository.save_change_log(
                self._change_log_entry(result)
            )
            items.append({"result": result, "report": report})

        groups: list[dict[str, Any]] = []
        versions = list(dict.fromkeys(result["bom_version"] for result in prepared_results))
        for bom_version in versions:
            version_results = (
                self.state.repository.analysis_results_for_bom_version(
                    bom_version
                )
            )
            combined = self._combine_bom_version_results(
                draft_id, bom_version, version_results
            )
            combined_report = markdown_report(combined)
            self.state.repository.save_analysis(
                combined["analysis_id"], combined, combined_report
            )
            groups.append({"result": combined, "report": combined_report})
        return {"draft_id": draft_id, "items": items, "groups": groups}

    def _analyze_change(
        self,
        payload: dict[str, Any],
        *,
        draft_id: str,
        item_no: int,
        bom_version: str,
    ) -> dict[str, Any]:
        request = ChangeRequest(
            change_type=str(payload.get("change_type", "")),
            old_component=str(payload.get("old_component", "")),
            new_component=str(payload.get("new_component", "")),
            target_parent=str(payload.get("target_parent", "")),
            old_quantity=payload.get("old_quantity"),
            new_quantity=payload.get("new_quantity"),
            effective_date=str(payload.get("effective_date", "")),
            old_effective_date=str(payload.get("old_effective_date", "")),
            new_effective_date=str(payload.get("new_effective_date", "")),
            bom_version=bom_version,
            note=str(payload.get("note", "")),
            old_on_hand=payload.get("old_on_hand"),
            old_in_transit=payload.get("old_in_transit"),
            old_open_po=payload.get("old_open_po"),
            old_liability=payload.get("old_liability"),
            old_consumable_demand=payload.get("old_consumable_demand"),
            new_on_hand=payload.get("new_on_hand"),
            new_in_transit=payload.get("new_in_transit"),
            new_open_po=payload.get("new_open_po"),
            new_required_demand=payload.get("new_required_demand"),
            planning_plant=str(payload.get("planning_plant", "")),
            storage_location=str(payload.get("storage_location", "")),
            disposition=str(payload.get("disposition", "")),
            disposition_code=str(payload.get("disposition_code", "")),
            disposition_scope=str(payload.get("disposition_scope", "")),
            disposition_owner=str(payload.get("disposition_owner", "")),
            disposition_status=str(
                payload.get("disposition_status", "待评审")
            ),
            disposition_note=str(payload.get("disposition_note", "")),
        )
        result = self.state.engine.analyze(request)
        analysis_id = f"AN-{secrets.token_hex(6).upper()}"
        result["analysis_id"] = analysis_id
        result["draft_id"] = draft_id
        result["change_item_no"] = item_no
        result["bom_version"] = bom_version
        result["summary"] = rule_summary(result)
        result["checklists"] = role_checklists(result)
        return result

    @staticmethod
    def _change_log_entry(result: dict[str, Any]) -> dict[str, Any]:
        change = result["change"]
        target = change["target_parent"] or change["old_component"]
        return {
            "log_id": f"CL-{secrets.token_hex(6).upper()}",
            "draft_id": result["draft_id"],
            "analysis_id": result["analysis_id"],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "change_type": change["type"],
            "change_type_label": change["type_label"],
            "ecn": result["ecn"],
            "bom_version": result["bom_version"],
            "target_material": target,
            "effective_date": change["effective_date"],
            "old_effective_date": change.get("old_effective_date", ""),
            "new_effective_date": change.get("new_effective_date", ""),
            "risk_level": result["risk"]["level"],
            "upstream_paths": result["metrics"]["upstream_paths"],
            "downstream_paths": result["metrics"]["downstream_paths"],
            "top_materials": result["metrics"]["top_materials"],
            "material_validation_status": result["material_validation"]["status"],
            "summary": result["summary"],
        }

    @staticmethod
    def _combine_bom_version_results(
        draft_id: str,
        bom_version: str,
        results: list[dict[str, Any]],
    ) -> dict[str, Any]:
        def unique_records(
            records: list[dict[str, Any]],
            fields: tuple[str, ...],
        ) -> list[dict[str, Any]]:
            seen: set[tuple[Any, ...]] = set()
            output: list[dict[str, Any]] = []
            for record in records:
                key = tuple(record.get(field) for field in fields)
                if key in seen:
                    continue
                seen.add(key)
                output.append(deepcopy(record))
            return output

        def combined_paths(direction: str) -> list[dict[str, Any]]:
            output: list[dict[str, Any]] = []
            for result in results:
                for path in result["directions"][direction]["paths"]:
                    item = deepcopy(path)
                    item["path_id"] = f"{result['ecn']}-{path['path_id']}"
                    item["ecn"] = result["ecn"]
                    output.append(item)
            return output

        upstream_paths = combined_paths("upstream")
        downstream_paths = combined_paths("downstream")
        direct_parents = sorted(
            {
                value
                for result in results
                for value in result["direct_parents"]
            }
        )
        all_parents = sorted(
            {value for result in results for value in result["all_parents"]}
        )
        direct_children = sorted(
            {
                value
                for result in results
                for value in result["direct_children"]
            }
        )
        all_children = sorted(
            {value for result in results for value in result["all_children"]}
        )
        top_materials = sorted(
            {value for result in results for value in result["top_materials"]}
        )
        sales_orders = unique_records(
            [item for result in results for item in result["sales_orders"]],
            ("source_id", "source_row", "sales_order", "item"),
        )
        production_orders = unique_records(
            [item for result in results for item in result["production_orders"]],
            ("source_id", "source_row", "production_order"),
        )
        for index, item in enumerate(sales_orders, start=1):
            item["evidence_id"] = f"SOE-{index:03d}"
        for index, item in enumerate(production_orders, start=1):
            item["evidence_id"] = f"MOE-{index:03d}"

        warnings: list[dict[str, Any]] = []
        for result in results:
            for warning in result["warnings"]:
                item = deepcopy(warning)
                item["warning_id"] = f"W-{len(warnings) + 1:03d}"
                item["message"] = f"{result['ecn']}：{warning['message']}"
                warnings.append(item)

        level_order = {"低": 0, "中": 1, "高": 2}
        risk_level = max(
            (result["risk"]["level"] for result in results),
            key=lambda level: level_order.get(level, 0),
        )
        risk_reasons = [
            f"{result['ecn']}：{reason}"
            for result in results
            if result["risk"]["level"] == risk_level
            for reason in result["risk"]["reasons"]
        ]

        validations = [
            (result["ecn"], result["material_validation"])
            for result in results
            if result["material_validation"].get("applicable")
        ]
        validation_order = {"不适用": 0, "通过": 1, "待补证": 2, "不通过": 3}
        validation_status = (
            max(
                (validation["status"] for _, validation in validations),
                key=lambda status: validation_order.get(status, 0),
            )
            if validations
            else "不适用"
        )
        validation_checks = [
            {
                **deepcopy(check),
                "label": (
                    f"{ecn} · {validation['material']} · {check['label']}"
                ),
            }
            for ecn, validation in validations
            for check in validation["checks"]
        ]
        material_validation = {
            "applicable": bool(validations),
            "material": f"{len(validations)} 个新组件" if validations else "",
            "status": validation_status,
            "checks": validation_checks,
            "evidence_sources": sorted(
                {
                    source
                    for _, validation in validations
                    for source in validation.get("evidence_sources", [])
                }
            ),
        }

        metrics = {
            "direct_parents": len(direct_parents),
            "all_parents": len(all_parents),
            "direct_children": len(direct_children),
            "all_children": len(all_children),
            "upstream_paths": len(upstream_paths),
            "downstream_paths": len(downstream_paths),
            "top_materials": len(top_materials),
            "paths": len(upstream_paths) + len(downstream_paths),
            "sales_order_items": len(sales_orders),
            "production_orders": len(production_orders),
        }
        ecn_list = [result["ecn"] for result in results]
        draft_ids = list(
            dict.fromkeys(result.get("draft_id", draft_id) for result in results)
        )
        dates = sorted(
            {
                result["change"]["effective_date"]
                for result in results
                if result["change"]["effective_date"]
            }
        )
        summary = (
            f"{bom_version} 综合包含 {len(results)} 个变更项目（"
            f"{'、'.join(ecn_list)}），涉及 {metrics['top_materials']} 个顶层成品，关联 "
            f"{metrics['sales_order_items']} 个销售订单行和 "
            f"{metrics['production_orders']} 个生产订单。按预设规则，当前关注等级为"
            f"“{risk_level}”。BOM 使用关系共核查 "
            f"{metrics['upstream_paths']} 条成品使用路径和 "
            f"{metrics['downstream_paths']} 条下层展开路径，包含 "
            f"{metrics['direct_parents']} 个直接父件、"
            f"{metrics['all_parents']} 个全部上层物料、"
            f"{metrics['direct_children']} 个直接下层物料和 "
            f"{metrics['all_children']} 个全部下层物料。"
        )
        checklists = {
            role: list(
                dict.fromkeys(
                    item
                    for result in results
                    for item in result.get("checklists", {}).get(role, [])
                )
            )
            for role in ("研发", "计划", "制造")
        }
        planning_items = [
            result.get("planning_impact", {}) for result in results
        ]
        recommendation_rank = {
            "待补供应数据": 0,
            "情景数据待补齐": 1,
            "Hard Cut": 2,
            "Run-in Change": 3,
            "暂缓维护 Future BOM": 4,
        }
        planning_recommendation = max(
            (
                item.get("recommendation", "待补供应数据")
                for item in planning_items
            ),
            key=lambda value: recommendation_rank.get(value, 0),
        )
        planning_impact = {
            "stage": "第一阶段",
            "status": "按核查版本汇总需求侧影响",
            "scope": (
                planning_items[0].get("scope", {})
                if len(planning_items) == 1
                else {
                    "material": "按各 ECN 变更料号",
                    "plant": "按各 ECN 备料情景",
                    "storage_location": "按各 ECN 备料情景",
                }
            ),
            "metrics": {
                "affected_production_orders": len(production_orders),
                "active_production_orders": sum(
                    item.get("status") in {"已释放/执行中"}
                    or (
                        item.get("status") == "已完成"
                        and float(item.get("undelivered_quantity") or 0) > 0
                    )
                    for item in production_orders
                ),
                "affected_production_quantity": sum(
                    float(item.get("order_quantity") or 0)
                    for item in production_orders
                ),
                "remaining_production_quantity": sum(
                    float(item.get("undelivered_quantity") or 0)
                    for item in production_orders
                    if item.get("status") in {"已释放/执行中"}
                    or (
                        item.get("status") == "已完成"
                        and float(item.get("undelivered_quantity") or 0) > 0
                    )
                ),
                "affected_sales_items": len(sales_orders),
                "affected_sales_quantity": sum(
                    float(item.get("order_quantity") or 0)
                    for item in sales_orders
                ),
                "linked_sales_items": len(sales_orders),
                "action_sales_items": sum(
                    item.get("status") not in {"未知", "其他/待确认"}
                    for item in sales_orders
                ),
                "pending_sales_items": sum(
                    item.get("status") in {"未知", "其他/待确认"}
                    for item in sales_orders
                ),
            },
            "coverage": planning_items[0].get("coverage", [])
            if planning_items
            else [],
            "scenario": {
                "provided": any(
                    item.get("scenario", {}).get("provided")
                    for item in planning_items
                ),
                "complete": all(
                    item.get("scenario", {}).get("complete")
                    for item in planning_items
                ),
                "origin": (
                    planning_items[0].get("scenario", {}).get("origin")
                    if len(planning_items) == 1
                    else "按各 ECN 分别测算"
                ),
                "scenario_id": (
                    planning_items[0].get("scenario", {}).get("scenario_id")
                    if len(planning_items) == 1
                    else None
                ),
                "data_attribute": (
                    planning_items[0].get("scenario", {}).get("data_attribute")
                    if len(planning_items) == 1
                    else None
                ),
                "data_date": (
                    planning_items[0].get("scenario", {}).get("data_date")
                    if len(planning_items) == 1
                    else None
                ),
                "items": [
                    {
                        "ecn": result["ecn"],
                        "scenario_id": result.get(
                            "planning_impact", {}
                        ).get("scenario", {}).get("scenario_id"),
                        "recommendation": result.get(
                            "planning_impact", {}
                        ).get("recommendation", "待补供应数据"),
                        "rationale": result.get(
                            "planning_impact", {}
                        ).get("rationale", ""),
                    }
                    for result in results
                ],
            },
            "recommendation": planning_recommendation,
            "rationale": "综合建议取各变更项目中约束最强的备料结论。",
            "boundary": "计划切换建议由完整备料情景驱动；状态未知订单只列为待确认关联对象，不参与建议，不代替ERP供需平衡或制造对象识别。",
        }
        routing_assignments = unique_records(
            [
                item
                for result in results
                for item in result.get("manufacturing_impact", {}).get(
                    "routing_assignments", []
                )
            ],
            ("material", "plant", "routing_group", "group_counter"),
        )
        manufacturing_actions = []
        manufacturing_review_objects = []
        disposition_records = []
        recommendation_records = []
        for result in results:
            manufacturing = result.get("manufacturing_impact", {})
            disposition_records.append(
                {
                    "ecn": result["ecn"],
                    "code": manufacturing.get("disposition_code", "UNCONFIRMED"),
                    "name": manufacturing.get("disposition_name", "未确认"),
                    "scope": manufacturing.get("disposition_scope", "待确认"),
                    "owner": manufacturing.get("disposition_owner", "制造工程"),
                    "approval_status": manufacturing.get(
                        "approval_status", "待评审"
                    ),
                }
            )
            recommendation_records.append(
                {
                    "ecn": result["ecn"],
                    **deepcopy(
                        manufacturing.get("recommended_disposition", {})
                    ),
                }
            )
            for review_object in manufacturing.get("review_objects", []):
                manufacturing_review_objects.append(
                    {"ecn": result["ecn"], **deepcopy(review_object)}
                )
            for action in result.get("manufacturing_impact", {}).get(
                "actions", []
            ):
                manufacturing_actions.append(
                    {"ecn": result["ecn"], **deepcopy(action)}
                )
        manufacturing_impact = {
            "stage": "第一阶段",
            "disposition": "按各ECN分别记录",
            "disposition_records": disposition_records,
            "recommended_dispositions": recommendation_records,
            "approval_status": (
                "已批准"
                if disposition_records
                and all(
                    item["approval_status"] == "已批准"
                    for item in disposition_records
                )
                else "待评审"
            ),
            "routing_status": "工艺路线数据仅作来源证据"
            if routing_assignments
            else "未匹配工艺路线来源",
            "operation_detail_status": "当前仅用于辅助识别对象，不作工序结论",
            "routing_assignments": routing_assignments,
            "review_objects": manufacturing_review_objects,
            "review_object_count": len(manufacturing_review_objects),
            "data_gaps": sorted(
                {
                    gap
                    for result in results
                    for gap in result.get("manufacturing_impact", {}).get(
                        "data_gaps", []
                    )
                }
            ),
            "stage_counts": {
                stage: sum(
                    result.get("manufacturing_impact", {})
                    .get("stage_counts", {})
                    .get(stage, 0)
                    for result in results
                )
                for stage in (
                    "WIP/生产中",
                    "完工未交付",
                    "已关闭",
                    "状态待确认",
                )
            },
            "actions": manufacturing_actions,
            "boundary": "制造模块只识别需要ME确认的订单对象并给出各ECN候选Code，不判断已装配工序、返工路径或质量放行。",
        }
        analysis_id = f"AN-BOM-{secrets.token_hex(5).upper()}"
        return {
            "analysis_id": analysis_id,
            "analysis_kind": "bom_version",
            "draft_id": "、".join(draft_ids),
            "draft_ids": draft_ids,
            "ecn": "、".join(ecn_list),
            "ecns": ecn_list,
            "bom_version": bom_version,
            "change_item_count": len(results),
            "change": {
                "type": "batch",
                "type_label": f"{len(results)} 项组合变更",
                "propagation_direction": "both",
                "propagation_direction_label": "成品及下层物料综合核查",
                "old_component": "多个变更对象",
                "new_component": "",
                "target_parent": "",
                "old_quantity": None,
                "new_quantity": None,
                "effective_date": "、".join(dates) or "—",
                "old_effective_date": "",
                "new_effective_date": "",
                "note": "",
            },
            "direction": {
                "value": "both",
                "label": "按核查版本汇总成品及下层物料",
            },
            "directions": {
                "upstream": {"paths": upstream_paths},
                "downstream": {"paths": downstream_paths},
            },
            "risk": {"level": risk_level, "reasons": risk_reasons},
            "metrics": metrics,
            "direct_parents": direct_parents,
            "all_parents": all_parents,
            "direct_children": direct_children,
            "all_children": all_children,
            "top_materials": top_materials,
            "paths": upstream_paths + downstream_paths,
            "sales_orders": sales_orders,
            "production_orders": production_orders,
            "alternative_groups": sorted(
                {
                    value
                    for result in results
                    for value in result["alternative_groups"]
                }
            ),
            "change_history": {"referenced": 0, "matched": 0, "items": []},
            "warnings": warnings,
            "evidence": {
                "edge_count": sum(
                    result["evidence"]["edge_count"] for result in results
                ),
                "source_rule": "按核查版本汇总各 ECN 参考号的脱敏路径与行级来源记录。",
                "data_snapshot": "当前本机脱敏数据",
            },
            "material_validation": material_validation,
            "planning_impact": planning_impact,
            "manufacturing_impact": manufacturing_impact,
            "calculated_on": results[0]["calculated_on"],
            "summary": summary,
            "checklists": checklists,
        }

    def _ai_explain(self) -> None:
        length = int(self.headers.get("Content-Length", "0") or "0")
        payload = self._read_json() if length else {}
        analysis_id = str(payload.get("analysis_id", "")).strip()
        latest = (
            self.state.repository.analysis_by_id(analysis_id)
            if analysis_id
            else self.state.repository.last_analysis()
        )
        if not latest:
            raise ValueError("请先完成一次影响核查")
        result, _ = latest
        try:
            text = explain_with_openai(result, self.state.repository)
        except AIUnavailable as exc:
            self._error(409, str(exc))
            return
        result["ai_explanation"] = text
        report = markdown_report(result, text)
        self.state.repository.save_analysis(result["analysis_id"], result, report)
        self._json(200, {"ok": True, "text": text, "report": report})


def run_server(
    host: str = HOST,
    port: int = PORT,
    database_path: Path = DATABASE_PATH,
    open_browser: bool = True,
) -> None:
    state = AppState(database_path)
    handler = type("BoundHandler", (Handler,), {"state": state})
    server = ThreadingHTTPServer((host, port), handler)
    url = f"http://{host}:{port}"
    print(f"BOM 工程变更影响分析助手已启动：{url}")
    print("数据仅在本机处理。按 Control-C 停止。")
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description="BOM 工程变更影响分析助手")
    parser.add_argument("--host", default=HOST)
    parser.add_argument("--port", type=int, default=PORT)
    parser.add_argument("--database", type=Path, default=DATABASE_PATH)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()
    run_server(
        host=args.host,
        port=args.port,
        database_path=args.database,
        open_browser=not args.no_browser,
    )


if __name__ == "__main__":
    main()
