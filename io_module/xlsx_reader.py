"""Minimální read-only XLSX reader (jen stdlib).

Vrací každou buňku přesně tak, jak je uložená v XML listu — čísla zůstávají
jako desetinný text, nikdy float — takže parsery mohou stavět přesné Decimaly.
Metadata rozměru listu se ignorují (XTB zapisuje chybná). Vzorce, styly ani
formátování se nevyhodnocují.

Převzato z projektu LEDGER_TAX (ledger_tax/xlsx.py) bez změny logiky.
"""
from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from typing import Dict, List, Optional

_MAIN = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
_REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PKG_REL = "{http://schemas.openxmlformats.org/package/2006/relationships}"
_CELL_REF_RE = re.compile(r"([A-Z]{1,3})([0-9]+)", re.ASCII)
MAX_PART_SIZE = 200 * 1024 * 1024  # nekomprimované bajty na jednu XML část


class XlsxError(Exception):
    pass


@dataclass(frozen=True)
class Workbook:
    sheets: Dict[str, Dict[int, Dict[int, str]]]  # název -> číslo řádku -> sloupec (1 = A) -> text
    date1904: bool


def read_xlsx(data: bytes) -> Workbook:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise XlsxError("Soubor není platný XLSX (ZIP) dokument.") from exc
    with archive:
        workbook = _xml(archive, "xl/workbook.xml")
        rels = _xml(archive, "xl/_rels/workbook.xml.rels")
        targets = {r.get("Id"): r.get("Target", "") for r in rels.iter(f"{_PKG_REL}Relationship")}
        shared = _shared_strings(archive)
        pr = workbook.find(f"{_MAIN}workbookPr")
        date1904 = pr is not None and pr.get("date1904", "").lower() in ("1", "true")

        sheets = {}
        for sheet in workbook.iter(f"{_MAIN}sheet"):
            target = targets.get(sheet.get(f"{_REL}id"))
            if not target:
                raise XlsxError(f"List {sheet.get('name')!r} nemá platný odkaz v workbook.xml.rels.")
            part = target.lstrip("/") if target.startswith("/") else f"xl/{target}"
            sheets[sheet.get("name")] = _sheet_rows(_xml(archive, part), shared)
    return Workbook(sheets=sheets, date1904=date1904)


def _xml(archive: zipfile.ZipFile, name: str) -> ET.Element:
    try:
        info = archive.getinfo(name)
    except KeyError as exc:
        raise XlsxError(f"XLSX neobsahuje část {name}.") from exc
    if info.file_size > MAX_PART_SIZE:
        raise XlsxError(f"Část {name} je příliš velká ({info.file_size} B).")
    try:
        return ET.fromstring(archive.read(info))
    except (ET.ParseError, zipfile.BadZipFile, OSError) as exc:
        raise XlsxError(f"Část {name} nelze přečíst: {exc}") from exc


def _shared_strings(archive: zipfile.ZipFile) -> List[str]:
    if "xl/sharedStrings.xml" not in archive.namelist():
        return []
    root = _xml(archive, "xl/sharedStrings.xml")
    return [_text(si) for si in root.iter(f"{_MAIN}si")]


def _text(node: ET.Element) -> str:
    # Prostý <t> nebo rich-text běhy <r><t>; fonetické nápovědy (<rPh>) nejsou text buňky.
    t = node.find(f"{_MAIN}t")
    if t is not None:
        return t.text or ""
    return "".join(t.text or "" for r in node.findall(f"{_MAIN}r") for t in r.findall(f"{_MAIN}t"))


def _sheet_rows(root: ET.Element, shared: List[str]) -> Dict[int, Dict[int, str]]:
    data = root.find(f"{_MAIN}sheetData")
    rows: Dict[int, Dict[int, str]] = {}
    if data is None:
        return rows
    row_no = 0
    for row in data.iter(f"{_MAIN}row"):
        row_no = int(row.get("r")) if row.get("r") else row_no + 1
        cells: Dict[int, str] = {}
        col = 0
        for cell in row.iter(f"{_MAIN}c"):
            ref = cell.get("r")
            if ref:
                match = _CELL_REF_RE.fullmatch(ref)
                if not match:
                    raise XlsxError(f"Neplatný odkaz buňky {ref!r}.")
                col = _column_index(match.group(1))
            else:
                col += 1
            value = _cell_value(cell, shared)
            if value is not None:
                cells[col] = value
        rows[row_no] = cells
    return rows


def _cell_value(cell: ET.Element, shared: List[str]) -> Optional[str]:
    kind = cell.get("t", "n")
    if kind == "inlineStr":
        node = cell.find(f"{_MAIN}is")
        return _text(node) if node is not None else None
    v = cell.find(f"{_MAIN}v")
    if v is None or v.text is None:
        return None
    if kind == "s":
        try:
            return shared[int(v.text)]
        except (ValueError, IndexError) as exc:
            raise XlsxError(f"Neplatný index sdíleného řetězce {v.text!r}.") from exc
    return v.text  # n, str, b, e, d: ponecháno tak, jak je uloženo


def _column_index(letters: str) -> int:
    index = 0
    for ch in letters:
        index = index * 26 + ord(ch) - ord("A") + 1
    return index
