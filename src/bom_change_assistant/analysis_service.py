"""Application service for impact analysis, batch composition and reports."""
from __future__ import annotations

import re
import secrets
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any

from . import engineering, procurement
from .impact_checks import order_demand_coverage, production_progress_metrics, sum_known_quantities
from .impact_engine import ImpactEngine
from .models import AnalysisBatch, AnalysisItem, ChangeRequest, RiskLevel
from .repository import Repository
from .summaries import markdown_report, role_checklists, rule_summary


class AnalysisService:
    def __init__(self, repository: Repository):
        self.repository = repository
        self.engine = ImpactEngine(repository)

    def analyze_changes(self, changes: list[dict[str, Any]]) -> AnalysisBatch:
        draft_id = f"DR-{secrets.token_hex(5).upper()}"
        prepared_results: list[dict[str, Any]] = []
        for index, payload in enumerate(changes, start=1):
            if not isinstance(payload, dict):
                raise ValueError("每个变更项目须为对象")
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
        ecn_values = self.repository.reserve_ecns(len(prepared_results))
        evidence = procurement.load_evidence(self.repository)
        queries: dict[str, procurement.ProcurementQuery] = {}
        items: list[AnalysisItem] = []
        for result, ecn, payload in zip(prepared_results, ecn_values, changes):
            result["procurement_query"] = {k: str(payload.get(k) or "") for k in ("planning_plant", "storage_location")}
            result["ecn"] = ecn
            if result.get("engineering_check"):
                result["engineering_check"]["ecn"] = ecn
                result["engineering_impact"] = engineering.group([result["engineering_check"]])
            query = procurement.query_for_change(result["change"], **result["procurement_query"], ecn=ecn)
            queries[result["analysis_id"]] = query
            procurement.attach_impact(result, procurement.analyze(evidence, [query]))
            result["summary"] = rule_summary(result)
            result["checklists"] = role_checklists(result)
            report = markdown_report(result)
            self.repository.save_analysis(
                result["analysis_id"], result, report
            )
            self.repository.save_change_log(
                self._change_log_entry(result)
            )
            items.append({"result": result, "report": report})

        groups: list[AnalysisItem] = []
        versions = list(dict.fromkeys(result["bom_version"] for result in prepared_results))
        for bom_version in versions:
            version_results = (
                self.repository.analysis_results_for_bom_version(
                    bom_version, draft_id
                )
            )
            ordinary = [item for item in version_results if not item.get("engineering_check")]
            combined = self._combine_bom_version_results(
                draft_id, bom_version, ordinary or version_results
            )
            combined["procurement_impact"] = (
                version_results[0]["procurement_impact"] if len(version_results) == 1 else
                procurement.analyze(evidence, [queries[item["analysis_id"]] for item in version_results])
            )
            combined["change_checks"] = [
                {
                    "ecn": item.get("ecn"),
                    "quantity_impact": deepcopy(item.get("quantity_impact")),
                    "effectivity_comparison": item.get("effectivity_comparison"),
                }
                for item in version_results
            ]
            for checked in combined["change_checks"]:
                for pending in (checked.get("quantity_impact") or {}).get("pending_paths", []):
                    pending["path_id"] = f"{checked['ecn']}-{pending['path_id']}"
            engineering_checks = [
                item["engineering_check"] for item in version_results if item.get("engineering_check")
            ]
            if engineering_checks:
                combined["engineering_impact"] = engineering.group(engineering_checks)
                combined["engineering_only"] = len(engineering_checks) == len(version_results)
                combined["ecns"] = [item["ecn"] for item in version_results]
                combined["ecn"] = "、".join(combined["ecns"])
                combined["change_item_count"] = len(version_results)
                combined["change"]["type_label"] = f"{len(version_results)} 项组合变更"
                combined["summary"] = rule_summary(combined)
            combined_report = markdown_report(combined)
            self.repository.save_analysis(
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
        if payload.get("analysis_mode") == "engineering" or any(
            payload.get(field) for field in ("engineering_version", "target_bom_row")
        ):
            result = self._engineering_result(payload)
        else:
            result = self.engine.analyze(request, include_procurement=False)
        analysis_id = f"AN-{secrets.token_hex(6).upper()}"
        result["analysis_id"] = analysis_id
        result["draft_id"] = draft_id
        result["change_item_no"] = item_no
        result["bom_version"] = bom_version
        return result

    def _engineering_result(self, payload: dict[str, Any]) -> dict[str, Any]:
        check = engineering.analyze(self.repository, payload)
        target = check["target"]
        parents = list(dict.fromkeys(row["parent"] for row in check["path"]))
        tops = [check["version"]["product"]] if check["path"] else []
        return {
            "engineering_only": True,
            "engineering_check": check,
            "change": {
                "type": check["change_type"],
                "type_label": engineering.CHANGE_TYPES[check["change_type"]],
                "target_parent": target["parent"],
                "old_component": check["old_component"],
                "new_component": check["new_component"],
                "effective_date": check["effective_date"],
            },
            "metrics": {
                "direct_parents": int(bool(parents)), "all_parents": len(parents),
                "direct_children": 0, "all_children": 0, "downstream_paths": 0,
                "upstream_paths": int(bool(check["path"])), "top_materials": len(tops),
                "sales_order_items": 0, "production_orders": 0,
            },
            "directions": {"upstream": {"paths": []}, "downstream": {"paths": []}},
            "direct_parents": [target["parent"]] if parents else [],
            "all_parents": parents, "direct_children": [], "all_children": [],
            "top_materials": tops, "paths": [], "sales_orders": [], "production_orders": [],
            "alternative_groups": [], "warnings": [],
            "risk": {"level": RiskLevel.MEDIUM, "reasons": [check["path_issue"] or "工程版本命中须结合订单资料评审"]},
            "evidence": {"edge_count": len(check["inspected_rows"])},
            "material_validation": {"applicable": False, "status": "不适用"},
            "calculated_on": datetime.now().date().isoformat(),
        }

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

        risk_level = max(
            (result["risk"]["level"] for result in results),
            key=RiskLevel.priority,
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
        demand_coverage = {
            item["item"]: item
            for item in order_demand_coverage(production_orders, sales_orders)
        }
        active_orders = [
            item for item in production_orders
            if ImpactEngine._production_needs_attention(item)
        ]
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
                "active_production_orders": len(active_orders),
                "affected_production_quantity": sum_known_quantities(
                    item.get("order_quantity") for item in production_orders
                ),
                **production_progress_metrics(active_orders),
                "affected_sales_items": len(sales_orders),
                "affected_sales_quantity": sum_known_quantities(
                    item.get("order_quantity")
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
            "coverage": [
                ({**item, "status": "需复核"}
                 if item.get("item") == "BOM结构与生效范围" and any(
                     coverage.get("item") == "BOM结构与生效范围" and coverage.get("status") == "需复核"
                     for impact in planning_items for coverage in impact.get("coverage", [])
                 ) else demand_coverage.get(item.get("item"), item))
                for item in planning_items[0].get("coverage", [])
            ]
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
