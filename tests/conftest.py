"""Shared pytest fixtures pro StocksLedger testy."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest

from core.ledger_store import LedgerStore
from core.model import RawRow


@pytest.fixture
def tmp_db(tmp_path) -> str:
    """In-memory SQLite DB cesta v tmp adresáři."""
    db_path = str(tmp_path / "test_ledger.db")
    store = LedgerStore(db_path)
    store.close()
    return db_path


@pytest.fixture
def sample_buy_row() -> RawRow:
    return RawRow(
        timestamp=datetime(2024, 3, 15, 10, 30, 0),
        type="BUY",
        asset="AAPL",
        amount=Decimal("10"),
        currency="USD",
        price=Decimal("175.50"),
        venue="xtb",
        note="Test nákup",
    )


@pytest.fixture
def sample_sell_row() -> RawRow:
    return RawRow(
        timestamp=datetime(2024, 6, 1, 14, 0, 0),
        type="SELL",
        asset="AAPL",
        amount=Decimal("-5"),
        currency="USD",
        price=Decimal("190.00"),
        venue="xtb",
    )
