"""Regresní testy pro reversed_trade_ids() — parsování REVERSAL note.

Bug: build_reversal_rows() připojí původní poznámku za středník
('REVERSAL of X; poznámka'), ale enginy braly celý zbytek note jako trade_id,
takže storno obchodu s poznámkou se neuplatnilo.
"""
from datetime import datetime
from decimal import Decimal

from core.model import RawRow
from core.services.holdings_engine import (
    compute_cash_balance,
    compute_holdings,
    compute_net_deposits,
    reversed_trade_ids,
)
from core.services.reentry_engine import compute_sell_events
from core.services.reversal_service import build_reversal_rows
from core.services.trade_service import AddTradeInput, build_trade_rows

_D = Decimal
_T1 = datetime(2024, 1, 10, 10, 0, 0)
_T2 = datetime(2024, 3, 15, 10, 0, 0)


def _rev(note: str) -> RawRow:
    return RawRow(
        id="REV_X_abcd1234", timestamp=_T2, type="REVERSAL", asset="EUR",
        amount=_D("1"), currency="EUR", price=_D("1"), venue="xtb", note=note,
    )


def _trade(type_: str, qty: str, total: str, trade_id: str,
           ts: datetime = _T1, note: str = None):
    return build_trade_rows(
        AddTradeInput(type_, ts, "AAPL", _D(qty), "EUR", _D(total), "xtb", note=note),
        trade_id=trade_id,
    )


# ── Parsování note ────────────────────────────────────────────────────────────

class TestReversedTradeIds:
    def test_note_bez_puvodni_poznamky(self):
        assert reversed_trade_ids([_rev("REVERSAL of X")]) == {"X"}

    def test_note_s_puvodni_poznamkou(self):
        assert reversed_trade_ids([_rev("REVERSAL of X; poznámka")]) == {"X"}

    def test_note_s_vice_strednikami(self):
        assert reversed_trade_ids([_rev("REVERSAL of X; poznámka; další text")]) == {"X"}

    def test_kanonicke_id_s_poznamkou(self):
        note = "REVERSAL of 20260604_135255_XTB_BUY_001; moje; poznámka"
        assert reversed_trade_ids([_rev(note)]) == {"20260604_135255_XTB_BUY_001"}

    def test_ignoruje_ne_reversal_radky(self):
        row = RawRow(id="T1", timestamp=_T1, type="BUY", asset="AAPL", amount=_D("1"),
                     currency="EUR", price=_D("1"), venue="xtb", note="REVERSAL of Y")
        assert reversed_trade_ids([row]) == set()

    def test_ignoruje_reversal_bez_prefixu(self):
        assert reversed_trade_ids([_rev("storno"), _rev(None)]) == set()

    def test_prazdne_id_se_neprida(self):
        assert reversed_trade_ids([_rev("REVERSAL of ; poznámka")]) == set()

    def test_reversal_rows_z_reversal_service(self):
        rows = _trade("BUY", "10", "1000", "T1", note="moje poznámka")
        rev = build_reversal_rows(rows)
        assert rev[0].note == "REVERSAL of T1; moje poznámka"  # formát zápisu beze změny
        assert reversed_trade_ids(rev) == {"T1"}


# ── Enginy respektují storno obchodu s poznámkou ──────────────────────────────

class TestEnginesWithNotedReversal:
    def test_stornovany_buy_s_poznamkou_neni_v_holdings(self):
        buy = _trade("BUY", "10", "1000", "T1", note="moje poznámka")
        assert compute_holdings(buy + build_reversal_rows(buy)) == []

    def test_stornovany_buy_s_poznamkou_ostatni_pozice_zustava(self):
        keep = _trade("BUY", "5", "600", "T0")
        buy = _trade("BUY", "10", "1000", "T1", note="moje poznámka")
        holdings = compute_holdings(keep + buy + build_reversal_rows(buy))
        assert len(holdings) == 1
        assert holdings[0].quantity == _D("5")

    def test_stornovany_sell_s_poznamkou_neni_v_sell_events(self):
        buy = _trade("BUY", "10", "1000", "B1")
        sell = _trade("SELL", "4", "480", "S1", ts=_T2, note="prodej; omylem")
        events = compute_sell_events(buy + sell + build_reversal_rows(sell))
        assert events == []

    def test_cash_balance_respektuje_storno_s_poznamkou(self):
        cash_in = [RawRow(id="C1", timestamp=_T1, type="CASH_IN", asset="EUR",
                          amount=_D("2000"), currency="EUR", price=_D("0"), venue="xtb")]
        buy = _trade("BUY", "10", "1000", "T1", note="moje poznámka")
        result = compute_cash_balance(cash_in + buy + build_reversal_rows(buy))
        assert result["EUR"] == _D("2000")

    def test_net_deposits_respektuje_storno_s_poznamkou(self):
        keep = RawRow(id="C1", timestamp=_T1, type="CASH_IN", asset="EUR",
                      amount=_D("2000"), currency="EUR", price=_D("0"), venue="xtb")
        wrong = RawRow(id="C2", timestamp=_T1, type="CASH_IN", asset="EUR",
                       amount=_D("500"), currency="EUR", price=_D("0"), venue="xtb",
                       note="duplicitní vklad")
        result = compute_net_deposits([keep, wrong] + build_reversal_rows([wrong]))
        assert result == {"EUR": _D("2000")}
