"""Fixed input, evidence and output for a role-based BOM review."""
from __future__ import annotations

import json
import math
import re
import unicodedata
from datetime import date

from . import approval, department_intent, engineering, knowledge, knowledge_store, procurement
from .normalization import coerce_date, coerce_number
from .roles import LABELS as ROLES

CONTRACT = "bom-workflow-v1"
SCHEMA = """
CREATE TABLE IF NOT EXISTS workflow_runs (
    run_id TEXT PRIMARY KEY,
    result_json TEXT NOT NULL,
    report_markdown TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS engineering_assessments (
    run_id TEXT PRIMARY KEY REFERENCES workflow_runs(run_id) ON DELETE CASCADE,
    reference_json TEXT NOT NULL
);
"""
ENGINEERING_SEARCH_TERMS = "工程版本 EBOM BOM行 生效 替换 路径 版本引用 变更"
STANDALONE_ROLE_LABELS = {"planning": "计划", "warehouse": "仓储", "service": "售后"}
KEYWORDS = {
    "rd": ("研发", "设计", "用在哪里", "父件", "互换"),
    "procurement": ("采购", "承诺", "到货", "收货", "退货", "供应商"),
    "sales": ("销售", "交期", "交货", "客户需求", "订单行"),
    "manufacturing": ("制造", "生产", "工单", "领料", "投料", "报工"),
    "engineering": ("工程", "ebom", "版本引用", "生效", "bom 行", "bom行"),
    "planning": ("物料计划", "物控", "齐套", "切换缺口"),
    "warehouse": ("仓储", "仓库", "物流", "实物盘点", "隔离库"),
    "quality": ("质量", "质检", "复验", "检验放行"),
    "service": ("售后", "维修备件", "存量设备"),
}
FOCUSES = ("采购承诺", "到货", "退货", "冲销", "供应来源", "交期", "配置",
           "投料", "领料", "工单", "报工", "版本引用", "BOM 行", "用量", "生效", "替换")
FOCUSES += tuple(dict.fromkeys(topic for topics in department_intent.TOPICS.values()
                              for topic, _, _ in topics))
LABELS = {
    "role": "业务角色", "analysis_mode": "核查范围", "change_type": "变更类型", "old_component": "原组件",
    "new_component": "新组件", "target_parent": "目标父件",
    "effective_date": "拟生效日期", "old_effective_date": "原生效日期",
    "product": "成品", "plant": "工程版本工厂", "engineering_version": "工程版本",
    "target_bom_row": "目标 BOM 行号", "old_quantity": "原用量",
    "new_quantity": "新用量", "planning_plant": "采购工厂", "storage_location": "采购库位",
}
CHANGE_TYPES = {
    **engineering.CHANGE_TYPES, "add": "新增组件", "effectivity": "改变生效日期",
}
IDENTITY_FIELDS = (
    "old_component", "new_component", "target_parent", "product", "plant",
    "engineering_version", "target_bom_row", "planning_plant", "storage_location",
)
ID_WORD = r"[A-Za-z0-9][A-Za-z0-9_.-]{0,99}"
ROLE_LABELS = {label: key for key, label in ROLES.items()}
SCOPE_HINTS = {
    "product": r"成品(?:编号|料号)?",
    "plant": r"工厂(?:代码)?",
    "engineering_version": r"(?:工程版本|EBOM\s*版本)",
    "target_bom_row": r"BOM\s*行(?:号)?",
}


PART = rf"(?<![A-Za-z0-9_./-])({ID_WORD})(?![A-Za-z0-9_./-])"
MATERIAL_SUFFIX = r"(?:\s*(?:这(?:颗|个|款|种)(?:料(?:件)?|物料|组件)|组件|物料|料件|采购件))?"
MATERIAL_LABEL = r"(?:(?:原|新|旧)?(?:物料|组件|料号|采购件|料件|件|料)\s*)?"
ALTERNATIVE = r"\s*(?:或者|或|还是|、)\s*"
NEGATION = r"(?:先)?(?:不(?:要|能|想|打算|考虑|再)?|无需|暂不|取消)\s*$"
SUPPLY_TIME = r"(?:已经|今天|明天|后天|本周|这周|目前|暂时|现在|这批|20\d{2}[-年/]\d{1,2}[-月/]\d{1,2}日?)?"
SHORTAGE = (r"(?:到不了货?|到不(?:了|齐)|到货晚|到货延期|没(?:有)?到货|不能到货|交不了货|"
            r"延迟到货|晚到货|停供|断供|缺货|断货|缺料|短缺|买不到|采购不到|采购来不及|"
            r"(?:要到|要等到|得等到)\s*20\d{2}[-年/]\d{1,2}[-月/]\d{1,2}日?\s*才(?:能)?(?:到货|交货)|"
            r"(?:无法|不能|来不及|没法)(?:按时|及时|按期)?(?:采购|到货|供应|交货))")
FORWARD = r"(?:替换为|替换成|更换为|更换成|替代为|换为|换成|(?<![替更交调])换(?![料掉下到])|改用|改成|改采|改订)"
SUBSTITUTE = r"(?:替代|代替|替换|取代|顶替|替(?![代换]))"
USING = r"(?:使用|采用|选用|拿|用)"
REPLACEMENT_LABEL = r"(?:替代(?:采购件|物料|料号|件|料)|替换件|备选料号)"
FIELD_UPDATE = (
    r"(?:(?:原|旧|新)(?:组件|物料|料号|料|件)|日期|生效日|生效|工厂|库位|用量|单耗|版本号?|行号|角色|成品)"
    rf"(?:\s*(?:从|由)(?:\s*{ID_WORD})?)?\s*$"
)
NON_MATERIAL = r"(?:成品(?:编号|料号)?|(?:采购)?工厂|库位|版本(?:号)?|(?:采购|销售|生产)?(?:订单|单号|工单)|BOM\s*行(?:号)?)\s*[:=]?\s*$"


