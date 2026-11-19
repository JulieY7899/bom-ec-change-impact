"""Evidence required by explicit engineering searches before relevance ranking."""
import re
import unicodedata

CODE = r"[a-z0-9]+(?:[-_.][a-z0-9]+)*"
CODE_START = r"(?<![a-z0-9._-])"
CODE_END = r"(?![a-z0-9_-]|\.[a-z0-9])"
VERSION = re.compile(rf"{CODE_START}(?:eng[-_]?)?v[0-9]+(?:\.[0-9]+)*{CODE_END}")
BOM_ROW = re.compile(rf"{CODE_START}(?:v[0-9]+[-_])?l[0-9]+(?:\.[0-9]+)*{CODE_END}")
NAMED_PLANT = re.compile(r"(?:华[东西南北中]|[东西南北中])厂")
PLANT_CODE = re.compile(rf"(?:工厂|厂区)\s*[:：]?\s*({CODE})")

NUMBER = r"(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]+)?"
LIMIT_REQUEST = re.compile(r"阈值|限值|上限|下限|合格(?:标准|线)\s*(?:是|为)?\s*多少")
GRAVITY_UNIT_REQUEST = re.compile(r"(?:多少|几)\s*倍重力加速度")
GRAVITY_LIMIT_REQUEST = re.compile(r"(?:允许|容许|不得超过)\s*(?:多少|几)\s*倍重力加速度")
RESISTANCE_NAME = re.compile(r"绝缘电阻|接地电阻")
# Preserve SI prefix case when checking the value from the cited sentence.
MEGAOHM = r"(?:兆欧(?:姆)?|M\s*(?:Ω|[Oo][Hh][Mm]\b))"
MEGAOHM_REQUEST = re.compile(MEGAOHM)
PART_CODE = re.compile(rf"{CODE_START}(?=[a-z0-9._-]*[0-9])[a-z][a-z0-9]*[-_]{CODE}{CODE_END}")
LIMIT_VALUE = (
    r"(?:阈值|限值|上限|下限|不[得能]?低于|不[得能]?高于|至少|至多|"
    r"(?:合格|验收|判定)(?:标准|线)|不[得能]?超过|大于等于|小于等于|大于|小于|[≤≥]|[<>]=?)\s*(?:为|是|[:：])?\s*"
)
SENTENCE = re.compile(r"[。！？；!?;\n]")
CONTRACT_REQUEST = re.compile(r"合同(?:编号|号)")
CONTRACT_VALUE = re.compile(
    rf"合同(?:编号|号)\s*(?:为|是|[:：#])?\s*(?=[a-z0-9._-]*[0-9]){CODE}{CODE_END}"
)
CANCELLATION = r"(?:取消|撤单|终止)[^。！？；!?;\n]{0,20}(?:赔偿|赔付|补偿|违约金)"
CANCELLATION_REQUEST = re.compile(CANCELLATION + r"[^。！？；!?;\n]{0,8}(?:金额|多少|费用)")
MONEY = rf"(?:[¥￥]\s*{NUMBER}|{NUMBER}\s*(?:元|万(?:元)?|人民币|美元|欧元|cny|rmb|usd|eur))"
CANCELLATION_VALUE = re.compile(
    CANCELLATION + rf"[^。！？；!?;\n\d¥￥]{{0,12}}{MONEY}"
)
IN_TRANSIT = re.compile(r"在途|在运|运输中")
OPEN_RECEIPT = re.compile(r"未到货|未交货|未交付|待到货|尚未收货")
DOUBLE_COUNTING = re.compile(r"重复|去重|防重|重叠|相加|加总|双计")
RECEIPT_RULE = re.compile(r"按|核对|检查|不得|不能|不可|允许|避免|须|仅|只|禁止")
INVOICE_REQUEST = re.compile(r"哪(?:一)?[张份笔](?:实际|采购|供应商)?发票|发票(?:编号|号码|号)|票号")
INVOICE_OFFSET = re.compile(r"抵减|冲抵|冲减|抵扣")
INVOICE_REFERENCE = re.compile(
    rf"(?:发票(?:编号|号码|号)?|票号)\s*(?:为|是|[:：#])?\s*(?=[a-z0-9._-]*[0-9]){CODE}{CODE_END}"
)
OFFSET_AMOUNT = re.compile(
    rf"(?:{INVOICE_OFFSET.pattern})(?:金额|款项|额度)?\s*(?:为|是|[:：])?\s*(?:人民币\s*)?{MONEY}"
    rf"|{MONEY}\s*(?:可以?|用于|可用于|用来|直接|予以|已)(?:{INVOICE_OFFSET.pattern})"
)
OFFSET_BALANCE = re.compile(rf"{IN_TRANSIT.pattern}|{OPEN_RECEIPT.pattern}|采购承诺")
OFFSET_PENDING = re.compile(
    rf"不得|不可|不能|禁止|(?:不|无需)(?:{INVOICE_OFFSET.pattern})"
    r"|拟(?:用|抵|冲)|待(?:补|确认|审核|批准)|尚未确定|草案"
)
APPROVED = re.compile(r"(?:已(?:经|正式)?(?:获批|批准|审批通过|通过审批)|审批(?:已)?通过|正式获批)(?![后前时才])")
APPROVAL_STATUS = re.compile(r"(?:切换)?(?:审批|批准|签核)状态\s*[:：]\s*(?:已批准|已通过|通过|已获批)(?![后前时才])")
SIGNATURE = re.compile(r"签字|签名|签署|会签|签核")
SIGNED = re.compile(
    rf"已(?:经|完成)?(?:{SIGNATURE.pattern})"
    rf"|(?:{SIGNATURE.pattern})(?:人员|人|记录|页)?\s*(?:见|位于|归档于|已归档)"
)
CUTOVER_TIME_REQUEST = re.compile(r"切换(?:的)?(?:时间|日期|时点|窗口)|何时切换|什么时候切换")
DATE = r"(?:20[0-9]{2}[-/.年])?[0-9]{1,2}(?:[-/.][0-9]{1,2}(?![0-9])|月[0-9]{1,2}日)"
CUTOVER_TIME = re.compile(
    rf"切换(?:的)?(?:时间|日期|时点|窗口)\s*(?:为|是|定于|锁定为|(?:已)?批准为|[:：])?\s*{DATE}"
    rf"|{DATE}(?:\s+[0-9]{{1,2}}:[0-9]{{2}})?\s*(?:正式|开始|实施|执行)切换"
)
SIGNATURE_LOCATOR = re.compile(
    rf"第\s*[0-9一二三四五六七八九十]+\s*页"
    rf"|(?:审批单|批准单|签核单|变更单|流程单|审批记录|切换单)\s*(?:编号|号|[:：#])?\s*"
    rf"(?=[a-z0-9._-]*[0-9]){CODE}{CODE_END}"
)
SIGNER_FIELD = re.compile(r"(?:签字人|签名人|签署人|会签人)\s*[:：]\s*(?!待|未|无|空|见|签署|签字|签名)[\w-]{2,}")
APPROVAL_PENDING = re.compile(
    r"草案|未(?:获|经|完成)?(?:批准|审批通过|签字|签名|签署|会签|签核)"
    r"|审批(?:未|不)通过|待(?:审批|批准|签字|签名|签署|会签|签核)"
    r"|(?:签字|签名|签署|会签|签核)(?:待补|未完成)"
    r"|(?:若|如果)[^。！？；\n]{0,12}(?:审批通过|获批|批准)"
    r"|撤销|作废|取消批准|拟(?:定|安排|于|切换)|虚构|审批样例|签字示例|演示记录"
)
ORDER_LINE = r"订单行|订单逐行|订单按行|订单每行|订单的.{0,8}行|(?:销售|客户)订单[^。！？；\n]{0,24}(?:逐行|各行|每行|分行)"
OPEN_ORDER = r"(?:开放|未关闭|未完结)(?:的)?(?:销售)?订单行"
ORDER_REVIEW = r"评审|复核|核查"
VERSION_GAP = r"(?:缺少|缺失|没有|未录入|未维护|没填).{0,6}(?:版本|版次)|(?:版本|版次)(?:引用)?(?:缺失|缺少|未录入|没填)"
STABLE_BOM_ROW = r"(?:稳定|固定)\s*(?:bom\s*)?行号"
ROW_NUMBER = r"(?:bom\s*行号|(?:稳定|固定)行号|bom\s*行标识)"
LINE_CONFIG = r"配置|选配"
PER_LINE = r"逐行|各行|每行|两行|多行|不同(?:的)?订单行|订单行.{0,6}(?:分别|独立)"
SCOPE_CONFLICT = r"(?:适用性|配置|适用.{0,6}排除).{0,8}(?:冲突|矛盾|打架)"
PATH_EFFECTIVITY = r"(?:路径|上层总成|组件行).{0,16}生效(?:日期|日).{0,8}有效"
WINDOW_BOUNDARIES = r"(?:起日|首日|起始日期?).{0,10}(?:止日|末日|截止日期?)"
CHANGE_DATE = r"(?:拟生效|变更生效|切换)(?:日期|日)"
EARLY_DEMAND = rf"需求(?:日期|日)(?:早于{CHANGE_DATE}|先于{CHANGE_DATE}|在{CHANGE_DATE}(?:之)?前)"
STOCK_CATEGORIES = re.compile(r"待检|冻结|寄售")
STOCK_RULE = re.compile(r"并入|计入|纳入|扣除|排除|算入|不算|不(?:可|能)用|方可使用")


