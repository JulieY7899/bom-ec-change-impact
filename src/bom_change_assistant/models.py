from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any, TypedDict


@dataclass
class ImportResult:
    source_id: str
    source_type: str
    rows_seen: int
    rows_imported: int
    warnings: list[str] = field(default_factory=list)
    sheet_count: int = 1

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ChangeRequest:
    change_type: str
    propagation_direction: str = "upstream"
    old_component: str = ""
    new_component: str = ""
    target_parent: str = ""
    old_quantity: float | None = None
    new_quantity: float | None = None
    effective_date: str = ""
    old_effective_date: str = ""
    new_effective_date: str = ""
    ecn: str = ""
    bom_version: str = ""
    note: str = ""
    old_on_hand: float | None = None
    old_in_transit: float | None = None
    old_open_po: float | None = None
    old_liability: float | None = None
    old_consumable_demand: float | None = None
    new_on_hand: float | None = None
    new_in_transit: float | None = None
    new_open_po: float | None = None
    new_required_demand: float | None = None
    planning_plant: str = ""
    storage_location: str = ""
    disposition: str = ""
    disposition_code: str = ""
    disposition_scope: str = ""
    disposition_owner: str = ""
    disposition_status: str = "待评审"
    disposition_note: str = ""


class OrderStatus(StrEnum):
    # These values are the existing SQLite and report labels.
    UNKNOWN = "未知"
    OPEN = "已释放/执行中"
    COMPLETED = "已完成"
    CLOSED = "已关闭"
    CREATED = "已创建"
    REVIEW = "其他/待确认"


class RiskLevel(StrEnum):
    LOW = "低"
    MEDIUM = "中"
    HIGH = "高"

    @staticmethod
    def priority(level: str) -> int:
        return {RiskLevel.LOW: 0, RiskLevel.MEDIUM: 1, RiskLevel.HIGH: 2}.get(level, 0)


class RiskAssessment(TypedDict):
    level: RiskLevel
    reasons: list[str]


class AnalysisItem(TypedDict):
    result: dict[str, Any]
    report: str


class AnalysisBatch(TypedDict):
    draft_id: str
    items: list[AnalysisItem]
    groups: list[AnalysisItem]
