"""Regresní testy pro atomický zápis ekonomické transakce (insert_group).

Bug: import_rows zapisoval řádky jednotlivě a kolizi row_fp jen započítal do
skipped, takže mohla vzniknout neúplná transakce (např. SAP.DE BUY bez
peněžní nohy). Interaktivní zápis teď platí jako všechno, nebo nic.
"""
from datetime import datetime
from decimal import Decimal

import pytest

from core.ledger_store import DuplicateRowError, LedgerStore
from core.model import RawRow
from core.services import reversal_service
from core.services.holdings_engine import compute_holdings
from core.services.reversal_service import reverse_trade
from core.services.trade_service import AddTradeInput, add_trade
from core.services.ui_facade import AddTradeRequestDTO, add_trade as facade_add

_D = Decimal
_TS = datetime(2026, 6, 4, 13, 52, 55)


def _inp(type_="BUY", asset="SAP", qty="3", total="489.54", fee=None, ts=_TS):
    return AddTradeInput(type_, ts, asset, _D(qty), "EUR", _D(total), "xtb",
                         fee_amount=_D(fee) if fee else None)


def _rows(db_path: str):
    store = LedgerStore(db_path)
    try:
        return store.timeline()
    finally:
        store.close()


def _ids(db_path: str) -> set:
    return {r.id for r in _rows(db_path)}


def _insert_raw(db_path: str, row: RawRow) -> None:
    store = LedgerStore(db_path)
    try:
        assert store.insert(row)
    finally:
        store.close()


# ── LedgerStore.insert_group ──────────────────────────────────────────────────

class TestInsertGroup:
    def _row(self, amount: str, asset: str = "AAPL", id_: str = "G1") -> RawRow:
        return RawRow(id=id_, timestamp=_TS, type="BUY", asset=asset, amount=_D(amount),
                      currency="EUR", price=_D("1"), venue="xtb")

    def test_zapise_vsechny_radky(self, tmp_db):
        store = LedgerStore(tmp_db)
        try:
            assert store.insert_group([self._row("1"), self._row("-1", asset="EUR")]) == 2
            assert store.count() == 2
        finally:
            store.close()

    def test_kolize_rollback_cele_skupiny(self, tmp_db):
        store = LedgerStore(tmp_db)
        try:
            store.insert(self._row("-1", asset="EUR", id_="OLD"))
            with pytest.raises(DuplicateRowError):
                store.insert_group([self._row("1"), self._row("-1", asset="EUR")])
            assert store.count() == 1
            assert store.get_rows_by_id("G1") == []
        finally:
            store.close()

    def test_duplicate_row_error_je_value_error(self):
        assert issubclass(DuplicateRowError, ValueError)


class TestImportRowsBulkBezeZmeny:
    def test_import_rows_preskoci_duplicitu_a_zbytek_zapise(self, tmp_db):
        store = LedgerStore(tmp_db)
        try:
            a = RawRow(id="A", timestamp=_TS, type="BUY", asset="AAPL", amount=_D("1"),
                       currency="EUR", price=_D("1"), venue="xtb")
            b = RawRow(id="B", timestamp=_TS, type="BUY", asset="MSFT", amount=_D("1"),
                       currency="EUR", price=_D("1"), venue="xtb")
            store.insert(a)
            assert store.import_rows([a, b]) == {"inserted": 1, "skipped": 1}
            assert store.count() == 2
        finally:
            store.close()


# ── trade_service.add_trade ───────────────────────────────────────────────────

class TestTradeAtomic:
    def test_buy_zapise_obe_nohy(self, tmp_db):
        result = add_trade(tmp_db, _inp("BUY"))
        assert result.inserted == 2
        assert len(_rows(tmp_db)) == 2

    def test_buy_s_fee_zapise_tri_radky(self, tmp_db):
        result = add_trade(tmp_db, _inp("BUY", fee="1.5"))
        rows = _rows(tmp_db)
        assert result.inserted == 3
        assert len({r.id for r in rows}) == 1
        assert sorted(r.type for r in rows) == ["BUY", "BUY", "FEE"]

    def test_sell_zapise_obe_nohy(self, tmp_db):
        result = add_trade(tmp_db, _inp("SELL"))
        assert result.inserted == 2
        assert {r.type for r in _rows(tmp_db)} == {"SELL"}

    def test_kolize_jedne_nohy_nezapise_nic(self, tmp_db):
        # cizí řádek se stejným row_fp jako peněžní noha nového BUY
        _insert_raw(tmp_db, RawRow(id="OTHER", timestamp=_TS, type="BUY", asset="EUR",
                                   amount=_D("-489.54"), currency="EUR", price=_D("1"),
                                   venue="xtb"))
        with pytest.raises(DuplicateRowError):
            add_trade(tmp_db, _inp("BUY"))
        assert _ids(tmp_db) == {"OTHER"}

    def test_kolize_fee_nezapise_nic(self, tmp_db):
        add_trade(tmp_db, _inp("BUY", qty="1", total="100", fee="1"))
        before = _rows(tmp_db)
        with pytest.raises(DuplicateRowError):
            add_trade(tmp_db, _inp("BUY", qty="2", total="200", fee="1"))
        after = _rows(tmp_db)
        assert len(after) == len(before) == 3
        assert "20260604_135255_XTB_BUY_002" not in {r.id for r in after}


