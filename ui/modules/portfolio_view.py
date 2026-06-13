"""Portfolio View — investorský dashboard StocksLedger M3.

Zobrazuje aktuální akciové pozice odvozené z ledgeru.
Ceny a P&L se načítají na pozadí (yfinance → EUR přes EURUSD kurz).
"""
from __future__ import annotations

import logging
import threading
import time
from decimal import Decimal
from typing import Dict, List, Optional

import flet as ft

logger = logging.getLogger(__name__)

from core.services.ui_facade import PortfolioSnapshotDTO, PositionDTO, get_portfolio_snapshot

BG_CARD  = "#0f1621"
BORDER   = "#1e293b"
T_PRI    = "#e2e8f0"
T_MUT    = "#7b8799"
BLUE     = "#1d4ed8"
BLUE_300 = "#93c5fd"
GREEN    = "#22c55e"
RED      = "#ef4444"

_SORT_FIELDS = [
    ("roi",  "ROI %"),
    ("pnl",  "P&L"),
    ("val",  "Value"),
    ("name", "Name"),
]


# ── Formátovací helpers ───────────────────────────────────────────────────────

def _fmt_qty(v: Decimal) -> str:
    try:
        f = float(v)
        if f == int(f):
            return str(int(f))
        return f"{f:.4f}".rstrip("0")
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


def _fmt_pnl(v: Optional[Decimal], currency: str = "EUR") -> str:
    """Formátuje P&L s explicitním + prefixem pro kladné hodnoty."""
    if v is None:
        return "—"
    try:
        f = float(v)
        prefix = "+" if f > 0 else ""
        if abs(f) >= 10_000:
            return f"{prefix}{f:,.0f} {currency}".replace(",", " ")
        elif abs(f) >= 1:
            return f"{prefix}{f:,.2f} {currency}".replace(",", " ")
        else:
            return f"{prefix}{f:.4f} {currency}"
    except Exception:
        return str(v)


def _fmt_roi(v: Optional[Decimal]) -> str:
    """Formátuje ROI jako procento s explicitním + prefixem."""
    if v is None:
        return "—"
    try:
        pct = float(v) * 100
        prefix = "+" if pct > 0 else ""
        return f"{prefix}{pct:.2f}%"
    except Exception:
        return "—"


def _pnl_color(v: Optional[Decimal]) -> str:
    """Vrátí barvu podle znaménka hodnoty."""
    if v is None:
        return T_MUT
    try:
        f = float(v)
        if f > 0:
            return GREEN
        if f < 0:
            return RED
    except Exception:
        pass
    return T_MUT


def _sort_positions(positions: List[PositionDTO], field: str, asc: bool) -> List[PositionDTO]:
    """Seřadí pozice podle pole; None hodnoty vždy na konci."""
    def _split_sort(key_fn):
        has = [p for p in positions if key_fn(p) is not None]
        nones = [p for p in positions if key_fn(p) is None]
        return sorted(has, key=key_fn, reverse=not asc) + nones

    if field == "name":
        return sorted(positions, key=lambda p: p.ticker, reverse=not asc)
    if field == "val":
        return _split_sort(lambda p: p.position_value)
    if field == "pnl":
        return _split_sort(lambda p: p.unrealized_pnl)
    if field == "roi":
        return _split_sort(lambda p: p.roi)
    return positions


# ── View builder ──────────────────────────────────────────────────────────────

