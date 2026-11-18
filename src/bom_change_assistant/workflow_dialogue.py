"""Collect a structured workflow draft from a local, multi-turn conversation."""
from __future__ import annotations

import re
import unicodedata
from datetime import date

from . import workflow

MAX_TURNS = 30
MAX_MESSAGE = 500
DATE = r"(?<!\d)(20\d{2})\s*[-年/]\s*(\d{1,2})\s*[-月/]\s*(\d{1,2})(?!\d)(?:\s*日)?"
NUMBER = r"(?:\d+(?:\.\d+)?|\.\d+)"
ROLE = "|".join(workflow.ROLE_LABELS)
FIELD_HINTS = {
    **workflow.SCOPE_HINTS,
    "plant": r"(?:工程版本)?工厂(?:代码)?",
    "engineering_version": r"(?:工程版本|EBOM\s*版本|版本号|版本)",
    "target_bom_row": r"(?:目标\s*)?(?:BOM\s*行(?:号)?|行号)",
    "old_component": r"(?:(?:原|旧)(?:组件|物料|料号|件)|原来用的)",
    "new_component": r"(?:新(?:组件|物料|料号|件)|替代(?:采购件|件|料号))",
    "target_parent": r"(?:目标\s*)?父件(?:号|物料)?",
    "planning_plant": r"采购工厂(?:代码)?",
    "storage_location": r"(?:采购)?库位(?:代码)?",
}
ACTION_WORDS = {
    "replace": r"替换(?:组件)?|换料",
    "remove": r"删除(?:组件)?|移除(?:组件)?",
    "add": r"新增(?:组件)?|添加(?:组件)?",
    "quantity": r"(?:修改|调整|变更)用量|用量(?:从|改)",
    "effectivity": r"(?:修改|改变|调整|变更)生效(?:日期|日)",
}
ROLE_PROMPT = "这次从哪个视角核查？可以回复研发、采购、销售、制造或工程。"
ACTION_PROMPT = "你打算替换、删除、新增组件，还是调整用量或生效日期？"
PROMPTS = {
    "role": ROLE_PROMPT,
    "analysis_mode": "要按工程版本和 BOM 行限定范围，还是按物料看所有使用位置？",
    "change_type": ACTION_PROMPT,
    "effective_date": "拟在哪一天生效？请回复带年份的日期，例如 2026-12-01。",
    "old_effective_date": "原生效日期是哪一天？请包含年份。",
    "engineering_version": "哪个工程版本？可以直接回复版本号。",
    "target_bom_row": "要核查哪条 BOM 行？请回复该版本下的 BOM 行号。",
    "old_component": "这次变更的原组件料号是什么？",
    "new_component": "新组件料号是什么？",
    "target_parent": "要在哪个父件下新增？请回复父件料号。",
    "old_quantity": "原用量是多少？零也可以直接填写。",
    "new_quantity": "新用量是多少？",
    "product": "请提供工程版本对应的成品料号。",
}
SCOPE_FIELDS = ("product", "plant", "engineering_version")
CHANGE_FIELDS = {
    "old_component": {"replace", "remove", "quantity", "effectivity"},
    "new_component": {"replace", "add"},
    "old_quantity": {"quantity"},
    "new_quantity": {"quantity"},
    "old_effective_date": {"effectivity"},
}


def _iso(match):
    try:
        return date(*map(int, match.groups())).isoformat()
    except ValueError:
        return None


def _material_edits(message):
    """Keep a change to a named field out of the BOM replacement parser."""
    edits = {}
    for field in ("old_component", "new_component"):
        label = FIELD_HINTS[field]
        part = workflow.ID_WORD
        edit = (rf"{label}\s*(?:(?:从|由)\s*{part}\s*(?:改为|改成|换成)|"
                rf"(?:不是\s*{part}|(?:我)?(?:说错|报错|写错|填错)了?)\s*[,，、]?\s*(?:应该是|应为|而是|是))"
                rf"\s*({part})")

        def take_value(match):
            edits[field] = match[1]
            return " "

        message = re.sub(edit, take_value, message)
    return message, edits


