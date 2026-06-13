"""Re-entry Watch Engine — M-RE1.

Derives SELL events purely from ledger rows.
No DB writes, no price lookups, no UI dependencies.

Public API:
    compute_sell_events(rows: List[RawRow]) -> List[SellEventRaw]
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional

from core.model import RawRow
from core.services.holdings_engine import FIAT, _REV_PREFIX

_ZERO    = Decimal("0")
_EPSILON = Decimal("1E-8")


@dataclass(frozen=True)
class SellEventRaw:
    """One SELL event derived purely from ledger rows — no prices, no enrichment.

    released_eur resolution (in compute_sell_events):
      - Preferred: exact FIAT cash leg amount (same trade_id, FIAT asset, amount > 0).
      - Fallback:  sell_qty × sell_price  (approximation used when no paired FIAT leg).
    """
    trade_id:     str
    ticker:       str
    sell_qty:     Decimal    # positive — abs(amount) of the ticker leg
    sell_price:   Decimal    # price per unit at sell time (from RawRow.price)
    released_eur: Decimal    # EUR cash released; see note above
    currency:     str        # quote currency of the sell (EUR, USD, ...)
    sell_date:    datetime
    venue:        str


def compute_sell_events(rows: List[RawRow]) -> List[SellEventRaw]:
    """Derive all non-reversed SELL events for non-FIAT assets from ledger rows.

    Algorithm:
      1. Collect reversed trade IDs from REVERSAL notes.
      2. Group all SELL rows by trade_id (pairs ticker leg with FIAT cash leg).
      3. For each non-reversed group: extract SellEventRaw.

    Returns results sorted by sell_date ascending.
    """
    # Step 1: reversed trade IDs
    reversed_ids: set = set()
    for r in rows:
        if r.type == "REVERSAL" and r.note and r.note.startswith(_REV_PREFIX):
            reversed_ids.add(r.note[len(_REV_PREFIX):])

    # Step 2: group SELL rows by trade_id
    sell_groups: Dict[str, List[RawRow]] = {}
    for r in rows:
        if r.type == "SELL":
            sell_groups.setdefault(r.id, []).append(r)

    # Step 3: extract events
    events: List[SellEventRaw] = []

    for trade_id, group in sell_groups.items():
        if trade_id in reversed_ids:
            continue

        ticker_leg: Optional[RawRow] = None
        fiat_leg:   Optional[RawRow] = None

        for r in group:
            if r.asset not in FIAT and r.amount < _ZERO:
                ticker_leg = r
            elif r.asset in FIAT and r.amount > _ZERO:
                fiat_leg = r

        # No ticker leg = FIAT-only or malformed row — skip
        if ticker_leg is None:
            continue

        sell_qty   = abs(ticker_leg.amount).normalize()
        sell_price = (
            ticker_leg.price.normalize()
            if ticker_leg.price and ticker_leg.price > _ZERO
            else _ZERO
        )

        # Prefer exact cash leg; fallback to sell_qty × sell_price
        if fiat_leg is not None and fiat_leg.amount > _EPSILON:
            released_eur = fiat_leg.amount.normalize()
        else:
            released_eur = (sell_qty * sell_price).normalize()

        events.append(SellEventRaw(
            trade_id=trade_id,
            ticker=ticker_leg.asset,
            sell_qty=sell_qty,
            sell_price=sell_price,
            released_eur=released_eur,
            currency=ticker_leg.currency,
            sell_date=ticker_leg.timestamp,
            venue=ticker_leg.venue,
        ))

    events.sort(key=lambda e: e.sell_date)
    return events
