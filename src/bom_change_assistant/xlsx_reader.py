"""Small, dependency-free XLSX reader for tabular ERP exports.

It intentionally reads values only. Macros, drawings, formulas and formatting are
never executed or copied.
"""

from __future__ import annotations

import posixpath
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterator
from xml.etree import ElementTree as ET

MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_DOC_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
REL_PKG_NS = "http://schemas.openxmlformats.org/package/2006/relationships"

_CELL_REF = re.compile(r"([A-Z]+)")
_DATE_TOKEN = re.compile(r"(?<!\\)[ymdhs]", re.IGNORECASE)
_BUILTIN_DATE_FORMATS = {
    14,
    15,
    16,
    17,
    18,
    19,
    20,
    21,
    22,
    27,
    28,
    29,
    30,
    31,
    32,
    33,
    34,
    35,
    36,
    45,
    46,
    47,
    50,
    51,
    52,
    53,
    54,
    55,
    56,
    57,
    58,
}


@dataclass(frozen=True)
class SheetInfo:
    name: str
    path: str


def column_index(cell_reference: str) -> int:
    match = _CELL_REF.match(cell_reference or "")
    if not match:
        return 0
    result = 0
    for char in match.group(1):
        result = result * 26 + (ord(char) - 64)
    return result - 1


def _looks_like_date_format(format_code: str) -> bool:
    cleaned = re.sub(r'"[^"]*"', "", format_code)
    cleaned = re.sub(r"\[[^\]]+\]", "", cleaned)
    cleaned = cleaned.replace("\\", "")
    return bool(_DATE_TOKEN.search(cleaned))


