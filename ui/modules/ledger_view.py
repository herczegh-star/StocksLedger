"""Timeline pohled — chronologický seznam všech transakcí s možností REVERSAL."""
from __future__ import annotations

from decimal import Decimal
from typing import Callable, List

import flet as ft

from core.model import RawRow
from core.services.ui_facade import get_ledger_rows, reverse_trade

# Barvy podle typu transakce
_TYPE_COLORS = {
    "BUY":      ft.Colors.GREEN_400,
    "SELL":     ft.Colors.RED_400,
    "DIVIDEND": ft.Colors.BLUE_300,
    "FEE":      ft.Colors.ORANGE_300,
    "TAX":      ft.Colors.ORANGE_400,
    "CASH_IN":  ft.Colors.CYAN_300,
    "CASH_OUT": ft.Colors.PINK_300,
    "REVERSAL": ft.Colors.GREY_400,
}

_COL_WIDTHS = {
    "datum":     160,
    "typ":        90,
    "asset":      80,
    "mnozstvi":  120,
    "mena":       60,
    "cena":       90,
    "venue":      90,
    "poznamka":  180,
    "id":        200,
}


def _fmt_amount(amount: Decimal) -> str:
    try:
        v = float(amount)
        return f"{v:+.4f}".rstrip("0").rstrip(".")
    except Exception:
        return str(amount)


def _fmt_price(price) -> str:
    if price is None:
        return ""
    try:
        v = float(price)
        if v == 0:
            return ""
        return f"{v:.4f}".rstrip("0").rstrip(".")
    except Exception:
        return str(price)