def _replacement(question):
    # An explicit pair has priority over a shortage mentioned for background.
    pairs, proposed, shortages = set(), set(), set()
    patterns = (
        (rf"{USING}\s*{MATERIAL_LABEL}{PART}{MATERIAL_SUFFIX}\s*(?:来)?{SUBSTITUTE}\s*{MATERIAL_LABEL}{PART}", 2, 1),
        (rf"{PART}{MATERIAL_SUFFIX}\s*{FORWARD}\s*{MATERIAL_LABEL}{PART}", 1, 2),
        (rf"{USING}\s*{MATERIAL_LABEL}{PART}{MATERIAL_SUFFIX}\s*(?:来)?{SUBSTITUTE}(?!\s*{ID_WORD})", 0, 1),
        (rf"(?:{FORWARD}|{REPLACEMENT_LABEL}\s*(?:是|为|用|[:=])?)\s*{MATERIAL_LABEL}{PART}", 0, 1),
    )
    ambiguous_target = False
    for pattern, old_index, new_index in patterns:
        for match in re.finditer(pattern, question):
            before = question[:match.start()].rstrip()
            if re.search(NEGATION, before) or re.search(FIELD_UPDATE, before):
                continue
            new = match[new_index]
            if re.fullmatch(r"20\d{2}-\d{1,2}-\d{1,2}", new):
                continue
            if old_index:
                old = match[old_index]
                if (re.fullmatch(r"20\d{2}-\d{1,2}-\d{1,2}", old)
                        or re.search(NON_MATERIAL, question[:match.start(old_index)])):
                    continue
                pairs.add(old.upper())
                alternatives = question[match.end(old_index):] if old_index > new_index else ""
                extra_old = re.match(rf"{MATERIAL_SUFFIX}{ALTERNATIVE}{MATERIAL_LABEL}{PART}", alternatives)
                if extra_old:
                    pairs.add(extra_old[1].upper())
            proposed.add(new.upper())
            tail = question[match.end(new_index):]
            ambiguous_target |= bool(re.match(rf"{MATERIAL_SUFFIX}{ALTERNATIVE}{MATERIAL_LABEL}{PART}", tail))
    for match in re.finditer(rf"{PART}{MATERIAL_SUFFIX}\s*{SUPPLY_TIME}\s*(?:都|均|也)?\s*{SHORTAGE}", question):
        if re.search(NON_MATERIAL, question[:match.start()]):
            continue
        shortages.add(match[1].upper())
        previous = re.search(rf"{PART}{MATERIAL_SUFFIX}\s*(?:和|与|及|、)\s*$", question[:match.start()])
        if previous:
            shortages.add(previous[1].upper())

    draft = {}
    if subjects := pairs or shortages:
        draft["old_component"] = next(iter(subjects)) if len(subjects) == 1 else ""
    choice = re.search(rf"(?:选(?:用)?|考虑用)\s*{PART}{ALTERNATIVE}{PART}\s*(?:来)?{SUBSTITUTE}", question)
    if proposed or choice:
        draft["change_type"] = "replace"
        draft["new_component"] = next(iter(proposed)) if len(proposed) == 1 and not (ambiguous_target or choice) else ""
    return draft


def _suggest(question):
    question = unicodedata.normalize("NFKC", question)
    draft = {}
    dates = re.findall(r"(?<!\d)(20\d{2})[-年/](\d{1,2})[-月/](\d{1,2})(?:日)?(?!\d)", question)
    if len(dates) == 1:
        try:
            draft["effective_date"] = date(*map(int, dates[0])).isoformat()
        except ValueError:
            pass
    replace = _replacement(question)
    remove = re.search(rf"(?:删除|移除)\s*({ID_WORD})", question)
    if replace:
        draft.update(replace)
    if remove and "change_type" not in draft:
        draft.update(change_type="remove", old_component=remove[1])
    for field, label in SCOPE_HINTS.items():
        candidates = re.findall(rf"{label}\s*[:=]?\s*({ID_WORD})", question, re.IGNORECASE)
        if len(candidates) == 1:
            draft[field] = candidates[0]
    if "engineering_version" in draft or "target_bom_row" in draft:
        draft["analysis_mode"] = "engineering"
    return draft


ROLE_ALIASES = {**ROLE_LABELS, "生产": "manufacturing", "生产现场": "manufacturing",
                "制造工程": "manufacturing", "ME": "manufacturing",
                "物料计划": "planning", "计划": "planning", "物控": "planning", "PMC": "planning",
                "工程研发": "engineering", "物料管理": "planning", "物料": "planning",
                "仓储": "warehouse", "物流": "warehouse", "仓库": "warehouse",
                "品保": "quality", "质检": "quality", "QA": "quality", "QC": "quality",
                "售后": "service", "服务": "service",
                "订单管理": "sales"}
ROLE_PATTERN = "|".join(sorted(ROLE_ALIASES, key=len, reverse=True))


