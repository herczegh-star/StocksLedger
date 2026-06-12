"""Unit testy pro compute_cash_balance() a cash v PortfolioSnapshotDTO."""
from decimal import Decimal
from datetime import datetime

import pytest

from core.model import RawRow
from core.services.holdings_engine import compute_cash_balance


# ── Helpers ───────────────────────────────────────────────────────────────────

def _row(type_: str, asset: str, amount: str, *, currency: str = "EUR",
         id_: str = "T001", note: str = None) -> RawRow:
    return RawRow(
        id=id_,
        timestamp=datetime(2024, 1, 1),
        type=type_,
        asset=asset,
        amount=Decimal(amount),
        currency=currency,
        price=Decimal("0"),
        venue="test",
        note=note,
    )


# ── Základní toky ─────────────────────────────────────────────────────────────

class TestCashBalance:
    def test_cash_in_pridava_zustatek(self):
        result = compute_cash_balance([_row("CASH_IN", "EUR", "1000")])
        assert result["EUR"] == Decimal("1000")

    def test_cash_out_snizuje_zustatek(self):
        rows = [
            _row("CASH_IN", "EUR", "1000", id_="T001"),
            _row("CASH_OUT", "EUR", "-500", id_="T002"),
        ]
        result = compute_cash_balance(rows)
        assert result["EUR"] == Decimal("500")

    def test_buy_eur_nozicka_snizuje_cash(self):
        rows = [
            _row("CASH_IN", "EUR", "1000", id_="T001"),
            _row("BUY",     "EUR", "-262.35", id_="T002"),
        ]
        result = compute_cash_balance(rows)
        assert result["EUR"] == Decimal("737.65")

    def test_sell_eur_nozicka_zvysuje_cash(self):
        rows = [
            _row("CASH_IN", "EUR", "500",    id_="T001"),
            _row("SELL",    "EUR", "262.35", id_="T002"),
        ]
        result = compute_cash_balance(rows)
        assert result["EUR"] == Decimal("762.35")

    def test_dividend_pridava_cash(self):
        rows = [
            _row("CASH_IN",  "EUR", "1000", id_="T001"),
            _row("DIVIDEND", "EUR", "5.00", id_="T002"),
        ]
        result = compute_cash_balance(rows)
        assert result["EUR"] == Decimal("1005")

    def test_fee_snizuje_cash(self):
        rows = [
            _row("CASH_IN", "EUR", "1000",  id_="T001"),
            _row("FEE",     "EUR", "-2.00", id_="T002"),
        ]
        result = compute_cash_balance(rows)
        assert result["EUR"] == Decimal("998")

    def test_tax_snizuje_cash(self):
        rows = [
            _row("CASH_IN", "EUR", "1000",  id_="T001"),
            _row("TAX",     "EUR", "-1.00", id_="T002"),
        ]
        result = compute_cash_balance(rows)
        assert result["EUR"] == Decimal("999")


# ── Non-FIAT assets se nezapočítávají ─────────────────────────────────────────

class TestCashNonFiat:
    def test_tickerova_nozicka_buy_se_ignoruje(self):
        rows = [
            _row("CASH_IN", "EUR",    "1000", id_="T001"),
            _row("BUY",     "ABB.CH", "3",    id_="T002"),  # asset leg — ignoruj
        ]
        result = compute_cash_balance(rows)
        assert "ABB.CH" not in result
        assert result["EUR"] == Decimal("1000")

    def test_tickerova_nozicka_sell_se_ignoruje(self):
        rows = [
            _row("CASH_IN", "EUR",   "1000", id_="T001"),
            _row("SELL",    "SMCI.US", "-5", id_="T002"),  # asset leg — ignoruj
        ]
        result = compute_cash_balance(rows)
        assert "SMCI.US" not in result
        assert result["EUR"] == Decimal("1000")


# ── REVERSAL logika ───────────────────────────────────────────────────────────

class TestCashReversal:
    def test_reversal_vylucuje_puvodni_trade(self):
        rows = [
            _row("CASH_IN", "EUR", "1000",   id_="T001"),
            _row("BUY",     "EUR", "-500",   id_="T002"),
            _row("REVERSAL","EUR", "0",      id_="T003", note="REVERSAL of T002"),
        ]
        result = compute_cash_balance(rows)
        # T002 je stornován — EUR nožička BUY se nepočítá
        assert result["EUR"] == Decimal("1000")

    def test_reversal_row_sam_se_nezapocitava(self):
        # REVERSAL type row sám o sobě nesmí přidat do balance
        rows = [
            _row("REVERSAL", "EUR", "999", id_="T001", note="REVERSAL of XXXX"),
        ]
        result = compute_cash_balance(rows)
        assert result == {}


# ── Edge cases ────────────────────────────────────────────────────────────────

