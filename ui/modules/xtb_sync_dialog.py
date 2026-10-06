"""XTB Sync dialog — porovnání otevřených pozic z XTB výpisu s ledgerem (XTB sync, M4).

Postup: Vybrat soubor… (systémový dialog Otevřít, jen .xlsx/.zip) → Porovnat →
výběr MISSING tickerů → potvrzení se seznamem BUY → import → výsledek
(importováno / odmítnuto) → automatické nové porovnání a obnovení portfolia a ledgeru.

Ruční zadání cesty je jen záložní: zobrazí se, pokud systémový dialog selže.
Nic se neimportuje bez potvrzení. Číslo účtu se nezobrazuje (v zobrazeném názvu
souboru se dlouhé řady číslic maskují) ani neukládá; cesta se drží jen v otevřeném dialogu.
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Callable, Dict, Iterable, List, Optional, Tuple

import flet as ft

from core.services.position_sync import (
    CANNOT_IMPORT,
    DIFFERENT,
    LEDGER_ONLY,
    MATCHED,
    MISSING,
    SYNC_STATES,
    SyncReport,
    TickerSync,
)
from core.services.ui_facade import get_export_dir, get_xtb_sync_report, import_xtb_missing
from ui.modules.add_trade_dialog import _card, _close_modal, _set_st, _show_modal, _status

ALLOWED_EXTENSIONS = (".zip", ".xlsx")

STATE_COLORS = {
    MATCHED: ft.Colors.GREEN_400,
    MISSING: ft.Colors.BLUE_300,
    CANNOT_IMPORT: ft.Colors.ORANGE_300,
    DIFFERENT: ft.Colors.RED_300,
    LEDGER_ONLY: ft.Colors.GREY_400,
}

# (záhlaví, šířka) sloupců tabulky; první sloupec = checkbox
_COLUMNS = [("", 40), ("Ticker", 100), ("Stav", 120), ("XTB ks", 90), ("Ledger ks", 90),
            ("Loty XTB/BUY", 100), ("Náklad XTB / ledger EUR", 170), ("Důvod / diagnostika", 300)]


# ── čisté pomocné funkce ──────────────────────────────────────────────────────

def _validate_path(text: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    """Cesta k výpisu: (cesta, None) nebo (None, chyba). Odstraní uvozovky z 'Kopírovat jako cestu'."""
    path = (text or "").strip().strip('"').strip()
    if not path:
        return None, "Zadej cestu k XTB výpisu (.zip nebo .xlsx)."
    if not path.lower().endswith(ALLOWED_EXTENSIONS):
        return None, "Výpis musí být soubor .zip nebo .xlsx."
    if not os.path.isfile(path):
        return None, "Soubor na zadané cestě neexistuje."
    return path, None


def _display_path(path: str) -> str:
    """Zobrazitelný název vybraného souboru: dlouhé řady číslic (číslo účtu) se maskují."""
    mask = lambda s: re.sub(r"\d{6,}", "••••", s)
    folder, name = os.path.split(path)
    return f"{mask(name)}  (složka: {mask(folder)})" if folder else mask(name)


def _fmt_qty(value: Optional[Decimal]) -> str:
    if value is None:
        return "—"
    return format(value.normalize(), "f")


def _fmt_eur(value: Optional[Decimal]) -> str:
    """Zobrazení nákladu na centy (jen pro zobrazení; data se nemění)."""
    if value is None:
        return "—"
    return f"{value.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP):,.2f}".replace(",", " ")


def _fmt_time(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def _reason_text(item: TickerSync) -> str:
    if item.state == MISSING:
        return f"Lze importovat ({item.xtb_lot_count} lot/y)."
    if item.state == CANNOT_IMPORT:
        blocking = item.blocking_lots
        first = blocking[0].detail or blocking[0].reason or "neznámý důvod"
        more = f" (+{len(blocking) - 1} další lot/y)" if len(blocking) > 1 else ""
        return f"{first}{more}"
    if item.state == DIFFERENT:
        diff = item.xtb_quantity - item.ledger_quantity
        sign = "+" if diff > 0 else ""
        return f"Množství se liší: XTB − ledger = {sign}{_fmt_qty(diff)} ks."
    if item.state == LEDGER_ONLY:
        return "V otevřených pozicích XTB není."
    notes = []
    if item.xtb_lot_count != item.ledger_buy_count:
        notes.append(f"počet lotů {item.xtb_lot_count} vs BUY {item.ledger_buy_count}")
    if item.xtb_open_cost is not None and item.ledger_cost_basis is not None \
            and abs(item.xtb_open_cost - item.ledger_cost_basis) > Decimal("0.01"):
        notes.append("náklad se liší")
    return ("Shoda (diagnostika: " + ", ".join(notes) + ")") if notes else "Shoda."


def _summary_text(report: SyncReport) -> str:
    counts = report.counts()
    parts = " · ".join(f"{state} {counts[state]}" for state in SYNC_STATES)
    return (f"Měna účtu: {report.currency} · Stav výpisu k: {_fmt_time(report.as_of_local)}\n"
            f"{parts} · vyloučeno (nepodporované kategorie): {report.excluded_count}")


def _planned_buys(report: SyncReport, tickers: Iterable[str]) -> List[Tuple[str, datetime, Decimal, Decimal]]:
    """BUY, které vzniknou: (ticker, datum, množství, EUR náklad) — jen vybrané MISSING tickery."""
    wanted = set(tickers)
    result = []
    for item in report.items:
        if item.ticker not in wanted or item.state != MISSING:
            continue
        for rec in item.lots:
            result.append((item.ticker, rec.lot.open_time_local, rec.lot.volume, rec.cost))
    return result


def _import_result_text(imported: Dict[str, int], rejected: Dict[str, str]) -> str:
    lines = []
    if imported:
        lines.append("Importováno: " + ", ".join(f"{t} ({n} lot/y)" for t, n in sorted(imported.items())))
    if rejected:
        lines.append("Odmítnuto:")
        lines += [f"  {t}: {reason}" for t, reason in sorted(rejected.items())]
    return "\n".join(lines) if lines else "Nic nebylo importováno."


# ── dialog ────────────────────────────────────────────────────────────────────

def open_xtb_sync_dialog(page: ft.Page, db_path: str, on_after_change: Callable[[], None]) -> None:
    modal: list = [None]
    confirm_modal: list = [None]
    state: dict = {"report": None, "path": None, "file": None, "selected": set()}

    picker = ft.FilePicker()   # systémový dialog Otevřít; reference drží službu naživu
    pick_btn = ft.ElevatedButton("Vybrat soubor…", icon=ft.Icons.FOLDER_OPEN, data="pick")
    selected_txt = ft.Text("Není vybrán žádný soubor.", size=13, selectable=True, data="selected")
    compare_btn = ft.ElevatedButton("Porovnat", icon=ft.Icons.COMPARE_ARROWS, disabled=True, data="compare")

    app_root = os.path.dirname(get_export_dir())
    path_tf = ft.TextField(   # záložní ruční zadání — viditelné jen když systémový dialog selže
        label="Cesta k XTB výpisu (.zip nebo .xlsx)",
        hint_text=os.path.join(app_root, "imports", "vypis.xlsx"),
        width=720, visible=False, data="path",
    )
    st = _status()
    st.selectable = True
    summary = ft.Text("", size=13, selectable=True, data="summary")
    result = ft.Text("", size=13, selectable=True, data="result")
    table = ft.Column(spacing=0, scroll=ft.ScrollMode.AUTO, height=360, data="table")
    import_btn = ft.ElevatedButton("Importovat vybrané (0)", icon=ft.Icons.DOWNLOAD_DONE,
                                   disabled=True, data="import")

    def _close(_e=None): _close_modal(page, modal[0])

    def _update_import_button() -> None:
        n = len(state["selected"])
        import_btn.content = f"Importovat vybrané ({n})"
        import_btn.disabled = n == 0

    def _on_check(ticker: str) -> Callable:
        def _handler(e) -> None:
            if e.control.value:
                state["selected"].add(ticker)
            else:
                state["selected"].discard(ticker)
            _update_import_button()
            page.update()
        return _handler

    def _cell(text: str, width: int, color=None, bold: bool = False) -> ft.Container:
        return ft.Container(width=width, content=ft.Text(text, size=12, color=color, selectable=True,
                                                         weight=ft.FontWeight.BOLD if bold else None))

    def _render(report: Optional[SyncReport]) -> None:
        table.controls.clear()
        state["selected"].clear()
        _update_import_button()
        if report is None:
            summary.value = ""
            return
        summary.value = _summary_text(report)
        table.controls.append(ft.Row([_cell(h, w, ft.Colors.GREY_400, True) for h, w in _COLUMNS], spacing=4))
        for item in report.items:
            check = (ft.Checkbox(value=False, on_change=_on_check(item.ticker), data=f"check:{item.ticker}")
                     if item.state == MISSING else ft.Container())
            table.controls.append(ft.Row([
                ft.Container(width=_COLUMNS[0][1], content=check),
                _cell(item.ticker, _COLUMNS[1][1], bold=True),
                _cell(item.state, _COLUMNS[2][1], STATE_COLORS.get(item.state)),
                _cell(_fmt_qty(item.xtb_quantity), _COLUMNS[3][1]),
                _cell(_fmt_qty(item.ledger_quantity), _COLUMNS[4][1]),
                _cell(f"{item.xtb_lot_count} / {item.ledger_buy_count}", _COLUMNS[5][1]),
                _cell(f"{_fmt_eur(item.xtb_open_cost)} / {_fmt_eur(item.ledger_cost_basis)}", _COLUMNS[6][1]),
                _cell(_reason_text(item), _COLUMNS[7][1]),
            ], spacing=4, data=f"row:{item.ticker}"))

    def _compare(path: str) -> bool:
        r = get_xtb_sync_report(db_path, path)
        if not r.success:
            state["report"], state["path"] = None, None
            _render(None)
            _set_st(st, r.error_message or "Porovnání selhalo.", True, page)
            return False
        state["report"], state["path"] = r.report, path
        _render(r.report)
        _set_st(st, "Porovnáno. Zatím nic nebylo zapsáno.", False, page)
        return True

    async def _on_pick(_e=None) -> None:
        try:
            files = await picker.pick_files(
                dialog_title="Vyber XTB výpis",
                file_type=ft.FilePickerFileType.CUSTOM,
                allowed_extensions=["xlsx", "zip"],
                allow_multiple=False,
            )
        except Exception as exc:
            path_tf.visible = True
            compare_btn.disabled = False
            _set_st(st, f"Systémový dialog selhal ({type(exc).__name__}). "
                        "Zadej cestu k výpisu ručně a stiskni Porovnat.", True, page)
            return
        if not files:                         # zrušeno — beze změny, bez chyby
            return
        path, err = _validate_path(files[0].path)
        if err:
            _set_st(st, err if files[0].path else "Systém nevrátil cestu k souboru.", True, page)
            return
        state["file"] = path
        state["report"], state["path"] = None, None
        _render(None)
        result.value = ""
        selected_txt.value = f"Vybraný soubor: {_display_path(path)}"
        compare_btn.disabled = False
        _set_st(st, "Soubor vybrán. Stiskni Porovnat.", False, page)

    def _on_compare(_e=None) -> None:
        result.value = ""
        source = state["file"] if state["file"] and not path_tf.visible else path_tf.value
        path, err = _validate_path(source)
        if err:
            state["report"], state["path"] = None, None
            _render(None)
            _set_st(st, err, True, page)
            return
        _compare(path)

    def _do_import(_e=None) -> None:
        _close_modal(page, confirm_modal[0])
        tickers = sorted(state["selected"])
        path = state["path"]
        r = import_xtb_missing(db_path, path, tickers)
        if not r.success:
            result.value = ""
            _set_st(st, r.error_message or "Import selhal.", True, page)
            return
        _compare(path)                       # nové porovnání proti aktuální DB
        result.value = _import_result_text(r.imported, r.rejected)
        result.color = ft.Colors.RED_300 if r.rejected else ft.Colors.GREEN_400
        if r.imported:
            on_after_change()
        page.update()

    def _on_import(_e=None) -> None:
        report = state["report"]
        if report is None or not state["selected"]:
            return
        buys = _planned_buys(report, state["selected"])
        lines = [ft.Row([_cell(h, w, ft.Colors.GREY_400, True) for h, w in
                         (("Ticker", 100), ("Datum", 160), ("Množství", 100), ("Náklad EUR", 110))], spacing=4)]
        lines += [ft.Row([_cell(t, 100, bold=True), _cell(_fmt_time(d), 160), _cell(_fmt_qty(q), 100),
                          _cell(_fmt_eur(c), 110)], spacing=4, data="buy") for t, d, q, c in buys]
        confirm = _card(ft.Column([
            ft.Text("Potvrdit import", size=17, weight=ft.FontWeight.BOLD, color=ft.Colors.BLUE_300),
            ft.Text(f"Vytvoří se {len(buys)} BUY (akciová + EUR noha, bez poplatků, "
                    "poznámka 'XTB position …'):", size=13),
            ft.Column(lines, spacing=2, scroll=ft.ScrollMode.AUTO, height=min(300, 40 + 26 * len(buys))),
            ft.Row([
                ft.TextButton("Zpět", on_click=lambda _e: _close_modal(page, confirm_modal[0]), data="back"),
                ft.ElevatedButton("Importovat", icon=ft.Icons.DOWNLOAD_DONE, on_click=_do_import,
                                  data="confirm"),
            ], alignment=ft.MainAxisAlignment.END),
        ], spacing=12, tight=True), width=560)
        confirm_modal[0] = _show_modal(page, confirm)

    import_btn.on_click = _on_import
    pick_btn.on_click = _on_pick
    compare_btn.on_click = _on_compare

    card = _card(ft.Column([
        ft.Row([
            ft.Text("XTB Sync — otevřené pozice", size=17, weight=ft.FontWeight.BOLD,
                    color=ft.Colors.BLUE_300),
            ft.IconButton(ft.Icons.CLOSE, on_click=_close, icon_size=20),
        ], alignment=ft.MainAxisAlignment.SPACE_BETWEEN),
        ft.Divider(height=1),
        ft.Row([pick_btn, compare_btn, selected_txt], spacing=12),
        path_tf,
        st, summary, table, result,
        ft.Row([ft.TextButton("Zavřít", on_click=_close), import_btn], alignment=ft.MainAxisAlignment.END),
    ], spacing=10, tight=True), width=1080)
    modal[0] = _show_modal(page, card)
