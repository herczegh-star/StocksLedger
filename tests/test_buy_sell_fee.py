"""Testy pro volitelný poplatek ve formuláři BUY/SELL (krok B).

UI validace: _parse_fee (čistá funkce z add_trade_dialog).
Zbytek cesty: AddTradeRequestDTO → ui_facade.add_trade → AddTradeInput
→ build_trade_rows → LedgerStore.insert_group, stejně jako _form_buy_sell._submit.
"""
from datetime import datetime
from decimal import Decimal

import pytest

from core.ledger_store import LedgerStore
from core.model import RawRow
from core.services.holdings_engine import compute_cash_balance
from core.services.ui_facade import AddTradeRequestDTO, add_trade
from ui.modules.add_trade_dialog import _parse_fee

_D = Decimal
_TS = datetime(2026, 6, 4, 13, 52, 55)


def _submit(db_path: str, ttype: str, fee_text: str, qty: str = "3", total: str = "489.54"):
    """Napodobí _form_buy_sell._submit: validace poplatku, pak DTO do facade."""
    fee, err = _parse_fee(fee_text)
    assert err is None
    req = AddTradeRequestDTO(type=ttype, timestamp=_TS, asset="SAP.DE", amount=_D(qty),
                             currency="EUR", price=None, quote_amount=_D(total),
                             venue="xtb", fee_amount=fee, note=None)
    return add_trade(req, db_path)


def _rows(db_path: str):
    store = LedgerStore(db_path)
    try:
        return store.timeline()
    finally:
        store.close()


def _by_role(rows):
    fee = [r for r in rows if r.type == "FEE"]
    asset = [r for r in rows if r.type != "FEE" and r.asset != r.currency]
    cash = [r for r in rows if r.type != "FEE" and r.asset == r.currency]
    return asset, cash, fee


# ── UI validace poplatku ──────────────────────────────────────────────────────

class TestParseFee:
    @pytest.mark.parametrize("text", ["", "   ", None])
    def test_prazdne_znamena_bez_poplatku(self, text):
        assert _parse_fee(text) == (None, None)

    @pytest.mark.parametrize("text, expected", [
        ("2", _D("2")),
        ("2.00", _D("2.00")),
        ("2,50", _D("2.50")),
        (" 0.35 ", _D("0.35")),
        ("1 000.5", _D("1000.5")),
    ])
    def test_platne_kladne_cislo(self, text, expected):
        assert _parse_fee(text) == (expected, None)

    @pytest.mark.parametrize("text", ["0", "0.00", "-1", "-0.5", "abc", "2 EUR", "NaN", "Infinity"])
    def test_neplatne_je_chyba_ne_zadny_poplatek(self, text):
        fee, err = _parse_fee(text)
        assert fee is None
        assert err and "Poplatek" in err


# ── BUY / SELL přes facade ────────────────────────────────────────────────────

class TestBuySellFee:
    def test_buy_bez_fee_dva_radky(self, tmp_db):
        r = _submit(tmp_db, "BUY", "")
        assert r.success and r.n_rows_added == 2
        assert not any(x.type == "FEE" for x in _rows(tmp_db))

    def test_buy_s_fee_tri_radky_stejne_id(self, tmp_db):
        r = _submit(tmp_db, "BUY", "2.00")
        rows = _rows(tmp_db)
        assert r.success and r.n_rows_added == 3
        assert len(rows) == 3 and len({x.id for x in rows}) == 1
        asset, cash, fee = _by_role(rows)
        assert asset[0].amount == _D("3") and asset[0].type == "BUY"
        assert cash[0].amount == _D("-489.54")
        assert fee[0].type == "FEE"
        assert fee[0].amount == _D("-2.00")
        assert fee[0].asset == "EUR" and fee[0].currency == "EUR"

    def test_sell_bez_fee_dva_radky(self, tmp_db):
        r = _submit(tmp_db, "SELL", "", total="500")
        assert r.success and r.n_rows_added == 2
        assert not any(x.type == "FEE" for x in _rows(tmp_db))

    def test_sell_s_fee_tri_radky_stejne_id(self, tmp_db):
        r = _submit(tmp_db, "SELL", "2", total="500")
        rows = _rows(tmp_db)
        assert r.success and r.n_rows_added == 3
        assert len({x.id for x in rows}) == 1
        asset, cash, fee = _by_role(rows)
        assert asset[0].amount == _D("-3") and asset[0].type == "SELL"
        assert cash[0].amount == _D("500")          # prodejní hodnota bez poplatku
        assert fee[0].type == "FEE" and fee[0].amount == _D("-2") and fee[0].currency == "EUR"

    def test_cash_leg_je_hodnota_bez_fee_a_fee_snizi_cash(self, tmp_db):
        store = LedgerStore(tmp_db)
        try:
            store.insert(RawRow(id="C1", timestamp=datetime(2026, 1, 1), type="CASH_IN",
                                asset="EUR", amount=_D("1000"), currency="EUR",
                                price=_D("0"), venue="xtb"))
        finally:
            store.close()
        _submit(tmp_db, "BUY", "2.00")
        rows = _rows(tmp_db)
        _, cash, _ = _by_role([x for x in rows if x.type != "CASH_IN"])
        assert cash[0].amount == _D("-489.54")
        assert compute_cash_balance(rows)["EUR"] == _D("508.46")   # 1000 − 489.54 − 2


# ── Neplatné fee a atomičnost ─────────────────────────────────────────────────

class TestFeeInvalidAndAtomic:
    @pytest.mark.parametrize("fee", [_D("0"), _D("-2")])
    def test_neplatne_fee_ve_facade_nezapise_nic(self, tmp_db, fee):
        req = AddTradeRequestDTO(type="BUY", timestamp=_TS, asset="SAP.DE", amount=_D("3"),
                                 currency="EUR", price=None, quote_amount=_D("489.54"),
                                 venue="xtb", fee_amount=fee)
        r = add_trade(req, tmp_db)
        assert r.success is False and r.n_rows_added == 0
        assert _rows(tmp_db) == []

    def test_kolize_fee_nezapise_zadnou_cast(self, tmp_db):
        assert _submit(tmp_db, "BUY", "2", qty="1", total="100").success
        r = _submit(tmp_db, "BUY", "2", qty="2", total="200")   # stejná sekunda, stejný poplatek
        assert r.success is False and "Duplicitní transakce" in r.error_message
        rows = _rows(tmp_db)
        assert len(rows) == 3 and len({x.id for x in rows}) == 1
