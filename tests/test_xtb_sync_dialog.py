"""Testy pro XTB Sync dialog (M4) — čisté helpery a průchod dialogem s mock stránkou.

Pouze dočasné DB (tmp_db) a syntetické výpisy; žádný zápis do živé DB.
"""
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock

import flet as ft
import pytest

from core.ledger_store import LedgerStore
from core.services.position_sync import CANNOT_IMPORT, DIFFERENT, LEDGER_ONLY, MATCHED, MISSING
from core.services.ui_facade import get_xtb_sync_report
from tests.test_position_sync_import import _ledger_buy, _statement
from tests.xtb_fixtures import FAKE_ACCOUNT
from ui.modules.xtb_sync_dialog import (
    _fmt_eur,
    _fmt_qty,
    _import_result_text,
    _planned_buys,
    _reason_text,
    _summary_text,
    _validate_path,
    open_xtb_sync_dialog,
)

_D = Decimal


@pytest.fixture
def setup(tmp_db, tmp_path):
    _ledger_buy(tmp_db, "DDD.US", "4", "40")
    _ledger_buy(tmp_db, "EEE.US", "1", "10")
    _ledger_buy(tmp_db, "FFF.US", "3", "30")
    return tmp_db, _statement().write_xlsx(tmp_path)


def _report(db, path):
    return get_xtb_sync_report(db, path).report


def _item(report, ticker):
    return next(i for i in report.items if i.ticker == ticker)


def _row_count(db):
    store = LedgerStore(db)
    try:
        return store.count()
    finally:
        store.close()


# ── průchod stromem ovládacích prvků ──────────────────────────────────────────

def _walk(control):
    yield control
    content = getattr(control, "content", None)
    if isinstance(content, ft.Control):
        yield from _walk(content)
    for child in getattr(control, "controls", None) or []:
        yield from _walk(child)


def _find(root, data):
    return [c for c in _walk(root) if getattr(c, "data", None) == data]


def _one(root, data):
    found = _find(root, data)
    assert len(found) == 1, (data, len(found))
    return found[0]


def _with_prefix(root, prefix):
    return [c for c in _walk(root) if isinstance(getattr(c, "data", None), str) and c.data.startswith(prefix)]


class _FakePicker:
    """Náhrada systémového dialogu Otevřít (testy nemají GUI)."""
    next_result = []            # list souborů (SimpleNamespace path/name) nebo výjimka
    calls: list = []

    def __init__(self, *a, **kw):
        pass

    async def pick_files(self, **kwargs):
        _FakePicker.calls.append(kwargs)
        if isinstance(_FakePicker.next_result, Exception):
            raise _FakePicker.next_result
        return _FakePicker.next_result


def _picked(path):
    import os
    return [SimpleNamespace(path=path, name=os.path.basename(path) if path else "x.xlsx", size=0)]


class _Dialog:
    def __init__(self, db):
        from unittest.mock import patch
        self.page = MagicMock()
        self.page.overlay = []
        self.refreshed = 0
        _FakePicker.calls = []
        with patch.object(ft, "FilePicker", _FakePicker):
            open_xtb_sync_dialog(self.page, db, self._refresh)
        self.root = self.page.overlay[0]

    def _refresh(self):
        self.refreshed += 1

    def pick(self, result):
        import asyncio
        _FakePicker.next_result = result
        asyncio.run(_one(self.root, "pick").on_click(None))

    def compare(self, path):
        self.pick(_picked(path))
        _one(self.root, "compare").on_click(None)

    def check(self, ticker, value=True):
        box = _one(self.root, f"check:{ticker}")
        box.value = value
        box.on_change(SimpleNamespace(control=box))

    @property
    def import_button(self):
        return _one(self.root, "import")

    @property
    def confirm_dialog(self):
        return self.page.overlay[1] if len(self.page.overlay) > 1 else None

    def texts(self, root=None):
        return [c.value for c in _walk(root or self.root) if isinstance(c, ft.Text) and isinstance(c.value, str)]


# ── čisté helpery ─────────────────────────────────────────────────────────────