def explicit_role(question):
    candidates = set()
    for pattern in (
        rf"(?:从|切换到|改用|换个?|站在)\s*({ROLE_PATTERN})\s*(?:的?视角|的?角度|这边看|看)",
        rf"(?:我是|我这边是|我负责|角色\s*[:=]?)\s*({ROLE_PATTERN})(?=部门|这边|部|[,，。\s]|$)",
        rf"(?:^|[,，。;；])\s*(?:我们)?({ROLE_PATTERN})(?=\s*(?:角度|视角|看一下|来看|这边|部门|部[,，。\s]|[,，:：]|$))",
    ):
        candidates.update(ROLE_ALIASES[match[1]] for match in re.finditer(pattern, question))
    # Manufacturing engineering also performs explicit engineering-version reviews.
    if (candidates == {"manufacturing"} and re.match(r"^制造工程[,，:\s]", question)
            and re.search(r"工程版本.*BOM\s*行", question, re.I)
            and not re.search(r"工单|领料|在制|工序", question)):
        return {"engineering"}
    return candidates


def _role(question):
    explicit = explicit_role(question)
    if explicit:
        return next(iter(explicit)) if len(explicit) == 1 else ""
    matches = [key for key, words in KEYWORDS.items()
               if any(word in question.casefold() for word in words)]
    return matches[0] if len(matches) == 1 else ""


def _snapshot(repository):
    stats = repository.stats()
    with repository.connect() as connection:
        row = connection.execute("SELECT value FROM meta WHERE key='snapshot_ready_at'").fetchone()
        versions = engineering.VersionSnapshot(connection).catalog() if row else []
    return {
        "id": row[0] if row else "",
        "source_count": stats["source_count"],
        "expanded_bom_rows": stats["valid_bom_rows"],
        "excluded_bom_rows": stats["excluded_bom_rows"],
        "engineering_versions": len(versions),
        "sales_order_rows": stats["sales_items"],
        "production_orders": stats["production_orders"],
        "sources": stats["sources"],
    }


def _issue(issues, code, message, field=None, level="blocking"):
    issues.append({"code": code, "message": message, "field": field, "level": level})


def _values(draft, issues):
    if not isinstance(draft, dict):
        raise ValueError("核查参数须为对象")
    mode = draft.get("analysis_mode") or "material"
    if mode not in ("material", "engineering"):
        raise ValueError("请选择物料或工程版本核查")
    change = draft.get("change_type") or ""
    if not isinstance(change, str) or change and change not in CHANGE_TYPES:
        raise ValueError("变更类型无效")
    output = {"analysis_mode": mode, "change_type": change}
    relevant = set(IDENTITY_FIELDS)
    if mode == "material":
        relevant -= {"product", "plant", "engineering_version", "target_bom_row"}
    if change == "add":
        relevant.discard("old_component")
    if change not in {"add", "replace"}:
        relevant.discard("new_component")
    if mode == "material" and change != "add":
        relevant.discard("target_parent")
    for field in IDENTITY_FIELDS:
        if field in relevant:
            try:
                value = draft.get(field)
                if value is not None and not isinstance(value, str):
                    raise ValueError(f"{LABELS[field]}须为文本")
                output[field] = engineering.identifier(value, LABELS[field])
            except ValueError as exc:
                _issue(issues, "invalid_id", str(exc), field)
    date_fields = ("effective_date", "old_effective_date") if change == "effectivity" else ("effective_date",)
    for field in date_fields:
        raw = draft.get(field)
        output[field] = coerce_date(raw) if isinstance(raw, str) else None
        if raw and not output[field]:
            _issue(issues, "invalid_date", f"请填写有效的{LABELS[field]}", field)
    if change == "effectivity":
        output["new_effective_date"] = output["effective_date"]
        if output["effective_date"] and output["effective_date"] == output["old_effective_date"]:
            _issue(issues, "same_date", "新旧生效日期不能相同", "effective_date")
    if change == "quantity":
        for field in ("old_quantity", "new_quantity"):
            raw = draft.get(field)
            value = coerce_number(raw) if type(raw) in (int, float, str) else None
            output[field] = value if value is not None and math.isfinite(value) and value >= 0 else None
            if raw not in (None, "") and output[field] is None:
                _issue(issues, "invalid_quantity", f"{LABELS[field]}须为非负数字", field)
    return output


def _required(draft, issues):
    change = draft["change_type"]
    fields = ["change_type", "effective_date"]
    if draft["analysis_mode"] == "engineering":
        fields += ["product", "engineering_version", "target_bom_row"]
        if change and change not in engineering.CHANGE_TYPES:
            _issue(issues, "unsupported_change", "按工程版本核查支持替换、删除和用量调整", "change_type")
    if change:
        fields.append("target_parent" if change == "add" else "old_component")
    if change in {"add", "replace"}:
        fields.append("new_component")
    if change == "quantity":
        fields += ["old_quantity", "new_quantity"]
    if change == "effectivity":
        fields.append("old_effective_date")
    invalid = {item["field"] for item in issues}
    for field in fields:
        if (draft.get(field) is None or draft.get(field) == "") and field not in invalid:
            _issue(issues, "missing_field", f"请补充{LABELS[field]}", field)


