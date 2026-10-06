"""Testy pro XTB sync M3 — bezpečný import vybraných MISSING tickerů.

Pouze dočasné DB (tmp_db) a syntetické výpisy (tests/xtb_fixtures.py).
"""
import sqlite3
from datetime import datetime
from decimal import Decimal

import pytest

from core.ledger_store import LedgerStore
from core.model import RawRow
from core.services.holdings_engine import compute_cash_balance, compute_holdings
from core.services.position_sync import (
    CANNOT_IMPORT,
    DIFFERENT,
    LEDGER_ONLY,
    MATCHED,
    MISSING,
    build_import_rows,
    compare_positions,
)
from core.services.reversal_service import reverse_trade
from core.services.trade_service import AddTradeInput, build_trade_rows, generate_canonical_id
from core.services.ui_facade import get_xtb_sync_report, import_xtb_missing
from io_module.xtb_statement import load_xtb_open_positions
from tests.xtb_fixtures import FAKE_ACCOUNT, XtbStatementBuilder

_D = Decimal
_OPEN_UTC = datetime(2026, 3, 2, 9, 0, 0)          # → 2026-03-02 10:00:00 Praha
_OPEN_LOCAL = datetime(2026, 3, 2, 10, 0, 0)
_ALL = ["AAA.US", "BBB.US", "CCC.US", "DDD.US", "EEE.US", "FFF.US"]


def _statement(currency="EUR") -> XtbStatementBuilder:
    b = XtbStatementBuilder(currency=currency)
    # AAA.US: plně otevřený lot → MISSING (náklad 200.00)
    b.open_lot("11", "AAA.US", "10.0", _OPEN_UTC, value="250.00", gross="50.00")
    b.purchase("11", "AAA.US", "OPEN BUY 10 @ 20.00", "-200.00", _OPEN_UTC)
    # BBB.US: dva loty ve stejné sekundě, jeden částečně uzavřený → MISSING (33.33 + 20.00)
    b.open_lot("21", "BBB.US", "1.0", _OPEN_UTC, value="40.00", gross="6.67")
    b.purchase("21", "BBB.US", "OPEN BUY 3 @ 33.33", "-100.00", _OPEN_UTC)
    b.open_lot("22", "BBB.US", "2.0", _OPEN_UTC, value="25.00", gross="5.00")
    b.purchase("22", "BBB.US", "OPEN BUY 2 @ 10.00", "-20.00", _OPEN_UTC)
    # CCC.US: sloučená pozice → CANNOT_IMPORT
    b.open_lot("31", "CCC.US", "2.5", _OPEN_UTC, value="250", gross="0")
    b.purchase("31", "CCC.US", "OPEN BUY 0.5/2.5 @ 100", "-50.00", _OPEN_UTC)
    # DDD.US: 5 v XTB, 4 v ledgeru → DIFFERENT
    b.open_lot("41", "DDD.US", "5.0", _OPEN_UTC, value="60", gross="10")
    b.purchase("41", "DDD.US", "OPEN BUY 5 @ 10", "-50.00", _OPEN_UTC)
    # EEE.US: shodně 1 → MATCHED
    b.open_lot("51", "EEE.US", "1.0", _OPEN_UTC, value="12", gross="2")
    b.purchase("51", "EEE.US", "OPEN BUY 1 @ 10", "-10.00", _OPEN_UTC)
    return b


def _ledger_buy(db, ticker, qty, total, ts=datetime(2026, 1, 5, 12, 0, 0), venue="xtb", type_="BUY"):
    store = LedgerStore(db)
    try:
        trade_id = generate_canonical_id(ts, venue, type_, store.conn)
        store.insert_group(build_trade_rows(AddTradeInput(type_, ts, ticker, _D(qty), "EUR", _D(total), venue),
                                            trade_id=trade_id))
    finally:
        store.close()


@pytest.fixture
def setup(tmp_db, tmp_path):
    _ledger_buy(tmp_db, "DDD.US", "4", "40")
    _ledger_buy(tmp_db, "EEE.US", "1", "10")
    _ledger_buy(tmp_db, "FFF.US", "3", "30")
    return tmp_db, _statement().write_xlsx(tmp_path)