class TestHelpers:
    def test_validate_path(self, tmp_path):
        ok = tmp_path / "vypis.XLSX"
        ok.write_bytes(b"x")
        assert _validate_path(f'  "{ok}"  ') == (str(ok), None)     # uvozovky z "Kopírovat jako cestu"
        assert _validate_path("")[1].startswith("Zadej cestu")
        assert "musí být soubor" in _validate_path(str(tmp_path / "a.csv"))[1]
        assert "neexistuje" in _validate_path(str(tmp_path / "chybi.zip"))[1]

    def test_formatovani(self):
        assert _fmt_qty(_D("10.0")) == "10" and _fmt_qty(_D("0.9232")) == "0.9232" and _fmt_qty(None) == "—"
        assert _fmt_eur(_D("53.33000000000000000000000001")) == "53.33"
        assert _fmt_eur(_D("1234.5")) == "1 234.50" and _fmt_eur(None) == "—"

    def test_duvody_podle_stavu(self, setup):
        rep = _report(*setup)
        assert "Lze importovat (2" in _reason_text(_item(rep, "BBB.US"))
        assert "Sloučená pozice" in _reason_text(_item(rep, "CCC.US"))
        assert "+1 ks" in _reason_text(_item(rep, "DDD.US"))
        assert _reason_text(_item(rep, "EEE.US")) == "Shoda."
        assert "není" in _reason_text(_item(rep, "FFF.US"))

    def test_shrnuti(self, setup):
        text = _summary_text(_report(*setup))
        assert "Měna účtu: EUR" in text and "2026-10-06 16:57:52" in text
        for part in ("MATCHED 1", "MISSING 2", "CANNOT_IMPORT 1", "DIFFERENT 1", "LEDGER_ONLY 1", "vyloučeno"):
            assert part in text

    def test_planovane_buy_jen_vybrane_missing(self, setup):
        rep = _report(*setup)
        buys = _planned_buys(rep, ["BBB.US", "CCC.US", "EEE.US"])
        assert [(t, d, q, c) for t, d, q, c in buys] == [
            ("BBB.US", datetime(2026, 3, 2, 10, 0, 0), _D("1.0"), _D("33.33")),
            ("BBB.US", datetime(2026, 3, 2, 10, 0, 0), _D("2.0"), _D("20.00")),
        ]

    def test_text_vysledku(self):
        text = _import_result_text({"AAA.US": 1}, {"CCC.US": "Stav CANNOT_IMPORT"})
        assert "Importováno: AAA.US (1 lot/y)" in text and "Odmítnuto:" in text and "CCC.US" in text
        assert _import_result_text({}, {}) == "Nic nebylo importováno."


# ── dialog ────────────────────────────────────────────────────────────────────

class TestDialog:
    def test_otevreni_nic_nezobrazi_ani_nezapise(self, setup):
        db, _ = setup
        n = _row_count(db)
        d = _Dialog(db)
        assert d.import_button.disabled is True
        assert _one(d.root, "table").controls == []
        assert _row_count(db) == n

    def test_vybrany_soubor_neexistuje(self, setup, tmp_path):
        db, _ = setup
        d = _Dialog(db)
        d.pick(_picked(str(tmp_path / "chybi.xlsx")))
        assert any("neexistuje" in t for t in d.texts())
        assert _one(d.root, "compare").disabled is True
        assert _one(d.root, "table").controls == []

    def test_chybny_vypis_bez_cisla_uctu(self, setup, tmp_path):
        db, _ = setup
        bad = tmp_path / f"EUR_{FAKE_ACCOUNT}_2006-01-01_2026-10-06.xlsx"
        bad.write_bytes(b"not a zip")
        d = _Dialog(db)
        d.compare(str(bad))
        assert any("není ZIP ani XLSX" in t for t in d.texts())
        assert not any(FAKE_ACCOUNT in t for t in d.texts() if t != str(bad))

    def test_porovnani_tabulka_a_checkboxy_jen_missing(self, setup):
        db, path = setup
        n = _row_count(db)
        d = _Dialog(db)
        d.compare(path)
        rows = _with_prefix(d.root, "row:")
        assert [r.data for r in rows] == [f"row:{t}" for t in
                                          ("AAA.US", "BBB.US", "CCC.US", "DDD.US", "EEE.US", "FFF.US")]
        assert sorted(c.data for c in _with_prefix(d.root, "check:")) == ["check:AAA.US", "check:BBB.US"]
        assert "MISSING 2" in _one(d.root, "summary").value
        assert d.import_button.disabled is True
        assert _row_count(db) == n                                   # porovnání nic nezapíše

    def test_vyber_povoli_tlacitko(self, setup):
        db, path = setup
        d = _Dialog(db)
        d.compare(path)
        d.check("AAA.US")
        assert d.import_button.disabled is False and d.import_button.content == "Importovat vybrané (1)"
        d.check("AAA.US", False)
        assert d.import_button.disabled is True

    def test_potvrzeni_zobrazi_presne_buy_a_zpet_nic_nezapise(self, setup):
        db, path = setup
        n = _row_count(db)
        d = _Dialog(db)
        d.compare(path)
        d.check("BBB.US")
        d.import_button.on_click(None)
        confirm = d.confirm_dialog
        assert confirm is not None
        buys = _find(confirm, "buy")
        assert len(buys) == 2
        texts = d.texts(confirm)
        assert "2026-03-02 10:00:00" in texts and "33.33" in texts and "20.00" in texts
        _one(confirm, "back").on_click(None)
        assert len(d.page.overlay) == 1 and _row_count(db) == n

    def test_import_vysledek_nove_porovnani_a_refresh(self, setup):
        db, path = setup
        n = _row_count(db)
        d = _Dialog(db)
        d.compare(path)
        d.check("AAA.US")
        d.check("BBB.US")
        d.import_button.on_click(None)
        _one(d.confirm_dialog, "confirm").on_click(None)

        assert _row_count(db) == n + 6
        assert len(d.page.overlay) == 1                              # potvrzení zavřeno
        assert "Importováno: AAA.US (1 lot/y), BBB.US (2 lot/y)" in _one(d.root, "result").value
        assert d.refreshed == 1                                      # portfolio + ledger obnoveny
        rep_states = _one(d.root, "summary").value
        assert "MATCHED 3" in rep_states and "MISSING 0" in rep_states   # automaticky znovu porovnáno
        assert _with_prefix(d.root, "check:") == []
        assert d.import_button.disabled is True

    def test_odmitnute_tickery_zobrazeny_zvlast(self, setup):
        db, path = setup
        d = _Dialog(db)
        d.compare(path)
        d.check("AAA.US")
        _ledger_buy(db, "AAA.US", "10", "200")                       # mezitím zadáno ručně → už není MISSING
        d.import_button.on_click(None)
        _one(d.confirm_dialog, "confirm").on_click(None)
        text = _one(d.root, "result").value
        assert "Odmítnuto:" in text and "AAA.US" in text and "Importováno" not in text
        assert d.refreshed == 0

    def test_novy_vyber_souboru_zrusi_predchozi_porovnani(self, setup):
        db, path = setup
        d = _Dialog(db)
        d.compare(path)
        d.check("AAA.US")
        d.pick(_picked(path))                                       # znovu vybrán soubor
        assert _one(d.root, "table").controls == [] and _one(d.root, "summary").value == ""
        assert d.import_button.disabled is True


