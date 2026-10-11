from __future__ import annotations

import re
from typing import Any


def _business_text(value: Any) -> str:
    text = str(value or "")
    replacements = {
        "新组件物料与寻源验证": "新组件物料与供应准备",
        "新组件物料主数据与寻源验证": "新组件物料与供应准备",
        "新组件物料与供应准备为待补证":
            "新组件物料与供应准备状态为“待补证”",
        "按路径证据逐一确认": "按数据来源记录逐一确认",
        "逐项关闭新组件的物料主数据、采购类型、寻源和质量证据缺口":
            "逐项补齐新组件的物料主数据、采购类型、供应来源和质量资料",
        "新组件适用性尚未验证；缺少替代、质量、主数据和配置规则证据":
            "新组件适用性尚未确认；替代、质量、物料主数据和配置规则资料待补充",
        "结论基于当前导入快照，用于影响发现，不替代正式 BOM 放行与审批":
            "核查结论基于当前导入数据，不替代正式 BOM 放行与审批",
        "分析起点下没有找到有效的下层组件路径；请核查数据快照和字段映射":
            "变更料号下未找到有效的下层物料路径；请核查导入数据和字段对应关系",
        "当前快照未覆盖": "当前导入数据未覆盖",
        "未找到工厂级采购类型证据": "未找到工厂级采购类型记录",
        "未导入供应商来源清单或 AVL/AML 证据":
            "未导入供应商来源清单或 AVL/AML 资料",
        "顶层BOM": "顶层成品",
        "起点组件": "变更料号",
        "分析起点": "变更料号",
        "末端组件": "末端物料",
        "下层组件": "下层物料",
        "数据快照": "导入数据",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return re.sub(
        r"路径中出现 (\d+) 个替代组；系统只提示，不自动选料",
        r"BOM 使用路径中出现 \1 个替代组；替代料仍需人工确认",
        text,
    )


def rule_summary(result: dict[str, Any]) -> str:
    change = result["change"]
    metrics = result["metrics"]
    risk = result["risk"]
    target = (
        change["target_parent"]
        if change["type"] == "add"
        else change["old_component"]
    )
    scope = "、".join(result["top_materials"]) or "未识别到顶层成品"
    reasons = "；".join(_business_text(item) for item in risk["reasons"])
    if result.get("analysis_kind") == "bom_version":
        ecn_suffix = f"（{result.get('ecn')}）" if result.get("ecn") else ""
        opening = (
            f"{result.get('bom_version', '当前核查版本')} 共包含 "
            f"{result.get('change_item_count', 0)} 个变更项目"
            f"{ecn_suffix}，"
        )
    else:
        opening = (
            f"本项{change['type_label']}计划于 {change['effective_date']} 生效，"
        )
    summary = opening + (
        f"涉及 {metrics['top_materials']} 个顶层成品，关联 "
        f"{metrics['sales_order_items']} 个销售订单行和 "
        f"{metrics['production_orders']} 个生产订单。按预设规则，当前关注等级为"
        f"“{risk['level']}”。"
    )
    if result.get("analysis_kind") == "bom_version":
        summary += (
            f"BOM 使用关系共核查 {metrics['upstream_paths']} 条成品使用路径和 "
            f"{metrics['downstream_paths']} 条下层展开路径。"
        )
    else:
        summary += (
            f"BOM 使用关系核查结果：物料 {target} 关联 "
            f"{metrics['direct_parents']} 个直接父件、"
            f"{metrics['all_parents']} 个全部上层物料和 "
            f"{metrics['upstream_paths']} 条成品使用路径；其下包含 "
            f"{metrics['direct_children']} 个直接下层物料、"
            f"{metrics['all_children']} 个全部下层物料和 "
            f"{metrics['downstream_paths']} 条展开路径。"
        )
    summary += f"涉及顶层成品：{scope}。重点关注原因：{reasons}。"
    validation = result.get("material_validation", {})
    if validation.get("applicable"):
        material = validation.get("material", "待填写物料")
        prefix = "其中 " if result.get("analysis_kind") == "bom_version" else "新组件 "
        summary += (
            f"{prefix}{material}的物料与供应准备状态为"
            f"“{validation.get('status', '待补证')}”。"
        )
    return summary


def role_checklists(result: dict[str, Any]) -> dict[str, list[str]]:
    change_type = result["change"]["type"]
    engineering = [
        "按数据来源记录逐一确认使用位置、BOM 用途与生效日期。",
        "核对受影响成品的配置条件、版本和替代方案。",
    ]
    if change_type in {"add", "replace"}:
        engineering.append("验证新组件的适用性、互换性和设计约束。")
    if change_type == "quantity":
        engineering.append("逐使用位置确认原用量、新用量及计量单位。")
    if change_type == "effectivity":
        engineering.append("核对原生效日期、新生效日期及版本切换窗口。")
    planning = [
        "核对关联销售订单的需求日期与计划生效日期。",
        "补充库存、在途和采购订单后制定切换与余料方案。",
    ]
    if change_type in {"add", "replace"}:
        planning.append("逐项补齐新组件的物料主数据、采购类型、寻源和质量资料。")
    manufacturing = [
        "逐一核对关联生产订单是否已下达、投料、报工或完工。",
        "补充工单组件与实际领料数据后判断现场处置。",
    ]
    manufacturing_impact = result.get("manufacturing_impact", {})
    recommendation = manufacturing_impact.get("recommended_disposition", {})
    if recommendation.get("code"):
        manufacturing.append(
            f"评估系统给出的 ECN 级候选 {recommendation.get('code')}，并补充实际领料、工序或 MES 证据。"
        )
    return {"研发": engineering, "计划": planning, "制造": manufacturing}


def markdown_report(result: dict[str, Any], ai_text: str = "") -> str:
    metrics = result["metrics"]
    risk = result["risk"]
    checklists = result.get("checklists") or role_checklists(result)
    lines = [
        "# BOM 工程变更影响核查报告",
        "",
        f"- 报告编号：{result.get('analysis_id', '—')}",
        f"- 核查批次：{result.get('draft_id', '—')}",
        f"- ECN 参考号：{result.get('ecn', '—')}",
        f"- 核查版本：{result.get('bom_version', '—')}",
        f"- 核查日期：{result.get('calculated_on', '—')}",
        f"- 计划生效日期：{result['change']['effective_date']}",
        f"- 变更类型：{result['change']['type_label']}",
        "- 核查范围：涉及成品及全部下层物料",
        "- 数据口径：当前本机脱敏数据",
        "",
        "> 本报告用于正式工程变更前的影响核查，不执行审批、发布或 ERP 写回。",
        "",
        "## 一、变更影响概览",
        "",
        rule_summary(result),
        "",
        "## 二、关联范围",
        "",
        f"- 直接父件：{metrics['direct_parents']}",
        f"- 全部上层物料：{metrics['all_parents']}",
        f"- 成品使用路径：{metrics['upstream_paths']}",
        f"- 直接下层物料：{metrics['direct_children']}",
        f"- 全部下层物料：{metrics['all_children']}",
        f"- 下层展开路径：{metrics['downstream_paths']}",
        f"- 涉及顶层成品：{metrics['top_materials']}",
        f"- 关联销售订单行：{metrics['sales_order_items']}",
        f"- 关联生产订单：{metrics['production_orders']}",
        "",
        "## 三、关注等级与原因",
        "",
        f"**{risk['level']}**",
        "",
    ]
    lines.extend(f"- {_business_text(reason)}" for reason in risk["reasons"])
    validation = result.get("material_validation", {})
    if validation.get("applicable"):
        lines.extend(
            [
                "",
                "### 新组件物料与供应准备",
                "",
                f"- 总体状态：{_business_text(validation.get('status', '待补证'))}",
                f"- 新组件：{_business_text(validation.get('material', '待填写物料'))}",
            ]
        )
        lines.extend(
            f"- {_business_text(item['label'])}：{_business_text(item['status'])}"
            f"（{_business_text(item['detail'])}）"
            for item in validation.get("checks", [])
        )
    planning = result.get("planning_impact", {})
    planning_metrics = planning.get("metrics", {})
    lines.extend(
        [
            "",
            "## 四、计划与备料影响",
            "",
            f"- 备料建议（初步）：{planning.get('recommendation', '待补供应数据')}",
            f"- 判断依据：{planning.get('rationale', '')}",
            f"- 关联生产订单：{planning_metrics.get('affected_production_orders', 0)}",
            f"- 需关注生产订单：{planning_metrics.get('active_production_orders', 0)}",
            f"- 待交付/未完工成品数量：{planning_metrics.get('remaining_production_quantity', 0)}",
            f"- 关联销售订单行：{planning_metrics.get('linked_sales_items', planning_metrics.get('affected_sales_items', 0))}",
            f"- 待确认关联销售订单行：{planning_metrics.get('pending_sales_items', 0)}",
            f"- 结论范围：{planning.get('scope', {}).get('material', '本次ECN变更料号')} · "
            f"工厂 {planning.get('scope', {}).get('plant', '待补')} · "
            f"库位 {planning.get('scope', {}).get('storage_location', '待补')}",
            "",
        ]
    )
    lines.extend(
        f"- {item['item']}：{item['status']}（{item['detail']}）"
        for item in planning.get("coverage", [])
    )
    lines.extend(["", f"> {planning.get('boundary', '')}", ""])

    manufacturing = result.get("manufacturing_impact", {})
    manufacturing_lines = [
        "## 五、制造执行影响",
        "",
        f"- ECN级候选 Disposition Code：{manufacturing.get('recommended_disposition', {}).get('code', 'UNCONFIRMED')} · "
        f"{manufacturing.get('recommended_disposition', {}).get('name', '未确认')}",
    ]
    recommendation = manufacturing.get("recommended_disposition")
    if recommendation:
        manufacturing_lines.append(
            f"- 建议依据：{recommendation.get('evidence_level', '不足')} · "
            f"{recommendation.get('rationale', '当前数据不足，需ME补充信息。')}"
        )
    manufacturing_lines.extend(
        [
            f"- 需ME确认对象：{manufacturing.get('review_object_count', 0)} 个",
            "",
        ]
    )
    lines.extend(manufacturing_lines)
    for recommendation in manufacturing.get("recommended_dispositions", []):
        lines.append(
            f"- {recommendation.get('ecn', '—')} 系统建议："
            f"{recommendation.get('code', 'UNCONFIRMED')} · "
            f"{recommendation.get('name', '未确认')}（"
            f"{recommendation.get('evidence_level', '不足')}）"
        )
    for object_item in manufacturing.get("review_objects", []):
        lines.append(
            f"- {object_item['production_order']} · {object_item['material']} · "
            f"{object_item['stage']}：{object_item['review_reason']}"
        )
    for gap in manufacturing.get("data_gaps", []):
        lines.append(f"- 数据缺口：{gap}")
    lines.extend(["", f"> {manufacturing.get('boundary', '')}", ""])

    lines.extend(["", "## 六、BOM 使用关系", ""])
    for direction_key, heading in (
        ("upstream", "涉及成品与上层物料：顶层成品 → 各级父件 → 变更料号"),
        ("downstream", "涉及下层物料：变更料号 → 末端物料"),
    ):
        lines.extend([f"### {heading}", ""])
        direction_paths = result["directions"][direction_key]["paths"]
        if direction_paths:
            for path in direction_paths:
                node_details = path.get("node_details", [])
                if direction_key == "upstream" and node_details:
                    display_text = " → ".join(
                        f"{node['alias']}（"
                        + (
                            "顶层成品 · L0"
                            if node["role"] in {"顶层BOM", "顶层成品"}
                            else f"{_business_text(node['role'])} · L{index}"
                        )
                        + "）"
                        for index, node in enumerate(reversed(node_details))
                    )
                else:
                    display_text = _business_text(
                        path.get("path_text_labeled", path["path_text"])
                    )
                lines.extend(
                    [
                        f"#### {path['path_id']} · 涉及顶层成品 {path['top_material']}",
                        "",
                        display_text,
                        "",
                    ]
                )
        else:
            lines.extend(
                ["- 当前生效日期下未找到该范围的完整使用路径。", ""]
            )

    lines.extend(["", "## 七、分工与待办事项", ""])
    for role, items in checklists.items():
        lines.extend([f"### {role}", ""])
        lines.extend(f"- [ ] {_business_text(item)}" for item in items)
        lines.append("")

    lines.extend(["## 八、待确认事项", ""])
    lines.extend(
        f"- **{warning['warning_id']} / {warning['severity']}**："
        f"{_business_text(warning['message'])}"
        for warning in result["warnings"]
    )
    lines.extend(["", "## 九、数据来源明细", ""])
    for path in result["paths"]:
        refs = ", ".join(
            f"{evidence['source_id']} 第 {evidence['source_row']} 行"
            if evidence["source_row"]
            else evidence["source_id"]
            for evidence in path["evidence"]
        )
        lines.append(f"- {path['path_id']}：{refs}")
    for order in result["sales_orders"]:
        lines.append(
            f"- {order['evidence_id']}：{order['source_id']} "
            f"第 {order['source_row']} 行"
        )
    for order in result["production_orders"]:
        lines.append(
            f"- {order['evidence_id']}：{order['source_id']} "
            f"第 {order['source_row']} 行"
        )
    if ai_text:
        lines.extend(
            [
                "",
                "## 十、补充说明",
                "",
                ai_text.strip(),
                "",
                "> 本段文字根据脱敏核查结果辅助生成，不参与影响范围和关注等级判定。",
            ]
        )
    lines.extend(
        [
            "",
            "---",
            "",
            "**使用说明**：可用库存、在途、Open PO和切换前后组件需求可人工补充用于情景测算；"
            "库位、实际领料、工序和 MES 状态仍需补充；"
            "正式审批、发布和 ERP 维护仍需按业务流程完成。",
            "",
        ]
    )
    return "\n".join(lines)