def _preflight(repository, draft, snapshot, issues):
    strict = draft["analysis_mode"] == "engineering"
    if not snapshot["id"]:
        _issue(issues, "no_snapshot", "请先在数据准备导入并启用 BOM 数据")
    elif strict and not snapshot["engineering_versions"]:
        _issue(issues, "no_ebom", "当前资料缺少工程版本数据，请导入明确的 EBOM 版本、BOM 行和订单版本引用")
    elif not strict and not snapshot["expanded_bom_rows"]:
        _issue(issues, "no_expanded_bom", "当前快照没有普通展开 BOM，请使用工程版本核查或导入展开结构")
    if any(item["level"] == "blocking" for item in issues):
        return
    if strict:
        try:
            check = engineering.analyze(repository, draft)
        except ValueError as exc:
            _issue(issues, "ebom_scope_invalid", str(exc))
            return
        if check["path_issue"]:
            _issue(issues, "ebom_path_pending", check["path_issue"], level="review")
        if check["pending_references"]:
            _issue(issues, "order_references_pending",
                   f'{len(check["pending_references"])} 个订单的版本引用待核', level="review")
        pending_rows = sum(order["row_application"] == "UNKNOWN"
                           for order in check["version_reference_matches"])
        if pending_rows:
            _issue(issues, "order_rows_pending", f"{pending_rows} 个匹配订单的 BOM 行适用性待核", level="review")
        if check["path"] and not any(check[field] for field in (
            "version_reference_matches", "pending_references", "excluded_orders"
        )):
            _issue(issues, "order_references_missing", "当前快照未提供该成品的可核查订单版本引用", level="review")
    else:
        anchor = "target_parent" if draft["change_type"] == "add" else "old_component"
        material_key = repository.resolve_material(draft[anchor])
        with repository.connect() as connection:
            present = connection.execute("""
                SELECT 1 FROM bom_edges WHERE (parent_key=? OR component_key=?) AND date_valid=1 LIMIT 1
            """, (material_key, material_key)).fetchone()
        if not present:
            _issue(issues, "material_missing", "当前展开 BOM 中没有这个编号的有效结构记录", anchor)


# Evidence gaps are specific to the department and do not block BOM tracing.
DEPARTMENT_GAPS = {
    "planning": ("po_overlap", "在途与未交采购去重证据",
                 "需按采购订单行、发运批次与预计入库日期核对在途和未交数量。"),
    "warehouse": ("warehouse_stock", "WMS 库存、批次与实物状态",
                  "需补齐按物料、工厂、库位及批次的实物、在途、待检与隔离记录。"),
    "quality": ("quality_release", "质量验证与放行证据",
                "需补齐替代件检验、批次追溯、在制风险、返工验证及放行条件。"),
    "service": ("installed_base", "已交付设备与维修备件",
                "需补齐已交付设备序列号和版本、维修 BOM、备件库存与互换记录。"),
}


def _data_preparation(repository, context):
    draft, role, snapshot = (context[key] for key in ("draft", "role", "snapshot"))
    issues = context["issues"]
    codes = {issue["code"] for issue in issues}
    blocked = any(issue["level"] == "blocking" for issue in issues)
    fields = [{"key": "role", "label": LABELS["role"], "value": ROLES[role]}] if role in ROLES else []
    fields.extend({"key": key, "label": label, "value": _display_field(key, draft[key])}
                  for key, label in LABELS.items() if draft.get(key) not in (None, ""))
    sources = []

    def add(key, label, status, detail):
        sources.append({"key": key, "label": label, "status": status, "detail": detail})

    strict = draft["analysis_mode"] == "engineering"
    if strict:
        with repository.connect() as connection:
            counts = dict(connection.execute(
                "SELECT kind, COUNT(*) FROM engineering_evidence GROUP BY kind"
            )) if snapshot["id"] else {}
        add("ebom", "工程版本与目标 BOM 行",
            "需补资料" if not snapshot["engineering_versions"] else
            "待核" if blocked or "ebom_path_pending" in codes else "已定位",
            f'快照中有 {snapshot["engineering_versions"]} 个工程版本、{counts.get("BOM_ROW", 0)} 条 BOM 行。')
        references = counts.get("ORDER_REFERENCE", 0)
        add("references", "订单版本引用与 BOM 行适用性",
            "需补资料" if not references or "order_references_missing" in codes else "待核",
            f'快照中有 {references} 条订单引用、{counts.get("ORDER_ROW", 0)} 条行适用性记录。按所选版本和目标行核实归属。')
    else:
        rows = snapshot["expanded_bom_rows"]
        add("bom", "展开 BOM",
            "需补资料" if not rows else "未找到目标物料" if "material_missing" in codes else
            "已找到目标物料" if not blocked else "已导入，范围待核",
            f'快照中有 {rows} 条有效结构行。')
    if role in {"procurement", "planning", "warehouse"}:
        # These roles share the scoped evidence. Each has its own business decision.
        scope = {"old_component": "", "new_component": "", **draft}
        records = procurement.all_records(repository) if snapshot["id"] else []
        privacy = repository.privacy_key()
        matches = [(entry["kind"], procurement.match_scope(privacy, entry, scope)) for entry in records]
        for kind, key, label in (("PURCHASE_LINE", "purchases", "采购承诺明细"), ("RECEIPT", "receipts", "到货、退货与冲销明细")):
            if role == "warehouse" and kind != "RECEIPT":
                continue
            matched = [status for record_kind, status in matches if record_kind == kind and status]
            add(key, label, "需补资料" if not matched else "待核" if "范围待确认" in matched else "有相关记录",
                f'与所填物料及采购范围相关的记录 {len(matched)} 条。')
    elif role in {"sales", "manufacturing"}:
        key, label = ("sales_order_rows", "销售订单行") if role == "sales" else ("production_orders", "生产工单")
        count = snapshot[key]
        if not strict or count:
            add(key, label, "已导入，范围待核" if count else "需补资料", f'快照中有 {count} 条记录。')
    if role == "planning":
        scenario = None
        if draft["change_type"] == "replace" and not blocked:
            scenario = repository.planning_supply_scenario(
                draft["old_component"], draft["new_component"], draft["planning_plant"], draft["storage_location"]
            )
        add("planning_supply", "备料情景与切换前后需求", "待核" if scenario else "需补资料",
            "按物料、工厂与库位核对库存、在途、未交采购和需求口径。")
    if gap := DEPARTMENT_GAPS.get(role):
        key, label, detail = gap
        add(key, label, "需补资料", detail)
    if role in {"warehouse", "quality", "service"}:
        for index, (label, _, evidence) in enumerate(department_intent.TOPICS[role]):
            add(f"department_{index}", label, "需人工核对", evidence)
    return {"fields": fields, "sources": sources}


