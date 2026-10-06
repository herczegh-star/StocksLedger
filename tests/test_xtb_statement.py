"""Testy pro io_module.xtb_statement — XTB Open Positions parser (XTB sync, M1).

Pouze syntetické výpisy z tests/xtb_fixtures.py; žádná reálná data.
"""
import zipfile
from datetime import datetime
from decimal import Decimal

import pytest

from io_module.xlsx_reader import read_xlsx
from io_module.xtb_statement import (
    XtbStatementError,
    load_xtb_open_positions,
    parse_xtb_open_positions,
)
from tests.xtb_fixtures import FAKE_ACCOUNT, OPEN_HEADER, Num, XtbStatementBuilder, build_xlsx, serial

_D = Decimal
_SUMMER = datetime(2026, 6, 4, 11, 52, 55)   # UTC → 13:52:55 Praha (CEST, +2)
_WINTER = datetime(2026, 1, 15, 9, 30, 0)    # UTC → 10:30:00 Praha (CET, +1)


def _basic() -> XtbStatementBuilder:
    b = XtbStatementBuilder()
    b.open_lot("5001", "TST1.US", "10.0", _SUMMER, value="250.00", gross="50.00")
    b.open_lot("5002", "TST1.US", "0.9232", _WINTER, value="23.00", gross="-1.00")
    b.open_lot("6001", "TST2.DE", "3.0", _WINTER, category="ETF")
    b.purchase("5001", "TST1.US", "OPEN BUY 10 @ 20.00", "-200.00", _SUMMER)
    b.purchase("5002", "TST1.US", "OPEN BUY 0.9232/26.9232 @ 25.50", "-24.00", _WINTER)
    b.purchase("6001", "TST2.DE", "OPEN BUY 2 @ 30.00", "-60.00", _WINTER)
    b.purchase("6001", "TST2.DE", "OPEN BUY 1 @ 31.00", "-31.00", _WINTER)
    b.purchase("7777", "TST9.US", "OPEN BUY 5 @ 1.00", "-5.00", _WINTER)        # uzavřená pozice
    b.cash_op("Stock sell", "TST9.US", "CLOSE BUY 5 @ 2.00", "10.00", _WINTER, "7777")
    b.cash_op("Deposit", "", "deposit", "1000.00", _WINTER)
    return b


def _parse(builder: XtbStatementBuilder, sheets=None, name=None):
    return parse_xtb_open_positions(read_xlsx(builder.xlsx_bytes(sheets)), name or builder.xlsx_name)


def _error(builder: XtbStatementBuilder, sheets=None, name=None) -> str:
    with pytest.raises(XtbStatementError) as exc:
        _parse(builder, sheets, name)
    assert FAKE_ACCOUNT not in str(exc.value)
    return str(exc.value)


# ── soubor: ZIP / XLSX ────────────────────────────────────────────────────────

class TestFileInput:
    def test_xlsx(self, tmp_path):
        snap = load_xtb_open_positions(_basic().write_xlsx(tmp_path))
        assert len(snap.lots) == 3

    def test_zip_s_jednim_xlsx_ve_slozce(self, tmp_path):
        snap = load_xtb_open_positions(_basic().write_zip(tmp_path, extra=[("readme.txt", "x")]))
        assert snap.currency == "EUR" and len(snap.lots) == 3

    @pytest.mark.parametrize("count", [0, 2])
    def test_zip_bez_nebo_se_dvema_xlsx(self, tmp_path, count):
        path = _basic().write_zip(tmp_path, xlsx_count=count, extra=[("readme.txt", "x")])
        with pytest.raises(XtbStatementError, match=f"nalezeno {count}"):
            load_xtb_open_positions(path)

    def test_neni_zip_ani_xlsx(self, tmp_path):
        path = tmp_path / "x.zip"
        path.write_bytes(b"not a zip")
        with pytest.raises(XtbStatementError, match="není ZIP ani XLSX"):
            load_xtb_open_positions(str(path))

    def test_neexistujici_soubor_bez_cesty_ve_zprave(self, tmp_path):
        path = tmp_path / f"EUR_{FAKE_ACCOUNT}_2006-01-01_2026-10-06.xlsx"
        with pytest.raises(XtbStatementError) as exc:
            load_xtb_open_positions(str(path))
        assert FAKE_ACCOUNT not in str(exc.value) and str(tmp_path) not in str(exc.value)

    def test_zip_s_poskozenym_xlsx(self, tmp_path):
        path = tmp_path / "s.zip"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("EUR_1_2006-01-01_2026-10-06.xlsx", b"broken")
        with pytest.raises(XtbStatementError, match="není čitelný XLSX"):
            load_xtb_open_positions(str(path))


# ── struktura listů ───────────────────────────────────────────────────────────

