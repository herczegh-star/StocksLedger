"""Testy pro trade_service: BUY/SELL double-entry."""
from datetime import datetime
from decimal import Decimal

import pytest

from core.services.trade_service import (
    AddTradeInput,
    TradeResult,
    _FIAT_DEFAULT,
    add_trade,
    build_trade_rows,
)
from core.model import RawRow


def _inp(ttype="BUY", base_asset="AAPL", base_amount=Decimal("10"),
         quote_currency="USD", quote_amount=Decimal("1755"),
         venue="xtb", fee_amount=None, fee_currency=None, note=None) -> AddTradeInput:
    return AddTradeInput(
        type=ttype,
        timestamp=datetime(2024, 3, 15, 10, 30, 0),
        base_asset=base_asset,
        base_amount=base_amount,
        quote_currency=quote_currency,
        quote_amount=quote_amount,
        venue=venue,
        fee_amount=fee_amount,
        fee_currency=fee_currency,
        note=note,
    )


class TestFiatSet:
    def test_eur_in_fiat(self):
        assert "EUR" in _FIAT_DEFAULT

    def test_czk_in_fiat(self):
        assert "CZK" in _FIAT_DEFAULT

    def test_usd_in_fiat(self):
        assert "USD" in _FIAT_DEFAULT

    def test_gbp_in_fiat(self):
        assert "GBP" in _FIAT_DEFAULT

    def test_pln_in_fiat(self):
        assert "PLN" in _FIAT_DEFAULT


class TestBuildTradeRowsBuy:
    def test_returns_two_rows(self):
        rows = build_trade_rows(_inp("BUY"))
        assert len(rows) == 2

    def test_asset_row_positive(self):
        rows = build_trade_rows(_inp("BUY", base_amount=Decimal("10")))
        asset_row = next(r for r in rows if r.asset == "AAPL")
        assert asset_row.amount > 0

    def test_currency_row_negative(self):
        rows = build_trade_rows(_inp("BUY", quote_currency="USD"))
        currency_row = next(r for r in rows if r.asset == "USD")
        assert currency_row.amount < 0

    def test_price_computed_correctly(self):
        rows = build_trade_rows(_inp("BUY", base_amount=Decimal("10"),
                                     quote_amount=Decimal("1755")))
        asset_row = next(r for r in rows if r.asset == "AAPL")
        assert asset_row.price == Decimal("175.5")

    def test_shared_trade_id(self):
        rows = build_trade_rows(_inp("BUY"))
        ids = {r.id for r in rows}
        assert len(ids) == 1

    def test_venue_lowercase(self):
        rows = build_trade_rows(_inp("BUY", venue="XTB"))
        for r in rows:
            assert r.venue == "xtb"


class TestBuildTradeRowsSell:
    def test_asset_row_negative(self):
        rows = build_trade_rows(_inp("SELL", base_amount=Decimal("5")))
        asset_row = next(r for r in rows if r.asset == "AAPL")
        assert asset_row.amount < 0

    def test_currency_row_positive(self):
        rows = build_trade_rows(_inp("SELL", quote_currency="EUR"))
        currency_row = next(r for r in rows if r.asset == "EUR")
        assert currency_row.amount > 0


class TestBuildTradeRowsWithFee:
    def test_three_rows_with_fee(self):
        rows = build_trade_rows(_inp("BUY", fee_amount=Decimal("1.5")))
        assert len(rows) == 3

    def test_fee_row_negative(self):
        rows = build_trade_rows(_inp("BUY", fee_amount=Decimal("1.5")))
        fee_row = next(r for r in rows if r.type == "FEE")
        assert fee_row.amount < 0
        assert fee_row.amount == Decimal("-1.5")

    def test_fee_currency_defaults_to_quote(self):
        rows = build_trade_rows(_inp("BUY", quote_currency="EUR",
                                     fee_amount=Decimal("2")))
        fee_row = next(r for r in rows if r.type == "FEE")
        assert fee_row.asset == "EUR"

    def test_fee_custom_currency(self):
        rows = build_trade_rows(_inp("BUY", fee_amount=Decimal("0.01"),
                                     fee_currency="USD"))
        fee_row = next(r for r in rows if r.type == "FEE")
        assert fee_row.asset == "USD"


class TestMultiCurrency:
    def test_buy_in_eur(self):
        rows = build_trade_rows(_inp("BUY", quote_currency="EUR",
                                     quote_amount=Decimal("1600")))
        currency_row = next(r for r in rows if r.asset == "EUR")
        assert currency_row.amount == Decimal("-1600")

    def test_buy_in_gbp(self):
        rows = build_trade_rows(_inp("BUY", quote_currency="GBP",
                                     quote_amount=Decimal("1200")))
        currency_row = next(r for r in rows if r.asset == "GBP")
        assert currency_row.amount < 0

    def test_buy_in_pln(self):
        rows = build_trade_rows(_inp("BUY", quote_currency="PLN",
                                     quote_amount=Decimal("7000")))
        currency_row = next(r for r in rows if r.asset == "PLN")
        assert currency_row.amount < 0

    def test_invalid_currency_raises(self):
        with pytest.raises(ValueError, match="fiat"):
            build_trade_rows(_inp("BUY", quote_currency="BTC"))

    def test_ticker_as_base_asset(self):
        rows = build_trade_rows(_inp("BUY", base_asset="VOW3",
                                     quote_currency="EUR",
                                     quote_amount=Decimal("1250")))
        asset_row = next(r for r in rows if r.asset == "VOW3")
        assert asset_row.amount > 0