def _order_evidence(query):
    """Choose specific order facts for explicit questions, allowing separate facts for compound queries."""
    checks = []
    if re.search(OPEN_ORDER, query) and re.search(r"评审|复核", query):
        checks.append((OPEN_ORDER, ORDER_REVIEW))
    if re.search(VERSION_GAP, query) and re.search(r"补证|补齐|补全|补录", query):
        checks.append((VERSION_GAP, r"补|配置|订单引用|版本记录"))
    if re.search(rf"{ORDER_LINE}|(?:销售|客户)订单", query) or ("客户" in query and re.search(PER_LINE, query)):
        if re.search(SCOPE_CONFLICT, query):
            checks.append((ORDER_LINE, r"适用|纳入", r"排除|剔除", r"冲突|矛盾|同时|不一致"))
        if re.search(STABLE_BOM_ROW, query):
            checks.append((ORDER_LINE, ROW_NUMBER))
        elif re.search(LINE_CONFIG, query):
            if re.search(r"定制|选配", query) and re.search(r"版本|版次", query) and re.search(r"需求日期|交期", query):
                checks.append((ORDER_LINE, LINE_CONFIG, r"版本|版次", r"需求日期|交期"))
            elif re.search(PER_LINE, query):
                checks.append((ORDER_LINE, LINE_CONFIG))
    if re.search(r"引用版本|版本引用", query) and re.search(r"生效日期", query) and "订单" in query:
        checks.append((r"订单", r"版本|版次", r"生效(?:日期|日)"))
    return [tuple(re.compile(term) for term in types) for types in checks]


