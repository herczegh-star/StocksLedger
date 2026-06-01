"""Trade service: double-entry BUY/SELL rows pro StocksLedger.

Supported types: BUY, SELL (double-entry: asset leg + currency leg).
Ostatní typy (DIVIDEND, FEE, TAX, CASH_IN, CASH_OUT) jsou single-row
a zapisuje je přímo ui_facade.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import FrozenSet, List, Optional

from core.ledger_store import LedgerStore
from core.model import RawRow

# Podporované fiat měny pro akciové obchodování
_FIAT_DEFAULT: FrozenSet[str] = frozenset({"EUR", "CZK", "USD", "GBP", "PLN"})


def generate_canonical_id(
    timestamp: datetime,
    venue: str,
    type_str: str,
    conn,
) -> str:
    """Canonical ID: yyyymmdd_hhmmss_VENUE_TYPE_SEQ."""
    ts_part = timestamp.strftime("%Y%m%d_%H%M%S")
    venue_upper = venue.upper()
    type_upper = type_str.upper()

    row = conn.execute(
        "SELECT COUNT(DISTINCT id) FROM ledger "
        "WHERE timestamp = ? AND venue = ? AND type = ?",
        (timestamp.isoformat(), venue.lower(), type_upper),
    ).fetchone()
    seq = (row[0] if row else 0) + 1
    return f"{ts_part}_{venue_upper}_{type_upper}_{seq:03d}"


@dataclass
class TradeResult:
    rows: List[RawRow]
    inserted: int
    skipped: int


@dataclass
class AddTradeInput:
    """Vstup pro BUY/SELL. Všechna množství jsou kladná; service přiřadí znaménka."""

    type: str                          # "BUY" | "SELL"
    timestamp: datetime
    base_asset: str                    # ticker, např. "AAPL"
    base_amount: Decimal               # počet kusů (kladné)
    quote_currency: str                # měna, např. "EUR"
    quote_amount: Decimal              # celková částka v měně (kladné)
    venue: str
    fee_amount: Optional[Decimal] = field(default=None)
    fee_currency: Optional[str] = field(default=None)
    note: Optional[str] = field(default=None)


def _validate(inp: AddTradeInput, fiat: FrozenSet[str]) -> None:
    if inp.type not in ("BUY", "SELL"):
        raise ValueError(f"type musí být BUY nebo SELL, dostali jsme: {inp.type!r}")
    if inp.base_amount <= 0:
        raise ValueError(f"base_amount musí být > 0, dostali jsme: {inp.base_amount}")
    if inp.quote_amount <= 0:
        raise ValueError(f"quote_amount musí být > 0, dostali jsme: {inp.quote_amount}")
    if inp.fee_amount is not None and inp.fee_amount <= 0:
        raise ValueError(f"fee_amount musí být > 0, dostali jsme: {inp.fee_amount}")
    if not inp.venue:
        raise ValueError("venue nesmí být prázdné")
    q = inp.quote_currency.upper()
    if q not in fiat:
        raise ValueError(
            f"quote_currency {q!r} není v povoleném fiat setu {sorted(fiat)}. "
            "Přidej měnu do _FIAT_DEFAULT v trade_service.py."
        )
    b = inp.base_asset.upper()
    if b in fiat:
        raise ValueError(f"base_asset {b!r} nesmí být fiat měna")


def build_trade_rows(
    inp: AddTradeInput,
    fiat: FrozenSet[str] = _FIAT_DEFAULT,
    trade_id: Optional[str] = None,
) -> List[RawRow]:
    """Pure funkce — žádné side-effects. Vrátí 2 nebo 3 řádky.

    BUY:  asset +amount, currency -amount, fee -amount
    SELL: asset -amount, currency +amount, fee -amount
    """
    _validate(inp, fiat)

    if trade_id is None:
        trade_id = str(uuid.uuid4())

    base = inp.base_asset.upper()
    quote = inp.quote_currency.upper()
    price = inp.quote_amount / inp.base_amount

    if inp.type == "BUY":
        base_sign = inp.base_amount
        quote_sign = -inp.quote_amount
    else:
        base_sign = -inp.base_amount
        quote_sign = inp.quote_amount

    row_base = RawRow(
        id=trade_id,
        timestamp=inp.timestamp,
        type=inp.type,
        asset=base,
        amount=base_sign,
        currency=quote,
        price=price,
        venue=inp.venue.lower(),
        note=inp.note,
    )
    row_quote = RawRow(
        id=trade_id,
        timestamp=inp.timestamp,
        type=inp.type,
        asset=quote,
        amount=quote_sign,
        currency=quote,
        price=Decimal("1"),
        venue=inp.venue.lower(),
        note=inp.note,
    )

    rows: List[RawRow] = [row_base, row_quote]

    if inp.fee_amount is not None:
        fee_asset = (inp.fee_currency or inp.quote_currency).upper()
        row_fee = RawRow(
            id=trade_id,
            timestamp=inp.timestamp,
            type="FEE",
            asset=fee_asset,
            amount=-inp.fee_amount,
            currency=fee_asset,
            price=Decimal("1"),
            venue=inp.venue.lower(),
            note=inp.note,
        )
        rows.append(row_fee)

    return rows


def add_trade(
    db_path: str,
    inp: AddTradeInput,
    fiat: FrozenSet[str] = _FIAT_DEFAULT,
) -> TradeResult:
    store = LedgerStore(db_path)
    try:
        trade_id = generate_canonical_id(inp.timestamp, inp.venue, inp.type, store.conn)
        rows = build_trade_rows(inp, fiat, trade_id=trade_id)
        counts = store.import_rows(rows)
    finally:
        store.close()
    return TradeResult(rows=rows, inserted=counts["inserted"], skipped=counts["skipped"])
