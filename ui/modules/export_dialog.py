"""Export dialog — výběr typu exportu a jeho parametrů.

Typy:
    LEDGER_TAX — RAW ledger řádky do CSV pro aplikaci LEDGER_TAX.
                 Jediný parametr: Date to (od nejstaršího záznamu do konce dne včetně).
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Optional

import flet as ft

from core.services.ui_facade import export_ledger_tax
from ui.modules.add_trade_dialog import _card, _close_modal, _set_st, _show_modal, _status

EXPORT_TYPES = ["LEDGER_TAX"]

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _parse_date_to(val: Optional[str]) -> tuple[Optional[date], Optional[str]]:
    """Povinné datum YYYY-MM-DD: (datum, None) nebo (None, chyba)."""
    text = (val or "").strip()
    if not text:
        return None, "Zadej Date to (YYYY-MM-DD)"
    if not _DATE_RE.match(text):
        return None, "Neplatné datum — použij formát YYYY-MM-DD"
    try:
        return datetime.strptime(text, "%Y-%m-%d").date(), None
    except ValueError:
        return None, f"Neplatné datum: {text}"


def open_export_dialog(page: ft.Page, db_path: str) -> None:
    modal: list = [None]

    type_dd = ft.Dropdown(
        label="Typ exportu",
        options=[ft.dropdown.Option(t) for t in EXPORT_TYPES],
        value="LEDGER_TAX", width=200,
    )
    date_to_tf = ft.TextField(label="Date to", hint_text="2025-12-31", width=200)
    info = ft.Text(
        "Všechny ledger řádky od nejstaršího záznamu do konce zvoleného dne (včetně).",
        size=12, color=ft.Colors.GREY_400,
    )
    st = _status()
    st.selectable = True

    def _close(_e=None): _close_modal(page, modal[0])

    def _submit(_e=None):
        if type_dd.value != "LEDGER_TAX":
            _set_st(st, "Vyber typ exportu", True, page); return
        d, err = _parse_date_to(date_to_tf.value)
        if err: _set_st(st, err, True, page); return
        r = export_ledger_tax(db_path, d)
        if r.success:
            _set_st(st, f"Exportováno {r.n_rows} řádků → {r.path}", False, page)
        else:
            _set_st(st, r.error_message or "Chyba exportu", True, page)

    card = _card(ft.Column([
        ft.Row([
            ft.Text("Export", size=17, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_300),
            ft.IconButton(ft.Icons.CLOSE, on_click=_close, icon_size=20),
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        ft.Divider(height=1),
        ft.Row([type_dd, date_to_tf], spacing=12),
        info, st,
        ft.Row([
            ft.TextButton("Zavřít", on_click=_close),
            ft.ElevatedButton("Exportovat", icon=ft.Icons.DOWNLOAD, on_click=_submit),
        ], alignment=ft.MainAxisAlignment.END),
    ], spacing=12, tight=True))
    modal[0] = _show_modal(page, card)
