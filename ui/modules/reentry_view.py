"""Re-entry Watch View — M-RE3.

Zobrazuje ledger-derived SELL události jako harvest candidate karty.
Enrichment spot cenou a výpočet efficiency (is_alert) přijde v M-RE4.
"""
from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional

import flet as ft

logger = logging.getLogger(__name__)

from core.services.ui_facade import HarvestCandidateDTO, ReEntrySnapshotDTO, get_reentry_snapshot

# ── Barvy — sdílené s portfolio_view ─────────────────────────────────────────
BG_CARD  = "#0f1621"
BORDER   = "#1e293b"
T_PRI    = "#e2e8f0"
T_MUT    = "#7b8799"
BLUE     = "#1d4ed8"
BLUE_300 = "#93c5fd"
GREEN    = "#22c55e"
RED      = "#ef4444"


# ── Formátovací helpers ───────────────────────────────────────────────────────

def _fmt_date(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d")


def _fmt_qty(v: Decimal) -> str:
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


# ── View builder ──────────────────────────────────────────────────────────────

def build_reentry_view(page: ft.Page, db_path: str) -> tuple:
    """Vrátí (view, refresh_fn). refresh_fn() znovu načte data z ledgeru."""

    _snap: list = [None]

    # ── Dynamic region ────────────────────────────────────────────────────────
    cards_col  = ft.Column(spacing=12, scroll=ft.ScrollMode.AUTO, expand=True)
    w_summary  = ft.Text("", size=13, color=T_MUT)

    # ── Stat cell ─────────────────────────────────────────────────────────────
    def _stat(label: str, value: str) -> ft.Column:
        return ft.Column(
            [
                ft.Text(label, size=11, color=T_MUT),
                ft.Text(value, size=13, color=T_PRI),
            ],
            spacing=2, tight=True,
        )

    # ── Candidate card ────────────────────────────────────────────────────────
    def _make_card(c: HarvestCandidateDTO) -> ft.Container:
        ev = c.sell_event

        header = ft.Row(
            [
                ft.Text(ev.ticker, size=18, weight=ft.FontWeight.BOLD, color=T_PRI),
                ft.Text("Awaiting price...", size=12, color=T_MUT, italic=True),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        stats = ft.Row(
            [
                _stat("Sold",       _fmt_qty(ev.sell_qty) + " ks"),
                _stat("Sell Price", _fmt_price(ev.sell_price, ev.currency)),
                _stat("Released",   _fmt_price(ev.released_eur, "EUR")),
                _stat("Date",       _fmt_date(ev.sell_date)),
                _stat("Venue",      ev.venue),
            ],
            spacing=28,
        )

        return ft.Container(
            content=ft.Column(
                [header, ft.Divider(height=1, color="#1f2a3a"), stats],
                spacing=12,
            ),
            bgcolor=BG_CARD,
            border=ft.Border(
                left=ft.BorderSide(3, BORDER),   # neutral — no enrichment yet
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
            controls.extend(_make_card(c) for c in s.candidates)
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

    # ── Refresh ───────────────────────────────────────────────────────────────
    def refresh() -> None:
        snap = get_reentry_snapshot(db_path)
        _snap[0] = snap

        n = len(snap.candidates)
        if n == 0:
            w_summary.value = "Žádné harvest kandidáty."
        else:
            label = "kandidát" if n == 1 else "kandidátů"
            w_summary.value = f"{n} {label}  ·  ceny: čekají na načtení"

        _build_cards()
        page.update()

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
