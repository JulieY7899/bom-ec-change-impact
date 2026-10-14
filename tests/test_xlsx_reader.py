from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from bom_change_assistant.importers import classify_source
from bom_change_assistant.xlsx_reader import XlsxWorkbook


WORKBOOK_XML = """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
 <sheets><sheet name="Data" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""

RELS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Id="rId1"
  Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet"
  Target="worksheets/sheet1.xml"/>
</Relationships>"""

SHEET_XML = """<?xml version="1.0" encoding="UTF-8"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">
 <sheetData>
  <row r="1">
   <c r="A1" t="inlineStr"><is><t>物料编码</t></is></c>
   <c r="B1" t="inlineStr"><is><t>组件物料</t></is></c>
   <c r="C1" t="inlineStr"><is><t>直接父物料</t></is></c>
   <c r="D1" t="inlineStr"><is><t>Valid-from</t></is></c>
   <c r="E1" t="inlineStr"><is><t>Valid-to</t></is></c>
  </row>
  <row r="2">
   <c r="A2" t="inlineStr"><is><t>FG-A</t></is></c>
   <c r="B2" t="inlineStr"><is><t>COMP-001</t></is></c>
   <c r="C2" t="inlineStr"><is><t>FG-A</t></is></c>
   <c r="D2" t="inlineStr"><is><t>2025-01-01</t></is></c>
   <c r="E2" t="inlineStr"><is><t>2099-12-31</t></is></c>
  </row>
 </sheetData>
</worksheet>"""


class XlsxReaderTests(unittest.TestCase):
    def test_classifies_planning_supply_demo_headers(self) -> None:
        self.assertEqual(
            classify_source(
                [
                    "场景编号",
                    "数据属性",
                    "旧物料",
                    "新物料",
                    "工厂",
                    "库位",
                    "旧料可用库存",
                    "新料可用库存",
                ]
            ),
            "planning_supply_demo",
        )

    def test_reads_minimal_xlsx_and_classifies_headers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "sample.xlsx"
            with zipfile.ZipFile(path, "w") as archive:
                archive.writestr("xl/workbook.xml", WORKBOOK_XML)
                archive.writestr("xl/_rels/workbook.xml.rels", RELS_XML)
                archive.writestr("xl/worksheets/sheet1.xml", SHEET_XML)
            with XlsxWorkbook(path) as workbook:
                headers, rows = workbook.iter_table(workbook.sheets[0])
                self.assertEqual(classify_source(headers), "expanded_bom")
                row_number, values = next(rows)
                self.assertEqual(row_number, 2)
                self.assertEqual(values[1], "COMP-001")


if __name__ == "__main__":
    unittest.main()
