"""Holdings Engine — vypočte aktuální pozice z ledger rows.

Čistě ledger-centric: žádné externí zdroje, žádné zápisy do DB.
Pravda je v datech, nikoli v kódu.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import List

from core.model import RawRow

# Fiat měny — jejich amount legs v BUY/SELL ignorujeme
FIAT = frozenset({"EUR", "USD", "CZK", "GBP", "PLN"})

_HOLDING_TYPES = frozenset({"BUY", "SELL"})
_ZERO = Decimal("0")
_EPSILON = Decimal("1E-8")
_REV_PREFIX = "REVERSAL of "


@dataclass(frozen=True)
class HoldingRaw:
    ticker: str
    quantity: Decimal
    wac: Decimal          # Weighted Average Cost per unit, v quote měně
    cost_basis: Decimal   # quantity × wac
    currency: str         # quote měna (EUR, USD, ...)


def compute_holdings(rows: List[RawRow]) -> List[HoldingRaw]:
    """Vypočte aktuální pozice z ledger rows.

    Algoritmus:
    - Přeskočí stornované trade skupiny (reversed_ids + REVERSAL rows).
    - Zpracuje pouze BUY/SELL rows pro non-FIAT assets (stock tickers).
    - WAC se resetuje při uzavření pozice (qty ≤ 0).
    - Vrátí seřazený list dle ticker, pouze qty > epsilon.
    """
    # Krok 1: najdi stornovaná trade_id z REVERSAL notes
    reversed_ids: set = set()
    for r in rows:
        if r.type == "REVERSAL" and r.note and r.note.startswith(_REV_PREFIX):
            reversed_ids.add(r.note[len(_REV_PREFIX):])

    # Krok 2: chronologický průchod (rows jsou seřazeny ASC z DB)
    # state[ticker] = {"qty": Decimal, "wac": Decimal, "currency": str}
    state: dict = {}

    for r in rows:
        if r.type == "REVERSAL":
            continue
        if r.id in reversed_ids:
            continue
        if r.asset in FIAT:
            continue
        if r.type not in _HOLDING_TYPES:
            continue

        ticker = r.asset
        if ticker not in state:
            state[ticker] = {"qty": _ZERO, "wac": _ZERO, "currency": r.currency}

        s = state[ticker]

        if r.amount > _ZERO:
            # BUY: aktualizace WAC (Weighted Average Cost)
            price = r.price if (r.price and r.price > _ZERO) else _ZERO
            old_qty = s["qty"]
            new_qty = old_qty + r.amount
            if price > _ZERO and new_qty > _ZERO:
                s["wac"] = (s["wac"] * old_qty + price * r.amount) / new_qty
            s["qty"] = new_qty
            s["currency"] = r.currency  # aktualizuj quote měnu
        else:
            # SELL: qty klesá, WAC se nemění
            new_qty = s["qty"] + r.amount  # r.amount < 0
            if new_qty <= _EPSILON:
                s["qty"] = _ZERO
                s["wac"] = _ZERO  # pozice uzavřena → WAC reset
            else:
                s["qty"] = new_qty

    # Krok 3: sestavení výsledku — seřazeno dle ticker
    result: List[HoldingRaw] = []
    for ticker in sorted(state.keys()):
        s = state[ticker]
        qty = s["qty"]
        wac = s["wac"]
        if qty > _EPSILON:
            result.append(HoldingRaw(
                ticker=ticker,
                quantity=qty.normalize(),
                wac=wac.normalize() if wac else _ZERO,
                cost_basis=(qty * wac).normalize(),
                currency=s["currency"],
            ))

    return result
