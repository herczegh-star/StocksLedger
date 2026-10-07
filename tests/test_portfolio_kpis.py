"""Regresní testy dashboardu: Portfolio Value bez vkladů, AMZN.DE → AMZ.DE, viditelnost chybějících cen.

Dočasná DB (tmp_db) a podvržené ceny — žádná síť, žádný zápis do živé DB.
"""
import asyncio
from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock

import flet as ft
import pytest

from core.ledger_store import LedgerStore
from core.model import RawRow
from core.services.price_provider import _yf_ticker
from core.services.trade_service import AddTradeInput, build_trade_rows
from core.services.ui_facade import PortfolioSnapshotDTO, PositionDTO
from ui.modules import portfolio_view
from ui.modules.portfolio_view import _portfolio_value, _priced_status, build_portfolio_view

_D = Decimal


def _pos(ticker, spot=None):
    return PositionDTO(ticker=ticker, quantity=_D("1"), wac=_D("10"), cost_basis=_D("10"), currency="EUR",
                       spot_price=spot, spot_currency="EUR" if spot is not None else None)


# ── čisté helpery ─────────────────────────────────────────────────────────────

class TestPortfolioValue:
    def test_vklady_se_nezapocitavaji(self):
        snap = PortfolioSnapshotDTO(positions=[], total_cost_basis=_D("100"), portfolio_value=_D("120"),
                                    net_deposits_by_currency={"EUR": _D("1037.94")})
        assert _portfolio_value(snap) == _D("120")

    def test_bez_cen_neni_hodnota_ani_z_vkladu(self):
        snap = PortfolioSnapshotDTO(positions=[], total_cost_basis=_D("100"), portfolio_value=None,
                                    net_deposits_by_currency={"EUR": _D("1037.94")})
        assert _portfolio_value(snap) is None


class TestPricedStatus:
    def test_zadne_pozice(self):
        assert _priced_status([], True) == ("", False)

    def test_ceny_se_nacitaji(self):
        text, warn = _priced_status([_pos("A"), _pos("B")], False)
        assert text == "Priced positions: …/2 (loading quotes)" and warn is False

    def test_vse_ocenene(self):
        assert _priced_status([_pos("A", _D("1")), _pos("B", _D("2"))], True) == ("Priced positions: 2/2", False)

    def test_chybejici_cena_je_videt(self):
        text, warn = _priced_status([_pos("A", _D("1")), _pos("AMZN.DE")], True)
        assert warn is True
        assert text.startswith("Priced positions: 1/2 — no quote: AMZN.DE")


class TestYahooMapping:
    @pytest.mark.parametrize("ledger, yahoo", [
        ("AMZN.DE", "AMZ.DE"),     # Amazon na Xetře: AMZN.DE na Yahoo neexistuje (404)
        ("SAP.DE", "SAP.DE"),      # ostatní .DE beze změny
        ("NVD.DE", "NVD.DE"),
        ("BRKB.US", "BRK-B"),
        ("ANET.US", "ANET"),
        ("ABBN.CH", "ABBN.SW"),
    ])
    def test_mapovani(self, ledger, yahoo):
        assert _yf_ticker(ledger) == yahoo


# ── celý dashboard nad dočasnou DB, podvržené ceny ────────────────────────────

def _seed(db):
    store = LedgerStore(db)
    try:
        store.insert(RawRow(id="C1", timestamp=datetime(2026, 1, 1), type="CASH_IN", asset="EUR",
                            amount=_D("1037.94"), currency="EUR", price=_D("0"), venue="xtb"))
        for tid, ticker, qty, total in (("B1", "AAA.DE", "10", "100"), ("B2", "BBB.US", "5", "50")):
            store.insert_group(build_trade_rows(AddTradeInput("BUY", datetime(2026, 2, 1), ticker, _D(qty), "EUR",
                                                              _D(total), "xtb"), trade_id=tid))
    finally:
        store.close()


class _SyncThread:
    def __init__(self, target=None, daemon=None):
        self._target = target

    def start(self):
        self._target()


def _walk(c):
    yield c
    content = getattr(c, "content", None)
    if isinstance(content, ft.Control):
        yield from _walk(content)
    for child in getattr(c, "controls", None) or []:
        yield from _walk(child)


def _kpi_texts(view, label):
    box = next(c for c in _walk(view) if isinstance(c, ft.Column) and c.controls
               and isinstance(c.controls[0], ft.Text) and c.controls[0].value == label)
    return [t.value for t in box.controls[1:]]


@pytest.fixture
def dashboard(tmp_db, monkeypatch):
    import core.services.price_provider as pp
    import core.services.ticker_meta as tm
    _seed(tmp_db)
    prices = {}
    monkeypatch.setattr(portfolio_view.threading, "Thread", _SyncThread)
    monkeypatch.setattr(pp, "fetch_prices_with_currency", lambda tickers: {t: prices[t] for t in tickers if t in prices})
    monkeypatch.setattr(pp, "fetch_fx_rates", lambda: {"EURUSD": _D("1"), "GBPUSD": None, "CHFUSD": None})
    monkeypatch.setattr(tm, "fetch_names", lambda tickers: {})
    monkeypatch.setattr(tm, "save_names", lambda db, names: None)
    page = MagicMock()
    page.run_task.side_effect = lambda fn: asyncio.run(fn())
    view, refresh = build_portfolio_view(page, tmp_db)
    return view, refresh, prices


class TestDashboard:
    def test_vklad_neni_v_portfolio_value_a_chybejici_cena_je_videt(self, dashboard):
        view, refresh, prices = dashboard
        prices["AAA.DE"] = (_D("12"), "EUR")                    # BBB.US bez ceny
        refresh()
        value, invested, priced, deposits = _kpi_texts(view, "Portfolio Value")
        assert value == "120.00 EUR"                             # 10 × 12, ne 1 157.94 (+ vklad)
        assert invested == "Net Invested: 150.00 EUR"
        assert priced.startswith("Priced positions: 1/2 — no quote: BBB.US")
        assert deposits == "Net deposits (info, not in value): 1 037.94 EUR"
        assert _kpi_texts(view, "Unrealized P&L")[0].startswith("+20.00")

    def test_vse_ocenene(self, dashboard):
        view, refresh, prices = dashboard
        prices.update({"AAA.DE": (_D("12"), "EUR"), "BBB.US": (_D("11"), "EUR")})
        refresh()
        value, _, priced, _ = _kpi_texts(view, "Portfolio Value")
        assert value == "175.00 EUR" and priced == "Priced positions: 2/2"

    def test_zadna_cena_neni_ticha(self, dashboard):
        view, refresh, prices = dashboard
        refresh()                                                # Yahoo nevrátí nic
        value, _, priced, _ = _kpi_texts(view, "Portfolio Value")
        assert value == "—"                                      # dřív by tu byl vklad 1 037.94
        assert priced.startswith("Priced positions: 0/2 — no quote: AAA.DE, BBB.US")
