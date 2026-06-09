"""Portfolio View — výchozí obrazovka StocksLedger M2.

Zobrazuje aktuální akciové pozice odvozené z ledgeru.
Ceny (spot, value, P/L, ROI) se načítají volitelně přes yfinance na pozadí.
Názvy společností se načítají z JSON cache a doplňují přes yfinance.
"""
from __future__ import annotations

import threading
from decimal import Decimal
from typing import Dict, List, Optional

import flet as ft

from core.services.ui_facade import PortfolioSnapshotDTO, PositionDTO, get_portfolio_snapshot

BG_CARD = "#0f1621"
BORDER  = "#1e293b"
T_PRI   = "#e2e8f0"
T_MUT   = "#7b8799"
GREEN   = "#16a34a"
RED     = "#ef4444"
BLUE    = "#1d4ed8"

_SORT_FIELDS = [
    ("name", "Name"),
    ("pnl",  "P/L"),
    ("val",  "Value"),
    ("roi",  "ROI"),
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
        if f >= 10_000:
            return f"{f:,.0f} {currency}".replace(",", " ")
        elif f >= 1:
            return f"{f:,.2f} {currency}".replace(",", " ")
        else:
            return f"{f:.4f} {currency}"
    except Exception:
        return str(v)


def _fmt_pnl(v: Optional[Decimal], currency: str = "EUR") -> str:
    if v is None:
        return "—"
    try:
        f = float(v)
        sign = "+" if f >= 0 else ""
        return f"{sign}{f:,.2f} {currency}".replace(",", " ")
    except Exception:
        return str(v)


def _fmt_roi(v: Optional[Decimal]) -> str:
    if v is None:
        return "—"
    try:
        pct = float(v) * 100
        sign = "+" if pct >= 0 else ""
        return f"{sign}{pct:.2f}%"
    except Exception:
        return str(v)


def _pnl_color(v: Optional[Decimal]) -> str:
    if v is None:
        return T_MUT
    return GREEN if float(v) >= 0 else RED


def _sort_positions(positions: List[PositionDTO], field: str, asc: bool) -> List[PositionDTO]:
    _big = Decimal("999999999")
    if field == "name":
        return sorted(positions, key=lambda p: p.ticker, reverse=not asc)
    elif field == "pnl":
        return sorted(positions, key=lambda p: p.unrealized_pnl if p.unrealized_pnl is not None else -_big, reverse=not asc)
    elif field == "val":
        return sorted(positions, key=lambda p: p.value if p.value is not None else Decimal("0"), reverse=not asc)
    elif field == "roi":
        return sorted(positions, key=lambda p: p.roi if p.roi is not None else -_big, reverse=not asc)
    return positions


# ── View builder ──────────────────────────────────────────────────────────────

def build_portfolio_view(page: ft.Page, db_path: str) -> tuple:
    """Vrátí (view, refresh_fn). refresh_fn() znovu načte data z ledgeru."""

    # ── State ─────────────────────────────────────────────────────────────────
    _snap: list = [None]           # PortfolioSnapshotDTO
    _sort = {"field": "name", "asc": True}
    _names: list = [{}]            # Dict[ticker, company_name] — z JSON cache

    # ── KPI widgets ───────────────────────────────────────────────────────────
    w_cost = ft.Text("—", size=22, weight=ft.FontWeight.BOLD, color=T_PRI)
    w_val  = ft.Text("—", size=22, weight=ft.FontWeight.BOLD, color=T_MUT)
    w_pnl  = ft.Text("—", size=22, weight=ft.FontWeight.BOLD, color=T_MUT)
    w_roi  = ft.Text("—", size=22, weight=ft.FontWeight.BOLD, color=T_MUT)

    # ── Dynamic regions ───────────────────────────────────────────────────────
    pills_row = ft.Row(spacing=6)
    cards_col = ft.Column(spacing=12, scroll=ft.ScrollMode.AUTO, expand=True)

    # ── KPI box ───────────────────────────────────────────────────────────────
    def _kpi_box(label: str, widget: ft.Text) -> ft.Container:
        return ft.Container(
            content=ft.Column(
                [ft.Text(label, size=12, color=T_MUT), widget],
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
        currency = s.positions[0].currency if s.positions else "EUR"

        w_cost.value = _fmt_price(s.total_cost_basis, currency)
        w_cost.color = T_PRI

        if s.total_value is not None:
            w_val.value = _fmt_price(s.total_value, currency)
            w_val.color = T_PRI
            w_pnl.value = _fmt_pnl(s.total_pnl, currency)
            w_pnl.color = _pnl_color(s.total_pnl)
            w_roi.value = _fmt_roi(s.total_roi)
            w_roi.color = _pnl_color(s.total_roi)
        else:
            w_val.value = "—"; w_val.color = T_MUT
            w_pnl.value = "—"; w_pnl.color = T_MUT
            w_roi.value = "—"; w_roi.color = T_MUT

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
        currency = pos.currency
        pnl_col = _pnl_color(pos.unrealized_pnl)
        company_name = _names[0].get(pos.ticker)

        # Levá strana: název společnosti (pokud znám) + ticker
        if company_name:
            left = ft.Column(
                [
                    ft.Text(company_name, size=13, color=T_MUT),
                    ft.Text(pos.ticker, size=18, weight=ft.FontWeight.BOLD, color=T_PRI),
                ],
                spacing=1, tight=True,
            )
        else:
            left = ft.Text(pos.ticker, size=18, weight=ft.FontWeight.BOLD, color=T_PRI)

        return ft.Container(
            content=ft.Column(
                [
                    ft.Row(
                        [
                            left,
                            ft.Row(
                                [
                                    ft.Text(
                                        _fmt_pnl(pos.unrealized_pnl, currency),
                                        size=14, weight=ft.FontWeight.W_600, color=pnl_col,
                                    ),
                                    ft.Text(
                                        _fmt_roi(pos.roi),
                                        size=14, weight=ft.FontWeight.W_600, color=pnl_col,
                                    ),
                                ],
                                spacing=12, tight=True,
                            ),
                        ],
                        alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
                    ),
                    ft.Divider(height=1, color="#1f2a3a"),
                    ft.Row(
                        [
                            _stat("Qty",         _fmt_qty(pos.quantity)),
                            _stat("WAC",         _fmt_price(pos.wac, currency)),
                            _stat("Cost Basis",  _fmt_price(pos.cost_basis, currency)),
                            _stat("Spot",        _fmt_price(pos.spot_price, currency)),
                            _stat("Value",       _fmt_price(pos.value, currency)),
                        ],
                        spacing=32,
                    ),
                ],
                spacing=12,
            ),
            bgcolor=BG_CARD,
            border=ft.Border.all(1, BORDER),
            border_radius=12,
            padding=16,
        )

    # ── Cards build ───────────────────────────────────────────────────────────
    def _build_cards() -> None:
        s: Optional[PortfolioSnapshotDTO] = _snap[0]
        if s is None or not s.positions:
            cards_col.controls = [
                ft.Container(
                    content=ft.Text(
                        "Žádné pozice. Přidej první BUY transakci.",
                        size=14, color=T_MUT,
                    ),
                    padding=ft.Padding.all(32),
                )
            ]
            return

        sorted_pos = _sort_positions(s.positions, _sort["field"], _sort["asc"])
        cards_col.controls = [_make_card(p) for p in sorted_pos]
        cards_col.controls.append(ft.Container(height=32))

    # ── Background price + name loading ──────────────────────────────────────
    def _load_prices(snap: PortfolioSnapshotDTO) -> None:
        """Načte ceny z yfinance na pozadí a aktualizuje snapshot.
        Zároveň doplní chybějící názvy společností do JSON cache.
        """
        from core.services.ticker_meta import fetch_names, load_names, save_names

        tickers = [p.ticker for p in snap.positions]

        # ── Ceny ──────────────────────────────────────────────────────────────
        try:
            from core.services.price_provider import fetch_prices
            prices = fetch_prices(tickers)
        except Exception:
            prices = {}

        # ── Názvy — pouze pro tickery dosud neznámé ───────────────────────────
        current_names: Dict[str, str] = load_names(db_path)
        unknown = [t for t in tickers if t not in current_names]
        if unknown:
            new_names = fetch_names(unknown)
            if new_names:
                current_names.update(new_names)
                save_names(db_path, current_names)

        if not prices and not unknown:
            return

        # ── Sestav obohacené PositionDTO (nové instance — thread-safe) ────────
        enriched: List[PositionDTO] = []
        total_value = Decimal("0")
        all_have_price = True

        for pos in snap.positions:
            spot = prices.get(pos.ticker)
            if spot:
                value = pos.quantity * spot
                pnl = value - pos.cost_basis
                roi = pnl / pos.cost_basis if pos.cost_basis > 0 else None
                enriched.append(PositionDTO(
                    ticker=pos.ticker,
                    quantity=pos.quantity,
                    wac=pos.wac,
                    cost_basis=pos.cost_basis,
                    currency=pos.currency,
                    spot_price=spot,
                    value=value,
                    unrealized_pnl=pnl,
                    roi=roi,
                ))
                total_value += value
            else:
                enriched.append(pos)
                all_have_price = False

        if all_have_price and enriched:
            total_pnl = total_value - snap.total_cost_basis
            enriched_snap = PortfolioSnapshotDTO(
                positions=enriched,
                total_cost_basis=snap.total_cost_basis,
                total_value=total_value,
                total_pnl=total_pnl,
                total_roi=total_pnl / snap.total_cost_basis if snap.total_cost_basis > 0 else None,
            )
        else:
            enriched_snap = PortfolioSnapshotDTO(
                positions=enriched if enriched else snap.positions,
                total_cost_basis=snap.total_cost_basis,
            )

        async def _ui_update() -> None:
            _names[0] = current_names
            _snap[0] = enriched_snap
            _update_kpis()
            _build_cards()
            page.update()

        page.run_task(_ui_update)

    # ── Refresh ───────────────────────────────────────────────────────────────
    def refresh() -> None:
        from core.services.ticker_meta import load_names

        snap = get_portfolio_snapshot(db_path)
        _snap[0] = snap
        _names[0] = load_names(db_path)  # okamžitě z JSON cache (fast)

        _update_kpis()
        _build_pills()
        _build_cards()
        page.update()

        if snap.positions:
            threading.Thread(target=lambda: _load_prices(snap), daemon=True).start()

    # ── Layout ────────────────────────────────────────────────────────────────
    kpi_row = ft.Row(
        [
            _kpi_box("Cost Basis",     w_cost),
            _kpi_box("Portfolio Value", w_val),
            _kpi_box("Unrealized P/L", w_pnl),
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
