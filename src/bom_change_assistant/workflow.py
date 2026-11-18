"""Fixed input, evidence and output for a role-based BOM review."""
from __future__ import annotations

import json
import math
import re
import unicodedata
from datetime import date

from . import engineering, knowledge, procurement
from .normalization import coerce_date, coerce_number

CONTRACT = "bom-workflow-v1"
SCHEMA = """
CREATE TABLE IF NOT EXISTS workflow_runs (
    run_id TEXT PRIMARY KEY,
    result_json TEXT NOT NULL,
    report_markdown TEXT NOT NULL
);
"""
ROLES = {
    "rd": ("研发", "研发 设计验证 BOM结构 组件 父件 替换 互换 路径 用量"),
    "procurement": ("采购", "采购 承诺 到货 收货 冲销 退货 供应来源 未交 可取消"),
    "sales": ("销售", "销售订单 订单行 需求日期 交期 配置 项目 版本引用"),
    "manufacturing": ("制造", "制造 生产工单 工单 领料 投料 报工 生产状态 完工"),
    "engineering": ("工程", "工程版本 EBOM BOM行 生效 替换 路径 版本引用 变更"),
}
KEYWORDS = {
    "rd": ("研发", "设计", "用在哪里", "父件", "互换"),
    "procurement": ("采购", "承诺", "到货", "收货", "退货", "供应商"),
    "sales": ("销售", "交期", "交货", "客户需求", "订单行"),
    "manufacturing": ("制造", "生产", "工单", "领料", "投料", "报工"),
    "engineering": ("工程", "ebom", "版本引用", "生效", "bom 行", "bom行"),
}
FOCUSES = ("采购承诺", "到货", "退货", "冲销", "供应来源", "交期", "配置",
           "投料", "领料", "工单", "报工", "版本引用", "BOM 行", "用量", "生效", "替换")
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
ROLE_LABELS = {label: key for key, (label, _) in ROLES.items()}
SCOPE_HINTS = {
    "product": r"成品(?:编号|料号)?",
    "plant": r"工厂(?:代码)?",
    "engineering_version": r"(?:工程版本|EBOM\s*版本)",
    "target_bom_row": r"BOM\s*行(?:号)?",
}


def _replacement(question):
    part = rf"(?<![A-Za-z0-9_./-])({ID_WORD})(?![A-Za-z0-9_./-])"
    when = r"(?:已经|今天|明天|后天|本周|这周|目前|暂时|现在|这批|20\d{2}[-年/]\d{1,2}[-月/]\d{1,2}日?)?"
    shortage = (r"(?:到不了货?|到不(?:了|齐)|到货晚|到货延期|没(?:有)?到货|不能到货|交不了货|"
                r"停供|缺货|断货|缺料|短缺|买不到|采购不到|采购来不及|"
                r"(?:要到|要等到|得等到)\s*20\d{2}[-年/]\d{1,2}[-月/]\d{1,2}日?\s*才(?:能)?(?:到货|交货)|"
                r"(?:无法|不能|来不及|没法)(?:按时|及时|按期)?(?:采购|到货|供应|交货))")
    material_label = r"(?:(?:原|新|旧)?(?:物料|组件|料号|采购件)\s*)?"
    forward = r"(?:替换为|替换成|更换为|更换成|替代为|换为|换成|改用|改成|改采|改订)"
    substitute = r"(?:替代|代替|替换|取代)"
    label = r"(?:替代(?:采购件|物料|料号|件|料)|替换件|备选料号)"
    old_parts, new_parts = set(), set()
    ambiguous_target = False
    for match in re.finditer(rf"{part}\s*{when}\s*{shortage}", question):
        old_parts.add(match[1].upper())

    # Bind the complement to the change verb even when the subject was mentioned
    # in a preceding supply clause or an earlier turn.
    patterns = (
        (rf"{part}\s*{forward}\s*{material_label}{part}", 1, 2),
        (rf"(?:用|采用|选用)\s*{part}\s*(?:来)?{substitute}\s*{part}", 2, 1),
        (rf"(?:用|采用|选用)\s*{part}\s*(?:来)?{substitute}(?!\s*{ID_WORD})", 0, 1),
        (rf"(?:{forward}|{label}\s*(?:是|为|用|[:=])?)\s*{material_label}{part}", 0, 1),
    )
    for pattern, old_index, new_index in patterns:
        for match in re.finditer(pattern, question):
            prefix = question[:match.start()].rstrip()
            if re.search(r"(?:不|不要|不能|不想|不打算|无需|不考虑)$", prefix):
                continue
            # Field corrections like “拟生效日改成 …” must retain their own meaning.
            if not old_index and re.search(r"(?:(?:原|旧|新)(?:组件|物料|料号|件)|日期|生效日|生效|工厂|库位|用量|版本号?|行号|角色|成品)$", prefix):
                continue
            if old_index:
                old = match[old_index]
                if not re.fullmatch(r"20\d{2}-\d{1,2}-\d{1,2}", old):
                    old_parts.add(old.upper())
            new_parts.add(match[new_index].upper())
            tail = question[match.end(new_index):]
            if re.match(rf"\s*(?:或(?:者)?|、|还是)\s*{ID_WORD}", tail):
                ambiguous_target = True

    draft = {}
    if len(old_parts) == 1:
        draft["old_component"] = next(iter(old_parts))
    if new_parts:
        draft["change_type"] = "replace"
        # An unresolved choice withdraws any previously proposed replacement.
        draft["new_component"] = next(iter(new_parts)) if len(new_parts) == 1 and not ambiguous_target else ""
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