def prepare(repository, payload, *, session_scope=""):
    if not isinstance(payload, dict):
        raise ValueError("核查输入须为对象")
    question = payload.get("question") or ""
    if not isinstance(question, str) or len(question) > 500:
        raise ValueError("请用 500 字以内描述本次业务问题")
    issues = []
    role = payload.get("role") or ""
    if not isinstance(role, str):
        raise ValueError("业务角色无效")
    if not role:
        role = _role(question)
    if role not in ROLES:
        _issue(issues, "role_required", "请确认本次要从哪个业务角色核查", "role")
    source_draft = payload.get("draft", {})
    if not isinstance(source_draft, dict):
        raise ValueError("核查参数须为对象")
    requested = {**_suggest(question), **source_draft}
    draft = _values(requested, issues)
    if draft["analysis_mode"] == "material" and any(
        requested.get(field) for field in ("product", "plant", "engineering_version", "target_bom_row")
    ):
        _issue(issues, "material_scope", "按物料追溯会检查全部使用位置。需要限定工程范围时，请选择按工程版本和 BOM 行核查。",
               level="review")
    _required(draft, issues)
    snapshot = _snapshot(repository)
    _preflight(repository, draft, snapshot, issues)
    ready = all(item["level"] != "blocking" for item in issues)
    supplied_focus = payload.get("focus", [])
    if not isinstance(supplied_focus, list) or any(word not in FOCUSES for word in supplied_focus):
        raise ValueError("对话关注项无效")
    context = {"contract": CONTRACT, "role": role, "draft": draft, "snapshot": snapshot,
               "focus": [word for word in FOCUSES if word in supplied_focus or word.casefold() in question.casefold()],
               "issues": issues}
    return {"ready": ready, "context": context, "data_preparation": _data_preparation(repository, context),
            "confirmation_token": knowledge.sign(repository, "workflow" + session_scope, context) if ready else ""}


def confirm(repository, payload, *, session_scope=""):
    context = payload.get("context")
    if (
        payload.get("confirmed") is not True or not isinstance(context, dict)
        or context.get("contract") != CONTRACT
        or not knowledge.signed(repository, "workflow" + session_scope, context, payload.get("confirmation_token"))
    ):
        raise ValueError("请先预检并确认核查参数")
    if context["snapshot"]["id"] != _snapshot(repository)["id"]:
        raise ValueError("导入快照已变更，请重新预检")
    return context


def _order_answer(role, result, check):
    label, kind, items = ("销售订单行", "SALES", result["sales_orders"]) if role == "sales" else (
        "生产工单", "PRODUCTION", result["production_orders"]
    )
    if not check:
        lines = [f"按现有物料、订单关联与状态规则找到 {len(items)} 个{label}候选。"]
        if role == "sales":
            lines.extend(
                f'{entry["sales_order"]} / {entry["item"]} · 需求日 {entry["requested_date"] or "待核"}'
                f' · {entry["status"]} · {entry["source_id"]} 第 {entry["source_row"]} 行'
                for entry in items
            )
        else:
            lines.extend(
                f'{entry["production_order"]} · {entry["status"]} · {entry["source_id"]} 第 {entry["source_row"]} 行'
                for entry in items
            )
        return lines
    if not check["path"]:
        return [f"目标工程路径待核，暂未评估{label}的版本引用。"]
    buckets = []
    records = []
    for field in ("version_reference_matches", "excluded_orders", "pending_references"):
        orders = [order for order in check[field] if order["order_kind"] == kind]
        buckets.append(len(orders))
        records.extend(
            f'{engineering.reference_label(order)} · {order.get("review_status") or order.get("reason")}'
            f' · {engineering.evidence_text(order["evidence"])}'
            for order in orders
        )
    return [f"{label}版本引用命中 {buckets[0]} 个，排除 {buckets[1]} 个，引用待核 {buckets[2]} 个。", *records]


def _procurement_coverage(result):
    entries = [entry for entry in result["procurement_impact"]["lines"] if entry["kind"] == "PURCHASE_LINE"]
    return len(entries), sum(bool(entry["receipt_check"]["events"]) for entry in entries)


