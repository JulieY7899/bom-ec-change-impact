"""Deterministic review of dated, scoped approval records in the engineering pilot."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
import re
import unicodedata

from .retrieval_evidence import FULL_DATE

OPERATION = "常规换版生产"
CODE = r"(?<![A-Za-z0-9_-])[A-Za-z][A-Za-z0-9]*[-_][A-Za-z0-9_-]+(?![A-Za-z0-9_-])"
BATCH = re.compile(r"(?:LOT|BATCH)[-_][A-Z0-9_-]+", re.I)
APPROVAL_IN_QUESTION = re.compile(rf"(?:凭|依据|按|批件\s*)(?:\s*批件)?\s*({CODE})", re.I)
CHANGE = re.compile(r"换版")
LINE = re.compile(r"常规(?:装配)?线|装配[一二三四五六七八九十0-9]+线")
DATE = re.compile(FULL_DATE)
START = re.compile(rf"(?:自|从)\s*({FULL_DATE})|({FULL_DATE})\s*(?:零时)?\s*起")
END = re.compile(rf"(?:有效期至|有效至|(?:生效|起|授权)[^。；]*?至)\s*({FULL_DATE})(?:\s*(当日结束|当天结束|含当日|含当天))?")
COVERED_UNTIL = re.compile(rf"覆盖\s*(?:至|到)\s*({FULL_DATE})|覆盖\s*{FULL_DATE}\s*(?:至|到)\s*(?:(20\d{{2}})\s*年\s*)?(\d{{1,2}})\s*月\s*(\d{{1,2}})\s*日")
COVERAGE_START = re.compile(rf"覆盖\s*({FULL_DATE})\s*(?:至|到)")
ISSUED = re.compile(r"正式签发|正式批件")
DRAFT = re.compile(r"未签发|未正式生效|草案|未批准|不予批准|拟批准")
BENCH = re.compile(r"台架|台试|样件试制|仅限[^。；]*试验")
AUTHORIZES = re.compile(r"批准|签章归档件")
CONTINUING = re.compile(r"持续有效[^。]*?(?:直至|至)[^。]*(?:撤回|替代)")
PENDING_INSPECTION = re.compile(r"(?:如|须|待|条件为)[^。]*复检[^。]*(?:生效|方可|条件)")
PASSED_INSPECTION = re.compile(r"复检[^。]{0,10}(?<![不未非])合格")
UNCONFIRMED_INSPECTION = re.compile(r"尚未|未签认|待签认|不合格|未合格")
ARCHIVE = re.compile(r"档案|台账")
WITHDRAWAL = re.compile(r"正式撤回(?:单|令)|撤回(?:单|令)[^。；]*签发")
WORD_BREAK = re.compile(r"[。；\n]")


def _day(raw):
    try:
        return date(*map(int, re.findall(r"\d+", raw)))
    except (TypeError, ValueError):
        return None


def _identifiers(text):
    return {value.upper() for value in re.findall(CODE, text)}


@dataclass(frozen=True)
class Claim:
    approval_id: str
    material_id: str
    as_of: str
    line: str
    batch_id: str | None = None

    @classmethod
    def confirmed(cls, values):
        if not isinstance(values, dict):
            raise ValueError("审批核查范围须为对象")
        ids = [values.get(field) for field in ("approval_id", "material_id", "batch_id")]
        if any(value is not None and value != "" and
               (not isinstance(value, str) or len(value) > 60 or not re.fullmatch(CODE, value.strip()))
               for value in ids):
            raise ValueError("请填写完整的批件、物料和批次编号")
        approval_id, material, batch = (str(value or "").strip().upper() for value in ids)
        day = values.get("as_of")
        line = values.get("line")
        if not approval_id or not material or not isinstance(line, str) or not LINE.fullmatch(line.strip()):
            raise ValueError("请确认审批编号、物料、产线和查询日期")
        try:
            day = date.fromisoformat(day).isoformat()
        except (TypeError, ValueError) as exc:
            raise ValueError("请确认有效的查询日期") from exc
        if values.get("operation", OPERATION) != OPERATION:
            raise ValueError("审批试点范围为常规换版生产")
        return cls(approval_id, material, day, line.strip(), batch or None)

    @classmethod
    def from_question(cls, query):
        # This pilot recognises a question about an identified approval and operation.
        query = unicodedata.normalize("NFKC", query)
        approval = APPROVAL_IN_QUESTION.search(query)
        days = DATE.findall(query)
        if not approval or not CHANGE.search(query) or not days:
            return None
        line = LINE.search(query)
        records = _identifiers(query) - {approval[1].upper()}
        batches = {code for code in records if BATCH.fullmatch(code)}
        materials = records - batches
        day = _day(days[0])
        if not (len(days) == len(materials) == 1 and len(batches) <= 1 and line and day):
            raise ValueError("请使用审批范围表单确认物料、产线、批次和查询日期")
        return cls(approval[1].upper(), materials.pop(), day.isoformat(), line[0], next(iter(batches), None))

    def values(self):
        return {**asdict(self), "operation": OPERATION}

    def search_text(self):
        return " ".join(filter(None, (self.approval_id, self.material_id, self.line, self.batch_id,
                                      OPERATION, "签发 生效 截止 复检 效力归档 撤回 替代")))


def _start(text):
    for match in START.finditer(text):
        # Coverage and narrative dates do not establish when the grant starts.
        tail = text[match.start():].split("。", 1)[0]
        if re.search(r"生效|批准|换版|撤回|替代", tail):
            return _day(match[1] or match[2])
    return None


def _batches(text):
    return {code for code in _identifiers(text) if BATCH.fullmatch(code)}


def _covers(text, day):
    if re.search(r"待补|缺页|缺档|不完整|尚未核实", text):
        return False
    start = COVERAGE_START.search(text)
    if start and (not (first := _day(start[1])) or day < first):
        return False
    for match in COVERED_UNTIL.finditer(text):
        start_year = _day(start[1]).year if start else day.year
        last = _day(match[1] or f"{match[2] or start_year}-{match[3]}-{match[4]}")
        if last and day <= last:
            return True
    return False


def _object(text, claim):
    return {claim.approval_id, claim.material_id} <= _identifiers(text)


def _line(text, line):
    return line in text or (line == "常规线" and re.search(r"常规(?:线|装配线|生产)", text))


def _scope(text, claim):
    return _object(text, claim) and _line(text, claim.line)


def _withdrawal(text, claim):
    if not WITHDRAWAL.search(text) or DRAFT.search(text):
        return None
    for clause in WORD_BREAK.split(text):
        if not (_scope(clause, claim) and re.search(r"撤回|取消", clause) and CHANGE.search(clause)):
            continue
        return _start(clause), _batches(clause.split("保留", 1)[0])
    return None


def _supersedes(text, claim):
    return ("正式替代批件" in text and "全面替代" in text and "失效" in text and not DRAFT.search(text)
            and any(_scope(clause, claim) and CHANGE.search(clause) and "全面替代" in clause
                    and (not _batches(clause) or claim.batch_id in _batches(clause))
                    for clause in WORD_BREAK.split(text)))


@dataclass
class Grant:
    fragments: list
    starts: date | None
    ends: date | None
    inclusive_end: bool
    start_fragment: dict | None
    end_fragment: dict | None
    batches: set
    conditional: bool
    continuing: bool
    self_coverage: bool


def _grant(fragments, claim):
    statement, grant_clause = next(
        ((hit, clause) for hit in fragments for clause in WORD_BREAK.split(hit["text"])
         if _scope(clause, claim) and CHANGE.search(clause) and AUTHORIZES.search(clause)
         and not (DRAFT.search(clause) or BENCH.search(clause))), (None, ""))
    if not statement:
        return None
    # Continuation paragraphs belong to this grant when they stay on the same
    # record or explicitly refer to the grant without introducing another ID.
    scoped = [hit for hit in fragments if hit is statement or (
        not (_identifiers(hit["text"]) - _identifiers(statement["text"]))
        and re.match(r"授权|该授权|正式签发原件|有效期|有效至|自|从", hit["text"])
        and not (DRAFT.search(hit["text"]) or BENCH.search(hit["text"])))]
    issuer = next((hit for hit in scoped if ISSUED.search(hit["text"])), None)
    if not issuer and "签章归档件" not in grant_clause:
        return None
    start_fragment, starts = next(((hit, day) for hit in scoped if (day := _start(hit["text"])) is not None), (None, None))
    end_fragment, end_match = next(((hit, match) for hit in scoped if (match := END.search(hit["text"])) is not None), (None, None))
    conditional = next((hit for hit in scoped if PENDING_INSPECTION.search(hit["text"])), None)
    continuing = next((hit for hit in scoped if CONTINUING.search(hit["text"])), None)
    self_coverage = next((hit for hit in scoped if "档案岗确认" in hit["text"] and "所列期间无撤回" in hit["text"]), None)
    cited = (statement, issuer, start_fragment, end_fragment, conditional, continuing, self_coverage)
    return Grant([hit for hit in scoped if hit in cited], starts, _day(end_match[1]) if end_match else None,
                 bool(end_match and end_match[2]), start_fragment, end_fragment, _batches(grant_clause),
                 bool(conditional), bool(continuing), bool(self_coverage))


def assess(claim, material):
    """Assess the already retrieved material. The returned roles are shared by page and report."""
    day = date.fromisoformat(claim.as_of)
    sources = {}
    for hit in material:
        sources.setdefault(hit["reference"].split(":", 1)[0], []).append(hit)

    grants = [grant for hits in sources.values() if (grant := _grant(hits, claim))]
    grants = [grant for grant in grants
              if not claim.batch_id or not grant.batches or claim.batch_id in grant.batches]
    evidence = []
    for hit in material:
        text = hit["text"]
        if any(hit in grant.fragments for grant in grants) or (
            claim.approval_id in _identifiers(text + " " + hit["title"])
            and (_scope(text, claim) or re.search(r"档案|台账|持续有效|复检", text))
        ):
            evidence.append(hit)
    withdrawal_records = [(hit, *scope) for hit in evidence if (scope := _withdrawal(hit["text"], claim))]
    withdrawals = [(hit, starts) for hit, starts, batches in withdrawal_records
                   if not batches or claim.batch_id in batches]
    replacements = [(hit, _start(hit["text"])) for hit in evidence if _supersedes(hit["text"], claim)]
    policies = [hit for hit in evidence if CONTINUING.search(hit["text"])
                and claim.line in hit["text"] and re.search(r"口径|规则", hit["text"])]
    archive = [hit for hit in evidence if _object(hit["text"], claim) and ARCHIVE.search(hit["text"])
               and (not LINE.search(hit["text"]) or _line(hit["text"], claim.line))
               and _covers(hit["text"], day)
               and (not claim.batch_id or not _batches(hit["text"]) or claim.batch_id in _batches(hit["text"]))]
    def date_verified(hit):
        days = {_day(token) for token in DATE.findall(hit["text"])}
        return len(days) == 1 and None not in days and next(iter(days)) <= day

    signed_result = [hit for hit in evidence if PASSED_INSPECTION.search(hit["text"])
                     and re.search(r"签认", hit["text"]) and not PENDING_INSPECTION.search(hit["text"])
                     and not UNCONFIRMED_INSPECTION.search(hit["text"])
                     and _object(hit["text"], claim)
                     and (not claim.batch_id or claim.batch_id in _batches(hit["text"]))
                     and date_verified(hit)]

    def decision(state, reason, *, supporting=(), excluding=(), missing=""):
        used = {hit["reference"] for hit in (*supporting, *excluding)}
        return {"state": state, "reason": reason, "missing": missing, "claim": claim.values(),
                "support": list(supporting), "exclusion": list(excluding),
                "facts": [hit for hit in evidence if hit["reference"] not in used]}

    if not claim.batch_id and (any(grant.batches for grant in grants) or any(batches for _, _, batches in withdrawal_records)):
        return decision("证据不足", "正式记录按批次区分授权范围，当前主张未指定待核批次。", missing="待核查的实际批次")
    for records, action in ((withdrawals, "正式撤回"), (replacements, "被正式替代")):
        active = [(hit, starts) for hit, starts in records if starts and starts <= day]
        if active:
            start = min(starts for _, starts in active)
            scope = f"批次 {claim.batch_id} " if claim.batch_id else "所查范围"
            reason = f"{claim.approval_id} 对{scope}的授权自 {start} 起已{action}。查询日已到该时点。"
            if any("独立批准" in hit["text"] and "原撤回决定不变" in hit["text"] for hit in evidence):
                reason += "另有重新授权记录，原批件的撤回仍有效。"
            return decision("不适用", reason, excluding=[hit for hit, _ in active])
    starts = {grant.starts for grant in grants if grant.starts}
    if len(starts) > 1:
        dates = "、".join(map(str, sorted(starts)))
        return decision("证据冲突", f"同一审批范围的正式记录给出生效日 {dates}，缺少可确认优先版本的依据。",
                        missing="档案岗位确认有效版本及生效日期")
    ends = {(grant.ends, grant.inclusive_end) for grant in grants if grant.ends}
    if len(ends) > 1:
        return decision("证据冲突", "同一审批范围的正式记录给出了不同截止条款，缺少可确认优先版本的依据。",
                        missing="档案岗位确认有效版本及截止时点")
    if not grants:
        reason = ("已有复检合格记录，尚未核实该对象及范围的正式批准。"
                  if any(PASSED_INSPECTION.search(hit["text"]) for hit in evidence)
                  else "尚未核实该对象及范围的正式批准。")
        return decision("证据不足", reason,
                        missing="目标批件的正式授权原文和效力记录")
    grant = grants[0]
    if not grant.starts:
        return decision("证据不足", "已有签发记录，尚未核实正式生效日。",
                        missing="载明生效日期的正式条款或附页")
    if day < grant.starts:
        return decision("不适用", f"查询日早于 {grant.starts} 生效日。", excluding=[grant.start_fragment])
    if grant.ends and day > grant.ends:
        return decision("不适用", f"查询日晚于 {grant.ends} 截止日。", excluding=[grant.end_fragment])
    if grant.ends == day and not grant.inclusive_end:
        return decision("证据不足", f"条款写明至 {grant.ends}，未明确是否包含查询日当天。",
                        missing="截止日当天的适用规则或明确截止时点的条款")
    if any(starts is None for _, starts in (*withdrawals, *replacements)):
        return decision("证据不足", "已有适用范围内的正式效力变更记录，其生效日尚未核实。",
                        missing="正式撤回或替代的生效条款")
    if grant.conditional and not signed_result:
        other_batches = {batch for hit in evidence if PASSED_INSPECTION.search(hit["text"])
                         for batch in _batches(hit["text"]) if batch != claim.batch_id}
        reason = f"正式批准附复检条件，尚未核实 {claim.batch_id or '所查范围'} 已签认合格。"
        if other_batches:
            reason += f"另有签认记录覆盖 {'、'.join(sorted(other_batches))}。"
        return decision("证据不足", reason, missing=f"{claim.batch_id or '同批'} 的复检合格签认")
    if not grant.ends and not (grant.continuing or policies):
        return decision("证据不足", "原批件未列截止日，缺少持续有效依据。",
                        missing="适用于该批件的持续有效条款或业务规则")
    if not archive and not grant.self_coverage:
        return decision("证据不足", f"效力档案未能证实覆盖查询日 {day}。",
                        missing="覆盖查询日的正式撤回、替代及修订档案")
    supporting = {hit["reference"]: hit for hit in (*grant.fragments, *policies, *archive, *signed_result)}
    reason = f"对象、动作和适用范围相符，正式授权自 {grant.starts} 生效，效力记录覆盖查询日。"
    if grant.ends:
        reason += f"条款截止日为 {grant.ends}。" if not grant.inclusive_end else f"授权包含 {grant.ends} 当日。"
    if grant.conditional or any(PASSED_INSPECTION.search(hit["text"]) for hit in grant.fragments):
        dates = sorted({token for hit in signed_result for token in DATE.findall(hit["text"])})
        reason += ("已核实复检合格和正式批准。" if not dates
                   else f"已核实 {'、'.join(dates)} 的同批复检合格签认。")
    for hit, starts, batches in withdrawal_records:
        if starts and starts > day:
            supporting[hit["reference"]] = hit
            reason += f"已签发的撤回自 {starts} 起生效。"
        elif batches and claim.batch_id not in batches and re.search(
            rf"(?:{re.escape(claim.batch_id or '')}[^。；]*保留|保留[^。；]*{re.escape(claim.batch_id or '')})", hit["text"]
        ):
            supporting[hit["reference"]] = hit
            reason += f"撤回条款明确保留 {claim.batch_id}。"
    if not grant.ends:
        reason += "已核实持续有效依据和查询日的效力档案。"
    if any("撤回草案" in hit["text"] and DRAFT.search(hit["text"]) for hit in evidence):
        reason += "档案中的撤回草案尚未生效。"
    return decision("适用", reason, supporting=supporting.values())
