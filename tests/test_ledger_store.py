"""Testy pro LedgerStore — append-only SQLite."""
from datetime import datetime
from decimal import Decimal

import pytest

from core.ledger_store import LedgerStore
from core.model import RawRow


def _row(asset="AAPL", amount=Decimal("10"), ttype="BUY",
         ts=None, currency="USD", venue="xtb", **kwargs) -> RawRow:
    return RawRow(
        timestamp=ts or datetime(2024, 1, 15, 10, 0, 0),
        type=ttype,
        asset=asset,
        amount=amount,
        currency=currency,
        price=Decimal("175"),
        venue=venue,
        **kwargs,
    )


class TestInsert:
    def test_insert_returns_true(self, tmp_db):
        store = LedgerStore(tmp_db)
        assert store.insert(_row()) is True
        store.close()

    def test_duplicate_returns_false(self, tmp_db):
        store = LedgerStore(tmp_db)
        row = _row()
        store.insert(row)
        assert store.insert(row) is False
        store.close()

    def test_count_increases(self, tmp_db):
        store = LedgerStore(tmp_db)
        assert store.count() == 0
        store.insert(_row())
        assert store.count() == 1
        store.close()

    def test_asset_uppercase_normalized(self, tmp_db):
        store = LedgerStore(tmp_db)
        store.insert(_row(asset="aapl"))
        rows = store.timeline()
        assert rows[0].asset == "AAPL"
        store.close()

    def test_venue_lowercase_normalized(self, tmp_db):
        store = LedgerStore(tmp_db)
        store.insert(_row(venue="XTB"))
        rows = store.timeline()
        assert rows[0].venue == "xtb"
        store.close()


class TestImportRows:
    def test_import_returns_counts(self, tmp_db):
        store = LedgerStore(tmp_db)
        rows = [_row(amount=Decimal(str(i + 1))) for i in range(3)]
        result = store.import_rows(rows)
        assert result["inserted"] == 3
        assert result["skipped"] == 0
        store.close()

    def test_import_deduplication(self, tmp_db):
        store = LedgerStore(tmp_db)
        row = _row()
        store.import_rows([row, row])  # druhý je duplikát
        result = store.import_rows([row])
        assert result["inserted"] == 0
        assert result["skipped"] == 1
        store.close()


class TestTimeline:
    def test_timeline_ordered_by_timestamp(self, tmp_db):
        store = LedgerStore(tmp_db)
        store.insert(_row(ts=datetime(2024, 3, 1), amount=Decimal("1")))
        store.insert(_row(ts=datetime(2024, 1, 1), amount=Decimal("2")))
        store.insert(_row(ts=datetime(2024, 2, 1), amount=Decimal("3")))
        rows = store.timeline()
        assert rows[0].timestamp.month == 1
        assert rows[1].timestamp.month == 2
        assert rows[2].timestamp.month == 3
        store.close()

    def test_timeline_empty(self, tmp_db):
        store = LedgerStore(tmp_db)
        assert store.timeline() == []
        store.close()


class TestGetRowsById:
    def test_returns_all_rows_with_same_id(self, tmp_db):
        store = LedgerStore(tmp_db)
        trade_id = "test-trade-001"
        r1 = _row(id=trade_id, amount=Decimal("10"))
        r2 = _row(asset="USD", id=trade_id, ttype="BUY", amount=Decimal("-1755"),
                  currency="USD", venue="xtb",
                  ts=datetime(2024, 1, 15, 10, 0, 0))
        store.import_rows([r1, r2])
        found = store.get_rows_by_id(trade_id)
        assert len(found) == 2
        store.close()

    def test_returns_empty_for_unknown_id(self, tmp_db):
        store = LedgerStore(tmp_db)
        assert store.get_rows_by_id("nonexistent") == []
        store.close()


class TestDeduplication:
    def test_same_fingerprint_prevented(self, tmp_db):
        """Dvě transakce se stejnými klíčovými poli → pouze jedna uložena."""
        store = LedgerStore(tmp_db)
        ts = datetime(2024, 1, 15, 10, 0, 0)
        row1 = RawRow(timestamp=ts, type="BUY", asset="AAPL",
                      amount=Decimal("10"), currency="USD",
                      price=Decimal("175"), venue="xtb")
        row2 = RawRow(timestamp=ts, type="BUY", asset="AAPL",
                      amount=Decimal("10"), currency="USD",
                      price=Decimal("175"), venue="xtb")
        store.insert(row1)
        inserted = store.insert(row2)
        assert inserted is False
        assert store.count() == 1
        store.close()

    def test_different_amount_not_dedup(self, tmp_db):
        store = LedgerStore(tmp_db)
        ts = datetime(2024, 1, 15, 10, 0, 0)
        row1 = RawRow(timestamp=ts, type="BUY", asset="AAPL",
                      amount=Decimal("10"), currency="USD",
                      price=Decimal("175"), venue="xtb")
        row2 = RawRow(timestamp=ts, type="BUY", asset="AAPL",
                      amount=Decimal("11"), currency="USD",
                      price=Decimal("175"), venue="xtb")
        store.insert(row1)
        assert store.insert(row2) is True
        store.close()


class TestAppendOnly:
    def test_no_delete_method(self):
        """LedgerStore nemá žádnou delete_all nebo drop metodu."""
        store = LedgerStore(":memory:")
        assert not hasattr(store, "delete_all")
        assert not hasattr(store, "drop_table")
        store.close()
