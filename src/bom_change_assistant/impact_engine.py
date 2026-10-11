from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from dataclasses import replace
from datetime import date
from typing import Any

from .config import MAX_ENUMERATED_PATHS
from .dispositions import resolve_disposition
from .models import ChangeRequest
from .normalization import coerce_date, safe_text
from .repository import Repository

VALID_CHANGE_TYPES = {"add", "remove", "replace", "quantity", "effectivity"}
VALID_DIRECTIONS = {"upstream", "downstream"}
PROPOSED_COMPONENT_KEY = "__PROPOSED_COMPONENT__"


class ImpactError(ValueError):
    pass


class ImpactEngine:
    def __init__(self, repository: Repository):
        self.repository = repository

    def analyze(self, request: ChangeRequest) -> dict[str, Any]:
        """Calculate both business views for every proposed BOM change."""
        upstream = self._analyze_direction(
            replace(request, propagation_direction="upstream")
        )
        downstream = self._analyze_direction(
            replace(request, propagation_direction="downstream")
        )
        result = self._combine_directions(upstream, downstream)
        if request.change_type in {"add", "replace"}:
            validation = self.repository.material_readiness(request.new_component)
            result["material_validation"] = validation
            if validation["status"] != "通过":
                severity = "重要" if validation["status"] == "不通过" else "提醒"
                result["warnings"].append(
                    {
                        "warning_id": f"W-{len(result['warnings']) + 1:03d}",
                        "code": "MATERIAL_READINESS",
                        "severity": severity,
                        "message": (
                            f"新组件物料主数据与寻源验证状态为“{validation['status']}”；"
                            "请在放行前完成缺失或不合规项目。"
                        ),
                    }
                )
                reason = f"新组件物料与寻源验证为{validation['status']}"
                if reason not in result["risk"]["reasons"]:
                    result["risk"]["reasons"].append(reason)
                if validation["status"] == "不通过":
                    result["risk"]["level"] = "高"
                elif result["risk"]["level"] == "低":
                    result["risk"]["level"] = "中"
        else:
            result["material_validation"] = {
                "applicable": False,
                "material": "",
                "status": "不适用",
                "checks": [],
                "evidence_sources": [],
            }
        result["planning_impact"] = self._planning_impact(request, result)
        result["manufacturing_impact"] = self._manufacturing_impact(
            request, result
        )
        return result

    def _analyze_direction(self, request: ChangeRequest) -> dict[str, Any]:
        if request.change_type not in VALID_CHANGE_TYPES:
            raise ImpactError("不支持的变更类型")
        direction = request.propagation_direction or "upstream"
        if direction not in VALID_DIRECTIONS:
            raise ImpactError("不支持的核查方向")
        if request.change_type in {"add", "replace"} and not request.new_component.strip():
            raise ImpactError("新增或替换组件时必须填写新组件")
        if request.change_type == "effectivity":
            old_effective_date = coerce_date(request.old_effective_date)
            new_effective_date = coerce_date(request.new_effective_date)
            if not old_effective_date or not new_effective_date:
                raise ImpactError("改变生效日期时必须填写原生效日期和新生效日期")
            if old_effective_date == new_effective_date:
                raise ImpactError("新旧生效日期不能相同")
            effective_date = new_effective_date
        else:
            effective_date = coerce_date(request.effective_date)
        if not effective_date:
            raise ImpactError("请输入有效的计划生效日期")

        is_add = request.change_type == "add"
        lookup_value = request.target_parent if is_add else request.old_component
        if not lookup_value:
            raise ImpactError("新增组件需要目标父件；其他变更需要原组件")
        anchor_key = self.repository.resolve_material(lookup_value)
        if not anchor_key:
            label = "目标父件" if is_add else "原组件"
            raise ImpactError(f"未在当前导入数据中找到{label}")

        alias_maps = self.repository.alias_maps()
        anchor_column = "component_key" if direction == "upstream" else "parent_key"
        with self.repository.connect() as connection:
            edge_rows = connection.execute(
                """
                SELECT * FROM bom_edges
                WHERE date_valid=1
                  AND (valid_from IS NULL OR valid_from<=?)
                  AND (valid_to IS NULL OR valid_to>=?)
                ORDER BY top_key, component_key, parent_key, edge_id
                """,
                (effective_date, effective_date),
            ).fetchall()
            invalid_anchor_dates = connection.execute(
                f"""
                SELECT COUNT(*) FROM bom_edges
                WHERE ({anchor_column}=? OR top_key=?) AND date_valid=0
                """,
                (anchor_key, anchor_key),
            ).fetchone()[0]
            all_anchor_uses = connection.execute(
                f"SELECT COUNT(*) FROM bom_edges "
                f"WHERE {anchor_column}=? OR top_key=?",
                (anchor_key, anchor_key),
            ).fetchone()[0]

        upward: dict[tuple[str, str], list[Any]] = defaultdict(list)
        downward: dict[tuple[str, str], list[Any]] = defaultdict(list)
        top_keys: set[str] = set()
        for row in edge_rows:
            upward[(row["top_key"], row["component_key"])].append(row)
            downward[(row["top_key"], row["parent_key"])].append(row)
            top_keys.add(row["top_key"])

        candidate_tops = sorted(
            top
            for top in top_keys
            if (
                (top, anchor_key)
                in (upward if direction == "upstream" else downward)
                or top == anchor_key
            )
        )

        raw_paths: list[tuple[str, list[str], list[Any]]] = []
        cycles: list[list[str]] = []
        incomplete_branches = 0
        truncated = False

        def walk_upstream(
            top_key: str,
            current: str,
            nodes: list[str],
            edges: list[Any],
            visited: set[str],
        ) -> None:
            nonlocal incomplete_branches, truncated
            if len(raw_paths) >= MAX_ENUMERATED_PATHS:
                truncated = True
                return
            if current == top_key:
                raw_paths.append((top_key, list(nodes), list(edges)))
                return
            parents = upward.get((top_key, current), [])
            if not parents:
                incomplete_branches += 1
                return
            for edge in parents:
                parent = edge["parent_key"]
                if parent in visited:
                    cycles.append(nodes + [parent])
                    continue
                walk_upstream(
                    top_key,
                    parent,
                    nodes + [parent],
                    edges + [edge],
                    visited | {parent},
                )

        def walk_downstream(
            top_key: str,
            current: str,
            nodes: list[str],
            edges: list[Any],
            visited: set[str],
        ) -> None:
            nonlocal truncated
            if len(raw_paths) >= MAX_ENUMERATED_PATHS:
                truncated = True
                return
            children = downward.get((top_key, current), [])
            if not children:
                if edges:
                    raw_paths.append((top_key, list(nodes), list(edges)))
                return
            for edge in children:
                child = edge["component_key"]
                if child in visited:
                    cycles.append(nodes + [child])
                    continue
                walk_downstream(
                    top_key,
                    child,
                    nodes + [child],
                    edges + [edge],
                    visited | {child},
                )

        for top_key in candidate_tops:
            if direction == "upstream":
                walk_upstream(top_key, anchor_key, [anchor_key], [], {anchor_key})
            else:
                walk_downstream(top_key, anchor_key, [anchor_key], [], {anchor_key})

        if is_add and direction == "downstream":
            for top_key in candidate_tops:
                raw_paths.insert(
                    0,
                    (
                        top_key,
                        [anchor_key, PROPOSED_COMPONENT_KEY],
                        [None],
                    ),
                )

        def material_alias(key: str) -> str:
            if key == PROPOSED_COMPONENT_KEY:
                return safe_text(request.new_component, 120) or "待填写物料"
            return alias_maps.get("material", {}).get(key, "MAT-UNKNOWN")

        def top_alias(key: str) -> str:
            return alias_maps.get("top", {}).get(key, material_alias(key))

        direct_edge_rows = [
            edge
            for top in candidate_tops
            for edge in (
                upward.get((top, anchor_key), [])
                if direction == "upstream"
                else downward.get((top, anchor_key), [])
            )
        ]
        direct_related_keys = {
            edge["parent_key"]
            if direction == "upstream"
            else edge["component_key"]
            for edge in direct_edge_rows
        }
        if is_add:
            if direction == "upstream":
                direct_related_keys.add(anchor_key)
            else:
                direct_related_keys.add(PROPOSED_COMPONENT_KEY)

        all_related_keys = {
            node
            for _, nodes, _ in raw_paths
            for node in nodes[1:]
            if direction == "downstream" or node not in candidate_tops
        } | direct_related_keys
        impacted_top_keys = (
            set(candidate_tops)
            if direction == "downstream"
            else {top for top, _, _ in raw_paths}
        )
        if anchor_key in top_keys:
            impacted_top_keys.add(anchor_key)

        paths: list[dict[str, Any]] = []
        traversed_edge_ids: set[int] = set()
        traversed_change_keys: set[str] = set()
        alternative_keys: set[str] = set()
        for index, (top_key, nodes, edges) in enumerate(raw_paths, start=1):
            edge_evidence = []
            for edge in edges:
                if edge is None:
                    edge_evidence.append(
                        {
                            "edge_id": "PROPOSAL",
                            "source_id": "拟议变更",
                            "source_row": None,
                            "quantity": request.new_quantity,
                            "unit": "",
                            "valid_from": effective_date,
                            "valid_to": None,
                            "change": "",
                            "alternative_group": "",
                        }
                    )
                    continue
                traversed_edge_ids.add(edge["edge_id"])
                if edge["change_key"]:
                    traversed_change_keys.add(edge["change_key"])
                if edge["alternative_key"]:
                    alternative_keys.add(edge["alternative_key"])
                edge_evidence.append(
                    {
                        "edge_id": f"E-{edge['edge_id']:05d}",
                        "source_id": edge["source_id"],
                        "source_row": edge["source_row"],
                        "quantity": edge["quantity"],
                        "unit": edge["unit"] or "",
                        "valid_from": edge["valid_from"],
                        "valid_to": edge["valid_to"],
                        "change": alias_maps.get("change", {}).get(
                            edge["change_key"], ""
                        ),
                        "alternative_group": alias_maps.get("alternative", {}).get(
                            edge["alternative_key"], ""
                        ),
                    }
                )
            path_nodes = list(nodes)
            if is_add and direction == "upstream":
                path_nodes = [PROPOSED_COMPONENT_KEY] + path_nodes
                edge_evidence = [
                    {
                        "edge_id": "PROPOSAL",
                        "source_id": "拟议变更",
                        "source_row": None,
                        "quantity": request.new_quantity,
                        "unit": "",
                        "valid_from": effective_date,
                        "valid_to": None,
                        "change": "",
                        "alternative_group": "",
                    }
                ] + edge_evidence
            node_details = []
            for node_index, key in enumerate(path_nodes):
                if direction == "upstream":
                    if key == PROPOSED_COMPONENT_KEY:
                        role = "拟新增物料"
                    elif node_index == 0:
                        role = "变更料号"
                    elif node_index == len(path_nodes) - 1:
                        role = "顶层成品"
                    else:
                        role = "上层父件"
                else:
                    if key == PROPOSED_COMPONENT_KEY:
                        role = "拟新增物料"
                    elif node_index == 0:
                        role = "变更料号"
                    elif node_index == len(path_nodes) - 1:
                        role = "末端物料"
                    else:
                        role = "下层物料"
                node_details.append(
                    {"alias": material_alias(key), "role": role}
                )
            display_nodes = [node["alias"] for node in node_details]
            paths.append(
                {
                    "path_id": f"P-{index:03d}",
                    "direction": direction,
                    "direction_label": (
                        "向上：变更料号 → 顶层成品"
                        if direction == "upstream"
                        else "向下：变更料号 → 末端物料"
                    ),
                    "top_material": top_alias(top_key),
                    "top_material_node": material_alias(top_key),
                    "nodes": display_nodes,
                    "node_details": node_details,
                    "path_text": " → ".join(display_nodes),
                    "path_text_labeled": " → ".join(
                        f"{node['alias']}（{node['role']}）"
                        for node in node_details
                    ),
                    "levels": max(0, len(display_nodes) - 1),
                    "evidence": edge_evidence,
                }
            )

        sales_orders, production_orders = self._orders(
            impacted_top_keys, alias_maps
        )
        change_history = self._change_history(traversed_change_keys, alias_maps)

        warnings: list[dict[str, str]] = []

        def warn(code: str, message: str, severity: str = "提醒") -> None:
            warnings.append(
                {
                    "warning_id": f"W-{len(warnings) + 1:03d}",
                    "code": code,
                    "severity": severity,
                    "message": message,
                }
            )

        if not raw_paths:
            if all_anchor_uses and not is_add:
                warn(
                    "NO_EFFECTIVE_USE",
                    "该物料存在历史结构关系，但在计划生效日期下没有有效的 BOM 使用路径。",
                    "重要",
                )
            else:
                warn(
                    "NO_PATH",
                    (
                        "没有找到从变更料号到顶层成品的完整使用路径；"
                        if direction == "upstream"
                        else "变更料号下没有找到有效的下层物料展开路径；"
                    )
                    + "请核查当前导入数据和字段对应关系。",
                    "重要",
                )
        if invalid_anchor_dates:
            warn(
                "INVALID_EFFECTIVITY",
                f"目标组件有 {invalid_anchor_dates} 条日期记录无法解析，已从本次核查中排除。",
                "重要",
            )
        if incomplete_branches:
            warn(
                "INCOMPLETE_BRANCH",
                f"发现 {incomplete_branches} 个无法继续追溯到顶层成品的使用分支。",
                "重要",
            )
        if cycles:
            warn("GRAPH_CYCLE", f"发现 {len(cycles)} 条环路，相关分支已停止遍历。", "重要")
        if truncated:
            warn(
                "PATH_LIMIT",
                f"路径超过 {MAX_ENUMERATED_PATHS} 条，界面仅展示前述上限。",
                "重要",
            )
        if alternative_keys:
            warn(
                "ALTERNATIVE_GROUP",
                f"BOM 使用路径中出现 {len(alternative_keys)} 个替代组；替代料仍需人工确认。",
            )
        if request.change_type in {"add", "replace"}:
            warn(
                "NEW_COMPONENT_UNVERIFIED",
                "新组件适用性尚未确认；替代、质量、物料主数据和配置规则资料待补齐。",
                "重要",
            )
        if request.change_type == "effectivity":
            warn(
                "EFFECTIVITY_SHIFT",
                (
                    f"组件生效日期拟从 {request.old_effective_date} 调整为 "
                    f"{request.new_effective_date}；请核对日期区间内订单、工单与版本衔接。"
                ),
                "重要",
            )
        if production_orders:
            warn(
                "NO_ISSUE_DATA",
                "已关联生产订单，但缺少实际领料数据，不能判断是否已投料或需要返工。",
            )
        for code, message in (
            ("NO_INVENTORY", "未提供库存和在途数据，无法判断余料与切换消耗。"),
            ("NO_PROCUREMENT", "未提供采购订单和供应商数据，无法判断采购影响。"),
            ("NO_COST", "未提供可靠成本数据，不计算成本影响。"),
            ("NO_QUALITY", "未提供质量、认证和 AVL/AML 数据，相关影响需人工核查。"),
            (
                "SNAPSHOT_SCOPE",
                "核查结果基于当前导入数据，不替代正式 BOM 放行与审批。",
            ),
        ):
            warn(code, message)

        risk_level, risk_reasons = self._risk_level(
            impacted_top_keys,
            paths,
            production_orders,
            alternative_keys,
            warnings,
        )

        change_labels = {
            "add": "新增组件",
            "remove": "删除组件",
            "replace": "替换组件",
            "quantity": "修改用量",
            "effectivity": "改变生效日期",
        }
        old_alias = material_alias(anchor_key)
        direction_label = (
            "向上：变更料号 → 顶层成品"
            if direction == "upstream"
            else "向下：变更料号 → 末端物料"
        )
        direct_label = "直接父件" if direction == "upstream" else "直接下层物料"
        all_label = "全部上层父件" if direction == "upstream" else "全部下层物料"
        result: dict[str, Any] = {
            "change": {
                "type": request.change_type,
                "type_label": change_labels[request.change_type],
                "propagation_direction": direction,
                "propagation_direction_label": direction_label,
                "old_component": "" if is_add else old_alias,
                "new_component": safe_text(request.new_component, 120)
                if request.change_type in {"add", "replace"}
                else "",
                "target_parent": old_alias if is_add else "",
                "old_quantity": request.old_quantity,
                "new_quantity": request.new_quantity,
                "effective_date": effective_date,
                "old_effective_date": (
                    request.old_effective_date
                    if request.change_type == "effectivity"
                    else ""
                ),
                "new_effective_date": (
                    request.new_effective_date
                    if request.change_type == "effectivity"
                    else ""
                ),
                "note": "已提供（未保存）" if request.note.strip() else "",
            },
            "direction": {
                "value": direction,
                "label": direction_label,
                "direct_label": direct_label,
                "all_label": all_label,
                "path_label": (
                    "到顶层成品的完整使用路径"
                    if direction == "upstream"
                    else "到末端物料的完整展开路径"
                ),
            },
            "risk": {"level": risk_level, "reasons": risk_reasons},
            "metrics": {
                "direct_parents": len(direct_related_keys),
                "all_parents": len(all_related_keys),
                "direct_nodes": len(direct_related_keys),
                "all_nodes": len(all_related_keys),
                "top_materials": len(impacted_top_keys),
                "paths": len(paths),
                "sales_order_items": len(sales_orders),
                "production_orders": len(production_orders),
            },
            "direct_parents": sorted(
                material_alias(key) for key in direct_related_keys
            ),
            "all_parents": sorted(
                material_alias(key) for key in all_related_keys
            ),
            "direct_nodes": sorted(
                material_alias(key) for key in direct_related_keys
            ),
            "all_nodes": sorted(
                material_alias(key) for key in all_related_keys
            ),
            "top_materials": sorted(top_alias(key) for key in impacted_top_keys),
            "paths": paths,
            "sales_orders": sales_orders,
            "production_orders": production_orders,
            "alternative_groups": sorted(
                alias_maps.get("alternative", {}).get(key, "ALT-UNKNOWN")
                for key in alternative_keys
            ),
            "change_history": change_history,
            "warnings": warnings,
            "evidence": {
                "edge_count": len(traversed_edge_ids),
                "source_rule": "每条路径均引用脱敏来源编号和工作表行号。",
                "data_snapshot": "当前本机脱敏导入数据",
            },
            "calculated_on": date.today().isoformat(),
        }
        return result

    @staticmethod
    def _combine_directions(
        upstream: dict[str, Any],
        downstream: dict[str, Any],
    ) -> dict[str, Any]:
        def unique_records(
            records: list[dict[str, Any]],
            fields: tuple[str, ...],
        ) -> list[dict[str, Any]]:
            seen: set[tuple[Any, ...]] = set()
            items: list[dict[str, Any]] = []
            for record in records:
                key = tuple(record.get(field) for field in fields)
                if key in seen:
                    continue
                seen.add(key)
                items.append(deepcopy(record))
            return items

        def numbered_paths(
            result: dict[str, Any],
            prefix: str,
        ) -> list[dict[str, Any]]:
            items = deepcopy(result["paths"])
            for index, path in enumerate(items, start=1):
                path["path_id"] = f"{prefix}-{index:03d}"
            return items

        upstream_paths = numbered_paths(upstream, "UP")
        downstream_paths = numbered_paths(downstream, "DOWN")
        paths = upstream_paths + downstream_paths

        sales_orders = unique_records(
            upstream["sales_orders"] + downstream["sales_orders"],
            ("source_id", "source_row", "sales_order", "item"),
        )
        for index, item in enumerate(sales_orders, start=1):
            item["evidence_id"] = f"SOE-{index:03d}"

        production_orders = unique_records(
            upstream["production_orders"] + downstream["production_orders"],
            ("source_id", "source_row", "production_order"),
        )
        for index, item in enumerate(production_orders, start=1):
            item["evidence_id"] = f"MOE-{index:03d}"

        warnings = unique_records(
            upstream["warnings"] + downstream["warnings"],
            ("code", "message"),
        )
        for index, warning in enumerate(warnings, start=1):
            warning["warning_id"] = f"W-{index:03d}"

        history_items = unique_records(
            upstream["change_history"].get("items", [])
            + downstream["change_history"].get("items", []),
            ("change",),
        )
        change_history = {
            "referenced": len(history_items),
            "matched": len(history_items),
            "items": history_items,
            "note": "未匹配到历史记录不表示该物料从未发生变更。",
        }

        level_order = {"低": 0, "中": 1, "高": 2}
        risk_level = max(
            (upstream["risk"]["level"], downstream["risk"]["level"]),
            key=lambda level: level_order.get(level, 0),
        )
        risk_reasons = list(
            dict.fromkeys(
                reason
                for direction_result in (upstream, downstream)
                if direction_result["risk"]["level"] == risk_level
                for reason in direction_result["risk"]["reasons"]
            )
        )

        top_materials = sorted(
            set(upstream["top_materials"]) | set(downstream["top_materials"])
        )
        alternative_groups = sorted(
            set(upstream["alternative_groups"])
            | set(downstream["alternative_groups"])
        )
        change = deepcopy(upstream["change"])
        change["propagation_direction"] = "both"
        change["propagation_direction_label"] = "双向：向上核查顶层成品，并向下展开末端物料"

        def direction_view(
            result: dict[str, Any],
            direction_paths: list[dict[str, Any]],
        ) -> dict[str, Any]:
            return {
                "direction": deepcopy(result["direction"]),
                "metrics": deepcopy(result["metrics"]),
                "direct_nodes": deepcopy(result["direct_nodes"]),
                "all_nodes": deepcopy(result["all_nodes"]),
                "top_materials": deepcopy(result["top_materials"]),
                "paths": direction_paths,
            }

        metrics = {
            "direct_parents": upstream["metrics"]["direct_nodes"],
            "all_parents": upstream["metrics"]["all_nodes"],
            "direct_children": downstream["metrics"]["direct_nodes"],
            "all_children": downstream["metrics"]["all_nodes"],
            "upstream_paths": len(upstream_paths),
            "downstream_paths": len(downstream_paths),
            "top_materials": len(top_materials),
            "paths": len(paths),
            "sales_order_items": len(sales_orders),
            "production_orders": len(production_orders),
        }
        return {
            "change": change,
            "direction": {
                "value": "both",
                "label": "双向影响核查",
                "upstream_label": "向上：变更料号 → 顶层成品",
                "downstream_label": "向下：变更料号 → 末端物料",
            },
            "directions": {
                "upstream": direction_view(upstream, upstream_paths),
                "downstream": direction_view(downstream, downstream_paths),
            },
            "risk": {"level": risk_level, "reasons": risk_reasons},
            "metrics": metrics,
            "direct_parents": deepcopy(upstream["direct_nodes"]),
            "all_parents": deepcopy(upstream["all_nodes"]),
            "direct_children": deepcopy(downstream["direct_nodes"]),
            "all_children": deepcopy(downstream["all_nodes"]),
            "top_materials": top_materials,
            "paths": paths,
            "sales_orders": sales_orders,
            "production_orders": production_orders,
            "alternative_groups": alternative_groups,
            "change_history": change_history,
            "warnings": warnings,
            "evidence": {
                "edge_count": (
                    upstream["evidence"]["edge_count"]
                    + downstream["evidence"]["edge_count"]
                ),
                "source_rule": "向上与向下的每条路径均引用脱敏来源编号和工作表行号。",
                "data_snapshot": "当前本机脱敏导入数据",
            },
            "calculated_on": upstream["calculated_on"],
        }

    def _orders(
        self, top_keys: set[str], alias_maps: dict[str, dict[str, str]]
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        if not top_keys:
            return [], []
        placeholders = ",".join("?" for _ in top_keys)
        parameters = tuple(sorted(top_keys))
        with self.repository.connect() as connection:
            sales_rows = connection.execute(
                f"""
                SELECT s.*, h.status_category AS header_status,
                       h.requested_date AS header_requested_date,
                       sm.plant_key, sm.order_quantity
                FROM sales_items s
                LEFT JOIN sales_headers h
                  ON h.sales_order_key=s.sales_order_key
                LEFT JOIN sales_item_metrics sm
                  ON sm.sales_item_key=s.sales_item_key
                WHERE s.material_key IN ({placeholders})
                  AND COALESCE(NULLIF(s.status_category, '未知'), h.status_category, '未知')
                    NOT IN ('已关闭', '已取消', '已拒绝')
                ORDER BY s.sales_order_key, s.item_key
                """,
                parameters,
            ).fetchall()
            sales_item_keys = {row["sales_item_key"] for row in sales_rows}
            sales_order_keys = {row["sales_order_key"] for row in sales_rows}

            conditions = [f"material_key IN ({placeholders})"]
            order_parameters: list[str] = list(parameters)
            if sales_item_keys:
                item_placeholders = ",".join("?" for _ in sales_item_keys)
                conditions.append(f"sales_item_key IN ({item_placeholders})")
                order_parameters.extend(sorted(sales_item_keys))
            if sales_order_keys:
                order_placeholders = ",".join("?" for _ in sales_order_keys)
                conditions.append(f"sales_order_key IN ({order_placeholders})")
                order_parameters.extend(sorted(sales_order_keys))
            production_rows = connection.execute(
                f"""
                SELECT p.*, pm.plant_key, pm.order_quantity,
                       pm.delivered_quantity, pm.confirmed_quantity
                FROM production_orders p
                LEFT JOIN production_order_metrics pm
                  ON pm.production_order_key=p.production_order_key
                WHERE ({" OR ".join(conditions)})
                  AND (
                    p.status_category IN ('已释放/执行中')
                    OR (
                      p.status_category='已完成'
                      AND pm.order_quantity IS NOT NULL
                      AND COALESCE(pm.delivered_quantity, 0) < pm.order_quantity
                    )
                  )
                ORDER BY p.production_order_key
                """,
                tuple(order_parameters),
            ).fetchall()

        sales_orders: list[dict[str, Any]] = []
        for row in sales_rows:
            order_alias = alias_maps.get("sales_order", {}).get(
                row["sales_order_key"], "SO-UNKNOWN"
            )
            sales_orders.append(
                {
                    "evidence_id": f"SOE-{len(sales_orders) + 1:03d}",
                    "sales_order": order_alias,
                    "item": alias_maps.get("sales_item", {}).get(
                        row["item_key"], "项目号未知"
                    ),
                    "material": alias_maps.get("top", {}).get(
                        row["material_key"],
                        alias_maps.get("material", {}).get(
                            row["material_key"], "MAT-UNKNOWN"
                        ),
                    ),
                    "configuration": "已提供"
                    if row["configuration_key"]
                    else "未提供",
                    "status": row["status_category"]
                    if row["status_category"] != "未知"
                    else (row["header_status"] or "未知"),
                    "requested_date": row["requested_date"]
                    or row["header_requested_date"],
                    "plant": alias_maps.get("plant", {}).get(
                        row["plant_key"], "—"
                    ),
                    "order_quantity": row["order_quantity"],
                    "source_id": row["source_id"],
                    "source_row": row["source_row"],
                }
            )

        production_orders = [
            {
                "evidence_id": f"MOE-{index + 1:03d}",
                "production_order": alias_maps.get("production_order", {}).get(
                    row["production_order_key"], "MO-UNKNOWN"
                ),
                "material": alias_maps.get("top", {}).get(
                    row["material_key"],
                    alias_maps.get("material", {}).get(
                        row["material_key"], "MAT-UNKNOWN"
                    ),
                ),
                "linked_sales_order": alias_maps.get("sales_order", {}).get(
                    row["sales_order_key"], ""
                ),
                "status": row["status_category"],
                "plant": alias_maps.get("plant", {}).get(
                    row["plant_key"], "—"
                ),
                "order_quantity": row["order_quantity"],
                "delivered_quantity": row["delivered_quantity"],
                "confirmed_quantity": row["confirmed_quantity"],
                "remaining_quantity": max(
                    float(row["order_quantity"] or 0)
                    - float(row["confirmed_quantity"] or 0),
                    0,
                ),
                "undelivered_quantity": max(
                    float(row["order_quantity"] or 0)
                    - float(row["delivered_quantity"] or 0),
                    0,
                ),
                "created_date": row["created_date"],
                "actual_start": row["actual_start"],
                "actual_finish": row["actual_finish"],
                "source_id": row["source_id"],
                "source_row": row["source_row"],
            }
            for index, row in enumerate(production_rows)
        ]
        return sales_orders, production_orders

    @staticmethod
    def _provided(values: list[float | None]) -> bool:
        return any(value is not None for value in values)

    @staticmethod
    def _production_needs_attention(item: dict[str, Any]) -> bool:
        return item.get("status") in {"已释放/执行中"} or (
            item.get("status") == "已完成"
            and float(item.get("undelivered_quantity") or 0) > 0
        )

    def _planning_impact(
        self, request: ChangeRequest, result: dict[str, Any]
    ) -> dict[str, Any]:
        production_orders = result["production_orders"]
        sales_orders = result["sales_orders"]
        active_orders = [
            item
            for item in production_orders
            if self._production_needs_attention(item)
        ]
        production_quantity = sum(
            float(item.get("order_quantity") or 0) for item in production_orders
        )
        remaining_quantity = sum(
            float(item.get("remaining_quantity") or 0) for item in active_orders
        )
        sales_quantity = sum(
            float(item.get("order_quantity") or 0) for item in sales_orders
        )
        pending_sales_orders = [
            item
            for item in sales_orders
            if item.get("status") in {"未知", "其他/待确认"}
        ]
        action_sales_orders = [
            item for item in sales_orders if item not in pending_sales_orders
        ]

        input_names = (
            "old_on_hand",
            "old_in_transit",
            "old_open_po",
            "old_consumable_demand",
            "new_on_hand",
            "new_in_transit",
            "new_open_po",
            "new_required_demand",
        )
        scenario_inputs = {name: getattr(request, name) for name in input_names}
        manual_scenario = self._provided(list(scenario_inputs.values()))
        imported_scenario = None
        if not manual_scenario and request.change_type == "replace":
            imported_scenario = self.repository.planning_supply_scenario(
                request.old_component,
                request.new_component,
                request.planning_plant,
                request.storage_location,
            )
            if imported_scenario:
                scenario_inputs = {
                    name: imported_scenario.get(name) for name in input_names
                }
        old_inputs = [scenario_inputs[name] for name in input_names[:4]]
        new_inputs = [scenario_inputs[name] for name in input_names[4:]]
        scenario_provided = self._provided(old_inputs + new_inputs)
        scenario_complete = all(value is not None for value in old_inputs + new_inputs)

        old_exposure = sum(
            float(value or 0)
            for value in (
                scenario_inputs["old_on_hand"],
                scenario_inputs["old_in_transit"],
                scenario_inputs["old_open_po"],
            )
        )
        old_excess = old_exposure - float(
            scenario_inputs["old_consumable_demand"] or 0
        )
        new_supply = sum(
            float(value or 0)
            for value in (
                scenario_inputs["new_on_hand"],
                scenario_inputs["new_in_transit"],
                scenario_inputs["new_open_po"],
            )
        )
        new_gap = float(scenario_inputs["new_required_demand"] or 0) - new_supply

        if not scenario_provided:
            recommendation = "待补供应数据"
            rationale = "已识别订单侧影响；可用库存、在途、Open PO和组件需求尚未提供。"
            recommendation_code = "DATA_GAP"
        elif not scenario_complete:
            recommendation = "情景数据待补齐"
            rationale = "已有部分人工补充数量，但不足以形成完整的备料切换判断。"
            recommendation_code = "PARTIAL_INPUT"
        elif new_gap > 0:
            recommendation = "暂缓维护 Future BOM"
            rationale = f"按计划备料情景测算，新料缺口为 {new_gap:g}。"
            recommendation_code = "HOLD_FUTURE_BOM"
        elif old_excess > 0:
            recommendation = "Run-in Change"
            rationale = f"新料供应可覆盖需求，但旧料预计剩余 {old_excess:g}。"
            recommendation_code = "RUN_IN"
        else:
            recommendation = "Hard Cut"
            rationale = "按计划备料情景测算，新料无缺口且旧料无预计剩余。"
            recommendation_code = "HARD_CUT"

        if request.change_type == "replace":
            planning_material = f"{request.old_component} → {request.new_component}"
        elif request.change_type == "add":
            planning_material = request.new_component or request.target_parent
        else:
            planning_material = request.old_component
        affected_plants = sorted(
            {
                str(item.get("plant") or "").strip()
                for item in production_orders + sales_orders
                if str(item.get("plant") or "").strip() not in {"", "—"}
            }
        )
        imported_plant = str((imported_scenario or {}).get("plant") or "").strip()
        imported_location = str(
            (imported_scenario or {}).get("storage_location") or ""
        ).strip()
        planning_plant = (
            request.planning_plant.strip()
            or imported_plant
            or ", ".join(affected_plants)
        )
        planning_scope = {
            "material": planning_material or "按本次ECN变更料号",
            "plant": planning_plant or "待补工厂",
            "storage_location": (
                request.storage_location.strip()
                or imported_location
                or "待补库位"
            ),
        }

        coverage = [
            {
                "item": "BOM结构与生效范围",
                "status": "已具备",
                "detail": f"{result['metrics']['paths']} 条BOM关联路径",
            },
            {
                "item": "生产需求",
                "status": "已具备" if production_orders else "未匹配",
                "detail": f"{len(production_orders)} 个关联生产订单",
            },
            {
                "item": "销售需求",
                "status": (
                    "待确认"
                    if pending_sales_orders
                    else "已具备"
                    if sales_orders
                    else "未匹配"
                ),
                "detail": (
                    f"{len(sales_orders)} 个关联销售订单行，其中 "
                    f"{len(pending_sales_orders)} 个状态待确认"
                ),
            },
            {
                "item": "可用库存/在途/Open PO",
                "status": (
                    "虚拟数据"
                    if imported_scenario
                    else "人工补充"
                    if scenario_provided
                    else "待补数据"
                ),
                "detail": "当前按可用库存、在途和Open PO情景补充；Liability暂不纳入计算",
            },
        ]
        if imported_scenario:
            coverage.append(
                {
                    "item": "演示数据范围",
                    "status": "虚拟数据",
                    "detail": (
                        f"{imported_scenario['scenario_id']} · 数据截止 "
                        f"{imported_scenario.get('data_date') or '待补'} · "
                        "仅用于功能演示，不代表ERP实时余额"
                    ),
                }
            )
        if pending_sales_orders:
            coverage.append(
                {
                    "item": "销售订单状态",
                    "status": "待补数据",
                    "detail": "当前销售订单状态缺失，只列为待确认关联订单，不驱动计划切换建议",
                }
            )
        if not request.storage_location.strip() and not imported_location:
            coverage.append(
                {
                    "item": "核查范围",
                    "status": "待补数据",
                    "detail": "当前数据未提供库位，建议按物料+工厂+库位补齐库存口径",
                }
            )
        return {
            "stage": "第一阶段",
            "status": "核查关联订单；计划切换建议按完整备料情景测算",
            "scope": planning_scope,
            "metrics": {
                "affected_production_orders": len(production_orders),
                "active_production_orders": len(active_orders),
                "affected_production_quantity": production_quantity,
                "remaining_production_quantity": remaining_quantity,
                "affected_sales_items": len(sales_orders),
                "affected_sales_quantity": sales_quantity,
                "linked_sales_items": len(sales_orders),
                "action_sales_items": len(action_sales_orders),
                "pending_sales_items": len(pending_sales_orders),
            },
            "coverage": coverage,
            "scenario": {
                "provided": scenario_provided,
                "complete": scenario_complete,
                "origin": (
                    "导入的虚拟计划备料数据"
                    if imported_scenario
                    else "人工补充"
                    if manual_scenario
                    else "未提供"
                ),
                "scenario_id": (
                    imported_scenario.get("scenario_id")
                    if imported_scenario
                    else None
                ),
                "data_attribute": (
                    imported_scenario.get("data_attribute")
                    if imported_scenario
                    else None
                ),
                "data_date": (
                    imported_scenario.get("data_date")
                    if imported_scenario
                    else None
                ),
                "old_exposure": old_exposure if scenario_provided else None,
                "old_excess": old_excess if scenario_complete else None,
                "new_supply": new_supply if scenario_provided else None,
                "new_gap": new_gap if scenario_complete else None,
                "inputs": scenario_inputs,
            },
            "recommendation": recommendation,
            "recommendation_code": recommendation_code,
            "rationale": rationale,
            "conclusion": {
                "decision": recommendation,
                "code": recommendation_code,
                "logic": rationale,
                "data_sufficiency": "完整" if scenario_complete else "不足",
            },
            "boundary": "计划切换建议由完整的物料+工厂+库位备料情景驱动；状态未知订单只列为待确认关联对象，不参与建议，不代替ERP供需平衡或制造处置。",
        }

    @staticmethod
    def _manufacturing_stage(
        status: str, undelivered_quantity: float | None = None
    ) -> str:
        if status == "已完成" and float(undelivered_quantity or 0) > 0:
            return "完工未交付"
        return {
            "已释放/执行中": "WIP/生产中",
        }.get(status, "状态待确认")

    @staticmethod
    def _manufacturing_action(stage: str, disposition_code: str) -> str:
        if disposition_code == "USE_AS_IS":
            if stage == "已下达未开工":
                return "按生效日期确认投料版本"
            return "继续生产并保留旧料使用记录"
        if disposition_code == "USE_UP":
            return "按Run-in窗口消耗旧料并锁定截止日期"
        if disposition_code == "REWORK":
            if stage == "已下达未开工":
                return "暂停投料并切换新BOM"
            return "拦截订单，补充MES工位后制定返工方案"
        if disposition_code == "SCRAP":
            if stage == "已下达未开工":
                return "停止旧料投料并调整订单组件"
            return "隔离实物并发起报废审批"
        if disposition_code == "RETURN_SUPPLIER":
            return "冻结旧料使用，转采购确认退货/取消/索赔"
        if disposition_code == "HOLD_REVIEW":
            return "暂停后续工序并等待处置结论"
        if stage in {"WIP/生产中", "完工未交付"}:
            return "先行拦截，补充Disposition与MES位置"
        return "补充Disposition后确认制造动作"

    @staticmethod
    def _recommend_disposition(
        result: dict[str, Any], stage_counts: dict[str, int]
    ) -> dict[str, Any]:
        """Recommend a candidate code from impact facts; never treat it as approval."""

        production_orders = result.get("production_orders", [])
        if not production_orders:
            return {
                "code": "UNCONFIRMED",
                "name": "未确认",
                "evidence_level": "不足",
                "rationale": "当前未匹配生产订单，制造处置对象不足，建议由制造工程确认是否适用。",
                "requires_confirmation": True,
            }
        if stage_counts.get("WIP/生产中", 0) or stage_counts.get(
            "完工未交付", 0
        ):
            return {
                "code": "HOLD_REVIEW",
                "name": "隔离待判",
                "evidence_level": "不足",
                "rationale": "存在WIP或完工未交付订单，但当前没有实际领料、工序和MES数据；建议先识别需要ME确认的对象。",
                "requires_confirmation": True,
            }
        planning = result.get("planning_impact", {})
        if planning.get("recommendation") == "Run-in Change":
            return {
                "code": "USE_UP",
                "name": "旧料限期消耗",
                "evidence_level": "部分",
                "rationale": "备料情景显示旧料存在预计剩余；制造侧仍需确认未开工订单是否按Run-in窗口执行。",
                "requires_confirmation": True,
            }
        return {
            "code": "USE_AS_IS",
            "name": "旧料可继续使用",
            "evidence_level": "部分",
            "rationale": "关联订单均未开工，暂未发现返工对象；建议ME确认工单组件按生效日期切换。",
            "requires_confirmation": True,
        }

    def _manufacturing_impact(
        self, request: ChangeRequest, result: dict[str, Any]
    ) -> dict[str, Any]:
        routing = self.repository.routing_assignments(result["top_materials"])
        disposition = resolve_disposition(
            request.disposition_code, request.disposition
        )
        disposition_status = request.disposition_status or "待评审"
        disposition_scope = (
            request.disposition_scope.strip() or disposition["scope"]
        )
        disposition_owner = (
            request.disposition_owner.strip() or disposition["owner"]
        )
        stage_counts: dict[str, int] = defaultdict(int)
        actions: list[dict[str, Any]] = []
        review_objects: list[dict[str, Any]] = []
        for order in result["production_orders"]:
            stage = self._manufacturing_stage(
                order["status"], order.get("undelivered_quantity")
            )
            stage_counts[stage] += 1
            if stage == "WIP/生产中":
                review_reason = "确认是否已投料或安装"
            elif stage == "完工未交付":
                review_reason = "确认完工品是否需要隔离或放行"
            else:
                review_reason = "确认工单组件是否按生效日期切换"
            review_objects.append(
                {
                    "production_order": order["production_order"],
                    "material": order["material"],
                    "plant": order.get("plant") or "—",
                    "stage": stage,
                    "status": order.get("status"),
                    "order_quantity": order.get("order_quantity"),
                    "undelivered_quantity": order.get("undelivered_quantity"),
                    "review_reason": review_reason,
                    "evidence_id": order.get("evidence_id"),
                }
            )
            actions.append(
                {
                    "production_order": order["production_order"],
                    "material": order["material"],
                    "plant": order.get("plant") or "—",
                    "stage": stage,
                    "disposition": disposition["name"],
                    "disposition_code": disposition["code"],
                    "disposition_name": disposition["name"],
                    "disposition_scope": disposition_scope,
                    "disposition_owner": disposition_owner,
                    "approval_status": disposition_status,
                    "quality_required": disposition["quality_required"],
                    "action": self._manufacturing_action(
                        stage, disposition["code"]
                    ),
                    "quantity": order.get("order_quantity"),
                    "source_id": order["source_id"],
                    "source_row": order["source_row"],
                }
            )
        recommendation = self._recommend_disposition(result, stage_counts)
        selected_code = disposition["code"]
        recommendation["selected_code"] = selected_code
        recommendation["selection_status"] = (
            "已提供人工选择" if selected_code != "UNCONFIRMED" else "待人工确认"
        )
        return {
            "stage": "第一阶段",
            "scope": {
                "level": "ECN",
                "plants": sorted(
                    {
                        str(item.get("plant") or "").strip()
                        for item in review_objects
                        if str(item.get("plant") or "").strip() not in {"", "—"}
                    }
                ),
                "storage_location": request.storage_location.strip() or "待补库位",
            },
            "disposition": disposition["name"],
            "disposition_code": disposition["code"],
            "disposition_name": disposition["name"],
            "disposition_scope": disposition_scope,
            "disposition_owner": disposition_owner,
            "approval_status": disposition_status,
            "quality_required": disposition["quality_required"],
            "old_material_allowed": disposition["old_material_allowed"],
            "plan_signal": disposition["plan_signal"],
            "approval_required": disposition_status != "已批准",
            "definition": disposition["description"],
            "note": request.disposition_note,
            "recommended_disposition": recommendation,
            "review_object_count": len(review_objects),
            "review_objects": review_objects,
            "data_gaps": [
                "缺少实际领料/退料数据，无法确认旧料是否已投料",
                "缺少工序与MES状态，无法确认安装位置和返工路径",
            ],
            "routing_status": "工艺路线关联已具备"
            if routing
            else "未匹配工艺路线关联",
            "operation_detail_status": "当前仅用于辅助识别对象，不作工序结论",
            "routing_assignments": routing,
            "stage_counts": dict(stage_counts),
            "actions": actions,
            "boundary": "制造模块只识别需要ME确认的订单对象并给出ECN级候选Code，不判断已装配工序、返工路径或质量放行。",
        }

    def _change_history(
        self,
        change_keys: set[str],
        alias_maps: dict[str, dict[str, str]],
    ) -> dict[str, Any]:
        if not change_keys:
            return {"referenced": 0, "matched": 0, "items": []}
        placeholders = ",".join("?" for _ in change_keys)
        with self.repository.connect() as connection:
            rows = connection.execute(
                f"""
                SELECT change_key, COUNT(*) AS evidence_rows,
                       MIN(changed_date) AS first_date,
                       MAX(changed_date) AS last_date
                FROM change_history
                WHERE change_key IN ({placeholders})
                GROUP BY change_key
                ORDER BY change_key
                """,
                tuple(sorted(change_keys)),
            ).fetchall()
        items = [
            {
                "change": alias_maps.get("change", {}).get(
                    row["change_key"], "EC-UNKNOWN"
                ),
                "evidence_rows": row["evidence_rows"],
                "first_date": row["first_date"],
                "last_date": row["last_date"],
            }
            for row in rows
        ]
        return {
            "referenced": len(change_keys),
            "matched": len(items),
            "items": items,
            "note": "未匹配到历史记录不表示该物料从未发生变更。",
        }

    @staticmethod
    def _risk_level(
        impacted_tops: set[str],
        paths: list[dict[str, Any]],
        production_orders: list[dict[str, Any]],
        alternative_keys: set[str],
        warnings: list[dict[str, str]],
    ) -> tuple[str, list[str]]:
        high_reasons: list[str] = []
        medium_reasons: list[str] = []
        if len(impacted_tops) > 1:
            high_reasons.append("影响多个顶层成品")
        active_orders = sum(
            order["status"] == "已释放/执行中" for order in production_orders
        )
        if active_orders:
            high_reasons.append(f"关联 {active_orders} 个已释放或执行中的生产订单")
        critical_codes = {
            "INVALID_EFFECTIVITY",
            "INCOMPLETE_BRANCH",
            "GRAPH_CYCLE",
            "PATH_LIMIT",
        }
        if any(warning["code"] in critical_codes for warning in warnings):
            high_reasons.append("关键日期或 BOM 使用路径资料不完整")
        if high_reasons:
            return "高", high_reasons

        if production_orders:
            medium_reasons.append("存在关联生产订单")
        if alternative_keys:
            medium_reasons.append("路径包含替代组，需要人工确认")
        if len(paths) > 1:
            medium_reasons.append("存在多条 BOM 使用路径")
        if medium_reasons:
            return "中", medium_reasons
        return "低", ["影响范围单一，未发现已知订单或关键路径缺口"]
