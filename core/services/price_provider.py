"""Price Provider — volitelný wrapper pro Yahoo Finance.

Nevyvolá výjimku: pokud yfinance není nainstalováno nebo fetch selže,
vrátí prázdný dict. UI pak zobrazí '—' pro price-dependent pole.

Instalace: pip install yfinance
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Dict, List

logger = logging.getLogger(__name__)


def fetch_prices(tickers: List[str]) -> Dict[str, Decimal]:
    """Načte aktuální ceny z Yahoo Finance.

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
        try:
            fi = yf.Ticker(ticker).fast_info
            price = getattr(fi, "last_price", None)
            if not price or float(price) <= 0:
                price = getattr(fi, "previous_close", None)
            if price and float(price) > 0:
                result[ticker] = Decimal(str(round(float(price), 4)))
                logger.debug("Cena %s: %s", ticker, result[ticker])
        except Exception as exc:
            logger.debug("Fetch ceny %s selhal: %s", ticker, exc)

    return result