def _rows(db):
    store = LedgerStore(db)
    try:
        return store.timeline()
    finally:
        store.close()


def _raw_db(db):
    c = sqlite3.connect(db)
    try:
        return c.execute("SELECT * FROM ledger ORDER BY pk").fetchall()
    finally:
        c.close()


def _states(db, path):
    return {i.ticker: i.state for i in get_xtb_sync_report(db, path).report.items}


# ── generate_canonical_id(reserved=...) ───────────────────────────────────────

class TestCanonicalIdReserved:
    _TS = datetime(2026, 3, 2, 10, 0, 0)
    _PFX = "20260302_100000_XTB_BUY_"

    def test_bez_reserved_beze_zmeny(self, tmp_db):
        store = LedgerStore(tmp_db)
        try:
            assert generate_canonical_id(self._TS, "xtb", "BUY", store.conn) == f"{self._PFX}001"
            assert generate_canonical_id(self._TS, "xtb", "BUY", store.conn, None) == f"{self._PFX}001"
        finally:
            store.close()

    def test_reserved_se_zapocita(self, tmp_db):
        store = LedgerStore(tmp_db)
        try:
            assert generate_canonical_id(self._TS, "xtb", "BUY", store.conn,
                                         {f"{self._PFX}001", f"{self._PFX}002"}) == f"{self._PFX}003"
        finally:
            store.close()

    def test_reserved_a_db_dohromady(self, tmp_db):
        _ledger_buy(tmp_db, "AAA.US", "1", "1", ts=self._TS)          # DB má _001
        store = LedgerStore(tmp_db)
        try:
            assert generate_canonical_id(self._TS, "xtb", "BUY", store.conn, {f"{self._PFX}004"}) \
                == f"{self._PFX}005"
            assert generate_canonical_id(self._TS, "xtb", "BUY", store.conn, {f"{self._PFX}001"}) \
                == f"{self._PFX}002"
        finally:
            store.close()

    def test_reserved_jineho_prefixu_se_ignoruje(self, tmp_db):
        store = LedgerStore(tmp_db)
        try:
            assert generate_canonical_id(self._TS, "xtb", "BUY", store.conn,
                                         {"20260302_100000_XTB_SELL_009", "20260302_100000_DEGIRO_BUY_009"}) \
                == f"{self._PFX}001"
        finally:
            store.close()


# ── build_import_rows ─────────────────────────────────────────────────────────

class TestBuildImportRows:
    def _item(self, db, path, ticker):
        report = compare_positions(load_xtb_open_positions(path), _rows(db))
        return next(i for i in report.items if i.ticker == ticker)

    def test_presne_radky_jednoho_lotu(self, setup):
        db, path = setup
        store = LedgerStore(db)
        try:
            rows = build_import_rows(self._item(db, path, "AAA.US"), "EUR", store.conn)
        finally:
            store.close()
        assert len(rows) == 2 and not any(r.type == "FEE" for r in rows)
        stock, cash = rows
        for r in rows:
            assert r.id == "20260302_100000_XTB_BUY_001"
            assert r.timestamp == _OPEN_LOCAL and r.type == "BUY" and r.venue == "xtb"
            assert r.currency == "EUR" and r.note == "XTB position 11"
        assert stock.asset == "AAA.US" and stock.amount == _D("10") and str(stock.amount) == "10"
        assert stock.price == _D("20")                                  # 200.00 / 10
        assert cash.asset == "EUR" and cash.amount == _D("-200.00") and cash.price == _D("1")

    def test_castecny_lot_a_dva_loty_ve_stejne_sekunde(self, setup):
        db, path = setup
        store = LedgerStore(db)
        try:
            rows = build_import_rows(self._item(db, path, "BBB.US"), "EUR", store.conn)
        finally:
            store.close()
        groups = {}
        for r in rows:
            groups.setdefault(r.id, []).append(r)
        assert sorted(groups) == ["20260302_100000_XTB_BUY_001", "20260302_100000_XTB_BUY_002"]
        cash = sorted(r.amount for r in rows if r.asset == "EUR")
        assert cash == [_D("-33.33"), _D("-20.00")]                     # proporcionální náklad 100 × 1/3

    @pytest.mark.parametrize("ticker", ["CCC.US", "DDD.US", "EEE.US", "FFF.US"])
    def test_jen_missing(self, setup, ticker):
        db, path = setup
        store = LedgerStore(db)
        try:
            with pytest.raises(ValueError):
                build_import_rows(self._item(db, path, ticker), "EUR", store.conn)
        finally:
            store.close()


