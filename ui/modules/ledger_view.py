"""Timeline pohled — chronologický seznam všech transakcí s možností smazání."""
from __future__ import annotations

from decimal import Decimal
from typing import Callable, List

import flet as ft

from core.model import RawRow
from core.services.ui_facade import delete_trade, get_ledger_rows

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
    on_after_change: Callable[[], None] = None,
) -> tuple:

    row_count_text  = ft.Text("", size=13, color=ft.Colors.GREY_400)
    status_text     = ft.Text("", size=13)
    table_container = ft.Column(scroll=ft.ScrollMode.AUTO, expand=True)

    def _set_status(msg: str, error: bool = False) -> None:
        status_text.value = msg
        status_text.color = ft.Colors.RED_400 if error else ft.Colors.GREEN_400
        page.update()

    def _confirm_delete(trade_id: str, trade_type: str) -> None:
        """Dvoustupňový dialog pro smazání transakce."""

        def _do_delete(_e) -> None:
            page.pop_dialog()
            result = delete_trade(db_path, trade_id)
            if result.success:
                _set_status("Transakce smazána.")
                refresh()
                if on_after_change:
                    on_after_change()
            else:
                _set_status(result.error_message or "Chyba při mazání.", error=True)

        def _cancel(_e) -> None:
            page.pop_dialog()

        short_id = trade_id[:40] + ("…" if len(trade_id) > 40 else "")

        dlg = ft.AlertDialog(
            modal=True,
            title=ft.Row(
                [
                    ft.Icon(ft.Icons.DELETE_FOREVER, color=ft.Colors.RED_400, size=22),
                    ft.Text("Smazat transakci?", weight=ft.FontWeight.BOLD),
                ],
                spacing=8,
                vertical_alignment=ft.CrossAxisAlignment.CENTER,
            ),
            content=ft.Column(
                [
                    ft.Row(
                        [
                            ft.Container(
                                ft.Text(trade_type, size=12, weight=ft.FontWeight.BOLD,
                                        color=_TYPE_COLORS.get(trade_type, ft.Colors.WHITE)),
                                bgcolor="#1a2030", border_radius=6,
                                padding=ft.Padding(left=10, top=4, right=10, bottom=4),
                            ),
                            ft.Text(short_id, size=11, color=ft.Colors.GREY_400),
                        ],
                        spacing=10,
                        vertical_alignment=ft.CrossAxisAlignment.CENTER,
                    ),
                    ft.Container(
                        content=ft.Row(
                            [
                                ft.Icon(ft.Icons.WARNING_AMBER_ROUNDED,
                                        color=ft.Colors.RED_300, size=18),
                                ft.Text(
                                    "Tato operace je nevratná.\n"
                                    "Všechny řádky tohoto trade_id budou\n"
                                    "trvale smazány z databáze.",
                                    size=13, color=ft.Colors.RED_300,
                                ),
                            ],
                            spacing=10,
                            vertical_alignment=ft.CrossAxisAlignment.START,
                        ),
                        bgcolor="#2d1010",
                        border=ft.Border.all(1, "#7f1d1d"),
                        border_radius=8,
                        padding=12,
                    ),
                ],
                spacing=12,
                tight=True,
            ),
            actions=[
                ft.TextButton("Zrušit", on_click=_cancel),
                ft.ElevatedButton(
                    "Smazat",
                    icon=ft.Icons.DELETE_FOREVER,
                    on_click=_do_delete,
                    style=ft.ButtonStyle(
                        color=ft.Colors.WHITE,
                        bgcolor=ft.Colors.RED_700,
                    ),
                ),
            ],
            actions_alignment=ft.MainAxisAlignment.END,
        )
        page.show_dialog(dlg)

    def _build_table(rows: List[RawRow]) -> ft.Control:
        if not rows:
            return ft.Text("Ledger je prázdný. Přidej první transakci.",
                           color=ft.Colors.GREY_400)

        header_cells = [
            ft.DataColumn(ft.Text("Datum / čas",  size=12, weight=ft.FontWeight.BOLD)),
            ft.DataColumn(ft.Text("Typ",           size=12, weight=ft.FontWeight.BOLD)),
            ft.DataColumn(ft.Text("Asset",         size=12, weight=ft.FontWeight.BOLD)),
            ft.DataColumn(ft.Text("Množství",      size=12, weight=ft.FontWeight.BOLD), numeric=True),
            ft.DataColumn(ft.Text("Měna",          size=12, weight=ft.FontWeight.BOLD)),
            ft.DataColumn(ft.Text("Cena",          size=12, weight=ft.FontWeight.BOLD), numeric=True),
            ft.DataColumn(ft.Text("Venue",         size=12, weight=ft.FontWeight.BOLD)),
            ft.DataColumn(ft.Text("Poznámka",      size=12, weight=ft.FontWeight.BOLD)),
            ft.DataColumn(ft.Text("Trade ID",      size=12, weight=ft.FontWeight.BOLD)),
            ft.DataColumn(ft.Text("Akce",          size=12, weight=ft.FontWeight.BOLD)),
        ]

        data_rows = []
        seen_ids: set = set()

        for row in reversed(rows):
            ttype      = row.type
            color      = _TYPE_COLORS.get(ttype, ft.Colors.WHITE)
            amount_str = _fmt_amount(row.amount)
            price_str  = _fmt_price(row.price)
            ts_str     = row.timestamp.strftime("%Y-%m-%d %H:%M:%S")
            short_id   = (row.id or "")[:28] + ("…" if len(row.id or "") > 28 else "")

            # Tlačítko smazat — zobrazit jen jednou na trade_id
            show_delete = row.id not in seen_ids
            if row.id:
                seen_ids.add(row.id)

            del_btn = ft.PopupMenuButton(
                icon=ft.Icons.MORE_VERT,
                icon_color=ft.Colors.GREY_500,
                icon_size=18,
                items=[
                    ft.PopupMenuItem(
                        icon=ft.Icons.DELETE_OUTLINE,
                        content="Smazat transakci",
                        on_click=lambda _e, tid=row.id, tt=ttype: _confirm_delete(tid, tt),
                    ),
                ],
            ) if show_delete else ft.Text("")

            data_rows.append(ft.DataRow(
                cells=[
                    ft.DataCell(ft.Text(ts_str, size=12)),
                    ft.DataCell(ft.Text(ttype, size=12, color=color,
                                        weight=ft.FontWeight.BOLD)),
                    ft.DataCell(ft.Text(row.asset, size=12)),
                    ft.DataCell(ft.Text(amount_str, size=12,
                                        color=ft.Colors.GREEN_300 if row.amount > 0
                                        else ft.Colors.RED_300)),
                    ft.DataCell(ft.Text(row.currency, size=12)),
                    ft.DataCell(ft.Text(price_str, size=12,
                                        color=ft.Colors.GREY_400)),
                    ft.DataCell(ft.Text(row.venue, size=12)),
                    ft.DataCell(ft.Text(row.note or "", size=11,
                                        color=ft.Colors.GREY_400)),
                    ft.DataCell(ft.Text(
                        short_id, size=11, color=ft.Colors.GREY_500,
                        tooltip=row.id or "",
                    )),
                    ft.DataCell(del_btn),
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