def _new_details(message, draft, expected):
    proposal, edits = _material_edits(message)
    details = {key: value for key, value in workflow._suggest(proposal).items()
               if key in ("change_type", "old_component", "new_component")}
    dates = list(re.finditer(DATE, message))
    effective_dates = []
    for match in dates:
        before = message[:match.start()]
        after = message[match.end():]
        if (re.search(r"(?:生效(?:日(?:期)?)?|切换日)\s*(?:改为|改成|定在|为|是|[:=])?\s*$", before)
                or re.match(r"\s*(?:起)?生效", after)):
            effective_dates.append(match)
    if len(effective_dates) == 1:
        chosen = effective_dates[0]
    elif len(dates) == 1:
        match = dates[0]
        before = re.split(r"[,，;；。\n]", message[:match.start()])[-1]
        after = re.split(r"[,，;；。\n]", message[match.end():])[0]
        supply_date = (re.search(r"(?:到货|交货|交期|采购日期)(?:日(?:期)?)?\s*(?:为|是|在|[:=])?\s*$", before)
                       or re.match(r"\s*(?:前|当天|仍|也|才(?:能)?|预计|无法|不能|没法|未能|不能及时)*"
                                   r"(?:到货|交货|到不了|交不了)", after))
        chosen = None if supply_date else match
    else:
        chosen = None
    if chosen and (parsed := _iso(chosen)):
        details["effective_date"] = parsed
    role = re.search(rf"(?:从|我是|改为|切换到|角色\s*[:=]?\s*)({ROLE})(?:\s*(?:角度|视角)|(?=[，,。 ]|$))", message)
    if role:
        details["role"] = workflow.ROLE_LABELS[role[1]]
    elif prefix := re.match(rf"^(?:我们)?({ROLE})(?:这边|部门|\s*[:：])", message):
        details["role"] = workflow.ROLE_LABELS[prefix[1]]
    elif message in workflow.ROLE_LABELS:
        details["role"] = workflow.ROLE_LABELS[message]
    elif "role" not in draft:
        inferred = workflow._role(message)
        if inferred:
            details["role"] = inferred
    phrases = [kind for kind, pattern in ACTION_WORDS.items()
               if re.fullmatch(pattern, message) or re.search(rf"(?:想|计划|打算|拟|(?<!不)要)\s*(?:{pattern})", message)]
    if not details.get("change_type") and len(phrases) == 1:
        details["change_type"] = phrases[0]
    replacement_unresolved = details.get("new_component") == ""
    for field, label in FIELD_HINTS.items():
        if field == "new_component" and replacement_unresolved:
            continue
        # Keep a purchase plant out of the EBOM scope.
        prefix = r"(?<!采购)" if field == "plant" else ""
        found = re.findall(
            rf"{prefix}{label}\s*(?:改为|改成|改用|换成|设为|用|是|为|[:=])?\s*({workflow.ID_WORD})",
            message, re.IGNORECASE,
        )
        if len(found) == 1:
            details[field] = found[0]
    details.update(edits)
    if expected == "new_component" and not details.get("new_component"):
        chosen_part = re.fullmatch(rf"(?:最终|确定|确认)(?:选用|用|选)\s*({workflow.ID_WORD})", message)
        if chosen_part:
            details["new_component"] = chosen_part[1]
    if not details.get("change_type") and details.get("old_component") and details.get("new_component"):
        if re.search(r"替换|替代|更换|换料", message):
            details["change_type"] = "replace"
    if details.get("effective_date") and not re.search(r"生效", message) and any(
        details.get(field) == details["effective_date"] for field in FIELD_HINTS
    ):
        details.pop("effective_date")
    if re.search(r"按\s*物料(?:追溯|核查|看)?", message):
        details["analysis_mode"] = "material"
    elif re.search(r"按\s*(?:工程版本|EBOM)|工程版本\s*(?:改为|改成|用|换成)", message, re.I) or any(
        field in details for field in ("engineering_version", "target_bom_row")
    ):
        details["analysis_mode"] = "engineering"

    quantity = re.search(rf"用量\s*从\s*({NUMBER})\s*(?:改为|改成|调到|到)\s*({NUMBER})", message)
    if quantity:
        details.update(change_type="quantity", old_quantity=float(quantity[1]), new_quantity=float(quantity[2]))
        subject = re.search(rf"({workflow.ID_WORD})\s*(?:的)?\s*用量\s*从", message)
        if subject:
            details.setdefault("old_component", subject[1])
    for count in re.finditer(rf"(原用量|新用量)\s*(?:改为|改成|是|为|[:=])?\s*({NUMBER})", message):
        details["old_quantity" if count[1] == "原用量" else "new_quantity"] = float(count[2])
        details.setdefault("change_type", "quantity")
    if addition := re.search(rf"(?:新增|添加)(?:组件)?\s*({workflow.ID_WORD})", message):
        details.update(change_type="add", new_component=addition[1])
    if re.search(r"(?:原生效|新生效|生效(?:日(?:期)?)?从)", message):
        pair = re.search(rf"生效(?:日(?:期)?)?\s*从\s*({DATE})\s*(?:改为|改到|改成|到)\s*({DATE})", message)
        if pair:
            details["change_type"] = "effectivity"
            for name, value in (("old_effective_date", pair[1]), ("effective_date", pair[5])):
                if parsed := _iso(re.fullmatch(DATE, value)):
                    details[name] = parsed
        else:
            for name, label in (("old_effective_date", "原生效"), ("effective_date", "新生效")):
                match = re.search(rf"{label}(?:日(?:期)?)?\s*(?:为|是|[:=])?\s*{DATE}", message)
                if match and (parsed := _iso(match)):
                    details[name] = parsed
                    details["change_type"] = "effectivity"
            if "old_effective_date" in details and not re.search(r"新生效", message):
                details.pop("effective_date", None)

    if expected == "old_effective_date" and re.fullmatch(DATE, message):
        if parsed := _iso(re.fullmatch(DATE, message)):
            details.pop("effective_date", None)
            details["old_effective_date"] = parsed
    correction = re.fullmatch(rf"不是\s*({workflow.ID_WORD})\s*[,，、]\s*(?:是|而是)\s*({workflow.ID_WORD})", message)
    if correction:
        candidates = [field for field in FIELD_HINTS if (
            str(draft.get(field, "")).casefold() == correction[1].casefold()
            if field in ("old_component", "new_component") else draft.get(field) == correction[1]
        )]
        if len(candidates) == 1:
            details[candidates[0]] = correction[2]
    if not details:
        if expected in FIELD_HINTS and re.fullmatch(workflow.ID_WORD, message):
            details[expected] = message
        elif expected in {"old_quantity", "new_quantity"} and re.fullmatch(NUMBER, message):
            details[expected] = float(message)
    for field in ("old_component", "new_component"):
        if field in details:
            details[field] = details[field].upper()
    return details


