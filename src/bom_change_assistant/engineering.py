"""Read-only engineering revision, BOM position and order reference checks."""
from __future__ import annotations

import json
import math
import re
from collections import defaultdict

from .impact_checks import quantity_check
from .normalization import coerce_date, coerce_number, normalize_identifier, normalize_item_no

SCHEMA = """
CREATE TABLE IF NOT EXISTS engineering_evidence (
    source_id TEXT NOT NULL REFERENCES sources(source_id),
    sheet INTEGER NOT NULL,
    source_row INTEGER NOT NULL,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    PRIMARY KEY (source_id, sheet, source_row)
);
"""

KINDS = {"VERSION", "BOM_ROW", "ORDER_REFERENCE", "ORDER_ROW"}
SCOPE_FIELDS = ("product", "plant", "engineering_version")
ORDER_FIELDS = ("order_kind", "order_id", "order_item")
OPEN = {"开放", "已释放", "执行中", "完工待交付"}
CLOSED = {"已关闭", "已取消", "已拒绝"}
STATUS = OPEN | CLOSED | {"未知"}
COUNT_UNITS = {"EA", "PC", "PCS", "件", "个"}
CHANGE_TYPES = {"replace": "替换组件", "remove": "删除组件", "quantity": "修改用量"}
IDENTIFIER = re.compile(r"[\w .:/+()#@=-]{1,100}")


def identifier(value, label, required=False):
    if value is None or value == "":
        if not required:
            return ""
        raise ValueError(f"缺少{label}")
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    value = str(value).strip()
    if not value and not required:
        return ""
    if not IDENTIFIER.fullmatch(value):
        raise ValueError(f"{label}包含无效字符或超出长度限制")
    return value


def scope_key(row):
    return (
        normalize_identifier(row["product"]),
        normalize_identifier(row["plant"]),
        row["engineering_version"],
    )


def order_key(row):
    order, item = row["order_id"], row["order_item"]
    if row["order_kind"] != "ORDER_BOM":
        order, item = normalize_identifier(order), normalize_item_no(item)
    return row["order_kind"], order, item


def amount(value):
    if isinstance(value, bool):
        return None
    number = coerce_number(value)
    return number if number is not None and math.isfinite(number) and number >= 0 else None


def canonical(kind, raw):
    row = {
        field: identifier(raw.get(field), field, field != "plant" and
                          (field != "engineering_version" or kind != "ORDER_REFERENCE"))
        for field in SCOPE_FIELDS
    }
    if kind == "BOM_ROW":
        row.update({
            field: identifier(raw.get(field), field, field != "parent_row_id")
            for field in ("row_id", "parent", "component", "parent_row_id")
        })
        start, end = (coerce_date(raw.get(field)) for field in ("valid_from", "valid_to"))
        row.update({
            "valid_from": start,
            "valid_to": end,
            "date_valid": (
                (start is not None or raw.get("valid_from") in (None, ""))
                and (end is not None or raw.get("valid_to") in (None, ""))
                and not (start and end and start > end)
            ),
            "quantity": amount(raw.get("quantity")),
            "unit": identifier(raw.get("unit"), "计量单位").upper(),
        })
    if kind in {"ORDER_REFERENCE", "ORDER_ROW"}:
        order_kind = str(raw.get("order_kind") or "").strip().upper()
        if order_kind not in {"ORDER_BOM", "SALES", "PRODUCTION"}:
            raise ValueError("订单类型须为 ORDER_BOM、SALES 或 PRODUCTION")
        row.update(
            order_kind=order_kind,
            order_id=identifier(raw.get("order_id"), "订单编号", True),
            order_item=identifier(raw.get("order_item"), "订单行号", order_kind == "SALES"),
        )
        if order_kind == "PRODUCTION" and row["order_item"]:
            raise ValueError("生产订单按单号引用，请将订单行号留空")
    if kind == "ORDER_REFERENCE":
        status = str(raw.get("order_status") or "").strip()
        row.update(
            order_status=status if status in STATUS else "未知",
            order_relevant_date=coerce_date(raw.get("order_relevant_date")),
            purchased=confirmation(raw.get("purchased")),
            received=confirmation(raw.get("received")),
        )
    if kind == "ORDER_ROW":
        application = str(raw.get("row_application") or "").strip().upper()
        row.update(
            row_id=identifier(raw.get("row_id"), "BOM 行号", True),
            row_application=application if application in {"APPLIES", "EXCLUDED"} else "UNKNOWN",
        )
    return row


