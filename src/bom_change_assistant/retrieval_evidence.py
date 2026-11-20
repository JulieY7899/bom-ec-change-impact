"""Evidence required by explicit engineering searches before relevance ranking."""
import re
import unicodedata
from datetime import date
from decimal import Decimal

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
    r"|(?:现行|仍有效|生效的).{0,10}批件|请提供.{0,12}批件|签了什么变更"
)
DECISION_ACTION = re.compile(r"换型|切换|换产|投用|更换|启用|换装|变更")
DECISION_HEADER = re.compile(r"^(?:放行|换型|切换|投用)?(?:批件|审批单|决议|批准单|审批记录)")
DECISION_FIELD = re.compile(r"^(?:结论|决定|审批状态|审批结论|决议)\s*[:：]\s*(?:同意|通过|批准|准予)")
DECISION_APPROVED = re.compile(r"联审通过|签发|现行.{0,8}(?:批件|决议)|(?:同意|批准|准予)")
DECISION_SCOPE = re.compile(r"(?:适用|批准|授权)(?:的)?(?:对象|范围|线体|产线|批次|场地)(?:为|限|是|[:：])")
DECISION_SITE = re.compile(r"(?:[东西南北中]|[东南][东西南北])(?:场|站|区|厂)")
RECORD_SCOPE_BREAK = re.compile(r"[，,]\s*(?:另(?:一|有|案)|原(?:批件|决议)|对照|对比|仅供|(?:[东西南北中](?:场|站|区|厂))同型)")
DECISION_NONAUTHORITATIVE = re.compile(
    r"仅.{0,12}(?:对比|对照|试装|台架|实验室)|留作|维持|未列|不适用|暂未产生|撤回|已取消|废止"
)
PRODUCTION = re.compile(r"批量(?:生产|换产|投产)|量产|正式投产|连续(?:生产线|流水线)")
CURRENT_AUTHORIZATION = re.compile(r"仍(?:然)?(?:批准|授权|有效)|(?:撤回|撤销|撤批)后.{0,24}(?:投用|授权|批准)")
WITHDRAWAL = re.compile(r"撤回|撤销|撤批|撤销授权")
WITHDRAWN_SCOPE = re.compile(r"从.{0,35}(?:授权|批准)范围中(?:删除|剔除|移除)|不再(?:获准|授权|批准)")
CONTINUING_AUTHORIZATION = re.compile(r"(?:继续|仍然?|保持)有效|(?:继续|仍然?)按.{0,30}投用")
PRODUCTION_RECORD_QUERY = re.compile(r"量产放行(?:单|记录).{0,12}(?:签发|哪|编号)|(?:签发|批准|放行).{0,26}(?:量产|连续流水线)|(?:量产|连续流水线).{0,16}(?:签发|批准|放行)")
PRODUCTION_ISSUE = re.compile(r"已(?:正式)?签发.{0,4}量产放行(?:单|记录)|(?:量产放行(?:单|记录)|投产批件).{0,45}已(?:正式)?签发|(?:已批准|批准|同意|获准).{0,24}(?:量产|正式投产|批量生产|批量换产)")
TRIAL_ONLY = re.compile(r"台架|离线试制|试装|试用|不得.{0,8}(?:量产|生产线|流水线)|试验记录")
CONDITION_QUERY = re.compile(r"前置条件|哪些条件|需满足什么条件|须满足什么条件")
CONDITION_TEXT = re.compile(r"仅当|只有在|方可|才允许|前置条件")
ACCEPTED_TEST_QUERY = re.compile(r"(?:复检|复验).{0,10}已(?:完成)?签认.{0,6}合格")
ACCEPTED_TEST = re.compile(r"复(?:检|验).{0,20}已(?:完成)?签认.{0,18}(?:结论.{0,3})?合格")
SIGNED_APPENDIX_QUERY = re.compile(r"签发.{0,16}(?:附页|附件)|(?:附页|附件).{0,16}签发")
SIGNATURE_DETAIL_QUERY = re.compile(r"(?:会签|签署|签字).{0,4}(?:页号|页码|岗位)")
SIGNATURE_ROLE = re.compile(r"(?:会签|签署|签字)(?:岗位|人)\s*[:：为]\s*(?!待|未|无|空)[^，,。]+")
SIGNATURE_PAGE = re.compile(r"(?:会签|签署|签字).{0,8}(?:第\s*[0-9一二三四五六七八九十]+\s*页|页(?:码|号)\s*[:：为]\s*[0-9]+)")
RECORD_TYPES = (
    (re.compile(r"(?:新|有效的?).{0,8}合格证.{0,8}(?:编号|号)"), r"合格证(?:书)?"),
    (re.compile(r"客户.{0,8}(?:发放|放行|交付).{0,8}(?:单号|编号|签批|凭证)"), r"客户.{0,8}(?:发放|放行|交付)(?:单|凭证|签批)"),
    (re.compile(r"(?:量产.{0,8}(?:放行|审批|签发)|(?:批准|放行)量产).{0,12}(?:单号|编号|签发单)"), r"量产(?:放行|批准|签发)(?:单|记录|凭证)"),
)
PROCEDURE_TEXT = re.compile(r"规程|作业标准|指导书|规范")
PROCEDURE_REQUEST = re.compile(
    rf"(?:通用|正式|现行|常规|双签|干装|返库|交班).{{0,12}}(?:{PROCEDURE_TEXT.pattern})"
    rf"|(?:{PROCEDURE_TEXT.pattern})(?:的)?(?:正式)?正文"
    rf"|(?:适用的?|仍有效[^。！？]{{0,55}})(?:{PROCEDURE_TEXT.pattern})"
    r"|(?:交班|交接|复查).{0,12}正式条款"
)
PROCEDURE_CONTINUATION = re.compile(r"(?:步骤|规程|条文).{0,4}(?:续条|续页)|续(?:条|页).{0,6}(?:规定|要求)")
PROCEDURE_CONTROLLED = re.compile(r"已(?:受控)?(?:发布|签发|生效)|(?:受控|现行)版本|已发版")
PROCEDURE_DIRECTIVE = re.compile(r"要求|规定|允许|必须|应当|不得|须|方可|才可|禁止|仅(?:当|在)|每(?:隔|班|日|次)")
PROCEDURE_STEP = re.compile(r"放入|移入|交(?:给|由)|再交|核对|检查|确认|复核|登记|通知")
PROCEDURE_ROLE_REQUEST = re.compile(r"谁|哪(?:一|个)?岗|哪个(?:人员|角色)")
PROCEDURE_ROLE = re.compile(r"岗位|岗|[班组线]长|领班|主管|(?:检验|质量|操作|维护|维修|管理)员|责任人|工程师")
PROCEDURE_OVERRIDE = (r"替代|代替|代行", r"省略|免(?:去|除|做|检)|跳过")
PROCEDURE_VISUAL_ID = re.compile(r"[红黄绿蓝橙白黑](?:色)?(?:状态)?(?:标牌|标签|料箱|周转框|牌|箱|框)")
PROCEDURE_UNCONTROLLED = re.compile(
    r"拟稿|草案|(?:征求|试征)意见|未(?:受控|正式)?发布|未签发现行规程|已废止"
    r"|只限本次|仅限(?:该|此|本)(?:次|批|工单)|一次性(?:偏差|作业规程|许可|授权|处置|指令)"
    r"|单次处置|不得推广|临时(?:处置|通知)|已(?:停用|废弃)|征询"
    r"|单(?:件|批)偏离|返工复盘|交班演练|模拟评审|档案附件"
    r"|仅供展示|演示附页|仅为培训|仅限.{0,8}模型|(?:目录|索引).{0,24}(?:规程|条文|题名)|(?:未附|缺少).{0,8}正文|正文未附"
)
PROCEDURE_TOPICS = (
    r"清场", r"双签|双人签署", r"软(?:质)?护套", r"双层(?:式)?托盘", r"跨班",
    r"封样桶", r"量块", r"复机", r"首件", r"巡检", r"入库",
    r"换卷", r"定位叉", r"检具", r"热封线",
    r"导通复查", r"潮封", r"压合", r"定位块", r"连续生产", r"二次渗漏",
    r"封签|签封", r"交班|转交下一班|交下一班", r"干装", r"密封检查", r"返库",
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
    (r"启动摩擦力|起动摩擦力", r"n|牛顿?"),
    (r"回弹间隙", r"mm|毫米|μm|微米"),
    (r"阻抗", r"ω|欧姆?"),
    (r"渗液量", r"ml|毫升"),
    (r"横向摆幅", r"mm|毫米|μm|微米"),
    (r"响应(?:时间|时长)?", r"ms|毫秒|s|秒"),
    (r"(?:光源)?亮度", r"%|cd/m²|cd/m2"),
    (r"温升", r"k|开尔文|°c|摄氏度"),
    (r"泄漏率", r"ml\s*/\s*min|毫升\s*/\s*分钟|毫升每分钟"),
)
LOWER_LIMIT = r"下限|最低(?:允许)?|最小(?:允许)?|不(?:得|能)?低于|至少|不(?:得|能)?小于|≥|>="
UPPER_LIMIT = r"上限|最高(?:允许)?|最大(?:允许)?|最多(?:允许)?|不(?:得|能)?(?:超过|高于)|至多|不(?:得|能)?大于|≤|<="
NEUTRAL_LIMIT = r"限值|公差|合格界限|合格线|验收标准|判退线"
LIMIT_AUTHORITY = re.compile(
    rf"已(?:发布|签发|生效)|现行(?:版|条款|规格|验收|\s*{VERSION.pattern})"
    r"|检验规范|(?:正式|当前)(?:检验令|验收令|放行细则|放行验收表)"
    r"|(?:验收|放行)(?:条件|标准|文件|规范)|合格界限|条款规定|另列.{0,20}(?:上限|下限)"
)
LIMIT_NOT_EFFECTIVE = re.compile(r"已停用|停止使用|停止作为|征求意见|讨论稿|未签|待(?:评审|审定)|未(?:批准|生效|设|规定)|仅供|不(?:适用|作为)|草案")
LIMIT_OBSERVATION = re.compile(r"实测|记录值|实测均值|原始读数|量程|控制目标|调试目标|试设")
LIMIT_CONDITIONS = (
    r"热老化(?:后)?|老化后", r"暖机(?:后)?", r"冷启动", r"常温|室温", r"低温",
    r"干态", r"湿态|淋湿",
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

FULL_DATE = r"(?:20[0-9]{2}|二[〇零][〇零一二三四五六七八九]{2})\s*(?:年|[-/.])\s*[0-9]{1,2}\s*(?:月|[-/.])\s*[0-9]{1,2}(?:\s*日)?(?![0-9])"
DATED_WINDOW = re.compile(rf"({FULL_DATE})\s*(?:至|到|~|—|–|－)\s*({FULL_DATE})")
EFFECTIVE_START = re.compile(rf"(?:从|自)\s*({FULL_DATE})\s*(?:起|开始)?\s*(?:有效|生效)")
EFFECTIVE_END = re.compile(rf"(?:至|到|截止于?|有效期至)\s*({FULL_DATE})(?:\s*(?:截止|结束|为止))?")
EFFECTIVE_DAY = re.compile(rf"({FULL_DATE})\s*(?:当天|当日)\s*(?:仍)?有效")
DATED_EVIDENCE_REQUEST = re.compile(r"(?:当天|当日)(?:仍)?有效|仍(?:在)?有效|已(?:经)?(?:生效|有效)")
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


APPROVAL_SCOPE_ALIAS = re.compile(r"[甲乙丙丁戊己庚辛壬癸](?:号签件|组批次)")
BATCH_REFERENCE = re.compile(
    rf"{CODE_START}(?:lot|batch|bg)[-_]{CODE}{CODE_END}|[甲乙丙丁戊己庚辛壬癸]组批次"
)
APPROVAL_DATE_INTENT = re.compile(
    r"撤回(?:的)?(?:起算|生效)日|范围调整后|生效确认单|生效日的批件|有效(?:批准|批件|放行)"
    r"|(?:新|生效)批件|(?:试装|试制).{0,24}签发单"
)
AUTHORIZED_OPERATION = re.compile(r"换版|换装|切换|换产|投用|启用|试装|试制|制造|生产|生效|放行|继续有效")
AUTHORIZATION_RECORD = re.compile(r"批件|签件|签发单|批准单|放行单|确认单|授权范围调整单")
AUTHORIZATION_GRANT = re.compile(r"批准|准予|同意|已签发|许可|授权|确认单|继续有效|仍可凭")
AUTHORIZATION_NONOPERATIVE = re.compile(
    r"签到|宣贯|预装|台账|库存记录|追溯索引|排期|排产|拟提交|仅作|复检|完成.{0,8}试装"
)
AUTHORIZATION_START = re.compile(
    rf"(?:从|自)\s*({FULL_DATE})\s*(?:零时|当日|当天|起|开始|正式)*"
    r"(?:有效|生效|启用|实施|投用|切换)"
)
WITHDRAWAL_START = re.compile(
    rf"(?:从|自|于)\s*({FULL_DATE})\s*(?:零时|当日|当天|起|开始)*(?:正式)?(?:撤回|撤销|作废)"
)


def _references(text):
    return set(PART_CODE.findall(text)) | set(APPROVAL_SCOPE_ALIAS.findall(text))


def _withdrawal_date(clause):
    if effective := WITHDRAWAL_START.search(clause):
        return _calendar_date(effective[1])
    day = re.search(FULL_DATE, clause)
    return _calendar_date(day[0]) if day else None


def _dated_authorization(query):
    day = re.search(FULL_DATE, query)
    requested = _calendar_date(day[0]) if day else None
    if not (requested and APPROVAL_DATE_INTENT.search(query)):
        return None
    # Existing date-window records and formal signatures retain their specific checks.
    if not re.search(r"撤回起算日|范围调整后|生效确认单|换版|常规(?:装配|生产|线|制造)|正式制造|批量切换|试装|新批件|编号正式切换", query):
        return None
    references = _references(query)
    asks_boundary = "撤回起算日" in query
    asks_adjustment = "范围调整后" in query
    adjusted_scope = set(BATCH_REFERENCE.findall(query)) or references
    asks_confirmation = "生效确认单" in query
    asks_mass = bool(re.search(r"批量|量产", query))
    asks_trial = bool(re.search(r"样机|验证台|离线|台架", query) and re.search(r"试装|试制", query))
    asks_new = bool(re.search(r"哪.{0,4}新批件", query))

    def qualifies(text):
        if not references.issubset(_references(text)):
            return False
        if AUTHORIZATION_NONOPERATIVE.search(text):
            return False
        sentences = SENTENCE.split(text)
        if asks_boundary:
            return any(
                (effective := _withdrawal_date(s)) and effective > requested
                and WITHDRAWAL.search(s) and references.issubset(_references(s))
                for s in sentences
            ) and any(
                (stated := re.search(FULL_DATE, s)) and _calendar_date(stated[0]) == requested
                and re.search(r"仍(?:可|有效)|继续有效", s) and AUTHORIZED_OPERATION.search(s)
                for s in sentences
            )
        if re.search(r"作废|已废止|待.{0,16}后生效|等.{0,8}生效日", text):
            return False
        if asks_adjustment:
            return any(
                re.search(r"范围调整|缩小范围|部分撤", s)
                and any(re.search(r"继续有效|保留.{0,8}授权", clause)
                        and adjusted_scope.issubset(_references(clause))
                        for clause in re.split(r"[，,]", s))
                and (start := re.search(FULL_DATE, s)) and (effective := _calendar_date(start[0]))
                and effective <= requested
                for s in sentences
            )
        if asks_confirmation and "生效确认单" not in text:
            return False
        if asks_new and not re.search(r"新批件|重新签发|重新批准", text):
            return False
        if not asks_trial and re.search(r"(?:仅|只).{0,20}(?:试装|试制|台架)|须.{0,8}独立批件", text):
            return False
        if asks_trial and not re.search(r"试装|试制", text):
            return False
        if asks_mass and not any(AUTHORIZATION_GRANT.search(s) and re.search(r"批量|量产", s) for s in sentences):
            return False
        if not (AUTHORIZATION_RECORD.search(text) and AUTHORIZATION_GRANT.search(text)
                and AUTHORIZED_OPERATION.search(text)):
            return False
        start = AUTHORIZATION_START.search(text)
        if start:
            effective = _calendar_date(start[1])
            return bool(effective and effective <= requested)
        return any(
            (date_in_sentence := re.search(FULL_DATE, s)) and "自该日生效" in s
            and (effective := _calendar_date(date_in_sentence[0])) and effective <= requested
            for s in sentences
        )

    return qualifies


def withdrawn_authorization_filter(query):
    """Identify a scoped withdrawal when looking for authorization that is still valid."""
    query = unicodedata.normalize("NFKC", query).casefold()
    references = _references(query)
    dated = _dated_authorization(query)
    if (not references or not (CURRENT_AUTHORIZATION.search(query) or dated)
            or (len(references) < 2 and not dated)
            or re.search(r"(?:撤回|撤销)前|撤回起算日|历史批件|原始批件|哪.{0,4}新批件|范围调整后", query)):
        return None
    day = re.search(FULL_DATE, query)
    requested_day = _calendar_date(day[0]) if day else None

    def withdraws(text):
        clauses = SENTENCE.split(unicodedata.normalize("NFKC", text).casefold())
        for clause in clauses:
            if not (WITHDRAWN_SCOPE.search(clause) or WITHDRAWAL.search(clause)):
                continue
            if re.search(r"拟撤|待撤|未撤|是否撤|可能撤", clause):
                continue
            if requested_day and (effective := _withdrawal_date(clause)) and effective > requested_day:
                continue
            # A scope reduction can withdraw one batch and retain another in the same sentence.
            removed_scope = re.split(r"[，,](?=[^，,。]*?(?:继续有效|保持有效|保留))", clause, maxsplit=1)[0]
            if references.issubset(_references(removed_scope)) and not CONTINUING_AUTHORIZATION.search(removed_scope):
                return True
        return False

    return withdraws


def _decision_requirements(query, references):
    checks = []
    if references and CURRENT_AUTHORIZATION.search(query):
        checks.append(lambda sentences: any(
            references.issubset(PART_CODE.findall(" ".join(sentences)))
            and CONTINUING_AUTHORIZATION.search(sentence)
            and not WITHDRAWN_SCOPE.search(sentence)
            and set(PART_CODE.findall(sentence)).intersection(references)
            for sentence in sentences
        ) or not WITHDRAWAL.search(" ".join(sentences)) and any(
            references.issubset(PART_CODE.findall(sentence)) and (ISSUED.search(sentence) or re.search(r"现行.{0,12}批件", sentence))
            and not (RECORD_PENDING.search(sentence) or DECISION_NONAUTHORITATIVE.search(sentence))
            for sentence in sentences
        ))
        if re.search(r"(?:撤回|撤销|撤批)后", query):
            checks.append(lambda sentences: any(CONTINUING_AUTHORIZATION.search(s) for s in sentences))
    if references and PRODUCTION_RECORD_QUERY.search(query) and not PROCEDURE_REQUEST.search(query):
        checks.append(lambda sentences: any(
            references.issubset(PART_CODE.findall(sentence)) and PRODUCTION_ISSUE.search(sentence)
            and not (TRIAL_ONLY.search(sentence) or RECORD_PENDING.search(sentence))
            for sentence in sentences
        ))
    if references and CONDITION_QUERY.search(query) and PRODUCTION.search(query):
        checks.append(lambda sentences: (
            any(ISSUED.search(s) and references.issubset(PART_CODE.findall(s))
                and not RECORD_PENDING.search(s) for s in sentences)
            and any(CONDITION_TEXT.search(s) and PRODUCTION.search(s) for s in sentences)
        ))
    if references and ACCEPTED_TEST_QUERY.search(query):
        # The inspection result, rather than the conditional approval, establishes readiness.
        checks.append(lambda sentences: any(
            ACCEPTED_TEST.search(s) and not re.search(r"仅当|若|待|尚未|未签|不合格", s)
            and references.issubset(PART_CODE.findall(s)) for s in sentences
        ))
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
    if DECISION_REQUEST.search(query) and not CURRENT_AUTHORIZATION.search(query) and (references or DECISION_SITE.search(query)):
        sites = set(DECISION_SITE.findall(query))
        mass_production = PRODUCTION.search(query)
        needs_time = CUTOVER_DATE_REQUEST.search(query)

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


CHINESE_DECIMAL = re.compile(r"[〇零一二两三四五六七八九]点[〇零一二三四五六七八九]+")
CHINESE_DIGITS = str.maketrans("〇零一二两三四五六七八九点", "001223456789.")
DURATION = re.compile(rf"(?<![0-9.])({NUMBER}|[一二两三四五六七八九]?十[一二三四五六七八九]?|[一二两三四五六七八九])\s*(小时|分钟|秒)(?!钟?响应)")
CONDITION_VALUES = {
    "湿度": re.compile(rf"({NUMBER})\s*%\s*(?:相对)?湿度"),
    "测量频率": re.compile(rf"({NUMBER})\s*khz"),
    "预紧扭矩": re.compile(rf"({NUMBER})\s*n[ ·⋅.]*m\s*预紧"),
}
CONDITION_STATES = (
    (r"回温(?!前)|复温(?!前)", r"(?:尚)?未回温|回温前|未复温"),
    (r"(?:带|保留|有)防护膜", r"(?:去|移除|无|撕除)防护膜"),
    (r"立向|竖向|纵向", r"横向(?=压紧)|水平(?=压紧)"),
)


def _measurement_text(text):
    return CHINESE_DECIMAL.sub(lambda match: match[0].translate(CHINESE_DIGITS), text)


def _duration_values(text):
    amounts = set()
    for number, unit in DURATION.findall(text):
        if "十" in number:
            tens, ones = number.split("十")
            amount = 10 * int(tens.translate(CHINESE_DIGITS) or 1) + int(ones.translate(CHINESE_DIGITS) or 0)
        else:
            amount = Decimal(number.translate(CHINESE_DIGITS).replace(",", ""))
        amounts.add(amount * {"小时": 3600, "分钟": 60, "秒": 1}[unit])
    return amounts


def _condition_state(text, present, absent):
    if re.search(absent, text):
        return False
    if re.search(present, text):
        return True
    return None


def _bound_quantity(amount, unit):
    value = Decimal(amount.replace(",", "").replace("−", "-"))
    unit = {"毫米": "mm", "微米": "μm", "欧": "ω", "欧姆": "ω", "牛": "n",
            "牛顿": "n", "毫升": "ml"}.get(unit, unit)
    return (value / 1000, "mm") if unit == "μm" else (value, unit)


def _quantitative_requirement(query):
    query = _measurement_text(query)
    both_bounds = bool(re.search(r"上下限|上、下限|两(?:个)?界限|区间两端|验收区间|允许范围|区间(?:上限和下限|下限和上限)", query))
    direction = [marker for marker in (LOWER_LIMIT, UPPER_LIMIT) if both_bounds or re.search(marker, query)]
    if not direction and not re.search(NEUTRAL_LIMIT, query):
        return None
    measures = [(metric, unit) for metric, unit in MEASURE_UNITS if re.search(metric, query)]
    if not measures:
        return None
    # Retain the existing leakage-bound evidence path for requests without an explicit condition.
    if re.search(LEAKAGE_RATE, query) and not CELSIUS.search(query) and len(measures) == 1:
        return None
    comparator = "|".join(direction or (LOWER_LIMIT, UPPER_LIMIT, NEUTRAL_LIMIT))
    conditions = [condition for condition in LIMIT_CONDITIONS if re.search(condition, query)]
    statistics = [statistic for statistic in LIMIT_STATISTICS if re.search(statistic, query)]
    temperatures = {re.sub(r"\s+", "", value).replace("−", "-") for value in CELSIUS.findall(query)}
    references = set(PART_CODE.findall(query)) | set(VERSION.findall(query))

    duration = _duration_values(query)
    requested_values = [(pattern, {Decimal(value.replace(",", "")) for value in pattern.findall(query)})
                        for pattern in CONDITION_VALUES.values()]
    required_states = [(present, absent, state) for present, absent in CONDITION_STATES
                       if (state := _condition_state(query, present, absent)) is not None]
    after = r"\s*(?:为|是|定为|[:：])?\s*"
    bounds = []
    for metric, unit in measures:
        value = rf"(?P<value>[+−-]?{NUMBER})\s*(?P<unit>{unit})(?![a-z·])"
        requested_quantities = {
            _bound_quantity(match["value"], match["unit"])
            for match in re.finditer(value, query)
        } if not re.search(r"何值|多少|哪两个", query) else set()
        labeled_bound = (
            rf"(?:(?:{metric})(?:的)?(?:合格界限为|应|须|必须)?\s*(?:{comparator})|"
            rf"(?:{comparator})\s*(?:{metric})){after}{value}"
        )
        # An explicit heading can introduce an inequality stated after a colon.
        bounds.append((re.compile(labeled_bound), requested_quantities))
        if not direction or UPPER_LIMIT in direction:
            bounds.append((re.compile(
                rf"(?:{metric})(?:的)?(?:{UPPER_LIMIT})\s*[:：][^。；]{{0,40}}"
                rf"(?:须在\s*|(?:不得超过|不高于)\s*){value}(?:\s*(?:以)?内)?"
            ), requested_quantities))
        if not direction or LOWER_LIMIT in direction:
            bounds.append((re.compile(
                rf"(?:{metric})(?:的)?(?:{LOWER_LIMIT})\s*[:：][^。；]{{0,40}}(?:不得低于|不低于)\s*{value}"
            ), requested_quantities))

    def qualifies(sentences):
        sentences = [_measurement_text(s) for s in sentences]
        for index, clause in enumerate(sentences):
            if not any(
                (match := bound.search(clause))
                and (not quantities or _bound_quantity(match["value"], match["unit"]) in quantities)
                for bound, quantities in bounds
            ):
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
            if duration and duration.isdisjoint(_duration_values(clause)):
                continue
            if any(values and values.isdisjoint(Decimal(value.replace(",", "")) for value in pattern.findall(clause))
                   for pattern, values in requested_values):
                continue
            if any(_condition_state(clause, present, absent) is not state
                   for present, absent, state in required_states):
                continue
            return True
        return False

    return qualifies


PROCEDURE_RECORD_ALIAS = re.compile(r"[甲乙丙丁戊己庚辛壬癸]号(?:程序文件|工单)")
LOCAL_DEVIATION_REQUEST = re.compile(r"单(?:批|件|次)(?:偏离|授权|例外|批准)")
LOCAL_DEVIATION_RECORD = re.compile(r"(?:单(?:批|件|次)|(?:该|此|本)(?:批|工单)).{0,24}(?:偏离|授权|特准|批准)")
PROCEDURE_OPERATIONS = (
    (r"原返工人员.{0,10}(?:自己|自行|复查)", r"原返工人员.{0,14}(?:自行|复查)"),
    (r"只称一次|只称一遍|仅称一次|一次称重", r"(?:只|仅)称(?:重)?一次|一次称重|以第一次称重(?:记录)?"),
    (r"操作员自检单", r"(?:凭|依据|按)操作员自检单"),
    (r"自行补写封签", r"(?:自行|自己)补(?:写|录)封签"),
    (r"先标记后(?:做)?密封检查", r"先(?:做)?标记.{0,8}(?:再|后)(?:做)?密封检查"),
    (r"(?:单人|一人)核签", r"(?:单人|一人)核签"),
)
PROCEDURE_EXCEPTION = re.compile(r"发生.{0,16}(?:离线|故障)时|仅当|只有在|例外(?:情况)?")
IN_FORCE_PROCEDURE = re.compile(
    rf"(?:从|自)\s*({FULL_DATE})\s*(?:起|开始)?(?:施行|执行|生效|启用)"
)


def _procedure_requirement(query, references):
    topics = [topic for topic in PROCEDURE_TOPICS if re.search(topic, query)]
    requested_overrides = []
    if re.search(r"允许|可否|可以|有准许|改用|改贴", query):
        requested_overrides = [action for action in PROCEDURE_OVERRIDE if re.search(action, query)]
    visual_ids = {mark.replace("色", "") for mark in PROCEDURE_VISUAL_ID.findall(query)}
    needs_role = PROCEDURE_ROLE_REQUEST.search(query)
    aliases = set(PROCEDURE_RECORD_ALIAS.findall(query))
    operations = [supports for requested, supports in PROCEDURE_OPERATIONS if re.search(requested, query)]
    asks_general = bool(re.search(r"默认|每批|常规|所有|通用|一律", query))
    production = bool(re.search(r"常规(?:装配|生产|线)|所有.{0,8}装配设施", query))
    requested_date = re.search(FULL_DATE, query)
    requested_day = _calendar_date(requested_date[0]) if requested_date else None

    def qualifies(sentences):
        if PROCEDURE_UNCONTROLLED.search(" ".join(sentences)):
            return False
        for index, heading in enumerate(sentences):
            if not (PROCEDURE_TEXT.search(heading)
                    or references and PROCEDURE_CONTINUATION.search(heading)):
                continue
            # Published headings can introduce operative text in the next sentence
            # without repeating the title or document number on each instruction.
            rule = " ".join(sentences[index:index + 3])
            if not (PROCEDURE_DIRECTIVE.search(rule)
                    or PROCEDURE_CONTROLLED.search(heading) and PROCEDURE_STEP.search(rule)):
                continue
            if visual_ids and not visual_ids.issubset(
                mark.replace("色", "") for mark in PROCEDURE_VISUAL_ID.findall(rule)
            ):
                continue
            if needs_role and not PROCEDURE_ROLE.search(rule):
                continue
            if not references.issubset(PART_CODE.findall(rule)) or not aliases.issubset(PROCEDURE_RECORD_ALIAS.findall(rule)):
                continue
            if not all(re.search(topic, rule) for topic in topics):
                continue
            if production and re.search(r"验证线|模拟设施|仅限验证|限验证区", rule):
                continue
            start = IN_FORCE_PROCEDURE.search(rule)
            if requested_day and start and (effective := _calendar_date(start[1])) and effective > requested_day:
                continue
            operative_rule = PROCEDURE_EXCEPTION.split(rule, maxsplit=1)[0] if asks_general else rule
            if not all(
                re.search(operation, operative_rule)
                and not re.search(rf"(?:禁止|不得|不允许|不可).{{0,16}}(?:{operation})", operative_rule)
                for operation in operations
            ):
                continue
            if all(re.search(rf"(?:允许|可以|可由|准许).{{0,32}}(?:{action})", rule)
                   and not re.search(rf"(?:禁止|不得|不允许|不可).{{0,20}}(?:{action})", rule)
                   for action in requested_overrides):
                return True
        return False

    return qualifies


def _formal_requirements(query, quantitative=None):
    references = set(PART_CODE.findall(query))
    checks = _decision_requirements(query, references)
    if CUTOVER_DECISION_REQUEST.search(query) and (references or not SIGNATURE.search(query)):
        needs_date = CUTOVER_DATE_REQUEST.search(query)

        def cutover(sentences):
            for index, sentence in enumerate(sentences):
                decision = DECISION_TRAILING.split(sentence, maxsplit=1)[0]
                if RECORD_PENDING.search(decision) or not references.issubset(PART_CODE.findall(decision)):
                    continue
                if not (CUTOVER_ISSUE.search(decision) or (APPROVED.search(decision) and "切换" in decision)
                        or (re.search(r"(?:批准|审批|签批).{0,30}已(?:正式)?签发", decision)
                            and DECISION_ACTION.search(decision))):
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
    elif LOCAL_DEVIATION_REQUEST.search(query) and ("工单" in query or references):
        aliases = set(PROCEDURE_RECORD_ALIAS.findall(query))
        topics = [topic for topic in PROCEDURE_TOPICS if re.search(topic, query)]
        operations = [supports for requested, supports in PROCEDURE_OPERATIONS if re.search(requested, query)]

        def local_deviation(sentences):
            return any(
                LOCAL_DEVIATION_RECORD.search(sentence) and re.search(r"授权|特准|批准|准许", sentence)
                and not RECORD_PENDING.search(sentence)
                and references.issubset(PART_CODE.findall(sentence))
                and aliases.issubset(PROCEDURE_RECORD_ALIAS.findall(sentence))
                and all(re.search(p, sentence) for p in topics + operations)
                for sentence in sentences
            )

        checks.append(local_deviation)
    if not quantitative and re.search(LEAKAGE_RATE, query) and re.search(r"最大|上限|限值|判退", query):
        checks.append(lambda sentences: any(
            LEAKAGE_BOUND.search(sentence) and not RECORD_PENDING.search(sentence)
            and references.issubset(PART_CODE.findall(sentence))
            for sentence in sentences
        ))
    return checks


def _calendar_date(value):
    try:
        normalized = value.translate(str.maketrans("〇零一二三四五六七八九", "00123456789"))
        return date(*map(int, re.findall(r"[0-9]+", normalized)))
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
                or (index > 0 and re.match(r"\s*(?:有效期|自|从)", sentence)
                    and ISSUED.search(sentences[index - 1])
                    and _valid_on(sentences[index - 1] + " " + sentence, requested_day, identifiers))
                for index, sentence in enumerate(sentences)
            ))

        checks.append(date_matches)
    return checks


def evidence_requirement(query):
    """Build checks only for scope or evidence explicitly requested in the query."""
    original_query = unicodedata.normalize("NFKC", query)
    query = original_query.casefold()
    if dated := _dated_authorization(query):
        return lambda text: dated(unicodedata.normalize("NFKC", text).casefold())
    versions = set(VERSION.findall(query))
    rows = set(BOM_ROW.findall(query))
    named_plants = set(NAMED_PLANT.findall(query))
    plant_codes = PLANT_CODE.findall(query)
    plants = re.compile(rf"{CODE_START}(?:{'|'.join(map(re.escape, plant_codes))}){CODE_END}") if plant_codes else None
    quantitative = _quantitative_requirement(query)
    formal = _formal_requirements(query, quantitative)
    context = _context_requirements(query)
    asks_limit = bool(LIMIT_REQUEST.search(query) or GRAVITY_LIMIT_REQUEST.search(query))
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
