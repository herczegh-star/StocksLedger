"""Ledger Store: SQLite databáze pro ledger řádky."""
import sqlite3
from datetime import datetime
from decimal import Decimal
from typing import List, Optional
from core.model import RawRow

_INSERT_SQL = """INSERT INTO ledger
   (id, timestamp, type, asset, amount, currency, price, venue, note, row_fp, imported_at)
   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"""


class DuplicateRowError(ValueError):
    """Skupina řádků nebyla zapsána, protože některý řádek už v ledgeru existuje (row_fp)."""


def _insert_params(row: RawRow, imported_at: str) -> tuple:
    return (
        row.id or "",
        row.timestamp.isoformat(),
        row.type,
        row.asset.upper(),
        str(row.amount),
        row.currency.upper(),
        str(row.price) if row.price is not None else None,
        row.venue.lower(),
        row.note,
        row.fingerprint(),
        imported_at,
    )


class LedgerStore:
    def __init__(self, db_path: str = "stocks_ledger.db"):
        self.db_path = db_path
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._create_tables()

    def _create_tables(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS ledger (
                pk INTEGER PRIMARY KEY AUTOINCREMENT,
                id TEXT NOT NULL,
                timestamp TEXT NOT NULL,
                type TEXT NOT NULL,
                asset TEXT NOT NULL,
                amount TEXT NOT NULL,
                currency TEXT NOT NULL,
                price TEXT,
                venue TEXT NOT NULL,
                note TEXT,
                row_fp TEXT NOT NULL,
                imported_at TEXT NOT NULL
            )
        """)
        self.conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_row_fp ON ledger(row_fp)
        """)
        self.conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_timestamp ON ledger(timestamp)
        """)
        self.conn.commit()

    def insert(self, row: RawRow) -> bool:
        try:
            self.conn.execute(_INSERT_SQL, _insert_params(row, datetime.now().isoformat()))
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def insert_group(self, rows: List[RawRow]) -> int:
        """Zapíše skupinu řádků jedné ekonomické transakce: všechny, nebo žádný.

        Při kolizi row_fp (nebo jiné IntegrityError) provede rollback celé
        skupiny a vyvolá DuplicateRowError. Vrátí počet zapsaných řádků.
        """
        now = datetime.now().isoformat()
        row = None
        try:
            with self.conn:
                for row in rows:
                    self.conn.execute(_INSERT_SQL, _insert_params(row, now))
        except sqlite3.IntegrityError as exc:
            raise DuplicateRowError(
                "Duplicitní transakce: řádek "
                f"{row.type} {row.asset} {row.amount} {row.currency} ({row.timestamp.isoformat()}) "
                "už v ledgeru existuje. Nic nebylo zapsáno."
            ) from exc
        return len(rows)

    def import_rows(self, rows: List[RawRow]) -> dict:
        """Bulk import po řádcích: duplicity (row_fp) přeskočí a započítá do skipped.

        Není atomický — pro jednu ekonomickou transakci použij insert_group().
        """
        inserted = 0
        skipped = 0
        for row in rows:
            if self.insert(row):
                inserted += 1
            else:
                skipped += 1
        return {"inserted": inserted, "skipped": skipped}

    def _row_to_rawrow(self, r: sqlite3.Row) -> RawRow:
        return RawRow(
            id=r["id"],
            timestamp=datetime.fromisoformat(r["timestamp"]),
            type=r["type"],
            asset=r["asset"],
            amount=Decimal(r["amount"]),
            currency=r["currency"],
            price=Decimal(r["price"]) if r["price"] else None,
            venue=r["venue"],
            note=r["note"],
        )

    def timeline(self) -> List[RawRow]:
        rows = self.conn.execute(
            "SELECT * FROM ledger ORDER BY timestamp ASC, id ASC, row_fp ASC"
        ).fetchall()
        return [self._row_to_rawrow(r) for r in rows]

    def timeline_filtered(
        self,
        venue: Optional[str] = None,
        asset: Optional[str] = None,
        time_from: Optional[datetime] = None,
        time_to: Optional[datetime] = None,
    ) -> List[RawRow]:
        query = "SELECT * FROM ledger WHERE 1=1"
        params: list = []
        if venue:
            query += " AND venue = ?"
            params.append(venue.lower())
        if asset:
            query += " AND asset = ?"
            params.append(asset.upper())
        if time_from:
            query += " AND timestamp >= ?"
            params.append(time_from.isoformat())
        if time_to:
            query += " AND timestamp <= ?"
            params.append(time_to.isoformat())
        query += " ORDER BY timestamp ASC, id ASC, row_fp ASC"
        rows = self.conn.execute(query, params).fetchall()
        return [self._row_to_rawrow(r) for r in rows]

    def get_rows_by_id(self, row_id: str) -> List[RawRow]:
        rows = self.conn.execute(
            "SELECT * FROM ledger WHERE id = ? ORDER BY timestamp ASC, id ASC, row_fp ASC",
            (row_id,),
        ).fetchall()
        return [self._row_to_rawrow(r) for r in rows]

    def get_pks_by_id(self, row_id: str) -> List[int]:
        rows = self.conn.execute(
            "SELECT pk FROM ledger WHERE id = ? ORDER BY pk ASC", (row_id,)
        ).fetchall()
        return [r["pk"] for r in rows]

    def recent_rows(self, limit: int = 50) -> List[dict]:
        rows = self.conn.execute(
            "SELECT pk, id, timestamp, type, asset, amount, currency, venue FROM ledger ORDER BY pk DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def delete_trade(self, trade_id: str) -> int:
        """Smaže všechny řádky se zadaným trade_id. Vrátí počet smazaných řádků."""
        cursor = self.conn.execute("DELETE FROM ledger WHERE id = ?", (trade_id,))
        self.conn.commit()
        return cursor.rowcount

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM ledger").fetchone()[0]

    def close(self):
        self.conn.close()
