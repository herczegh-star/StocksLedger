"""Testy pro syntaktický validator."""
from datetime import datetime
from decimal import Decimal

import pytest

from core.model import RawRow
from core.validator import validate_row, validate_rows


def _make_row(**kwargs) -> RawRow:
    defaults = dict(
        timestamp=datetime(2024, 1, 15, 10, 0),
        type="BUY", asset="AAPL",
        amount=Decimal("10"), currency="USD",
        price=Decimal("175"), venue="xtb",
    )
    defaults.update(kwargs)
    return RawRow(**defaults)


class TestValidateRow:
    def test_valid_buy(self):
        ok, errs = validate_row(_make_row())
        assert ok is True
        assert errs == []

    def test_valid_sell(self):
        ok, errs = validate_row(_make_row(type="SELL", amount=Decimal("-5")))
        assert ok is True

    def test_valid_dividend(self):
        ok, errs = validate_row(_make_row(type="DIVIDEND", asset="AAPL",
                                          amount=Decimal("25.50")))
        assert ok is True

    def test_valid_fee(self):
        ok, errs = validate_row(_make_row(type="FEE", asset="EUR",
                                          amount=Decimal("-5"), currency="EUR"))
        assert ok is True

    def test_valid_tax(self):
        ok, errs = validate_row(_make_row(type="TAX", asset="USD",
                                          amount=Decimal("-3.75"), currency="USD"))
        assert ok is True

    def test_valid_cash_in(self):
        ok, errs = validate_row(_make_row(type="CASH_IN", asset="EUR",
                                          amount=Decimal("1000"), currency="EUR", price=None))
        assert ok is True

    def test_valid_cash_out(self):
        ok, errs = validate_row(_make_row(type="CASH_OUT", asset="EUR",
                                          amount=Decimal("-500"), currency="EUR", price=None))
        assert ok is True

    def test_valid_reversal(self):
        ok, errs = validate_row(_make_row(type="REVERSAL", amount=Decimal("-10")))
        assert ok is True

    def test_invalid_type(self):
        ok, errs = validate_row(_make_row(type="STAKING"))
        assert ok is False
        assert any("type" in e.lower() or "neplatný" in e.lower() for e in errs)

    def test_zero_amount_fails(self):
        ok, errs = validate_row(_make_row(amount=Decimal("0")))
        assert ok is False
        assert any("0" in e for e in errs)

    def test_empty_asset_fails(self):
        ok, errs = validate_row(_make_row(asset=""))
        assert ok is False

    def test_empty_venue_fails(self):
        ok, errs = validate_row(_make_row(venue=""))
        assert ok is False

    def test_empty_currency_fails(self):
        ok, errs = validate_row(_make_row(currency=""))
        assert ok is False

    def test_invalid_price_fails(self):
        row = _make_row()
        row.price = "not-a-number"  # type: ignore
        ok, errs = validate_row(row)
        assert ok is False


class TestValidateRows:
    def test_all_valid(self):
        rows = [_make_row(amount=Decimal(str(i + 1))) for i in range(3)]
        valid, invalid = validate_rows(rows)
        assert len(valid) == 3
        assert len(invalid) == 0

    def test_mixed_valid_invalid(self):
        rows = [
            _make_row(amount=Decimal("10")),
            _make_row(type="INVALID_TYPE"),
            _make_row(amount=Decimal("5")),
        ]
        valid, invalid = validate_rows(rows)
        assert len(valid) == 2
        assert len(invalid) == 1
        assert invalid[0][0] == 1  # index
