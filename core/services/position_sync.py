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

Porovnání (reconstruct_lot, compare_positions) je čisté, bez I/O.
Import (M3, import_missing_tickers) zapisuje jen vybrané MISSING tickery:
každý XTB lot = jedna BUY skupina (akciová + EUR noha, bez FEE) s poznámkou
"XTB position <Position ID>", per ticker atomicky přes insert_group.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_EVEN, Decimal
from typing import Dict, Iterable, List, Optional, Set, Tuple

from core.ledger_store import DuplicateRowError, LedgerStore
from core.model import RawRow
from core.services.holdings_engine import compute_holdings, reversed_trade_ids
from core.services.trade_service import AddTradeInput, build_trade_rows, generate_canonical_id
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

# důvody, proč DIFFERENT nelze automaticky opravit doplněním lotů (M5)
XTB_NOT_GREATER = "XTB_NOT_GREATER"
LEDGER_HAS_SELL = "LEDGER_HAS_SELL"
LEDGER_CHANGED_AFTER_AS_OF = "LEDGER_CHANGED_AFTER_AS_OF"
UNMATCHED_LEDGER_BUY = "UNMATCHED_LEDGER_BUY"
AMBIGUOUS_MATCH = "AMBIGUOUS_MATCH"
MISSING_LOT_NOT_RECONSTRUCTABLE = "MISSING_LOT_NOT_RECONSTRUCTABLE"
ALREADY_IMPORTED = "ALREADY_IMPORTED"
QUANTITY_MISMATCH = "QUANTITY_MISMATCH"

SYNC_VENUE = "xtb"
CENT = Decimal("0.01")
_ZERO = Decimal("0")
IMPORT_NOTE_PREFIX = "XTB position "