def _planning_facts(result, context):
    planning = result.get("planning_impact", {})
    scenario = planning.get("scenario", {})
    numbers = scenario.get("inputs", {})
    draft = context["draft"]
    scope = planning.get("scope", {}) if scenario.get("provided") else {}
    po_lines, _ = _procurement_coverage(result)
    lines = [
        f'备料范围 · 工厂 {scope.get("plant") or draft.get("planning_plant") or "待补"} · '
        f'库位 {scope.get("storage_location") or draft.get("storage_location") or "待补"}',
        f'关联采购订单行 {po_lines} 条，可用于逐行核对。',
        f'备料情景来源 · {scenario.get("origin") or "未提供"}',
    ]
    if order_metrics := planning.get("metrics"):
        lines.append(
            f'关联需求线索 · 生产工单 {order_metrics["affected_production_orders"]} 条 · '
            f'销售订单行 {order_metrics["affected_sales_items"]} 条'
        )
    for prefix, material, demand, label in (
        ("old", "旧料", "old_consumable_demand", "切换前可消耗需求"),
        ("new", "新料", "new_required_demand", "切换后所需需求"),
    ):
        lines.append(
            f'{material}情景 · 可用库存 {procurement.shown(numbers.get(prefix + "_on_hand"))} · '
            f'在途 {procurement.shown(numbers.get(prefix + "_in_transit"))} · '
            f'未交采购 {procurement.shown(numbers.get(prefix + "_open_po"))} · '
            f'{label} {procurement.shown(numbers.get(demand))}'
        )
    lines.append("计划结论待核。先按采购订单行核对在途和未交，再确认旧料剩余、新料缺口、齐套日期及切换方式。")
    return lines


def _answer(context, result, *, account_managed):
    role = context["role"]
    check = result.get("engineering_check")
    metrics = result["metrics"]
    if role in {"sales", "manufacturing"}:
        lines = _order_answer(role, result, check)
    elif role == "procurement":
        impact = result["procurement_impact"]
        direct = [entry for entry in impact["lines"] if entry["kind"] == "PURCHASE_LINE"]
        lines = [impact["status"] + "。",
                 f'已关联 {len(direct)} 条采购行和 {len(impact["lines"]) - len(direct)} 条范围汇总。']
        for entry in impact["lines"]:
            label = f'{entry["po"]} / {entry["item"]}' if entry["kind"] == "PURCHASE_LINE" else "范围汇总"
            lines.append(
                f'{label} · {entry["material"]} · {entry["match_status"]} · '
                f'剩余承诺 {procurement.shown(entry["O"])} {entry["unit"] or "单位待核"} · '
                f'到货 {entry["receipt_check"]["status"]} · {procurement.evidence_text(entry["evidence"])}'
            )
    elif role == "planning":
        lines = _planning_facts(result, context)
    elif role == "warehouse":
        po_lines, receipts = _procurement_coverage(result)
        lines = [f'关联采购订单行 {po_lines} 条，其中 {receipts} 条有到货事件明细。',
                 "收货凭证与实物可用库存分别核对。实物、批次、待检与隔离证据待补。"]
    elif role == "quality":
        validation = result["material_validation"]
        lines = [f'新组件主数据和寻源检查 · {validation["status"]}。',
                 "质量验证和放行证据待补。"]
    elif role == "service":
        lines = [f'当前 BOM 关联到 {metrics["top_materials"]} 个成品，可供维修备件范围核对。',
                 "已交付设备版本、维修 BOM 和备件互换性待核。"]
    elif check:
        lines = [
            check["path_issue"] or f'目标 BOM 行 {check["target"]["row_id"]} 的工程路径已定位。',
            f'版本引用命中 {len(check["version_reference_matches"])} 个，'
            f'排除 {len(check["excluded_orders"])} 个，待核 {len(check["pending_references"])} 个。'
            if check["path"] else "当前路径条件不足，暂未评估订单引用。",
        ]
        lines.extend(f'{edge["parent"]} → {edge["component"]} · {engineering.evidence_text(edge["evidence"])}'
                     for edge in check["path"])
    else:
        lines = [f'当前范围找到 {metrics["upstream_paths"]} 条上溯路径，涉及 {metrics["top_materials"]} 个成品。']
        if result["top_materials"]:
            lines.append("涉及成品 " + "、".join(result["top_materials"]))
    checklist = result.get("checklists", {})
    actions = {
        "rd": [*checklist.get("研发", []), "核对互换验证和配置限制，确认需要补做的设计验证。"],
        "engineering": ["核对受影响的 BOM 版本、产品及工厂，确认生效点和临时替代条件。"],
        "sales": ["回查订单行需求日期、配置及交付承诺，再由销售和计划复核。"],
        "procurement": [
            "对照采购行及收货凭证核对到货覆盖和剩余承诺。",
            "向供应商确认投产、发运、可改可撤数量、继续收货及可承诺日期。",
        ],
        "manufacturing": [*checklist.get("制造", []), "逐单核对实际领料、工序和在制进度，再确认改料、隔离或返工范围。"],
        "planning": ["按物料、工厂、库位和采购订单行核对未交与在途重叠，再确认备料缺口及切换方案。"],
        "warehouse": ["核对 WMS 库位、批次、待检和隔离状态，确认可用、退回或转用的范围。"],
        "quality": ["核对新料验证、在制品和库存批次，确认复验、返工、隔离或放行条件。"],
        "service": ["结合已交付设备版本、维修 BOM 和备件记录，确认替代兼容性与旧件保留范围。"],
    }[role]
    label = ROLES[role] if account_managed else STANDALONE_ROLE_LABELS.get(role, ROLES[role])
    return {"role": label, "facts": lines, "review_actions": actions}


def _report_text(value):
    # Keep human-reviewed text inert in a Markdown reader.
    return re.sub(r"([\\\x60*_{}\[\]()<>#!|~&])", r"\\\1", str(value).replace("\r", " "))


