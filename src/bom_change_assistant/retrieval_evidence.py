"""Evidence required by explicit engineering searches before relevance ranking."""
import re
import unicodedata
from datetime import date

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
CONTRACT_REQUEST = re.compile(r"合同(?:编号|号|条款(?:编号|号)?)")
CONTRACT_VALUE = re.compile(
    rf"合同(?:编号|号|条款(?:编号|号)?)\s*(?:为|是|[:：#])?\s*(?=[a-z0-9._-]*[0-9]){CODE}{CODE_END}"
)
CONTRACT_CLAUSE = re.compile(r"合同\s*第\s*[0-9一二三四五六七八九十]+(?:[.．][0-9]+)*\s*条")
CANCELLATION = r"(?:取消|撤销|撤单|终止|解除)[^。！？；!?;\n]{0,20}(?:赔偿|赔付|补偿|违约金)"
CANCELLATION_REQUEST = re.compile(
    CANCELLATION + r"[^。！？；!?;\n]{0,16}(?:金额|多少|费用)"
    r"|(?:取消|撤销|撤单|终止|解除).{0,20}(?:多少|需付).{0,6}(?:赔偿|赔付|补偿|违约金)"
)
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
DATE = r"(?:20[0-9]{2}\s*[-/.年]\s*)?[0-9]{1,2}\s*(?:[-/.]\s*[0-9]{1,2}(?![0-9])|月\s*[0-9]{1,2}\s*日)"
CUTOVER_TIME = re.compile(
    rf"切换(?:的|执行)?(?:时间|日期|时点|时刻|窗口)\s*(?:为|是|定于|定为|锁定为|(?:已)?批准为|[:：])?\s*{DATE}"
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
# Explicit formal-record questions require evidence of the decision, its scope and locator.
ISSUED = re.compile(r"已(?:经|正式)?(?:签发|签批|发放)|(?:签发|签批|签署)了?|正式(?:批准|发布)|获准")
RECORD_PENDING = re.compile(r"草案|拟(?:定|签|于|发)|待(?:审批|批准|签|发|补|确认)|(?:尚)?未(?:获|经|完成)?(?:审批|批准|签|发)"
                            r"|(?:已)?(?:作废|失效|过期|撤销)|不(?:予|得|可)(?:签|发|放行)")
OBJECT_SCOPE = re.compile(r"(?:批准|授权|放行)(?:的)?(?:对象|范围)(?:为|是|包括|[:：])?\s*([^，,。]+)")
CUTOVER_DECISION_REQUEST = re.compile(r"切换.{0,10}(?:批准|审批)|(?:批准|审批).{0,24}(?:正式)?切换")
DECISION_TRAILING = re.compile(r"[，,]\s*(?:对照|比较|另行|另一个|仅供|附注)")
CUTOVER_DATE_REQUEST = re.compile(r"日期|时间|时点|窗口|哪[天日]|几点|何时|什么时候")
CUTOVER_ISSUE = re.compile(r"切换(?:审批单|批准单|决定|签批).{0,24}已(?:正式)?签发")
DECISION_REQUEST = re.compile(
    r"换型.{0,10}(?:决定|决议|审批)|(?:决定|决议|同意).{0,12}换型"
    r"|(?:联审|评审).{0,5}(?:结论|通过)|(?:同意|批准|准予).{0,24}(?:更换|换产|投用|开线)"
    r"|(?:现行|仍有效|生效的).{0,10}批件|请提供.{0,12}批件"
)
DECISION_ACTION = re.compile(r"换型|切换|换产|投用|更换|启用")
DECISION_HEADER = re.compile(r"^(?:放行|换型|切换|投用)?(?:批件|审批单|决议|批准单|审批记录)")
DECISION_FIELD = re.compile(r"^(?:结论|决定|审批状态|审批结论|决议)\s*[:：]\s*(?:同意|通过|批准|准予)")
DECISION_APPROVED = re.compile(r"联审通过|签发|现行.{0,8}(?:批件|决议)|(?:同意|批准|准予)")
DECISION_SCOPE = re.compile(r"(?:适用|批准|授权)(?:的)?(?:对象|范围|线体|产线|批次|场地)(?:为|限|是|[:：])")
DECISION_SITE = re.compile(r"(?:[东西南北中]|[东南][东西南北])(?:场|站|区|厂)")
RECORD_SCOPE_BREAK = re.compile(r"[，,]\s*(?:另(?:一|有|案)|原(?:批件|决议)|对照|对比|仅供|(?:[东西南北中](?:场|站|区|厂))同型)")
DECISION_NONAUTHORITATIVE = re.compile(
    r"仅.{0,12}(?:对比|对照|试装|台架|实验室)|留作|维持|未列|不适用|暂未产生|撤回|已取消|废止"
)
PRODUCTION = re.compile(r"批量(?:生产|换产|投产)|量产|正式投产")
SIGNED_APPENDIX_QUERY = re.compile(r"签发.{0,16}(?:附页|附件)|(?:附页|附件).{0,16}签发")
SIGNATURE_DETAIL_QUERY = re.compile(r"(?:会签|签署|签字).{0,4}(?:页号|页码|岗位)")
SIGNATURE_ROLE = re.compile(r"(?:会签|签署|签字)(?:岗位|人)\s*[:：为]\s*(?!待|未|无|空)[^，,。]+")
SIGNATURE_PAGE = re.compile(r"(?:会签|签署|签字).{0,8}(?:第\s*[0-9一二三四五六七八九十]+\s*页|页(?:码|号)\s*[:：为]\s*[0-9]+)")
RECORD_TYPES = (
    (re.compile(r"(?:新|有效的?).{0,8}合格证.{0,8}(?:编号|号)"), r"合格证(?:书)?"),
    (re.compile(r"客户.{0,8}(?:发放|放行|交付).{0,8}(?:单号|编号|签批|凭证)"), r"客户.{0,8}(?:发放|放行|交付)(?:单|凭证|签批)"),
    (re.compile(r"(?:量产.{0,8}(?:放行|审批|签发)|(?:批准|放行)量产).{0,12}(?:单号|编号|签发单)"), r"量产(?:放行|批准|签发)(?:单|记录|凭证)"),
)
PROCEDURE_TEXT = re.compile(r"规程|作业标准|作业指导书|规范")
PROCEDURE_REQUEST = re.compile(
    rf"(?:通用|正式|双签).{{0,12}}(?:{PROCEDURE_TEXT.pattern})"
    rf"|(?:{PROCEDURE_TEXT.pattern})(?:的)?(?:正式)?正文"
)
PROCEDURE_DIRECTIVE = re.compile(r"要求|规定|必须|应当|不得|须|方可|才可|禁止")
PROCEDURE_UNCONTROLLED = re.compile(
    r"拟稿|草案|征求意见|未(?:受控|正式)?发布|未签发现行规程|已废止"
    r"|只限本次|仅限(?:该|此|本)(?:次|批|工单)|一次性(?:偏差|作业规程|许可|授权|处置|指令)"
    r"|单次处置|不得推广|临时(?:处置|通知)"
    r"|仅供展示|(?:目录|索引).{0,24}(?:规程|条文|题名)|(?:未附|缺少).{0,8}正文|正文未附"
)
PROCEDURE_TOPICS = (
    r"清场", r"双签|双人签署", r"软(?:质)?护套", r"双层(?:式)?托盘", r"跨班",
    r"封样桶", r"量块", r"复机", r"首件", r"巡检", r"入库",
)
LEAKAGE_RATE = r"(?:最大(?:允许)?)?泄漏率"
LEAKAGE_BOUND = re.compile(
    rf"(?:最大(?:允许)?泄漏率|泄漏率(?:的)?(?:上限|限值|不得超过|不大于|至多|[≤<]))"
    rf"\s*(?:为|是|[:：])?\s*{NUMBER}"
)
# Use metric-specific units and comparators so observed values and opposite limits
# cannot satisfy an explicit request for a formal bound.
MEASURE_UNITS = (
    (r"压力保持率|保压保持率", r"%|百分比"),
    (r"脱开力|拔出力|拉脱力", r"n|牛顿?"),
    (r"同轴度", r"mm|毫米"),
    (r"响应(?:时间|时长)?", r"ms|毫秒|s|秒"),
    (r"(?:光源)?亮度", r"%|cd/m²|cd/m2"),
)
LOWER_LIMIT = r"下限|最低(?:允许)?|最小(?:允许)?|不(?:得|能)?低于|至少|不小于|≥|>="
UPPER_LIMIT = r"上限|最高(?:允许)?|最大(?:允许)?|最多(?:允许)?|不(?:得|能)?(?:超过|高于)|至多|不大于|≤|<="
NEUTRAL_LIMIT = r"限值|公差|合格界限|合格线|验收标准"
LIMIT_AUTHORITY = re.compile(r"已(?:发布|签发|生效)|现行版|检验规范|(?:验收|放行)(?:条件|标准)|合格界限|条款规定")
LIMIT_NOT_EFFECTIVE = re.compile(r"已停用|征求意见|待(?:评审|审定)|未(?:批准|生效|设|规定)|仅供|不(?:适用|作为)|草案")
LIMIT_OBSERVATION = re.compile(r"实测|记录值|实测均值|原始读数|量程|控制目标|调试目标|试设")
LIMIT_CONDITIONS = (
    r"热老化(?:后)?|老化后", r"暖机(?:后)?", r"冷启动", r"常温|室温", r"低温",
    r"干态", r"湿态",
)
LIMIT_STATISTICS = (r"平均(?:值)?|均值", r"单次|每次|各次")
CELSIUS = re.compile(r"(?<![0-9.])[+−-]?[0-9]+(?:\.[0-9]+)?\s*°\s*c")
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

FULL_DATE = r"20[0-9]{2}\s*(?:年|[-/.])\s*[0-9]{1,2}\s*(?:月|[-/.])\s*[0-9]{1,2}(?:\s*日)?(?![0-9])"
DATED_WINDOW = re.compile(rf"({FULL_DATE})\s*(?:至|到|~|—|–|－)\s*({FULL_DATE})")
EFFECTIVE_START = re.compile(rf"(?:从|自)\s*({FULL_DATE})\s*(?:起|开始)?\s*(?:有效|生效)")
EFFECTIVE_END = re.compile(rf"(?:至|到|截止于?|有效期至)\s*({FULL_DATE})(?:\s*(?:截止|结束|为止))?")
EFFECTIVE_DAY = re.compile(rf"({FULL_DATE})\s*(?:当天|当日)\s*(?:仍)?有效")
DATED_EVIDENCE_REQUEST = re.compile(r"(?:当天|当日)(?:仍)?有效|仍(?:在)?有效")
ZERO = r"(?<![0-9.])(?:0(?:\.0+)?(?![0-9.])|零)"
RECEIPT_ZERO_REQUEST = re.compile(rf"(?:{OPEN_RECEIPT.pattern}).{{0,18}}{ZERO}\s*(?:件|个|数量)")
RECEIPT_ZERO_VALUE = re.compile(
    rf"(?:{OPEN_RECEIPT.pattern})(?:的)?(?:数量|余量|余额|结余)?\s*"
    rf"(?:经核实|已核实|已确认|已清点|确认)?\s*(?:为|是|[:：=])?\s*{ZERO}\s*(?:件|个|ea\b)"
)
UNCONFIRMED_BALANCE = re.compile(r"未经|尚未核|未匹配|暂挂|待核|待确认|未知|拟|估算|示例|假定|另(?:案|一)")
RECEIPT_SUPPLEMENT = re.compile(r"采购.{0,20}(?:单据|凭证).{0,12}(?:补核|补查|补数|补证|补齐)"
                                r"|采购.{0,14}(?:补核|补查|补数|补证|补齐).{0,12}(?:单据|凭证)")


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
    if "外部修订" in query:
        checks.append((r"销售(?:订单|单)", r"外部修订", r"映射|对应", r"版本|订单\s*bom"))
    if "装配位置" in query and re.search(r"多个|重复|相同", query):
        checks.append((r"装配.{0,5}位置|装配位", r"稳定.{0,8}(?:行键|标识)|路径键"))
    if all(term in query for term in ("工单", "实际", "工程")) and re.search(r"对回|映射|对应", query):
        checks.append((r"工单", r"实际使用|实际采用", r"映射|对应", r"(?:组件行|用料行).{0,16}工程"))
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


def _decision_requirements(query, references):
    checks = []
    if SIGNED_APPENDIX_QUERY.search(query):
        checks.append(lambda sentences: any(
            references.issubset(PART_CODE.findall(sentence)) and not RECORD_PENDING.search(sentence)
            and not DECISION_NONAUTHORITATIVE.search(sentence)
            and (re.search(r"签发(?!人|栏|岗位|状态|记录)", sentence)
                 and re.search(r"正本|批件|审批单", sentence)
                 or re.search(r"适用.{0,5}(?:附页|附件)|(?:附页|附件).{0,12}(?:线体|装配线|产线)", sentence)
                 and CUTOVER_TIME.search(sentence))
            for sentence in sentences
        ))
    if SIGNATURE_DETAIL_QUERY.search(query):
        asks_page = re.search(r"页号|页码|第几页", query)
        asks_role = "岗位" in query
        checks.append(lambda sentences: (
            references.issubset(PART_CODE.findall(" ".join(sentences)))
            and (not asks_page or any(SIGNATURE_PAGE.search(s) and not RECORD_PENDING.search(s) for s in sentences))
            and (not asks_role or any(SIGNATURE_ROLE.search(s) and not RECORD_PENDING.search(s) for s in sentences))
        ))
    if DECISION_REQUEST.search(query) and (references or DECISION_SITE.search(query)):
        sites = set(DECISION_SITE.findall(query))
        mass_production = PRODUCTION.search(query)
        needs_time = re.search(r"几点|何时|开线时刻|哪天|时刻|时间", query)

        def approve(sentences):
            # Keep table metadata with the decision. Commentary and superseded clauses
            # cannot supply missing objects or grant authority to another activity.
            clauses = [part.strip() for sentence in sentences
                       for part in RECORD_SCOPE_BREAK.split(sentence, maxsplit=1)[:1] if part.strip()]
            records = []
            for clause in clauses:
                if not records or DECISION_HEADER.match(clause):
                    records.append([])
                records[-1].append(clause)
            return any(approved_record(record) for record in records)

        def approved_record(record):
            authority = [part for part in record
                         if not (RECORD_PENDING.search(part) or DECISION_NONAUTHORITATIVE.search(part))]
            if not any(
                (DECISION_FIELD.search(part) or DECISION_APPROVED.search(part)) and DECISION_ACTION.search(part)
                and (not mass_production or PRODUCTION.search(part))
                for part in authority
            ):
                return False
            scope = [part for part in authority
                     if (DECISION_SCOPE.search(part) or DECISION_HEADER.search(part)
                         or (DECISION_APPROVED.search(part) and DECISION_ACTION.search(part)))]
            scoped_text = " ".join(scope)
            if not references.issubset(PART_CODE.findall(scoped_text)) or not sites.issubset(DECISION_SITE.findall(scoped_text)):
                return False
            return not needs_time or any(re.search(DATE, p) and re.search(r"投用|切换|启用|开线|执行", p) for p in authority)

        checks.append(approve)
    return checks


def _quantitative_requirement(query):
    direction = [marker for marker in (LOWER_LIMIT, UPPER_LIMIT) if re.search(marker, query)]
    if not direction and not re.search(NEUTRAL_LIMIT, query):
        return None
    measures = [(metric, unit) for metric, unit in MEASURE_UNITS if re.search(metric, query)]
    if not measures:
        return None
    comparator = "|".join(direction or (LOWER_LIMIT, UPPER_LIMIT, NEUTRAL_LIMIT))
    conditions = [condition for condition in LIMIT_CONDITIONS if re.search(condition, query)]
    statistics = [statistic for statistic in LIMIT_STATISTICS if re.search(statistic, query)]
    temperatures = {re.sub(r"\s+", "", value).replace("−", "-") for value in CELSIUS.findall(query)}
    references = set(PART_CODE.findall(query)) | set(VERSION.findall(query))

    after = r"\s*(?:为|是|定为|[:：])?\s*"
    bounds = [
        re.compile(
            rf"(?:(?:{metric})(?:的)?(?:合格界限为|应|必须)?\s*(?:{comparator})|"
            rf"(?:{comparator})\s*(?:{metric})){after}[+−-]?{NUMBER}\s*(?:{unit})(?![a-z])"
        )
        for metric, unit in measures
    ]

    def qualifies(sentences):
        for index, clause in enumerate(sentences):
            if not any(bound.search(clause) for bound in bounds):
                continue
            if LIMIT_NOT_EFFECTIVE.search(clause) or LIMIT_OBSERVATION.search(clause):
                continue
            if statistics and not any(re.search(statistic, clause) for statistic in statistics):
                continue
            governing = [clause]
            for previous in reversed(sentences[max(index - 2, 0):index]):
                if LIMIT_NOT_EFFECTIVE.search(previous) or LIMIT_OBSERVATION.search(previous):
                    break
                governing.append(previous)
                if LIMIT_AUTHORITY.search(previous):
                    break
            scope = " ".join(governing)
            if not LIMIT_AUTHORITY.search(scope):
                continue
            if not references.issubset(set(PART_CODE.findall(scope)) | set(VERSION.findall(scope))):
                continue
            if conditions and not any(re.search(condition, scope) for condition in conditions):
                continue
            actual = {re.sub(r"\s+", "", value).replace("−", "-") for value in CELSIUS.findall(scope)}
            if temperatures and temperatures.isdisjoint(actual):
                continue
            return True
        return False

    return qualifies


def _procedure_requirement(query, references):
    topics = [topic for topic in PROCEDURE_TOPICS if re.search(topic, query)]

    def qualifies(sentences):
        if PROCEDURE_UNCONTROLLED.search(" ".join(sentences)):
            return False
        for index, heading in enumerate(sentences):
            if not PROCEDURE_TEXT.search(heading):
                continue
            # Published headings can introduce operative text in the next sentence
            # without repeating the title or document number on each instruction.
            rule = " ".join(sentences[index:index + 3])
            if not PROCEDURE_DIRECTIVE.search(rule):
                continue
            if not references.issubset(PART_CODE.findall(rule)):
                continue
            if all(re.search(topic, rule) for topic in topics):
                return True
        return False

    return qualifies


def _formal_requirements(query):
    references = set(PART_CODE.findall(query))
    checks = _decision_requirements(query, references)
    if CUTOVER_DECISION_REQUEST.search(query) and (references or not SIGNATURE.search(query)):
        needs_date = CUTOVER_DATE_REQUEST.search(query)

        def cutover(sentences):
            for index, sentence in enumerate(sentences):
                decision = DECISION_TRAILING.split(sentence, maxsplit=1)[0]
                if RECORD_PENDING.search(decision) or not references.issubset(PART_CODE.findall(decision)):
                    continue
                if not (CUTOVER_ISSUE.search(decision) or (APPROVED.search(decision) and "切换" in decision)):
                    continue
                scope = OBJECT_SCOPE.search(decision)
                if scope:
                    other_ids = set(PART_CODE.findall(decision[scope.end():])) - set(PART_CODE.findall(scope[1]))
                    if references.intersection(other_ids):
                        continue
                following = sentences[index + 1:index + 2]
                if not needs_date or CUTOVER_TIME.search(decision) or any(
                    CUTOVER_TIME.match(line.strip()) and not RECORD_PENDING.search(line)
                    and set(PART_CODE.findall(line)).issubset(references)
                    for line in following
                ):
                    return True
            return False

        checks.append(cutover)
    for request, kind in RECORD_TYPES:
        if request.search(query):
            locator = re.compile(
                rf"{kind}\s*(?:编号|号|[:：#])?\s*(?=[a-z0-9._-]*[0-9]){CODE}{CODE_END}"
            )

            def issued(sentences, locator=locator):
                return any(
                    ISSUED.search(sentence) and not RECORD_PENDING.search(sentence)
                    and (match := locator.search(sentence))
                    and references.issubset(PART_CODE.findall(sentence))
                    and set(PART_CODE.findall(match[0])) - references
                    for sentence in sentences
                )

            checks.append(issued)
    if PROCEDURE_REQUEST.search(query):
        checks.append(_procedure_requirement(query, references))
    if re.search(LEAKAGE_RATE, query) and re.search(r"最大|上限|限值|判退", query):
        checks.append(lambda sentences: any(
            LEAKAGE_BOUND.search(sentence) and not RECORD_PENDING.search(sentence)
            and references.issubset(PART_CODE.findall(sentence))
            for sentence in sentences
        ))
    return checks


def _calendar_date(value):
    try:
        return date(*map(int, re.findall(r"[0-9]+", value)))
    except ValueError:
        return None


def _valid_on(sentence, day, identifiers):
    if window := DATED_WINDOW.search(sentence):
        first, last = map(_calendar_date, window.groups())
    elif start := EFFECTIVE_START.search(sentence):
        window = start
        first = _calendar_date(start[1])
        end = EFFECTIVE_END.search(sentence, start.end())
        last = _calendar_date(end[1]) if end else date.max
    elif explicit_day := EFFECTIVE_DAY.search(sentence):
        window = explicit_day
        first = last = _calendar_date(explicit_day[1])
    else:
        return False
    if not (first and last and first <= day <= last):
        return False
    # Only the dated clause can establish which record the window belongs to.
    scope = sentence[:window.end()] + re.split(r"[，,]", sentence[window.end():], maxsplit=1)[0]
    codes = set(PART_CODE.findall(scope)) | set(BOM_ROW.findall(scope))
    return (identifiers.issubset(codes) and bool(re.search(r"有效|生效", scope))
            and not re.search(r"过期|作废|停用|待确认|待核|批准前|预计", scope))


def _context_requirements(query):
    checks = []
    if RECEIPT_ZERO_REQUEST.search(query):
        checks.append(lambda text, sentences: any(
            RECEIPT_ZERO_VALUE.search(sentence) and not UNCONFIRMED_BALANCE.search(sentence)
            for sentence in sentences
        ))
    if RECEIPT_SUPPLEMENT.search(query):
        checks.append(lambda text, sentences: all(re.search(term, text) for term in (
            r"采购(?:订单)?行", r"(?:收货|到货)(?:凭证|记录)", r"补数|补核|补查|补齐|补录|需提供",
        )))
    if DATED_EVIDENCE_REQUEST.search(query) and (match := re.search(FULL_DATE, query)):
        requested_day = _calendar_date(match[0])
        identifiers = set(PART_CODE.findall(query)) | set(BOM_ROW.findall(query))

        def date_matches(text, sentences):
            return bool(requested_day and any(
                _valid_on(sentence, requested_day, identifiers)
                for sentence in sentences
            ))

        checks.append(date_matches)
    return checks


def evidence_requirement(query):
    """Build checks only for scope or evidence explicitly requested in the query."""
    original_query = unicodedata.normalize("NFKC", query)
    query = original_query.casefold()
    versions = set(VERSION.findall(query))
    rows = set(BOM_ROW.findall(query))
    named_plants = set(NAMED_PLANT.findall(query))
    plant_codes = PLANT_CODE.findall(query)
    plants = re.compile(rf"{CODE_START}(?:{'|'.join(map(re.escape, plant_codes))}){CODE_END}") if plant_codes else None
    formal = _formal_requirements(query)
    context = _context_requirements(query)
    asks_limit = bool(LIMIT_REQUEST.search(query) or GRAVITY_LIMIT_REQUEST.search(query))
    quantitative = _quantitative_requirement(query)
    if quantitative:
        formal.append(quantitative)
        asks_limit = False
    if re.search(LEAKAGE_RATE, query) and not RESISTANCE_NAME.search(query):
        # The metric-specific check binds the number to the requested measurement.
        asks_limit = False
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
            or effectivity_evidence or stock_categories or formal or context):
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
        if not all(check(normalized_sentences) for check in formal):
            return False
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
        if not all(check(text, normalized_sentences) for check in context):
            return False
        if asks_contract and not (CONTRACT_VALUE.search(text) or CONTRACT_CLAUSE.search(text)):
            return False
        if asks_compensation and not any(
            CANCELLATION_VALUE.search(sentence) and not RECORD_PENDING.search(sentence)
            and (not asks_contract or CONTRACT_VALUE.search(sentence) or CONTRACT_CLAUSE.search(sentence))
            for sentence in normalized_sentences
        ):
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
