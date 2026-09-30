"""Testy pro LEDGER_TAX export (krok C).

RAW export: jeden ledger row = jeden CSV row, hodnoty přesně z DB,
rozsah od nejstaršího záznamu do konce dne Date to, reversed_by nad celým ledgerem.
"""
import csv
import hashlib
import os
import sqlite3
from datetime import date, datetime
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

from core.ledger_store import LedgerStore
from core.model import RawRow
from core.services.ledger_tax_export import (
    LEDGER_TAX_COLUMNS,
    compute_reversed_by,
    export_ledger_tax,
    export_upper_bound,
    ledger_tax_filename,
)
from core.services.reversal_service import reverse_trade
from core.services.trade_service import AddTradeInput, add_trade
from core.services.ui_facade import export_ledger_tax as facade_export
from ui.modules.export_dialog import _parse_date_to, open_export_dialog

_D = Decimal
_DATE_TO = date(2026, 12, 31)
_NOW = datetime(2027, 1, 15, 9, 30, 0)


def _cash_in(ts: datetime, amount: str, id_: str) -> RawRow:
    return RawRow(id=id_, timestamp=ts, type="CASH_IN", asset="EUR", amount=_D(amount),
                  currency="EUR", price=_D("0"), venue="xtb")


def _insert(db: str, *rows: RawRow) -> None:
    store = LedgerStore(db)
    try:
        for r in rows:
            assert store.insert(r)
    finally:
        store.close()


def _buy(db: str, ts: datetime, qty="3", total="489.54", fee=None, type_="BUY"):
    return add_trade(db, AddTradeInput(type_, ts, "SAP.DE", _D(qty), "EUR", _D(total), "xtb",
                                       fee_amount=_D(fee) if fee else None))


def _export(db: str, out_dir, date_to: date = _DATE_TO):
    path, n = export_ledger_tax(db, date_to, str(out_dir), now=_NOW)
    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.reader(f)
        header = next(reader)
        data = [dict(zip(header, row)) for row in reader]
    return path, n, header, data


def _db_rows(db: str) -> list:
    c = sqlite3.connect(db)
    c.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in c.execute("SELECT * FROM ledger ORDER BY pk")]
    finally:
        c.close()


# ── Hlavička a formát ─────────────────────────────────────────────────────────

class TestFormat:
    def test_hlavicka_presne_poradi(self, tmp_db, tmp_path):
        _, _, header, _ = _export(tmp_db, tmp_path)
        assert header == [
            "ledger_pk", "trade_id", "timestamp", "type", "asset", "amount", "currency",
            "price", "venue", "note", "row_fp", "imported_at", "reversed_by",
        ]
        assert tuple(header) == LEDGER_TAX_COLUMNS

    def test_prazdny_vysledek_jen_hlavicka(self, tmp_db, tmp_path):
        path, n, header, data = _export(tmp_db, tmp_path)
        assert n == 0 and data == [] and header == list(LEDGER_TAX_COLUMNS)
        with open(path, encoding="utf-8", newline="") as f:
            assert f.read() == ",".join(LEDGER_TAX_COLUMNS) + "\r\n"

    def test_nazev_souboru(self):
        assert ledger_tax_filename(_DATE_TO, _NOW) == "stocks_ledger_tax_2026-12-31_20270115_093000.csv"

    def test_existujici_soubor_neprepise(self, tmp_db, tmp_path):
        export_ledger_tax(tmp_db, _DATE_TO, str(tmp_path), now=_NOW)
        with pytest.raises(FileExistsError):
            export_ledger_tax(tmp_db, _DATE_TO, str(tmp_path), now=_NOW)


# ── Rozsah Date to ────────────────────────────────────────────────────────────