def _display_field(field, value):
    if field == "analysis_mode":
        return "按工程版本和 BOM 行" if value == "engineering" else "按物料追溯全部使用位置"
    return CHANGE_TYPES[value] if field == "change_type" else value


def finish(repository, context, item, *, account_managed=False):
    result = item["result"]
    answer = _answer(context, result, account_managed=account_managed)
    stages = [
        {"name": "输入确认", "status": "完成", "detail": f'{answer["role"]} · {CONTRACT}'},
        {"name": "数据预检", "status": "有待核项" if context["issues"] else "完成",
         "detail": f'{context["snapshot"]["source_count"]} 个来源'},
        {"name": "规则核查", "status": "已计算", "detail": result["analysis_id"]},
        {"name": "角色核查结果", "status": "已生成", "detail": answer["role"]},
    ]
    output = {"run_id": result["analysis_id"], "context": context, "steps": stages, "answer": answer}
    report = _workflow_report(output, item["report"])
    with repository.connect() as connection:
        connection.execute("INSERT INTO workflow_runs VALUES (?, ?, ?)",
                           (output["run_id"], json.dumps(output, ensure_ascii=False), report))
        # A workflow confirms one change. Restore that same report on page reload.
        connection.execute("UPDATE meta SET value=? WHERE key='last_analysis_id'", (output["run_id"],))
    return {**output, "report": report}


def _workflow_report(output, base_report):
    context, stages, answer = (output[key] for key in ("context", "steps", "answer"))
    lines = ["# BOM 工作流核查", "", f'- 工作流 {CONTRACT} · {output["run_id"]}',
             f'- 快照 {context["snapshot"]["id"]}',
             "", "## 执行步骤", "",
             *[f'- {stage["name"]} · {stage["status"]} · {stage["detail"]}' for stage in stages],
             "", "## 已确认输入", "",
             *[f'- {LABELS.get(field, field)} · {_report_text(_display_field(field, value))}'
               for field, value in context["draft"].items() if value not in (None, "")],
             "", "## 预检待核", "",
             *([f'- {_report_text(issue["message"])}' for issue in context["issues"]] or ["- 无"]),
             "", f'## {answer["role"]}核查', "", *[f"- {_report_text(line)}" for line in answer["facts"]],
             "", *[f"- 待业务复核 · {_report_text(line)}" for line in answer["review_actions"]],
             ""]
    return "\n".join(lines) + "\n" + base_report



