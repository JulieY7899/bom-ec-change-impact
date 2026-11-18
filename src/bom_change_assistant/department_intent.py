"""Local extraction of departmental questions and evidence scope."""
from __future__ import annotations

import re
from datetime import date
from math import isfinite

# These topics describe questions. The accompanying evidence still needs verification.
TOPICS = {
    "engineering": (
        ("组件差异与验证", r"差异|验证|新料是否可用|兼容|互换", "新旧件规格差异、验证记录和适用条件"),
        ("版本与适用范围", r"版本|BOM\s*行|配置|工厂适用|哪些产品|适用范围", "成品与工厂、实际版本、BOM 行和配置限制"),
        ("切换与临时替代", r"临时替|切换|生效|替代条件", "切换生效点、适用工厂和临时替代期限"),
    ),
    "procurement": (
        ("未结采购与处置", r"采购订单|采购单|未结|未交|撤单|可撤|可改|撤销|继续收货|采购责任", "采购订单行、未交数量、改单或撤单条件和旧料采购责任"),
        ("供应商投产与发运", r"投产|发货|发运|出货|已发", "供应商已投产量、已发运量及对应采购订单行"),
        ("到货与承诺", r"承诺|到货|啥时到|何时到|交货|断供|缺货|缺料|采购不到|到不了货|无法及时采购", "承诺数量、承诺到货日、实际到货凭证和数据日期"),
    ),
    "planning": (
        ("库存与需求平衡", r"库存|在途|采购量|需求|未交|重复|重叠", "按物料、工厂、库位准备可用库存、在途、未到货和切换前后需求，按采购订单行核对重叠"),
        ("齐套与切换", r"齐套|缺口|剩余|切换|备料", "同口径的旧料预计剩余、新料缺口、齐套时间和切换方案"),
    ),
    "warehouse": (
        ("库存状态与可用量", r"库存|在途|待检|物理|实物|可用|批次|存放", "物料、工厂、库位、批次、实物库存及待检或隔离状态"),
        ("隔离退回与转用", r"隔离|退回|转用|退库|退料", "隔离范围、退回凭证、转用条件和可计入可用量的时间"),
    ),
    "manufacturing": (
        ("工单状态与实际用料", r"工单|领料|在制|未开工|完工未交|工序|实际用料|报工", "工单状态、实际领用料、工序进度、未交付范围"),
        ("生产处置与执行点", r"继续生产|停工|暂停|改料|隔离|返工|执行点|执行范围", "逐单确认继续生产、改料、隔离、返工或暂停的执行范围和生效点"),
    ),
    "quality": (
        ("新料验证与适用性", r"新料|新件|验证|替代|适用", "验证记录、替代适用范围和验证有效性"),
        ("库存与在制质量风险", r"库存|在制|待检|风险|批次", "库存与在制批次、工单、缺陷和风险范围"),
        ("复验返工与放行", r"放行|隔离|复验|返工|报废|继续使用", "质量判定、隔离范围、复验或返工记录和放行条件"),
    ),
    "sales": (
        ("订单配置与版本", r"订单|配置|版本|定制|替代配置", "订单行、成品、标准或定制配置、实际引用版本"),
        ("交付与客户沟通", r"交期|交付|需求日|客户沟通|承诺", "需求日期、交付承诺、替代配置和需沟通的客户订单行"),
    ),
    "service": (
        ("已交付产品与备件", r"已交付|存量|维修|备件|序列号|设备", "已交付产品型号、序列号、现装件号和维修备件清单"),
        ("兼容性与旧件保留", r"兼容|使用新|改用|保留|旧件|旧料", "存量设备适用性、售后替代记录和旧件保留范围"),
    ),
}
TOPICS["rd"] = TOPICS["engineering"]
OWNERS = {
    "engineering": "工程／研发确认新料适用范围及切换条件。",
    "rd": "工程／研发确认新料适用范围及切换条件。",
    "procurement": "采购与工程、计划核对后，由采购确认供应承诺及订单处置。",
    "planning": "计划核对供需、齐套日期与建议切换方式。",
    "warehouse": "仓储核实库存与批次，隔离和放行条件按质量流程确认。",
    "manufacturing": "制造与工程逐单确认现场处置、执行范围与生效点。",
    "quality": "质量确认继续使用、隔离、复验、返工或报废的条件和范围。",
    "sales": "销售与计划核实交付承诺，按销售流程确认下单及客户沟通。",
    "service": "售后与工程确认存量设备兼容性及旧件保留范围。",
}
LABELS = {
    "material": "关注物料", "product": "成品", "plant": "工厂", "storage_location": "库位",
    "lot": "批次", "purchase_order": "采购单", "sales_order": "销售订单", "order_item": "订单行",
    "production_order": "工单", "operation": "工序", "serial": "设备序列号",
    "engineering_version": "工程版本", "target_bom_row": "BOM 行",
    "old_component": "原组件", "new_component": "新组件", "effective_date": "拟生效日",
    "requested_date": "需求日期", "arrival_date": "到货日", "committed_quantity": "承诺数量",
    "supplier_produced": "供应商已投产", "supplier_shipped": "供应商已发运",
    "validation_state": "验证状态", "stock_state": "库存状态", "work_state": "工单状态",
    "bom_kind": "BOM 类型", "old_on_hand": "旧料可用库存", "old_in_transit": "旧料在途",
    "old_open_po": "旧料未到货采购量", "old_consumable_demand": "切换前可消耗需求",
    "new_on_hand": "新料可用库存", "new_in_transit": "新料在途",
    "new_open_po": "新料未到货采购量", "new_required_demand": "切换后目标需求",
    "physical_stock": "实物库存", "inspection_stock": "待检数量", "isolated_stock": "隔离数量",
}
IDENTIFIERS = {
    "material": r"(?:维修备件|备件(?:料号)?|实际用料|物料(?:号)?|件号)",
    "product": r"(?:下单成品|成品(?:物料号|料号|编号)?|产品料号)",
    "plant": r"(?:采购|计划|收货|工程版本)?工厂(?:代码)?",
    "storage_location": r"(?:存放|采购)?库位(?:代码)?",
    "lot": r"(?:物料)?批次(?:号)?",
    "purchase_order": r"(?:未结)?采购(?:订单|单)(?:号)?|PO(?:号)?",
    "sales_order": r"销售(?:订单|单)(?:号)?|订单号",
    "order_item": r"(?<!BOM\s)(?<!BOM)(?:订单)?行(?:号)?",
    "production_order": r"(?:生产)?工单(?:号)?|生产订单(?:号)?",
    "operation": r"工序(?:号)?",
    "serial": r"(?:设备|产品)?序列号|存量设备|(?:已交付)?设备(?:编号|号)",
    "old_component": r"(?:旧|原)(?:件|料号|物料|组件|料)",
    "new_component": r"新(?:件|料号|物料|组件|料)",
}
NUMBERS = {
    "supplier_produced": r"(?:供应商)?已投产(?:数量|量)?",
    "supplier_shipped": r"(?:供应商)?已(?:发货|发运|出货)(?:数量|量)?",
    "committed_quantity": r"(?:供应商)?承诺(?:数量|量)",
    "old_on_hand": r"旧料\s*可用库存(?:量)?",
    "old_in_transit": r"旧料\s*在途(?:量|数量)?",
    "old_open_po": r"旧料\s*(?:未到货采购|未交采购)(?:量|数量)?",
    "old_consumable_demand": r"切换前(?:可消耗)?需求(?:量)?",
    "new_on_hand": r"新料\s*可用库存(?:量)?",
    "new_in_transit": r"新料\s*在途(?:量|数量)?",
    "new_open_po": r"新料\s*(?:未到货采购|未交采购)(?:量|数量)?",
    "new_required_demand": r"切换后(?:新料)?需求(?:量)?",
    "physical_stock": r"实物库存(?:量)?",
    "inspection_stock": r"待检(?:量|数量)",
    "isolated_stock": r"隔离(?:量|数量)",
}
DATES = {
    "arrival_date": r"(?:承诺|计划|预计|实际)?(?:到货|交货)(?:日(?:期)?)?",
    "requested_date": r"(?:需求日期|需求日|要求交付日)",
}
DATE = re.compile(r"^(20\d{2})\s*[-年/]\s*(\d{1,2})\s*[-月/]\s*(\d{1,2})(?!\d)")
ID = r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}"
PART = rf"(?<![A-Za-z0-9_./-])({ID})(?![A-Za-z0-9_./-])"
VALUE = r"\s*(?:改为|改成|更正为|应为|是|为|[:=#])?\s*"
GENERIC = {"BOM", "EBOM", "ERP", "SAP", "PLM", "MES", "WMS", "PO", "SO", "QA", "QC"}
ALTERNATE = re.compile(rf"\s*(?:或(?:者)?|、|和|还是|与)\s*{PART}")
STATES = {
    "stock_state": ("待检", "隔离", "在途", "已放行", "冻结"),
    "work_state": ("已释放未开工", "已释放", "未开工", "已领料", "在制", "完工未交付"),
}


