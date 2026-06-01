"""Hlavní Flet UI pro StocksLedger M1.

Struktura:
    AppBar  — název + verze + stav DB
    Tabs    — "Přidat transakci" | "Timeline"
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


# ── Onboarding (první spuštění nebo chybná DB) ────────────────────────────────

def _build_onboarding_view(page: ft.Page, ctx: AppContextDTO, on_ready: callable) -> ft.Control:
    """Jednoduché nastavení — vytvoří DB a spustí hlavní app."""

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

    db_info = ft.Text(
        f"Databáze: {ctx.db_path}",
        size=13,
        color=ft.Colors.GREY_400,
        selectable=True,
    )

    create_btn = ft.ElevatedButton(
        "Vytvořit databázi a spustit",
        icon=ft.Icons.STORAGE,
        on_click=_create,
    )

    return ft.Column(
        controls=[
            ft.Icon(ft.Icons.SHOW_CHART, size=48, color=ft.Colors.BLUE_400),
            ft.Text("StocksLedger", size=28, weight=ft.FontWeight.BOLD),
            ft.Text("První spuštění — databáze nebyla nalezena.", size=14),
            db_info,
            ft.Divider(),
            create_btn,
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
    """Postaví a zobrazí hlavní tabové UI."""
    from ui.modules.add_trade_dialog import build_add_trade_view
    from ui.modules.ledger_view import build_ledger_view

    page.controls.clear()

    # Závislosit — ledger_view refresh vyvolaný po přidání transakce
    ledger_view_ref: list = [None]

    def on_trade_added() -> None:
        # Obnoví timeline po přidání transakce
        if ledger_view_ref[0] is not None:
            # Přepneme na záložku Timeline automaticky (index 1)
            tabs_ref[0].selected_index = 1
            page.update()

    add_view = build_add_trade_view(page, ctx.db_path, on_trade_added)

    def on_refresh() -> None:
        pass  # ledger_view spravuje vlastní refresh

    ledger_view = build_ledger_view(page, ctx.db_path, on_refresh)
    ledger_view_ref[0] = ledger_view

    tabs = ft.Tabs(
        selected_index=0,
        animation_duration=200,
        tabs=[
            ft.Tab(
                text="Přidat transakci",
                icon=ft.Icons.ADD_CIRCLE_OUTLINE,
                content=ft.Container(
                    content=add_view,
                    padding=ft.padding.all(20),
                    expand=True,
                ),
            ),
            ft.Tab(
                text="Timeline",
                icon=ft.Icons.LIST_ALT,
                content=ft.Container(
                    content=ledger_view,
                    padding=ft.padding.all(20),
                    expand=True,
                ),
            ),
        ],
        expand=True,
    )
    tabs_ref = [tabs]

    page.appbar = ft.AppBar(
        leading=ft.Icon(ft.Icons.SHOW_CHART, color=ft.Colors.BLUE_400),
        leading_width=48,
        title=ft.Text("StocksLedger", weight=ft.FontWeight.BOLD),
        center_title=False,
        bgcolor=ft.Colors.SURFACE_VARIANT,
        actions=[
            ft.Text(
                f"v{ctx.version}",
                size=12,
                color=ft.Colors.GREY_400,
            ),
            ft.Container(width=12),
        ],
    )

    page.add(tabs)
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

        ctx = create_app_context()
        logger.info("AppContext: db_state=%s  db_path=%s", ctx.db_state, ctx.db_path)

        if ctx.db_state == "DB_ERROR":
            page.add(ft.Container(
                content=_build_error_view(page, ctx),
                padding=40,
                expand=True,
                alignment=ft.alignment.center,
            ))
            return

        if ctx.db_state == "DB_MISSING":
            container = ft.Container(expand=True, padding=40, alignment=ft.alignment.center)

            def on_ready() -> None:
                # Reload context po vytvoření DB
                new_ctx = create_app_context()
                page.controls.clear()
                page.appbar = None
                _build_main_app(page, new_ctx)

            container.content = _build_onboarding_view(page, ctx, on_ready)
            page.add(container)
            return

        _build_main_app(page, ctx)

    ft.run(main)
