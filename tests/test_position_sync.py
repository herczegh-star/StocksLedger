"""Testy pro core/services/position_sync.py — čistý XTB sync engine (M2).

Pouze syntetická data; žádný zápis do DB.
"""
from datetime import datetime
from decimal import Decimal

import pytest

from core.model import RawRow
from core.services.position_sync import (
    CANNOT_IMPORT,
    COST_NOT_RECONCILED,
    DIFFERENT,
    INVALID_PURCHASE_AMOUNT,
    LEDGER_ONLY,
    MATCHED,
    MERGED_POSITION,
    MISSING,
    NO_PURCHASE,
    TICKER_MISMATCH,
    UNPARSED_COMMENT,
    VOLUME_EXCEEDS_PURCHASE,
    compare_positions,
    reconstruct_lot,
)
from core.services.reversal_service import build_reversal_rows
from core.services.trade_service import AddTradeInput, build_trade_rows
from io_module.xtb_statement import XtbOpenLot, XtbOpenPositionsSnapshot, XtbPurchase

_D = Decimal
_AS_OF = datetime(2026, 10, 6, 16, 57, 52)
_T = datetime(2026, 3, 2, 10, 0, 0)


def _purchase(comment="OPEN BUY 10 @ 20.00", amount="-200.00", ticker="TST1.US", qty=None, total=None,
              position="1"):
    import re
    m = re.fullmatch(r"OPEN BUY ([0-9.]+)(?:/([0-9.]+))? @ ([0-9.]+)", comment)
    q = _D(qty) if qty is not None else (_D(m.group(1)) if m else None)
    b = _D(total) if total is not None else (_D(m.group(2) or m.group(1)) if m else None)
    return XtbPurchase(position_id=position, ticker=ticker, comment=comment, quantity=q, reported_total=b,
                       amount=_D(amount), time_local=_T)


def _lot(volume="10", purchases=None, value="250.00", gross="50.00", ticker="TST1.US", position="1",
         open_time=_T):
    return XtbOpenLot(position_id=position, ticker=ticker, category="STOCK", volume=_D(volume),
                      open_time_local=open_time,
                      value=_D(value) if value is not None else None,
                      gross_profit=_D(gross) if gross is not None else None,
                      purchases=tuple([_purchase()] if purchases is None else purchases))


def _snapshot(*lots, excluded=0):
    return XtbOpenPositionsSnapshot(currency="EUR", as_of_local=_AS_OF, lots=tuple(lots), excluded_count=excluded)


def _buy(ticker, qty, total, ts=_T, venue="xtb", trade_id=None):
    return build_trade_rows(AddTradeInput("BUY", ts, ticker, _D(qty), "EUR", _D(total), venue),
                            trade_id=trade_id or f"{ticker}-{ts.isoformat()}-{qty}")


def _item(report, ticker):
    return next(i for i in report.items if i.ticker == ticker)


# ── rekonstrukce lotu ─────────────────────────────────────────────────────────