def _ids(message, label):
    values = set()
    for match in re.finditer(rf"(?:{label}){VALUE}{PART}", message, re.I):
        values.add(match[1])
        if another := ALTERNATE.match(message[match.end():]):
            values.add(another[1])
    return {value for value in values if value.upper() not in GENERIC and not DATE.fullmatch(value)}


def _order_references(message, updates):
    for field in ("purchase_order", "sales_order"):
        label = IDENTIFIERS[field]
        match = re.search(rf"(?:{label}){VALUE}({ID})\s*(?:[/／]\s*|(?:订单)?行(?:号)?{VALUE})({ID})(?![A-Za-z0-9_./-])",
                          message, re.I)
        if match and match[1].upper() not in GENERIC:
            updates[field], updates["order_item"] = match[1], match[2]
    if "order_item" not in updates:
        values = _ids(message, r"订单行(?:号)?")
        if values:
            updates["order_item"] = next(iter(values)) if len(values) == 1 else ""


def update_scope(scope, message, expected):
    updates = {}
    for field, label in IDENTIFIERS.items():
        if field == "order_item":
            continue
        values = _ids(message, label)
        if field == "plant":
            values.update(re.findall(rf"{PART}\s*工厂(?=\s*[,，、;；。]|\s*$)", message))
        if values:
            updates[field] = next(iter(values)) if len(values) == 1 else ""
    _order_references(message, updates)
    for field, prefix in (("purchase_order", "PO"), ("sales_order", "SO"), ("production_order", "MO")):
        if field not in updates:
            candidates = set(re.findall(rf"(?<![A-Za-z0-9_.-]){prefix}-[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*", message, re.I))
            if candidates:
                updates[field] = candidates.pop() if len(candidates) == 1 else ""
    if expected in IDENTIFIERS and re.fullmatch(PART, message) and not DATE.fullmatch(message):
        if message.upper() not in GENERIC:
            updates.setdefault(expected, message)
    if any(field in updates and field in scope and updates[field] != scope[field]
           for field in ("purchase_order", "sales_order")):
        scope.pop("order_item", None)
    for key, label in NUMBERS.items():
        if match := re.search(rf"{label}{VALUE}(\d+(?:\.\d+)?)\s*(件|套|个|箱|千克|公斤|吨|米|EA|PCS)?", message, re.I):
            number, unit = match.groups()
            updates[key] = " ".join(filter(None, (number, unit))) if isfinite(float(number)) else ""
    for key, label in DATES.items():
        label_match = re.search(rf"{label}{VALUE}", message)
        if label_match and (match := DATE.match(message[label_match.end():])):
            try:
                updates[key] = date(*map(int, match.groups())).isoformat()
            except ValueError:
                pass
    if match := re.search(r"验证(?:状态)?\s*(?:为|是|[:=])?\s*(未通过|已通过|通过|待验证|未验证|复验中|未完成)", message):
        updates["validation_state"] = match[1]
    for field, states in STATES.items():
        found = [match[0] for match in re.finditer("|".join(states), message)
                 if not re.search(r"(?:是否|要不要|需不需要|需要|建议|如何|能否|尚未|未)"
                                  r"\s*(?:应|应当|要|需|仍需)?\s*$", message[:match.start()])]
        if found:
            updates[field] = list(dict.fromkeys(found))
    for key, value in updates.items():
        if value == "":
            scope.pop(key, None)
        else:
            scope[key] = value


