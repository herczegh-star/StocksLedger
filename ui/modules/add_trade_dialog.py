"""Modální dialog pro ruční zadání transakce."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Callable, Optional

import flet as ft

from core.services.ui_facade import AddTradeRequestDTO, AddTradeResultDTO, add_trade

_TRADE_TYPES  = ["BUY", "SELL", "DIVIDEND", "FEE", "TAX", "CASH_IN", "CASH_OUT"]
_CURRENCIES   = ["EUR", "CZK", "USD", "GBP", "PLN"]
_TICKER_TYPES = {"BUY", "SELL", "DIVIDEND"}
_PRICE_TYPES  = {"BUY", "SELL"}
_CASH_TYPES   = {"CASH_IN", "CASH_OUT"}


def open_add_trade_dialog(
    page: ft.Page,
    db_path: str,
    on_after_add: Callable[[], None],
) -> None:
    """Otevře modální dialog pro přidání transakce."""

    # ── State ─────────────────────────────────────────────────────────────────
    _recalculating   = [False]          # guard proti smyčce při programatickém plnění polí
    _last_price_field = ["per_share"]   # "per_share" | "total" — zdroj posledního ručního zadání

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

    asset_tf = ft.TextField(
        label="Ticker (např. ANET.US)",
        hint_text="ANET.US, VOW3.DE, IWDA.IE...",
        width=180,
    )

    amount_tf = ft.TextField(
        label="Množství",
        hint_text="Kladné číslo",
        width=140,
        keyboard_type=ft.KeyboardType.NUMBER,
    )

    # EUR-centric pole — zobrazena pouze pro BUY/SELL (přes controls list)
    eur_per_share_tf = ft.TextField(
        label="EUR / ks",
        hint_text="140.25",
        width=165,
        keyboard_type=ft.KeyboardType.NUMBER,
    )

    eur_total_tf = ft.TextField(
        label="Celkem EUR",
        hint_text="2 805.00",
        width=175,
        keyboard_type=ft.KeyboardType.NUMBER,
    )

    # Měna — zobrazena pouze pro DIVIDEND/FEE/TAX
    currency_dd = ft.Dropdown(
        label="Měna",
        options=[ft.dropdown.Option(c) for c in _CURRENCIES],
        value="EUR",
        width=120,
    )

    # Dynamické řádky — controls list se přestavuje podle typu transakce
    row_asset  = ft.Row(spacing=12)   # asset_tf + amount_tf [+ currency_dd]
    row_prices = ft.Row(spacing=12)   # eur_per_share_tf + eur_total_tf (jen BUY/SELL)

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

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _set_status(msg: str, error: bool = False) -> None:
        status_text.value = msg
        status_text.color = ft.Colors.RED_400 if error else ft.Colors.GREEN_400
        page.update()

    def _try_decimal(val: str) -> Optional[Decimal]:
        try:
            d = Decimal((val or "").strip().replace(",", "."))
            return d if d > 0 else None
        except (InvalidOperation, AttributeError):
            return None

    # ── Auto-výpočet EUR polí ─────────────────────────────────────────────────

    def _recalc_total() -> None:
        qty       = _try_decimal(amount_tf.value or "")
        per_share = _try_decimal(eur_per_share_tf.value or "")
        if qty and per_share:
            _recalculating[0] = True
            eur_total_tf.value = str(round(qty * per_share, 2))
            page.update()
            _recalculating[0] = False

    def _recalc_per_share() -> None:
        qty   = _try_decimal(amount_tf.value or "")
        total = _try_decimal(eur_total_tf.value or "")
        if qty and total:
            _recalculating[0] = True
            eur_per_share_tf.value = str(round(total / qty, 4))
            page.update()
            _recalculating[0] = False

    def _on_per_share_change(_e) -> None:
        if _recalculating[0]:
            return
        _last_price_field[0] = "per_share"
        _recalc_total()

    def _on_total_change(_e) -> None:
        if _recalculating[0]:
            return
        _last_price_field[0] = "total"
        _recalc_per_share()

    def _on_qty_change(_e) -> None:
        if type_dd.value not in _PRICE_TYPES:
            return
        if _last_price_field[0] == "per_share":
            _recalc_total()
        else:
            _recalc_per_share()

    eur_per_share_tf.on_change = _on_per_share_change
    eur_total_tf.on_change     = _on_total_change
    amount_tf.on_change        = _on_qty_change

    # ── Viditelnost polí při změně typu ──────────────────────────────────────

    def _on_type_change(_e) -> None:
        ttype       = (getattr(_e, "data", None) or type_dd.value or "BUY")
        is_buy_sell = ttype in _PRICE_TYPES
        is_cash     = ttype in _CASH_TYPES

        if ttype in _TICKER_TYPES:
            asset_tf.label     = "Ticker (např. ANET.US)"
            asset_tf.hint_text = "ANET.US, VOW3.DE, IWDA.IE..."
        elif is_cash:
            asset_tf.label     = "Měna"
            asset_tf.hint_text = "EUR"
            if not asset_tf.value:
                asset_tf.value = "EUR"
        else:
            asset_tf.label     = "Měna / Asset"
            asset_tf.hint_text = "EUR, USD, CZK..."

        amount_tf.label     = "Částka"   if is_cash else "Množství"
        amount_tf.hint_text = "1 037.94" if is_cash else "Kladné číslo"

        # Přestav controls list — spolehlivější než widget.visible v Flet 0.85 dialozích
        row_asset.controls  = [asset_tf, amount_tf] + ([currency_dd] if (not is_buy_sell and not is_cash) else [])
        row_prices.controls = [eur_per_share_tf, eur_total_tf] if is_buy_sell else []

        status_text.value = ""
        if _e is not None:
            page.update()

    type_dd.on_change = _on_type_change
    _on_type_change(None)  # nastav počáteční stav řádků před show_dialog

    # ── Zavření ───────────────────────────────────────────────────────────────

    def _close(_e=None) -> None:
        page.pop_dialog()

    # ── Submit ────────────────────────────────────────────────────────────────

    def _on_submit(_e) -> None:
        ttype       = type_dd.value or "BUY"
        is_buy_sell = ttype in _PRICE_TYPES

        try:
            ts = datetime.fromisoformat(date_tf.value.strip())
        except (ValueError, AttributeError):
            _set_status("Neplatné datum — použij formát YYYY-MM-DD HH:MM:SS", error=True)
            return

        try:
            amount = Decimal(amount_tf.value.strip().replace(",", "."))
            if amount <= 0:
                raise ValueError
        except (InvalidOperation, ValueError, AttributeError):
            _set_status("Množství musí být kladné číslo (např. 10 nebo 1500.50)", error=True)
            return

        asset = asset_tf.value.strip()
        venue = venue_tf.value.strip()

        if not asset:
            _set_status("Ticker / asset nesmí být prázdný", error=True)
            return
        if not venue:
            _set_status("Broker / venue nesmí být prázdný", error=True)
            return

        if is_buy_sell:
            # EUR-centric BUY/SELL — currency vždy EUR
            eur_per_share = _try_decimal(eur_per_share_tf.value or "")
            eur_total     = _try_decimal(eur_total_tf.value or "")

            # Dopočet chybějícího pole při submitu
            if eur_per_share and not eur_total:
                eur_total = Decimal(str(round(amount * eur_per_share, 2)))
            elif eur_total and not eur_per_share:
                eur_per_share = Decimal(str(round(eur_total / amount, 4)))

            if not eur_total or eur_total <= 0:
                _set_status("Zadej EUR / ks nebo Celkem EUR", error=True)
                return

            req = AddTradeRequestDTO(
                type=ttype,
                timestamp=ts,
                asset=asset,
                amount=amount,
                currency="EUR",
                price=eur_per_share,
                quote_amount=eur_total,
                venue=venue,
                note=note_tf.value.strip() or None,
            )

        else:
            # DIVIDEND, FEE, TAX: currency z dropdownu
            # CASH_IN, CASH_OUT: currency = asset (obě jsou stejná měna)
            currency = asset if ttype in _CASH_TYPES else currency_dd.value
            req = AddTradeRequestDTO(
                type=ttype,
                timestamp=ts,
                asset=asset,
                amount=amount,
                currency=currency,
                price=None,
                venue=venue,
                note=note_tf.value.strip() or None,
            )

        result: AddTradeResultDTO = add_trade(req, db_path)

        if result.success:
            page.pop_dialog()
            on_after_add()
        else:
            _set_status(result.error_message or "Neznámá chyba", error=True)

    # ── Layout ────────────────────────────────────────────────────────────────

    dlg = ft.AlertDialog(
        modal=True,
        title=ft.Text("Přidat transakci", weight=ft.FontWeight.BOLD, size=18),
        content=ft.Container(
            width=580,
            content=ft.Column(
                controls=[
                    ft.Row([type_dd, date_tf], spacing=12),
                    row_asset,
                    row_prices,
                    venue_tf,
                    note_tf,
                    status_text,
                ],
                spacing=12,
                scroll=ft.ScrollMode.AUTO,
                tight=True,
            ),
        ),
        actions=[
            ft.TextButton("Zrušit", on_click=_close),
            ft.ElevatedButton(
                "Přidat transakci",
                icon=ft.Icons.ADD,
                on_click=_on_submit,
            ),
        ],
        actions_alignment=ft.MainAxisAlignment.END,
    )
    page.show_dialog(dlg)
