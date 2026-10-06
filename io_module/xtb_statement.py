"""XTB výpis → otevřené pozice STOCK/ETF/ETC pro synchronizaci (XTB sync, M1).

Čte jen list "Open Positions" a řádky "Stock purchase" z listu "Cash Operations",
které jsou přes Position ID navázané na otevřené loty. Uzavřené obchody se nečtou.
Parser nic nepočítá ani nerekonstruuje — rekonstrukce nákladů je v position_sync (M2).

Vstup: ZIP s právě jedním XLSX výpisem, nebo samotné XLSX.
Podporovány jsou jen EUR účty; jiná měna se odmítne před čímkoli dalším.
Časy (Excel serial v UTC) se převádějí na Europe/Prague bez časové zóny,
zaokrouhlené na sekundy — stejná konvence jako ručně zadané časy v ledgeru.

Číslo účtu se nikdy neukládá ani nevypisuje (ani v chybových hláškách).
"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from io_module.xlsx_reader import MAX_PART_SIZE, Workbook, XlsxError, read_xlsx

SHEET_OPEN = "Open Positions"
SHEET_CASH = "Cash Operations"
SUPPORTED_CATEGORIES = frozenset({"STOCK", "ETF", "ETC"})
SUPPORTED_CURRENCY = "EUR"
PURCHASE_TYPE = "Stock purchase"
LOCAL_TZ = ZoneInfo("Europe/Prague")
MAX_HEADER_SCAN = 30

# logický sloupec -> přijímané názvy v hlavičce
OPEN_COLUMNS = {
    "Position": ("Instrument/Position",),
    "Ticker": ("Ticker",),
    "Category": ("Category",),
    "Type": ("Type",),
    "Volume": ("Volume",),
    "Value": ("Value",),
    "Open time": ("Open time (UTC)",),
    "Gross Profit": ("Gross Profit",),
}
CASH_COLUMNS = {
    "Type": ("Type",),
    "Ticker": ("Ticker",),
    "Time": ("Time", "Time (UTC)"),
    "Amount": ("Amount",),
    "Comment": ("Comment",),
    "Position ID": ("Position ID",),
}
_AS_OF_LABEL = "Data as of report generated"

_STATEMENT_NAME_RE = re.compile(r"([A-Z]{3})_[0-9]+_\d{4}-\d{2}-\d{2}_\d{4}-\d{2}-\d{2}\.xlsx", re.ASCII)
_QTY = r"[0-9]+(?:\.[0-9]+)?"
_PURCHASE_COMMENT_RE = re.compile(rf"OPEN BUY ({_QTY})(?:/({_QTY}))? @ ({_QTY})", re.ASCII)
_DECIMAL_RE = re.compile(r"[+-]?([0-9]+(\.[0-9]*)?|\.[0-9]+)([eE][+-]?[0-9]+)?", re.ASCII)
_POSITION_ID_RE = re.compile(r"([0-9]+)(?:\.0+)?", re.ASCII)


class XtbStatementError(Exception):
    """Výpis nelze použít; zpráva je určená uživateli a neobsahuje číslo účtu."""


@dataclass(frozen=True)
class XtbPurchase:
    """Řádek "Stock purchase" z Cash Operations navázaný na otevřený lot."""
    position_id: str
    ticker: str
    comment: str
    quantity: Optional[Decimal]        # a z "OPEN BUY a[/b] @ p"; None = komentář nelze rozpoznat
    reported_total: Optional[Decimal]  # b (velikost pozice po nákupu), bez "/b" rovno a; None = nerozpoznáno
    amount: Decimal                    # částka v měně účtu přesně jako ve výpisu (nákup = záporná)
    time_local: datetime


@dataclass(frozen=True)
class XtbOpenLot:
    """Jeden otevřený lot (Position ID) z listu Open Positions."""
    position_id: str
    ticker: str
    category: str
    volume: Decimal
    open_time_local: datetime
    value: Optional[Decimal]           # aktuální hodnota v měně účtu (jen pro kontrolu nákladů)
    gross_profit: Optional[Decimal]    # hrubý zisk v měně účtu (jen pro kontrolu nákladů)
    purchases: Tuple[XtbPurchase, ...]


@dataclass(frozen=True)
class XtbOpenPositionsSnapshot:
    currency: str
    as_of_local: datetime
    lots: Tuple[XtbOpenLot, ...]       # jen podporované kategorie, seřazeno (ticker, čas, Position ID)
    excluded_count: int                # otevřené loty nepodporovaných kategorií (CFD apod.)


# ── vstup: soubor ─────────────────────────────────────────────────────────────

def load_xtb_open_positions(path) -> XtbOpenPositionsSnapshot:
    """Načte XTB výpis (ZIP nebo XLSX) ze zadané cesty. Pouze čte."""
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise XtbStatementError(f"Soubor výpisu nelze přečíst: {exc.strerror or 'chyba čtení'}.") from exc
    name, xlsx = _extract_xlsx(Path(path).name, data)
    try:
        book = read_xlsx(xlsx)
    except XlsxError as exc:
        raise XtbStatementError(f"Výpis není čitelný XLSX dokument: {exc}") from exc
    return parse_xtb_open_positions(book, name)


def _extract_xlsx(file_name: str, data: bytes) -> Tuple[str, bytes]:
    """XLSX výpis uvnitř ZIPu; samotné XLSX se přijme také."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise XtbStatementError("Soubor není ZIP ani XLSX výpis XTB.") from exc
    with archive:
        names = archive.namelist()
        if "xl/workbook.xml" in names:
            return file_name, data
        statements = [n for n in names if n.lower().endswith(".xlsx") and not n.endswith("/")]
        if len(statements) != 1:
            raise XtbStatementError(f"ZIP musí obsahovat právě jeden výpis XLSX, nalezeno {len(statements)}.")
        info = archive.getinfo(statements[0])
        if info.file_size > MAX_PART_SIZE:
            raise XtbStatementError("Výpis XLSX v ZIPu je příliš velký.")
        try:
            return PurePosixPath(statements[0]).name, archive.read(info)
        except (zipfile.BadZipFile, OSError) as exc:
            raise XtbStatementError("Výpis XLSX nelze ze ZIPu přečíst.") from exc


