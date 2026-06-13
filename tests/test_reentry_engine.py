"""Unit testy pro compute_sell_events() — ReEntry Watch M-RE1."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from core.model import RawRow
from core.services.reentry_engine import SellEventRaw, compute_sell_events


# ── Helpers ───────────────────────────────────────────────────────────────────

_D = Decimal

_T1 = datetime(2024, 1, 10)
_T2 = datetime(2024, 3, 15)
_T3 = datetime(2024, 6, 1)


def _row(
    type_: str,
    asset: str,
    amount: str,
    *,
    id_: str = "T001",
    price: str = "0",
    currency: str = "EUR",
    venue: str = "xtb",
    ts: datetime = _T3,
    note: str = None,
) -> RawRow:
    return RawRow(
        id=id_,
        timestamp=ts,
        type=type_,
        asset=asset,
        amount=_D(amount),
        currency=currency,
        price=_D(price),
        venue=venue,
        note=note,
    )


def _sell_pair(
    ticker: str,
    qty: str,
    price: str,
    released: str,
    *,
    id_: str = "S001",
    currency: str = "EUR",
    venue: str = "xtb",
    ts: datetime = _T3,
) -> list:
    """Double-entry SELL: ticker leg (negative) + FIAT cash leg (positive)."""
    return [
        _row("SELL", ticker,   f"-{qty}", id_=id_, price=price,
             currency=currency, venue=venue, ts=ts),
        _row("SELL", currency, released,  id_=id_, price="0",
             currency=currency, venue=venue, ts=ts),
    ]


def _buy_pair(
    ticker: str,
    qty: str,
    price: str,
    *,
    id_: str = "B001",
    currency: str = "EUR",
    venue: str = "xtb",
    ts: datetime = _T1,
) -> list:
    """Double-entry BUY: ticker leg (positive) + FIAT cash leg (negative)."""
    total = str(_D(qty) * _D(price))
    return [
        _row("BUY", ticker,   qty,        id_=id_, price=price,
             currency=currency, venue=venue, ts=ts),
        _row("BUY", currency, f"-{total}", id_=id_, price="0",
             currency=currency, venue=venue, ts=ts),
    ]


# ── Basic derivation ──────────────────────────────────────────────────────────

class TestComputeSellEventsBasic:

    def test_empty_rows_returns_empty_list(self):
        assert compute_sell_events([]) == []

    def test_only_buy_rows_returns_empty(self):
        rows = _buy_pair("AAPL", "10", "150")
        assert compute_sell_events(rows) == []

    def test_simple_buy_sell_pair(self):
        rows = _buy_pair("AAPL", "10", "150") + _sell_pair("AAPL", "10", "180", "1800")
        result = compute_sell_events(rows)
        assert len(result) == 1
        e = result[0]
        assert e.ticker       == "AAPL"
        assert e.sell_qty     == _D("10")
        assert e.sell_price   == _D("180")
        assert e.released_eur == _D("1800")
        assert e.currency     == "EUR"
        assert e.venue        == "xtb"

    def test_sell_qty_is_always_positive(self):
        # Ticker leg amount is negative; sell_qty must be positive
        rows = _sell_pair("AAPL", "5", "200", "1000")
        result = compute_sell_events(rows)
        assert result[0].sell_qty > _D("0")
        assert result[0].sell_qty == _D("5")


# ── Multiple SELLs ────────────────────────────────────────────────────────────

class TestMultipleSells:

    def test_multiple_sells_same_ticker(self):
        rows = (
            _buy_pair("AAPL", "10", "150", id_="B001", ts=_T1)
            + _sell_pair("AAPL", "4", "180", "720",  id_="S001", ts=_T2)
            + _sell_pair("AAPL", "6", "200", "1200", id_="S002", ts=_T3)
        )
        result = compute_sell_events(rows)
        assert len(result) == 2

    def test_multiple_sells_different_tickers(self):
        rows = (
            _sell_pair("AAPL", "5", "180", "900",  id_="S001", ts=_T2)
            + _sell_pair("SAP",  "2", "160", "320",  id_="S002", ts=_T3)
        )
        result = compute_sell_events(rows)
        tickers = {e.ticker for e in result}
        assert tickers == {"AAPL", "SAP"}

    def test_results_sorted_by_sell_date_ascending(self):
        rows = (
            _sell_pair("SAP",  "2", "160", "320",  id_="S001", ts=_T3)
            + _sell_pair("AAPL", "5", "180", "900",  id_="S002", ts=_T2)
        )
        result = compute_sell_events(rows)
        assert result[0].sell_date == _T2   # earlier SELL first
        assert result[1].sell_date == _T3


# ── Reversed SELL skipped ─────────────────────────────────────────────────────

class TestReversedSell:

    def test_reversed_sell_is_skipped(self):
        rows = (
            _sell_pair("AAPL", "5", "180", "900", id_="S001")
            + [_row("REVERSAL", "AAPL", "5", id_="REV001", note="REVERSAL of S001")]
        )
        result = compute_sell_events(rows)
        assert result == []

    def test_unreversed_sell_stays_when_other_reversed(self):
        rows = (
            _sell_pair("AAPL", "5", "180", "900", id_="S001", ts=_T2)
            + _sell_pair("SAP",  "2", "160", "320", id_="S002", ts=_T3)
            + [_row("REVERSAL", "AAPL", "5", id_="REV001", note="REVERSAL of S001")]
        )
        result = compute_sell_events(rows)
        assert len(result) == 1
        assert result[0].ticker == "SAP"

    def test_reversal_row_itself_not_treated_as_sell(self):
        rows = [_row("REVERSAL", "AAPL", "-5", id_="REV001", note="REVERSAL of S001")]
        assert compute_sell_events(rows) == []


# ── FIAT SELL skipped ─────────────────────────────────────────────────────────

class TestFiatSell:

    def test_fiat_ticker_leg_skipped(self):
        # SELL row where asset is a FIAT currency — no ticker leg found → skip
        rows = [_row("SELL", "EUR", "-500", id_="S001")]
        assert compute_sell_events(rows) == []

    def test_fiat_cash_leg_alone_skipped(self):
        # Positive FIAT leg without paired ticker leg — no ticker leg → skip
        rows = [_row("SELL", "USD", "500", id_="S001")]
        assert compute_sell_events(rows) == []

    def test_fiat_sell_does_not_pollute_results_alongside_valid_sell(self):
        rows = (
            _sell_pair("AAPL", "5", "180", "900", id_="S001")
            + [_row("SELL", "EUR", "-500", id_="S002")]
        )
        result = compute_sell_events(rows)
        assert len(result) == 1
        assert result[0].ticker == "AAPL"


# ── Partial sell supported ────────────────────────────────────────────────────

class TestPartialSell:

    def test_partial_sell_captures_correct_qty(self):
        # Buy 10, sell 3 → sell event has qty=3
        rows = (
            _buy_pair("AAPL", "10", "150", id_="B001")
            + _sell_pair("AAPL", "3", "180", "540", id_="S001")
        )
        result = compute_sell_events(rows)
        assert len(result) == 1
        assert result[0].sell_qty == _D("3")

    def test_partial_sell_released_eur_from_cash_leg(self):
        rows = _sell_pair("AAPL", "3", "180", "540", id_="S001")
        result = compute_sell_events(rows)
        # 540 comes from the exact EUR leg, not 3 × 180 = 540 (same here, but tested separately below)
        assert result[0].released_eur == _D("540")

    def test_partial_sell_decimal_precision(self):
        # Non-integer quantities
        rows = _sell_pair("AAPL", "2.5", "182.40", "456.00", id_="S001")
        result = compute_sell_events(rows)
        assert result[0].sell_qty     == _D("2.5")
        assert result[0].sell_price   == _D("182.40")
        assert result[0].released_eur == _D("456.00")


# ── released_eur resolution ───────────────────────────────────────────────────

class TestReleasedEur:

    def test_prefers_exact_fiat_leg_when_present(self):
        # EUR leg = 910 (e.g. after fees), price × qty would give 900
        rows = _sell_pair("AAPL", "5", "180", "910", id_="S001")
        result = compute_sell_events(rows)
        assert result[0].released_eur == _D("910")   # leg value, not 5×180=900

    def test_fallback_to_qty_times_price_when_no_fiat_leg(self):
        # Single-row SELL (no EUR leg) — only ticker leg
        rows = [_row("SELL", "AAPL", "-5", id_="S001", price="180")]
        result = compute_sell_events(rows)
        assert len(result) == 1
        assert result[0].released_eur == _D("900")   # 5 × 180

    def test_fallback_released_eur_zero_when_no_price_no_leg(self):
        # No price recorded, no EUR leg — released_eur = 0
        rows = [_row("SELL", "AAPL", "-5", id_="S001", price="0")]
        result = compute_sell_events(rows)
        assert result[0].released_eur == _D("0")

    def test_fiat_leg_with_zero_amount_triggers_fallback(self):
        # EUR leg exists but amount=0 → use fallback
        rows = [
            _row("SELL", "AAPL", "-5",   id_="S001", price="180"),
            _row("SELL", "EUR",  "0",    id_="S001"),   # zero amount → fallback
        ]
        result = compute_sell_events(rows)
        assert result[0].released_eur == _D("900")


# ── trade_id and venue preserved ─────────────────────────────────────────────

class TestMetadata:

    def test_trade_id_preserved(self):
        rows = _sell_pair("AAPL", "5", "180", "900", id_="S-XTB-2024-001")
        result = compute_sell_events(rows)
        assert result[0].trade_id == "S-XTB-2024-001"

    def test_venue_preserved(self):
        rows = _sell_pair("AAPL", "5", "180", "900", id_="S001", venue="degiro")
        result = compute_sell_events(rows)
        assert result[0].venue == "degiro"

    def test_sell_date_preserved(self):
        ts = datetime(2024, 11, 15, 9, 30, 0)
        rows = _sell_pair("AAPL", "5", "180", "900", id_="S001", ts=ts)
        result = compute_sell_events(rows)
        assert result[0].sell_date == ts

    def test_currency_from_ticker_leg(self):
        rows = _sell_pair("AAPL", "5", "180", "900", id_="S001", currency="EUR")
        result = compute_sell_events(rows)
        assert result[0].currency == "EUR"
