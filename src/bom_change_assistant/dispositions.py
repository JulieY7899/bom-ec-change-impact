"""First-phase manufacturing disposition code master.

The codes are intentionally kept small and explicit.  They are a business
configuration seam: once the customer's formal EC guideline is available,
the labels and mappings can be replaced without changing the impact engine.
"""

from __future__ import annotations

from typing import Any


UNCONFIRMED = {
    "code": "UNCONFIRMED",
    "name": "未确认",
    "scope": "待确认",
    "description": "尚未提供制造处置结论",
    "owner": "制造工程",
    "quality_required": True,
    "old_material_allowed": False,
    "plan_signal": "HOLD",
}


DISPOSITION_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "code": "USE_AS_IS",
        "name": "旧料可继续使用",
        "scope": "生产订单/WIP",
        "description": "无需返工，按批准的生效范围继续生产或放行。",
        "owner": "制造工程",
        "quality_required": False,
        "old_material_allowed": True,
        "plan_signal": "ALLOW_OLD_USE",
    },
    {
        "code": "USE_UP",
        "name": "旧料限期消耗",
        "scope": "旧料/生产订单",
        "description": "在规定的 Run-in 窗口内消耗旧料，并锁定截止日期。",
        "owner": "制造工程",
        "quality_required": False,
        "old_material_allowed": True,
        "plan_signal": "RUN_IN",
    },
    {
        "code": "REWORK",
        "name": "需返工",
        "scope": "WIP/完工品",
        "description": "隔离受影响对象，按批准的返工路线处理。",
        "owner": "制造工程",
        "quality_required": True,
        "old_material_allowed": False,
        "plan_signal": "HOLD_REWORK",
    },
    {
        "code": "HOLD_REVIEW",
        "name": "隔离待判",
        "scope": "WIP/完工品",
        "description": "先隔离并暂停后续动作，等待工程与质量共同判定。",
        "owner": "制造工程",
        "quality_required": True,
        "old_material_allowed": False,
        "plan_signal": "HOLD",
    },
    {
        "code": "SCRAP",
        "name": "报废",
        "scope": "旧料/WIP/完工品",
        "description": "隔离实物并按审批流程执行报废。",
        "owner": "制造工程",
        "quality_required": True,
        "old_material_allowed": False,
        "plan_signal": "HOLD",
    },
    {
        "code": "RETURN_SUPPLIER",
        "name": "退供应商",
        "scope": "库存/Open PO",
        "description": "转采购与供应商处理退货、取消或索赔。",
        "owner": "采购/供应链",
        "quality_required": False,
        "old_material_allowed": False,
        "plan_signal": "SUPPLY_ACTION",
    },
)

BY_CODE = {item["code"]: item for item in DISPOSITION_DEFINITIONS}
LEGACY_LABELS = {
    item["name"]: item["code"] for item in DISPOSITION_DEFINITIONS
}
LEGACY_LABELS["停线/待判"] = "HOLD_REVIEW"


def resolve_disposition(code: str = "", label: str = "") -> dict[str, Any]:
    """Resolve a formal code, while keeping old saved drafts compatible."""

    normalized_code = (code or "").strip().upper()
    if normalized_code in BY_CODE:
        return dict(BY_CODE[normalized_code])
    normalized_label = (label or "").strip()
    legacy_code = LEGACY_LABELS.get(normalized_label)
    if legacy_code:
        return dict(BY_CODE[legacy_code])
    return dict(UNCONFIRMED)


def disposition_options() -> list[dict[str, Any]]:
    return [dict(item) for item in DISPOSITION_DEFINITIONS]