def summarize(role, messages, scope, draft, intake):
    if role not in TOPICS:
        return None
    topics = [(name, evidence) for name, pattern, evidence in TOPICS[role]
              if any(re.search(pattern, text, re.I) for text in messages)]
    if not topics:
        return None
    resolved = {**scope, **{field: draft[field] for field in LABELS if field in draft}}
    if role not in {"warehouse", "quality"}:
        resolved.pop("stock_state", None)
    if role not in {"manufacturing", "quality"}:
        resolved.pop("work_state", None)
    if intake:
        resolved.update({key: intake[key] for key in ("product", "bom_kind") if key in intake})
        if intake.get("order_id"):
            resolved["sales_order"] = intake["order_id"]
    bom_kind = {"standard": "标准 BOM", "custom": "客户定制 BOM", "unknown": "待确认"}
    rows = [{"label": LABELS[key], "value": bom_kind.get(value, value) if key == "bom_kind" else value}
            for key, value in resolved.items()]
    quantities = {}
    for key in resolved.keys() & NUMBERS.keys():
        number, _, unit = resolved[key].partition(" ")
        quantities[key] = {"value": float(number), "unit": unit or None}
    return {
        "role": role, "quantities": quantities, "topics": [name for name, _ in topics], "scope": resolved, "rows": rows,
        "evidence": [evidence for _, evidence in topics], "decision_owner": OWNERS[role],
        "status": "impact_review" if draft.get("change_type") else "evidence_review",
    }


def next_scope(intent):
    if not intent or intent["status"] != "evidence_review":
        return None
    role, scope = intent["role"], intent["scope"]
    if role == "warehouse":
        needed = ("material", "plant", "storage_location", "lot")
    elif role == "planning" and not scope.get("purchase_order"):
        needed = ("plant", "storage_location")
    elif role == "quality":
        needed = () if any(scope.get(key) for key in ("lot", "production_order", "new_component")) else ("material",)
    elif role == "service":
        needed = () if scope.get("product") else ("serial",)
    elif role == "manufacturing":
        needed = ("production_order",)
    elif role == "procurement" and "未结采购与处置" in intent["topics"]:
        needed = ("purchase_order", "order_item")
    elif role == "sales" and not scope.get("sales_order"):
        needed = ("sales_order",)
    else:
        needed = ()
    return next((key for key in needed if not scope.get(key)), None)
