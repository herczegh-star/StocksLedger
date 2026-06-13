"""Unit testy pro to_eur() — currency-aware EUR conversion (BUG-014)."""
from __future__ import annotations

from decimal import Decimal
from typing import Dict, Optional

import pytest

from core.services.price_provider import cache_clear, to_eur

_D = Decimal

# ── Helpers ───────────────────────────────────────────────────────────────────

def _fx(eurusd="1.1", gbpusd=None, chfusd=None) -> Dict[str, Optional[Decimal]]:
    return {
        "EURUSD": _D(eurusd) if eurusd else None,
        "GBPUSD": _D(gbpusd) if gbpusd else None,
        "CHFUSD": _D(chfusd) if chfusd else None,
    }


# ── USD → EUR ─────────────────────────────────────────────────────────────────

class TestToEurUsd:

    def test_basic_usd_conversion(self):
        # 110 USD / 1.1 EURUSD = 100 EUR
        result = to_eur(_D("110"), "USD", _fx("1.1"))
        assert result == _D("100")

    def test_usd_with_different_rate(self):
        result = to_eur(_D("200"), "USD", _fx("1.25"))
        assert result == _D("160")

    def test_usd_missing_eurusd_returns_none(self):
        assert to_eur(_D("100"), "USD", _fx(eurusd=None)) is None

    def test_usd_zero_price_returns_none(self):
        assert to_eur(_D("0"), "USD", _fx()) is None

    def test_usd_negative_price_returns_none(self):
        assert to_eur(_D("-50"), "USD", _fx()) is None


# ── EUR → EUR (no conversion) ─────────────────────────────────────────────────

class TestToEurEur:

    def test_eur_returned_as_is(self):
        result = to_eur(_D("150"), "EUR", _fx())
        assert result == _D("150")

    def test_eur_does_not_need_eurusd(self):
        # Even with EURUSD missing, EUR passes through
        result = to_eur(_D("200"), "EUR", _fx(eurusd=None))
        assert result == _D("200")

    def test_eur_zero_returns_none(self):
        assert to_eur(_D("0"), "EUR", _fx()) is None


# ── CHF → EUR ─────────────────────────────────────────────────────────────────

class TestToEurChf:

    def test_chf_conversion(self):
        # 100 CHF * 1.0 CHFUSD / 1.1 EURUSD = ~90.909 EUR
        result = to_eur(_D("100"), "CHF", _fx("1.1", chfusd="1.0"))
        assert result is not None
        assert abs(result - _D("90.909")) < _D("0.001")

    def test_chf_missing_chfusd_returns_none(self):
        assert to_eur(_D("100"), "CHF", _fx("1.1", chfusd=None)) is None

    def test_chf_missing_eurusd_returns_none(self):
        assert to_eur(_D("100"), "CHF", _fx(eurusd=None, chfusd="1.0")) is None


# ── GBP → EUR ─────────────────────────────────────────────────────────────────

class TestToEurGbp:

    def test_gbp_conversion(self):
        # 100 GBP * 1.25 GBPUSD / 1.1 EURUSD = ~113.636 EUR
        result = to_eur(_D("100"), "GBP", _fx("1.1", gbpusd="1.25"))
        assert result is not None
        assert abs(result - _D("113.636")) < _D("0.001")

    def test_gbp_missing_gbpusd_returns_none(self):
        assert to_eur(_D("100"), "GBP", _fx("1.1", gbpusd=None)) is None

    def test_gbp_missing_eurusd_returns_none(self):
        assert to_eur(_D("100"), "GBP", _fx(eurusd=None, gbpusd="1.25")) is None


# ── GBp (pence) → EUR ─────────────────────────────────────────────────────────

class TestToEurGbpPence:

    def test_gbp_pence_divides_by_100(self):
        # 10000 GBp = 100 GBP; 100 GBP * 1.25 GBPUSD / 1.1 EURUSD ≈ 113.636 EUR
        result = to_eur(_D("10000"), "GBp", _fx("1.1", gbpusd="1.25"))
        assert result is not None
        assert abs(result - _D("113.636")) < _D("0.001")

    def test_gbx_alias_same_as_gbp_pence(self):
        r_gbp = to_eur(_D("10000"), "GBp", _fx("1.1", gbpusd="1.25"))
        r_gbx = to_eur(_D("10000"), "GBX", _fx("1.1", gbpusd="1.25"))
        assert r_gbp == r_gbx

    def test_gbp_pence_result_equals_gbp_divided_by_100(self):
        # 100 GBp should equal 1 GBP after division
        pence = to_eur(_D("100"), "GBp", _fx("1.1", gbpusd="1.25"))
        whole = to_eur(_D("1"),   "GBP", _fx("1.1", gbpusd="1.25"))
        assert pence is not None and whole is not None
        assert abs(pence - whole) < _D("0.0001")

    def test_gbp_pence_missing_gbpusd_returns_none(self):
        assert to_eur(_D("10000"), "GBp", _fx("1.1", gbpusd=None)) is None


# ── Edge cases ────────────────────────────────────────────────────────────────

class TestToEurEdge:

    def test_none_price_returns_none(self):
        assert to_eur(None, "USD", _fx()) is None

    def test_unknown_currency_returns_none(self):
        assert to_eur(_D("100"), "SEK", _fx()) is None

    def test_none_currency_returns_none(self):
        assert to_eur(_D("100"), None, _fx()) is None

    def test_empty_fx_dict_usd_returns_none(self):
        assert to_eur(_D("100"), "USD", {}) is None

    def test_empty_fx_dict_eur_passthrough(self):
        # EUR never needs FX rates
        assert to_eur(_D("100"), "EUR", {}) == _D("100")

    def test_nonstarter_cad_returns_none(self):
        assert to_eur(_D("100"), "CAD", _fx()) is None
