"""LEDGER_TAX export — RAW ledger řádky do CSV pro samostatnou aplikaci LEDGER_TAX.

Žádná daňová logika (FIFO, CZK, časový test, zisky): jeden ledger row = jeden
CSV row, hodnoty přesně jak jsou uložené v DB. Jediný odvozený sloupec je
reversed_by, počítaný nad celým ledgerem (storno může ležet až po Date to).

Rozsah: od nejstaršího záznamu do konce dne Date to včetně
(timestamp < Date to + 1 den 00:00:00).
"""
from __future__ import annotations

import csv
import os
from datetime import date, datetime, time, timedelta
from typing import Dict, List, Optional, Tuple

from core.ledger_store import LedgerStore
from core.model import RawRow
from core.services.holdings_engine import reversed_trade_ids

LEDGER_TAX_COLUMNS = (
    "ledger_pk",
    "trade_id",
    "timestamp",
    "type",
    "asset",
    "amount",
    "currency",
    "price",
    "venue",
    "note",
    "row_fp",
    "imported_at",
    "reversed_by",
)


def compute_reversed_by(rows: List[RawRow]) -> Dict[str, str]:
    """Vrátí {trade_id: id REVERSAL skupiny, která ho stornuje}. Čistá funkce.

    Parsování note používá centralizované reversed_trade_ids (A1).
    """
    result: Dict[str, str] = {}
    for r in rows:
        if r.type != "REVERSAL":
            continue
        for original_id in reversed_trade_ids([r]):
            result.setdefault(original_id, r.id)
    return result


def export_upper_bound(date_to: date) -> datetime:
    """Exkluzivní horní hranice: následující den 00:00:00 (celý Date to je zahrnut)."""
    return datetime.combine(date_to + timedelta(days=1), time.min)


def ledger_tax_filename(date_to: date, now: datetime) -> str:
    return f"stocks_ledger_tax_{date_to.isoformat()}_{now.strftime('%Y%m%d_%H%M%S')}.csv"


def export_ledger_tax(
    db_path: str,
    date_to: date,
    out_dir: str,
    now: Optional[datetime] = None,
) -> Tuple[str, int]:
    """Zapíše LEDGER_TAX CSV do out_dir. Vrátí (cesta k souboru, počet datových řádků).

    Ledger pouze čte. Existující soubor nikdy nepřepíše (FileExistsError).
    """
    store = LedgerStore(db_path)
    try:
        db_rows = store.export_rows(before=export_upper_bound(date_to))
        reversed_by = compute_reversed_by(store.timeline())
    finally:
        store.close()

    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, ledger_tax_filename(date_to, now or datetime.now()))
    with open(path, "x", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(LEDGER_TAX_COLUMNS)
        for r in db_rows:
            writer.writerow([
                r["pk"], r["id"], r["timestamp"], r["type"], r["asset"], r["amount"],
                r["currency"], r["price"], r["venue"], r["note"], r["row_fp"],
                r["imported_at"], reversed_by.get(r["id"], ""),
            ])
    return path, len(db_rows)
