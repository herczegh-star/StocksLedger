"""Ledger Store: SQLite append-only databáze. Žádné UPDATE / DELETE."""
import sqlite3
from datetime import datetime
from decimal import Decimal
from typing import List, Optional, Tuple
from core.model import RawRow


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
        fp = row.fingerprint()
        try:
            self.conn.execute(
                """INSERT INTO ledger
                   (id, timestamp, type, asset, amount, currency, price, venue, note, row_fp, imported_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    row.id or "",
                    row.timestamp.isoformat(),
                    row.type,
                    row.asset.upper(),
                    str(row.amount),
                    row.currency.upper(),
                    str(row.price) if row.price is not None else None,
                    row.venue.lower(),
                    row.note,
                    fp,
                    datetime.now().isoformat(),
                ),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def import_rows(self, rows: List[RawRow]) -> dict:
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

    def insert_pair(self, row_a: RawRow, row_b: RawRow) -> Tuple[bool, bool]:
        """Vloží dva řádky v jedné atomické transakci (double-entry BUY/SELL)."""
        fp_a = row_a.fingerprint()
        fp_b = row_b.fingerprint()
        now = datetime.now().isoformat()
        results = [False, False]
        with self.conn:
            try:
                self.conn.execute(
                    """INSERT INTO ledger
                       (id, timestamp, type, asset, amount, currency, price, venue, note, row_fp, imported_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (row_a.id or "", row_a.timestamp.isoformat(), row_a.type,
                     row_a.asset.upper(), str(row_a.amount), row_a.currency.upper(),
                     str(row_a.price) if row_a.price is not None else None,
                     row_a.venue.lower(), row_a.note, fp_a, now),
                )
                results[0] = True
            except sqlite3.IntegrityError:
                pass
            try:
                self.conn.execute(
                    """INSERT INTO ledger
                       (id, timestamp, type, asset, amount, currency, price, venue, note, row_fp, imported_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (row_b.id or "", row_b.timestamp.isoformat(), row_b.type,
                     row_b.asset.upper(), str(row_b.amount), row_b.currency.upper(),
                     str(row_b.price) if row_b.price is not None else None,
                     row_b.venue.lower(), row_b.note, fp_b, now),
                )
                results[1] = True
            except sqlite3.IntegrityError:
                pass
        return tuple(results)

    def recent_rows(self, limit: int = 50) -> List[dict]:
        rows = self.conn.execute(
            "SELECT pk, id, timestamp, type, asset, amount, currency, venue FROM ledger ORDER BY pk DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def count(self) -> int:
        return self.conn.execute("SELECT COUNT(*) FROM ledger").fetchone()[0]

    def close(self):
        self.conn.close()
