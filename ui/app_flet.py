"""Hlavní Flet UI pro StocksLedger M2.

Struktura:
    Header (54px)  — název + Add Transaction + Refresh
    Body:
        Left Nav (88px) | VerticalDivider | Content area

Navigace:
    [0] Portfolio  — výchozí obrazovka, holdings + WAC (ledger-centric)
    [1] Ledger     — chronologická timeline
"""
from __future__ import annotations

import logging

import flet as ft

from core.logging_setup import configure_logging
from core.services.ui_facade import (
    AppContextDTO,
    SimpleResultDTO,
    create_app_context,
    create_db,
    set_db_path,
)

logger = logging.getLogger(__name__)

BG          = "#0b0f14"
BG_HDR      = "#0d1117"
BORDER      = "#1e293b"
T_PRI       = "#e2e8f0"
T_MUT       = "#7b8799"
BLUE_ACTIVE = "#1d4ed8"


# ── Onboarding ────────────────────────────────────────────────────────────────

def _build_onboarding_view(page: ft.Page, ctx: AppContextDTO, on_ready: callable) -> ft.Control:
    status = ft.Text("", size=13)

    def _create(_e) -> None:
        result: SimpleResultDTO = create_db(ctx.db_path)
        if result.success:
            set_db_path(ctx.db_path)
            on_ready()
        else:
            status.value = result.error_message or "Chyba"
            status.color = ft.Colors.RED_400
            page.update()

    return ft.Column(
        controls=[
            ft.Icon(ft.Icons.SHOW_CHART, size=48, color=ft.Colors.BLUE_400),
            ft.Text("StocksLedger", size=28, weight=ft.FontWeight.BOLD),
            ft.Text("První spuštění — databáze nebyla nalezena.", size=14),
            ft.Text(f"Databáze: {ctx.db_path}", size=13,
                    color=ft.Colors.GREY_400, selectable=True),
            ft.Divider(),
            ft.ElevatedButton(
                "Vytvořit databázi a spustit",
                icon=ft.Icons.STORAGE,
                on_click=_create,
            ),
            status,
        ],
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        spacing=16,
    )


def _build_error_view(page: ft.Page, ctx: AppContextDTO) -> ft.Control:
    return ft.Column(
        controls=[
            ft.Icon(ft.Icons.ERROR_OUTLINE, size=48, color=ft.Colors.RED_400),
            ft.Text("Chyba databáze", size=20, weight=ft.FontWeight.BOLD),
            ft.Text(ctx.error or "Neznámá chyba.", color=ft.Colors.RED_400),
            ft.Text(f"Cesta: {ctx.db_path}", size=12, color=ft.Colors.GREY_400),
        ],
        horizontal_alignment=ft.CrossAxisAlignment.CENTER,
        spacing=12,
    )


# ── Hlavní aplikace ───────────────────────────────────────────────────────────

