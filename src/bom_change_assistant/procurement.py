"""B19: purchase evidence and receipt reconciliation, independent of supply planning."""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import date, datetime
import hashlib
import json
import math
import re
from typing import Any

from .normalization import normalize_header

SCHEMA = '''
CREATE TABLE IF NOT EXISTS procurement_records (
    kind TEXT NOT NULL,
    record_key TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    evidence_json TEXT NOT NULL,
    PRIMARY KEY(kind, record_key)
);
'''
QUANTITIES = ('Q', 'R', 'O', 'C')
LABELS = {'Q': '采购总量', 'R': '累计净到货量', 'O': '剩余未到货承诺', 'C': '取消/关闭未交数量'}
ROLES = ['工程', '采购', '计划']
TOPICS = ['已到货与未到货承诺的处理', '取消或改采购条件', '新料放行与切换窗口']
BOUNDARY = '采购证据仅供共同确认；累计净到货不等于可用库存，剩余承诺不自动并入在途或 Open PO；不自动取消、报废、迁移订单或放行。'


def text(value):
    # Procurement line identifiers retain leading zeros; never use normalize_item_no.
    return '' if value is None else str(value).strip().upper()


def identifier(value, required=False):
    v = text(value)
    if (required and not v) or len(v) > 100 or re.search(r'^[+-]?\d+(?:\.\d+)?E[+-]?\d+$', v):
        raise ValueError('业务编号缺失、过长或为科学计数法')
    return v or None


def iso(value, required=False):
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    v = text(value)
    if not v and not required:
        return None
    if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', v):
        raise ValueError('日期须为 YYYY-MM-DD')
    try:
        date.fromisoformat(v)
    except ValueError:
        raise ValueError('日期无效') from None
    return v


def number(value, positive=False):
    if value is None or (isinstance(value, str) and not value.strip()):
        if positive:
            raise ValueError('流水数量须为正数')
        return None
    try:
        if isinstance(value, bool):
            raise ValueError()
        n = float(value)
        if not math.isfinite(n) or n < 0 or (positive and n <= 0):
            raise ValueError()
    except (ValueError, TypeError, OverflowError):
        raise ValueError('数量须为有限非负数；流水数量须大于零') from None
    return n


def enum(value, choices):
    v = text(value)
    return v if v in choices else 'UNKNOWN'


def canonical(row, aggregate=False):
    """Only explicitly allowed fields survive normalization; no free text is persisted."""
    get = lambda key: row.get(key)
    kind = 'SUMMARY' if aggregate else enum(get('kind'), {'PURCHASE_LINE', 'RECEIPT'})
    if kind == 'UNKNOWN':
        raise ValueError('采购数据类型须为 PURCHASE_LINE 或 RECEIPT')
    r = {'kind': kind, 'po': identifier(get('po'), kind != 'SUMMARY'),
         'item': identifier(get('item'), kind != 'SUMMARY'),
         'material': identifier(get('material'), True), 'plant': identifier(get('plant'), True),
         'storage_location': identifier(get('storage_location')), 'unit': identifier(get('unit'), kind == 'RECEIPT'),
         'as_of': iso(get('as_of'), kind == 'RECEIPT')}
    if kind == 'RECEIPT':
        r.update(document=identifier(get('document'), True), year=identifier(get('year'), True),
                 document_item=identifier(get('document_item'), True), business_date=iso(get('business_date'), True),
                 event_type=enum(get('event_type'), {'RECEIPT', 'RETURN', 'REVERSAL'}),
                 quantity=number(get('quantity'), True), history=enum(get('history'), {'COMPLETE', 'PARTIAL', 'UNKNOWN'}),
                 ref_document=identifier(get('ref_document')), ref_year=identifier(get('ref_year')),
                 ref_item=identifier(get('ref_item')))
    else:
        r.update({key: number(get(key)) for key in QUANTITIES})
        r.update(status=enum(get('status'), {'OPEN', 'CLOSED', 'CANCELLED', 'UNKNOWN'}),
                 cancelability=enum(get('cancelability'), {'YES', 'NO', 'UNKNOWN'}),
                 expected_date=iso(get('expected_date')), quality=enum(get('quality'), {'RELEASED', 'HOLD', 'UNKNOWN'}))
    return r