def confirmation(value):
    text = str(value).strip().casefold()
    if text in {"是", "yes", "true", "1"}:
        return "是"
    if text in {"否", "no", "false", "0"}:
        return "否"
    return "未知"


def import_rows(connection, source_id, sheet, headers, rows):
    fields = [str(header or "").strip().lower() for header in headers]
    if len(set(filter(None, fields))) != len(list(filter(None, fields))):
        raise ValueError(f"{source_id} 表 {sheet} 的工程版本列名重复")
    count = 0
    for row_number, cells in rows:
        raw = dict(zip(fields, cells))
        if not any(value not in ("", None) for value in raw.values()):
            continue
        kind = str(raw.get("engineering_record") or "").strip().upper()
        try:
            if kind not in KINDS:
                raise ValueError("无效的工程版本记录类型")
            row = canonical(kind, raw)
        except ValueError as exc:
            raise ValueError(f"{source_id} 表 {sheet} 第 {row_number} 行：{exc}") from exc
        connection.execute(
            "INSERT INTO engineering_evidence VALUES (?, ?, ?, ?, ?)",
            (source_id, sheet, row_number, kind, json.dumps(row, ensure_ascii=False)),
        )
        count += 1
    return count, count, []


def same_row(left, right):
    compared = {"parent", "component", "parent_row_id", "quantity", "unit",
                "valid_from", "valid_to", "date_valid"}
    return all(
        normalize_identifier(left[field]) == normalize_identifier(right[field])
        if field in {"parent", "component"} else left[field] == right[field]
        for field in compared
    )