class TestReconstructLot:
    def test_plne_otevreny_lot_naklad_z_nakupu(self):
        r = reconstruct_lot(_lot())                      # 10 ks za 200.00, XTB 250 − 50 = 200
        assert r.importable and r.cost == _D("200.00") and r.xtb_open_cost == _D("200.00")
        assert r.reason is None

    def test_castecne_uzavreny_lot_proporcne(self):
        lot = _lot(volume="1", purchases=[_purchase("OPEN BUY 3 @ 33.33", "-100.00")], value="40.00",
                   gross="6.67")
        r = reconstruct_lot(lot)                         # 100 × 1/3 = 33.333… → 33.33
        assert r.importable and r.cost == _D("33.33")

    def test_zaokrouhleni_half_even(self):
        lot = _lot(volume="1", purchases=[_purchase("OPEN BUY 2 @ 0.125", "-0.25")], value="0.12", gross="0")
        assert reconstruct_lot(lot).cost == _D("0.12")   # 0.125 → 0.12 (half-even, ne 0.13)

    def test_vice_nakupu_pod_jednim_id(self):
        lot = _lot(volume="3", purchases=[_purchase("OPEN BUY 2/3 @ 10", "-20.00"),
                                          _purchase("OPEN BUY 1/3 @ 11", "-11.00")],
                   value="40.00", gross="9.00")
        r = reconstruct_lot(lot)
        assert r.importable and r.cost == _D("31.00")

    def test_slouceny_lot_cannot_import(self):
        lot = _lot(volume="2.5", purchases=[_purchase("OPEN BUY 0.5/2.5 @ 100", "-50.00")], value="250",
                   gross="0")
        r = reconstruct_lot(lot)
        assert not r.importable and r.reason == MERGED_POSITION and r.cost is None

    def test_slouceny_i_castecne_uzavreny(self):
        lot = _lot(volume="10", purchases=[_purchase("OPEN BUY 33/33.9684 @ 80", "-2640.00")],
                   value="900", gross="100")
        assert reconstruct_lot(lot).reason == MERGED_POSITION

    def test_objem_vetsi_nez_nakup(self):
        lot = _lot(volume="2", purchases=[_purchase("OPEN BUY 1 @ 10", "-10.00")])
        assert reconstruct_lot(lot).reason == VOLUME_EXCEEDS_PURCHASE

    def test_chybi_nakup(self):
        r = reconstruct_lot(_lot(purchases=[]))
        assert r.reason == NO_PURCHASE and "dřívějšího data" in r.detail

    def test_nerozpoznany_komentar(self):
        assert reconstruct_lot(_lot(purchases=[_purchase("something")])).reason == UNPARSED_COMMENT

    def test_jiny_ticker_nakupu(self):
        assert reconstruct_lot(_lot(purchases=[_purchase(ticker="OTHER.US")])).reason == TICKER_MISMATCH

    @pytest.mark.parametrize("amount", ["0", "200.00"])
    def test_castka_neni_platba(self, amount):
        assert reconstruct_lot(_lot(purchases=[_purchase(amount=amount)])).reason == INVALID_PURCHASE_AMOUNT

    def test_guard_rozdil_presne_cent_projde(self):
        r = reconstruct_lot(_lot(value="250.01", gross="50.00"))     # XTB 200.01 vs 200.00
        assert r.importable
        assert r.cost == _D("200.00")                    # zdrojem je nákup, ne Value − Gross Profit

    def test_guard_rozdil_nad_cent_cannot_import(self):
        r = reconstruct_lot(_lot(value="250.011", gross="50.00"))
        assert not r.importable and r.reason == COST_NOT_RECONCILED

    @pytest.mark.parametrize("value, gross", [(None, "50.00"), ("250.00", None)])
    def test_guard_bez_value_nebo_gross(self, value, gross):
        assert reconstruct_lot(_lot(value=value, gross=gross)).reason == COST_NOT_RECONCILED


# ── stavy ─────────────────────────────────────────────────────────────────────