# ── parser ────────────────────────────────────────────────────────────────────

def parse_xtb_open_positions(book: Workbook, statement_name: str) -> XtbOpenPositionsSnapshot:
    open_rows = _sheet(book, SHEET_OPEN)
    cash_rows = _sheet(book, SHEET_CASH)
    open_header, open_index = _find_header(open_rows, SHEET_OPEN, OPEN_COLUMNS)
    cash_header, cash_index = _find_header(cash_rows, SHEET_CASH, CASH_COLUMNS)

    currency = _currency(open_rows, open_header, statement_name)
    as_of = _as_of(open_rows, open_header, book.date1904)

    lots, excluded = _open_lots(open_rows, open_header, open_index, book.date1904)
    purchases = _purchases(cash_rows, cash_header, cash_index, {lot["position_id"] for lot in lots},
                           book.date1904)

    result = tuple(sorted(
        (XtbOpenLot(purchases=tuple(purchases.get(lot["position_id"], ())), **lot) for lot in lots),
        key=lambda lot: (lot.ticker, lot.open_time_local, lot.position_id),
    ))
    return XtbOpenPositionsSnapshot(currency=currency, as_of_local=as_of, lots=result, excluded_count=excluded)


def _sheet(book: Workbook, name: str) -> Dict[int, Dict[int, str]]:
    rows = book.sheets.get(name)
    if rows is None:
        raise XtbStatementError(f"Výpis neobsahuje list {name!r}.")
    return rows


def _find_header(rows, sheet: str, columns: Dict[str, Tuple[str, ...]]) -> Tuple[int, Dict[str, int]]:
    for line in sorted(rows)[:MAX_HEADER_SCAN]:
        header = rows[line]
        index: Dict[str, int] = {}
        for logical, accepted in columns.items():
            found = [col for col, value in header.items() if value in accepted]
            if len(found) > 1:
                raise XtbStatementError(f"List {sheet!r}: duplicitní sloupec {logical!r}.")
            if found:
                index[logical] = found[0]
        if len(index) == len(columns):
            return line, index
    raise XtbStatementError(f"List {sheet!r}: nenalezena hlavička se sloupci {', '.join(columns)}.")


def _data_rows(rows, header_line: int):
    for line in sorted(rows):
        if line > header_line and any(v != "" for v in rows[line].values()):
            yield line, rows[line]


def _currency(rows, header_line: int, statement_name: str) -> str:
    """Měna účtu z názvu výpisu a ze souhrnné tabulky Open Positions; musí se shodovat a být EUR."""
    found = set()
    match = _STATEMENT_NAME_RE.fullmatch(statement_name or "")
    if match:
        found.add(match.group(1))
    currency_col = None
    for line in sorted(rows):
        if line >= header_line:
            break
        cells = rows[line]
        if currency_col is None:
            cols = [col for col, value in cells.items() if value == "Currency"]
            if cols and "Metric" in cells.values():
                currency_col = cols[0]
            continue
        value = cells.get(currency_col, "").strip()
        if value:
            found.add(value)
    if not found:
        raise XtbStatementError("Nelze určit měnu účtu výpisu.")
    if len(found) > 1:
        raise XtbStatementError(f"Měna účtu výpisu není jednoznačná ({', '.join(sorted(found))}).")
    currency = found.pop()
    if currency != SUPPORTED_CURRENCY:
        raise XtbStatementError(
            f"Podporovány jsou jen EUR účty XTB; výpis je v měně {currency}. Synchronizace odmítnuta."
        )
    return currency


