"""Qualify sales order questions before proposing an impact review."""
from __future__ import annotations

import re

from .workflow import ID_WORD, PART

ORDER_DECISION = re.compile(
    r"(?:能否|是否可以|能不能|可不可以|还能不能|还能|可以|可否|能|是否能|能否继续)\s*(?:继续|正常|直接|先|再)?\s*"
    r"(?:下单|接单|下订单|接订单|开单|建单|录单|[接下](?:这|该)(?:笔|个)?(?:订单|单)|[接下](?=吗|么|[?？]|$))|"
    r"(?:下单|接单|开单|建单|录单)\s*(?:吗|行不行|可以吗|有问题吗|有没有问题|可不可以|行吗)"
)
BOM_CONTEXT = re.compile(r"BOM|工程变更|换料|组件变更|版本变更|版本切换|成品.{0,8}(?:变更|改了)", re.I)
NO_BOM_CHANGE = re.compile(r"(?:没有|并无|无|不存在)\s*(?:BOM|工程(?:版本)?)\s*(?:变更|变化)|与\s*BOM\s*无关", re.I)
COMMERCIAL = re.compile(r"信用|付款|价格|报价|账期|合同|授信")
ORDER_ID = re.compile(rf"(?:销售订单(?:号)?|销售单(?:号)?|订单号|单号)\s*[:=#]?\s*{PART}|(?<![\w-])(SO-[A-Za-z0-9-]+)", re.I)
PRODUCT = re.compile(
    rf"(?:下单成品|成品(?:的)?(?:物料号|料号|编号|物料)?|下单(?:的)?(?:物料号|料号)|产品料号)"
    rf"\s*(?:是|为|改为|改成|应为|换成|[:=])?\s*{PART}", re.I
)
PRODUCT_CHOICE = re.compile(rf"\s*(?:或(?:者)?|还是|和|与|及|、)\s*{PART}")
BOM_KIND = {
    "custom": r"(?<![非不])(?:客户定制|客户专用|客制|订单专用|按单定制|定制)(?:的)?\s*(?:BOM)?",
    "standard": r"(?:非定制|不定制|非客制|非客户定制|(?:通用|标准|常规)(?:的)?\s*(?:BOM|成品)|标准\s*BOM)",
}
UNCERTAIN = r"(?:不确定|不清楚|不知道|还没确认|待确认|未知)"
GENERIC = {"BOM", "EBOM", "ORDER", "SO", "SAP", "ERP", "PLM"}
OUTSIDE_REPLY = "这个问题请由销售订单流程或对应负责人核实。BOM 助手能检查工程变更对物料、配置和关联订单的影响。"
ORDER_REPLY = "我可以核查 BOM 变更及关联订单的影响。下单决定由销售订单流程确认。"
PROMPTS = {
    "bom_context": "这次下单疑问是因为 BOM 或工程版本发生了变更吗？可以补充具体原因。",
    "sales_product": "准备下单的成品物料号是什么？请发成品料号，例如 FG-810。",
    "bom_kind": "这笔订单用标准 BOM，还是客户定制 BOM？如果尚未确认，可以回复“不确定”。",
    "engineering_version": "请核对该销售订单行，并提供它实际引用的工程版本。可从订单 BOM 或版本引用导出核对，有工厂范围也可以一起提供。",
    "target_bom_row": "该版本中受变更影响的是哪条 BOM 行？请提供目标 BOM 行号。",
}


def update(intake, message, role, expected):
    """Remember order context inside the conversation only."""
    sales_context = role == "sales" or re.search(r"销售订单|销售单号|\bSO-[A-Za-z0-9]", message, re.I)
    if not intake and not (sales_context and ORDER_DECISION.search(message)):
        return
    if not intake:
        intake.update(intent="sales_order_decision", bom_related=None)
    if NO_BOM_CHANGE.search(message) or (expected == "bom_context" and message in {"不是", "否", "没有"}):
        intake["bom_related"] = False
    elif BOM_CONTEXT.search(message):
        intake["bom_related"] = True
    elif COMMERCIAL.search(message):
        intake["bom_related"] = False
    if match := ORDER_ID.search(message):
        order_id = match[1] or match[2]
        if order_id.upper() not in GENERIC:
            if intake.get("order_id") and intake["order_id"].casefold() != order_id.casefold():
                intake.pop("product", None)
                intake.pop("bom_kind", None)
            intake["order_id"] = order_id
    products = set()
    for match in PRODUCT.finditer(message):
        products.add(match[1])
        alternate = PRODUCT_CHOICE.match(message[match.end():])
        if alternate:
            products.add(alternate[1])
    if expected == "sales_product" and re.fullmatch(ID_WORD, message):
        products.add(message)
    products = {product for product in products if product.upper() not in GENERIC
                and not re.fullmatch(r"20\d{2}-\d{1,2}-\d{1,2}", product)}
    if len(products) == 1:
        intake["product"] = products.pop()
    elif products:
        intake.pop("product", None)

    if kind := classify_bom(message, expected):
        intake["bom_kind"] = kind
        if kind != "unknown" and intake["bom_related"] is None:
            intake["bom_related"] = True


def classify_bom(message, expected):
    asking = re.search(r"(?:是否|是不是|算不算|要不要)\s*(?:用|使用|属于|是)?\s*(?:客户)?(?:定制|标准|通用)", message)
    if (asking
            or (re.search(UNCERTAIN, message) and re.search(r"定制|标准|通用|BOM类型", message, re.I))
            or (expected == "bom_kind" and re.fullmatch(rf"{UNCERTAIN}|是|否|对|不是|没有", message))):
        return "unknown"
    text = re.sub(r"(?:不是|非|并非)\s*(?:客户)?定制(?:的)?\s*(?:BOM)?", "非定制", message, flags=re.I)
    text = re.sub(r"不是\s*(?:标准|通用|常规)(?:的)?\s*(?:BOM)?", "", text, flags=re.I)
    kinds = [kind for kind, pattern in BOM_KIND.items() if re.search(pattern, text, re.I)]
    if kinds:
        return kinds[0] if len(kinds) == 1 else "unknown"
    if re.fullmatch(r"(?:普通|标准|通用|常规)", message):
        return "standard"
    return ""


def pending(intake, draft):
    if intake["bom_related"] is None:
        return ["bom_context"]
    if intake["bom_related"] is False:
        return []
    required = ["sales_product", "bom_kind"]
    values = {"sales_product": intake.get("product"), "bom_kind": intake.get("bom_kind") in ("standard", "custom")}
    if intake.get("bom_kind") == "custom":
        required.extend(("engineering_version", "target_bom_row"))
        values.update({field: draft.get(field) for field in required[2:]})
    return [field for field in required if not values.get(field)]
