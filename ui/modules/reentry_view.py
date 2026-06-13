"""Re-entry Watch View — M-RE4a/4b.

Zobrazuje ledger-derived SELL události jako harvest candidate karty,
obohacené o aktuální spot ceny a simulaci rebuye načtenou na pozadí.

Cenové obohacení používá currency-aware konverzi (BUG-014 fix):
USD→EUR přes EURUSD, EUR přímo, CHF přes CHFUSD/EURUSD, GBp/GBP přes GBPUSD/EURUSD.
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

import flet as ft

logger = logging.getLogger(__name__)

from core.services.ui_facade import (
    HarvestCandidateDTO,
    ReEntrySnapshotDTO,
    enrich_candidate,
    get_reentry_snapshot,
)

# ── Barvy — sdílené s portfolio_view ─────────────────────────────────────────
BG_CARD  = "#0f1621"
BORDER   = "#1e293b"
T_PRI    = "#e2e8f0"
T_MUT    = "#7b8799"
BLUE     = "#1d4ed8"
BLUE_300 = "#93c5fd"
GREEN    = "#22c55e"
RED      = "#ef4444"

_ZERO = Decimal("0")


# ── Formátovací helpers (module-level — testable) ─────────────────────────────

def _fmt_date(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def _fmt_qty(v: Optional[Decimal]) -> str:
    if v is None:
        return "—"
    try:
        f = float(v)
        return str(int(f)) if f == int(f) else f"{f:.4f}".rstrip("0")
    except Exception:
        return str(v)


def _fmt_price(v: Optional[Decimal], currency: str = "EUR") -> str:
    if v is None:
        return "—"
    try:
        f = float(v)
        if abs(f) >= 10_000:
            return f"{f:,.0f} {currency}".replace(",", " ")
        elif abs(f) >= 1:
            return f"{f:,.2f} {currency}".replace(",", " ")
        else:
            return f"{f:.4f} {currency}"
    except Exception:
        return str(v)


def _fmt_pct(v: Optional[Decimal]) -> str:
    """Formátuje procento s explicitním + prefixem."""
    if v is None:
        return "—"
    try:
        f = float(v)
        sign = "+" if f > 0 else ""
        return f"{sign}{f:.2f}%"
    except Exception:
        return "—"


def _fmt_shares(v: Optional[Decimal]) -> str:
    """Formátuje počet kusů se znaménkem (pro extra_shares)."""
    if v is None:
        return "—"
    try:
        f = float(v)
        sign = "+" if f > 0 else ""
        return f"{sign}{int(f)} ks" if f == int(f) else f"{sign}{f:.2f} ks"
    except Exception:
        return "—"


# ── Sort helper (pure — testable) ────────────────────────────────────────────

def _sort_candidates(candidates: list) -> list:
    """Sort: alert first (desc eff), then non-alert desc eff, missing eff last."""
    def _key(c: HarvestCandidateDTO):
        if c.is_alert:
            eff = c.efficiency_pct if c.efficiency_pct is not None else _ZERO
            return (0, -eff)
        if c.efficiency_pct is not None:
            return (1, -c.efficiency_pct)
        return (2, _ZERO)
    return sorted(candidates, key=_key)


# ── Status helper (pure — testable) ──────────────────────────────────────────

def _candidate_status(c: HarvestCandidateDTO) -> tuple:
    """Returns (text, color) for a candidate's status indicator.

    States:
      spot_eur is None           → "Awaiting price..."  (T_MUT, italic)
      enriched, efficiency None  → "Price N/A"          (T_MUT)
      is_alert True              → "Re-entry opportunity" (BLUE_300)
      enriched, not alert        → "Not ready  +X.X%"   (T_MUT)
    """
    if c.spot_eur is None:
        return "Awaiting price...", T_MUT
    if c.efficiency_pct is None:
        return "Price N/A", T_MUT
    if c.is_alert:
        return "Re-entry opportunity", BLUE_300
    pct_str = _fmt_pct(c.efficiency_pct)
    return f"Not ready  {pct_str}", T_MUT


# ── View builder ──────────────────────────────────────────────────────────────

def build_reentry_view(page: ft.Page, db_path: str) -> tuple:
    """Vrátí (view, refresh_fn). refresh_fn() znovu načte data z ledgeru."""

    _snap: list = [None]
    _gen:  list = [0]   # generační čítač — ochrana před stale background updates

    # ── Dynamic region ────────────────────────────────────────────────────────
    cards_col = ft.Column(spacing=12, scroll=ft.ScrollMode.AUTO, expand=True)
    w_summary = ft.Text("", size=13, color=T_MUT)

    # ── Stat cell ─────────────────────────────────────────────────────────────
    def _stat(label: str, value: str, color: str = T_PRI) -> ft.Column:
        return ft.Column(
            [
                ft.Text(label, size=11, color=T_MUT),
                ft.Text(value, size=13, color=color),
            ],
            spacing=2, tight=True,
        )

    # ── Candidate card ────────────────────────────────────────────────────────
    def _make_card(c: HarvestCandidateDTO) -> ft.Container:
        ev = c.sell_event
        status_text, status_color = _candidate_status(c)

        header = ft.Row(
            [
                ft.Text(ev.ticker, size=18, weight=ft.FontWeight.BOLD, color=T_PRI),
                ft.Text(
                    status_text, size=12,
                    color=status_color,
                    italic=(c.spot_eur is None),
                ),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        sell_row = ft.Row(
            [
                _stat("Sold",       _fmt_qty(ev.sell_qty) + " ks"),
                _stat("Sell Price", _fmt_price(ev.sell_price, ev.currency)),
                _stat("Released",   _fmt_price(ev.released_eur, "EUR")),
                _stat("Date",       _fmt_date(ev.sell_date)),
                _stat("Venue",      ev.venue),
            ],
            spacing=28,
        )

        sections: list = [header, ft.Divider(height=1, color="#1f2a3a"), sell_row]

        # Enrichment row — only when spot price was loaded
        if c.spot_eur is not None:
            eff_color = GREEN if c.is_alert else T_MUT
            enrich_row = ft.Row(
                [
                    _stat("Spot EUR",    _fmt_price(c.spot_eur, "EUR")),
                    _stat("Rebuy At",    _fmt_price(c.rebuy_price, "EUR")),
                    _stat("Rebuy Qty",   _fmt_qty(c.rebuy_qty)),
                    _stat("Extra",       _fmt_shares(c.extra_shares)),
                    _stat("Efficiency",  _fmt_pct(c.efficiency_pct), color=eff_color),
                ],
                spacing=28,
            )
            sections.append(ft.Divider(height=1, color="#1f2a3a"))
            sections.append(enrich_row)

        left_accent = GREEN if c.is_alert else BORDER
        return ft.Container(
            content=ft.Column(sections, spacing=12),
            bgcolor=BG_CARD,
            border=ft.Border(
                left=ft.BorderSide(3, left_accent),
                top=ft.BorderSide(1, BORDER),
                right=ft.BorderSide(1, BORDER),
                bottom=ft.BorderSide(1, BORDER),
            ),
            border_radius=12,
            padding=16,
        )

    # ── Cards build ───────────────────────────────────────────────────────────
    def _build_cards() -> None:
        s: Optional[ReEntrySnapshotDTO] = _snap[0]
        controls = []

        if s and s.candidates:
            controls.extend(_make_card(c) for c in _sort_candidates(s.candidates))
        else:
            controls.append(ft.Container(
                content=ft.Text(
                    "Žádné prodeje v ledgeru. Přidej SELL transakci pro sledování re-entry.",
                    size=14, color=T_MUT,
                ),
                padding=ft.Padding.all(32),
            ))

        controls.append(ft.Container(height=32))
        cards_col.controls = controls

    # ── Background price loading ──────────────────────────────────────────────
    def _load_prices(snap: ReEntrySnapshotDTO, gen: int) -> None:
        """Fetch EUR spot prices and call enrich_candidate(). Daemon thread."""
        tickers = list({c.sell_event.ticker for c in snap.candidates})
        if not tickers:
            return

        try:
            from core.services.price_provider import fetch_fx_rates, fetch_prices_with_currency, to_eur
            prices_raw = fetch_prices_with_currency(tickers)
            fx_rates   = fetch_fx_rates()
        except Exception as exc:
            logger.warning("Re-entry price fetch failed: %s", exc)
            return

        enriched: List[HarvestCandidateDTO] = []
        for c in snap.candidates:
            raw      = prices_raw.get(c.sell_event.ticker)
            spot_eur = to_eur(raw[0], raw[1], fx_rates) if raw else None
            enriched.append(enrich_candidate(c.sell_event, spot_eur))

        n_alert = sum(1 for c in enriched if c.is_alert)

        enriched_snap = ReEntrySnapshotDTO(
            candidates=enriched,
            threshold_pct=snap.threshold_pct,
        )

        async def _ui_update() -> None:
            if _gen[0] != gen:
                return   # stale — newer refresh already in flight
            _snap[0] = enriched_snap
            n = len(enriched)
            label = "kandidát" if n == 1 else "kandidátů"
            alert_part = f"  ·  {n_alert} alert" if n_alert else ""
            w_summary.value = f"{n} {label}{alert_part}"
            _build_cards()
            page.update()

        page.run_task(_ui_update)

    # ── Refresh ───────────────────────────────────────────────────────────────
    def refresh() -> None:
        _gen[0] += 1
        current_gen = _gen[0]

        snap = get_reentry_snapshot(db_path)
        _snap[0] = snap

        n = len(snap.candidates)
        if n == 0:
            w_summary.value = "Žádné harvest kandidáty."
        else:
            label = "kandidát" if n == 1 else "kandidátů"
            w_summary.value = f"{n} {label}  ·  ceny: načítám..."

        _build_cards()
        page.update()

        if snap.candidates:
            threading.Thread(
                target=lambda: _load_prices(snap, current_gen),
                daemon=True,
            ).start()

    # ── Layout ────────────────────────────────────────────────────────────────
    view = ft.Column(
        controls=[
            ft.Container(
                content=ft.Column(
                    [
                        ft.Row(
                            [
                                ft.Icon(ft.Icons.AUTORENEW, color=BLUE_300, size=18),
                                ft.Text(
                                    "Re-entry Watch", size=15,
                                    weight=ft.FontWeight.W_600, color=T_PRI,
                                ),
                                ft.Container(width=8),
                                w_summary,
                            ],
                            vertical_alignment=ft.CrossAxisAlignment.CENTER,
                            spacing=8,
                        ),
                        ft.Divider(height=1, color=BORDER),
                        ft.Container(height=12),
                        cards_col,
                    ],
                    spacing=0,
                    expand=True,
                ),
                expand=True,
                padding=ft.Padding.all(24),
            ),
        ],
        expand=True,
        spacing=0,
    )

    return view, refresh