def _missing(draft):
    issues = []
    normalized = workflow._values(draft, issues)
    workflow._required(normalized, issues)
    missing = [item["field"] for item in issues if item["field"]]
    if not draft.get("analysis_mode") and any(draft.get(field) for field in SCOPE_FIELDS):
        missing.insert(0, "analysis_mode")
    if not draft.get("role"):
        missing.insert(0, "role")
    return list(dict.fromkeys(missing))


def collect(messages):
    if not isinstance(messages, list) or not 0 < len(messages) <= MAX_TURNS:
        raise ValueError("请发送 1 到 30 条对话消息")
    if any(not isinstance(message, str) or not message.strip() or len(message) > MAX_MESSAGE for message in messages):
        raise ValueError("每条消息须包含 1 到 500 个字符")
    draft, cleared, last = {}, set(), {}
    missing = _missing(draft)
    for raw in messages:
        message = unicodedata.normalize("NFKC", raw).strip().rstrip("。！？")
        last = _new_details(message, draft, missing[0] if missing else None)
        if "target_bom_row" not in last and any(
            field in last and field in draft and last[field] != draft[field] for field in SCOPE_FIELDS
        ):
            draft.pop("target_bom_row", None)
            cleared.add("target_bom_row")
        cleared.difference_update(last)
        for field, value in last.items():
            if value == "":
                draft.pop(field, None)
                cleared.add(field)
            else:
                draft[field] = value
        if draft.get("change_type"):
            for field, changes in CHANGE_FIELDS.items():
                if draft["change_type"] not in changes:
                    draft.pop(field, None)
        if draft.get("analysis_mode") == "material":
            for field in (*SCOPE_FIELDS, "target_bom_row"):
                draft.pop(field, None)
        missing = _missing(draft)
    rows = []
    for field, label in workflow.LABELS.items():
        if field in draft:
            value = draft[field]
            display = workflow.ROLES[value][0] if field == "role" else workflow._display_field(field, value)
            rows.append({"field": field, "label": label, "display": display})
    recognized = [str(row["display"]) if row["field"] in ("role", "change_type")
                  else f'{row["label"]} {row["display"]}' for row in rows if row["field"] in last]
    understood = "已识别" + "、".join(recognized) + "。" if recognized else "可以补充或更正已整理的字段。"
    question = PROMPTS.get(missing[0], "请核对已整理的字段。确认后可一键填入核查表。") if missing else (
        "必填项已整理好。核对后可一键填入核查表，再做数据预检。"
    )
    focus = [word for word in workflow.FOCUSES if any(word.casefold() in message.casefold() for message in messages)]
    return {"fields": draft, "rows": rows, "cleared": sorted(cleared), "missing": missing,
            "reply": f"{understood}{question}", "focus": focus}
