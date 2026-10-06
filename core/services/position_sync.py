"""XTB sync — čistý porovnávací engine (M2).

Porovná otevřené pozice z XTB výpisu (XtbOpenPositionsSnapshot) s otevřenými
pozicemi v ledgeru. Jednotkou synchronizace je ticker, ne XTB lot.

Stavy:
    MATCHED        ticker v XTB i v ledgeru, celkové otevřené množství je přesně stejné
    MISSING        ticker v XTB, v ledgeru množství 0, všechny loty bezpečně rekonstruovatelné
    CANNOT_IMPORT  ticker v XTB, v ledgeru množství 0, aspoň jeden lot nelze bezpečně rekonstruovat
    DIFFERENT      ticker v obou, celkové množství se liší (jen zobrazení)
    LEDGER_ONLY    ticker v ledgeru, v otevřených pozicích XTB chybí (jen zobrazení)

Počet lotů, data, Position ID a porovnání nákladů jsou jen diagnostika — stav
MATCHED/DIFFERENT neovlivní; rekonstrukce lotů rozhoduje jen mezi MISSING a CANNOT_IMPORT.

Ledger: jen venue "xtb" a řádky s timestamp ≤ as_of výpisu. Storna (REVERSAL)
platí bez ohledu na datum — jde o opravu chybného zápisu, ne o ekonomickou událost.

Žádné I/O, žádné zápisy do DB.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Dict, List, Optional, Tuple

from core.model import RawRow
from core.services.holdings_engine import compute_holdings, reversed_trade_ids
from io_module.xtb_statement import XtbOpenLot, XtbOpenPositionsSnapshot

MATCHED = "MATCHED"
MISSING = "MISSING"
CANNOT_IMPORT = "CANNOT_IMPORT"
DIFFERENT = "DIFFERENT"
LEDGER_ONLY = "LEDGER_ONLY"
SYNC_STATES = (MATCHED, MISSING, CANNOT_IMPORT, DIFFERENT, LEDGER_ONLY)

# důvody, proč lot nelze bezpečně rekonstruovat
NO_PURCHASE = "NO_PURCHASE"
UNPARSED_COMMENT = "UNPARSED_COMMENT"
TICKER_MISMATCH = "TICKER_MISMATCH"
INVALID_PURCHASE_AMOUNT = "INVALID_PURCHASE_AMOUNT"
MERGED_POSITION = "MERGED_POSITION"
VOLUME_EXCEEDS_PURCHASE = "VOLUME_EXCEEDS_PURCHASE"
COST_NOT_RECONCILED = "COST_NOT_RECONCILED"

SYNC_VENUE = "xtb"
CENT = Decimal("0.01")
_ZERO = Decimal("0")


@dataclass(frozen=True)
class LotReconstruction:
    lot: XtbOpenLot
    importable: bool
    cost: Optional[Decimal]            # rekonstruovaný EUR náklad otevřeného lotu (jen když importable)
    xtb_open_cost: Optional[Decimal]   # Value − Gross Profit: jen kontrola, nikdy zdroj nákladu
    reason: Optional[str] = None       # kód důvodu, když lot nelze importovat
    detail: Optional[str] = None       # srozumitelný popis důvodu


@dataclass(frozen=True)
class TickerSync:
    ticker: str
    state: str
    xtb_quantity: Decimal
    ledger_quantity: Decimal
    lots: Tuple[LotReconstruction, ...]          # XTB loty (u MISSING podklad importu, jinak diagnostika)
    ledger_buy_count: int                        # diagnostika
    ledger_buy_dates: Tuple[datetime, ...]       # diagnostika
    xtb_open_cost: Optional[Decimal]             # diagnostika: Σ (Value − Gross Profit)
    ledger_cost_basis: Optional[Decimal]         # diagnostika

    @property
    def xtb_lot_count(self) -> int:
        return len(self.lots)

    @property
    def blocking_lots(self) -> Tuple[LotReconstruction, ...]:
        return tuple(r for r in self.lots if not r.importable)


@dataclass(frozen=True)
class SyncReport:
    currency: str
    as_of_local: datetime
    items: Tuple[TickerSync, ...]                # seřazeno podle tickeru
    excluded_count: int

    def counts(self) -> Dict[str, int]:
        result = {state: 0 for state in SYNC_STATES}
        for item in self.items:
            result[item.state] += 1
        return result


# ── rekonstrukce lotu ─────────────────────────────────────────────────────────

def reconstruct_lot(lot: XtbOpenLot) -> LotReconstruction:
    """EUR náklad otevřeného lotu z nákupů "Stock purchase" navázaných přes Position ID.

    Nikdy nehádá: co nelze bezpečně rekonstruovat, vrátí jako neimportovatelné s důvodem.
    """
    xtb_cost = (lot.value - lot.gross_profit
                if lot.value is not None and lot.gross_profit is not None else None)

    def fail(reason: str, detail: str) -> LotReconstruction:
        return LotReconstruction(lot, False, None, xtb_cost, reason, detail)

    purchases = lot.purchases
    if not purchases:
        return fail(NO_PURCHASE, "Nákup lotu není ve výpisu — použijte výpis od dřívějšího data.")
    if any(p.quantity is None for p in purchases):
        return fail(UNPARSED_COMMENT, "Komentář nákupu nelze rozpoznat (očekáváno 'OPEN BUY a[/b] @ cena').")
    if any(p.ticker.upper() != lot.ticker.upper() for p in purchases):
        return fail(TICKER_MISMATCH, "Nákup navázaný přes Position ID má jiný ticker než lot.")
    if any(p.amount >= _ZERO for p in purchases):
        return fail(INVALID_PURCHASE_AMOUNT, "Částka nákupu není platba (očekávána záporná částka).")

    bought = sum((p.quantity for p in purchases), _ZERO)                 # Q
    paid = -sum((p.amount for p in purchases), _ZERO)                    # C
    reported = max(p.reported_total for p in purchases)                  # B
    if reported > bought:
        return fail(MERGED_POSITION,
                    f"Sloučená pozice: XTB hlásí {reported} ks, navázané nákupy pokrývají jen {bought} ks.")
    if lot.volume > bought:
        return fail(VOLUME_EXCEEDS_PURCHASE,
                    f"Otevřený objem {lot.volume} převyšuje nakoupené množství {bought}.")

    if lot.volume == bought:
        cost = paid
    else:
        cost = (paid * lot.volume / bought).quantize(CENT, rounding=ROUND_HALF_EVEN)

    if xtb_cost is None:
        return fail(COST_NOT_RECONCILED, "Náklad nelze ověřit: ve výpisu chybí Value nebo Gross Profit.")
    if abs(cost - xtb_cost) > CENT:
        return fail(COST_NOT_RECONCILED,
                    f"Rekonstruovaný náklad {cost} EUR nesouhlasí s XTB ({xtb_cost} EUR).")
    return LotReconstruction(lot, True, cost, xtb_cost)


# ── porovnání ─────────────────────────────────────────────────────────────────

def compare_positions(snapshot: XtbOpenPositionsSnapshot, ledger_rows: List[RawRow]) -> SyncReport:
    """Porovná XTB otevřené pozice s ledgerem k datu as_of výpisu. Čistá funkce."""
    xtb_rows = [r for r in ledger_rows if r.venue == SYNC_VENUE]
    as_of_rows = [r for r in xtb_rows if r.timestamp <= snapshot.as_of_local or r.type == "REVERSAL"]
    holdings = {h.ticker: h for h in compute_holdings(as_of_rows)}
    buy_dates = _ledger_buy_dates(as_of_rows)

    lots_by_ticker: Dict[str, List[XtbOpenLot]] = {}
    for lot in snapshot.lots:
        lots_by_ticker.setdefault(lot.ticker.upper(), []).append(lot)

    items: List[TickerSync] = []
    for ticker in sorted(set(lots_by_ticker) | set(holdings)):
        reconstructions = tuple(reconstruct_lot(lot) for lot in lots_by_ticker.get(ticker, ()))
        xtb_qty = sum((r.lot.volume for r in reconstructions), _ZERO)
        holding = holdings.get(ticker)
        ledger_qty = holding.quantity if holding else _ZERO

        if not reconstructions:
            state = LEDGER_ONLY
        elif ledger_qty == _ZERO:
            state = MISSING if all(r.importable for r in reconstructions) else CANNOT_IMPORT
        elif xtb_qty == ledger_qty:
            state = MATCHED
        else:
            state = DIFFERENT

        xtb_costs = [r.xtb_open_cost for r in reconstructions]
        dates = buy_dates.get(ticker, ())
        items.append(TickerSync(
            ticker=ticker,
            state=state,
            xtb_quantity=xtb_qty,
            ledger_quantity=ledger_qty,
            lots=reconstructions,
            ledger_buy_count=len(dates),
            ledger_buy_dates=dates,
            xtb_open_cost=(sum(xtb_costs, _ZERO) if xtb_costs and None not in xtb_costs else None),
            ledger_cost_basis=holding.cost_basis if holding else None,
        ))

    return SyncReport(
        currency=snapshot.currency,
        as_of_local=snapshot.as_of_local,
        items=tuple(items),
        excluded_count=snapshot.excluded_count,
    )


def _ledger_buy_dates(rows: List[RawRow]) -> Dict[str, Tuple[datetime, ...]]:
    """Ticker -> data nestornovaných BUY skupin (diagnostika)."""
    reversed_ids = reversed_trade_ids(rows)
    seen: Dict[str, Dict[str, datetime]] = {}
    for r in rows:
        if r.type != "BUY" or r.id in reversed_ids or r.asset == r.currency:
            continue
        seen.setdefault(r.asset, {}).setdefault(r.id, r.timestamp)
    return {ticker: tuple(sorted(groups.values())) for ticker, groups in seen.items()}