class TestDateTo:
    def test_horni_hranice_je_nasledujici_den(self):
        assert export_upper_bound(_DATE_TO) == datetime(2027, 1, 1, 0, 0, 0)

    def test_hranice_dne(self, tmp_db, tmp_path):
        _insert(tmp_db,
                _cash_in(datetime(2026, 12, 30, 10, 0, 0), "1", "BEFORE"),
                _cash_in(datetime(2026, 12, 31, 0, 0, 0), "2", "DAY_START"),
                _cash_in(datetime(2026, 12, 31, 14, 0, 0), "3", "DURING"),
                _cash_in(datetime(2026, 12, 31, 23, 59, 59), "4", "DAY_END"),
                _cash_in(datetime(2026, 12, 31, 23, 59, 59, 999999), "5", "DAY_END_US"),
                _cash_in(datetime(2027, 1, 1, 0, 0, 0), "6", "NEXT_MIDNIGHT"),
                _cash_in(datetime(2027, 1, 1, 12, 0, 0), "7", "NEXT_DAY"))
        _, n, _, data = _export(tmp_db, tmp_path)
        ids = [r["trade_id"] for r in data]
        assert ids == ["BEFORE", "DAY_START", "DURING", "DAY_END", "DAY_END_US"]
        assert n == 5

    def test_stary_buy_z_predchozich_let_zustava(self, tmp_db, tmp_path):
        _buy(tmp_db, datetime(2019, 3, 1, 10, 0, 0))
        _, _, _, data = _export(tmp_db, tmp_path)
        assert [r["timestamp"] for r in data] == ["2019-03-01T10:00:00"] * 2


# ── RAW hodnoty ───────────────────────────────────────────────────────────────

class TestRawValues:
    def test_hodnoty_odpovidaji_db(self, tmp_db, tmp_path):
        _insert(tmp_db, _cash_in(datetime(2025, 1, 1), "1000", "C1"))
        _buy(tmp_db, datetime(2026, 6, 4, 13, 52, 55), fee="2.00")
        _, _, _, data = _export(tmp_db, tmp_path)
        db = {str(r["pk"]): r for r in _db_rows(tmp_db)}
        assert len(data) == len(db) == 4
        for row in data:
            src = db[row["ledger_pk"]]
            assert row["trade_id"] == src["id"]
            assert row["timestamp"] == src["timestamp"]
            assert row["type"] == src["type"]
            assert row["asset"] == src["asset"]
            assert row["amount"] == src["amount"]
            assert row["currency"] == src["currency"]
            assert row["price"] == (src["price"] or "")
            assert row["venue"] == src["venue"]
            assert row["note"] == (src["note"] or "")
            assert row["row_fp"] == src["row_fp"]
            assert row["imported_at"] == src["imported_at"]

    def test_fee_radek_zachovan_beze_zmeny(self, tmp_db, tmp_path):
        _buy(tmp_db, datetime(2026, 6, 4, 13, 52, 55), fee="2.00")
        _, _, _, data = _export(tmp_db, tmp_path)
        assert len({r["trade_id"] for r in data}) == 1
        fee = [r for r in data if r["type"] == "FEE"]
        assert len(fee) == 1
        assert fee[0]["amount"] == "-2.00" and fee[0]["currency"] == "EUR"
        cash = [r for r in data if r["type"] == "BUY" and r["asset"] == "EUR"]
        assert cash[0]["amount"] == "-489.54"             # bez poplatku, znaménko zachováno

    def test_price_neni_prepocitana(self, tmp_db, tmp_path):
        _buy(tmp_db, datetime(2026, 1, 5, 10, 0, 0), qty="3", total="100")
        _, _, _, data = _export(tmp_db, tmp_path)
        stock = next(r for r in data if r["asset"] == "SAP.DE")
        db_price = next(r["price"] for r in _db_rows(tmp_db) if r["asset"] == "SAP.DE")
        assert stock["price"] == db_price               # přesně řetězec z DB

    def test_export_nemeni_ledger(self, tmp_db, tmp_path):
        _buy(tmp_db, datetime(2026, 6, 4, 13, 52, 55), fee="1")
        before_rows = _db_rows(tmp_db)
        with open(tmp_db, "rb") as f:
            before_hash = hashlib.sha256(f.read()).hexdigest()
        _export(tmp_db, tmp_path)
        with open(tmp_db, "rb") as f:
            assert hashlib.sha256(f.read()).hexdigest() == before_hash
        assert _db_rows(tmp_db) == before_rows


# ── reversed_by ───────────────────────────────────────────────────────────────

