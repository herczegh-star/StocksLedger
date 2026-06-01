"""Testy pro RawRow datový model."""
from datetime import datetime
from decimal import Decimal

import pytest

from core.model import RawRow, VALID_TYPES


class TestValidTypes:
    def test_contains_buy_sell(self):
        assert "BUY" in VALID_TYPES
        assert "SELL" in VALID_TYPES

    def test_contains_stocks_specific_types(self):
        assert "DIVIDEND" in VALID_TYPES
        assert "FEE" in VALID_TYPES
        assert "TAX" in VALID_TYPES
        assert "CASH_IN" in VALID_TYPES
        assert "CASH_OUT" in VALID_TYPES
        assert "REVERSAL" in VALID_TYPES

    def test_no_staking(self):
        assert "STAKING" not in VALID_TYPES

    def test_no_transfer_in_m1(self):
        # TRANSFER není v M1
        assert "TRANSFER" not in VALID_TYPES


class TestRawRowCreation:
    def test_basic_creation(self):
        row = RawRow(
            timestamp=datetime(2024, 1, 15, 10, 0, 0),
            type="BUY",
            asset="AAPL",
            amount=Decimal("10"),
            currency="USD",
            price=Decimal("175.50"),
            venue="xtb",
        )
        assert row.asset == "AAPL"
        assert row.amount == Decimal("10")
        assert row.id is not None

    def test_auto_id_generated(self):
        row = RawRow(
            timestamp=datetime(2024, 1, 15),
            type="BUY", asset="AAPL",
            amount=Decimal("5"), currency="USD",
            price=Decimal("100"), venue="xtb",
        )
        assert row.id is not None
        assert len(row.id) > 0

    def test_explicit_id_preserved(self):
        row = RawRow(
            timestamp=datetime(2024, 1, 15),
            type="BUY", asset="AAPL",
            amount=Decimal("5"), currency="USD",
            price=Decimal("100"), venue="xtb",
            id="my-custom-id",
        )
        assert row.id == "my-custom-id"

    def test_int_amount_converted_to_decimal(self):
        row = RawRow(
            timestamp=datetime(2024, 1, 15),
            type="BUY", asset="AAPL",
            amount=10, currency="USD",
            price=175.50, venue="xtb",
        )
        assert isinstance(row.amount, Decimal)
        assert isinstance(row.price, Decimal)

    def test_string_timestamp_parsed(self):
        row = RawRow(
            timestamp="2024-03-15T10:30:00",
            type="BUY", asset="AAPL",
            amount=Decimal("5"), currency="EUR",
            price=Decimal("100"), venue="xtb",
        )
        assert isinstance(row.timestamp, datetime)
        assert row.timestamp.year == 2024

    def test_note_optional(self):
        row = RawRow(
            timestamp=datetime(2024, 1, 15),
            type="DIVIDEND", asset="AAPL",
            amount=Decimal("25.50"), currency="USD",
            price=None, venue="xtb",
        )
        assert row.note is None

    def test_price_none_allowed(self):
        row = RawRow(
            timestamp=datetime(2024, 1, 15),
            type="CASH_IN", asset="EUR",
            amount=Decimal("1000"), currency="EUR",
            price=None, venue="xtb",
        )
        assert row.price is None


class TestFingerprint:
    def test_fingerprint_is_hex_string(self):
        row = RawRow(
            timestamp=datetime(2024, 1, 15, 10, 0),
            type="BUY", asset="AAPL",
            amount=Decimal("10"), currency="USD",
            price=Decimal("175"), venue="xtb",
        )
        fp = row.fingerprint()
        assert isinstance(fp, str)
        assert len(fp) == 64  # SHA-256 hex

    def test_identical_rows_same_fingerprint(self):
        ts = datetime(2024, 1, 15, 10, 0, 0)
        row1 = RawRow(timestamp=ts, type="BUY", asset="AAPL",
                      amount=Decimal("10"), currency="USD",
                      price=Decimal("175"), venue="xtb")
        row2 = RawRow(timestamp=ts, type="BUY", asset="AAPL",
                      amount=Decimal("10"), currency="USD",
                      price=Decimal("175"), venue="xtb")
        assert row1.fingerprint() == row2.fingerprint()

    def test_different_amount_different_fingerprint(self):
        ts = datetime(2024, 1, 15, 10, 0, 0)
        row1 = RawRow(timestamp=ts, type="BUY", asset="AAPL",
                      amount=Decimal("10"), currency="USD",
                      price=Decimal("175"), venue="xtb")
        row2 = RawRow(timestamp=ts, type="BUY", asset="AAPL",
                      amount=Decimal("11"), currency="USD",
                      price=Decimal("175"), venue="xtb")
        assert row1.fingerprint() != row2.fingerprint()

    def test_different_currency_different_fingerprint(self):
        ts = datetime(2024, 1, 15, 10, 0, 0)
        row1 = RawRow(timestamp=ts, type="BUY", asset="AAPL",
                      amount=Decimal("10"), currency="USD",
                      price=Decimal("175"), venue="xtb")
        row2 = RawRow(timestamp=ts, type="BUY", asset="AAPL",
                      amount=Decimal("10"), currency="EUR",
                      price=Decimal("175"), venue="xtb")
        assert row1.fingerprint() != row2.fingerprint()

    def test_case_insensitive_asset_venue(self):
        ts = datetime(2024, 1, 15, 10, 0, 0)
        row1 = RawRow(timestamp=ts, type="BUY", asset="aapl",
                      amount=Decimal("10"), currency="usd",
                      price=Decimal("175"), venue="XTB")
        row2 = RawRow(timestamp=ts, type="BUY", asset="AAPL",
                      amount=Decimal("10"), currency="USD",
                      price=Decimal("175"), venue="xtb")
        # fingerprint normalizuje: upper pro asset/currency, lower pro venue
        assert row1.fingerprint() == row2.fingerprint()

    def test_to_dict(self):
        row = RawRow(
            timestamp=datetime(2024, 1, 15, 10, 0),
            type="BUY", asset="AAPL",
            amount=Decimal("10"), currency="USD",
            price=Decimal("175.50"), venue="xtb",
        )
        d = row.to_dict()
        assert d["asset"] == "AAPL"
        assert d["amount"] == "10"
        assert d["price"] == "175.50"
        assert isinstance(d["timestamp"], str)