def _effectivity_evidence(query):
    requested = []
    if re.search(PATH_EFFECTIVITY, query):
        requested.append(PATH_EFFECTIVITY)
    if re.search(WINDOW_BOUNDARIES, query) and "生效" in query:
        requested.append(WINDOW_BOUNDARIES)
    if re.search(EARLY_DEMAND, query):
        requested.append(EARLY_DEMAND)
    return [re.compile(pattern) for pattern in requested]


def _mentions_receipt_balances(text):
    return IN_TRANSIT.search(text) and OPEN_RECEIPT.search(text)


def _has_invoice_offset(sentence):
    return (INVOICE_REFERENCE.search(sentence) and OFFSET_BALANCE.search(sentence)
            and OFFSET_AMOUNT.search(sentence) and not OFFSET_PENDING.search(sentence))


def _has_signed_cutover(text, sentences):
    if APPROVAL_PENDING.search(text):
        return False
    has_approved_date = (any(APPROVED.search(sentence) and CUTOVER_TIME.search(sentence) for sentence in sentences)
                         or (CUTOVER_TIME.search(text) and APPROVAL_STATUS.search(text)))
    return has_approved_date and any(
        SIGNER_FIELD.search(sentence) or (SIGNED.search(sentence) and SIGNATURE_LOCATOR.search(sentence))
        for sentence in sentences
    )