class TestReversedBy:
    def test_reversal_v_rozsahu_zachovan_a_original_oznacen(self, tmp_db, tmp_path):
        trade = _buy(tmp_db, datetime(2026, 6, 4, 13, 52, 55))
        rev_rows = reverse_trade(tmp_db, trade.rows[0].id)   # storno má dnešní timestamp
        rev_id = rev_rows[0].id
        _, _, _, data = _export(tmp_db, tmp_path, date_to=date.today())
        originals = [r for r in data if r["trade_id"] == trade.rows[0].id]
        reversals = [r for r in data if r["type"] == "REVERSAL"]
        assert len(originals) == 2 and all(r["reversed_by"] == rev_id for r in originals)
        assert len(reversals) == 2 and all(r["reversed_by"] == "" for r in reversals)
        assert {r["amount"] for r in reversals} == {"-3", "489.54"}

    def test_reversal_po_date_to_oznaci_starsi_original(self, tmp_db, tmp_path):
        trade = _buy(tmp_db, datetime(2026, 12, 30, 10, 0, 0))
        _insert(tmp_db, RawRow(id="REV_LATE", timestamp=datetime(2027, 1, 5, 9, 0, 0),
                               type="REVERSAL", asset="SAP.DE", amount=_D("-3"),
                               currency="EUR", price=_D("163.18"), venue="xtb",
                               note=f"REVERSAL of {trade.rows[0].id}; pozdní storno"))
        _, _, _, data = _export(tmp_db, tmp_path)
        assert not any(r["type"] == "REVERSAL" for r in data)   # storno je mimo rozsah
        assert len(data) == 2
        assert all(r["reversed_by"] == "REV_LATE" for r in data)

    def test_nestornovany_radek_ma_prazdne_reversed_by(self, tmp_db, tmp_path):
        _buy(tmp_db, datetime(2026, 1, 5, 10, 0, 0), fee="1")
        _, _, _, data = _export(tmp_db, tmp_path)
        assert len(data) == 3 and all(r["reversed_by"] == "" for r in data)

    def test_compute_reversed_by_pouziva_a1_parser(self):
        rev = RawRow(id="REV_X_1", timestamp=datetime(2026, 1, 1), type="REVERSAL", asset="EUR",
                     amount=_D("1"), currency="EUR", price=_D("1"), venue="xtb",
                     note="REVERSAL of X; poznámka; další")
        assert compute_reversed_by([rev]) == {"X": "REV_X_1"}


# ── Facade ────────────────────────────────────────────────────────────────────

class TestFacade:
    def test_uspech(self, tmp_db, tmp_path):
        _buy(tmp_db, datetime(2026, 1, 5, 10, 0, 0))
        r = facade_export(tmp_db, _DATE_TO, str(tmp_path / "exp"))
        assert r.success and r.n_rows == 2 and os.path.isfile(r.path)
        assert os.path.basename(r.path).startswith("stocks_ledger_tax_2026-12-31_")

    def test_chybejici_db_nevytvori_db(self, tmp_path):
        missing = str(tmp_path / "missing.db")
        r = facade_export(missing, _DATE_TO, str(tmp_path))
        assert r.success is False and "neexistuje" in r.error_message
        assert not os.path.exists(missing)


# ── UI: Date to validace a sestavení dialogu ──────────────────────────────────

class TestExportDialog:
    def test_platne_datum(self):
        assert _parse_date_to(" 2026-12-31 ") == (date(2026, 12, 31), None)

    @pytest.mark.parametrize("text", ["", "   ", None])
    def test_povinne(self, text):
        d, err = _parse_date_to(text)
        assert d is None and "Zadej Date to" in err

    @pytest.mark.parametrize("text", ["2026-02-30", "2026-13-01", "31.12.2026", "20261231",
                                      "2026-1-5", "abc", "2026-12-31T10:00"])
    def test_neplatne(self, text):
        d, err = _parse_date_to(text)
        assert d is None and "Neplatné datum" in err

    def test_dialog_se_sestavi(self):
        page = MagicMock()
        page.overlay = []
        open_export_dialog(page, "unused.db")
        col = page.overlay[-1].content.content
        labels = [c.label for row in col.controls if hasattr(row, "controls")
                  for c in row.controls if hasattr(c, "label")]
        assert labels == ["Typ exportu", "Date to"]
