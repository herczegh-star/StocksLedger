"""Syntetické XTB výpisy (XLSX / ZIP) s rozložením jako skutečný export. Žádná reálná data.

Účet, tickery, Position ID i částky jsou smyšlené.
"""
from __future__ import annotations

import io
import zipfile
from datetime import datetime
from typing import Dict, List, Optional
from xml.sax.saxutils import escape

FAKE_ACCOUNT = "98765432"

CASH_HEADER = ("Type", "Instrument", "Ticker", "Category", "Time", "Amount", "ID", "Comment", "Product",
               "Position ID")
OPEN_HEADER = ("Product", "Instrument/Position", "Ticker", "Category", "Type", "Volume", "Value", "Current price",
               "Open price", "Open time (UTC)", "Stop Loss", "Take Profit", "Net Profit %", "Net Profit",
               "Gross Profit", "Margin", "Open Commission", "Swap", "Rollover")


class Num(str):
    """Číselná buňka: zapíše se jako <v>text</v> bez typu řetězce, přesně jak je zadána."""


def serial(dt_utc: datetime) -> Num:
    """Excel serial UTC času uložený jako binární float text (jako XTB)."""
    delta = dt_utc - datetime(1899, 12, 30)
    return Num(repr(delta.days + (delta.seconds * 1000 + delta.microseconds // 1000) / 86_400_000))


class XtbStatementBuilder:
    def __init__(self, currency: str = "EUR", account: str = FAKE_ACCOUNT,
                 as_of_utc: datetime = datetime(2026, 10, 6, 14, 57, 52),
                 summary_currency: Optional[str] = None):
        self.currency = currency
        self.account = account
        self.as_of_utc = as_of_utc
        self.summary_currency = summary_currency or currency
        self.cash: List[tuple] = []
        self._tickers: Dict[str, str] = {}          # ticker -> kategorie souhrnného řádku
        self._positions: List[tuple] = []
        self._next_id = 1000

    # ── Open Positions ────────────────────────────────────────────────────
    def open_lot(self, position: str, ticker: str, volume: str, open_utc: datetime, *,
                 value: str = "100.00", gross: str = "10.00", category: str = "STOCK",
                 type_: str = "BUY", row_category: str = "") -> "XtbStatementBuilder":
        self._tickers.setdefault(ticker, category)
        row = [""] * len(OPEN_HEADER)
        row[0], row[1], row[2], row[3], row[4] = "My Trades", position, ticker, row_category, type_
        row[5], row[6], row[9], row[14] = Num(volume), Num(value) if value else "", serial(open_utc), \
            Num(gross) if gross else ""
        self._positions.append(tuple(row))
        return self

    # ── Cash Operations ───────────────────────────────────────────────────
    def purchase(self, position: str, ticker: str, comment: str, amount: str,
                 time_utc: datetime) -> "XtbStatementBuilder":
        return self.cash_op("Stock purchase", ticker, comment, amount, time_utc, position)

    def cash_op(self, type_: str, ticker: str, comment: str, amount: str, time_utc: datetime,
                position: str = "") -> "XtbStatementBuilder":
        self._next_id += 1
        self.cash.append((type_, f"Instrument {ticker}", ticker, "STOCK", serial(time_utc), Num(amount),
                          str(self._next_id), comment, "My Trades", position))
        return self

    # ── sestavení ─────────────────────────────────────────────────────────
    @property
    def xlsx_name(self) -> str:
        return f"{self.currency}_{self.account}_2006-01-01_2026-10-06.xlsx"

    def sheets(self) -> Dict[str, List[tuple]]:
        open_rows: List[tuple] = []
        for ticker, category in self._tickers.items():
            summary = [""] * len(OPEN_HEADER)
            summary[0], summary[1], summary[2], summary[3] = "My Trades", f"Instrument {ticker}", ticker, category
            open_rows.append(tuple(summary))
            open_rows += [p for p in self._positions if p[2] == ticker]
        opened = [
            ("Account number", self.account), ("Open Positions", ""),
            ("Data as of report generated", serial(self.as_of_utc)),
            ("Product", "Metric", "Amount", "Currency"),
            ("My Trades", "Open position value", Num("1000.00"), self.summary_currency),
            ("My Trades", "Open position profit", Num("10.00"), self.summary_currency),
            (), ("Note", "Summary values and open positions are shown as of the report generation time"),
            OPEN_HEADER, *open_rows,
        ]
        cash = [
            ("Account number", self.account), ("Cash Operations", ""),
            ("Date from (UTC)", serial(datetime(2006, 1, 1))), ("Date to (UTC)", serial(self.as_of_utc)),
            CASH_HEADER, *self.cash, ("Total", "", "", "", "", Num("0")),
        ]
        return {"Closed Positions": [("Account number", self.account)], "Cash Operations": cash,
                "Open Positions": opened}

    def xlsx_bytes(self, sheets: Optional[Dict[str, List[tuple]]] = None) -> bytes:
        return build_xlsx(self.sheets() if sheets is None else sheets)

    def write_xlsx(self, tmp_path, name: Optional[str] = None, sheets=None) -> str:
        path = tmp_path / (name or self.xlsx_name)
        path.write_bytes(self.xlsx_bytes(sheets))
        return str(path)

    def write_zip(self, tmp_path, name: str = "statement.zip", extra=(), xlsx_count: int = 1) -> str:
        path = tmp_path / name
        with zipfile.ZipFile(path, "w") as zf:
            for i in range(xlsx_count):
                suffix = "" if i == 0 else f"_{i}"
                zf.writestr(f"{self.account}/{self.xlsx_name.replace('.xlsx', suffix + '.xlsx')}",
                            self.xlsx_bytes())
            for extra_name, data in extra:
                zf.writestr(extra_name, data)
        return str(path)


def build_xlsx(sheets: Dict[str, List[tuple]], date1904: bool = False) -> bytes:
    """Minimální XLSX: sdílené řetězce pro text, prosté <v> pro Num, chybný rozměr jako XTB."""
    strings: List[str] = []
    index: Dict[str, int] = {}

    def cell(ref, value):
        if value is None or value == "":
            return ""
        if isinstance(value, Num):
            return f'<c r="{ref}" t="n"><v>{value}</v></c>'
        if value not in index:
            index[value] = len(strings)
            strings.append(value)
        return f'<c r="{ref}" t="s"><v>{index[value]}</v></c>'

    parts = {}
    for n, rows in enumerate(sheets.values(), start=1):
        body = "".join(
            f'<row r="{r}">' + "".join(cell(f"{_col(c)}{r}", v) for c, v in enumerate(row, start=1)) + "</row>"
            for r, row in enumerate(rows, start=1)
        )
        parts[f"xl/worksheets/sheet{n}.xml"] = (
            '<?xml version="1.0" encoding="UTF-8"?><worksheet xmlns="http://schemas.openxmlformats.org/'
            'spreadsheetml/2006/main"><dimension ref="A1"/><sheetData>' + body + "</sheetData></worksheet>"
        )
    sheet_tags = "".join(f'<sheet name="{escape(name)}" sheetId="{n}" r:id="rId{n + 2}"/>'
                         for n, name in enumerate(sheets, start=1))
    parts["xl/workbook.xml"] = (
        '<?xml version="1.0" encoding="UTF-8"?><workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/'
        '2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<workbookPr date1904="{"true" if date1904 else "false"}"/><sheets>{sheet_tags}</sheets></workbook>'
    )
    rels = "".join(f'<Relationship Id="rId{n + 2}" Target="worksheets/sheet{n}.xml" Type="http://schemas.'
                   'openxmlformats.org/officeDocument/2006/relationships/worksheet"/>'
                   for n in range(1, len(sheets) + 1))
    parts["xl/_rels/workbook.xml.rels"] = (
        '<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="http://schemas.openxmlformats.org/package/'
        '2006/relationships"><Relationship Id="rId1" Target="sharedStrings.xml" Type="http://schemas.'
        'openxmlformats.org/officeDocument/2006/relationships/sharedStrings"/>' + rels + "</Relationships>"
    )
    parts["xl/sharedStrings.xml"] = (
        '<?xml version="1.0" encoding="UTF-8"?><sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/'
        '2006/main">' + "".join(f"<si><t>{escape(s)}</t></si>" for s in strings) + "</sst>"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("[Content_Types].xml", '<?xml version="1.0" encoding="UTF-8"?><Types xmlns="http://schemas.'
                                           'openxmlformats.org/package/2006/content-types"/>')
        for name, xml in parts.items():
            zf.writestr(name, xml)
    return buffer.getvalue()


def _col(n: int) -> str:
    letters = ""
    while n:
        n, rem = divmod(n - 1, 26)
        letters = chr(ord("A") + rem) + letters
    return letters
