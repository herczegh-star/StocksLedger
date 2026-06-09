"""Price Provider — volitelný wrapper pro Yahoo Finance.

Nevyvolá výjimku: pokud yfinance není nainstalováno nebo fetch selže,
vrátí prázdný dict. UI pak zobrazí '—' pro price-dependent pole.

Instalace: pip install yfinance
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# Explicitní aliasy: ledger ticker → yfinance ticker
_EXPLICIT_ALIASES: Dict[str, str] = {
    "BRKB.US": "BRK-B",
}


def _yf_ticker(ledger_ticker: str) -> str:
    """Převede ledger ticker na yfinance ticker.

    Pořadí:
      1. explicitní alias (_EXPLICIT_ALIASES)
      2. strip .US suffix (ANET.US → ANET)
      3. původní ticker jako fallback
    """
    if ledger_ticker in _EXPLICIT_ALIASES:
        return _EXPLICIT_ALIASES[ledger_ticker]
    if ledger_ticker.endswith(".US"):
        return ledger_ticker[:-3]
    return ledger_ticker


def _fetch_one(yf, yf_sym: str) -> Optional[float]:
    """Pokusí se načíst cenu pro jeden yfinance symbol. Vrátí None při selhání."""
    try:
        fi = yf.Ticker(yf_sym).fast_info
        price = getattr(fi, "last_price", None)
        if not price or float(price) <= 0:
            price = getattr(fi, "previous_close", None)
        if price and float(price) > 0:
            return float(price)
    except Exception as exc:
        logger.debug("Fetch ceny %s selhal: %s", yf_sym, exc)
    return None


def fetch_prices(tickers: List[str]) -> Dict[str, Decimal]:
    """Načte aktuální ceny z Yahoo Finance.

    Klíče výsledku jsou vždy původní ledger tickery (ne yfinance symboly).
    Vrátí prázdný dict pokud yfinance není dostupné nebo fetch selže.
    Network errors jsou tiše ignorovány.
    """
    if not tickers:
        return {}

    try:
        import yfinance as yf  # optional dependency
    except ImportError:
        logger.debug("yfinance není nainstalováno — ceny nedostupné")
        return {}

    result: Dict[str, Decimal] = {}
    for ticker in tickers:
        yf_sym = _yf_ticker(ticker)
        price = _fetch_one(yf, yf_sym)

        # fallback na původní ticker pokud alias selhal a byl použit alias
        if price is None and yf_sym != ticker:
            logger.debug("Alias %s → %s selhal, zkouším původní", ticker, yf_sym)
            price = _fetch_one(yf, ticker)

        if price is not None:
            result[ticker] = Decimal(str(round(price, 4)))
            logger.debug("Cena %s (yf: %s): %s", ticker, yf_sym, result[ticker])

    return result
