"""Timeline pohled — chronologický seznam všech transakcí s možností smazání."""
from __future__ import annotations

from collections import OrderedDict
from decimal import Decimal
from typing import Callable, List

import flet as ft

from core.model import RawRow
from core.services.ui_facade import delete_trade, get_ledger_rows

_TYPE_COLORS = {
    "BUY":      "#22c55e",
    "SELL":     "#ef4444",
    "DIVIDEND": "#93c5fd",
    "FEE":      "#fdba74",
    "TAX":      "#fb923c",
    "CASH_IN":  "#67e8f9",
    "CASH_OUT": "#f9a8d4",
    "REVERSAL": "#94a3b8",
}

# Barva svislé čáry pro skupiny podle typu
_GROUP_LINE = {
    "BUY":  "#166534",   # tmavě zelená
    "SELL": "#7f1d1d",   # tmavě červená
}
_GROUP_LINE_DEFAULT = "#1e3a5a"  # tmavě modrá pro ostatní

BG_ROW_EVEN = "transparent"
BG_ROW_ODD  = "#0d131c"
BORDER_ROW  = "#131d2b"
T_MUT       = "#7b8799"
T_PRI       = "#e2e8f0"

# Šířky sloupců [px] — musí odpovídat hlavičce i datovým řádkům
_COLS = [
    ("Datum / čas",  165, False),
    ("Typ",           70, False),
    ("Asset",         90, False),
    ("Množství",      95, True),
    ("Měna",          55, False),
    ("Cena",          90, True),
    ("Venue",         55, False),
    ("Poznámka",     110, False),
    ("Trade ID",     200, False),
    ("",              40, False),   # Akce (delete)
]


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


def _cell(content: ft.Control, width: int, right: bool = False) -> ft.Container:
    return ft.Container(
        content=content,
        width=width,
        alignment=ft.Alignment(1, 0) if right else ft.Alignment(-1, 0),
        padding=ft.Padding(left=0 if right else 8, top=0, right=8 if right else 0, bottom=0),
    )


def _header_row() -> ft.Container:
    cells = [
        _cell(
            ft.Text(label, size=11, weight=ft.FontWeight.BOLD, color=T_MUT),
            w,
            right,
        )
        for label, w, right in _COLS
    ]
    return ft.Container(
        content=ft.Row(cells, spacing=0, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        height=36,
        padding=ft.Padding(left=12, top=0, right=0, bottom=0),
        border=ft.Border(bottom=ft.BorderSide(1, "#1e2d42")),
    )


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
        type_color = _TYPE_COLORS.get(trade_type, ft.Colors.WHITE)

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
                                        color=type_color),
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

    def _make_data_row(row: RawRow, row_idx: int) -> ft.Container:
        ttype      = row.type
        type_color = _TYPE_COLORS.get(ttype, T_PRI)
        amount_str = _fmt_amount(row.amount)
        price_str  = _fmt_price(row.price)
        ts_str     = row.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        short_id   = (row.id or "")[:24] + ("…" if len(row.id or "") > 24 else "")

        del_btn = ft.PopupMenuButton(
            icon=ft.Icons.MORE_VERT,
            icon_color=ft.Colors.GREY_600,
            icon_size=16,
            items=[
                ft.PopupMenuItem(
                    icon=ft.Icons.DELETE_OUTLINE,
                    content="Smazat transakci",
                    on_click=lambda _e, tid=row.id, tt=ttype: _confirm_delete(tid, tt),
                ),
            ],
        )

        cells = [
            _cell(ft.Text(ts_str, size=12, color=T_MUT), 165),
            _cell(ft.Text(ttype, size=12, color=type_color, weight=ft.FontWeight.W_600), 70),
            _cell(ft.Text(row.asset, size=12, color=T_PRI), 90),
            _cell(
                ft.Text(
                    amount_str, size=12,
                    color=ft.Colors.GREEN_300 if row.amount > 0 else ft.Colors.RED_300,
                ),
                95, right=True,
            ),
            _cell(ft.Text(row.currency, size=12, color=T_MUT), 55),
            _cell(ft.Text(price_str, size=12, color=T_MUT), 90, right=True),
            _cell(ft.Text(row.venue, size=12, color=T_MUT), 55),
            _cell(ft.Text(row.note or "", size=11, color=T_MUT), 110),
            _cell(
                ft.Text(short_id, size=11, color="#4a5a70", tooltip=row.id or ""),
                200,
            ),
            ft.Container(content=del_btn, width=40),
        ]

        bg = BG_ROW_ODD if row_idx % 2 else BG_ROW_EVEN

        return ft.Container(
            content=ft.Row(cells, spacing=0, vertical_alignment=ft.CrossAxisAlignment.CENTER),
            height=40,
            bgcolor=bg,
            padding=ft.Padding(left=12, top=0, right=0, bottom=0),
            border=ft.Border(bottom=ft.BorderSide(1, BORDER_ROW)),
        )

    def _build_table(rows: List[RawRow]) -> ft.Control:
        if not rows:
            return ft.Text(
                "Ledger je prázdný. Přidej první transakci.",
                color=ft.Colors.GREY_400,
            )

        # Seskup řádky podle trade_id (zachováme pořadí — newest first)
        groups: OrderedDict = OrderedDict()
        for r in reversed(rows):
            key = r.id or ""
            if key not in groups:
                groups[key] = []
            groups[key].append(r)

        result: list = [_header_row()]
        row_idx = 0

        for trade_id, group_rows in groups.items():
            is_multi = len(group_rows) > 1
            first_type = group_rows[0].type if group_rows else ""
            line_color = _GROUP_LINE.get(first_type, _GROUP_LINE_DEFAULT)

            data_rows = []
            for r in group_rows:
                data_rows.append(_make_data_row(r, row_idx))
                row_idx += 1

            if is_multi:
                # Svislá čára vlevo obaluje celou skupinu
                result.append(
                    ft.Container(
                        content=ft.Column(data_rows, spacing=0, tight=True),
                        border=ft.Border(left=ft.BorderSide(3, line_color)),
                    )
                )
            else:
                result.extend(data_rows)

        return ft.Column(result, spacing=0, tight=True)

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