def key_for(r):
    if r['kind'] == 'RECEIPT':
        key = [r['document'], r['year'], r['document_item']]
    elif r['kind'] == 'SUMMARY':
        key = [r[k] for k in ('material', 'plant', 'storage_location')]
    else:
        key = [r[k] for k in ('po', 'item', 'plant')]
    return hashlib.sha256(json.dumps(key, ensure_ascii=False).encode()).hexdigest()


def store(connection, row, evidence, aggregate=False):
    r = canonical(row, aggregate)
    key = key_for(r)
    encoded = json.dumps(r, ensure_ascii=False, sort_keys=True)
    old = connection.execute('SELECT payload_json, evidence_json FROM procurement_records WHERE kind=? AND record_key=?', (r['kind'], key)).fetchone()
    sources = []
    if old:
        if old['payload_json'] != encoded:
            raise ValueError('重复采购/到货业务键的内容或截至日冲突')
        sources = json.loads(old['evidence_json'])
    if evidence not in sources:
        sources.append(evidence)
    connection.execute('INSERT OR REPLACE INTO procurement_records VALUES (?,?,?,?)',
                       (r['kind'], key, encoded, json.dumps(sources, ensure_ascii=False)))
    return r


HEADERS = {
    'kind': ['采购数据类型'], 'po': ['采购单号'], 'item': ['采购行号'],
    'material': ['物料'], 'plant': ['工厂'], 'storage_location': ['库位'],
    'unit': ['计量单位', '单位'], 'as_of': ['数据截至日'],
    'Q': ['采购总量'], 'R': ['累计净到货量', '已到货量'], 'O': ['剩余未到货承诺', '剩余采购承诺'],
    'C': ['取消/关闭未交数量'], 'status': ['采购状态'], 'cancelability': ['可取消性', '可取消'],
    'expected_date': ['预计到货日'], 'quality': ['质量状态'],
    'document': ['到货凭证号'], 'year': ['凭证年度'], 'document_item': ['凭证行号'],
    'business_date': ['业务日期'], 'event_type': ['类型'], 'quantity': ['数量'],
    'history': ['历史完整性'], 'ref_document': ['被冲销凭证号'], 'ref_year': ['被冲销凭证年度'], 'ref_item': ['被冲销凭证行号'],
}


def import_rows(connection, source_id, sheet_index, headers, rows, aggregate=False):
    idx = {normalize_header(h): i for i, h in enumerate(headers)}
    seen = 0
    for rn, values in rows:
        if all(v in (None, '') for v in values):
            continue
        seen += 1
        row = {}
        for field, names in HEADERS.items():
            for name in names:
                i = idx.get(normalize_header(name))
                if i is not None and i < len(values):
                    row[field] = values[i]
                    break
        try:
            store(connection, row, {'source_id': source_id, 'sheet_index': sheet_index, 'source_row': rn}, aggregate)
        except ValueError as exc:
            raise ValueError(f'{source_id} 工作表 {sheet_index} 第 {rn} 行：{exc}') from None
    return seen, seen, []


def all_records(repository):
    with repository.connect() as c:
        rows = c.execute('SELECT * FROM procurement_records ORDER BY kind, record_key').fetchall()
    return [{**json.loads(r['payload_json']), 'record_id': r['kind'] + ':' + r['record_key'],
             'evidence': json.loads(r['evidence_json'])} for r in rows]


def identity(r):
    return tuple(r[k] for k in ('po', 'item', 'plant'))