@dataclass(frozen=True)
class LedgerBuy:
    """Nestornovaná BUY skupina ledgeru pro párování s XTB loty (M5)."""
    trade_id: str
    quantity: Decimal
    cost: Optional[Decimal]          # −částka EUR nohy; None = skupina bez peněžní nohy
    timestamp: datetime
    note: Optional[str]


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
    # M5 — oprava DIFFERENT doplněním chybějících lotů (vyplněno jen u DIFFERENT)
    repairable: bool = False
    repair_lots: Tuple[LotReconstruction, ...] = ()
    repair_reason: Optional[str] = None
    repair_detail: Optional[str] = None

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
    ledger = _LedgerFacts(xtb_rows, as_of_rows, snapshot.as_of_local)

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

        repair = (plan_repair(ticker, reconstructions, xtb_qty, ledger_qty, ledger)
                  if state == DIFFERENT else _RepairPlan())
        xtb_costs = [r.xtb_open_cost for r in reconstructions]
        dates = buy_dates.get(ticker, ())
        items.append(TickerSync(
            repairable=repair.repairable,
            repair_lots=repair.lots,
            repair_reason=repair.reason,
            repair_detail=repair.detail,
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


# ── oprava DIFFERENT doplněním chybějících lotů (M5) ─────────────────────────

class _LedgerFacts:
    """Fakta z ledgeru (venue xtb) potřebná pro plán opravy."""

    def __init__(self, xtb_rows: List[RawRow], as_of_rows: List[RawRow], as_of: datetime):
        reversed_ids = reversed_trade_ids(xtb_rows)
        active = [r for r in xtb_rows if r.type != "REVERSAL" and r.id not in reversed_ids]
        self.sell_tickers = {r.asset for r in active if r.type == "SELL" and r.asset != r.currency}
        self.after_as_of_tickers = {r.asset for r in active if r.timestamp > as_of}
        self.active_notes = {r.note for r in active if r.note}
        self.buys = _ledger_buy_groups(as_of_rows)


def _ledger_buy_groups(rows: List[RawRow]) -> Dict[str, List[LedgerBuy]]:
    """Ticker -> nestornované BUY skupiny (množství akciové nohy, EUR náklad z peněžní nohy)."""
    reversed_ids = reversed_trade_ids(rows)
    stock: Dict[str, RawRow] = {}
    cash: Dict[str, RawRow] = {}
    for r in rows:
        if r.type != "BUY" or r.id in reversed_ids:
            continue
        (cash if r.asset == r.currency else stock)[r.id] = r
    result: Dict[str, List[LedgerBuy]] = {}
    for trade_id, s in stock.items():
        c = cash.get(trade_id)
        result.setdefault(s.asset, []).append(LedgerBuy(
            trade_id=trade_id, quantity=s.amount, cost=(-c.amount if c is not None else None),
            timestamp=s.timestamp, note=s.note,
        ))
    for buys in result.values():
        buys.sort(key=lambda b: (b.timestamp, b.trade_id))
    return result


@dataclass(frozen=True)
class _RepairPlan:
    repairable: bool = False
    lots: Tuple[LotReconstruction, ...] = ()
    reason: Optional[str] = None
    detail: Optional[str] = None


def plan_repair(ticker: str, lots: Tuple[LotReconstruction, ...], xtb_qty: Decimal, ledger_qty: Decimal,
                ledger: "_LedgerFacts") -> _RepairPlan:
    """Lze DIFFERENT opravit čistě doplněním chybějících XTB lotů? Nikdy nehádá.

    Ledger BUY skupiny se párují s XTB loty 1:1 — podle poznámky "XTB position <id>",
    jinak podle přesného množství a nákladu ±0.01 EUR (Value − Gross Profit, jen k párování);
    datum (stejný kalendářní den) jen rozhoduje remízu. Chybějící = nespárované loty.
    """
    def no(reason: str, detail: str) -> _RepairPlan:
        return _RepairPlan(False, (), reason, detail)

    if xtb_qty <= ledger_qty:
        return no(XTB_NOT_GREATER, "V ledgeru je víc kusů než v XTB — doplnění lotů to nevyřeší.")
    if ticker in ledger.sell_tickers:
        return no(LEDGER_HAS_SELL, "Ledger obsahuje prodej (SELL) tohoto tickeru — automatická oprava není možná.")
    if ticker in ledger.after_as_of_tickers:
        return no(LEDGER_CHANGED_AFTER_AS_OF, "Ledger obsahuje záznam tohoto tickeru po datu výpisu — "
                                              "použijte aktuální výpis.")

    by_id = {r.lot.position_id: r for r in lots}
    paired: Dict[str, str] = {}                     # trade_id -> position_id
    evidence_buys: List[LedgerBuy] = []
    for b in ledger.buys.get(ticker, []):
        if b.note and b.note.startswith(IMPORT_NOTE_PREFIX):
            pid = b.note[len(IMPORT_NOTE_PREFIX):]
            rec = by_id.get(pid)
            if rec is None or pid in paired.values() or rec.lot.volume != b.quantity:
                return no(UNMATCHED_LEDGER_BUY, f"BUY z {b.timestamp:%Y-%m-%d} ({_plain(b.quantity)} ks) "
                                                "nelze spárovat s lotem XTB podle poznámky.")
            paired[b.trade_id] = pid
        else:
            evidence_buys.append(b)

    def candidates(b: LedgerBuy) -> Set[str]:
        if b.cost is None:
            return set()
        return {pid for pid, r in by_id.items()
                if r.lot.volume == b.quantity and r.xtb_open_cost is not None
                and abs(r.xtb_open_cost - b.cost) <= CENT}

    options = {b.trade_id: candidates(b) for b in evidence_buys}
    pending = list(evidence_buys)
    while pending:
        taken = set(paired.values())
        progress = False
        for b in list(pending):
            free = options[b.trade_id] - taken
            if not free:
                return no(UNMATCHED_LEDGER_BUY, f"BUY z {b.timestamp:%Y-%m-%d} ({_plain(b.quantity)} ks, "
                                                f"{b.cost} EUR) nemá odpovídající lot v XTB.")
            if len(free) == 1:
                paired[b.trade_id] = free.pop()
                pending.remove(b)
                taken = set(paired.values())
                progress = True
        if progress:
            continue
        for b in pending:                           # remíza: rozhodne jen stejný kalendářní den
            same_day = {pid for pid in options[b.trade_id] - taken
                        if by_id[pid].lot.open_time_local.date() == b.timestamp.date()}
            if len(same_day) == 1:
                paired[b.trade_id] = same_day.pop()
                pending.remove(b)
                progress = True
                break
        if not progress:
            b = pending[0]
            return no(AMBIGUOUS_MATCH, f"BUY z {b.timestamp:%Y-%m-%d} ({_plain(b.quantity)} ks) odpovídá více "
                                       "lotům XTB — nelze jednoznačně určit chybějící lot.")

    missing = tuple(r for pid, r in by_id.items() if pid not in set(paired.values()))
    if not missing:
        return no(QUANTITY_MISMATCH, "Všechny loty XTB jsou spárované, rozdíl množství nelze vysvětlit.")
    blocked = [r for r in missing if not r.importable]
    if blocked:
        return no(MISSING_LOT_NOT_RECONSTRUCTABLE,
                  f"Chybějící lot nelze bezpečně rekonstruovat: {blocked[0].detail or blocked[0].reason}")
    if any(import_note(r.lot.position_id) in ledger.active_notes for r in missing):
        return no(ALREADY_IMPORTED, "Chybějící lot už byl dříve importován (XTB position v ledgeru).")
    if ledger_qty + sum((r.lot.volume for r in missing), _ZERO) != xtb_qty:
        return no(QUANTITY_MISMATCH, "Doplnění chybějících lotů by nedalo přesně množství z XTB.")
    return _RepairPlan(True, missing)


# ── zápis BUY skupin pro XTB loty (M3 import MISSING, M5 doplnění DIFFERENT) ──

def import_note(position_id: str) -> str:
    """Auditní poznámka importované BUY skupiny (nikdy neobsahuje číslo účtu)."""
    return f"{IMPORT_NOTE_PREFIX}{position_id}"


def build_import_rows(item: TickerSync, currency: str, conn,
                      reserved: Optional[Set[str]] = None) -> List[RawRow]:
    """BUY skupiny pro všechny loty MISSING tickeru: akciová + EUR noha, bez FEE.

    Náklad = rekonstruovaný EUR náklad lotu (nikdy Value − Gross Profit).
    reserved: ID přidělená v této dávce — doplní se o nově přidělená.
    Raises ValueError, pokud ticker není MISSING nebo některý lot není importovatelný.
    """
    if item.state != MISSING:
        raise ValueError(f"{item.ticker}: importovat lze jen stav {MISSING}, ne {item.state}.")
    return _lot_rows(item.ticker, item.lots, currency, conn, reserved)


def build_repair_rows(item: TickerSync, currency: str, conn,
                      reserved: Optional[Set[str]] = None) -> List[RawRow]:
    """BUY skupiny jen pro chybějící loty opravitelného DIFFERENT tickeru (nic jiného).

    Raises ValueError, pokud ticker není opravitelný DIFFERENT nebo by doplnění nedalo přesně XTB množství.
    """
    if item.state != DIFFERENT or not item.repairable or not item.repair_lots:
        raise ValueError(f"{item.ticker}: doplnit lze jen opravitelný stav {DIFFERENT}.")
    added = sum((r.lot.volume for r in item.repair_lots), _ZERO)
    if item.ledger_quantity + added != item.xtb_quantity:          # pojistka proti přeplnění
        raise ValueError(f"{item.ticker}: doplnění by nedalo přesně množství z XTB.")
    return _lot_rows(item.ticker, item.repair_lots, currency, conn, reserved)


def _lot_rows(ticker: str, recs: Tuple[LotReconstruction, ...], currency: str, conn,
              reserved: Optional[Set[str]]) -> List[RawRow]:
    if not recs or not all(r.importable and r.cost is not None for r in recs):
        raise ValueError(f"{ticker}: některý lot nelze bezpečně rekonstruovat.")
    reserved = set() if reserved is None else reserved
    rows: List[RawRow] = []
    for rec in recs:
        lot = rec.lot
        trade_id = generate_canonical_id(lot.open_time_local, SYNC_VENUE, "BUY", conn, reserved)
        reserved.add(trade_id)
        rows += build_trade_rows(
            AddTradeInput(
                type="BUY",
                timestamp=lot.open_time_local,
                base_asset=ticker,
                base_amount=_plain(lot.volume),
                quote_currency=currency,
                quote_amount=rec.cost,
                venue=SYNC_VENUE,
                note=import_note(lot.position_id),
            ),
            trade_id=trade_id,
        )
    return rows


def import_missing_tickers(
    store: LedgerStore,
    snapshot: XtbOpenPositionsSnapshot,
    tickers: Iterable[str],
) -> Tuple[Dict[str, int], Dict[str, str]]:
    """Importuje vybrané tickery; vrátí ({ticker: počet lotů}, {ticker: důvod odmítnutí}).

    Před zápisem znovu porovná výpis s aktuálním stavem DB (nevěří starému reportu).
    Zapisuje jen MISSING; každý ticker atomicky (všechny loty, nebo nic).
    """
    imported: Dict[str, int] = {}
    rejected: Dict[str, str] = {}
    rows = store.timeline()
    report = compare_positions(snapshot, rows)
    items = {i.ticker: i for i in report.items}

    reversed_ids = reversed_trade_ids(rows)
    active_notes = {r.note for r in rows if r.type != "REVERSAL" and r.id not in reversed_ids and r.note}
    after_as_of = {r.asset for r in rows if r.venue == SYNC_VENUE and r.type != "REVERSAL"
                   and r.timestamp > snapshot.as_of_local}

    reserved: Set[str] = set()
    for ticker in dict.fromkeys(t.strip().upper() for t in tickers if t and t.strip()):
        item = items.get(ticker)
        if item is None or not item.lots:
            rejected[ticker] = "Ticker není mezi otevřenými pozicemi ve výpisu."
            continue
        if item.state != MISSING:
            rejected[ticker] = f"Stav {item.state} — importovat lze jen {MISSING}."
            continue
        if any(import_note(r.lot.position_id) in active_notes for r in item.lots):
            rejected[ticker] = "Lot tohoto tickeru už byl dříve importován (XTB position v ledgeru)."
            continue
        if ticker in after_as_of:
            rejected[ticker] = ("Ledger obsahuje záznam tohoto tickeru po datu výpisu — "
                                "použijte aktuální výpis.")
            continue
        try:
            group = build_import_rows(item, snapshot.currency, store.conn, reserved)
            store.insert_group(group)
        except (ValueError, DuplicateRowError) as exc:
            rejected[ticker] = str(exc)
            continue
        imported[ticker] = len(item.lots)
    return imported, rejected


def repair_different_tickers(
    store: LedgerStore,
    snapshot: XtbOpenPositionsSnapshot,
    tickers: Iterable[str],
) -> Tuple[Dict[str, int], Dict[str, str]]:
    """Doplní chybějící loty vybraných opravitelných DIFFERENT tickerů (M5).

    Plán opravy se znovu spočítá proti aktuální DB těsně před zápisem.
    Pouze přidává BUY skupiny chybějících lotů — existující řádky nikdy nemění ani nemaže.
    Každý ticker atomicky. Vrátí ({ticker: počet doplněných lotů}, {ticker: důvod odmítnutí}).
    """
    repaired: Dict[str, int] = {}
    rejected: Dict[str, str] = {}
    items = {i.ticker: i for i in compare_positions(snapshot, store.timeline()).items}

    reserved: Set[str] = set()
    for ticker in dict.fromkeys(t.strip().upper() for t in tickers if t and t.strip()):
        item = items.get(ticker)
        if item is None or not item.lots:
            rejected[ticker] = "Ticker není mezi otevřenými pozicemi ve výpisu."
            continue
        if item.state != DIFFERENT:
            rejected[ticker] = f"Stav {item.state} — doplnit chybějící loty lze jen u {DIFFERENT}."
            continue
        if not item.repairable:
            rejected[ticker] = f"Nelze automaticky doplnit: {item.repair_detail}"
            continue
        try:
            store.insert_group(build_repair_rows(item, snapshot.currency, store.conn, reserved))
        except (ValueError, DuplicateRowError) as exc:
            rejected[ticker] = str(exc)
            continue
        repaired[ticker] = len(item.repair_lots)
    return repaired, rejected


def _plain(value: Decimal) -> Decimal:
    """Bez zbytečných nul a bez exponentu (10.0 → 10, ne 1E+1)."""
    return Decimal(format(value.normalize(), "f"))