def build_ledger_view(
    page: ft.Page,
    db_path: str,
    on_after_reverse: Callable[[], None] = None,
) -> tuple:

    row_count_text = ft.Text("", size=13, color=ft.Colors.GREY_400)
    status_text    = ft.Text("", size=13)
    table_container = ft.Column(scroll=ft.ScrollMode.AUTO, expand=True)

    def _set_status(msg: str, error: bool = False) -> None:
        status_text.value = msg
        status_text.color = ft.Colors.RED_400 if error else ft.Colors.GREEN_400
        page.update()

    def _confirm_reversal(trade_id: str, trade_type: str) -> None:
        """Zobrazí potvrzovací dialog pro REVERSAL."""

        def _do_reversal(_e) -> None:
            page.pop_dialog()
            try:
                rows = reverse_trade(db_path, trade_id)
                _set_status(f"Stornováno ({len(rows)} řádků přidáno).")
                refresh()
                if on_after_reverse:
                    on_after_reverse()
            except ValueError as exc:
                _set_status(str(exc), error=True)

        def _cancel(_e) -> None:
            page.pop_dialog()

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Text("Stornovat transakci?"),
            content=ft.Text(
                f"Typ: {trade_type}\nID: {trade_id}\n\n"
                "Vytvoří se REVERSAL řádky, které přesně negují tuto transakci.\n"
                "Operaci nelze vrátit zpět.",
            ),
            actions=[
                ft.TextButton("Zrušit", on_click=_cancel),
                ft.ElevatedButton(
                    "Stornovat",
                    icon=ft.Icons.UNDO,
                    on_click=_do_reversal,
                    style=ft.ButtonStyle(color=ft.Colors.RED_400),
                ),
            ],
        )
        page.show_dialog(dlg)

    def _build_table(rows: List[RawRow]) -> ft.Control:
        if not rows:
            return ft.Text("Ledger je prázdný. Přidej první transakci.", color=ft.Colors.GREY_400)

        # Hlavička
        header_cells = [
            ft.DataColumn(ft.Text("Datum / čas",   size=12, weight=ft.FontWeight.BOLD)),
            ft.DataColumn(ft.Text("Typ",            size=12, weight=ft.FontWeight.BOLD)),
            ft.DataColumn(ft.Text("Asset",          size=12, weight=ft.FontWeight.BOLD)),
            ft.DataColumn(ft.Text("Množství",       size=12, weight=ft.FontWeight.BOLD), numeric=True),
            ft.DataColumn(ft.Text("Měna",           size=12, weight=ft.FontWeight.BOLD)),
            ft.DataColumn(ft.Text("Cena",           size=12, weight=ft.FontWeight.BOLD), numeric=True),
            ft.DataColumn(ft.Text("Venue",          size=12, weight=ft.FontWeight.BOLD)),
            ft.DataColumn(ft.Text("Poznámka",       size=12, weight=ft.FontWeight.BOLD)),
            ft.DataColumn(ft.Text("Trade ID",       size=12, weight=ft.FontWeight.BOLD)),
            ft.DataColumn(ft.Text("Akce",           size=12, weight=ft.FontWeight.BOLD)),
        ]

        # Řádky (nejnovější nahoře)
        data_rows = []
        seen_ids: set = set()

        for row in reversed(rows):
            ttype  = row.type
            color  = _TYPE_COLORS.get(ttype, ft.Colors.WHITE)
            amount_str = _fmt_amount(row.amount)
            price_str  = _fmt_price(row.price)
            ts_str = row.timestamp.strftime("%Y-%m-%d %H:%M:%S")
            short_id = (row.id or "")[:28] + ("…" if len(row.id or "") > 28 else "")

            # REVERSAL tlačítko — zobrazit jen jednou na trade_id a jen pro non-REVERSAL
            can_reverse = ttype != "REVERSAL" and row.id not in seen_ids
            if row.id:
                seen_ids.add(row.id)

            rev_btn = ft.IconButton(
                icon=ft.Icons.UNDO,
                tooltip="Stornovat tuto transakci",
                icon_color=ft.Colors.ORANGE_300,
                icon_size=18,
                on_click=lambda _e, tid=row.id, tt=ttype: _confirm_reversal(tid, tt),
            ) if can_reverse else ft.Text("")

            data_rows.append(ft.DataRow(
                cells=[
                    ft.DataCell(ft.Text(ts_str, size=12)),
                    ft.DataCell(ft.Text(ttype, size=12, color=color, weight=ft.FontWeight.BOLD)),
                    ft.DataCell(ft.Text(row.asset, size=12)),
                    ft.DataCell(ft.Text(amount_str, size=12,
                                        color=ft.Colors.GREEN_300 if row.amount > 0 else ft.Colors.RED_300)),
                    ft.DataCell(ft.Text(row.currency, size=12)),
                    ft.DataCell(ft.Text(price_str, size=12, color=ft.Colors.GREY_400)),
                    ft.DataCell(ft.Text(row.venue, size=12)),
                    ft.DataCell(ft.Text(row.note or "", size=11, color=ft.Colors.GREY_400)),
                    ft.DataCell(ft.Text(
                        short_id, size=11, color=ft.Colors.GREY_500,
                        tooltip=row.id or "",
                    )),
                    ft.DataCell(rev_btn),
                ],
            ))

        return ft.DataTable(
            columns=header_cells,
            rows=data_rows,
            column_spacing=16,
            horizontal_margin=12,
            data_row_min_height=36,
            data_row_max_height=48,
            heading_row_height=40,
        )

    def refresh() -> None:
        rows = get_ledger_rows(db_path)
        row_count_text.value = f"{len(rows)} řádků celkem"
        table_container.controls.clear()
        table_container.controls.append(_build_table(rows))
        page.update()

    refresh()

    view = ft.Column(
        controls=[
            ft.Text("Timeline", size=20, weight=ft.FontWeight.BOLD),
            ft.Divider(height=8),
            ft.Row(
                controls=[
                    row_count_text,
                    ft.IconButton(
                        ft.Icons.REFRESH,
                        tooltip="Obnovit",
                        icon_size=20,
                        on_click=lambda _e: refresh(),
                    ),
                    status_text,
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            ft.Row(
                controls=[table_container],
                scroll=ft.ScrollMode.AUTO,
                expand=True,
            ),
        ],
        expand=True,
        spacing=4,
    )
    return view, refresh