# ── ui_facade.add_trade ───────────────────────────────────────────────────────

class TestFacadeAtomic:
    def _req(self, type_="BUY", asset="AAPL", amount="10", **kw):
        return AddTradeRequestDTO(type=type_, timestamp=_TS, asset=asset, amount=_D(amount),
                                  currency="EUR", price=None, venue="xtb",
                                  quote_amount=kw.get("quote"), fee_amount=kw.get("fee"))

    def test_dvoji_odeslani_druhe_selze(self, tmp_db):
        req = self._req(quote=_D("1000"))
        assert facade_add(req, tmp_db).success is True
        second = facade_add(req, tmp_db)
        assert second.success is False
        assert second.n_rows_added == 0
        assert "Duplicitní transakce" in second.error_message
        rows = _rows(tmp_db)
        assert len(rows) == 2
        assert len({r.id for r in rows}) == 1

    def test_buy_s_fee_pres_facade(self, tmp_db):
        result = facade_add(self._req(quote=_D("1000"), fee=_D("2")), tmp_db)
        assert result.success is True
        assert result.n_rows_added == 3

    def test_single_row_duplicita_selze(self, tmp_db):
        req = self._req(type_="CASH_IN", asset="EUR", amount="500")
        assert facade_add(req, tmp_db).success is True
        second = facade_add(req, tmp_db)
        assert second.success is False
        assert "Duplicitní transakce" in second.error_message
        assert len(_rows(tmp_db)) == 1


# ── REVERSAL ──────────────────────────────────────────────────────────────────

class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 6, 13, 14, 43, 49)


class TestReversalAtomic:
    def test_reversal_zapise_vsechny_nohy(self, tmp_db):
        trade = add_trade(tmp_db, _inp("BUY", fee="1"))
        rev = reverse_trade(tmp_db, trade.rows[0].id)
        assert len(rev) == 3
        assert len([r for r in _rows(tmp_db) if r.type == "REVERSAL"]) == 3

    def test_reversal_kolize_nezapise_nic(self, tmp_db, monkeypatch):
        monkeypatch.setattr(reversal_service, "datetime", _FixedDatetime)
        trade = add_trade(tmp_db, _inp("BUY"))
        # cizí REVERSAL řádek se stejným row_fp jako peněžní reversal noha
        _insert_raw(tmp_db, RawRow(id="REV_OTHER", timestamp=_FixedDatetime.now(),
                                   type="REVERSAL", asset="EUR", amount=_D("489.54"),
                                   currency="EUR", price=_D("1"), venue="xtb"))
        with pytest.raises(DuplicateRowError):
            reverse_trade(tmp_db, trade.rows[0].id)
        rev_ids = {r.id for r in _rows(tmp_db) if r.type == "REVERSAL"}
        assert rev_ids == {"REV_OTHER"}


# ── Scénář SAP.DE ─────────────────────────────────────────────────────────────

class TestSapDeScenario:
    def test_nahradni_obchod_nevznikne_neuplny(self, tmp_db):
        orig = add_trade(tmp_db, _inp("BUY", asset="SAP"))
        reverse_trade(tmp_db, orig.rows[0].id)

        with pytest.raises(DuplicateRowError):
            add_trade(tmp_db, _inp("BUY", asset="SAP.DE"))

        rows = _rows(tmp_db)
        assert "20260604_135255_XTB_BUY_002" not in {r.id for r in rows}
        buy_groups: dict = {}
        for r in rows:
            if r.type == "BUY":
                buy_groups.setdefault(r.id, []).append(r)
        assert all(len(g) == 2 for g in buy_groups.values())
        assert compute_holdings(rows) == []

    def test_nahradni_obchod_pres_facade_hlasi_chybu(self, tmp_db):
        orig = add_trade(tmp_db, _inp("BUY", asset="SAP"))
        reverse_trade(tmp_db, orig.rows[0].id)
        req = AddTradeRequestDTO(type="BUY", timestamp=_TS, asset="SAP.DE", amount=_D("3"),
                                 currency="EUR", price=None, venue="xtb",
                                 quote_amount=_D("489.54"))
        result = facade_add(req, tmp_db)
        assert result.success is False
        assert "Duplicitní transakce" in result.error_message
        assert not any(r.asset == "SAP.DE" for r in _rows(tmp_db))