class XlsxWorkbook:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self._zip = zipfile.ZipFile(self.path)
        members = self._zip.infolist()
        if len(members) > 2_000 or sum(item.file_size for item in members) > 256 * 1024 * 1024:
            self._zip.close()
            raise ValueError("XLSX 解压规模超过安全上限")
        self._shared_strings = self._read_shared_strings()
        self._date1904, self._sheets = self._read_workbook()
        self._date_styles = self._read_date_styles()

    def close(self) -> None:
        self._zip.close()

    def __enter__(self) -> "XlsxWorkbook":
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def sheets(self) -> list[SheetInfo]:
        return list(self._sheets)

    def _read_shared_strings(self) -> list[str]:
        try:
            root = ET.fromstring(self._zip.read("xl/sharedStrings.xml"))
        except KeyError:
            return []
        values: list[str] = []
        for item in root.findall(f"{{{MAIN_NS}}}si"):
            parts = [node.text or "" for node in item.iter(f"{{{MAIN_NS}}}t")]
            values.append("".join(parts))
        return values

    def _read_workbook(self) -> tuple[bool, list[SheetInfo]]:
        workbook = ET.fromstring(self._zip.read("xl/workbook.xml"))
        workbook_pr = workbook.find(f"{{{MAIN_NS}}}workbookPr")
        date1904 = bool(
            workbook_pr is not None
            and workbook_pr.attrib.get("date1904", "").lower() in {"1", "true"}
        )

        relationships = ET.fromstring(
            self._zip.read("xl/_rels/workbook.xml.rels")
        )
        targets = {
            rel.attrib["Id"]: rel.attrib["Target"]
            for rel in relationships.findall(f"{{{REL_PKG_NS}}}Relationship")
        }
        sheets: list[SheetInfo] = []
        sheets_node = workbook.find(f"{{{MAIN_NS}}}sheets")
        if sheets_node is None:
            return date1904, sheets
        for sheet in sheets_node.findall(f"{{{MAIN_NS}}}sheet"):
            rel_id = sheet.attrib.get(f"{{{REL_DOC_NS}}}id", "")
            target = targets.get(rel_id, "")
            if not target:
                continue
            if target.startswith("/"):
                target = target.lstrip("/")
            else:
                target = posixpath.normpath(posixpath.join("xl", target))
            sheets.append(SheetInfo(sheet.attrib.get("name", "Sheet"), target))
        return date1904, sheets

    def _read_date_styles(self) -> set[int]:
        try:
            root = ET.fromstring(self._zip.read("xl/styles.xml"))
        except KeyError:
            return set()
        custom_formats: dict[int, str] = {}
        num_fmts = root.find(f"{{{MAIN_NS}}}numFmts")
        if num_fmts is not None:
            for item in num_fmts.findall(f"{{{MAIN_NS}}}numFmt"):
                custom_formats[int(item.attrib["numFmtId"])] = item.attrib.get(
                    "formatCode", ""
                )

        result: set[int] = set()
        cell_xfs = root.find(f"{{{MAIN_NS}}}cellXfs")
        if cell_xfs is None:
            return result
        for style_index, xf in enumerate(cell_xfs.findall(f"{{{MAIN_NS}}}xf")):
            num_fmt_id = int(xf.attrib.get("numFmtId", "0"))
            if num_fmt_id in _BUILTIN_DATE_FORMATS or _looks_like_date_format(
                custom_formats.get(num_fmt_id, "")
            ):
                result.add(style_index)
        return result

    def _excel_datetime(self, serial: float) -> datetime:
        base = datetime(1904, 1, 1) if self._date1904 else datetime(1899, 12, 30)
        return base + timedelta(days=serial)

    def _cell_value(self, cell: ET.Element) -> Any:
        cell_type = cell.attrib.get("t", "")
        style_id = int(cell.attrib.get("s", "0"))
        if cell_type == "inlineStr":
            return "".join(
                node.text or "" for node in cell.iter(f"{{{MAIN_NS}}}t")
            )
        value_node = cell.find(f"{{{MAIN_NS}}}v")
        if value_node is None or value_node.text is None:
            return None
        raw = value_node.text
        if cell_type == "s":
            try:
                return self._shared_strings[int(raw)]
            except (ValueError, IndexError):
                return ""
        if cell_type in {"str", "e"}:
            return raw
        if cell_type == "b":
            return raw == "1"
        try:
            number = float(raw)
        except ValueError:
            return raw
        if style_id in self._date_styles:
            return self._excel_datetime(number)
        if number.is_integer():
            return int(number)
        return number

    def iter_rows(self, sheet: SheetInfo) -> Iterator[tuple[int, list[Any]]]:
        with self._zip.open(sheet.path) as stream:
            for _, element in ET.iterparse(stream, events=("end",)):
                if element.tag != f"{{{MAIN_NS}}}row":
                    continue
                row_number = int(element.attrib.get("r", "0") or "0")
                values: list[Any] = []
                for cell in element.findall(f"{{{MAIN_NS}}}c"):
                    index = column_index(cell.attrib.get("r", ""))
                    if index >= len(values):
                        values.extend([None] * (index + 1 - len(values)))
                    values[index] = self._cell_value(cell)
                yield row_number, values
                element.clear()

    def locate_header(
        self, sheet: SheetInfo, max_rows: int = 20
    ) -> tuple[int, list[str]]:
        candidates: list[tuple[int, list[Any]]] = []
        for row_number, values in self.iter_rows(sheet):
            candidates.append((row_number, values))
            if len(candidates) >= max_rows:
                break
        if not candidates:
            return 1, []
        row_number, values = max(
            candidates,
            key=lambda item: (
                sum(value not in (None, "") for value in item[1]),
                sum(isinstance(value, str) for value in item[1]),
            ),
        )
        return row_number, [str(value).strip() if value is not None else "" for value in values]

    def iter_table(
        self, sheet: SheetInfo
    ) -> tuple[list[str], Iterator[tuple[int, list[Any]]]]:
        header_row, headers = self.locate_header(sheet)

        def rows() -> Iterator[tuple[int, list[Any]]]:
            for row_number, values in self.iter_rows(sheet):
                if row_number <= header_row:
                    continue
                if not any(value not in (None, "") for value in values):
                    continue
                if len(values) < len(headers):
                    values.extend([None] * (len(headers) - len(values)))
                yield row_number, values

        return headers, rows()