# ── facade: report ────────────────────────────────────────────────────────────

class TestSyncReportFacade:
    def test_report_stavy(self, setup):
        db, path = setup
        assert _states(db, path) == {"AAA.US": MISSING, "BBB.US": MISSING, "CCC.US": CANNOT_IMPORT,
                                     "DDD.US": DIFFERENT, "EEE.US": MATCHED, "FFF.US": LEDGER_ONLY}

    def test_report_jen_cte(self, setup):
        db, path = setup
        before = _raw_db(db)
        get_xtb_sync_report(db, path)
        assert _raw_db(db) == before

    def test_neexistujici_vypis(self, tmp_db, tmp_path):
        r = get_xtb_sync_report(tmp_db, str(tmp_path / f"EUR_{FAKE_ACCOUNT}_x.xlsx"))
        assert r.success is False and r.report is None
        assert FAKE_ACCOUNT not in r.error_message and str(tmp_path) not in r.error_message

    def test_chybejici_db_se_nevytvori(self, tmp_path):
        missing = str(tmp_path / "none.db")
        r = get_xtb_sync_report(missing, _statement().write_xlsx(tmp_path))
        assert r.success is False and not (tmp_path / "none.db").exists()


# ── facade: import ────────────────────────────────────────────────────────────

class TestImport:
    def test_import_vybranych_missing(self, setup):
        db, path = setup
        before_cash = compute_cash_balance(_rows(db)).get("EUR", _D(0))
        n_before = len(_raw_db(db))
        r = import_xtb_missing(db, path, ["AAA.US", "BBB.US"])
        assert r.success and r.imported == {"AAA.US": 1, "BBB.US": 2} and r.rejected == {}
        rows = _rows(db)
        assert len(rows) == n_before + 6
        assert not any(x.type == "FEE" for x in rows)
        holdings = {h.ticker: h for h in compute_holdings(rows)}
        assert holdings["AAA.US"].quantity == 10 and holdings["AAA.US"].cost_basis == _D("200")
        assert holdings["BBB.US"].quantity == 3
        assert holdings["BBB.US"].cost_basis.quantize(_D("0.01")) == _D("53.33")   # WAC má 28 míst
        # cash: v ledgeru je jen záporný EUR (žádný CASH_IN) → porovnáme hrubé součty
        eur = sum((x.amount for x in rows if x.asset == "EUR"), _D(0))
        assert eur == _D("-80") - _D("253.33")
        assert before_cash == _D(0)
        assert _states(db, path)["AAA.US"] == MATCHED and _states(db, path)["BBB.US"] == MATCHED

    @pytest.mark.parametrize("ticker, state", [("CCC.US", CANNOT_IMPORT), ("DDD.US", DIFFERENT),
                                               ("EEE.US", MATCHED), ("FFF.US", LEDGER_ONLY)])
    def test_ostatni_stavy_odmitnuty(self, setup, ticker, state):
        db, path = setup
        before = _raw_db(db)
        r = import_xtb_missing(db, path, [ticker])
        assert r.success and r.imported == {}
        assert ticker in r.rejected
        assert _raw_db(db) == before
        if state != LEDGER_ONLY:
            assert state in r.rejected[ticker]

    def test_neznamy_ticker(self, setup):
        db, path = setup
        r = import_xtb_missing(db, path, ["XYZ.US"])
        assert r.imported == {} and "není mezi otevřenými" in r.rejected["XYZ.US"]

    def test_duplicitni_a_maly_ticker_v_pozadavku(self, setup):
        db, path = setup
        r = import_xtb_missing(db, path, ["aaa.us", "AAA.US", " AAA.US "])
        assert r.imported == {"AAA.US": 1}

    def test_idempotence(self, setup):
        db, path = setup
        import_xtb_missing(db, path, ["AAA.US", "BBB.US"])
        after_first = _raw_db(db)
        r = import_xtb_missing(db, path, ["AAA.US", "BBB.US"])
        assert r.imported == {} and set(r.rejected) == {"AAA.US", "BBB.US"}
        assert all(MATCHED in msg for msg in r.rejected.values())
        assert _raw_db(db) == after_first

    def test_znovu_porovna_s_aktualni_db(self, setup):
        db, path = setup
        assert get_xtb_sync_report(db, path).report is not None             # report: AAA MISSING
        _ledger_buy(db, "AAA.US", "10", "200")                              # mezitím ručně zadáno
        r = import_xtb_missing(db, path, ["AAA.US"])
        assert r.imported == {} and MATCHED in r.rejected["AAA.US"]

    def test_uz_importovany_lot_se_neimportuje_znovu(self, setup):
        db, path = setup
        import_xtb_missing(db, path, ["AAA.US"])
        _ledger_buy(db, "AAA.US", "10", "300", ts=datetime(2026, 5, 1), type_="SELL")   # pozice prodána
        assert _states(db, path)["AAA.US"] == MISSING
        r = import_xtb_missing(db, path, ["AAA.US"])
        assert r.imported == {} and "dříve importován" in r.rejected["AAA.US"]

    def test_stornovany_import_bezpecne_odmitnut_row_fp(self, setup):
        db, path = setup
        import_xtb_missing(db, path, ["AAA.US"])
        reverse_trade(db, "20260302_100000_XTB_BUY_001")
        assert _states(db, path)["AAA.US"] == MISSING
        before = _raw_db(db)
        r = import_xtb_missing(db, path, ["AAA.US"])        # stornované řádky drží stejný row_fp
        assert r.imported == {} and "Duplicitní transakce" in r.rejected["AAA.US"]
        assert _raw_db(db) == before

    def test_smazany_import_lze_importovat_znovu(self, setup):
        from core.services.ui_facade import delete_trade
        db, path = setup
        import_xtb_missing(db, path, ["AAA.US"])
        assert delete_trade(db, "20260302_100000_XTB_BUY_001").success
        r = import_xtb_missing(db, path, ["AAA.US"])
        assert r.imported == {"AAA.US": 1}
        assert _states(db, path)["AAA.US"] == MATCHED

    def test_zaznam_po_as_of_blokuje_import(self, setup):
        db, path = setup
        _ledger_buy(db, "AAA.US", "2", "50", ts=datetime(2026, 10, 7, 9, 0, 0))   # po as-of výpisu
        assert _states(db, path)["AAA.US"] == MISSING
        r = import_xtb_missing(db, path, ["AAA.US"])
        assert r.imported == {} and "po datu výpisu" in r.rejected["AAA.US"]

    def test_atomicky_per_ticker(self, setup):
        db, path = setup
        store = LedgerStore(db)
        try:   # cizí řádek se stejným row_fp jako EUR noha druhého lotu BBB
            store.insert(RawRow(id="OTHER", timestamp=_OPEN_LOCAL, type="BUY", asset="EUR",
                                amount=_D("-20.00"), currency="EUR", price=_D("1"), venue="xtb"))
        finally:
            store.close()
        r = import_xtb_missing(db, path, ["AAA.US", "BBB.US"])
        assert r.imported == {"AAA.US": 1}
        assert "Duplicitní transakce" in r.rejected["BBB.US"]
        assert not any(x.asset == "BBB.US" for x in _rows(db))

    def test_jina_mena_nic_nezapise(self, tmp_db, tmp_path):
        before = _raw_db(tmp_db)
        r = import_xtb_missing(tmp_db, _statement(currency="USD").write_xlsx(tmp_path), ["AAA.US"])
        assert r.success is False and "jen EUR" in r.error_message
        assert _raw_db(tmp_db) == before

    def test_cislo_uctu_nikde(self, setup):
        db, path = setup
        r = import_xtb_missing(db, path, _ALL)
        assert all(FAKE_ACCOUNT not in str(v) for v in _raw_db(db))
        assert FAKE_ACCOUNT not in repr(r)


