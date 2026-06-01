"""Testy pro reversal_service."""
from datetime import datetime
from decimal import Decimal

import pytest

from core.ledger_store import LedgerStore
from core.model import RawRow
from core.services.reversal_service import build_reversal_rows, reverse_trade


def _insert_trade(db_path: str, trade_id: str = "trade-001") -> list:
    """Vloží jednoduchý BUY double-entry pár do DB."""
    rows = [
        RawRow(id=trade_id, timestamp=datetime(2024, 1, 15, 10, 0),
               type="BUY", asset="AAPL", amount=Decimal("10"),
               currency="USD", price=Decimal("175"), venue="xtb"),
        RawRow(id=trade_id, timestamp=datetime(2024, 1, 15, 10, 0),
               type="BUY", asset="USD", amount=Decimal("-1750"),
               currency="USD", price=Decimal("1"), venue="xtb"),
    ]
    store = LedgerStore(db_path)
    store.import_rows(rows)
    store.close()
    return rows


class TestBuildReversalRows:
    def test_returns_same_count(self):
        original = [
            RawRow(id="t1", timestamp=datetime(2024, 1, 15),
                   type="BUY", asset="AAPL", amount=Decimal("10"),
                   currency="USD", price=Decimal("175"), venue="xtb"),
        ]
        result = build_reversal_rows(original)
        assert len(result) == 1

    def test_amount_negated(self):
        original = [
            RawRow(id="t1", timestamp=datetime(2024, 1, 15),
                   type="BUY", asset="AAPL", amount=Decimal("10"),
                   currency="USD", price=Decimal("175"), venue="xtb"),
        ]
        result = build_reversal_rows(original)
        assert result[0].amount == Decimal("-10")

    def test_type_is_reversal(self):
        original = [
            RawRow(id="t1", timestamp=datetime(2024, 1, 15),
                   type="BUY", asset="AAPL", amount=Decimal("10"),
                   currency="USD", price=Decimal("175"), venue="xtb"),
        ]
        result = build_reversal_rows(original)
        assert result[0].type == "REVERSAL"

    def test_note_contains_original_id(self):
        original = [
            RawRow(id="t1", timestamp=datetime(2024, 1, 15),
                   type="BUY", asset="AAPL", amount=Decimal("10"),
                   currency="USD", price=Decimal("175"), venue="xtb"),
        ]
        result = build_reversal_rows(original)
        assert "t1" in result[0].note

    def test_all_rows_share_new_id(self):
        original = [
            RawRow(id="t1", timestamp=datetime(2024, 1, 15),
                   type="BUY", asset="AAPL", amount=Decimal("10"),
                   currency="USD", price=Decimal("175"), venue="xtb"),
            RawRow(id="t1", timestamp=datetime(2024, 1, 15),
                   type="BUY", asset="USD", amount=Decimal("-1750"),
                   currency="USD", price=Decimal("1"), venue="xtb"),
        ]
        result = build_reversal_rows(original)
        ids = {r.id for r in result}
        assert len(ids) == 1
        assert all(r.id.startswith("REV_") for r in result)

    def test_empty_rows_raises(self):
        with pytest.raises(ValueError):
            build_reversal_rows([])

    def test_venue_preserved(self):
        original = [
            RawRow(id="t1", timestamp=datetime(2024, 1, 15),
                   type="BUY", asset="AAPL", amount=Decimal("10"),
                   currency="USD", price=Decimal("175"), venue="degiro"),
        ]
        result = build_reversal_rows(original)
        assert result[0].venue == "degiro"


class TestReverseTrade:
    def test_reversal_added_to_db(self, tmp_db):
        _insert_trade(tmp_db)
        rev_rows = reverse_trade(tmp_db, "trade-001")
        assert len(rev_rows) == 2
        store = LedgerStore(tmp_db)
        assert store.count() == 4  # 2 original + 2 reversal
        store.close()

    def test_reversal_amounts_negate_originals(self, tmp_db):
        _insert_trade(tmp_db)
        rev_rows = reverse_trade(tmp_db, "trade-001")
        aapl_rev = next(r for r in rev_rows if r.asset == "AAPL")
        assert aapl_rev.amount == Decimal("-10")

    def test_cannot_reverse_twice(self, tmp_db):
        _insert_trade(tmp_db)
        reverse_trade(tmp_db, "trade-001")
        with pytest.raises(ValueError, match="stornována"):
            reverse_trade(tmp_db, "trade-001")

    def test_reverse_nonexistent_raises(self, tmp_db):
        with pytest.raises(ValueError, match="nalezena"):
            reverse_trade(tmp_db, "nonexistent-id")

    def test_reversal_type_in_db(self, tmp_db):
        _insert_trade(tmp_db)
        reverse_trade(tmp_db, "trade-001")
        store = LedgerStore(tmp_db)
        rows = store.timeline()
        store.close()
        rev_rows = [r for r in rows if r.type == "REVERSAL"]
        assert len(rev_rows) == 2

    def test_single_row_reversal(self, tmp_db):
        """DIVIDEND (single row) lze stornovat."""
        trade_id = "div-001"
        store = LedgerStore(tmp_db)
        store.import_rows([
            RawRow(id=trade_id, timestamp=datetime(2024, 4, 1, 9, 0),
                   type="DIVIDEND", asset="AAPL", amount=Decimal("25.50"),
                   currency="USD", price=Decimal("0"), venue="xtb")
        ])
        store.close()
        rev_rows = reverse_trade(tmp_db, trade_id)
        assert len(rev_rows) == 1
        assert rev_rows[0].amount == Decimal("-25.50")