def match_scope(repository, r, request):
    privacy = repository.privacy_key()
    materials = [request['new_component']] if request['change_type'] == 'add' else [request['old_component']]
    if request['change_type'] == 'replace':
        materials.append(request['new_component'])
    if privacy.digest('material', r['material']) not in {privacy.digest('material', x) for x in materials if x}:
        return None
    exact = True
    for key, query_key in [('plant', 'planning_plant'), ('storage_location', 'storage_location')]:
        q = text(request.get(query_key))
        value = r.get(key)
        if q and value:
            equal = privacy.digest('plant', q) == privacy.digest('plant', value) if key == 'plant' else q == value
            if not equal:
                return None
        else:
            exact = False
    return '精确匹配' if exact else '范围待确认'


def receipt_check(line, events):
    issues = []
    coverage_issues = []
    valid = []
    future = []
    all_ids = {(e['document'], e['year'], e['document_item']): e for e in events}
    reversed_ids = set()
    for e in events:
        e['issues'] = []
        if not line.get('as_of'):
            e['issues'].append('采购行截至日缺失')
        elif e['business_date'] > line['as_of']:
            e['excluded'] = '截至日后流水'
            future.append(e)
            continue
        if e['as_of'] != line.get('as_of'):
            coverage_issues.append('流水与采购行截至日不一致，覆盖待确认')
            e['coverage_issue'] = coverage_issues[-1]
        if any(not line.get(k) or not e.get(k) or line[k] != e[k] for k in ('material', 'storage_location', 'unit')):
            e['issues'].append('流水物料/库位/单位不一致或缺失')
        sign = {'RECEIPT': 1, 'RETURN': -1}.get(e['event_type'])
        if e['event_type'] == 'REVERSAL':
            ref = (e['ref_document'], e['ref_year'], e['ref_item'])
            target = all_ids.get(ref)
            if not target or target['event_type'] not in ('RECEIPT', 'RETURN') or ref in reversed_ids:
                e['issues'].append('冲销目标缺失、不支持或已被冲销')
            elif (abs(target['quantity'] - e['quantity']) > 1e-6 or
                  target['business_date'] > e['business_date'] or any(
                      target[k] != e[k] for k in ('material', 'unit', 'storage_location'))):
                e['issues'].append('仅支持同范围全额冲销，日期顺序须有效')
            else:
                sign = -1 if target['event_type'] == 'RECEIPT' else 1
                reversed_ids.add(ref)
        elif sign is None:
            e['issues'].append('未知到货类型')
        if e['issues']:
            issues.extend(e['issues'])
        elif sign is not None:
            e['signed_quantity'] = sign * e['quantity']
            valid.append(e)
    net = sum(e['signed_quantity'] for e in valid)
    if net < -1e-6 or not math.isfinite(net):
        issues.append('退货超过收货或净额溢出')
    invalid_net = bool(issues)
    complete = bool(events) and not coverage_issues and all(e['history'] == 'COMPLETE' for e in events)
    if issues:
        status = '需复核'
    elif not complete:
        status = '覆盖待确认'
    elif line.get('R') is None:
        status = '待补累计净到货量'
    elif abs(net - line['R']) > 1e-6:
        status = '需复核'
        issues.append('完整流水净额与采购行累计净到货量不一致')
    else:
        status = '一致'
    return {'status': status, 'provided_net': None if invalid_net else net if events else None,
            'complete_net': net if complete and not invalid_net else None, 'issues': list(dict.fromkeys(issues + coverage_issues)),
            'events': events, 'future_excluded': future}