# ── M5: doplnění chybějících lotů u DIFFERENT ─────────────────────────────────

from core.services.position_sync import build_repair_rows  # noqa: E402
from core.services.ui_facade import repair_xtb_different  # noqa: E402

_LOT_A_UTC = datetime(2026, 2, 2, 14, 0, 0)       # → 15:00 Praha
_LOT_B_UTC = datetime(2026, 5, 4, 13, 0, 0)       # → 15:00 Praha (letní čas)


def _repair_statement() -> XtbStatementBuilder:
    b = XtbStatementBuilder()
    # GGG.US: ledger má lot A (5 ks / 50.00), chybí lot B (20 ks / 200.00) → opravitelný DIFFERENT
    b.open_lot("71", "GGG.US", "5.0", _LOT_A_UTC, value="60.00", gross="10.00")
    b.purchase("71", "GGG.US", "OPEN BUY 5 @ 10.00", "-50.00", _LOT_A_UTC)
    b.open_lot("72", "GGG.US", "20.0", _LOT_B_UTC, value="230.00", gross="30.00")
    b.purchase("72", "GGG.US", "OPEN BUY 20 @ 10.00", "-200.00", _LOT_B_UTC)
    # HHH.US: ledger 4 ks, XTB lot 5 ks → DIFFERENT, nelze spárovat → neopravitelný
    b.open_lot("81", "HHH.US", "5.0", _LOT_A_UTC, value="60", gross="10")
    b.purchase("81", "HHH.US", "OPEN BUY 5 @ 10", "-50.00", _LOT_A_UTC)
    # III.US: MISSING (cesta oprav ho musí odmítnout)
    b.open_lot("91", "III.US", "1.0", _LOT_A_UTC, value="12", gross="2")
    b.purchase("91", "III.US", "OPEN BUY 1 @ 10", "-10.00", _LOT_A_UTC)
    return b