class VersionSnapshot:
    def __init__(self, connection):
        self.versions = {}
        self.rows = defaultdict(dict)
        self.positions = defaultdict(lambda: defaultdict(list))
        self.orders = defaultdict(list)
        self.applications = defaultdict(list)
        for entry in connection.execute(
            "SELECT * FROM engineering_evidence ORDER BY source_id, sheet, source_row"
        ):
            kind = entry["kind"]
            row = json.loads(entry["content"])
            row["evidence"] = [{k: entry[k] for k in ("source_id", "sheet", "source_row")}]
            key = scope_key(row)
            if kind == "VERSION":
                self.add(self.versions, key, row)
            elif kind == "BOM_ROW":
                self.add(self.rows[key], row["row_id"], row)
            elif kind == "ORDER_REFERENCE":
                self.orders[order_key(row)].append(row)
            else:
                self.applications[(order_key(row), key, row["row_id"])].append(row)
        for key, rows in self.rows.items():
            for row in rows.values():
                component = normalize_identifier(row["component"])
                self.positions[key][component].append(row)

    @staticmethod
    def add(collection, key, row):
        previous = collection.get(key)
        if previous is None:
            collection[key] = row
        elif "row_id" in row and not same_row(row, previous):
            raise ValueError(f"同键 BOM 行结构冲突，请核对来源 {evidence_text(previous['evidence'] + row['evidence'])}")
        else:
            previous["evidence"].extend(row["evidence"])

    def validate(self):
        viable = False
        for key, rows in self.rows.items():
            if key not in self.versions:
                raise ValueError("版本 BOM 行的所属工程版本未导入")
            for row in rows.values():
                upper_id = row["parent_row_id"]
                parent = normalize_identifier(row["parent"])
                if upper_id:
                    upper = rows.get(upper_id)
                    if upper is None or normalize_identifier(upper["component"]) != parent:
                        raise ValueError(
                            f"显式上层 BOM 行缺失或父件不符：{evidence_text(row['evidence'])}"
                        )
                elif parent == key[0] and row["date_valid"]:
                    viable = True
        return viable

    def catalog(self):
        return [
            {**version, "rows": list(self.rows.get(key, {}).values())}
            for key, version in self.versions.items()
        ]

    def path(self, key, target, effective_date):
        """The path identifies exact BOM occurrences, even for repeated assemblies."""
        path = []
        visited = set()
        row = target
        while row:
            row_id = row["row_id"]
            if row_id in visited:
                return [], "路径存在 BOM 环路", path
            visited.add(row_id)
            path.append(row)
            if not row["date_valid"]:
                return [], "路径含无效或倒置生效日期", path
            if ((row["valid_from"] and effective_date < row["valid_from"])
                    or (row["valid_to"] and effective_date > row["valid_to"])):
                return [], "BOM 行或上层路径在该日未生效", path
            if row["parent_row_id"]:
                row = self.rows[key][row["parent_row_id"]]
                continue
            parent = normalize_identifier(row["parent"])
            if parent == key[0]:
                return list(reversed(path)), "", path
            options = self.positions[key].get(parent, [])
            if len(options) != 1:
                reason = "上层 BOM 位置不唯一，请明确上层行号" if options else "BOM 路径断链"
                return [], reason, path
            row = options[0]

    def classify_orders(self, key, row_id, effective_date):
        matches, excluded, pending = [], [], []
        for identity, entries in self.orders.items():
            if not any(scope_key(item)[0] == key[0] for item in entries):
                continue
            keys = {scope_key(item) for item in entries}
            order = {**entries[0], "identity": list(identity),
                     "evidence": collect_evidence(entries)}
            linked = next(iter(keys)) if len(keys) == 1 else None
            if linked is None:
                order.update(
                    reason="同一订单有多个工程版本引用",
                    references=[{**item} for item in entries],
                )
                pending.append(order)
            elif not linked[2]:
                pending.append({**order, "reason": "缺少工程版本引用"})
            elif linked not in self.versions:
                pending.append({**order, "reason": "引用的工程版本范围未导入"})
            elif linked != key:
                excluded.append({**order, "reason": "引用其他工程版本范围"})
            else:
                applications = self.applications[(identity, linked, row_id)]
                matches.append(assess_order(order, entries, applications, effective_date))
        return matches, excluded, pending


def collect_evidence(entries):
    return [source for entry in entries for source in entry["evidence"]]


def one_value(entries, field, default):
    values = {record[field] for record in entries}
    return next(iter(values)) if len(values) == 1 else default


def assess_order(order, entries, applications, effective_date):
    application = one_value(applications, "row_application", "UNKNOWN")
    status = one_value(entries, "order_status", "未知")
    day = one_value(entries, "order_relevant_date", None)
    purchased = one_value(entries, "purchased", "未知")
    received = one_value(entries, "received", "未知")
    reasons = []
    if status in CLOSED:
        disposition = "仅保留引用证据"
        reasons.append("订单已终止")
    elif application == "EXCLUDED":
        disposition = "排除行影响"
        reasons.append("订单配置排除此行")
    else:
        if application == "UNKNOWN":
            reasons.append(
                "订单行适用性证据冲突"
                if len({item["row_application"] for item in applications}) > 1
                else "订单行适用性待核"
            )
        if status not in OPEN:
            reasons.append("订单执行状态待核")
        if day is None:
            reasons.append("订单需求或执行日期待核")
        elif day < effective_date:
            reasons.append("订单日期早于拟生效日，请核对实际执行")
        disposition = "待核对" if reasons else "待业务评审"
    purchase_conflict = (
        any(len({entry[field] for entry in entries} - {"未知"}) > 1
            for field in ("purchased", "received"))
        or (purchased == "否" and received == "是")
    )
    purchase_review = disposition in {"待核对", "待业务评审"} and (
        purchase_conflict or any(entry[field] == "是"
                                 for entry in entries for field in ("purchased", "received"))
    )
    if purchase_conflict:
        reasons.append("订单采购证据冲突")
    return {
        **order, "order_status": status, "order_relevant_date": day,
        "purchased": purchased, "received": received, "row_application": application,
        "application_evidence": collect_evidence(applications),
        "application_records": applications,
        "purchase_evidence": [
            {field: entry[field] for field in ("purchased", "received", "evidence")}
            for entry in entries
        ],
        "review_status": disposition, "review_reasons": reasons,
        "line_candidate": application == "APPLIES" and status not in CLOSED,
        "review_owners": ["工程", "采购", "计划"] if purchase_review else ["工程", "计划"],
    }