class TestSheetStructure:
    def test_chybi_list(self):
        sheets = _basic().sheets()
        del sheets["Cash Operations"]
        assert "Cash Operations" in _error(_basic(), sheets)

    def test_chybi_sloupec_hlavicky(self):
        sheets = _basic().sheets()
        opened = sheets["Open Positions"]
        i = opened.index(OPEN_HEADER)
        opened[i] = tuple("Volume X" if h == "Volume" else h for h in OPEN_HEADER)
        assert "nenalezena hlavička" in _error(_basic(), sheets)

    def test_duplicitni_sloupec(self):
        sheets = _basic().sheets()
        opened = sheets["Open Positions"]
        i = opened.index(OPEN_HEADER)
        opened[i] = OPEN_HEADER + ("Ticker",)
        assert "duplicitní sloupec 'Ticker'" in _error(_basic(), sheets)

    def test_chybi_as_of(self):
        sheets = _basic().sheets()
        sheets["Open Positions"] = [r for r in sheets["Open Positions"]
                                    if not (r and r[0] == "Data as of report generated")]
        assert "Data as of report generated" in _error(_basic(), sheets)


# ── loty ──────────────────────────────────────────────────────────────────────

class TestLots:
    def test_zakladni_loty_serazene(self):
        snap = _parse(_basic())
        assert [(l.ticker, l.position_id) for l in snap.lots] == [
            ("TST1.US", "5002"), ("TST1.US", "5001"), ("TST2.DE", "6001")]   # ticker, čas, ID
        lot = next(l for l in snap.lots if l.position_id == "5001")
        assert lot.volume == _D("10.0") and lot.category == "STOCK"
        assert lot.value == _D("250.00") and lot.gross_profit == _D("50.00")

    def test_desetinny_objem_presne(self):
        lot = next(l for l in _parse(_basic()).lots if l.position_id == "5002")
        assert str(lot.volume) == "0.9232"

    def test_kategorie_ze_souhrnneho_radku(self):
        lot = next(l for l in _parse(_basic()).lots if l.ticker == "TST2.DE")
        assert lot.category == "ETF"

    def test_kategorie_z_radku_pozice_ma_prednost(self):
        b = XtbStatementBuilder().open_lot("1", "TST1.US", "1", _WINTER, category="CFD", row_category="ETC")
        assert _parse(b).lots[0].category == "ETC"

    def test_cfd_a_neznama_kategorie_vylouceny(self):
        b = _basic()
        b.open_lot("8001", "TSTC.US", "5", _WINTER, category="CFD", type_="SELL")   # SELL u CFD nevadí
        b.open_lot("8002", "TSTX.US", "1", _WINTER, category="FOREX")
        snap = _parse(b)
        assert snap.excluded_count == 2
        assert {l.ticker for l in snap.lots} == {"TST1.US", "TST2.DE"}

    def test_souhrnne_radky_nejsou_loty(self):
        assert len(_parse(_basic()).lots) == 3

    def test_position_id_jako_cislo_s_nulou(self):
        b = XtbStatementBuilder().open_lot("4242.0", "TST1.US", "1", _WINTER)
        b.purchase("4242", "TST1.US", "OPEN BUY 1 @ 1.00", "-1.00", _WINTER)
        lot = _parse(b).lots[0]
        assert lot.position_id == "4242" and len(lot.purchases) == 1

    def test_prazdna_value_a_gross_jsou_none(self):
        b = XtbStatementBuilder().open_lot("1", "TST1.US", "1", _WINTER, value="", gross="")
        lot = _parse(b).lots[0]
        assert lot.value is None and lot.gross_profit is None

    def test_typ_jiny_nez_buy_u_akcie_je_chyba(self):
        b = XtbStatementBuilder().open_lot("1", "TST1.US", "1", _WINTER, type_="SELL")
        assert "nepodporovaný typ pozice" in _error(b)

    @pytest.mark.parametrize("volume", ["0", "-1"])
    def test_neplatny_objem(self, volume):
        b = XtbStatementBuilder().open_lot("1", "TST1.US", volume, _WINTER)
        assert "neplatný objem" in _error(b)

    def test_neciselny_objem(self):
        b = XtbStatementBuilder().open_lot("1", "TST1.US", "abc", _WINTER)
        assert "neplatné číslo" in _error(b)

    def test_duplicitni_position_id(self):
        b = XtbStatementBuilder().open_lot("1", "TST1.US", "1", _WINTER).open_lot("1", "TST1.US", "2", _WINTER)
        assert "duplicitní Position ID" in _error(b)


# ── nákupy z Cash Operations ──────────────────────────────────────────────────