@pytest.fixture
def repair_setup(tmp_db, tmp_path):
    _ledger_buy(tmp_db, "GGG.US", "5", "50.00", ts=datetime(2026, 2, 2, 15, 0, 40))
    _ledger_buy(tmp_db, "HHH.US", "4", "40.00")
    return tmp_db, _repair_statement().write_xlsx(tmp_path)


class TestRepair:
    def test_report(self, repair_setup):
        db, path = repair_setup
        items = {i.ticker: i for i in get_xtb_sync_report(db, path).report.items}
        assert items["GGG.US"].state == DIFFERENT and items["GGG.US"].repairable
        assert [r.lot.position_id for r in items["GGG.US"].repair_lots] == ["72"]
        assert items["HHH.US"].state == DIFFERENT and not items["HHH.US"].repairable
        assert items["III.US"].state == MISSING

    def test_doplni_jen_chybejici_lot_a_nemeni_existujici(self, repair_setup):
        db, path = repair_setup
        before = _raw_db(db)
        r = repair_xtb_different(db, path, ["GGG.US"])
        assert r.success and r.imported == {"GGG.US": 1} and r.rejected == {}
        after = _raw_db(db)
        assert after[:len(before)] == before                         # existující řádky beze změny
        added = [x for x in _rows(db) if x.note == "XTB position 72"]
        assert len(after) == len(before) + 2 and len(added) == 2
        stock = next(x for x in added if x.asset == "GGG.US")
        cash = next(x for x in added if x.asset == "EUR")
        assert stock.amount == _D("20") and cash.amount == _D("-200.00") and stock.type == cash.type == "BUY"
        assert stock.timestamp == datetime(2026, 5, 4, 15, 0, 0) and not any(x.type == "FEE" for x in added)
        holdings = {h.ticker: h for h in compute_holdings(_rows(db))}
        assert holdings["GGG.US"].quantity == 25
        assert _states(db, path)["GGG.US"] == MATCHED

    def test_idempotence(self, repair_setup):
        db, path = repair_setup
        repair_xtb_different(db, path, ["GGG.US"])
        after_first = _raw_db(db)
        r = repair_xtb_different(db, path, ["GGG.US"])
        assert r.imported == {} and MATCHED in r.rejected["GGG.US"]
        assert _raw_db(db) == after_first

    def test_neopravitelny_different_odmitnut(self, repair_setup):
        db, path = repair_setup
        before = _raw_db(db)
        r = repair_xtb_different(db, path, ["HHH.US"])
        assert r.imported == {} and "Nelze automaticky doplnit" in r.rejected["HHH.US"]
        assert _raw_db(db) == before

    def test_missing_cestou_oprav_odmitnut_a_naopak(self, repair_setup):
        db, path = repair_setup
        before = _raw_db(db)
        r1 = repair_xtb_different(db, path, ["III.US"])
        assert r1.imported == {} and MISSING in r1.rejected["III.US"]
        r2 = import_xtb_missing(db, path, ["GGG.US"])                # import MISSING beze změny chování
        assert r2.imported == {} and DIFFERENT in r2.rejected["GGG.US"]
        assert _raw_db(db) == before

    def test_plan_se_prepocita_proti_aktualni_db(self, repair_setup):
        db, path = repair_setup
        assert get_xtb_sync_report(db, path).report is not None     # report: GGG opravitelný
        _ledger_buy(db, "GGG.US", "20", "200.00", ts=datetime(2026, 5, 4, 15, 0, 20))   # mezitím ručně
        before = _raw_db(db)
        r = repair_xtb_different(db, path, ["GGG.US"])
        assert r.imported == {} and MATCHED in r.rejected["GGG.US"]
        assert _raw_db(db) == before

    def test_nelze_preplnit_po_zmene_db(self, repair_setup):
        db, path = repair_setup
        _ledger_buy(db, "GGG.US", "10", "100.00", ts=datetime(2026, 3, 1, 12, 0, 0))   # nespárovatelný BUY
        before = _raw_db(db)
        r = repair_xtb_different(db, path, ["GGG.US"])
        assert r.imported == {} and "Nelze automaticky doplnit" in r.rejected["GGG.US"]
        assert _raw_db(db) == before
        assert {h.ticker: h.quantity for h in compute_holdings(_rows(db))}["GGG.US"] == 15

    def test_atomicky_per_ticker(self, repair_setup):
        db, path = repair_setup
        store = LedgerStore(db)
        try:   # cizí řádek se stejným row_fp jako EUR noha doplňovaného lotu
            store.insert(RawRow(id="OTHER", timestamp=datetime(2026, 5, 4, 15, 0, 0), type="BUY",
                                asset="EUR", amount=_D("-200.00"), currency="EUR", price=_D("1"), venue="xtb"))
        finally:
            store.close()
        before = _raw_db(db)
        r = repair_xtb_different(db, path, ["GGG.US"])
        assert r.imported == {} and "Duplicitní transakce" in r.rejected["GGG.US"]
        assert _raw_db(db) == before

    def test_build_repair_rows_jen_opravitelny(self, repair_setup):
        db, path = repair_setup
        items = {i.ticker: i for i in get_xtb_sync_report(db, path).report.items}
        store = LedgerStore(db)
        try:
            for ticker in ("HHH.US", "III.US"):
                with pytest.raises(ValueError):
                    build_repair_rows(items[ticker], "EUR", store.conn)
        finally:
            store.close()

    def test_cislo_uctu_nikde(self, repair_setup):
        db, path = repair_setup
        r = repair_xtb_different(db, path, ["GGG.US", "HHH.US", "III.US"])
        assert all(FAKE_ACCOUNT not in str(v) for v in _raw_db(db)) and FAKE_ACCOUNT not in repr(r)
