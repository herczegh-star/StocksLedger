"""Formulář pro ruční zadání transakce."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Callable, Optional

import flet as ft

from core.services.ui_facade import AddTradeRequestDTO, AddTradeResultDTO, add_trade

_TRADE_TYPES = ["BUY", "SELL", "DIVIDEND", "FEE", "TAX", "CASH_IN", "CASH_OUT"]
_CURRENCIES  = ["EUR", "CZK", "USD", "GBP", "PLN"]

# Typy, které potřebují pole Ticker (asset je stock ticker, ne měna)
_TICKER_TYPES = {"BUY", "SELL", "DIVIDEND"}
# Typy, které zobrazují pole Cena/kus
_PRICE_TYPES  = {"BUY", "SELL"}


def build_add_trade_view(
    page: ft.Page,
    db_path: str,
    on_trade_added: Callable[[], None],
) -> ft.Control:
    """Vrátí ft.Column s formulářem pro přidání transakce."""

    # ── Formulářové prvky ─────────────────────────────────────────────────────

    type_dd = ft.Dropdown(
        label="Typ transakce",
        options=[ft.dropdown.Option(t) for t in _TRADE_TYPES],
        value="BUY",
        width=200,
    )

    date_tf = ft.TextField(
        label="Datum a čas",
        hint_text="2024-01-15 10:30:00",
        value=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        width=230,
    )

    # Asset / Ticker — label se mění podle typu
    asset_tf = ft.TextField(
        label="Ticker (např. AAPL)",
        hint_text="AAPL, VOW3, IWDA...",
        width=180,
    )

    amount_tf = ft.TextField(
        label="Množství / částka",
        hint_text="Kladné číslo",
        width=160,
        keyboard_type=ft.KeyboardType.NUMBER,
    )

    currency_dd = ft.Dropdown(
        label="Měna",
        options=[ft.dropdown.Option(c) for c in _CURRENCIES],
        value="EUR",
        width=120,
    )

    price_tf = ft.TextField(
        label="Cena / kus (volitelné)",
        hint_text="150.25",
        width=190,
        keyboard_type=ft.KeyboardType.NUMBER,
    )

    venue_tf = ft.TextField(
        label="Broker / Venue",
        hint_text="xtb, degiro, ibkr...",
        width=180,
    )

    note_tf = ft.TextField(
        label="Poznámka (volitelné)",
        width=380,
        hint_text="Volitelný komentář",
    )

    status_text = ft.Text("", size=13)

    def _set_status(msg: str, error: bool = False) -> None:
        status_text.value = msg
        status_text.color = ft.Colors.RED_400 if error else ft.Colors.GREEN_400
        page.update()

    def _on_type_change(_e) -> None:
        ttype = type_dd.value or "BUY"
        if ttype in _TICKER_TYPES:
            asset_tf.label = "Ticker (např. AAPL)"
            asset_tf.hint_text = "AAPL, VOW3, IWDA..."
        else:
            asset_tf.label = "Měna / Asset"
            asset_tf.hint_text = "EUR, USD, CZK..."
        price_tf.visible = ttype in _PRICE_TYPES
        status_text.value = ""
        page.update()

    type_dd.on_change = _on_type_change

    def _clear_form() -> None:
        date_tf.value = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        asset_tf.value = ""
        amount_tf.value = ""
        price_tf.value = ""
        note_tf.value = ""
        status_text.value = ""
        page.update()

    def _on_submit(_e) -> None:
        # Parsování data
        try:
            ts = datetime.fromisoformat(date_tf.value.strip())
        except (ValueError, AttributeError):
            _set_status("Neplatné datum — použij formát YYYY-MM-DD HH:MM:SS", error=True)
            return

        # Parsování množství
        try:
            amount = Decimal(amount_tf.value.strip().replace(",", "."))
            if amount <= 0:
                raise ValueError
        except (InvalidOperation, ValueError, AttributeError):
            _set_status("Množství musí být kladné číslo (např. 10 nebo 1500.50)", error=True)
            return

        # Parsování ceny (volitelné)
        price: Optional[Decimal] = None
        if price_tf.visible and price_tf.value and price_tf.value.strip():
            try:
                price = Decimal(price_tf.value.strip().replace(",", "."))
                if price < 0:
                    raise ValueError
            except (InvalidOperation, ValueError):
                _set_status("Neplatná cena — zadej kladné číslo", error=True)
                return

        asset = asset_tf.value.strip()
        venue = venue_tf.value.strip()

        if not asset:
            _set_status("Ticker / asset nesmí být prázdný", error=True)
            return
        if not venue:
            _set_status("Broker / venue nesmí být prázdný", error=True)
            return

        req = AddTradeRequestDTO(
            type=type_dd.value,
            timestamp=ts,
            asset=asset,
            amount=amount,
            currency=currency_dd.value,
            price=price,
            venue=venue,
            note=note_tf.value.strip() or None,
        )

        result: AddTradeResultDTO = add_trade(req, db_path)

        if result.success:
            n = result.n_rows_added
            ttype = type_dd.value
            _set_status(f"OK — {ttype} uložen ({n} {'řádek' if n == 1 else 'řádky' if n <= 4 else 'řádků'})")
            _clear_form()
            on_trade_added()
        else:
            _set_status(result.error_message or "Neznámá chyba", error=True)

    submit_btn = ft.ElevatedButton(
        "Přidat transakci",
        icon=ft.Icons.ADD,
        on_click=_on_submit,
    )

    # ── Layout ────────────────────────────────────────────────────────────────
    return ft.Column(
        controls=[
            ft.Text("Přidat transakci", size=20, weight=ft.FontWeight.BOLD),
            ft.Divider(height=8),
            ft.Row([type_dd, date_tf], wrap=True, spacing=12),
            ft.Row([asset_tf, amount_tf, currency_dd], wrap=True, spacing=12),
            ft.Row([price_tf, venue_tf], wrap=True, spacing=12),
            note_tf,
            ft.Row([submit_btn, status_text], spacing=16, vertical_alignment=ft.CrossAxisAlignment.CENTER),
        ],
        spacing=12,
        scroll=ft.ScrollMode.AUTO,
    )
