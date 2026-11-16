"""Conservative, review-only before/after impact checks.

No automatic order-BOM version migration, ERP write-back, or approval occurs.
"""
from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable
from typing import Any


def sum_known_quantities(values: Iterable[float | None]) -> float | None:
    """Only report a total when every contributing quantity is known."""
    total = 0.0
    for value in values:
        if value is None:
            return None
        total += float(value)
    return total


def production_progress_metrics(active_orders: list[dict[str, Any]]) -> dict[str, float | None]:
    """Keep unfinished and undelivered totals distinct, including unknowns."""
    unfinished = sum_known_quantities(item.get("remaining_quantity") for item in active_orders)
    undelivered = sum_known_quantities(item.get("undelivered_quantity") for item in active_orders)
    return {
        "unfinished_production_quantity": unfinished,
        "undelivered_production_quantity": undelivered,
        # Legacy field now consistently means unfinished quantity on every route.
        "remaining_production_quantity": unfinished,
    }


def order_demand_coverage(
    production_orders: list[dict[str, Any]], sales_orders: list[dict[str, Any]],
) -> list[dict[str, str]]:
    production_missing = sum(
        any(item.get(field) is None for field in (
            "order_quantity", "confirmed_quantity", "delivered_quantity",
        ))
        for item in production_orders
    )
    sales_missing = sum(item.get("order_quantity") is None for item in sales_orders)
    pending_sales = sum(
        item.get("status") in {"未知", "其他/待确认"} for item in sales_orders
    )
    return [
        {
            "item": "生产需求",
            "status": "数量待补" if production_missing else "已具备" if production_orders else "未匹配",
            "detail": f"{len(production_orders)} 个关联生产订单"
            + (f"，其中 {production_missing} 个数量待补" if production_missing else ""),
        },
        {
            "item": "销售需求",
            "status": "数量待补" if sales_missing else "待确认" if pending_sales else "已具备" if sales_orders else "未匹配",
            "detail": f"{len(sales_orders)} 个关联销售订单行，其中 {pending_sales} 个状态待确认"
            + (f"、{sales_missing} 个数量待补" if sales_missing else ""),
        },
    ]


def quantity_check(
    upstream_paths: list[dict[str, Any]], old_quantity: Any, new_quantity: Any,
    *, warnings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Compute theoretical component delta per ONE top unit, when evidence is complete."""
    boundary = (
        "按导入快照的有效 BOM 路径测算单位成品理论差量；未做配置筛选、"
        "订单版本适配、订单需求展开、库存扣减或实际生产影响确认。"
    )
    if old_quantity is None or new_quantity is None:
        return {"status": "待补数量", "by_top": [], "pending_paths": [], "boundary": boundary}
    try:
        old = float(old_quantity)
        new = float(new_quantity)
    except (ValueError, TypeError) as exc:
        raise ValueError("变更用量必须为数字") from exc
    if not all(math.isfinite(q) and q >= 0 for q in (old, new)):
        raise ValueError("变更用量必须是非负有限数字")

    delta = new - old
    incomplete = any(w.get("code") in {
        "PATH_LIMIT", "INCOMPLETE_BRANCH", "GRAPH_CYCLE", "INVALID_EFFECTIVITY"
    } for w in warnings or [])
    # Multiple imported files may represent distinct, unmodeled EBOM revisions.
    # Do not sum their paths and call the result a valid product quantity.
    top_sources: dict[str, set[str]] = defaultdict(set)
    for path in upstream_paths:
        for edge in path.get("evidence") or []:
            if edge.get("source_id"):
                top_sources[str(path.get("top_material", "未知成品"))].add(str(edge["source_id"]))
    ambiguous_tops = {top for top, sources in top_sources.items() if len(sources) > 1}
    by_top: dict[str, float] = defaultdict(float)
    pending: list[dict[str, str]] = []
    for path in upstream_paths:
        evidence = path.get("evidence") or []
        reason = (
            "同一成品有多个导入来源，尚未确认 EBOM 版本"
            if str(path.get("top_material", "未知成品")) in ambiguous_tops
            else None
        )
        if incomplete:
            reason = "结构路径不完整，无法确认完整用量"
        elif any(edge.get("alternative_group") for edge in evidence):
            reason = "存在替代组，尚未确认实际选用配置"
        if not evidence:
            reason = "缺少 BOM 行证据"
        elif not reason:
            direct = evidence[0].get("quantity")
            try:
                actual = float(direct)
                if not math.isfinite(actual) or not math.isclose(actual, old, rel_tol=1e-8, abs_tol=1e-8):
                    reason = "输入的旧用量与当前 BOM 行用量不一致"
            except (ValueError, TypeError):
                reason = "当前 BOM 行缺少可解析的旧用量"
        multiplier = 1.0
        if not reason:
            for parent_edge in evidence[1:]:
                try:
                    factor = float(parent_edge.get("quantity"))
                    if not math.isfinite(factor) or factor < 0:
                        raise ValueError()
                except (ValueError, TypeError):
                    reason = "上层 BOM 用量缺失或无效"
                    break
                multiplier *= factor
                if not math.isfinite(multiplier):
                    reason = "累计用量超出可计算范围"
                    break
        if not reason and not math.isfinite(delta * multiplier):
            reason = "用量差量超出可计算范围"
        if reason:
            pending.append({"path_id": str(path.get("path_id", "")), "reason": reason})
            continue
        by_top[str(path.get("top_material", "未知成品"))] += delta * multiplier
    # A partial sum can be mistaken for a full product total. Suppress all
    # totals until every contributing path has adequate evidence.
    if pending or incomplete or any(not math.isfinite(v) for v in by_top.values()):
        by_top.clear()
    return {
        "status": "需人工复核" if pending or incomplete or not upstream_paths or not by_top else "已完成理论测算",
        "old_quantity": old,
        "new_quantity": new,
        "by_top": [
            {"top_material": top, "delta_per_top_unit": amount}
            for top, amount in sorted(by_top.items())
        ],
        "pending_paths": pending,
        "boundary": boundary,
    }


def effectivity_comparison(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Compare valid graph paths at two dates within the same imported snapshot."""
    def paths(result: dict[str, Any]) -> set[tuple[Any, ...]]:
        return {
            (str(path.get("direction", "")), str(path.get("top_material", "")),
             tuple(str(n) for n in path.get("nodes", [])),
             tuple((str(e.get("source_id", "")), str(e.get("source_row", "")),
                    str(e.get("edge_id", ""))) for e in path.get("evidence", [])))
            for path in result.get("paths", [])
        }
    previous = paths(before)
    current = paths(after)
    cutoff = any(
        item.get("code") in {"PATH_LIMIT", "INCOMPLETE_BRANCH", "GRAPH_CYCLE", "INVALID_EFFECTIVITY"}
        for item in before.get("warnings", []) + after.get("warnings", [])
    )
    return {
        "status": "不完整，结构或生效性证据不足" if cutoff else "待业务确认",
        "added_paths": len(current - previous),
        "removed_paths": len(previous - current),
        "unchanged_paths": len(current & previous),
        "added_top_materials": sorted(set(after.get("top_materials", [])) - set(before.get("top_materials", []))),
        "removed_top_materials": sorted(set(before.get("top_materials", [])) - set(after.get("top_materials", []))),
        "boundary": (
            "仅对比同一导入快照下按日期过滤的有效 BOM 路径；"
            "不等价于不同 EBOM 版本的差异或订单 BOM 的自动迁移判断。"
        ),
    }