def analyze(repository, requests):
    """One pass over records for single or batch requests; each record counted once."""
    records = all_records(repository)
    lines = [x for x in records if x['kind'] != 'RECEIPT']
    events = [x for x in records if x['kind'] == 'RECEIPT']
    output = []
    orphans = []
    for line in lines:
        matches = [(q, match_scope(repository, line, q)) for q in requests]
        matches = [(q, m) for q, m in matches if m]
        if not matches:
            continue
        line['match_status'] = '精确匹配' if all(m == '精确匹配' for _, m in matches) else '范围待确认'
        line['change_refs'] = list(dict.fromkeys(q.get('ecn') or '本项' for q, _ in matches))
        line['issues'] = []
        for key, label in [('po','采购行标识'), ('unit','单位'), ('as_of','数据截至日'), ('storage_location','库位')]:
            if not line.get(key): line['issues'].append(label + '缺失')
        if line['match_status'] != '精确匹配': line['issues'].append('匹配范围待确认')
        quantities = [line[k] for k in QUANTITIES]
        if any(x is None for x in quantities):
            line['balance'] = '待补数据'
        elif not math.isfinite(sum(quantities)) or abs(line['Q'] - line['R'] - line['O'] - line['C']) > 1e-6:
            line['balance'] = '需复核'
            line['issues'].append('采购总量与到货/剩余承诺/取消数量不平衡')
        else:
            line['balance'] = '一致'
        if line['Q'] is not None and line['R'] is not None and line['R'] > line['Q'] + 1e-6:
            line['balance'] = '需复核'; line['issues'].append('累计净到货量超过采购总量')
        if line['status'] in ('CLOSED', 'CANCELLED') and line['O'] not in (None, 0):
            line['issues'].append('关闭/取消状态与剩余承诺冲突')
        linked = [deepcopy(e) for e in events if identity(e) == identity(line)] if line['kind'] == 'PURCHASE_LINE' else []
        line['receipt_check'] = receipt_check(line, linked)
        if line['kind'] == 'SUMMARY': line['issues'].append('范围汇总，未提供采购行及到货流水')
        if line['quality'] != 'RELEASED': line['issues'].append('质量放行待补证或受限')
        else: line['issues'].append('源记录质量状态 RELEASED，仍需核实范围与切换窗口')
        if line['cancelability'] == 'UNKNOWN': line['issues'].append('取消或改采购条件待采购确认')
        line['date_reviews'] = []
        for q, _ in matches:
            effective = q.get('effective_date')
            line['date_reviews'].append({'ecn': q.get('ecn') or '本项', 'effective_date': effective,
                'status': '预计到货日期未知' if not line['expected_date'] else
                    '晚于变更生效日' if effective and line['expected_date'] > effective else
                    '日期相符仍需计划确认' if effective and line['expected_date'] == effective else '需计划确认切换窗口'})
        output.append(line)
    for e in events:
        matches = [(q, match_scope(repository, e, q)) for q in requests]
        if any(m for _, m in matches) and not any(identity(e) == identity(l) for l in output if l['kind']=='PURCHASE_LINE'):
            e['issues'] = ['未匹配采购行，缺少采购余额证据']
            e['change_refs'] = list(dict.fromkeys(q.get('ecn') or '本项' for q,m in matches if m))
            orphans.append(e)
    # Overlap blocks totals; neither the summary nor lines are silently preferred.
    for line in output:
        overlap = any(other['kind'] != line['kind'] and other['kind'] != 'RECEIPT' and
                      all(other[k] == line[k] for k in ('material', 'plant', 'storage_location')) for other in output)
        line['overlap'] = overlap
        if overlap: line['issues'].append('范围汇总与采购明细可能重叠，覆盖范围待确认')
    groups = defaultdict(list)
    for line in output:
        groups[tuple(line[k] for k in ('material','plant','storage_location','unit','as_of'))].append(line)
    totals = []
    for key, items in groups.items():
        fields = dict(zip(('material','plant','storage_location','unit','as_of'), key))
        fields['totals'] = {}; fields['known_partial'] = {}
        for quantity in QUANTITIES:
            trusted = [x for x in items if x[quantity] is not None and x['match_status']=='精确匹配' and
                       x['unit'] and x['as_of'] and not x['overlap'] and x['balance']!='需复核' and
                       not (quantity=='O' and x['status'] in ('CLOSED','CANCELLED') and x['O'] not in (None,0)) and
                       not (quantity=='R' and x['receipt_check']['status']=='需复核')]
            subtotal = sum(x[quantity] for x in trusted)
            finite = math.isfinite(subtotal)
            fields['totals'][quantity] = subtotal if finite and len(trusted)==len(items) else None
            fields['known_partial'][quantity] = subtotal if finite and trusted else None
        fields['incomplete'] = any(v is None for v in fields['totals'].values())
        totals.append(fields)
    any_evidence = bool(output or orphans)
    review_topics = list(TOPICS)
    if len(output) == 1:
        review_topics[0] = f"已到货 {shown(output[0]['R'])} 与未到货承诺 {shown(output[0]['O'])} 的处理"
    result = {'status': '需工程/采购/计划共同确认' if any_evidence else '当前快照未匹配采购证据',
              'lines': output, 'orphan_receipts': orphans, 'groups': totals,
              'overall_totals': totals[0]['totals'] if len(totals)==1 and not orphans else None,
              'business_review_required': ROLES if any_evidence else [], 'review_topics': review_topics if any_evidence else [],
              'approved_action': None, 'boundary': BOUNDARY, 'inventory_from_receipts': None}
    result['sections'] = sections(result)
    return result


