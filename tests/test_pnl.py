"""Unit testy pro enrich_with_pnl() — analytická vrstva, bez DB."""
from decimal import Decimal

import pytest

from core.services.ui_facade import PositionDTO, enrich_with_pnl


def _pos(
    currency: str = "EUR",
    cost_basis: str = "100.00",
    spot_currency: str | None = "EUR",
    position_value: str | None = "120.00",
    wac: str = "100.00",
) -> PositionDTO:
    """Pomocná factory — vytvoří minimální PositionDTO pro testy."""
    return PositionDTO(
        ticker="TEST.US",
        quantity=Decimal("1"),
        wac=Decimal(wac),
        cost_basis=Decimal(cost_basis),
        currency=currency,
        spot_price=Decimal(position_value) if position_value else None,
        spot_currency=spot_currency,
        position_value=Decimal(position_value) if position_value else None,
    )


# ── Základní výpočet ──────────────────────────────────────────────────────────

class TestEnrichWithPnlZisk:
    def test_pnl_zisk(self):
        result = enrich_with_pnl(_pos(cost_basis="100.00", position_value="120.00"))
        assert result.unrealized_pnl == Decimal("20.00")

    def test_roi_zisk(self):
        result = enrich_with_pnl(_pos(cost_basis="100.00", position_value="120.00"))
        assert result.roi == Decimal("0.20")

    def test_pnl_ztrata(self):
        result = enrich_with_pnl(_pos(cost_basis="100.00", position_value="80.00"))
        assert result.unrealized_pnl == Decimal("-20.00")

    def test_roi_ztrata(self):
        result = enrich_with_pnl(_pos(cost_basis="100.00", position_value="80.00"))
        assert result.roi == Decimal("-0.20")

    def test_pnl_breakeven(self):
        result = enrich_with_pnl(_pos(cost_basis="134.90", position_value="134.90"))
        assert result.unrealized_pnl == Decimal("0.00")
        assert result.roi == Decimal("0")

    def test_zachova_ostatni_pole(self):
        pos = _pos(cost_basis="100.00", position_value="110.00")
        result = enrich_with_pnl(pos)
        assert result.ticker == pos.ticker
        assert result.quantity == pos.quantity
        assert result.wac == pos.wac
        assert result.cost_basis == pos.cost_basis
        assert result.currency == pos.currency
        assert result.spot_price == pos.spot_price
        assert result.spot_currency == pos.spot_currency
        assert result.position_value == pos.position_value


# ── Guard podmínky — musí vrátit pos beze změny ───────────────────────────────

class TestEnrichWithPnlGuards:
    def test_bez_position_value(self):
        pos = _pos(position_value=None, spot_currency=None)
        result = enrich_with_pnl(pos)
        assert result.unrealized_pnl is None
        assert result.roi is None

    def test_mismatch_meny_usd_vs_eur(self):
        """Starý USD záznam v ledgeru + EUR spot cena — P&L nesmí být počítán."""
        pos = _pos(currency="USD", spot_currency="EUR", position_value="120.00")
        result = enrich_with_pnl(pos)
        assert result.unrealized_pnl is None
        assert result.roi is None

    def test_mismatch_meny_eur_vs_usd(self):
        pos = _pos(currency="EUR", spot_currency="USD", position_value="120.00")
        result = enrich_with_pnl(pos)
        assert result.unrealized_pnl is None
        assert result.roi is None

    def test_nulovy_cost_basis(self):
        """ZeroDivisionError nesmí nastat."""
        pos = _pos(cost_basis="0", position_value="120.00", wac="0")
        result = enrich_with_pnl(pos)
        assert result.unrealized_pnl is None
        assert result.roi is None

    def test_spot_currency_none(self):
        pos = _pos(spot_currency=None, position_value="120.00")
        result = enrich_with_pnl(pos)
        assert result.unrealized_pnl is None
        assert result.roi is None

    def test_vraci_povodny_objekt_pri_guardu(self):
        """Funkce musí vrátit původní pos (nebo ekvivalent), ne nový objekt s None."""
        pos = _pos(position_value=None, spot_currency=None)
        result = enrich_with_pnl(pos)
        assert result is pos  # identita — přesně tentýž objekt


# ── Přesnost Decimal ──────────────────────────────────────────────────────────

class TestEnrichWithPnlDecimal:
    def test_decimal_presnost(self):
        """Výpočet nesmí ztrácet přesnost převodem na float."""
        pos = _pos(cost_basis="134.9000", position_value="134.4700")
        result = enrich_with_pnl(pos)
        expected_pnl = Decimal("134.4700") - Decimal("134.9000")
        assert result.unrealized_pnl == expected_pnl

    def test_velke_hodnoty(self):
        pos = _pos(cost_basis="100000.00", position_value="150000.00")
        result = enrich_with_pnl(pos)
        assert result.unrealized_pnl == Decimal("50000.00")
        assert result.roi == Decimal("0.5")

    def test_male_hodnoty(self):
        pos = _pos(cost_basis="0.01", position_value="0.02")
        result = enrich_with_pnl(pos)
        assert result.unrealized_pnl == Decimal("0.01")
        assert result.roi == Decimal("1")


# ── Různé měny (platné kombinace) ─────────────────────────────────────────────

class TestEnrichWithPnlMeny:
    def test_eur_eur(self):
        pos = _pos(currency="EUR", spot_currency="EUR", position_value="110.00")
        result = enrich_with_pnl(pos)
        assert result.unrealized_pnl is not None

    def test_usd_usd(self):
        """USD/USD pozice — P&L se vypočte (konzistentní měny)."""
        pos = _pos(currency="USD", spot_currency="USD", position_value="110.00")
        result = enrich_with_pnl(pos)
        assert result.unrealized_pnl is not None

    def test_czk_czk(self):
        pos = _pos(currency="CZK", spot_currency="CZK", position_value="110.00")
        result = enrich_with_pnl(pos)
        assert result.unrealized_pnl is not None