def build_portfolio_view(page: ft.Page, db_path: str) -> tuple:
    """Vrátí (view, refresh_fn). refresh_fn() znovu načte data z ledgeru."""

    # ── State ─────────────────────────────────────────────────────────────────
    _snap: list = [None]
    _sort = {"field": "roi", "asc": False}
    _names: list = [{}]
    _gen: list = [0]   # generační čítač — chrání před stale background updates

    # ── KPI widgets ───────────────────────────────────────────────────────────
    w_val      = ft.Text("—", size=22, weight=ft.FontWeight.BOLD, color=T_MUT)
    w_invested = ft.Text("", size=11, color=T_MUT)   # "Net Invested: X EUR"
    w_cash     = ft.Text("", size=11, color=T_MUT)   # "Free Cash: X EUR"
    w_pnl      = ft.Text("—", size=22, weight=ft.FontWeight.BOLD, color=T_MUT)
    w_roi      = ft.Text("—", size=22, weight=ft.FontWeight.BOLD, color=T_MUT)

    # ── Dynamic regions ───────────────────────────────────────────────────────
    pills_row = ft.Row(spacing=6)
    cards_col = ft.Column(spacing=12, scroll=ft.ScrollMode.AUTO, expand=True)

    # ── KPI box ───────────────────────────────────────────────────────────────
    def _kpi_box(label: str, *widgets: ft.Control) -> ft.Container:
        return ft.Container(
            content=ft.Column(
                [ft.Text(label, size=12, color=T_MUT), *widgets],
                spacing=4, tight=True,
            ),
            expand=True,
            padding=ft.Padding.all(16),
            bgcolor=BG_CARD,
            border=ft.Border.all(1, BORDER),
            border_radius=12,
        )

    # ── KPI update ────────────────────────────────────────────────────────────
    def _update_kpis() -> None:
        s: Optional[PortfolioSnapshotDTO] = _snap[0]
        if s is None:
            return

        deposits_eur = s.net_deposits_by_currency.get("EUR", Decimal("0"))  # CASH_IN − CASH_OUT

        # Portfolio Value = hodnota pozic + čisté vklady (CASH_IN − CASH_OUT)
        if s.portfolio_value is not None:
            total = s.portfolio_value + deposits_eur
            w_val.value = _fmt_price(total, "EUR")
            w_val.color = T_PRI
        elif deposits_eur > Decimal("0"):
            w_val.value = _fmt_price(deposits_eur, "EUR")
            w_val.color = T_MUT
        else:
            w_val.value = "—"
            w_val.color = T_MUT

        # Net Invested (sekundární text)
        currency = s.positions[0].currency if s.positions else "EUR"
        w_invested.value = f"Net Invested: {_fmt_price(s.total_cost_basis, currency)}"

        # Net Deposits (CASH_IN − CASH_OUT, sekundární text pod Portfolio Value)
        if deposits_eur != Decimal("0"):
            w_cash.value = f"Deposits: {_fmt_price(deposits_eur, 'EUR')}"
            w_cash.color = BLUE_300
        else:
            w_cash.value = ""

        # Unrealized P&L
        w_pnl.value = _fmt_pnl(s.total_pnl, "EUR")
        w_pnl.color = _pnl_color(s.total_pnl)

        # ROI % — pouze z investovaných pozic, nezahrnuje cash
        w_roi.value = _fmt_roi(s.total_roi)
        w_roi.color = _pnl_color(s.total_roi)

    # ── Sort pills ────────────────────────────────────────────────────────────
    def _build_pills() -> None:
        def _make_pill(field: str, label: str) -> ft.Container:
            active = _sort["field"] == field

            def _click(_e, f=field):
                if _sort["field"] == f:
                    _sort["asc"] = not _sort["asc"]
                else:
                    _sort["field"] = f
                    _sort["asc"] = False
                _build_pills()
                _build_cards()
                page.update()

            return ft.Container(
                content=ft.Text(
                    label, size=12,
                    color=T_PRI if active else T_MUT,
                    weight=ft.FontWeight.W_600 if active else ft.FontWeight.NORMAL,
                ),
                bgcolor=BLUE if active else "#162030",
                border_radius=20,
                padding=ft.Padding(left=14, top=6, right=14, bottom=6),
                on_click=_click,
                ink=True,
            )

        pills_row.controls = [_make_pill(f, l) for f, l in _SORT_FIELDS]

    # ── Stat cell ─────────────────────────────────────────────────────────────
    def _stat(label: str, value: str) -> ft.Column:
        return ft.Column(
            [ft.Text(label, size=11, color=T_MUT),
             ft.Text(value, size=13, color=T_PRI)],
            spacing=2, tight=True,
        )

    # ── Position card ─────────────────────────────────────────────────────────
    def _make_card(pos: PositionDTO) -> ft.Container:
        currency      = pos.currency
        spot_currency = pos.spot_currency or currency
        company_name  = _names[0].get(pos.ticker)

        # Levá část hlavičky — ticker + název
        if company_name:
            left = ft.Column(
                [
                    ft.Text(company_name, size=12, color=T_MUT),
                    ft.Text(pos.ticker, size=18, weight=ft.FontWeight.BOLD, color=T_PRI),
                ],
                spacing=1, tight=True,
            )
        else:
            left = ft.Text(pos.ticker, size=18, weight=ft.FontWeight.BOLD, color=T_PRI)

        # Pravá část hlavičky — P&L dominantně (zelená/červená/šedá)
        pnl_color = _pnl_color(pos.unrealized_pnl)
        if pos.unrealized_pnl is not None:
            right = ft.Column(
                [
                    ft.Text(
                        _fmt_pnl(pos.unrealized_pnl, spot_currency),
                        size=16, weight=ft.FontWeight.BOLD,
                        color=pnl_color,
                        text_align=ft.TextAlign.RIGHT,
                    ),
                    ft.Text(
                        _fmt_roi(pos.roi),
                        size=13, color=pnl_color,
                        text_align=ft.TextAlign.RIGHT,
                    ),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.END,
                spacing=2, tight=True,
            )
        else:
            right = ft.Text("—", size=14, color=T_MUT)

        header = ft.Row(
            [left, right],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
            vertical_alignment=ft.CrossAxisAlignment.CENTER,
        )

        # Sekundární stats řádek
        stats = ft.Row(
            [
                _stat("Qty",          _fmt_qty(pos.quantity)),
                _stat("Avg Buy",      _fmt_price(pos.wac, currency)),
                _stat("Net Invested", _fmt_price(pos.cost_basis, currency)),
                _stat("Spot",         _fmt_price(pos.spot_price, spot_currency)),
                _stat("Value",        _fmt_price(pos.position_value, spot_currency)),
            ],
            spacing=28,
        )

        # Barevný levý okraj podle P&L
        border_color = pnl_color if pos.unrealized_pnl is not None else BORDER

        return ft.Container(
            content=ft.Column(
                [header, ft.Divider(height=1, color="#1f2a3a"), stats],
                spacing=12,
            ),
            bgcolor=BG_CARD,
            border=ft.Border(
                left=ft.BorderSide(3, border_color),
                top=ft.BorderSide(1, BORDER),
                right=ft.BorderSide(1, BORDER),
                bottom=ft.BorderSide(1, BORDER),
            ),
            border_radius=12,
            padding=16,
        )

    # ── Cards build ───────────────────────────────────────────────────────────
    def _build_cards() -> None:
        s: Optional[PortfolioSnapshotDTO] = _snap[0]
        controls = []

        # Akciové pozice
        if s and s.positions:
            sorted_pos = _sort_positions(s.positions, _sort["field"], _sort["asc"])
            controls.extend([_make_card(p) for p in sorted_pos])
        elif not controls:
            controls.append(ft.Container(
                content=ft.Text("Žádné pozice. Přidej BUY nebo CASH IN transakci.",
                                size=14, color=T_MUT),
                padding=ft.Padding.all(32),
            ))

        controls.append(ft.Container(height=32))
        cards_col.controls = controls

    # ── Background price + name loading ──────────────────────────────────────
    def _load_prices(snap: PortfolioSnapshotDTO, gen: int) -> None:
        """Načte USD ceny + EUR/USD kurz, vypočte spot_eur, P&L a ROI. Aktualizuje UI."""
        from core.services.ticker_meta import fetch_names, save_names

        tickers = [p.ticker for p in snap.positions]

        try:
            from core.services.price_provider import fetch_fx_rates, fetch_prices_with_currency, to_eur
            from core.services.ui_facade import enrich_with_pnl
            t_p = time.perf_counter()
            prices_raw = fetch_prices_with_currency(tickers)
            logger.debug("prices fetch: %.1f ms (%d tickerů)", (time.perf_counter() - t_p) * 1000, len(tickers))
            t_fx = time.perf_counter()
            fx_rates = fetch_fx_rates()
            logger.debug("fx rates fetch: %.1f ms", (time.perf_counter() - t_fx) * 1000)
        except Exception:
            prices_raw = {}
            fx_rates = {}

        current_names: Dict[str, str] = dict(_names[0])  # already loaded in refresh()
        unknown = [t for t in tickers if t not in current_names]
        if unknown:
            new_names = fetch_names(unknown)
            if new_names:
                current_names.update(new_names)
                save_names(db_path, current_names)

        if not prices_raw and not unknown:
            return

        enriched: List[PositionDTO] = []
        portfolio_value = Decimal("0")

        for pos in snap.positions:
            raw = prices_raw.get(pos.ticker)
            spot_eur = to_eur(raw[0], raw[1], fx_rates) if raw else None
            pos_val  = (pos.quantity * spot_eur) if spot_eur is not None else None

            if pos_val is not None:
                portfolio_value += pos_val

            raw = PositionDTO(
                ticker=pos.ticker,
                quantity=pos.quantity,
                wac=pos.wac,
                cost_basis=pos.cost_basis,
                currency=pos.currency,
                spot_price=spot_eur,
                spot_currency="EUR" if spot_eur is not None else None,
                position_value=pos_val,
            )
            enriched.append(enrich_with_pnl(raw))

        pnl_values = [p.unrealized_pnl for p in enriched if p.unrealized_pnl is not None]
        total_pnl  = sum(pnl_values) if pnl_values else None

        eur_cost  = sum(
            p.cost_basis for p in enriched
            if p.currency == "EUR" and p.unrealized_pnl is not None
        )
        total_roi = (total_pnl / eur_cost) if (total_pnl is not None and eur_cost > 0) else None

        enriched_snap = PortfolioSnapshotDTO(
            positions=enriched,
            total_cost_basis=snap.total_cost_basis,
            portfolio_value=portfolio_value if portfolio_value > 0 else None,
            total_pnl=total_pnl,
            total_roi=total_roi,
            cash_by_currency=snap.cash_by_currency,
            net_deposits_by_currency=snap.net_deposits_by_currency,
        )

        async def _ui_update() -> None:
            if _gen[0] != gen:
                return   # stale update — mezitím proběhl refresh (např. delete)
            _names[0] = current_names
            _snap[0] = enriched_snap
            _update_kpis()
            _build_cards()
            page.update()

        page.run_task(_ui_update)

    # ── Refresh ───────────────────────────────────────────────────────────────
    def refresh() -> None:
        from core.services.ticker_meta import load_names

        _gen[0] += 1
        current_gen = _gen[0]

        t0 = time.perf_counter()
        snap = get_portfolio_snapshot(db_path)
        logger.debug("snapshot: %.1f ms", (time.perf_counter() - t0) * 1000)

        _snap[0] = snap
        _names[0] = load_names(db_path)

        t1 = time.perf_counter()
        _update_kpis()
        _build_pills()
        _build_cards()
        page.update()
        logger.debug("UI render: %.1f ms", (time.perf_counter() - t1) * 1000)

        if snap.positions:
            threading.Thread(
                target=lambda: _load_prices(snap, current_gen), daemon=True
            ).start()

    # ── Layout ────────────────────────────────────────────────────────────────
    kpi_row = ft.Row(
        [
            _kpi_box("Portfolio Value", w_val, w_invested, w_cash),
            _kpi_box("Unrealized P&L", w_pnl),
            _kpi_box("ROI",            w_roi),
        ],
        spacing=16,
    )

    view = ft.Column(
        controls=[
            ft.Container(
                content=ft.Column(
                    [
                        kpi_row,
                        ft.Container(height=16),
                        pills_row,
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
