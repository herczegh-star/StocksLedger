"""Add trade dialogy — StocksLedger M2.

Architektura: type selector → specializovaný formulář.
Každý formulář je statický (žádné dynamické přepínání).
Používá vlastní overlay container (ne ft.AlertDialog) kvůli Flet 0.85.2.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Callable, Optional

import flet as ft

from core.services.ui_facade import AddTradeRequestDTO, AddTradeResultDTO, add_trade

_CURRENCIES = ["EUR", "CZK", "USD", "GBP", "PLN"]


# ── Overlay helpers ───────────────────────────────────────────────────────────

def _make_modal(page: ft.Page, content: ft.Control) -> ft.Container:
    """Vytvoří poloprůhledný overlay s centrovaným obsahem."""
    return ft.Container(
        expand=True,
        bgcolor=ft.Colors.with_opacity(0.55, ft.Colors.BLACK),
        alignment=ft.Alignment(0, 0),
        content=content,
    )


def _close_modal(page: ft.Page, modal: ft.Container) -> None:
    if modal in page.overlay:
        page.overlay.remove(modal)
        page.update()


def _show_modal(page: ft.Page, content: ft.Control) -> ft.Container:
    modal = _make_modal(page, content)
    page.overlay.append(modal)
    page.update()
    return modal


def _card(content: ft.Control, width: int = 520) -> ft.Container:
    return ft.Container(
        width=width,
        bgcolor=ft.Colors.SURFACE,
        border_radius=12,
        padding=ft.Padding.all(24),
        shadow=ft.BoxShadow(
            blur_radius=32,
            color=ft.Colors.with_opacity(0.45, ft.Colors.BLACK),
        ),
        content=content,
    )


def _status() -> ft.Text:
    return ft.Text("", size=13)


def _set_st(st: ft.Text, msg: str, error: bool, page: ft.Page) -> None:
    st.value = msg
    st.color = ft.Colors.RED_400 if error else ft.Colors.GREEN_400
    page.update()


def _try_dec(val: str) -> Optional[Decimal]:
    try:
        d = Decimal((val or "").strip().replace(",", ".").replace(" ", ""))
        return d if d > 0 else None
    except (InvalidOperation, AttributeError):
        return None


# ── Type selector ─────────────────────────────────────────────────────────────

def open_add_trade_dialog(
    page: ft.Page,
    db_path: str,
    on_after_add: Callable[[], None],
) -> None:
    """Otevře výběr typu transakce."""
    modal: list = [None]

    def _close(_e=None) -> None:
        _close_modal(page, modal[0])

    def _pick(ttype: str) -> Callable:
        def _handler(_e=None):
            _close()
            _open_form(page, db_path, on_after_add, ttype)
        return _handler

    def _btn(label: str, ttype: str, icon: str, color: str) -> ft.ElevatedButton:
        return ft.ElevatedButton(
            label,
            icon=icon,
            on_click=_pick(ttype),
            style=ft.ButtonStyle(
                bgcolor=ft.Colors.with_opacity(0.12, color),
                color=color,
                padding=ft.Padding.all(16),
                shape=ft.RoundedRectangleBorder(radius=8),
            ),
            width=220,
        )

    card = _card(ft.Column([
        ft.Row([
            ft.Text("Jaký typ transakce?", size=17, weight=ft.FontWeight.BOLD),
            ft.IconButton(ft.Icons.CLOSE, on_click=_close, icon_size=20),
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        ft.Divider(height=1),
        ft.Row([
            _btn("BUY",  "BUY",  ft.Icons.TRENDING_UP,   ft.Colors.GREEN_400),
            _btn("SELL", "SELL", ft.Icons.TRENDING_DOWN,  ft.Colors.RED_400),
        ], spacing=12),
        ft.Row([
            _btn("CASH IN",  "CASH_IN",  ft.Icons.ARROW_DOWNWARD, ft.Colors.BLUE_300),
            _btn("CASH OUT", "CASH_OUT", ft.Icons.ARROW_UPWARD,   ft.Colors.ORANGE_300),
        ], spacing=12),
        ft.Row([
            _btn("DIVIDEND", "DIVIDEND", ft.Icons.PAYMENTS,      ft.Colors.TEAL_300),
            _btn("FEE / TAX","FEE",      ft.Icons.RECEIPT_LONG,  ft.Colors.GREY_400),
        ], spacing=12),
    ], spacing=14, tight=True), width=480)

    modal[0] = _show_modal(page, card)


# ── Dispatcher ────────────────────────────────────────────────────────────────

def _open_form(
    page: ft.Page,
    db_path: str,
    on_after_add: Callable[[], None],
    ttype: str,
) -> None:
    if ttype in {"BUY", "SELL"}:
        _form_buy_sell(page, db_path, on_after_add, ttype)
    elif ttype in {"CASH_IN", "CASH_OUT"}:
        _form_cash(page, db_path, on_after_add, ttype)
    elif ttype == "DIVIDEND":
        _form_dividend(page, db_path, on_after_add)
    else:  # FEE, TAX
        _form_fee_tax(page, db_path, on_after_add, ttype)


# ── BUY / SELL ────────────────────────────────────────────────────────────────

def _form_buy_sell(
    page: ft.Page,
    db_path: str,
    on_after_add: Callable[[], None],
    ttype: str,
) -> None:
    modal: list  = [None]
    recalc       = [False]
    last_field   = ["per_share"]

    ticker_tf = ft.TextField(label="Ticker", hint_text="AAPL, IWDA.IE, VOW3.DE...", width=180)
    qty_tf    = ft.TextField(label="Množství", hint_text="10", width=130,
                             keyboard_type=ft.KeyboardType.NUMBER)
    price_tf  = ft.TextField(label="EUR / ks", hint_text="140.25", width=160,
                             keyboard_type=ft.KeyboardType.NUMBER)
    total_tf  = ft.TextField(label="Celkem EUR", hint_text="1 402.50", width=170,
                             keyboard_type=ft.KeyboardType.NUMBER)
    venue_tf  = ft.TextField(label="Broker / Venue", hint_text="xtb, degiro, ibkr...", width=180)
    date_tf   = ft.TextField(label="Datum a čas",
                             value=datetime.now().strftime("%Y-%m-%d %H:%M:%S"), width=230)
    note_tf   = ft.TextField(label="Poznámka (volitelné)", width=370)
    st        = _status()

    def _close(_e=None): _close_modal(page, modal[0])

    def _recalc_total(_e=None):
        if recalc[0]: return
        last_field[0] = "per_share"
        q = _try_dec(qty_tf.value); p = _try_dec(price_tf.value)
        if q and p:
            recalc[0] = True; total_tf.value = str(round(q * p, 2)); page.update(); recalc[0] = False

    def _recalc_price(_e=None):
        if recalc[0]: return
        last_field[0] = "total"
        q = _try_dec(qty_tf.value); t = _try_dec(total_tf.value)
        if q and t:
            recalc[0] = True; price_tf.value = str(round(t / q, 4)); page.update(); recalc[0] = False

    def _recalc_qty(_e=None):
        if last_field[0] == "per_share": _recalc_total()
        else: _recalc_price()

    price_tf.on_change = _recalc_total
    total_tf.on_change = _recalc_price
    qty_tf.on_change   = _recalc_qty

    def _submit(_e=None):
        ticker = ticker_tf.value.strip().upper()
        venue  = venue_tf.value.strip()
        if not ticker: _set_st(st, "Ticker nesmí být prázdný", True, page); return
        if not venue:  _set_st(st, "Venue nesmí být prázdné", True, page); return
        q = _try_dec(qty_tf.value)
        if not q: _set_st(st, "Zadej množství", True, page); return
        p = _try_dec(price_tf.value); t = _try_dec(total_tf.value)
        if p and not t: t = Decimal(str(round(q * p, 2)))
        if t and not p: p = Decimal(str(round(t / q, 4)))
        if not t: _set_st(st, "Zadej EUR/ks nebo Celkem EUR", True, page); return
        try: ts = datetime.fromisoformat(date_tf.value.strip())
        except ValueError: _set_st(st, "Neplatné datum", True, page); return
        req = AddTradeRequestDTO(type=ttype, timestamp=ts, asset=ticker, amount=q,
                                 currency="EUR", price=p, quote_amount=t,
                                 venue=venue, note=note_tf.value.strip() or None)
        r = add_trade(req, db_path)
        if r.success: _close(); on_after_add()
        else: _set_st(st, r.error_message or "Chyba", True, page)

    color = ft.Colors.GREEN_400 if ttype == "BUY" else ft.Colors.RED_400
    card = _card(ft.Column([
        ft.Row([
            ft.Text(ttype, size=17, weight=ft.FontWeight.BOLD, color=color),
            ft.IconButton(ft.Icons.CLOSE, on_click=_close, icon_size=20),
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        ft.Divider(height=1),
        ft.Row([ticker_tf, qty_tf], spacing=12),
        ft.Row([price_tf, total_tf], spacing=12),
        ft.Row([venue_tf, date_tf], spacing=12),
        note_tf, st,
        ft.Row([
            ft.TextButton("Zrušit", on_click=_close),
            ft.ElevatedButton(ttype, icon=ft.Icons.ADD, on_click=_submit,
                              style=ft.ButtonStyle(bgcolor=color, color=ft.Colors.WHITE)),
        ], alignment=ft.MainAxisAlignment.END),
    ], spacing=12, tight=True))
    modal[0] = _show_modal(page, card)


# ── CASH IN / CASH OUT ────────────────────────────────────────────────────────

def _form_cash(
    page: ft.Page,
    db_path: str,
    on_after_add: Callable[[], None],
    ttype: str,
) -> None:
    modal: list = [None]

    currency_dd = ft.Dropdown(
        label="Měna",
        options=[ft.dropdown.Option(c) for c in _CURRENCIES],
        value="EUR", width=130,
    )
    amount_tf = ft.TextField(label="Částka", hint_text="1 037.94", width=200,
                             keyboard_type=ft.KeyboardType.NUMBER)
    venue_tf  = ft.TextField(label="Broker / Venue", hint_text="xtb, degiro, ibkr...", width=200)
    date_tf   = ft.TextField(label="Datum a čas",
                             value=datetime.now().strftime("%Y-%m-%d %H:%M:%S"), width=230)
    note_tf   = ft.TextField(label="Poznámka (volitelné)", width=370)
    st        = _status()

    def _close(_e=None): _close_modal(page, modal[0])

    def _submit(_e=None):
        venue = venue_tf.value.strip()
        if not venue: _set_st(st, "Venue nesmí být prázdné", True, page); return
        a = _try_dec(amount_tf.value)
        if not a: _set_st(st, "Zadej částku", True, page); return
        try: ts = datetime.fromisoformat(date_tf.value.strip())
        except ValueError: _set_st(st, "Neplatné datum", True, page); return
        cur = currency_dd.value or "EUR"
        req = AddTradeRequestDTO(type=ttype, timestamp=ts, asset=cur, amount=a,
                                 currency=cur, price=None,
                                 venue=venue, note=note_tf.value.strip() or None)
        r = add_trade(req, db_path)
        if r.success: _close(); on_after_add()
        else: _set_st(st, r.error_message or "Chyba", True, page)

    color = ft.Colors.BLUE_300 if ttype == "CASH_IN" else ft.Colors.ORANGE_300
    label = "Vklad na účet" if ttype == "CASH_IN" else "Výběr z účtu"
    card = _card(ft.Column([
        ft.Row([
            ft.Text(label, size=17, weight=ft.FontWeight.BOLD, color=color),
            ft.IconButton(ft.Icons.CLOSE, on_click=_close, icon_size=20),
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        ft.Divider(height=1),
        ft.Row([currency_dd, amount_tf], spacing=12),
        ft.Row([venue_tf, date_tf], spacing=12),
        note_tf, st,
        ft.Row([
            ft.TextButton("Zrušit", on_click=_close),
            ft.ElevatedButton(label, icon=ft.Icons.ADD, on_click=_submit,
                              style=ft.ButtonStyle(bgcolor=color, color=ft.Colors.WHITE)),
        ], alignment=ft.MainAxisAlignment.END),
    ], spacing=12, tight=True))
    modal[0] = _show_modal(page, card)


# ── DIVIDEND ──────────────────────────────────────────────────────────────────

def _form_dividend(
    page: ft.Page,
    db_path: str,
    on_after_add: Callable[[], None],
) -> None:
    modal: list = [None]

    ticker_tf   = ft.TextField(label="Ticker (zdroj dividendy)", hint_text="AAPL", width=180)
    amount_tf   = ft.TextField(label="Částka dividendy", hint_text="12.50", width=160,
                               keyboard_type=ft.KeyboardType.NUMBER)
    currency_dd = ft.Dropdown(label="Měna",
                              options=[ft.dropdown.Option(c) for c in _CURRENCIES],
                              value="EUR", width=120)
    venue_tf    = ft.TextField(label="Broker / Venue", hint_text="xtb, degiro, ibkr...", width=180)
    date_tf     = ft.TextField(label="Datum a čas",
                               value=datetime.now().strftime("%Y-%m-%d %H:%M:%S"), width=230)
    note_tf     = ft.TextField(label="Poznámka (volitelné)", width=370)
    st          = _status()

    def _close(_e=None): _close_modal(page, modal[0])

    def _submit(_e=None):
        ticker = ticker_tf.value.strip().upper()
        venue  = venue_tf.value.strip()
        if not ticker: _set_st(st, "Ticker nesmí být prázdný", True, page); return
        if not venue:  _set_st(st, "Venue nesmí být prázdné", True, page); return
        a = _try_dec(amount_tf.value)
        if not a: _set_st(st, "Zadej částku dividendy", True, page); return
        try: ts = datetime.fromisoformat(date_tf.value.strip())
        except ValueError: _set_st(st, "Neplatné datum", True, page); return
        req = AddTradeRequestDTO(type="DIVIDEND", timestamp=ts, asset=ticker, amount=a,
                                 currency=currency_dd.value or "EUR", price=None,
                                 venue=venue, note=note_tf.value.strip() or None)
        r = add_trade(req, db_path)
        if r.success: _close(); on_after_add()
        else: _set_st(st, r.error_message or "Chyba", True, page)

    card = _card(ft.Column([
        ft.Row([
            ft.Text("DIVIDEND", size=17, weight=ft.FontWeight.BOLD, color=ft.Colors.TEAL_300),
            ft.IconButton(ft.Icons.CLOSE, on_click=_close, icon_size=20),
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        ft.Divider(height=1),
        ft.Row([ticker_tf, amount_tf, currency_dd], spacing=12),
        ft.Row([venue_tf, date_tf], spacing=12),
        note_tf, st,
        ft.Row([
            ft.TextButton("Zrušit", on_click=_close),
            ft.ElevatedButton("Přidat dividendu", icon=ft.Icons.ADD, on_click=_submit),
        ], alignment=ft.MainAxisAlignment.END),
    ], spacing=12, tight=True))
    modal[0] = _show_modal(page, card)


# ── FEE / TAX ─────────────────────────────────────────────────────────────────

def _form_fee_tax(
    page: ft.Page,
    db_path: str,
    on_after_add: Callable[[], None],
    ttype: str,
) -> None:
    modal: list = [None]

    currency_dd = ft.Dropdown(label="Měna",
                              options=[ft.dropdown.Option(c) for c in _CURRENCIES],
                              value="EUR", width=120)
    amount_tf   = ft.TextField(label="Částka", hint_text="2.50", width=160,
                               keyboard_type=ft.KeyboardType.NUMBER)
    venue_tf    = ft.TextField(label="Broker / Venue", hint_text="xtb, degiro, ibkr...", width=200)
    date_tf     = ft.TextField(label="Datum a čas",
                               value=datetime.now().strftime("%Y-%m-%d %H:%M:%S"), width=230)
    note_tf     = ft.TextField(label="Poznámka (volitelné)", width=370)
    st          = _status()

    def _close(_e=None): _close_modal(page, modal[0])

    def _submit(_e=None):
        venue = venue_tf.value.strip()
        if not venue: _set_st(st, "Venue nesmí být prázdné", True, page); return
        a = _try_dec(amount_tf.value)
        if not a: _set_st(st, "Zadej částku", True, page); return
        try: ts = datetime.fromisoformat(date_tf.value.strip())
        except ValueError: _set_st(st, "Neplatné datum", True, page); return
        cur = currency_dd.value or "EUR"
        req = AddTradeRequestDTO(type=ttype, timestamp=ts, asset=cur, amount=a,
                                 currency=cur, price=None,
                                 venue=venue, note=note_tf.value.strip() or None)
        r = add_trade(req, db_path)
        if r.success: _close(); on_after_add()
        else: _set_st(st, r.error_message or "Chyba", True, page)

    label = "Poplatek (FEE)" if ttype == "FEE" else "Daň / Srážka (TAX)"
    card = _card(ft.Column([
        ft.Row([
            ft.Text(label, size=17, weight=ft.FontWeight.BOLD, color=ft.Colors.GREY_400),
            ft.IconButton(ft.Icons.CLOSE, on_click=_close, icon_size=20),
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        ft.Divider(height=1),
        ft.Row([currency_dd, amount_tf], spacing=12),
        ft.Row([venue_tf, date_tf], spacing=12),
        note_tf, st,
        ft.Row([
            ft.TextButton("Zrušit", on_click=_close),
            ft.ElevatedButton("Přidat", icon=ft.Icons.ADD, on_click=_submit),
        ], alignment=ft.MainAxisAlignment.END),
    ], spacing=12, tight=True))
    modal[0] = _show_modal(page, card)
