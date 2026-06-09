"""UI Facade — jediný kontrakt mezi UI a Core (StocksLedger M2).

Public API:
    create_app_context(config_path)   -> AppContextDTO
    create_db(db_path)                -> SimpleResultDTO
    set_db_path(new_db_path)          -> SimpleResultDTO
    add_trade(request, db_path)       -> AddTradeResultDTO
    get_ledger_rows(db_path)          -> list[RawRow]
    delete_trade(db_path, trade_id)   -> SimpleResultDTO
    get_portfolio_snapshot(db_path)   -> PortfolioSnapshotDTO

Typy transakcí:
    BUY / SELL   → double-entry přes trade_service (asset leg + currency leg)
    DIVIDEND     → single row, asset = ticker, amount = kladná dividenda v měně
    FEE / TAX    → single row, asset = měna, amount = kladné (facade přidá mínus)
    CASH_IN      → single row, asset = měna, amount = kladné (přichozí)
    CASH_OUT     → single row, asset = měna, amount = kladné (facade přidá mínus)
    REVERSAL     → přes reverse_trade(), nikoli add_trade()
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import List, Optional

logger = logging.getLogger(__name__)

from core.constants import TRADE_TYPES
from core.ledger_store import LedgerStore
from core.model import RawRow
from core.services.trade_service import (
    AddTradeInput,
    add_trade as _core_add_trade,
    generate_canonical_id,
)

_ZERO = Decimal("0")

# Znaménko amount pro single-row typy (uživatel zadává vždy kladné číslo)
_SINGLE_ROW_SIGN = {
    "DIVIDEND": +1,   # cash přichází
    "FEE":      -1,   # cash odchází
    "TAX":      -1,   # cash odchází
    "CASH_IN":  +1,   # cash přichází
    "CASH_OUT": -1,   # cash odchází
}

_DOUBLE_ENTRY_TYPES = frozenset({"BUY", "SELL"})
_SINGLE_ROW_TYPES   = frozenset(_SINGLE_ROW_SIGN.keys())


# ── DTOs ──────────────────────────────────────────────────────────────────────

@dataclass
class AppContextDTO:
    db_path: str
    version: str = "1.0.0"
    db_state: str = "OK"        # "OK" | "DB_MISSING" | "DB_ERROR"
    error: Optional[str] = None


@dataclass
class SimpleResultDTO:
    success: bool
    error_message: Optional[str] = None


@dataclass
class AddTradeRequestDTO:
    """Vstupní DTO pro add_trade(). Normalizace probíhá uvnitř facade."""

    type: str
    timestamp: datetime
    asset: str                          # ticker pro BUY/SELL/DIVIDEND; měna pro FEE/TAX/CASH
    amount: Decimal                     # vždy kladné; facade přiřadí znaménko
    currency: str                       # měna obchodu
    price: Optional[Decimal]           # informativní cena/kus (volitelné)
    venue: str
    quote_amount: Optional[Decimal] = None   # celková částka v měně pro BUY/SELL
    fee_amount: Optional[Decimal] = None     # poplatek bundlovaný s BUY/SELL
    fee_currency: Optional[str] = None
    note: Optional[str] = None


@dataclass
class AddTradeResultDTO:
    success: bool
    n_rows_added: int
    error_message: Optional[str] = None


@dataclass
class ImportResultDTO:
    success: bool
    rows_added: int
    rows_skipped: int
    error_message: Optional[str] = None


@dataclass
class PositionDTO:
    """Akciová pozice odvozená z ledgeru — ledger-centric, žádný cache."""
    ticker: str
    quantity: Decimal
    wac: Decimal               # Weighted Average Cost per unit
    cost_basis: Decimal        # quantity × wac
    currency: str              # quote měna (EUR, USD, ...)
    spot_price: Optional[Decimal] = None    # aktuální cena (yfinance, volitelné)
    value: Optional[Decimal] = None         # quantity × spot_price
    unrealized_pnl: Optional[Decimal] = None  # value − cost_basis
    roi: Optional[Decimal] = None           # unrealized_pnl / cost_basis (jako zlomek)


@dataclass
class PortfolioSnapshotDTO:
    """Snapshot portfolia odvozený čistě z ledgeru + volitelných cen."""
    positions: List[PositionDTO]
    total_cost_basis: Decimal
    total_value: Optional[Decimal] = None
    total_pnl: Optional[Decimal] = None
    total_roi: Optional[Decimal] = None


# ── App context ────────────────────────────────────────────────────────────────

def create_app_context(config_path=None) -> AppContextDTO:
    """Načte config, ověří DB a vrátí AppContextDTO."""
    from core.config import get_resolved_config_path, load_config

    try:
        from stocks_ledger import __version__ as _ver
    except Exception:
        _ver = "1.0.0"

    try:
        resolved = get_resolved_config_path(config_path)
        cfg = load_config(resolved)
    except Exception as exc:
        return AppContextDTO(db_path="", version=_ver, error=str(exc))

    db_path = cfg.get("db_path", "").strip()

    if not db_path:
        return AppContextDTO(
            db_path="", version=_ver,
            error="db_path není nakonfigurováno v stocks_ledger.ini",
        )

    if not os.path.exists(db_path):
        logger.info("DB nenalezena (první spuštění): %s", db_path)
        return AppContextDTO(db_path=db_path, version=_ver, db_state="DB_MISSING")

    try:
        store = LedgerStore(db_path)
        store.close()
        logger.info("DB OK: %s", db_path)
    except Exception as exc:
        msg = f"Nelze otevřít databázi '{db_path}': {exc}"
        logger.error(msg)
        return AppContextDTO(db_path=db_path, version=_ver, db_state="DB_ERROR", error=msg)

    return AppContextDTO(db_path=db_path, version=_ver)


def create_db(db_path: str) -> SimpleResultDTO:
    """Vytvoří (nebo otevře) SQLite ledger. Idempotentní."""
    try:
        os.makedirs(os.path.dirname(os.path.abspath(db_path)), exist_ok=True)
        store = LedgerStore(db_path)
        store.close()
        logger.info("DB vytvořena/ověřena: %s", db_path)
        return SimpleResultDTO(success=True)
    except Exception as exc:
        msg = f"Nelze vytvořit databázi '{db_path}': {exc}"
        logger.error(msg)
        return SimpleResultDTO(success=False, error_message=msg)


def set_db_path(new_db_path: str) -> SimpleResultDTO:
    """Uloží novou db_path do stocks_ledger.ini."""
    try:
        from core.config import set_db_path as _sdp
        _sdp(new_db_path)
        logger.info("db_path aktualizována: %s", new_db_path)
        return SimpleResultDTO(success=True)
    except Exception as exc:
        msg = f"Nelze aktualizovat db_path: {exc}"
        logger.error(msg)
        return SimpleResultDTO(success=False, error_message=msg)


# ── Add trade ──────────────────────────────────────────────────────────────────

def add_trade(request: AddTradeRequestDTO, db_path: str) -> AddTradeResultDTO:
    """Validuj, normalizuj a ulož transakci do ledgeru.

    Nikdy nevyvolá výjimku — chyby jdou do AddTradeResultDTO.error_message.
    """
    ttype    = (request.type     or "").upper().strip()
    asset    = (request.asset    or "").upper().strip()
    currency = (request.currency or "").upper().strip()
    venue    = (request.venue    or "").lower().strip()

    # ── Validace společná pro všechny typy ───────────────────────────────────
    if ttype not in TRADE_TYPES:
        return AddTradeResultDTO(success=False, n_rows_added=0,
                                 error_message=f"Neznámý typ: {ttype!r}")
    if ttype == "REVERSAL":
        return AddTradeResultDTO(success=False, n_rows_added=0,
                                 error_message="REVERSAL: použij reverse_trade(), ne add_trade().")
    if not asset:
        return AddTradeResultDTO(success=False, n_rows_added=0,
                                 error_message="asset nesmí být prázdný")
    if not venue:
        return AddTradeResultDTO(success=False, n_rows_added=0,
                                 error_message="venue nesmí být prázdné")
    if not currency:
        return AddTradeResultDTO(success=False, n_rows_added=0,
                                 error_message="currency nesmí být prázdná")
    if request.amount is None or request.amount <= _ZERO:
        return AddTradeResultDTO(success=False, n_rows_added=0,
                                 error_message="amount musí být > 0")

    # ── BUY / SELL — double-entry přes trade_service ──────────────────────────
    if ttype in _DOUBLE_ENTRY_TYPES:
        quote_amount = request.quote_amount
        if quote_amount is None and request.price is not None and request.price > _ZERO:
            quote_amount = request.amount * request.price
        if not quote_amount or quote_amount <= _ZERO:
            return AddTradeResultDTO(
                success=False, n_rows_added=0,
                error_message="Pro BUY/SELL zadej price > 0 nebo quote_amount > 0",
            )
        fee_currency = request.fee_currency.upper().strip() if request.fee_currency else None
        inp = AddTradeInput(
            type=ttype,
            timestamp=request.timestamp,
            base_asset=asset,
            base_amount=request.amount,
            quote_currency=currency,
            quote_amount=quote_amount,
            venue=venue,
            fee_amount=request.fee_amount,
            fee_currency=fee_currency,
            note=request.note,
        )
        try:
            result = _core_add_trade(db_path, inp)
        except ValueError as exc:
            return AddTradeResultDTO(success=False, n_rows_added=0, error_message=str(exc))
        return AddTradeResultDTO(success=True, n_rows_added=result.inserted)

    # ── Single-row typy: DIVIDEND, FEE, TAX, CASH_IN, CASH_OUT ──────────────
    sign = _SINGLE_ROW_SIGN[ttype]
    signed_amount = Decimal(str(request.amount)) * sign

    store = LedgerStore(db_path)
    try:
        trade_id = generate_canonical_id(request.timestamp, venue, ttype, store.conn)
        row = RawRow(
            id=trade_id,
            timestamp=request.timestamp,
            type=ttype,
            asset=asset,
            amount=signed_amount,
            currency=currency,
            price=request.price if request.price is not None else _ZERO,
            venue=venue,
            note=request.note,
        )
        counts = store.import_rows([row])
    finally:
        store.close()

    return AddTradeResultDTO(success=True, n_rows_added=counts["inserted"])


# ── Read ───────────────────────────────────────────────────────────────────────

def get_ledger_rows(db_path: str) -> List[RawRow]:
    """Vrátí všechny řádky seřazené podle timestamp ASC."""
    store = LedgerStore(db_path)
    try:
        return store.timeline()
    finally:
        store.close()


# ── Portfolio snapshot ─────────────────────────────────────────────────────────

def get_portfolio_snapshot(db_path: str) -> PortfolioSnapshotDTO:
    """Vrátí aktuální pozice odvozené čistě z ledger rows (žádný cache, žádný DB write).

    Ceny (spot_price, value, unrealized_pnl, roi) jsou None — obohacení
    probíhá volitelně v UI vrstvě přes price_provider na pozadí.
    """
    from core.services.holdings_engine import compute_holdings

    rows = get_ledger_rows(db_path)
    holdings = compute_holdings(rows)

    positions: List[PositionDTO] = []
    total_cost = Decimal("0")

    for h in holdings:
        total_cost += h.cost_basis
        positions.append(PositionDTO(
            ticker=h.ticker,
            quantity=h.quantity,
            wac=h.wac,
            cost_basis=h.cost_basis,
            currency=h.currency,
        ))

    return PortfolioSnapshotDTO(
        positions=positions,
        total_cost_basis=total_cost,
    )


# ── Delete trade ──────────────────────────────────────────────────────────────

def delete_trade(db_path: str, trade_id: str) -> SimpleResultDTO:
    """Smaže všechny řádky trade_id z ledgeru. Nevratná operace."""
    store = LedgerStore(db_path)
    try:
        n = store.delete_trade(trade_id)
        if n == 0:
            return SimpleResultDTO(success=False, error_message=f"Trade ID '{trade_id}' nenalezeno.")
        return SimpleResultDTO(success=True)
    except Exception as exc:
        msg = f"Nelze smazat trade '{trade_id}': {exc}"
        logger.error(msg)
        return SimpleResultDTO(success=False, error_message=msg)
    finally:
        store.close()