class TestCashEdgeCases:
    def test_prazdny_ledger_vraci_prazdny_dict(self):
        assert compute_cash_balance([]) == {}

    def test_nulovy_zustatek_neni_vracen(self):
        rows = [
            _row("CASH_IN",  "EUR", "1000",  id_="T001"),
            _row("CASH_OUT", "EUR", "-1000", id_="T002"),
        ]
        result = compute_cash_balance(rows)
        assert "EUR" not in result

    def test_vice_men_nezavisle(self):
        rows = [
            _row("CASH_IN", "EUR", "1000", currency="EUR", id_="T001"),
            _row("CASH_IN", "USD", "500",  currency="USD", id_="T002"),
        ]
        result = compute_cash_balance(rows)
        assert result["EUR"] == Decimal("1000")
        assert result["USD"] == Decimal("500")

    def test_decimal_presnost(self):
        rows = [
            _row("CASH_IN", "EUR", "1000.00", id_="T001"),
            _row("BUY",     "EUR", "-262.35", id_="T002"),
            _row("BUY",     "EUR", "-142.45", id_="T003"),
        ]
        result = compute_cash_balance(rows)
        assert result["EUR"] == Decimal("595.20")


# ── Integrace s ui_facade.PortfolioSnapshotDTO ────────────────────────────────

class TestPortfolioSnapshotCash:
    def test_snapshot_obsahuje_cash(self, tmp_path):
        from core.services.ui_facade import (
            AddTradeRequestDTO, add_trade, create_db, get_portfolio_snapshot,
        )

        db = str(tmp_path / "test.db")
        create_db(db)

        add_trade(AddTradeRequestDTO(
            type="CASH_IN", timestamp=datetime(2024, 1, 1),
            asset="EUR", amount=Decimal("1000"), currency="EUR",
            price=None, venue="xtb",
        ), db)

        snap = get_portfolio_snapshot(db)
        assert snap.cash_by_currency.get("EUR") == Decimal("1000")

    def test_cash_snizuje_se_po_buy(self, tmp_path):
        from core.services.ui_facade import (
            AddTradeRequestDTO, add_trade, create_db, get_portfolio_snapshot,
        )

        db = str(tmp_path / "test.db")
        create_db(db)

        add_trade(AddTradeRequestDTO(
            type="CASH_IN", timestamp=datetime(2024, 1, 1),
            asset="EUR", amount=Decimal("1000"), currency="EUR",
            price=None, venue="xtb",
        ), db)
        add_trade(AddTradeRequestDTO(
            type="BUY", timestamp=datetime(2024, 1, 2),
            asset="AAPL", amount=Decimal("2"), currency="EUR",
            price=Decimal("150"), quote_amount=Decimal("300"), venue="xtb",
        ), db)

        snap = get_portfolio_snapshot(db)
        assert snap.cash_by_currency.get("EUR") == Decimal("700")

    def test_cash_nema_pozice_v_holdings(self, tmp_path):
        """EUR cash nesmí vytvářet akciovou pozici v holdings."""
        from core.services.ui_facade import (
            AddTradeRequestDTO, add_trade, create_db, get_portfolio_snapshot,
        )

        db = str(tmp_path / "test.db")
        create_db(db)

        add_trade(AddTradeRequestDTO(
            type="CASH_IN", timestamp=datetime(2024, 1, 1),
            asset="EUR", amount=Decimal("5000"), currency="EUR",
            price=None, venue="xtb",
        ), db)

        snap = get_portfolio_snapshot(db)
        # Cash nesmí být jako PositionDTO
        tickers = [p.ticker for p in snap.positions]
        assert "EUR" not in tickers
        assert snap.positions == []

    def test_akciove_pnl_nezmeneny_pritomnosti_cash(self, tmp_path):
        """Přítomnost cash nesmí ovlivnit výpočet P&L akciových pozic."""
        from core.services.ui_facade import (
            AddTradeRequestDTO, add_trade, create_db, get_portfolio_snapshot,
        )

        db = str(tmp_path / "test.db")
        create_db(db)

        # Vložíme velký cash
        add_trade(AddTradeRequestDTO(
            type="CASH_IN", timestamp=datetime(2024, 1, 1),
            asset="EUR", amount=Decimal("100000"), currency="EUR",
            price=None, venue="xtb",
        ), db)

        # Koupíme akcii
        add_trade(AddTradeRequestDTO(
            type="BUY", timestamp=datetime(2024, 1, 2),
            asset="AAPL", amount=Decimal("1"), currency="EUR",
            price=Decimal("150"), quote_amount=Decimal("150"), venue="xtb",
        ), db)

        snap = get_portfolio_snapshot(db)
        pos = next(p for p in snap.positions if p.ticker == "AAPL")

        # cost_basis = 150 EUR (nezávisle na výši cash)
        assert pos.cost_basis == Decimal("150")
        assert pos.wac == Decimal("150")
        # P&L je None bez spot ceny — nezměněno
        assert pos.unrealized_pnl is None
        assert pos.roi is None