class TestStates:
    def test_matched(self):
        rep = compare_positions(_snapshot(_lot()), _buy("TST1.US", "10", "200"))
        assert _item(rep, "TST1.US").state == MATCHED

    def test_matched_i_pri_rozdilnych_lotech_a_datech(self):
        lots = (_lot(volume="5", position="1", purchases=[_purchase("OPEN BUY 5 @ 1", "-5", position="1")],
                     value="5", gross="0"),
                _lot(volume="5", position="2", purchases=[_purchase("OPEN BUY 5 @ 1", "-5", position="2")],
                     value="5", gross="0"))
        ledger = _buy("TST1.US", "10", "999", ts=datetime(2025, 1, 1))   # 1 BUY, jiné datum i náklad
        item = _item(compare_positions(_snapshot(*lots), ledger), "TST1.US")
        assert item.state == MATCHED
        assert item.xtb_lot_count == 2 and item.ledger_buy_count == 1

    def test_matched_i_kdyz_lot_nelze_rekonstruovat(self):
        rep = compare_positions(_snapshot(_lot(purchases=[])), _buy("TST1.US", "10", "200"))
        assert _item(rep, "TST1.US").state == MATCHED

    def test_missing(self):
        item = _item(compare_positions(_snapshot(_lot()), []), "TST1.US")
        assert item.state == MISSING and item.ledger_quantity == 0 and item.blocking_lots == ()

    def test_cannot_import_kdyz_jeden_lot_nebezpecny(self):
        ok = _lot(position="1")
        bad = _lot(position="2", purchases=[])
        item = _item(compare_positions(_snapshot(ok, bad), []), "TST1.US")
        assert item.state == CANNOT_IMPORT
        assert [r.lot.position_id for r in item.blocking_lots] == ["2"]

    def test_different(self):
        item = _item(compare_positions(_snapshot(_lot()), _buy("TST1.US", "7", "140")), "TST1.US")
        assert item.state == DIFFERENT and item.xtb_quantity == 10 and item.ledger_quantity == 7

    def test_different_zustava_different_i_s_nebezpecnym_lotem(self):
        rep = compare_positions(_snapshot(_lot(purchases=[])), _buy("TST1.US", "7", "140"))
        assert _item(rep, "TST1.US").state == DIFFERENT

    def test_ledger_only(self):
        item = _item(compare_positions(_snapshot(), _buy("TST2.US", "3", "30")), "TST2.US")
        assert item.state == LEDGER_ONLY and item.lots == () and item.xtb_quantity == 0

    def test_presna_shoda_desetinnych_mnozstvi(self):
        lot = _lot(volume="26.9232", purchases=[_purchase("OPEN BUY 26.9232 @ 1", "-26.92")],
                   value="26.92", gross="0")
        rep = compare_positions(_snapshot(lot), _buy("TST1.US", "26.9232", "26.92"))
        assert _item(rep, "TST1.US").state == MATCHED


# ── ledger: venue, as-of, storna ──────────────────────────────────────────────

class TestLedgerSide:
    def test_jen_venue_xtb(self):
        rep = compare_positions(_snapshot(_lot()), _buy("TST1.US", "10", "200", venue="degiro"))
        assert _item(rep, "TST1.US").state == MISSING

    def test_ledger_only_jen_pro_xtb(self):
        rep = compare_positions(_snapshot(), _buy("TST2.US", "3", "30", venue="degiro"))
        assert rep.items == ()

    def test_buy_po_as_of_se_nepocita(self):
        rep = compare_positions(_snapshot(_lot()), _buy("TST1.US", "10", "200", ts=datetime(2026, 10, 7)))
        assert _item(rep, "TST1.US").state == MISSING

    def test_buy_presne_v_as_of_se_pocita(self):
        rep = compare_positions(_snapshot(_lot()), _buy("TST1.US", "10", "200", ts=_AS_OF))
        assert _item(rep, "TST1.US").state == MATCHED

    def test_stornovany_buy_se_nepocita(self):
        buy = _buy("TST1.US", "10", "200")
        rep = compare_positions(_snapshot(_lot()), buy + build_reversal_rows(buy))
        assert _item(rep, "TST1.US").state == MISSING

    def test_storno_po_as_of_plati(self):
        buy = _buy("TST1.US", "10", "200")
        rev = [RawRow(id=r.id, timestamp=datetime(2026, 12, 1), type=r.type, asset=r.asset, amount=r.amount,
                      currency=r.currency, price=r.price, venue=r.venue, note=r.note)
               for r in build_reversal_rows(buy)]
        rep = compare_positions(_snapshot(_lot()), buy + rev)
        assert _item(rep, "TST1.US").state == MISSING

    def test_ticker_case(self):
        rep = compare_positions(_snapshot(_lot(ticker="tst1.us")), _buy("TST1.US", "10", "200"))
        assert [i.ticker for i in rep.items] == ["TST1.US"] and rep.items[0].state == MATCHED