def evidence_text(evidence):
    return "、".join(
        f"{source['source_id']} 表 {source['sheet']} 第 {source['source_row']} 行"
        for source in evidence
    )


def analyze(repository, payload):
    change_type = payload.get("change_type")
    if change_type not in CHANGE_TYPES:
        raise ValueError("工程版本核查仅支持现有行的替换、删除或用量调整")
    scope = {name: identifier(payload.get(name), name, name != "plant") for name in SCOPE_FIELDS}
    key = scope_key(scope)
    row_id = identifier(payload.get("target_bom_row"), "目标 BOM 行号", True)
    old = identifier(payload.get("old_component"), "原组件", True)
    new = identifier(payload.get("new_component"), "新组件", change_type == "replace")
    parent = identifier(payload.get("target_parent"), "父件")
    day = coerce_date(payload.get("effective_date"))
    if not day:
        raise ValueError("请填写有效的拟生效日期")
    old_qty, new_qty = (amount(payload.get(field)) for field in ("old_quantity", "new_quantity"))
    if change_type == "quantity" and None in (old_qty, new_qty):
        raise ValueError("原用量和新用量必须为非负有限数字")

    with repository.connect() as connection:
        snapshot = VersionSnapshot(connection)
    version = snapshot.versions.get(key)
    if version is None:
        raise ValueError("未找到指定成品、工厂范围和工程版本")
    target = snapshot.rows[key].get(row_id)
    if target is None:
        raise ValueError("该工程版本中未找到指定 BOM 行")
    if normalize_identifier(target["component"]) != normalize_identifier(old):
        raise ValueError("原组件与指定 BOM 行不符")
    if parent and normalize_identifier(target["parent"]) != normalize_identifier(parent):
        raise ValueError("父件与指定 BOM 行不符")
    path, issue, inspected = snapshot.path(key, target, day)
    matches, excluded, pending = snapshot.classify_orders(key, row_id, day) if path else ([], [], [])
    quantity = None
    if change_type == "quantity":
        if path and target["unit"] and all(row["unit"] in COUNT_UNITS for row in path[:-1]):
            # Version and positions are already verified. Multiple sources can
            # provide evidence for a single engineering structure.
            quantity = quantity_check(
                [{"path_id": row_id, "top_material": version["product"],
                  "evidence": [{"quantity": row["quantity"]} for row in reversed(path)]}],
                old_qty, new_qty,
            )
            quantity["unit"] = target["unit"]
        else:
            quantity = {"status": "需人工复核", "by_top": [],
                        "pending_paths": [{"path_id": row_id, "reason": issue or "路径计量单位待核，未确认上层总成的计数单位"}]}
    return {
        "version": version, "target": target, "path": path, "inspected_rows": inspected,
        "path_issue": issue, "effective_date": day, "change_type": change_type,
        "old_component": target["component"], "new_component": new,
        "version_reference_matches": matches, "excluded_orders": excluded,
        "pending_references": pending, "quantity_impact": quantity,
        "automatic_version_migration": False, "approved_action": None,
    }


def reference_label(order):
    return " / ".join(
        part for part in (order["order_kind"], order["order_id"], order["order_item"]) if part
    )