# ── výběr souboru: systémový dialog Otevřít ───────────────────────────────────

class TestFilePick:
    def test_vychozi_stav(self, setup):
        d = _Dialog(setup[0])
        assert _one(d.root, "compare").disabled is True
        assert _one(d.root, "path").visible is False                # ruční cesta jen jako záloha
        assert _one(d.root, "selected").value == "Není vybrán žádný soubor."

    def test_parametry_dialogu(self, setup):
        d = _Dialog(setup[0])
        d.pick([])
        kw = _FakePicker.calls[0]
        assert kw["file_type"] == ft.FilePickerFileType.CUSTOM
        assert kw["allowed_extensions"] == ["xlsx", "zip"] and kw["allow_multiple"] is False

    def test_zruseni_bez_zmeny_a_bez_chyby(self, setup):
        d = _Dialog(setup[0])
        d.pick([])
        assert _one(d.root, "compare").disabled is True
        assert _one(d.root, "selected").value == "Není vybrán žádný soubor."
        assert not any("selhal" in t or "neexistuje" in t for t in d.texts())

    def test_vyber_zobrazi_soubor_bez_cisla_uctu(self, setup):
        db, path = setup                                            # název obsahuje FAKE_ACCOUNT
        d = _Dialog(db)
        d.pick(_picked(path))
        shown = _one(d.root, "selected").value
        assert shown.startswith("Vybraný soubor: EUR_••••_2006-01-01_2026-10-06.xlsx")
        assert FAKE_ACCOUNT not in shown
        assert _one(d.root, "compare").disabled is False

    def test_system_nevratil_cestu(self, setup):
        d = _Dialog(setup[0])
        d.pick([SimpleNamespace(path=None, name="x.xlsx", size=0)])
        assert any("nevrátil cestu" in t for t in d.texts())
        assert _one(d.root, "compare").disabled is True

    def test_nepovolena_pripona(self, setup, tmp_path):
        bad = tmp_path / "vypis.csv"
        bad.write_text("x")
        d = _Dialog(setup[0])
        d.pick(_picked(str(bad)))
        assert any("musí být soubor .zip nebo .xlsx" in t for t in d.texts())

    def test_selhani_dialogu_zobrazi_zalozni_rucni_cestu(self, setup):
        db, path = setup
        d = _Dialog(db)
        d.pick(RuntimeError("picker unavailable"))
        assert _one(d.root, "path").visible is True
        assert any("Systémový dialog selhal" in t for t in d.texts())
        _one(d.root, "path").value = f'"{path}"'                     # záloha: ruční cesta
        _one(d.root, "compare").on_click(None)
        assert "MISSING 2" in _one(d.root, "summary").value

    def test_display_path_maskuje_cislice(self):
        from ui.modules.xtb_sync_dialog import _display_path
        shown = _display_path(r"C:\Users\x\XTB_VÝPISY\EUR_12345678_2006-01-01_2026-10-06.xlsx")
        assert "12345678" not in shown and "EUR_••••_2006-01-01_2026-10-06.xlsx" in shown
        assert "XTB_VÝPISY" in shown


