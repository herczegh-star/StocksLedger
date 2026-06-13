"""Tests for pure helper functions in reentry_view — M-RE4a."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from core.services.reentry_engine import SellEventRaw
from core.services.ui_facade import HarvestCandidateDTO
from ui.modules.reentry_view import (
    BLUE_300,
    GREEN,
    T_MUT,
    _candidate_status,
    _fmt_pct,
    _fmt_shares,
    _sort_candidates,
)

_D = Decimal
_TS = datetime(2024, 6, 1)


def _sell_event(ticker: str = "AAPL") -> SellEventRaw:
    return SellEventRaw(
        trade_id="S001", ticker=ticker,
        sell_qty=_D("10"), sell_price=_D("180"),
        released_eur=_D("1800"), currency="EUR",
        sell_date=_TS, venue="xtb",
    )


def _candidate(
    spot_eur=None,
    efficiency_pct=None,
    is_alert=False,
    rebuy_price=None,
    effective_cash=None,
    rebuy_qty=None,
    extra_shares=None,
) -> HarvestCandidateDTO:
    return HarvestCandidateDTO(
        sell_event=_sell_event(),
        spot_eur=_D(spot_eur) if spot_eur is not None else None,
        rebuy_price=_D(rebuy_price) if rebuy_price is not None else None,
        effective_cash=_D(effective_cash) if effective_cash is not None else None,
        rebuy_qty=_D(rebuy_qty) if rebuy_qty is not None else None,
        extra_shares=_D(extra_shares) if extra_shares is not None else None,
        efficiency_pct=_D(efficiency_pct) if efficiency_pct is not None else None,
        is_alert=is_alert,
    )


# ── _candidate_status ─────────────────────────────────────────────────────────

class TestCandidateStatus:

    def test_no_spot_returns_awaiting(self):
        text, color = _candidate_status(_candidate(spot_eur=None))
        assert text == "Awaiting price..."
        assert color == T_MUT

    def test_enriched_no_efficiency_returns_price_na(self):
        # spot loaded but efficiency somehow None (should not happen in practice)
        c = _candidate(spot_eur="140", efficiency_pct=None)
        text, color = _candidate_status(c)
        assert text == "Price N/A"
        assert color == T_MUT

    def test_alert_true_returns_opportunity(self):
        c = _candidate(spot_eur="140", efficiency_pct="27.5", is_alert=True)
        text, color = _candidate_status(c)
        assert text == "Re-entry opportunity"
        assert color == BLUE_300

    def test_not_alert_positive_efficiency(self):
        c = _candidate(spot_eur="140", efficiency_pct="5.50", is_alert=False)
        text, color = _candidate_status(c)
        assert text.startswith("Not ready")
        assert "+5.50%" in text
        assert color == T_MUT

    def test_not_alert_negative_efficiency(self):
        c = _candidate(spot_eur="200", efficiency_pct="-15.00", is_alert=False)
        text, color = _candidate_status(c)
        assert text.startswith("Not ready")
        assert "-15.00%" in text
        assert color == T_MUT

    def test_zero_efficiency_not_alert(self):
        c = _candidate(spot_eur="100", efficiency_pct="0.00", is_alert=False)
        text, color = _candidate_status(c)
        assert text.startswith("Not ready")
        assert color == T_MUT


# ── _fmt_pct ──────────────────────────────────────────────────────────────────

class TestFmtPct:

    def test_none_returns_dash(self):
        assert _fmt_pct(None) == "—"

    def test_positive_has_plus_prefix(self):
        result = _fmt_pct(_D("12.34"))
        assert result.startswith("+")
        assert "12.34%" in result

    def test_negative_has_no_plus(self):
        result = _fmt_pct(_D("-5.5"))
        assert not result.startswith("+")
        assert "-5.50%" in result

    def test_zero_no_plus(self):
        result = _fmt_pct(_D("0"))
        assert result == "0.00%"

    def test_two_decimal_places(self):
        result = _fmt_pct(_D("3"))
        assert result == "+3.00%"

    def test_large_value(self):
        result = _fmt_pct(_D("150.987"))
        assert "+150.99%" in result


# ── _fmt_shares ───────────────────────────────────────────────────────────────

class TestFmtShares:

    def test_none_returns_dash(self):
        assert _fmt_shares(None) == "—"

    def test_positive_integer_has_plus_and_ks(self):
        result = _fmt_shares(_D("5"))
        assert result == "+5 ks"

    def test_negative_no_plus(self):
        result = _fmt_shares(_D("-2"))
        assert result.startswith("-")
        assert "ks" in result

    def test_zero(self):
        result = _fmt_shares(_D("0"))
        assert "0" in result
        assert "ks" in result

    def test_fractional_share(self):
        result = _fmt_shares(_D("2.75"))
        assert "+2.75 ks" == result


# ── _sort_candidates ──────────────────────────────────────────────────────────

class TestSortCandidates:

    def _make(self, is_alert: bool, efficiency_pct=None, ticker: str = "X") -> HarvestCandidateDTO:
        ev = SellEventRaw(
            trade_id="S1", ticker=ticker,
            sell_qty=_D("10"), sell_price=_D("180"),
            released_eur=_D("1800"), currency="EUR",
            sell_date=_TS, venue="xtb",
        )
        return HarvestCandidateDTO(
            sell_event=ev,
            spot_eur=_D("100") if efficiency_pct is not None else None,
            efficiency_pct=_D(str(efficiency_pct)) if efficiency_pct is not None else None,
            is_alert=is_alert,
        )

    def test_alert_before_non_alert(self):
        a = self._make(is_alert=False, efficiency_pct="5")
        b = self._make(is_alert=True,  efficiency_pct="15")
        result = _sort_candidates([a, b])
        assert result[0].is_alert is True

    def test_non_alert_sorted_by_efficiency_desc(self):
        lo = self._make(is_alert=False, efficiency_pct="3")
        hi = self._make(is_alert=False, efficiency_pct="8")
        result = _sort_candidates([lo, hi])
        assert float(result[0].efficiency_pct) > float(result[1].efficiency_pct)

    def test_missing_efficiency_last(self):
        has_eff = self._make(is_alert=False, efficiency_pct="5")
        no_eff  = self._make(is_alert=False, efficiency_pct=None)
        result = _sort_candidates([no_eff, has_eff])
        assert result[-1].efficiency_pct is None

    def test_alert_with_higher_efficiency_first_within_alert_group(self):
        a_lo = self._make(is_alert=True, efficiency_pct="11")
        a_hi = self._make(is_alert=True, efficiency_pct="25")
        result = _sort_candidates([a_lo, a_hi])
        assert float(result[0].efficiency_pct) > float(result[1].efficiency_pct)

    def test_mixed_ordering(self):
        alert_hi  = self._make(is_alert=True,  efficiency_pct="20")
        alert_lo  = self._make(is_alert=True,  efficiency_pct="12")
        non_hi    = self._make(is_alert=False, efficiency_pct="8")
        non_lo    = self._make(is_alert=False, efficiency_pct="2")
        no_price  = self._make(is_alert=False, efficiency_pct=None)
        result = _sort_candidates([non_lo, no_price, alert_lo, non_hi, alert_hi])
        assert result[0].is_alert is True
        assert float(result[0].efficiency_pct) == 20
        assert result[1].is_alert is True
        assert float(result[1].efficiency_pct) == 12
        assert float(result[2].efficiency_pct) == 8
        assert float(result[3].efficiency_pct) == 2
        assert result[4].efficiency_pct is None

    def test_empty_list(self):
        assert _sort_candidates([]) == []

    def test_single_item_unchanged(self):
        c = self._make(is_alert=False, efficiency_pct="5")
        assert _sort_candidates([c]) == [c]