def _role(question):
    preferred = re.search(r"(?:从\s*(研发|采购|销售|制造|工程)\s*(?:角度|视角)|我是\s*(研发|采购|销售|制造|工程))", question)
    if preferred:
        return ROLE_LABELS[preferred[1] or preferred[2]]
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


def _data_preparation(repository, context):
    draft, role, snapshot = (context[key] for key in ("draft", "role", "snapshot"))
    issues = context["issues"]
    codes = {issue["code"] for issue in issues}
    blocked = any(issue["level"] == "blocking" for issue in issues)
    fields = [{"key": "role", "label": LABELS["role"], "value": ROLES[role][0]}] if role in ROLES else []
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
    if role == "procurement":
        # The same scope used by the procurement rules, including plant and location.
        scope = {"old_component": "", "new_component": "", **draft}
        records = procurement.all_records(repository) if snapshot["id"] else []
        privacy = repository.privacy_key()
        matches = [(entry["kind"], procurement.match_scope(privacy, entry, scope)) for entry in records]
        for kind, key, label in (("PURCHASE_LINE", "purchases", "采购承诺明细"), ("RECEIPT", "receipts", "到货、退货与冲销明细")):
            matched = [status for record_kind, status in matches if record_kind == kind and status]
            add(key, label, "需补资料" if not matched else "待核" if "范围待确认" in matched else "有相关记录",
                f'与所填物料及采购范围相关的记录 {len(matched)} 条。')
    elif role in {"sales", "manufacturing"}:
        key, label = ("sales_order_rows", "销售订单行") if role == "sales" else ("production_orders", "生产工单")
        count = snapshot[key]
        if not strict or count:
            add(key, label, "已导入，范围待核" if count else "需补资料", f'快照中有 {count} 条记录。')
    return {"fields": fields, "sources": sources}


def prepare(repository, payload):
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
            "confirmation_token": knowledge.sign(repository, "workflow", context) if ready else ""}


def confirm(repository, payload):
    context = payload.get("context")
    if (
        payload.get("confirmed") is not True or not isinstance(context, dict)
        or context.get("contract") != CONTRACT
        or not knowledge.signed(repository, "workflow", context, payload.get("confirmation_token"))
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


def _answer(role, result):
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
        "rd": checklist.get("研发", []),
        "engineering": checklist.get("研发", []),
        "manufacturing": checklist.get("制造", []),
        "sales": ["回查订单行需求日期、配置及交付承诺，再由销售和计划复核。"],
        "procurement": ["对照采购行及收货凭证核对到货覆盖和剩余承诺。"],
    }[role]
    return {
        "role": ROLES[role][0], "facts": lines, "review_actions": actions,
    }


def _report_text(value):
    # Keep human-reviewed text inert in a Markdown reader.
    return re.sub(r"([\\\x60*_{}\[\]()<>#!|~&])", r"\\\1", str(value).replace("\r", " "))


def _display_field(field, value):
    if field == "analysis_mode":
        return "按工程版本和 BOM 行" if value == "engineering" else "按物料追溯全部使用位置"
    return CHANGE_TYPES[value] if field == "change_type" else value


def finish(repository, context, item):
    result = item["result"]
    role = context["role"]
    answer = _answer(role, result)
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


def engineering_references(repository, context):
    terms = [ROLES["engineering"][1], *context["focus"], CHANGE_TYPES[context["draft"]["change_type"]]]
    return knowledge.retrieve(repository, " ".join(terms))