# ── M5: doplnění chybějících lotů u DIFFERENT v dialogu ───────────────────────

from tests.test_position_sync_import import _repair_statement  # noqa: E402


@pytest.fixture
def repair_setup(tmp_db, tmp_path):
    _ledger_buy(tmp_db, "GGG.US", "5", "50.00", ts=datetime(2026, 2, 2, 15, 0, 40))
    _ledger_buy(tmp_db, "HHH.US", "4", "40.00")
    return tmp_db, _repair_statement().write_xlsx(tmp_path)


def _check_box(d, data, value=True):
    box = _one(d.root, data)
    box.value = value
    box.on_change(SimpleNamespace(control=box))


class TestRepairDialog:
    def test_checkbox_jen_u_opravitelneho_different(self, repair_setup):
        db, path = repair_setup
        d = _Dialog(db)
        d.compare(path)
        assert [c.data for c in _with_prefix(d.root, "repair:")] == ["repair:GGG.US"]
        assert [c.data for c in _with_prefix(d.root, "check:")] == ["check:III.US"]   # MISSING zvlášť
        assert _one(d.root, "repair").disabled is True

    def test_duvod_v_tabulce(self, repair_setup):
        db, path = repair_setup
        rep = _report(db, path)
        assert "Lze doplnit chybějící 1 lot/y (+20 ks) → po doplnění 25 ks." == _reason_text(_item(rep, "GGG.US"))
        assert "Nelze automaticky doplnit:" in _reason_text(_item(rep, "HHH.US"))

    def test_oddeleny_vyber_a_tlacitka(self, repair_setup):
        db, path = repair_setup
        d = _Dialog(db)
        d.compare(path)
        _check_box(d, "repair:GGG.US")
        assert _one(d.root, "repair").disabled is False
        assert _one(d.root, "repair").content == "Doplnit chybějící loty (1)"
        assert d.import_button.disabled is True                      # MISSING import se nemíchá
        d.check("III.US")
        assert d.import_button.content == "Importovat vybrané (1)"
        assert _one(d.root, "repair").content == "Doplnit chybějící loty (1)"

    def test_potvrzeni_jen_chybejici_lot_a_zpet(self, repair_setup):
        db, path = repair_setup
        n = _row_count(db)
        d = _Dialog(db)
        d.compare(path)
        _check_box(d, "repair:GGG.US")
        _one(d.root, "repair").on_click(None)
        confirm = d.confirm_dialog
        assert len(_find(confirm, "buy")) == 1
        texts = d.texts(confirm)
        assert "2026-05-04 15:00:00" in texts and "20" in texts and "200.00" in texts
        assert any("PŘIDÁ 1 BUY" in t for t in texts)
        _one(confirm, "back").on_click(None)
        assert _row_count(db) == n

    def test_doplneni_nove_porovnani_a_refresh(self, repair_setup):
        db, path = repair_setup
        n = _row_count(db)
        d = _Dialog(db)
        d.compare(path)
        _check_box(d, "repair:GGG.US")
        _one(d.root, "repair").on_click(None)
        _one(d.confirm_dialog, "confirm").on_click(None)
        assert _row_count(db) == n + 2
        assert "Doplněno: GGG.US (1 lot/y)" in _one(d.root, "result").value
        assert d.refreshed == 1
        assert _states_from_dialog(d)["GGG.US"] == MATCHED
        assert _with_prefix(d.root, "repair:") == [] and _one(d.root, "repair").disabled is True


def _states_from_dialog(d):
    states = {}
    for row in _with_prefix(d.root, "row:"):
        ticker = row.data.split(":", 1)[1]
        texts = [c.value for c in _walk(row) if isinstance(c, ft.Text)]
        states[ticker] = texts[1]                                    # sloupec Stav
    return states
