"""Reversal service: append-only storno transakce podle trade_id.

Storno vytvoří novou skupinu REVERSAL řádků, která přesně neguje původní.
Žádná data se nikdy nemažou ani nemění.

ID formát reversal skupiny: "REV_{original_trade_id}_{8-char-hex}"
"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

from core.ledger_store import LedgerStore
from core.model import RawRow


def build_reversal_rows(
    original_rows: List[RawRow],
    new_trade_id: Optional[str] = None,
) -> List[RawRow]:
    """Pure funkce: postaví reversal řádky pro skupinu původních řádků.

    Args:
        original_rows: Řádky k reversalu (musí být neprázdné, sdílejí .id).
        new_trade_id:  Volitelné ID reversal skupiny.

    Returns:
        List[RawRow] s type="REVERSAL", amount=-original.amount.

    Raises:
        ValueError: Pokud je original_rows prázdný.
    """
    if not original_rows:
        raise ValueError("Cannot build reversals: original_rows is empty.")

    original_trade_id = original_rows[0].id or ""
    if new_trade_id is None:
        short = uuid.uuid4().hex[:8]
        new_trade_id = f"REV_{original_trade_id}_{short}"

    now = datetime.now().replace(microsecond=0)
    reversal_rows: List[RawRow] = []

    for row in original_rows:
        note_parts = [f"REVERSAL of {original_trade_id}"]
        if row.note:
            note_parts.append(row.note)
        note = "; ".join(note_parts)

        reversal_rows.append(RawRow(
            id=new_trade_id,
            timestamp=now,
            type="REVERSAL",
            asset=row.asset,
            amount=-row.amount,
            currency=row.currency,
            price=row.price,
            venue=row.venue,
            note=note,
        ))

    return reversal_rows


def reverse_trade(db_path: str, trade_id: str) -> List[RawRow]:
    """Načte všechny řádky trade_id, postaví a uloží reversal řádky.

    Raises:
        ValueError: Pokud trade_id neexistuje nebo byl již jednou stornován.
    """
    store = LedgerStore(db_path)
    try:
        original_rows = store.get_rows_by_id(trade_id)
        if not original_rows:
            raise ValueError(
                f"Transakce '{trade_id}' nebyla nalezena."
            )
        prefix = f"REV_{trade_id}_"
        existing = store.conn.execute(
            "SELECT COUNT(*) FROM ledger WHERE SUBSTR(id, 1, ?) = ?",
            (len(prefix), prefix),
        ).fetchone()[0]
        if existing > 0:
            raise ValueError(
                f"Transakce '{trade_id}' již byla stornována. "
                "Každou transakci lze stornovat pouze jednou."
            )
        reversal_rows = build_reversal_rows(original_rows)
        store.import_rows(reversal_rows)
        return reversal_rows
    finally:
        store.close()
