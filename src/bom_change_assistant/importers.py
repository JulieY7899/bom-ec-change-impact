from __future__ import annotations

import hashlib
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from .models import ImportResult
from .normalization import (
    coerce_date,
    coerce_int,
    coerce_number,
    normalize_header,
    normalize_identifier,
    normalize_item_no,
    safe_text,
)
from .privacy import PrivacyKey
from .repository import Repository
from .xlsx_reader import XlsxWorkbook


def _header_set(headers: Iterable[str]) -> set[str]:
    return {normalize_header(value) for value in headers if value}


def classify_source(headers: list[str]) -> str:
    values = _header_set(headers)

    def has(*names: str) -> bool:
        return all(normalize_header(name) in values for name in names)

    if has(
        "场景编号",
        "数据属性",
        "旧物料",
        "新物料",
        "工厂",
        "库位",
        "旧料可用库存",
        "新料可用库存",
    ):
        return "planning_supply_demo"
    if has("组件物料", "直接父物料", "Valid-from", "Valid-to"):
        return "expanded_bom"
    if has("订单", "销售订单", "销售订单项目", "物料", "系统状态"):
        return "production_order"
    if has("销售凭证", "项目", "物料", "配置"):
        return "sales_order_item"
    if has("销售凭证", "凭证类别", "要求交货日") and not has("物料"):
        return "sales_order_header"
    if has("变更编号", "ObjTyp", "对象管理记录 GUID"):
        return "change_history"
    if has("BOM 类", "物料清单", "AltBOM", "BOM状态"):
        return "bom_header_reference"
    if has("BOM 类", "物料清单", "组件", "数量"):
        return "bom_item_reference"
    if has("对象标识", "组件数量", "级别"):
        return "bom_view_reference"
    if has("物料", "行业标准描述", "跨工厂物料状态"):
        return "material_master_reference"
    if has("物料", "工厂", "采购类型", "BOM用途"):
        return "plant_master_reference"
    if has("物料", "SOrg.", "渠道", "产品层次"):
        return "sales_master_reference"
    if has("物料", "TL类型", "组", "GrC"):
        return "assignment_reference"
    if has("物料", "供应商") and (
        normalize_header("采购组织") in values
        or normalize_header("工厂") in values
    ):
        return "source_list_reference"
    return "unrecognized"


def _indices(headers: list[str]) -> dict[str, list[int]]:
    result: dict[str, list[int]] = defaultdict(list)
    for index, header in enumerate(headers):
        result[normalize_header(header)].append(index)
    return result


def _value(
    row: list[Any],
    index_map: dict[str, list[int]],
    *candidate_headers: str,
    occurrence: int = 0,
) -> Any:
    for header in candidate_headers:
        indexes = index_map.get(normalize_header(header), [])
        if occurrence < len(indexes):
            index = indexes[occurrence]
            if index < len(row):
                return row[index]
    return None


def _first_nonempty(
    row: list[Any], index_map: dict[str, list[int]], *candidate_headers: str
) -> Any:
    for header in candidate_headers:
        for index in index_map.get(normalize_header(header), []):
            if index < len(row) and row[index] not in (None, ""):
                return row[index]
    return None


def _status_category(*values: Any) -> str:
    text = " ".join(safe_text(value, 80).upper() for value in values if value)
    if not text:
        return "未知"
    if any(token in text for token in ("TECO", "CLSD", "DLFL", "删除", "关闭")):
        return "已关闭"
    if any(token in text for token in ("DLV", "CNF", "完成", "交货")):
        return "已完成"
    if any(token in text for token in ("REL", "PCNF", "释放", "执行", "开工")):
        return "已释放/执行中"
    if any(token in text for token in ("CRTD", "创建")):
        return "已创建"
    return "其他/待确认"


def _master_status_category(*values: Any) -> str:
    text = " ".join(safe_text(value, 80).upper() for value in values if value)
    if not text:
        return "未提供"
    if any(
        token in text
        for token in (
            "BLOCK",
            "LOCK",
            "DELETE",
            "OBSOLETE",
            "冻结",
            "停用",
            "删除",
            "禁用",
        )
    ):
        return "受限或停用"
    return "可用"