class TestValidation:
    def test_invalid_type_raises(self):
        with pytest.raises(ValueError):
            build_trade_rows(_inp("DIVIDEND"))

    def test_zero_base_amount_raises(self):
        with pytest.raises(ValueError):
            build_trade_rows(_inp("BUY", base_amount=Decimal("0")))

    def test_zero_quote_amount_raises(self):
        with pytest.raises(ValueError):
            build_trade_rows(_inp("BUY", quote_amount=Decimal("0")))

    def test_fiat_as_base_asset_raises(self):
        with pytest.raises(ValueError, match="fiat"):
            build_trade_rows(_inp("BUY", base_asset="EUR", quote_currency="EUR"))


class TestAddTrade:
    def test_writes_to_db(self, tmp_db):
        result = add_trade(tmp_db, _inp("BUY"))
        assert isinstance(result, TradeResult)
        assert result.inserted == 2  # base + currency

    def test_dedup_on_reimport(self, tmp_db):
        inp = _inp("BUY")
        add_trade(tmp_db, inp)
        result2 = add_trade(tmp_db, inp)
        assert result2.inserted == 0
        assert result2.skipped == 2

    def test_sell_writes_correctly(self, tmp_db):
        result = add_trade(tmp_db, _inp("SELL"))
        assert result.inserted == 2


class TestDividendFeeViaFacade:
    """Ověří single-row typy přes ui_facade (end-to-end)."""

    def test_dividend_single_row(self, tmp_db):
        from core.services.ui_facade import AddTradeRequestDTO, add_trade as facade_add
        req = AddTradeRequestDTO(
            type="DIVIDEND",
            timestamp=datetime(2024, 4, 1, 9, 0),
            asset="AAPL",
            amount=Decimal("25.50"),
            currency="USD",
            price=None,
            venue="xtb",
            note="Q1 dividend",
        )
        result = facade_add(req, tmp_db)
        assert result.success is True
        assert result.n_rows_added == 1

    def test_fee_single_row_negative(self, tmp_db):
        from core.services.ui_facade import AddTradeRequestDTO, add_trade as facade_add
        from core.ledger_store import LedgerStore
        req = AddTradeRequestDTO(
            type="FEE",
            timestamp=datetime(2024, 4, 1, 10, 0),
            asset="EUR",
            amount=Decimal("5"),
            currency="EUR",
            price=None,
            venue="xtb",
        )
        facade_add(req, tmp_db)
        store = LedgerStore(tmp_db)
        rows = store.timeline()
        store.close()
        fee_row = next(r for r in rows if r.type == "FEE")
        assert fee_row.amount == Decimal("-5")  # facade přidala mínus

    def test_tax_single_row_negative(self, tmp_db):
        from core.services.ui_facade import AddTradeRequestDTO, add_trade as facade_add
        from core.ledger_store import LedgerStore
        req = AddTradeRequestDTO(
            type="TAX",
            timestamp=datetime(2024, 4, 1, 11, 0),
            asset="USD",
            amount=Decimal("3.82"),
            currency="USD",
            price=None,
            venue="xtb",
        )
        facade_add(req, tmp_db)
        store = LedgerStore(tmp_db)
        rows = store.timeline()
        store.close()
        tax_row = next(r for r in rows if r.type == "TAX")
        assert tax_row.amount < 0

    def test_cash_in_positive(self, tmp_db):
        from core.services.ui_facade import AddTradeRequestDTO, add_trade as facade_add
        from core.ledger_store import LedgerStore
        req = AddTradeRequestDTO(
            type="CASH_IN",
            timestamp=datetime(2024, 1, 2, 9, 0),
            asset="EUR",
            amount=Decimal("1000"),
            currency="EUR",
            price=None,
            venue="xtb",
        )
        facade_add(req, tmp_db)
        store = LedgerStore(tmp_db)
        rows = store.timeline()
        store.close()
        row = next(r for r in rows if r.type == "CASH_IN")
        assert row.amount == Decimal("1000")

    def test_cash_out_negative(self, tmp_db):
        from core.services.ui_facade import AddTradeRequestDTO, add_trade as facade_add
        from core.ledger_store import LedgerStore
        req = AddTradeRequestDTO(
            type="CASH_OUT",
            timestamp=datetime(2024, 1, 3, 9, 0),
            asset="EUR",
            amount=Decimal("500"),
            currency="EUR",
            price=None,
            venue="xtb",
        )
        facade_add(req, tmp_db)
        store = LedgerStore(tmp_db)
        rows = store.timeline()
        store.close()
        row = next(r for r in rows if r.type == "CASH_OUT")
        assert row.amount == Decimal("-500")