class TestPurchases:
    def test_nakupy_navazane_pres_position_id(self):
        lots = {l.position_id: l for l in _parse(_basic()).lots}
        assert len(lots["5001"].purchases) == 1
        assert len(lots["6001"].purchases) == 2
        p = lots["5001"].purchases[0]
        assert p.quantity == _D("10") and p.reported_total == _D("10")
        assert p.amount == _D("-200.00") and p.ticker == "TST1.US"
        assert p.time_local == datetime(2026, 6, 4, 13, 52, 55)

    def test_komentar_s_lomitkem(self):
        p = next(l for l in _parse(_basic()).lots if l.position_id == "5002").purchases[0]
        assert p.quantity == _D("0.9232") and p.reported_total == _D("26.9232")

    def test_nerozpoznany_komentar_je_none(self):
        b = XtbStatementBuilder().open_lot("1", "TST1.US", "1", _WINTER)
        b.purchase("1", "TST1.US", "something else", "-1.00", _WINTER)
        p = _parse(b).lots[0].purchases[0]
        assert p.quantity is None and p.reported_total is None and p.comment == "something else"

    def test_uzavrene_a_jine_operace_se_ignoruji(self):
        lots = _parse(_basic()).lots
        assert all(p.position_id in {"5001", "5002", "6001"} for l in lots for p in l.purchases)
        assert sum(len(l.purchases) for l in lots) == 4

    def test_lot_bez_nakupu_ma_prazdne_purchases(self):
        b = XtbStatementBuilder().open_lot("1", "TST1.US", "1", _WINTER)
        assert _parse(b).lots[0].purchases == ()


# ── čas ───────────────────────────────────────────────────────────────────────

class TestTime:
    def test_leto_a_zima_na_prahu(self):
        lots = {l.position_id: l for l in _parse(_basic()).lots}
        assert lots["5001"].open_time_local == datetime(2026, 6, 4, 13, 52, 55)   # +2
        assert lots["5002"].open_time_local == datetime(2026, 1, 15, 10, 30, 0)   # +1

    def test_as_of(self):
        assert _parse(_basic()).as_of_local == datetime(2026, 10, 6, 16, 57, 52)

    @pytest.mark.parametrize("serial_text, expected_second", [
        ("46000.50000578", 0),       # 12:00:00.4994 UTC → dolů
        ("46000.500006944", 1),      # 12:00:00.6 UTC → nahoru (ne oříznutí)
    ])
    def test_zaokrouhleni_na_sekundy(self, serial_text, expected_second):
        sheets = XtbStatementBuilder().open_lot("1", "TST1.US", "1", _WINTER).sheets()
        opened = sheets["Open Positions"]
        i = next(n for n, r in enumerate(opened) if r and r[1] == "1")
        row = list(opened[i])
        row[9] = Num(serial_text)
        opened[i] = tuple(row)
        lot = parse_xtb_open_positions(read_xlsx(build_xlsx(sheets)), "x.xlsx").lots[0]
        assert lot.open_time_local == datetime(2025, 12, 9, 13, 0, expected_second)

    def test_neplatny_cas(self):
        sheets = XtbStatementBuilder().open_lot("1", "TST1.US", "1", _WINTER).sheets()
        opened = sheets["Open Positions"]
        i = next(n for n, r in enumerate(opened) if r and r[1] == "1")
        row = list(opened[i])
        row[9] = "yesterday"
        opened[i] = tuple(row)
        assert "Open time (UTC)" in _error(XtbStatementBuilder(), sheets)


# ── měna účtu ─────────────────────────────────────────────────────────────────

class TestCurrency:
    def test_eur(self):
        assert _parse(_basic()).currency == "EUR"

    def test_jina_mena_odmitnuta(self):
        msg = _error(XtbStatementBuilder(currency="USD").open_lot("1", "TST1.US", "1", _WINTER))
        assert "jen EUR" in msg and "USD" in msg

    def test_nazev_a_souhrn_se_lisi(self):
        b = XtbStatementBuilder(currency="EUR", summary_currency="USD")
        assert "není jednoznačná" in _error(b)

    def test_prejmenovany_soubor_mena_ze_souhrnu(self):
        assert _parse(_basic(), name="muj_vypis.xlsx").currency == "EUR"

    def test_prejmenovany_soubor_jina_mena_ze_souhrnu(self):
        b = XtbStatementBuilder(summary_currency="CZK")
        assert "jen EUR" in _error(b, name="muj_vypis.xlsx")

    def test_mena_nelze_urcit(self):
        sheets = _basic().sheets()
        sheets["Open Positions"] = [r for r in sheets["Open Positions"] if not (r and r[0] == "My Trades"
                                                                               and len(r) == 4)]
        assert "Nelze určit měnu" in _error(_basic(), sheets, name="muj_vypis.xlsx")

    def test_mena_se_kontroluje_pred_loty(self):
        b = XtbStatementBuilder(currency="USD").open_lot("1", "TST1.US", "abc", _WINTER)   # neplatný objem
        assert "jen EUR" in _error(b)


# ── číslo účtu ────────────────────────────────────────────────────────────────

class TestNoAccountNumber:
    def test_snapshot_neobsahuje_cislo_uctu(self, tmp_path):
        snap = load_xtb_open_positions(_basic().write_zip(tmp_path))
        assert FAKE_ACCOUNT not in repr(snap)
