"""Price Provider — přímé volání Yahoo Finance chart API přes requests.

Nevyvolá výjimku: pokud requests není dostupné nebo fetch selže,
vrátí prázdný dict. UI pak zobrazí '—' pro price-dependent pole.

Poznámka: záměrně nepoužívá yfinance.fast_info/history, protože yfinance 1.4.x
provádí opakované crumb/cookie fetche které Yahoo Finance agresivně rate-limituje.
Přímý chart v2 endpoint je spolehlivější a nevyžaduje autentizaci.
"""
from __future__ import annotations

import logging
from decimal import Decimal
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

_YF_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=1d&interval=1d"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}

# Explicitní aliasy: ledger ticker → Yahoo Finance ticker
_EXPLICIT_ALIASES: Dict[str, str] = {
    "BRKB.US": "BRK-B",
}


def _yf_ticker(ledger_ticker: str) -> str:
    """Převede ledger ticker na Yahoo Finance ticker.

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


def _make_session():
    """Requests session se zakázanou SSL verifikací.

    Nutné na strojích kde Windows certificate store neobsahuje root CA
    používané Yahoo Finance. Bezpečné pro osobní desktop aplikaci —
    data jsou veřejné tržní ceny.
    """
    try:
        import requests
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        s = requests.Session()
        s.verify = False
        s.headers.update(_HEADERS)
        return s
    except ImportError:
        return None


def _fetch_one(session, yf_sym: str) -> Optional[float]:
    """Načte regularMarketPrice pro jeden Yahoo Finance symbol přes chart API."""
    try:
        url = _YF_CHART_URL.format(ticker=yf_sym)
        r = session.get(url, timeout=10)
        if r.status_code != 200:
            logger.debug("Cena %s: HTTP %s", yf_sym, r.status_code)
            return None
        meta = r.json().get("chart", {}).get("result", [{}])[0].get("meta", {})
        price = meta.get("regularMarketPrice") or meta.get("previousClose")
        if price and float(price) > 0:
            return float(price)
    except Exception as exc:
        logger.debug("Fetch ceny %s selhal: %s", yf_sym, exc)
    return None


def fetch_prices(tickers: List[str]) -> Dict[str, Decimal]:
    """Načte aktuální ceny z Yahoo Finance chart API.

    Klíče výsledku jsou vždy původní ledger tickery (ne Yahoo Finance symboly).
    Vrátí prázdný dict pokud requests není dostupné nebo fetch selže.
    Network errors jsou tiše ignorovány.
    """
    if not tickers:
        return {}

    session = _make_session()
    if session is None:
        logger.debug("requests není nainstalováno — ceny nedostupné")
        return {}

    result: Dict[str, Decimal] = {}
    for ticker in tickers:
        yf_sym = _yf_ticker(ticker)
        price = _fetch_one(session, yf_sym)

        # fallback na původní ticker pokud alias selhal a byl použit alias
        if price is None and yf_sym != ticker:
            logger.debug("Alias %s → %s selhal, zkouším původní", ticker, yf_sym)
            price = _fetch_one(session, ticker)

        if price is not None:
            result[ticker] = Decimal(str(round(price, 4)))
            logger.debug("Cena %s (yf: %s): %s", ticker, yf_sym, result[ticker])

    return result