def shown(value):
    return '待补数据' if value is None else f'{value:g}' if isinstance(value,(int,float)) else str(value)


def evidence_text(records):
    return '；'.join(f"{e['source_id']} 工作表 {e['sheet_index']} 第 {e['source_row']} 行" for e in records)


def sections(result):
    """One business-readable presentation contract used by HTML and Markdown."""
    out = [{'title': '采购承诺与到货核查', 'lines': [result['status'], result['boundary']]}]
    def event_text(e):
        return (f"凭证 {e['document']}/{e['year']}/{e['document_item']} · 采购 {e['po']}/{e['item']} · "
                f"{e['material']} · {e['plant']}/{e.get('storage_location') or '库位未知'} · {e['unit'] or '单位未知'} · "
                f"{e['business_date']} · 截至 {e['as_of']} · {e['event_type']} {shown(e['quantity'])} · "
                f"历史 {e['history']} · 冲销引用 {e.get('ref_document') or '无'}/{e.get('ref_year') or '无'}/{e.get('ref_item') or '无'} · "
                f"{e.get('excluded') or e.get('coverage_issue') or '；'.join(e.get('issues',[])) or '已提供流水'} · {evidence_text(e['evidence'])}")
    for line in result['lines']:
        check = line['receipt_check']
        out.append({'title': f"采购 {line['po'] or '范围汇总'}/{line['item'] or '无行号'} · {line['material']}", 'lines': [
            f"{line['plant']}/{line['storage_location'] or '库位未知'} · {line['unit'] or '单位未知'} · 截至 {line['as_of'] or '日期未知'} · {line['match_status']} · {' / '.join(line['change_refs'])}",
            '；'.join(f'{LABELS[k]}：{shown(line[k])}' for k in QUANTITIES),
            f"采购状态 {line['status']} · 可取消性 {line['cancelability']} · 质量状态 {line['quality']} · 对账 {line['balance']}",
            f"到货核对 {check['status']} · 已提供流水净额 {shown(check['provided_net'])} · 完整流水净额 {shown(check['complete_net'])}",
            *[f"{d['ecn']} · 预计到货 {line['expected_date'] or '未知'} · 变更生效 {d['effective_date'] or '未知'} · {d['status']}" for d in line['date_reviews']],
            *line['issues'], *check['issues'], *[event_text(e) for e in check['events']], evidence_text(line['evidence'])]})
    for group in result['groups']:
        out.append({'title': '采购数量分组', 'lines': [
            f"{group['material']} · {group['plant']}/{group['storage_location'] or '库位未知'} · {group['unit'] or '单位未知'} · 截至 {group['as_of'] or '日期未知'}",
            *[f"{LABELS[k]}完整合计：{shown(group['totals'][k])}；已知可信部分：{shown(group['known_partial'][k])}（完整合计未知时，此部分不得充当总量）" for k in QUANTITIES]]})
    if result['orphan_receipts']:
        out.append({'title':'未匹配采购行的到货流水','lines':[event_text(e) for e in result['orphan_receipts']]})
    if result['business_review_required']:
        out.append({'title':'共同确认事项','lines':['复核角色：'+'、'.join(result['business_review_required']), *result['review_topics'],
                    '已批准动作：无（approved_action=null）']})
    return out
