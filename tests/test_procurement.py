"""B19-DRAFT01 supplementary acceptance P19-01..28, synthetic HTTP/XLSX only."""
from __future__ import annotations
import html
import http.client
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
import tempfile
import threading
import unittest
import zipfile
from http.server import ThreadingHTTPServer

from bom_change_assistant.demo import load_demo_snapshot
from bom_change_assistant.web import AppState, Handler

BASE = dict(kind='PURCHASE_LINE', po='PO-P19-001', item='00010', material='PART-OLD',
            plant='PLANT-T', storage_location='STORE-T', unit='EA', as_of='2026-11-30',
            Q=50, R=20, O=30, C=0, status='OPEN', cancelability='UNKNOWN')
QUERY = dict(change_type='replace', old_component='PART-OLD', new_component='PART-NEW',
             effective_date='2026-12-01', bom_version='BOM-000001', planning_plant='PLANT-T', storage_location='STORE-T')
COLUMNS = [
 ('kind','采购数据类型'),('po','采购单号'),('item','采购行号'),('material','物料'),('plant','工厂'),
 ('storage_location','库位'),('unit','计量单位'),('as_of','数据截至日'),('Q','采购总量'),('R','累计净到货量'),
 ('O','剩余未到货承诺'),('C','取消/关闭未交数量'),('status','采购状态'),('cancelability','可取消性'),
 ('expected_date','预计到货日'),('quality','质量状态'),('document','到货凭证号'),('year','凭证年度'),
 ('document_item','凭证行号'),('business_date','业务日期'),('event_type','类型'),('quantity','数量'),
 ('history','历史完整性'),('ref_document','被冲销凭证号'),('ref_year','被冲销凭证年度'),('ref_item','被冲销凭证行号'),
 ('company','企业名称'),('supplier','供应商名称'),('person','联系人'),('note','自由备注'),('path','原始文件路径')]


def receipt(document='GR1', quantity=20, **kw):
    return dict(kind='RECEIPT', po='PO-P19-001', item='00010', material='PART-OLD', plant='PLANT-T',
                storage_location='STORE-T', unit='EA', as_of='2026-11-30', document=document,
                year='2026', document_item='001', business_date='2026-11-30', event_type='RECEIPT',
                quantity=quantity, history='COMPLETE', **kw)


def workbook(path, sheets):
    ns='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
    with zipfile.ZipFile(path,'w') as z:
        z.writestr('xl/workbook.xml',f'<workbook xmlns="{ns}" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'+''.join(f'<sheet name="S{i}" sheetId="{i}" r:id="rId{i}"/>' for i in range(1,len(sheets)+1))+'</sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'+''.join(f'<Relationship Id="rId{i}" Target="worksheets/sheet{i}.xml"/>' for i in range(1,len(sheets)+1))+'</Relationships>')
        for si,rows in enumerate(sheets,1):
            data=[]
            for rn,row in enumerate(rows,1):
                cells=[]
                for ci,v in enumerate(row,1):
                    if v is None:continue
                    col='';n=ci
                    while n:n,r=divmod(n-1,26);col=chr(65+r)+col
                    cells.append(f'<c r="{col}{rn}" t="inlineStr"><is><t>{html.escape(str(v))}</t></is></c>')
                data.append(f'<row r="{rn}">'+''.join(cells)+'</row>')
            z.writestr(f'xl/worksheets/sheet{si}.xml',f'<worksheet xmlns="{ns}"><sheetData>'+''.join(data)+'</sheetData></worksheet>')


class ProcurementAcceptanceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.state=AppState(self.root/'active.sqlite3');load_demo_snapshot(self.state.repository)
        self.state.repository.save_analysis('OLD',{'analysis_id':'OLD'},'old report')
        self.server=ThreadingHTTPServer(('127.0.0.1',0),type('ProcurementTestHandler',(Handler,),{'state':self.state}))
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.events=[];self.sequence=0
        evidence=os.environ.get('B19_EVIDENCE_DIR')
        self.evidence=Path(evidence)/self._testMethodName if evidence else None
        if self.evidence:self.evidence.mkdir(parents=True,exist_ok=False)

    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.thread.join();self.state.abort_import()
        if self.evidence:
            (self.evidence/'http-transcript.json').write_text(json.dumps(self.events,ensure_ascii=False,indent=2)+'\n')
        self.temp.cleanup()

    def request(self,path,body=None,method='POST'):
        c=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=10)
        try:
            data=body if isinstance(body,bytes) else json.dumps(body or {}).encode()
            c.request(method,path,data if method=='POST' else None)
            r=c.getresponse();raw=r.read();status=r.status
            content_type=r.getheader('Content-Type','')
            value=json.loads(raw) if 'json' in content_type else raw if 'spreadsheetml' in content_type else raw.decode()
            self.events.append({'path':path,'method':method,'request':{'bytes':len(body)} if isinstance(body,bytes) else body,'status':status,'response':{'bytes':len(value)} if isinstance(value,bytes) else value})
            return status,value
        finally:c.close()

    def upload(self,lines=None,receipts=None,summary=False,expected_status=200):
        self.sequence+=1
        sheets=[[['物料编码','直接父物料','组件物料','Valid-from','Valid-to','用量','单位','工厂'],
                 ['FG-T1','FG-T1','PART-OLD','2026-01-01','2026-12-31',2,'EA','PLANT-T']]]
        if summary:
            sheets.append([['物料','工厂','库位','采购总量','已到货量','剩余采购承诺','可取消'],
                           ['PART-OLD','PLANT-T','STORE-T',50,20,30,None]])
        rows=(lines or [])+(receipts or [])
        if rows:sheets.append([[h for _,h in COLUMNS]]+[[r.get(k) for k,_ in COLUMNS] for r in rows])
        path=self.root/f'input-{self.sequence}.xlsx';workbook(path,sheets)
        if self.evidence:(self.evidence/path.name).write_bytes(path.read_bytes())
        self.assertEqual(self.request('/api/snapshot/start',{'expected_files':1})[0],200)
        status,data=self.request('/api/import/file',path.read_bytes());self.assertEqual(status,expected_status,data)
        if status==200:self.assertEqual(self.request('/api/snapshot/finish')[0],200)
        return data

    def result(self,query=None,batch=None):
        if batch:
            status,data=self.request('/api/analyze-batch',{'changes':batch});self.assertEqual(status,200,data)
            out=data['groups'][0]
        else:
            status,out=self.request('/api/analyze',query or QUERY);self.assertEqual(status,200,out)
        self.assertEqual(self.request('/api/report.md',method='GET'),(200,out['report']))
        if self.evidence:
            (self.evidence/f'result-{len(self.events)}.json').write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
        return out['result'],out['result']['procurement_impact'],out['report']

    def test_P19_01_original_summary_unchanged(self):
        self.upload(summary=True);_,p,report=self.result();l=p['lines'][0]
        self.assertEqual([l['Q'],l['R'],l['O']],[50,20,30]);self.assertIsNone(l['unit']);self.assertIsNone(l['as_of']);self.assertIsNone(l['po'])
        self.assertEqual(p['business_review_required'],['工程','采购','计划']);self.assertIsNone(p['approved_action'])
        self.assertEqual(p['review_topics'],['已到货 20 与未到货承诺 30 的处理','取消或改采购条件','新料放行与切换窗口'])
        for expected in ['累计净到货量：20','剩余未到货承诺：30','质量放行待补证','范围汇总']:self.assertIn(expected,report)
        self.assertTrue(all(v is None for v in p['overall_totals'].values()))

    def test_P19_02_lines_and_receipts(self):
        a={**receipt(quantity=12),'business_date':'2026-11-20'};b=receipt('GR2',8)
        self.upload([BASE],[a,b]);result,p,report=self.result();l=p['lines'][0]
        self.assertEqual(l['item'],'00010');self.assertEqual(l['receipt_check']['complete_net'],20)
        self.assertEqual(l['receipt_check']['status'],'一致');self.assertEqual(l['balance'],'一致')
        self.assertEqual(p['overall_totals'],{'Q':50,'R':20,'O':30,'C':0});self.assertIsNone(p['inventory_from_receipts'])
        self.assertNotIn('未提供采购订单',report+json.dumps(result,ensure_ascii=False))

    def test_P19_03_supply_scenario_not_added(self):
        self.upload([BASE]);q={**QUERY,'old_on_hand':15,'old_in_transit':5,'old_open_po':30,'old_consumable_demand':12,
                              'new_on_hand':40,'new_in_transit':0,'new_open_po':0,'new_required_demand':35}
        r,p,_=self.result(q);self.assertEqual(r['planning_impact']['recommendation'],'Run-in Change')
        self.assertIn('旧料预计剩余 38', r['planning_impact']['scenario']['items'][0]['rationale'])
        self.assertEqual(p['lines'][0]['R'],20)
        _,data=self.request('/api/analyze-batch',{'changes':[q]})
        scenario=data['items'][0]['result']['planning_impact']['scenario']
        self.assertEqual(scenario['old_exposure'],50);self.assertEqual(scenario['old_excess'],38)
        self.assertEqual(scenario['new_gap'],-5)

    def test_P19_04_unknowns_not_zero(self):
        self.upload([{**BASE,'R':None,'O':None,'C':None}]);_,p,_=self.result();l=p['lines'][0]
        self.assertEqual([l['Q'],l['R'],l['O']],[50,None,None]);self.assertEqual(l['balance'],'待补数据')
        self.assertIsNone(p['overall_totals']['O'])

    def test_P19_05_zero_preserved(self):
        self.upload([{**BASE,'Q':0,'R':0,'O':0,'C':0}]);_,p,_=self.result()
        self.assertEqual(p['overall_totals'],dict.fromkeys(('Q','R','O','C'),0))

    def test_P19_06_inconsistent_balance(self):
        self.upload([{**BASE,'O':40}]);_,p,_=self.result()
        self.assertEqual(p['lines'][0]['O'],40);self.assertEqual(p['lines'][0]['balance'],'需复核')
        self.assertTrue(all(v is None for v in p['overall_totals'].values()))

    def test_P19_07_units_and_dates(self):
        self.upload([BASE,{**BASE,'po':'PO2','unit':'KG'},{**BASE,'po':'PO3','as_of':'2026-11-29'}]);_,p,_=self.result()
        self.assertEqual(len(p['groups']),3);self.assertIsNone(p['overall_totals'])

    def test_P19_08_other_scope_excluded(self):
        self.upload([BASE,{**BASE,'po':'POX','plant':'PLANT-X'},{**BASE,'po':'POY','storage_location':'STORE-X'}]);_,p,_=self.result()
        self.assertEqual([l['po'] for l in p['lines']],['PO-P19-001']);self.assertEqual(p['overall_totals']['O'],30)

    def test_P19_09_missing_scope(self):
        self.upload([BASE,{**BASE,'po':'PON','storage_location':None}]);_,p,_=self.result({**QUERY,'planning_plant':'','storage_location':''})
        self.assertTrue(all(l['match_status']=='范围待确认' for l in p['lines']));self.assertIsNone(p['overall_totals'])
        self.assertTrue(all(g['totals']['R'] is None for g in p['groups']))

    def test_P19_10_duplicate_lines(self):
        self.upload([BASE,BASE]);_,p,_=self.result()
        self.assertEqual(len(p['lines']),1);self.assertEqual(len(p['lines'][0]['evidence']),2);self.assertEqual(p['overall_totals']['Q'],50)

    def test_P19_11_conflicting_lines_fail_batch(self):
        for row in [{**BASE,'R':21},{**BASE,'as_of':'2026-11-29'}]:
            self.upload([BASE,row],expected_status=400)
            self.assertEqual(self.state.repository.last_analysis()[1],'old report')
            with self.state.repository.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM procurement_records').fetchone()[0],0)

    def test_P19_12_cancelled_history_retained(self):
        self.upload([{**BASE,'O':0,'C':30,'status':'CANCELLED'}]);_,p,_=self.result()
        self.assertEqual(p['lines'][0]['R'],20);self.assertEqual(p['lines'][0]['balance'],'一致');self.assertIsNone(p['approved_action'])

    def test_P19_13_closed_with_open_balance(self):
        self.upload([{**BASE,'status':'CLOSED'}]);_,p,_=self.result()
        self.assertEqual(p['lines'][0]['O'],30);self.assertIsNone(p['overall_totals']['O'])
        self.assertIn('关闭/取消状态与剩余承诺冲突',p['lines'][0]['issues'])

    def test_P19_14_receipts_returns_reversal(self):
        es=[receipt(quantity=20),{**receipt('RT1',5),'event_type':'RETURN'},receipt('GR2',5),
            {**receipt('RV1',5),'event_type':'REVERSAL','ref_document':'GR2','ref_year':'2026','ref_item':'001'},
            {**receipt('RT2',5),'event_type':'RETURN'}]
        self.upload([{**BASE,'R':10,'O':40}],es);_,p,_=self.result()
        self.assertEqual(p['lines'][0]['receipt_check']['complete_net'],10);self.assertEqual(p['overall_totals']['R'],10)

    def test_P19_15_duplicate_receipts(self):
        self.upload([BASE],[receipt(),receipt()]);_,p,_=self.result()
        check=p['lines'][0]['receipt_check'];self.assertEqual(check['complete_net'],20);self.assertEqual(len(check['events']),1)
        self.assertEqual(len(check['events'][0]['evidence']),2)
        self.upload([BASE],[receipt(),receipt(quantity=21)],expected_status=400)
        self.assertIsNotNone(self.state.repository.last_analysis())

    def test_P19_16_unmatched_or_unsupported_receipts(self):
        self.upload([], [receipt()]);_,p,_=self.result();self.assertEqual(len(p['orphan_receipts']),1)
        for e in [{**receipt(),'event_type':'REVERSAL','ref_document':'NONE','ref_year':'2026','ref_item':'001'},
                  {**receipt('RV1',10),'event_type':'REVERSAL','ref_document':'GR1','ref_year':'2026','ref_item':'001'},
                  {**receipt(),'event_type':'BAD'}]:
            self.upload([BASE],[receipt(),e] if e['document']!='GR1' else [e]);_,p,_=self.result()
            self.assertEqual(p['lines'][0]['receipt_check']['status'],'需复核');self.assertIsNone(p['lines'][0]['receipt_check']['complete_net'])
        rv={**receipt('RV1'),'event_type':'REVERSAL','ref_document':'GR1','ref_year':'2026','ref_item':'001'}
        rv2={**rv,'document':'RV2','ref_document':'RV1'}
        self.upload([BASE],[receipt(),rv,rv2]);_,p,_=self.result();self.assertIsNone(p['lines'][0]['receipt_check']['complete_net'])

    def test_P19_17_partial_history(self):
        self.upload([BASE],[{**receipt(quantity=8),'history':'PARTIAL'}]);_,p,_=self.result();l=p['lines'][0]
        self.assertEqual(l['R'],20);self.assertEqual(l['receipt_check']['provided_net'],8)
        self.assertEqual(l['receipt_check']['status'],'覆盖待确认');self.assertEqual(p['overall_totals']['R'],20)

    def test_P19_18_complete_history_mismatch(self):
        self.upload([BASE],[receipt(quantity=18)]);_,p,_=self.result();l=p['lines'][0]
        self.assertEqual(l['R'],20);self.assertEqual(l['receipt_check']['provided_net'],18)
        self.assertEqual(l['receipt_check']['status'],'需复核');self.assertIsNone(p['overall_totals']['R'])

    def test_P19_19_cutoff_inclusive(self):
        self.upload([BASE],[receipt(),{**receipt('FUTURE',7),'business_date':'2026-12-01'}]);_,p,_=self.result()
        check=p['lines'][0]['receipt_check'];self.assertEqual(check['complete_net'],20)
        self.assertEqual(check['future_excluded'][0]['quantity'],7)

    def test_P19_20_dates_quality_cancelability(self):
        self.upload([{**BASE,'expected_date':'2026-12-02'}]);_,p,report=self.result()
        self.assertEqual(p['lines'][0]['date_reviews'][0]['status'],'晚于变更生效日');self.assertIn('质量放行待补证',report)
        self.upload([{**BASE,'expected_date':'2026-12-01','quality':'RELEASED','cancelability':'YES'}]);_,p,report=self.result()
        self.assertIn('日期相符仍需计划确认',report);self.assertIsNone(p['approved_action']);self.assertIsNone(p['inventory_from_receipts'])

    def test_P19_21_partial_totals(self):
        self.upload([BASE,{**BASE,'po':'PO2','Q':10,'R':None,'O':5,'C':None}]);_,p,_=self.result()
        self.assertEqual(p['overall_totals'],{'Q':60,'R':None,'O':35,'C':None})
        self.assertEqual(p['groups'][0]['known_partial']['R'],20)

    def test_P19_22_overlap_not_added(self):
        self.upload([BASE],summary=True);_,p,_=self.result()
        self.assertEqual(len(p['lines']),2);self.assertTrue(all(l['overlap'] for l in p['lines']))
        self.assertTrue(all(g['totals']['R'] is None for g in p['groups']))

    def test_P19_23_batch_and_output_consistency(self):
        self.upload([BASE],[receipt()]);r,p,report=self.result(batch=[QUERY,{**QUERY,'change_type':'quantity','old_quantity':2,'new_quantity':3}])
        self.assertEqual(len(p['lines']),1);self.assertEqual(len(p['lines'][0]['change_refs']),2)
        self.assertEqual(p['overall_totals']['R'],20)
        for section in p['sections']:
            self.assertIn(section['title'],report)
            for text in section['lines']:self.assertIn(text,report)
        self.assertIn('SRC-001 工作表 2 第 2 行',report)

    def test_P19_24_invalid_rows_preserve_snapshot(self):
        for patch in [{'Q':-1},{'Q':'NaN'},{'Q':'Infinity'},{'Q':'text'},{'as_of':'2026-02-30'},{'item':None}]:
            self.upload([{**BASE,**patch}],expected_status=400)
            self.assertEqual(self.state.repository.last_analysis()[1],'old report')
            self.assertIsNone(self.state.staging_repository)

    def test_P19_25_privacy_whitelist(self):
        sentinels={k:'PRIVATE-P19-'+k for k in ['company','supplier','person','note','path']}
        self.upload([{**BASE,**sentinels}],[{**receipt(),**sentinels}]);r,p,report=self.result()
        raw=json.dumps(r,ensure_ascii=False)+report
        with self.state.repository.connect() as c:raw+='\n'.join(c.iterdump())
        for v in sentinels.values():self.assertNotIn(v,raw)
        self.assertIn('PO-P19-001',report);self.assertIn('00010',report);self.assertIn('SRC-001',report)

    def test_P19_26_other_change_types(self):
        self.upload([BASE,{**BASE,'po':'PONEW','material':'PART-NEW'},{**BASE,'po':'POTOP','material':'FG-T1'}])
        for change,material in [('add','PART-NEW'),('remove','PART-OLD'),('quantity','PART-OLD'),('effectivity','PART-OLD')]:
            q={**QUERY,'change_type':change,'target_parent':'FG-T1','old_quantity':2,'new_quantity':3,
               'old_effective_date':'2026-11-01','new_effective_date':'2026-12-01'}
            r,p,_=self.result(q);self.assertEqual({l['material'] for l in p['lines']},{material})
            self.assertEqual(r['planning_impact']['recommendation'],'非替换变更：待计划确认')

    def test_P19_27_unknown_c_and_overreceipt(self):
        self.upload([{**BASE,'C':None}]);_,p,_=self.result();self.assertIsNone(p['lines'][0]['C']);self.assertEqual(p['lines'][0]['balance'],'待补数据')
        self.upload([{**BASE,'R':55,'O':0}]);_,p,_=self.result();self.assertEqual(p['lines'][0]['R'],55)
        self.assertEqual(p['lines'][0]['balance'],'需复核');self.assertIsNone(p['approved_action'])

    def test_P19_28_no_evidence_not_never_purchased(self):
        self.upload();_,p,report=self.result();self.assertEqual(p['status'],'当前快照未匹配采购证据')
        self.assertIsNone(p['overall_totals']);self.assertIsNone(p['approved_action']);self.assertNotIn('从未采购',report)

    def test_receipt_cutoff_mismatch_preserves_provided_net_without_replacing_R(self):
        self.upload([BASE],[{**receipt(quantity=8),'as_of':'2026-11-29','business_date':'2026-11-29'}])
        _,p,report=self.result();line=p['lines'][0];check=line['receipt_check']
        self.assertEqual(check['provided_net'],8);self.assertIsNone(check['complete_net'])
        self.assertEqual(check['status'],'覆盖待确认');self.assertEqual(line['R'],20)
        self.assertIn('流水与采购行截至日不一致',report)

    def test_procurement_snapshot_replacement_and_failed_retry(self):
        self.upload([BASE],[receipt()]);self.result()
        old=self.request('/api/analysis/last',method='GET')
        self.upload([{**BASE,'Q':-1}],expected_status=400)
        self.assertEqual(self.request('/api/analysis/last',method='GET'),old)
        self.upload();self.assertEqual(self.result()[1]['lines'],[])
        backups=list(self.root.rglob('*.sqlite3'))
        preserved=False
        for backup in backups:
            if backup==self.state.repository.database_path:continue
            with closing(sqlite3.connect(backup)) as c:
                try:preserved |= c.execute('SELECT COUNT(*) FROM procurement_records').fetchone()[0]==2
                except sqlite3.OperationalError:pass
        self.assertTrue(preserved,'Successful replacement must retain purchase evidence in a backup')

    def test_downloaded_public_example_imports_and_preserves_identifier_text(self):
        status,body=self.request('/api/procurement-template.xlsx',method='GET');self.assertEqual(status,200)
        path=Path(__file__).resolve().parents[1]/'outputs/b19-import-template/B19采购与到货导入示例.xlsx'
        self.assertEqual(body,path.read_bytes())
        self.assertEqual(self.request('/api/snapshot/start',{'expected_files':1})[0],200)
        status,info=self.request('/api/import/file',body);self.assertEqual(status,200,info)
        self.assertEqual(self.request('/api/snapshot/finish')[0],200)
        _,p,_=self.result();line=p['lines'][0]
        self.assertEqual([line['Q'],line['R'],line['O'],line['C']],[50,20,30,0])
        self.assertEqual(line['item'],'00010');self.assertEqual(line['receipt_check']['complete_net'],20)

    def test_excel_native_date_cells_use_the_same_calendar_cutoff(self):
        from datetime import datetime
        from io import BytesIO
        from xml.etree import ElementTree as ET
        path=Path(__file__).resolve().parents[1]/'outputs/b19-import-template/B19采购与到货导入示例.xlsx'
        ns='http://schemas.openxmlformats.org/spreadsheetml/2006/main';tag=lambda n:f'{{{ns}}}{n}'
        raw=BytesIO()
        with zipfile.ZipFile(path) as original,zipfile.ZipFile(raw,'w') as altered:
            styles=ET.fromstring(original.read('xl/styles.xml'));xfs=styles.find(tag('cellXfs'));index=len(xfs)
            ET.SubElement(xfs,tag('xf'),{'numFmtId':'14','fontId':'0','fillId':'0','borderId':'0','xfId':'0','applyNumberFormat':'1'})
            xfs.set('count',str(len(xfs)))
            for member in original.namelist():
                body=original.read(member)
                if member=='xl/styles.xml':body=ET.tostring(styles)
                elif member in ['xl/worksheets/sheet3.xml','xl/worksheets/sheet4.xml']:
                    sheet=ET.fromstring(body)
                    for cell in sheet.iter(tag('c')):
                        if cell.get('r') in ['H2','H3']:
                            reference=cell.get('r');cell.clear();cell.set('r',reference)
                            cell.set('s',str(index));ET.SubElement(cell,tag('v')).text=str((datetime(2026,11,30)-datetime(1899,12,30)).days)
                    body=ET.tostring(sheet)
                altered.writestr(member,body)
        self.assertEqual(self.request('/api/snapshot/start',{'expected_files':1})[0],200)
        status,info=self.request('/api/import/file',raw.getvalue());self.assertEqual(status,200,info)
        self.assertEqual(self.request('/api/snapshot/finish')[0],200)
        _,p,_=self.result();self.assertEqual(p['lines'][0]['as_of'],'2026-11-30')
        self.assertEqual(p['lines'][0]['receipt_check']['complete_net'],20)


if __name__=='__main__':unittest.main()
