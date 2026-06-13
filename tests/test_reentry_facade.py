"""Unit testy pro enrich_candidate() a get_reentry_snapshot() — M-RE2."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from core.model import RawRow
from core.services.reentry_engine import SellEventRaw
from core.services.ui_facade import (
    HarvestCandidateDTO,
    ReEntrySnapshotDTO,
    enrich_candidate,
    get_reentry_snapshot,
)

# ── Helpers ───────────────────────────────────────────────────────────────────

_D = Decimal
_TS = datetime(2024, 6, 1)


def _sell_event(
    ticker:       str   = "AAPL",
    sell_qty:     str   = "10",
    sell_price:   str   = "180",
    released_eur: str   = "1800",
    trade_id:     str   = "S001",
    currency:     str   = "EUR",
    venue:        str   = "xtb",
    sell_date:    datetime = _TS,
) -> SellEventRaw:
    return SellEventRaw(
        trade_id=trade_id,
        ticker=ticker,
        sell_qty=_D(sell_qty),
        sell_price=_D(sell_price),
        released_eur=_D(released_eur),
        currency=currency,
        sell_date=sell_date,
        venue=venue,
    )


def _row(
    type_: str,
    asset: str,
    amount: str,
    *,
    id_: str = "T001",
    price: str = "0",
    currency: str = "EUR",
    venue: str = "xtb",
    ts: datetime = _TS,
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


# ── enrich_candidate: simulation math ────────────────────────────────────────

class TestEnrichCandidateMath:

    def test_basic_formula_components(self):
        ev     = _sell_event(sell_qty="10", released_eur="1800")
        spot   = _D("140")
        result = enrich_candidate(ev, spot, spread_buffer_pct=_D("0.5"), fee_pct=_D("0.1"))

        # rebuy_price = 140 × 1.005
        assert result.rebuy_price == _D("140") * (_D("1") + _D("0.5") / _D("100"))

        # effective_cash = 1800 × 0.999
        assert result.effective_cash == _D("1800") * (_D("1") - _D("0.1") / _D("100"))

    def test_rebuy_qty_and_extra_shares_computed(self):
        ev     = _sell_event(sell_qty="10", released_eur="1800")
        result = enrich_candidate(ev, _D("140"))

        assert result.rebuy_qty    is not None
        assert result.extra_shares is not None
        assert result.rebuy_qty    > _D("10")          # cheaper price → more shares
        assert result.extra_shares > _D("0")

    def test_efficiency_pct_formula(self):
        ev     = _sell_event(sell_qty="10", released_eur="1800")
        result = enrich_candidate(ev, _D("140"))

        # Verify formula: extra / sell_qty × 100
        expected = result.extra_shares / _D("10") * _D("100")
        assert result.efficiency_pct == expected

    def test_known_numbers_efficiency_range(self):
        # sell_qty=10, released=1800, spot=140, spread=0.5%, fee=0.1%
        # rebuy_price=140.70, effective_cash=1798.20
        # rebuy_qty ≈ 12.78, efficiency ≈ 27.8%
        result = enrich_candidate(
            _sell_event(sell_qty="10", released_eur="1800"),
            _D("140"),
        )
        assert result.efficiency_pct > _D("27")
        assert result.efficiency_pct < _D("28")

    def test_zero_spread_zero_fee_exact_math(self):
        # With no buffers the math is exact and easy to verify
        ev     = _sell_event(sell_qty="10", released_eur="1500")
        result = enrich_candidate(
            ev, _D("100"),
            spread_buffer_pct=_D("0"),
            fee_pct=_D("0"),
        )
        # rebuy_price = 100, effective_cash = 1500, rebuy_qty = 15
        assert result.rebuy_price    == _D("100")
        assert result.effective_cash == _D("1500")
        assert result.rebuy_qty      == _D("15")
        assert result.extra_shares   == _D("5")
        assert result.efficiency_pct == _D("50")

    def test_sell_event_reference_preserved(self):
        ev     = _sell_event()
        result = enrich_candidate(ev, _D("140"))
        assert result.sell_event is ev

    def test_spot_eur_stored_on_result(self):
        result = enrich_candidate(_sell_event(), _D("135"))
        assert result.spot_eur == _D("135")


# ── enrich_candidate: is_alert logic ─────────────────────────────────────────

class TestEnrichCandidateAlert:

    def test_alert_true_when_efficiency_above_threshold(self):
        # sell_qty=10, released=1100, spot=100, no buffers → efficiency=10% exactly → alert
        result = enrich_candidate(
            _sell_event(sell_qty="10", released_eur="1100"),
            _D("100"),
            threshold_pct=_D("10"),
            spread_buffer_pct=_D("0"),
            fee_pct=_D("0"),
        )
        assert result.efficiency_pct == _D("10")
        assert result.is_alert is True   # >= threshold

    def test_alert_false_when_efficiency_below_threshold(self):
        # released=1099 → efficiency slightly below 10%
        result = enrich_candidate(
            _sell_event(sell_qty="10", released_eur="1099"),
            _D("100"),
            threshold_pct=_D("10"),
            spread_buffer_pct=_D("0"),
            fee_pct=_D("0"),
        )
        assert result.efficiency_pct < _D("10")
        assert result.is_alert is False

    def test_custom_threshold_respected(self):
        # efficiency ≈ 27.8% with default buffers — alert at both 10% and 25%
        result_10 = enrich_candidate(_sell_event(sell_qty="10", released_eur="1800"),
                                     _D("140"), threshold_pct=_D("10"))
        result_30 = enrich_candidate(_sell_event(sell_qty="10", released_eur="1800"),
                                     _D("140"), threshold_pct=_D("30"))
        assert result_10.is_alert is True
        assert result_30.is_alert is False

    def test_negative_efficiency_no_alert(self):
        # Price went UP after sell — can buy fewer shares → efficiency negative
        result = enrich_candidate(
            _sell_event(sell_qty="10", released_eur="1000"),
            _D("120"),   # higher than sell price of 180 would have been
            spread_buffer_pct=_D("0"),
            fee_pct=_D("0"),
        )
        # rebuy_qty = 1000/120 ≈ 8.33 < 10 → extra_shares < 0
        assert result.extra_shares   < _D("0")
        assert result.efficiency_pct < _D("0")
        assert result.is_alert       is False


# ── enrich_candidate: safety guards ──────────────────────────────────────────

class TestEnrichCandidateSafety:

    def _assert_blank(self, result: HarvestCandidateDTO) -> None:
        assert result.spot_eur       is None
        assert result.rebuy_price    is None
        assert result.effective_cash is None
        assert result.rebuy_qty      is None
        assert result.extra_shares   is None
        assert result.efficiency_pct is None
        assert result.is_alert       is False

    def test_none_spot_returns_blank(self):
        self._assert_blank(enrich_candidate(_sell_event(), None))

    def test_zero_spot_returns_blank(self):
        self._assert_blank(enrich_candidate(_sell_event(), _D("0")))

    def test_negative_spot_returns_blank(self):
        self._assert_blank(enrich_candidate(_sell_event(), _D("-10")))

    def test_zero_released_eur_returns_blank(self):
        ev = _sell_event(released_eur="0")
        self._assert_blank(enrich_candidate(ev, _D("140")))

    def test_negative_released_eur_returns_blank(self):
        ev = _sell_event(released_eur="-100")
        self._assert_blank(enrich_candidate(ev, _D("140")))

    def test_zero_sell_qty_returns_blank(self):
        ev = _sell_event(sell_qty="0")
        self._assert_blank(enrich_candidate(ev, _D("140")))

    def test_sell_event_still_preserved_in_blank(self):
        ev     = _sell_event()
        result = enrich_candidate(ev, None)
        assert result.sell_event is ev


# ── enrich_candidate: buffer sensitivity ─────────────────────────────────────

class TestEnrichCandidateBuffers:

    def test_higher_spread_reduces_rebuy_qty(self):
        ev   = _sell_event(sell_qty="10", released_eur="1100")
        low  = enrich_candidate(ev, _D("100"), spread_buffer_pct=_D("0"),   fee_pct=_D("0"))
        high = enrich_candidate(ev, _D("100"), spread_buffer_pct=_D("2"),   fee_pct=_D("0"))
        assert high.rebuy_qty < low.rebuy_qty

    def test_higher_fee_reduces_effective_cash(self):
        ev   = _sell_event(sell_qty="10", released_eur="1100")
        low  = enrich_candidate(ev, _D("100"), spread_buffer_pct=_D("0"), fee_pct=_D("0"))
        high = enrich_candidate(ev, _D("100"), spread_buffer_pct=_D("0"), fee_pct=_D("1"))
        assert high.effective_cash < low.effective_cash

    def test_zero_buffers_gives_best_efficiency(self):
        ev   = _sell_event(sell_qty="10", released_eur="1100")
        zero = enrich_candidate(ev, _D("100"), spread_buffer_pct=_D("0"),   fee_pct=_D("0"))
        real = enrich_candidate(ev, _D("100"), spread_buffer_pct=_D("0.5"), fee_pct=_D("0.1"))
        assert zero.efficiency_pct > real.efficiency_pct


# ── get_reentry_snapshot ──────────────────────────────────────────────────────

class TestGetReentrySnapshot:

    def test_empty_ledger_returns_empty_candidates(self, tmp_path):
        from core.services.ui_facade import create_db
        db = str(tmp_path / "t.db")
        create_db(db)
        snap = get_reentry_snapshot(db)
        assert isinstance(snap, ReEntrySnapshotDTO)
        assert snap.candidates == []

    def test_default_threshold_is_ten_pct(self, tmp_path):
        from core.services.ui_facade import create_db
        db = str(tmp_path / "t.db")
        create_db(db)
        snap = get_reentry_snapshot(db)
        assert snap.threshold_pct == _D("10")

    def test_returns_one_candidate_per_sell(self, tmp_path):
        from core.services.ui_facade import AddTradeRequestDTO, add_trade, create_db

        db = str(tmp_path / "t.db")
        create_db(db)

        # BUY 10 AAPL then SELL 10 AAPL
        add_trade(AddTradeRequestDTO(
            type="BUY", timestamp=datetime(2024, 1, 1),
            asset="AAPL", amount=_D("10"), currency="EUR",
            price=_D("150"), quote_amount=_D("1500"), venue="xtb",
        ), db)
        add_trade(AddTradeRequestDTO(
            type="SELL", timestamp=datetime(2024, 6, 1),
            asset="AAPL", amount=_D("10"), currency="EUR",
            price=_D("180"), quote_amount=_D("1800"), venue="xtb",
        ), db)

        snap = get_reentry_snapshot(db)
        assert len(snap.candidates) == 1

    def test_candidate_sell_event_has_correct_ticker(self, tmp_path):
        from core.services.ui_facade import AddTradeRequestDTO, add_trade, create_db

        db = str(tmp_path / "t.db")
        create_db(db)

        add_trade(AddTradeRequestDTO(
            type="BUY", timestamp=datetime(2024, 1, 1),
            asset="SAP", amount=_D("5"), currency="EUR",
            price=_D("160"), quote_amount=_D("800"), venue="xtb",
        ), db)
        add_trade(AddTradeRequestDTO(
            type="SELL", timestamp=datetime(2024, 6, 1),
            asset="SAP", amount=_D("5"), currency="EUR",
            price=_D("200"), quote_amount=_D("1000"), venue="xtb",
        ), db)

        snap    = get_reentry_snapshot(db)
        c       = snap.candidates[0]
        assert c.sell_event.ticker == "SAP"

    def test_candidates_are_unenriched(self, tmp_path):
        """get_reentry_snapshot does not fetch prices — all simulation fields must be None."""
        from core.services.ui_facade import AddTradeRequestDTO, add_trade, create_db

        db = str(tmp_path / "t.db")
        create_db(db)

        add_trade(AddTradeRequestDTO(
            type="BUY", timestamp=datetime(2024, 1, 1),
            asset="AAPL", amount=_D("10"), currency="EUR",
            price=_D("150"), quote_amount=_D("1500"), venue="xtb",
        ), db)
        add_trade(AddTradeRequestDTO(
            type="SELL", timestamp=datetime(2024, 6, 1),
            asset="AAPL", amount=_D("10"), currency="EUR",
            price=_D("180"), quote_amount=_D("1800"), venue="xtb",
        ), db)

        snap = get_reentry_snapshot(db)
        c    = snap.candidates[0]

        assert c.spot_eur       is None
        assert c.rebuy_price    is None
        assert c.effective_cash is None
        assert c.rebuy_qty      is None
        assert c.extra_shares   is None
        assert c.efficiency_pct is None
        assert c.is_alert       is False

    def test_multiple_sells_multiple_candidates(self, tmp_path):
        from core.services.ui_facade import AddTradeRequestDTO, add_trade, create_db

        db = str(tmp_path / "t.db")
        create_db(db)

        for asset, qty, price in [("AAPL", "10", "180"), ("SAP", "5", "200")]:
            add_trade(AddTradeRequestDTO(
                type="BUY", timestamp=datetime(2024, 1, 1),
                asset=asset, amount=_D(qty), currency="EUR",
                price=_D(price), quote_amount=_D(qty) * _D(price), venue="xtb",
            ), db)
            add_trade(AddTradeRequestDTO(
                type="SELL", timestamp=datetime(2024, 6, 1),
                asset=asset, amount=_D(qty), currency="EUR",
                price=_D(price), quote_amount=_D(qty) * _D(price), venue="xtb",
            ), db)

        snap = get_reentry_snapshot(db)
        assert len(snap.candidates) == 2
