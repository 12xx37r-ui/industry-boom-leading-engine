from __future__ import annotations

import io
import re
import zipfile
import xml.etree.ElementTree as ET
from typing import Any


_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_REL_DOC = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_REL_PKG = "http://schemas.openxmlformats.org/package/2006/relationships"


def _column_index(reference: str) -> int:
    match = re.match(r"([A-Z]+)", reference.upper())
    if not match:
        return 0
    value = 0
    for char in match.group(1):
        value = value * 26 + (ord(char) - 64)
    return value - 1


def _shared_strings(archive: zipfile.ZipFile) -> list[str]:
    name = "xl/sharedStrings.xml"
    if name not in archive.namelist():
        return []
    root = ET.fromstring(archive.read(name))
    values: list[str] = []
    for item in root.findall(f"{{{_MAIN}}}si"):
        values.append("".join(node.text or "" for node in item.iter(f"{{{_MAIN}}}t")))
    return values


def _sheet_target(archive: zipfile.ZipFile, sheet_name: str | None) -> str:
    workbook = ET.fromstring(archive.read("xl/workbook.xml"))
    relations = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
    targets = {
        rel.attrib.get("Id"): rel.attrib.get("Target")
        for rel in relations.findall(f"{{{_REL_PKG}}}Relationship")
    }
    sheets = workbook.find(f"{{{_MAIN}}}sheets")
    if sheets is None:
        raise ValueError("xlsx workbook has no sheets")
    chosen = None
    for sheet in sheets.findall(f"{{{_MAIN}}}sheet"):
        if sheet_name is None or sheet.attrib.get("name") == sheet_name:
            chosen = sheet
            break
    if chosen is None:
        raise ValueError(f"xlsx sheet not found: {sheet_name}")
    relation_id = chosen.attrib.get(f"{{{_REL_DOC}}}id")
    target = targets.get(relation_id)
    if not target:
        raise ValueError("xlsx sheet relation missing")
    target = target.lstrip("/")
    if not target.startswith("xl/"):
        target = f"xl/{target}"
    return target


def read_xlsx_rows(payload: bytes, sheet_name: str | None = None) -> list[list[Any]]:
    if not payload.startswith(b"PK"):
        raise ValueError("payload is not an xlsx zip")
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        shared = _shared_strings(archive)
        target = _sheet_target(archive, sheet_name)
        root = ET.fromstring(archive.read(target))
        output: list[list[Any]] = []
        sheet_data = root.find(f"{{{_MAIN}}}sheetData")
        if sheet_data is None:
            return output
        for row in sheet_data.findall(f"{{{_MAIN}}}row"):
            values: list[Any] = []
            for cell in row.findall(f"{{{_MAIN}}}c"):
                index = _column_index(cell.attrib.get("r", "A1"))
                while len(values) <= index:
                    values.append(None)
                cell_type = cell.attrib.get("t")
                value_node = cell.find(f"{{{_MAIN}}}v")
                if cell_type == "inlineStr":
                    inline = cell.find(f"{{{_MAIN}}}is")
                    value: Any = "" if inline is None else "".join(
                        node.text or "" for node in inline.iter(f"{{{_MAIN}}}t")
                    )
                elif value_node is None:
                    value = None
                elif cell_type == "s":
                    shared_index = int(value_node.text or "0")
                    value = shared[shared_index] if 0 <= shared_index < len(shared) else None
                elif cell_type in {"str", "e"}:
                    value = value_node.text
                elif cell_type == "b":
                    value = value_node.text == "1"
                else:
                    raw = value_node.text or ""
                    try:
                        number = float(raw)
                        value = int(number) if number.is_integer() else number
                    except ValueError:
                        value = raw
                values[index] = value
            output.append(values)
        return output


def list_xlsx_sheets(payload: bytes) -> list[str]:
    if not payload.startswith(b"PK"):
        raise ValueError("payload is not an xlsx zip")
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        sheets = workbook.find(f"{{{_MAIN}}}sheets")
        if sheets is None:
            return []
        return [str(sheet.attrib.get("name") or "") for sheet in sheets.findall(f"{{{_MAIN}}}sheet")]