def sections(checks, unique_orders):
    output = []
    for check in checks:
        version, row = check["version"], check["target"]
        scope = " / ".join((version["product"], version["plant"] or "未指定工厂",
                            version["engineering_version"], row["row_id"]))
        unit = row["unit"] or "单位待核"
        quantity = "数量待核" if row["quantity"] is None else f"{row['quantity']:g} {unit}"
        from_date, to_date = row["valid_from"] or "无起日", row["valid_to"] or "无止日"
        lines = [
            f"定位：{scope} · 拟生效日 {check['effective_date']}",
            f"目标行：{row['parent']} → {row['component']} · {quantity} · {from_date} 至 {to_date}",
            f"拟变更：{CHANGE_TYPES[check['change_type']]}"
            + (f" → {check['new_component']}" if check["change_type"] == "replace" else ""),
            f"工程版本来源：{evidence_text(version['evidence'])}",
            f"目标行来源：{evidence_text(row['evidence'])}",
        ]
        if check["path_issue"]:
            lines.append(f"结构核查：{check['path_issue']}")
        for edge in check["path"] or reversed(check["inspected_rows"]):
            lines.append(
                f"路径证据 {edge['row_id']}：{edge['parent']} → {edge['component']}"
                f" · {evidence_text(edge['evidence'])}"
            )
        value = check["quantity_impact"]
        if value:
            lines.append(f"理论差量：{value['status']}")
            for item in value["by_top"]:
                lines.append(f"{item['top_material']} 每单位成品 {item['delta_per_top_unit']:g} {value['unit']}")
            for item in value["pending_paths"]:
                lines.append(f"用量待核：{item['reason']}")
        lines.append(f"版本引用命中：{len(check['version_reference_matches'])} 个")
        for order in check["version_reference_matches"]:
            lines.append(
                f"{reference_label(order)} · {order['review_status']} · 配置 {order['row_application']}"
                f" · 状态 {order['order_status']} · 日期 {order['order_relevant_date'] or '未知'}"
                f" · 已采购 {order['purchased']} · 已到货 {order['received']}"
                f" · {'／'.join(order['review_owners'])}复核 · {evidence_text(order['evidence'])}"
            )
            if order["review_reasons"]:
                lines.append("复核原因：" + "、".join(order["review_reasons"]))
            for application in order["application_records"]:
                lines.append(
                    f"配置证据 {application['row_application']} · {evidence_text(application['evidence'])}"
                )
            if "订单采购证据冲突" in order["review_reasons"]:
                for record in order["purchase_evidence"]:
                    lines.append(
                        f"采购证据：已采购 {record['purchased']} · 已到货 {record['received']}"
                        f" · {evidence_text(record['evidence'])}"
                    )
        for status, records in (("引用待核", check["pending_references"]),
                                ("排除", check["excluded_orders"])):
            for order in records:
                refs = order.get("references") or [order]
                versions = "、".join(
                    dict.fromkeys(item["engineering_version"] or "空白" for item in refs)
                )
                lines.append(
                    f"{status} {reference_label(order)} · 版本 {versions} · {order['reason']}"
                    f" · {evidence_text(order['evidence'])}"
                )
        output.append({"title": f"{check['ecn']} · 工程版本与 BOM 行核查", "lines": lines})
    if len(checks) > 1:
        output.append({
            "title": "工程版本订单汇总",
            "lines": [f"去重后版本引用命中：{len(unique_orders)} 个"]
            + [f"{reference_label(order)} · {', '.join(order['ecns'])}" for order in unique_orders],
        })
    return output


def group(checks):
    unique = {}
    for check in checks:
        for order in check["version_reference_matches"]:
            entry = unique.setdefault(tuple(order["identity"]), {
                **{field: order[field] for field in ORDER_FIELDS}, "ecns": [],
            })
            entry["ecns"].append(check["ecn"])
    orders = list(unique.values())
    return {"checks": checks, "unique_orders": orders, "sections": sections(checks, orders)}