class WorkbookImporter:
    def __init__(self, repository: Repository):
        self.repository = repository
        self.privacy: PrivacyKey = repository.privacy_key()

    @staticmethod
    def fingerprint(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    def import_file(self, path: str | Path) -> ImportResult:
        source_path = Path(path)
        source_hash = self.fingerprint(source_path)
        if self.repository.source_exists(source_hash):
            raise ValueError("该数据源已导入；为防止重复计算，本次已跳过")

        source_id = self.repository.next_source_id()
        total_seen = 0
        total_imported = 0
        source_types: list[str] = []
        warnings: list[str] = []
        sheet_count = 0

        with XlsxWorkbook(source_path) as workbook, self.repository.connect() as connection:
            connection.execute(
                """
                INSERT INTO sources(
                    source_id, source_type, source_hash, rows_seen,
                    rows_imported, warning_count, warnings_json
                ) VALUES (?, 'processing', ?, 0, 0, 0, '[]')
                """,
                (source_id, source_hash),
            )
            for sheet in workbook.sheets:
                sheet_count += 1
                headers, rows = workbook.iter_table(sheet)
                if sheet.name == "使用说明":
                    continue
                source_type = classify_source(headers)
                if source_type == "unrecognized" and not headers:
                    continue
                source_types.append(source_type)
                seen, imported, sheet_warnings = self._import_sheet(
                    connection, source_id, source_type, headers, rows
                )
                total_seen += seen
                total_imported += imported
                warnings.extend(sheet_warnings)

            unique_types = list(dict.fromkeys(source_types)) or ["unrecognized"]
            source_type = (
                unique_types[0] if len(unique_types) == 1 else "+".join(unique_types)
            )
            connection.execute(
                """
                UPDATE sources
                SET source_type=?, rows_seen=?, rows_imported=?,
                    warning_count=?, warnings_json=?
                WHERE source_id=?
                """,
                (
                    source_type,
                    total_seen,
                    total_imported,
                    len(warnings),
                    __import__("json").dumps(warnings, ensure_ascii=False),
                    source_id,
                ),
            )

        return ImportResult(
            source_id=source_id,
            source_type=source_type,
            rows_seen=total_seen,
            rows_imported=total_imported,
            warnings=warnings,
            sheet_count=sheet_count,
        )

    def _remember(
        self,
        connection: sqlite3.Connection,
        kind: str,
        entity_key: str | None,
        raw_value: Any,
    ) -> None:
        self.repository.register_business_identifier(
            connection, kind, entity_key, raw_value
        )

    def _import_sheet(
        self,
        connection: sqlite3.Connection,
        source_id: str,
        source_type: str,
        headers: list[str],
        rows: Iterable[tuple[int, list[Any]]],
    ) -> tuple[int, int, list[str]]:
        if source_type == "expanded_bom":
            return self._import_bom(connection, source_id, headers, rows)
        if source_type == "planning_supply_demo":
            return self._import_planning_supply_demo(
                connection, source_id, headers, rows
            )
        if source_type == "production_order":
            return self._import_production(connection, source_id, headers, rows)
        if source_type == "sales_order_item":
            return self._import_sales_items(connection, source_id, headers, rows)
        if source_type == "sales_order_header":
            return self._import_sales_headers(connection, source_id, headers, rows)
        if source_type == "change_history":
            return self._import_change_history(connection, source_id, headers, rows)
        if source_type == "assignment_reference":
            return self._import_assignment_reference(
                connection, source_id, headers, rows
            )
        if source_type in {
            "material_master_reference",
            "plant_master_reference",
            "sales_master_reference",
            "source_list_reference",
        }:
            return self._import_material_evidence(
                connection, source_id, source_type, headers, rows
            )

        seen = sum(1 for _ in rows)
        if source_type == "unrecognized":
            return seen, 0, ["存在未识别工作表；未写入分析模型"]
        return seen, 0, [f"{source_type} 已登记为参考证据，当前不参与主图计算"]

    def _import_planning_supply_demo(
        self,
        connection: sqlite3.Connection,
        source_id: str,
        headers: list[str],
        rows: Iterable[tuple[int, list[Any]]],
    ) -> tuple[int, int, list[str]]:
        index_map = _indices(headers)
        seen = imported = skipped = non_demo = incomplete = 0
        payload: list[tuple[Any, ...]] = []
        quantity_headers = (
            "旧料可用库存",
            "旧料在途",
            "旧料 Open PO",
            "切换前旧料可消耗需求",
            "新料可用库存",
            "新料在途",
            "新料 Open PO",
            "切换后新料需求",
        )
        for row_number, row in rows:
            seen += 1
            scenario_id = safe_text(_value(row, index_map, "场景编号"), 80)
            data_attribute = safe_text(_value(row, index_map, "数据属性"), 40).upper()
            old_raw = _value(row, index_map, "旧物料")
            new_raw = _value(row, index_map, "新物料")
            plant_raw = _value(row, index_map, "工厂")
            location = safe_text(_value(row, index_map, "库位"), 40)
            old_key = self.privacy.digest("material", old_raw)
            new_key = self.privacy.digest("material", new_raw)
            plant_key = self.privacy.digest("plant", plant_raw)
            if not all((scenario_id, old_key, new_key, plant_key, location)):
                skipped += 1
                continue
            if data_attribute != "DEMO_ONLY":
                non_demo += 1
                continue
            quantities = [
                coerce_number(_value(row, index_map, header))
                for header in quantity_headers
            ]
            if any(value is None for value in quantities):
                incomplete += 1
            self._remember(connection, "material", old_key, old_raw)
            self._remember(connection, "material", new_key, new_raw)
            self._remember(connection, "plant", plant_key, plant_raw)
            payload.append(
                (
                    scenario_id,
                    source_id,
                    row_number,
                    data_attribute,
                    old_key,
                    new_key,
                    plant_key,
                    location,
                    coerce_date(_value(row, index_map, "数据截止日期")),
                    coerce_date(_value(row, index_map, "计划生效日期")),
                    *quantities,
                    safe_text(_value(row, index_map, "预期建议"), 80) or None,
                )
            )
            imported += 1
        connection.executemany(
            """
            INSERT INTO planning_supply_scenarios(
                scenario_id, source_id, source_row, data_attribute,
                old_material_key, new_material_key, plant_key, storage_location,
                data_date, effective_date, old_on_hand, old_in_transit,
                old_open_po, old_consumable_demand, new_on_hand, new_in_transit,
                new_open_po, new_required_demand, expected_recommendation
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(scenario_id) DO UPDATE SET
                source_id=excluded.source_id,
                source_row=excluded.source_row,
                data_attribute=excluded.data_attribute,
                old_material_key=excluded.old_material_key,
                new_material_key=excluded.new_material_key,
                plant_key=excluded.plant_key,
                storage_location=excluded.storage_location,
                data_date=excluded.data_date,
                effective_date=excluded.effective_date,
                old_on_hand=excluded.old_on_hand,
                old_in_transit=excluded.old_in_transit,
                old_open_po=excluded.old_open_po,
                old_consumable_demand=excluded.old_consumable_demand,
                new_on_hand=excluded.new_on_hand,
                new_in_transit=excluded.new_in_transit,
                new_open_po=excluded.new_open_po,
                new_required_demand=excluded.new_required_demand,
                expected_recommendation=excluded.expected_recommendation
            """,
            payload,
        )
        warnings: list[str] = []
        if skipped:
            warnings.append(f"{skipped} 行缺少场景、物料、工厂或库位，已跳过")
        if non_demo:
            warnings.append(f"{non_demo} 行未标记 DEMO_ONLY，已拒绝导入")
        if incomplete:
            warnings.append(f"{incomplete} 个场景数量不完整，只能提示数据缺口")
        return seen, imported, warnings

    def _import_material_evidence(
        self,
        connection: sqlite3.Connection,
        source_id: str,
        source_type: str,
        headers: list[str],
        rows: Iterable[tuple[int, list[Any]]],
    ) -> tuple[int, int, list[str]]:
        index_map = _indices(headers)
        seen = imported = 0
        payload: list[tuple[Any, ...]] = []
        for row_number, row in rows:
            seen += 1
            material_raw = _value(row, index_map, "物料")
            material_key = self.privacy.digest("material", material_raw)
            if not material_key:
                continue
            plant_raw = _value(row, index_map, "工厂")
            plant_key = self.privacy.digest("plant", plant_raw) or None
            self._remember(connection, "material", material_key, material_raw)
            self._remember(connection, "plant", plant_key, plant_raw)
            supplier_raw = _first_nonempty(
                row, index_map, "供应商", "固定供应商", "来源供应商"
            )
            status = _master_status_category(
                _first_nonempty(
                    row,
                    index_map,
                    "跨工厂物料状态",
                    "工厂特定物料状态",
                    "工厂物料状态",
                    "销售状态",
                    "物料状态",
                    "删除标识",
                )
            )
            procurement_type = safe_text(
                _first_nonempty(
                    row,
                    index_map,
                    "采购类型",
                    "特殊采购类型",
                    "采购组",
                ),
                40,
            )
            payload.append(
                (
                    source_id,
                    row_number,
                    material_key,
                    source_type,
                    plant_key,
                    status,
                    procurement_type or None,
                    self.privacy.digest("supplier", supplier_raw) or None,
                )
            )
            imported += 1
        connection.executemany(
            """
            INSERT INTO material_evidence(
                source_id, source_row, material_key, evidence_type,
                plant_key, status_category, procurement_type, supplier_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            payload,
        )
        return seen, imported, []

    def _import_assignment_reference(
        self,
        connection: sqlite3.Connection,
        source_id: str,
        headers: list[str],
        rows: Iterable[tuple[int, list[Any]]],
    ) -> tuple[int, int, list[str]]:
        index_map = _indices(headers)
        seen = imported = 0
        evidence_payload: list[tuple[Any, ...]] = []
        routing_payload: list[tuple[Any, ...]] = []
        for row_number, row in rows:
            seen += 1
            material_raw = _value(row, index_map, "物料")
            material_key = self.privacy.digest("material", material_raw)
            if not material_key:
                continue
            plant_raw = _value(row, index_map, "工厂")
            plant_key = self.privacy.digest("plant", plant_raw) or None
            routing_group_raw = _value(row, index_map, "组")
            routing_group_key = (
                self.privacy.digest("routing_group", routing_group_raw) or None
            )
            self._remember(connection, "material", material_key, material_raw)
            self._remember(connection, "plant", plant_key, plant_raw)
            self._remember(
                connection,
                "routing_group",
                routing_group_key,
                routing_group_raw,
            )
            evidence_payload.append(
                (
                    source_id,
                    row_number,
                    material_key,
                    "assignment_reference",
                    plant_key,
                    "可用",
                    None,
                    None,
                )
            )
            routing_payload.append(
                (
                    source_id,
                    row_number,
                    material_key,
                    plant_key,
                    safe_text(_value(row, index_map, "TL类型"), 20) or None,
                    routing_group_key,
                    safe_text(_value(row, index_map, "GrC"), 20) or None,
                    coerce_date(_value(row, index_map, "有效期自")),
                    coerce_date(_value(row, index_map, "有效期至")),
                    safe_text(_value(row, index_map, "工艺路线版本"), 40) or None,
                )
            )
            imported += 1
        connection.executemany(
            """
            INSERT INTO material_evidence(
                source_id, source_row, material_key, evidence_type,
                plant_key, status_category, procurement_type, supplier_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            evidence_payload,
        )
        connection.executemany(
            """
            INSERT INTO routing_assignments(
                source_id, source_row, material_key, plant_key,
                task_list_type, routing_group_key, group_counter,
                valid_from, valid_to, routing_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            routing_payload,
        )
        return seen, imported, []

    def _import_bom(
        self,
        connection: sqlite3.Connection,
        source_id: str,
        headers: list[str],
        rows: Iterable[tuple[int, list[Any]]],
    ) -> tuple[int, int, list[str]]:
        index_map = _indices(headers)
        seen = imported = missing_identity = invalid_dates = 0
        payload: list[tuple[Any, ...]] = []
        for row_number, row in rows:
            seen += 1
            top_raw = _value(row, index_map, "物料编码")
            parent_raw = _value(row, index_map, "直接父物料")
            component_raw = _value(row, index_map, "组件物料")
            if not all(
                normalize_identifier(value)
                for value in (top_raw, parent_raw, component_raw)
            ):
                missing_identity += 1
                continue

            valid_from_raw = _value(row, index_map, "Valid-from", "有效期自")
            valid_to_raw = _value(row, index_map, "Valid-to", "有效期至")
            valid_from = coerce_date(valid_from_raw)
            valid_to = coerce_date(valid_to_raw)
            date_valid = int(
                not (
                    (valid_from_raw not in (None, "") and valid_from is None)
                    or (valid_to_raw not in (None, "") and valid_to is None)
                    or (
                        valid_from is not None
                        and valid_to is not None
                        and valid_from > valid_to
                    )
                )
            )
            if not date_valid:
                invalid_dates += 1

            change_raw = _value(row, index_map, "变更号", "变更编号")
            alternative_raw = _value(row, index_map, "替代项目组", "AltID")
            plant_raw = _value(row, index_map, "工厂")
            top_key = self.privacy.digest("material", top_raw)
            parent_key = self.privacy.digest("material", parent_raw)
            component_key = self.privacy.digest("material", component_raw)
            change_key = self.privacy.digest("change", change_raw) or None
            alternative_key = (
                self.privacy.digest("alternative", alternative_raw) or None
            )
            plant_key = self.privacy.digest("plant", plant_raw) or None
            self._remember(connection, "material", top_key, top_raw)
            self._remember(connection, "material", parent_key, parent_raw)
            self._remember(connection, "material", component_key, component_raw)
            self._remember(connection, "change", change_key, change_raw)
            self._remember(
                connection, "alternative", alternative_key, alternative_raw
            )
            self._remember(connection, "plant", plant_key, plant_raw)
            payload.append(
                (
                    source_id,
                    row_number,
                    top_key,
                    parent_key,
                    component_key,
                    coerce_int(_value(row, index_map, "层", "级别")),
                    coerce_number(_value(row, index_map, "用量", "数量")),
                    safe_text(_value(row, index_map, "单位", "计量单位", "UoM"), 24),
                    valid_from,
                    valid_to,
                    date_valid,
                    change_key,
                    alternative_key,
                    plant_key,
                )
            )
            imported += 1
            if len(payload) >= 1000:
                self._insert_bom_batch(connection, payload)
                payload.clear()
        if payload:
            self._insert_bom_batch(connection, payload)

        warnings: list[str] = []
        if missing_identity:
            warnings.append(f"{missing_identity} 行缺少顶层、父件或组件标识，已跳过")
        if invalid_dates:
            warnings.append(f"{invalid_dates} 行日期无法解析或起止倒置，分析时排除")
        return seen, imported, warnings

    @staticmethod
    def _insert_bom_batch(
        connection: sqlite3.Connection, payload: list[tuple[Any, ...]]
    ) -> None:
        connection.executemany(
            """
            INSERT INTO bom_edges(
                source_id, source_row, top_key, parent_key, component_key,
                level, quantity, unit, valid_from, valid_to, date_valid,
                change_key, alternative_key, plant_key
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            payload,
        )

    def _import_sales_headers(
        self,
        connection: sqlite3.Connection,
        source_id: str,
        headers: list[str],
        rows: Iterable[tuple[int, list[Any]]],
    ) -> tuple[int, int, list[str]]:
        index_map = _indices(headers)
        seen = imported = 0
        payload = []
        for row_number, row in rows:
            seen += 1
            order_raw = _value(row, index_map, "销售凭证")
            order_key = self.privacy.digest("sales_order", order_raw)
            if not order_key:
                continue
            self._remember(connection, "sales_order", order_key, order_raw)
            payload.append(
                (
                    order_key,
                    source_id,
                    row_number,
                    "未知",
                    coerce_date(_value(row, index_map, "要求交货日")),
                )
            )
            imported += 1
        connection.executemany(
            """
            INSERT INTO sales_headers VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(sales_order_key) DO UPDATE SET
                source_id=excluded.source_id,
                source_row=excluded.source_row,
                requested_date=COALESCE(excluded.requested_date, requested_date)
            """,
            payload,
        )
        return seen, imported, []

    def _import_sales_items(
        self,
        connection: sqlite3.Connection,
        source_id: str,
        headers: list[str],
        rows: Iterable[tuple[int, list[Any]]],
    ) -> tuple[int, int, list[str]]:
        index_map = _indices(headers)
        seen = imported = 0
        payload = []
        metrics_payload = []
        for row_number, row in rows:
            seen += 1
            order_raw = _value(row, index_map, "销售凭证")
            item_raw = _value(row, index_map, "项目")
            material_raw = _value(row, index_map, "物料")
            order_key = self.privacy.digest("sales_order", order_raw)
            item_normalized = normalize_item_no(item_raw)
            material_key = self.privacy.digest("material", material_raw)
            if not order_key or not item_normalized or not material_key:
                continue
            item_key = self.privacy.digest("sales_item", item_normalized)
            self._remember(connection, "sales_order", order_key, order_raw)
            self._remember(connection, "sales_item", item_key, item_raw)
            self._remember(connection, "material", material_key, material_raw)
            sales_item_key = self.privacy.digest(
                "sales_order_item", f"{normalize_identifier(order_raw)}|{item_normalized}"
            )
            configuration_raw = _value(row, index_map, "配置")
            plant_raw = _value(row, index_map, "工厂")
            plant_key = self.privacy.digest("plant", plant_raw) or None
            self._remember(connection, "plant", plant_key, plant_raw)
            payload.append(
                (
                    sales_item_key,
                    source_id,
                    row_number,
                    order_key,
                    item_key,
                    material_key,
                    self.privacy.digest("configuration", configuration_raw) or None,
                    _status_category(_value(row, index_map, "ST", "S")),
                    coerce_date(_value(row, index_map, "要求交货日")),
                )
            )
            metrics_payload.append(
                (
                    sales_item_key,
                    plant_key,
                    coerce_number(_value(row, index_map, "订单数量")),
                )
            )
            imported += 1
        connection.executemany(
            """
            INSERT INTO sales_items VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(sales_item_key) DO UPDATE SET
                source_id=excluded.source_id,
                source_row=excluded.source_row,
                material_key=excluded.material_key,
                configuration_key=excluded.configuration_key,
                status_category=excluded.status_category,
                requested_date=excluded.requested_date
            """,
            payload,
        )
        connection.executemany(
            """
            INSERT INTO sales_item_metrics(
                sales_item_key, plant_key, order_quantity
            ) VALUES (?, ?, ?)
            ON CONFLICT(sales_item_key) DO UPDATE SET
                plant_key=COALESCE(excluded.plant_key, plant_key),
                order_quantity=COALESCE(excluded.order_quantity, order_quantity)
            """,
            metrics_payload,
        )
        return seen, imported, []

    def _import_production(
        self,
        connection: sqlite3.Connection,
        source_id: str,
        headers: list[str],
        rows: Iterable[tuple[int, list[Any]]],
    ) -> tuple[int, int, list[str]]:
        index_map = _indices(headers)
        seen = imported = 0
        payload = []
        metrics_payload = []
        for row_number, row in rows:
            seen += 1
            order_raw = _value(row, index_map, "订单")
            material_raw = _value(row, index_map, "物料")
            production_key = self.privacy.digest("production_order", order_raw)
            material_key = self.privacy.digest("material", material_raw)
            if not production_key or not material_key:
                continue
            sales_order_raw = _value(row, index_map, "销售订单")
            sales_item_raw = _value(row, index_map, "销售订单项目")
            sales_order_key = self.privacy.digest("sales_order", sales_order_raw) or None
            item_normalized = normalize_item_no(sales_item_raw)
            sales_item_key = (
                self.privacy.digest(
                    "sales_order_item",
                    f"{normalize_identifier(sales_order_raw)}|{item_normalized}",
                )
                if sales_order_key and item_normalized
                else None
            )
            self._remember(connection, "production_order", production_key, order_raw)
            self._remember(connection, "material", material_key, material_raw)
            self._remember(
                connection, "sales_order", sales_order_key, sales_order_raw
            )
            plant_raw = _value(row, index_map, "工厂")
            plant_key = self.privacy.digest("plant", plant_raw) or None
            self._remember(connection, "plant", plant_key, plant_raw)
            payload.append(
                (
                    production_key,
                    source_id,
                    row_number,
                    material_key,
                    sales_order_key,
                    sales_item_key,
                    _status_category(
                        _value(row, index_map, "用户状态"),
                        _value(row, index_map, "系统状态"),
                    ),
                    coerce_date(_value(row, index_map, "创建日期")),
                    coerce_date(
                        _value(row, index_map, "实际开始时间", occurrence=0)
                    ),
                    coerce_date(_value(row, index_map, "实际完成日期")),
                )
            )
            metrics_payload.append(
                (
                    production_key,
                    plant_key,
                    coerce_number(
                        _value(row, index_map, "订单数量 (GMEIN)", "订单数量")
                    ),
                    coerce_number(
                        _value(row, index_map, "已交货数量 (GMEIN)", "已交货数量")
                    ),
                    coerce_number(
                        _value(row, index_map, "确认的产量 (GMEIN)", "确认的产量")
                    ),
                )
            )
            imported += 1
        connection.executemany(
            """
            INSERT INTO production_orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(production_order_key) DO UPDATE SET
                source_id=excluded.source_id,
                source_row=excluded.source_row,
                material_key=excluded.material_key,
                sales_order_key=COALESCE(excluded.sales_order_key, sales_order_key),
                sales_item_key=COALESCE(excluded.sales_item_key, sales_item_key),
                status_category=excluded.status_category,
                created_date=COALESCE(excluded.created_date, created_date),
                actual_start=COALESCE(excluded.actual_start, actual_start),
                actual_finish=COALESCE(excluded.actual_finish, actual_finish)
            """,
            payload,
        )
        connection.executemany(
            """
            INSERT INTO production_order_metrics(
                production_order_key, plant_key, order_quantity,
                delivered_quantity, confirmed_quantity
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(production_order_key) DO UPDATE SET
                plant_key=COALESCE(excluded.plant_key, plant_key),
                order_quantity=COALESCE(excluded.order_quantity, order_quantity),
                delivered_quantity=COALESCE(
                    excluded.delivered_quantity, delivered_quantity
                ),
                confirmed_quantity=COALESCE(
                    excluded.confirmed_quantity, confirmed_quantity
                )
            """,
            metrics_payload,
        )
        return seen, imported, []

    def _import_change_history(
        self,
        connection: sqlite3.Connection,
        source_id: str,
        headers: list[str],
        rows: Iterable[tuple[int, list[Any]]],
    ) -> tuple[int, int, list[str]]:
        index_map = _indices(headers)
        seen = imported = 0
        payload: list[tuple[Any, ...]] = []
        for row_number, row in rows:
            seen += 1
            change_raw = _value(row, index_map, "变更编号", "变更号")
            change_key = self.privacy.digest("change", change_raw)
            if not change_key:
                continue
            self._remember(connection, "change", change_key, change_raw)
            object_raw = _first_nonempty(row, index_map, "对象")
            payload.append(
                (
                    source_id,
                    row_number,
                    change_key,
                    self.privacy.digest("change_object", object_raw) or None,
                    _status_category(_value(row, index_map, "状态")),
                    coerce_date(
                        _first_nonempty(row, index_map, "更改日期", "创建日期")
                    ),
                )
            )
            imported += 1
            if len(payload) >= 2000:
                connection.executemany(
                    """
                    INSERT INTO change_history(
                        source_id, source_row, change_key, object_key,
                        status_category, changed_date
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    payload,
                )
                payload.clear()
        if payload:
            connection.executemany(
                """
                INSERT INTO change_history(
                    source_id, source_row, change_key, object_key,
                    status_category, changed_date
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                payload,
            )
        return seen, imported, []