def _as_of(rows, header_line: int, date1904: bool) -> datetime:
    for line in sorted(rows):
        if line >= header_line:
            break
        if rows[line].get(1) == _AS_OF_LABEL:
            return _excel_local(rows[line].get(2, ""), date1904, f"{SHEET_OPEN} / {_AS_OF_LABEL}")
    raise XtbStatementError(f"List {SHEET_OPEN!r}: chybí údaj {_AS_OF_LABEL!r}.")


def _open_lots(rows, header_line: int, index: Dict[str, int], date1904: bool):
    """Řádky pozic → slovníky lotů; souhrnné řádky nástrojů nesou kategorii."""
    cell = lambda row, key: row.get(index[key], "").strip()
    summary_category: Dict[str, str] = {}
    position_rows = []
    for line, row in _data_rows(rows, header_line):
        position_id = _position_id(cell(row, "Position"))
        if position_id is None:
            if cell(row, "Ticker") and cell(row, "Category"):
                summary_category.setdefault(cell(row, "Ticker"), cell(row, "Category"))
            continue
        position_rows.append((line, position_id, row))

    lots: List[dict] = []
    seen: set = set()
    excluded = 0
    for line, position_id, row in position_rows:
        where = f"{SHEET_OPEN}, řádek {line}"
        ticker = cell(row, "Ticker")
        if not ticker:
            raise XtbStatementError(f"{where}: chybí Ticker.")
        category = cell(row, "Category") or summary_category.get(ticker, "")
        if category not in SUPPORTED_CATEGORIES:
            excluded += 1
            continue
        if position_id in seen:
            raise XtbStatementError(f"{where}: duplicitní Position ID.")
        seen.add(position_id)
        if cell(row, "Type") != "BUY":
            raise XtbStatementError(f"{where}: nepodporovaný typ pozice {cell(row, 'Type')!r}.")
        volume = _decimal(cell(row, "Volume"), f"{where} / Volume")
        if volume <= 0:
            raise XtbStatementError(f"{where}: neplatný objem {cell(row, 'Volume')!r}.")
        lots.append(dict(
            position_id=position_id,
            ticker=ticker,
            category=category,
            volume=volume,
            open_time_local=_excel_local(cell(row, "Open time"), date1904, f"{where} / Open time (UTC)"),
            value=_optional_decimal(cell(row, "Value"), f"{where} / Value"),
            gross_profit=_optional_decimal(cell(row, "Gross Profit"), f"{where} / Gross Profit"),
        ))
    return lots, excluded


def _purchases(rows, header_line: int, index: Dict[str, int], open_ids: set, date1904: bool):
    """Position ID -> nákupy "Stock purchase" pro otevřené loty (ostatní řádky se ignorují)."""
    cell = lambda row, key: row.get(index[key], "").strip()
    result: Dict[str, List[XtbPurchase]] = {}
    for line, row in _data_rows(rows, header_line):
        if cell(row, "Type") != PURCHASE_TYPE:
            continue
        position_id = _position_id(cell(row, "Position ID"))
        if position_id not in open_ids:
            continue
        where = f"{SHEET_CASH}, řádek {line}"
        comment = cell(row, "Comment")
        match = _PURCHASE_COMMENT_RE.fullmatch(comment)
        quantity = Decimal(match.group(1)) if match else None
        reported_total = Decimal(match.group(2) or match.group(1)) if match else None
        result.setdefault(position_id, []).append(XtbPurchase(
            position_id=position_id,
            ticker=cell(row, "Ticker"),
            comment=comment,
            quantity=quantity,
            reported_total=reported_total,
            amount=_decimal(cell(row, "Amount"), f"{where} / Amount"),
            time_local=_excel_local(cell(row, "Time"), date1904, f"{where} / Time"),
        ))
    return result


# ── hodnoty ───────────────────────────────────────────────────────────────────

def _position_id(text: str) -> Optional[str]:
    match = _POSITION_ID_RE.fullmatch(text or "")
    return match.group(1) if match else None


def _decimal(text: str, where: str) -> Decimal:
    if not _DECIMAL_RE.fullmatch(text or ""):
        raise XtbStatementError(f"{where}: neplatné číslo {text!r}.")
    try:
        return Decimal(text)
    except InvalidOperation as exc:
        raise XtbStatementError(f"{where}: neplatné číslo {text!r}.") from exc


def _optional_decimal(text: str, where: str) -> Optional[Decimal]:
    return None if text == "" else _decimal(text, where)


def _excel_local(text: str, date1904: bool, where: str) -> datetime:
    """Excel serial (UTC) → Europe/Prague bez časové zóny, zaokrouhleno na sekundy."""
    days = _decimal(text, where)
    if days <= 0:
        raise XtbStatementError(f"{where}: neplatný čas {text!r}.")
    base = datetime(1904, 1, 1, tzinfo=timezone.utc) if date1904 else datetime(1899, 12, 30, tzinfo=timezone.utc)
    seconds = int((days * 86400).quantize(Decimal(1), rounding=ROUND_HALF_UP))
    return (base + timedelta(seconds=seconds)).astimezone(LOCAL_TZ).replace(tzinfo=None)