def evidence_requirement(query):
    """Build checks only for scope or evidence explicitly requested in the query."""
    original_query = unicodedata.normalize("NFKC", query)
    query = original_query.casefold()
    versions = set(VERSION.findall(query))
    rows = set(BOM_ROW.findall(query))
    named_plants = set(NAMED_PLANT.findall(query))
    plant_codes = PLANT_CODE.findall(query)
    plants = re.compile(rf"{CODE_START}(?:{'|'.join(map(re.escape, plant_codes))}){CODE_END}") if plant_codes else None
    asks_limit = bool(LIMIT_REQUEST.search(query) or GRAVITY_LIMIT_REQUEST.search(query))
    asks_contract = bool(CONTRACT_REQUEST.search(query))
    asks_compensation = bool(CANCELLATION_REQUEST.search(query))
    asks_double_counting = _mentions_receipt_balances(query) and DOUBLE_COUNTING.search(query)
    asks_invoice_offset = INVOICE_REQUEST.search(query) and INVOICE_OFFSET.search(query) and OFFSET_BALANCE.search(query)
    asks_signed_cutover = APPROVED.search(query) and CUTOVER_TIME_REQUEST.search(query) and SIGNATURE.search(query)
    order_evidence = _order_evidence(query)
    effectivity_evidence = _effectivity_evidence(query)
    stock_categories = set(STOCK_CATEGORIES.findall(query)) if re.search(r"可用(?:库存|量)", query) else set()
    if not (versions or rows or named_plants or plants or asks_limit or asks_contract or asks_compensation
            or asks_double_counting or asks_invoice_offset or asks_signed_cutover or order_evidence
            or effectivity_evidence or stock_categories):
        return None

    resistance = set(RESISTANCE_NAME.findall(query))
    unit = rf"\s*{MEGAOHM}" if MEGAOHM_REQUEST.search(original_query) else ""
    acceleration = None
    if asks_limit and GRAVITY_UNIT_REQUEST.search(query):
        unit = r"\s*(?:倍重力加速度|[gG](?![A-Za-z]))"
        # Tie g to acceleration, rather than a weight or some other measurement.
        acceleration = "振动" in query
    limit_parts = set(PART_CODE.findall(query)) if asks_limit else set()
    limit = re.compile(rf"{LIMIT_VALUE}[+−-]?{NUMBER}{unit}") if asks_limit else None

    def scope_matches(text):
        if versions and versions.isdisjoint(VERSION.findall(text)):
            return False
        if rows and rows.isdisjoint(BOM_ROW.findall(text)):
            return False
        if named_plants and named_plants.isdisjoint(NAMED_PLANT.findall(text)):
            return False
        return not plants or bool(plants.search(text))

    def qualifies(text):
        original = unicodedata.normalize("NFKC", text)
        sentences = SENTENCE.split(original)
        normalized_sentences = [sentence.casefold() for sentence in sentences]
        if not any(scope_matches(sentence) for sentence in normalized_sentences):
            return False
        if limit and not any(
            limit.search(sentence)
            and (not resistance or resistance.intersection(RESISTANCE_NAME.findall(sentence)))
            and (acceleration is None or ("加速度" in sentence and (not acceleration or "振动" in sentence)))
            and limit_parts.issubset(PART_CODE.findall(sentence.casefold()))
            for sentence in sentences
        ):
            return False
        text = original.casefold()
        if asks_contract and not CONTRACT_VALUE.search(text):
            return False
        if asks_compensation and not CANCELLATION_VALUE.search(text):
            return False
        if asks_double_counting and not any(
            _mentions_receipt_balances(sentence) and DOUBLE_COUNTING.search(sentence) and RECEIPT_RULE.search(sentence)
            for sentence in normalized_sentences
        ):
            return False
        if asks_invoice_offset and not any(_has_invoice_offset(sentence) for sentence in normalized_sentences):
            return False
        if asks_signed_cutover and not _has_signed_cutover(text, normalized_sentences):
            return False
        if (order_evidence or effectivity_evidence) and not (
            any(all(term.search(text) for term in types) for types in order_evidence)
            or any(pattern.search(text) for pattern in effectivity_evidence)
        ):
            return False
        if stock_categories and not any(
            any(category in sentence for category in stock_categories) and STOCK_RULE.search(sentence)
            for sentence in normalized_sentences
        ):
            return False
        return True

    return qualifies