def _build_main_app(page: ft.Page, ctx: AppContextDTO) -> None:
    from ui.modules.add_trade_dialog import open_add_trade_dialog
    from ui.modules.ledger_view import build_ledger_view
    from ui.modules.portfolio_view import build_portfolio_view

    page.controls.clear()
    page.appbar = None
    db_path = ctx.db_path

    # ── Content host ──────────────────────────────────────────────────────────
    _content = ft.Container(expand=True, bgcolor=BG)

    # ── Refresh helpers ───────────────────────────────────────────────────────
    # Closure čte proměnné níže z enclosing scope — přiřazeny před jakoukoli
    # interakcí, takže jsou vždy platné při zavolání. ✓
    _run_portfolio = None
    _run_ledger    = None
    _ledger_view   = None

    def _refresh_all() -> None:
        if _run_portfolio:
            _run_portfolio()
        if _content.content is _ledger_view and _run_ledger:
            _run_ledger()

    # ── Build views ───────────────────────────────────────────────────────────
    _portfolio_view, _run_portfolio = build_portfolio_view(page, db_path)
    _ledger_view,    _run_ledger    = build_ledger_view(
        page, db_path, on_after_change=_refresh_all,
    )

    # ── Nav ───────────────────────────────────────────────────────────────────
    _NAV_ITEMS = [
        (ft.Icons.SHOW_CHART_OUTLINED,  ft.Icons.SHOW_CHART,  "Portfolio"),
        (ft.Icons.LIST_ALT_OUTLINED,    ft.Icons.LIST_ALT,    "Ledger"),
    ]
    _nav_idx = [0]
    _nav_col = ft.Column(spacing=2, tight=True)

    def set_view(idx: int) -> None:
        _nav_idx[0] = idx
        _rebuild_nav_col()
        if idx == 0:
            _content.content = _portfolio_view
            _run_portfolio()
        else:
            _content.content = _ledger_view
            _run_ledger()
        page.update()

    def _build_nav_btn(idx: int) -> ft.Container:
        active = _nav_idx[0] == idx
        icon_off, icon_on, label = _NAV_ITEMS[idx]

        def _click(e, i=idx):
            set_view(i)

        return ft.Container(
            content=ft.Column(
                [
                    ft.Icon(
                        icon_on if active else icon_off,
                        color=T_PRI if active else T_MUT,
                        size=20,
                    ),
                    ft.Text(
                        label, size=9,
                        color=T_PRI if active else T_MUT,
                        text_align=ft.TextAlign.CENTER,
                        width=72,
                    ),
                ],
                horizontal_alignment=ft.CrossAxisAlignment.CENTER,
                spacing=3,
                tight=True,
            ),
            padding=ft.Padding.all(10),
            bgcolor=BLUE_ACTIVE + "55" if active else "transparent",
            border_radius=8,
            on_click=_click,
            ink=True,
            width=80,
        )

    def _rebuild_nav_col() -> None:
        _nav_col.controls = [_build_nav_btn(i) for i in range(len(_NAV_ITEMS))]

    _rebuild_nav_col()

    # ── Header ────────────────────────────────────────────────────────────────
    def _on_add_trade(e) -> None:
        open_add_trade_dialog(page, db_path, _refresh_all)

    def _on_refresh(e) -> None:
        if _content.content is _portfolio_view:
            _run_portfolio()
        else:
            _run_ledger()
        page.update()

    _header = ft.Container(
        content=ft.Row(
            [
                ft.Row(
                    [
                        ft.Icon(ft.Icons.SHOW_CHART, color=ft.Colors.BLUE_400, size=22),
                        ft.Text(
                            "StocksLedger", size=17,
                            weight=ft.FontWeight.BOLD, color=T_PRI,
                        ),
                        ft.Text(f"v{ctx.version}", size=11, color=T_MUT),
                    ],
                    spacing=8,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER,
                ),
                ft.Row(
                    [
                        ft.ElevatedButton(
                            "Add Transaction",
                            icon=ft.Icons.ADD,
                            on_click=_on_add_trade,
                            style=ft.ButtonStyle(
                                color=ft.Colors.WHITE,
                                bgcolor=ft.Colors.GREEN_700,
                            ),
                        ),
                        ft.TextButton(
                            "Refresh",
                            on_click=_on_refresh,
                            style=ft.ButtonStyle(color=T_MUT),
                        ),
                    ],
                    spacing=4,
                ),
            ],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        ),
        bgcolor=BG_HDR,
        padding=ft.Padding.all(12),
        border=ft.Border.only(bottom=ft.BorderSide(1, BORDER)),
    )

    # ── Body ──────────────────────────────────────────────────────────────────
    _nav_host = ft.Container(
        width=88,
        bgcolor=BG_HDR,
        content=ft.Column(
            [ft.Container(height=8), _nav_col],
            spacing=0,
            tight=True,
        ),
        padding=0,
    )

    _body_row = ft.Row(
        [_nav_host, ft.VerticalDivider(width=1, color=BORDER), _content],
        expand=True,
        spacing=0,
        vertical_alignment=ft.CrossAxisAlignment.STRETCH,
    )

    page.add(ft.Column([_header, _body_row], spacing=0, expand=True))

    # ── Výchozí obrazovka: Portfolio ─────────────────────────────────────────
    _content.content = _portfolio_view
    _run_portfolio()
    page.update()


# ── Entry point ───────────────────────────────────────────────────────────────

def run_ui() -> None:
    configure_logging()

    def main(page: ft.Page) -> None:
        page.title = "StocksLedger"
        page.theme_mode = ft.ThemeMode.DARK
        page.window.width = 1200
        page.window.height = 780
        page.window.min_width = 900
        page.window.min_height = 600
        page.padding = 0
        page.bgcolor = BG

        ctx = create_app_context()
        logger.info("AppContext: db_state=%s  db_path=%s", ctx.db_state, ctx.db_path)

        if ctx.db_state == "DB_ERROR":
            page.add(ft.Container(
                content=_build_error_view(page, ctx),
                padding=ft.Padding.all(40),
                expand=True,
                alignment=ft.Alignment(0, 0),
            ))
            return

        if ctx.db_state == "DB_MISSING":
            container = ft.Container(
                expand=True,
                padding=ft.Padding.all(40),
                alignment=ft.Alignment(0, 0),
            )

            def on_ready() -> None:
                new_ctx = create_app_context()
                page.controls.clear()
                page.appbar = None
                _build_main_app(page, new_ctx)

            container.content = _build_onboarding_view(page, ctx, on_ready)
            page.add(container)
            return

        _build_main_app(page, ctx)

    ft.run(main)