# ── diagnostika a report ──────────────────────────────────────────────────────

class TestReport:
    def test_diagnostika(self):
        ledger = (_buy("TST1.US", "4", "80", ts=datetime(2026, 1, 1), trade_id="A")
                  + _buy("TST1.US", "6", "120", ts=datetime(2026, 2, 1), trade_id="B"))
        item = _item(compare_positions(_snapshot(_lot()), ledger), "TST1.US")
        assert item.ledger_buy_count == 2
        assert item.ledger_buy_dates == (datetime(2026, 1, 1), datetime(2026, 2, 1))
        assert item.xtb_open_cost == _D("200.00") and item.ledger_cost_basis == _D("200")

    def test_xtb_open_cost_none_kdyz_chybi_hodnota(self):
        item = _item(compare_positions(_snapshot(_lot(value=None)), []), "TST1.US")
        assert item.xtb_open_cost is None

    def test_report_razeni_pocty_a_metadata(self):
        snap = XtbOpenPositionsSnapshot(
            currency="EUR", as_of_local=_AS_OF, excluded_count=3,
            lots=(_lot(ticker="ZZZ.US", purchases=[_purchase(ticker="ZZZ.US")]),
                  _lot(ticker="AAA.US", position="2",
                                              purchases=[_purchase(ticker="AAA.US")])))
        rep = compare_positions(snap, _buy("MMM.US", "1", "1"))
        assert [i.ticker for i in rep.items] == ["AAA.US", "MMM.US", "ZZZ.US"]
        assert rep.counts() == {MATCHED: 0, MISSING: 2, CANNOT_IMPORT: 0, DIFFERENT: 0, LEDGER_ONLY: 1}
        assert rep.currency == "EUR" and rep.as_of_local == _AS_OF and rep.excluded_count == 3

    def test_vstupni_radky_se_nemeni(self):
        ledger = _buy("TST1.US", "10", "200")
        before = [r.to_dict() for r in ledger]
        compare_positions(_snapshot(_lot()), ledger)
        assert [r.to_dict() for r in ledger] == before


# ── end-to-end: syntetický výpis → parser → engine ───────────────────────────

class TestWithParsedStatement:
    def test_syntheticky_vypis(self, tmp_path):
        from io_module.xtb_statement import load_xtb_open_positions
        from tests.xtb_fixtures import XtbStatementBuilder
        t = datetime(2026, 3, 2, 9, 0, 0)          # UTC → 10:00 Praha
        b = XtbStatementBuilder()
        b.open_lot("11", "AAA.US", "10.0", t, value="250.00", gross="50.00")
        b.purchase("11", "AAA.US", "OPEN BUY 10 @ 20.00", "-200.00", t)
        b.open_lot("21", "BBB.US", "1.0", t, value="40.00", gross="6.67")
        b.purchase("21", "BBB.US", "OPEN BUY 3 @ 33.33", "-100.00", t)
        b.open_lot("31", "CCC.US", "2.5", t, value="250", gross="0")
        b.purchase("31", "CCC.US", "OPEN BUY 0.5/2.5 @ 100", "-50.00", t)
        b.open_lot("41", "DDD.US", "5.0", t, value="60", gross="10")
        b.purchase("41", "DDD.US", "OPEN BUY 5 @ 10", "-50.00", t)
        snap = load_xtb_open_positions(b.write_xlsx(tmp_path))
        ledger = _buy("AAA.US", "10", "200") + _buy("DDD.US", "4", "40") + _buy("EEE.US", "1", "1")
        rep = compare_positions(snap, ledger)
        assert {i.ticker: i.state for i in rep.items} == {
            "AAA.US": MATCHED, "BBB.US": MISSING, "CCC.US": CANNOT_IMPORT,
            "DDD.US": DIFFERENT, "EEE.US": LEDGER_ONLY}
        assert _item(rep, "BBB.US").lots[0].cost == _D("33.33")
