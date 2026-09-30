"""Regresní testy pro generate_canonical_id() — pořadové číslo po DELETE.

Bug: SEQ se počítal jako COUNT(DISTINCT id) + 1, takže po smazání transakce
mohlo vzniknout ID, které už patří jiné existující transakci, a dvě
ekonomické transakce se sloučily pod jedno id.
"""
from datetime import datetime
from decimal import Decimal

import pytest

from core.ledger_store import LedgerStore
from core.services.trade_service import AddTradeInput, build_trade_rows, generate_canonical_id

_TS = datetime(2026, 1, 5, 10, 0, 0)
_PREFIX = "20260105_100000_XTB_BUY_"


@pytest.fixture
def store():
    s = LedgerStore(":memory:")
    yield s
    s.close()


def _add(store: LedgerStore, qty: str, *, venue: str = "xtb", type_: str = "BUY",
         ts: datetime = _TS) -> str:
    """Zapíše obchod stejně jako trade_service.add_trade a vrátí jeho id."""
    trade_id = generate_canonical_id(ts, venue, type_, store.conn)
    inp = AddTradeInput(type_, ts, "AAPL", Decimal(qty), "EUR", Decimal(qty) * 100, venue)
    store.import_rows(build_trade_rows(inp, trade_id=trade_id))
    return trade_id


def _all_ids(store: LedgerStore) -> set:
    return {r[0] for r in store.conn.execute("SELECT DISTINCT id FROM ledger")}


class TestCanonicalIdSequence:
    def test_bezne_generovani_001_002_003(self, store):
        ids = [_add(store, q) for q in ("1", "2", "3")]
        assert ids == [f"{_PREFIX}001", f"{_PREFIX}002", f"{_PREFIX}003"]

    def test_smazani_001_z_dvou_dalsi_je_003(self, store):
        _add(store, "1"); _add(store, "2")
        store.delete_trade(f"{_PREFIX}001")
        assert _add(store, "3") == f"{_PREFIX}003"

    def test_smazani_prostredniho_dalsi_je_004(self, store):
        _add(store, "1"); _add(store, "2"); _add(store, "3")
        store.delete_trade(f"{_PREFIX}002")
        assert _add(store, "4") == f"{_PREFIX}004"

    def test_nove_id_nekoliduje_s_existujicim(self, store):
        _add(store, "1"); _add(store, "2"); _add(store, "3")
        store.delete_trade(f"{_PREFIX}001")
        existing = _all_ids(store)
        new_id = generate_canonical_id(_TS, "xtb", "BUY", store.conn)
        assert new_id not in existing

    def test_ruzna_venue_cisluji_nezavisle(self, store):
        _add(store, "1", venue="xtb"); _add(store, "2", venue="xtb")
        assert _add(store, "3", venue="degiro") == "20260105_100000_DEGIRO_BUY_001"

    def test_ruzne_typy_cisluji_nezavisle(self, store):
        _add(store, "1", type_="BUY"); _add(store, "2", type_="BUY")
        assert _add(store, "3", type_="SELL") == "20260105_100000_XTB_SELL_001"

    def test_get_rows_by_id_neobsahuje_cizi_transakci(self, store):
        _add(store, "1"); _add(store, "2")
        store.delete_trade(f"{_PREFIX}001")
        new_id = _add(store, "3")
        rows = store.get_rows_by_id(new_id)
        assert len(rows) == 2
        assert sorted(str(r.amount) for r in rows) == ["-300", "3"]


class TestCanonicalIdPrefix:
    def test_podtrzitko_neni_wildcard(self, store):
        # 'X' na místě '_' by v LIKE vyhovělo; SUBSTR porovnává doslova
        store.conn.execute(
            "INSERT INTO ledger (id, timestamp, type, asset, amount, currency, price, venue,"
            " note, row_fp, imported_at) VALUES (?, ?, 'BUY', 'AAPL', '1', 'EUR', '1', 'xtb',"
            " NULL, 'fp-x', '2026-01-05T10:00:00')",
            ("20260105X100000_XTB_BUY_007", _TS.isoformat()),
        )
        assert generate_canonical_id(_TS, "xtb", "BUY", store.conn) == f"{_PREFIX}001"

    def test_necislene_suffixy_se_ignoruji(self, store):
        _add(store, "1")
        store.conn.execute(
            "INSERT INTO ledger (id, timestamp, type, asset, amount, currency, price, venue,"
            " note, row_fp, imported_at) VALUES (?, ?, 'BUY', 'AAPL', '1', 'EUR', '1', 'xtb',"
            " NULL, 'fp-y', '2026-01-05T10:00:00')",
            (f"{_PREFIX}abc", _TS.isoformat()),
        )
        assert generate_canonical_id(_TS, "xtb", "BUY", store.conn) == f"{_PREFIX}002"

    def test_venue_je_case_insensitive(self, store):
        _add(store, "1", venue="xtb")
        assert generate_canonical_id(_TS, "XTB", "buy", store.conn) == f"{_PREFIX}002"