def get_run(repository, run_id=""):
    with repository.connect() as connection:
        if run_id:
            row = connection.execute(
                "SELECT result_json, report_markdown FROM workflow_runs WHERE run_id=?", (run_id,)
            ).fetchone()
        else:
            row = connection.execute(
                "SELECT result_json, report_markdown FROM workflow_runs ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
    if row is None:
        return None
    output = json.loads(row[0])
    if "knowledge" not in output:
        return {**output, "report": row[1]}
    # Preserve historical rows while serving only the standard workflow output.
    output.pop("knowledge")
    output["context"].pop("knowledge_revision", None)
    output["answer"].pop("knowledge_references", None)
    output["steps"] = [step for step in output["steps"] if step["name"] != "知识检索"]
    analysis = repository.analysis_by_id(output["run_id"])
    return {**output, "report": _workflow_report(output, analysis[1] if analysis else "")}


def engineering_context(repository, run_id):
    if not run_id:
        return None
    with repository.connect() as connection:
        row = connection.execute("SELECT result_json FROM workflow_runs WHERE run_id=?", (run_id,)).fetchone()
    context = json.loads(row[0])["context"] if row else None
    return context if context and context["role"] == "engineering" else None


def _approval_review(repository, claim, section):
    result = knowledge_store.approval_material(repository, claim.approval_id, claim.search_text(), section)
    related = result.pop("related")
    materials = {hit["source_id"]: {key: hit[key] for key in ("source_id", "section_label", "title")}
                 for hit in (*result["matches"], *related)}
    return {**result, "approval": {**approval.assess(claim, related), "materials": list(materials.values()),
                                   "section": section}}


def engineering_references(repository, context, query="", section="", *, approval_scope=None):
    if not isinstance(query, str):
        raise ValueError("搜索问题须为文本")
    if len(query) > 240:
        raise ValueError("搜索问题最多 240 字")
    automatic = not query.strip()
    claim = approval.Claim.confirmed(approval_scope) if automatic and approval_scope is not None else approval.Claim.from_question(query)
    if claim:
        return _approval_review(repository, claim, section)
    if automatic:
        draft = context["draft"]
        terms = [ENGINEERING_SEARCH_TERMS, *context["focus"], CHANGE_TYPES[draft["change_type"]]]
        terms.extend(str(draft.get(k) or "") for k in
                     ("old_component", "new_component", "engineering_version", "target_bom_row"))
        query = " ".join(terms)[:240]
    return knowledge_store.retrieve(repository, query, section, context_query=automatic)


ASSESSMENT_TITLE = "工程辅助推论（待复核）"
REFERENCE_PATTERN = re.compile(r"[0-9a-f]{64}:[1-9][0-9]{0,4}:[1-9][0-9]{0,4}:[0-9a-f]{64}")
ASSESSMENT_RULES = (
    (r"退货|冲销|已收|到货|在途|未交|采购承诺", "按本次组件对应的采购订单行核对承诺、净到货及剩余量，同一订单行的在途量和未到货量按来源口径去重。"),
    (r"版本引用|EBOM|BOM\s*行", "核对本次目标 BOM 行的适用条件与订单实际引用版本，按订单逐行确认是否适用该条要求。"),
    (r"领料|投料|返工|工单|在制", "对照本次关联工单的领料、投料与执行状态，确认是否具备应用该条处置建议的条件。"),
    (r"生效|切换|旧料", "对照本次拟生效日期和订单执行状态，复核该条要求的切换条件及旧料处置范围。"),
    (r"互换|验证|测试|规格|尺寸", "核对本次组件和配置是否满足该条验证条件，补齐验证证据后再判断适用性。"),
)


def engineering_assessment(repository, run_id, context, references=None, *, approval_review=None):
    if references is not None:
        if (not isinstance(references, list) or len(references) > 3
                or any(not isinstance(ref, str) or not REFERENCE_PATTERN.fullmatch(ref) for ref in references)
                or len(set(references)) != len(references)):
            raise ValueError("请选择最多三个不同的已审核片段")
    save = references is not None
    with repository.connect() as db:
        if not save:
            row = db.execute("SELECT reference_json FROM engineering_assessments WHERE run_id=?", (run_id,)).fetchone()
            saved = json.loads(row[0]) if row else []
            if isinstance(saved, dict):
                references, approval_review = saved["references"], saved["approval_review"]
            else:
                references = saved
        review = None
        if approval_review is not None:
            if not isinstance(approval_review, dict) or references:
                raise ValueError("请单独录用审批核查结果")
            claim = approval.Claim.confirmed(approval_review.get("claim"))
            selected_section = approval_review.get("section", "")
            review = _approval_review(repository, claim, selected_section)["approval"]
            approval_review = {"claim": claim.values(), "section": selected_section}
        hits = knowledge_store.resolve_references(repository, references)
        missing = len(references) - len(hits)
        if save:
            if missing:
                raise ValueError("部分片段已重新审核，请重新检索后选择")
            saved = {"references": references, "approval_review": approval_review} if review else references
            db.execute("""
                INSERT INTO engineering_assessments VALUES (?, ?)
                ON CONFLICT(run_id) DO UPDATE SET reference_json=excluded.reference_json
            """, (run_id, json.dumps(saved)))
    findings = []
    for hit in hits:
        check = next((action for pattern, action in ASSESSMENT_RULES
                      if re.search(pattern, hit["text"], re.IGNORECASE)),
                     "对照本次变更的适用物料、范围和生效条件，复核该片段是否适用，记录需补充的工程证据。")
        position = "段" if hit["kind"] == "text" else "页"
        citation = " · ".join(filter(None, (hit["section_label"], hit["title"], hit["source_id"])))
        citation += f' · 第 {hit["slide"]} {position} · 片段 {hit["chunk"]}'
        findings.append({"citation": citation, "excerpt": hit["text"], "review": check})
    draft = context["draft"]
    fields = ("old_component", "new_component", "effective_date", "product", "engineering_version", "target_bom_row")
    scope = " · ".join([CHANGE_TYPES[draft["change_type"]], *(
        f"{LABELS[field]} {draft[field]}" for field in fields if draft.get(field)
    )])
    run = get_run(repository, run_id)
    notice = f"{missing} 条片段已变更或撤回，请重新选择。" if missing else ""
    return {"title": ASSESSMENT_TITLE, "scope": scope,
            "facts": run["answer"]["facts"][:2], "findings": findings, "notice": notice,
            **({"approval": review} if review else {})}


APPROVAL_EVIDENCE_LABELS = {"support": "支持依据", "exclusion": "排除依据", "facts": "已核实事实与关联记录"}


def engineering_report(base_report, assessment):
    lines = ["", "", f'## {assessment["title"]}', "",
             f'- 核查范围 · {_report_text(assessment["scope"])}']
    lines.extend(f"- 规则依据 · {_report_text(fact)}" for fact in assessment["facts"])
    if assessment["notice"]:
        lines.append(f'- {assessment["notice"]}')
    for item in assessment["findings"]:
        excerpt = " ".join(item["excerpt"].split())
        lines += ["", f'- 工程依据 · {_report_text(item["citation"])}',
                  f"  - 摘录 · {_report_text(excerpt)}", f'  - 待复核 · {item["review"]}']
    if review := assessment.get("approval"):
        claim = review["claim"]
        scope = [claim["approval_id"], claim["material_id"], claim["operation"], claim["line"],
                 claim["as_of"], claim["batch_id"] or "批次未指定"]
        lines += ["", "### 审批效力核查", "",
                  f'- 待核主张 · {_report_text(" · ".join(scope))}',
                  f'- 结论 · {review["state"]}', f'- 理由 · {review["reason"]}']
        if review["missing"]:
            lines.append(f'- 待确认 · {review["missing"]}')
        for material in review["materials"]:
            label = " · ".join((material["section_label"], material["title"], material["source_id"]))
            lines.append(f'- 找到的材料 · {_report_text(label)}')
        for role, label in APPROVAL_EVIDENCE_LABELS.items():
            for hit in review[role]:
                unit = "段" if hit["kind"] == "text" else "页"
                position = f'第 {hit["slide"]} {unit} · 片段 {hit["chunk"]}'
                citation = " · ".join((hit["section_label"], hit["title"], hit["source_id"], position))
                excerpt = " ".join(hit["text"].split())
                lines += [f'- {label} · {_report_text(citation)}', f'  - 摘录 · {_report_text(excerpt)}']
    return base_report.rstrip() + "\n".join(lines) + "\n"
