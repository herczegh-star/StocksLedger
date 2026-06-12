"""Price Provider — přímé volání Yahoo Finance chart API přes requests.

Nevyvolá výjimku: pokud requests není dostupné nebo fetch selže,
vrátí prázdný dict. UI pak zobrazí '—' pro price-dependent pole.

Poznámka: záměrně nepoužívá yfinance.fast_info/history, protože yfinance 1.4.x
provádí opakované crumb/cookie fetche které Yahoo Finance agresivně rate-limituje.
Přímý chart v2 endpoint je spolehlivější a nevyžaduje autentizaci.
"""
from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_YF_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=1d&interval=1d"
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}

# ── Alias / suffix mapování ───────────────────────────────────────────────────

# Explicitní aliasy mají přednost před suffix mapováním
_EXPLICIT_ALIASES: Dict[str, str] = {
    "BRKB.US": "BRK-B",
}

# XTB suffix → Yahoo Finance suffix
# "" znamená strip (ANET.US → ANET); jinak nahradit (ABBN.CH → ABBN.SW)
_SUFFIX_MAP: Dict[str, str] = {
    ".US": "",    # NYSE/NASDAQ — strip (ANET.US → ANET)
    ".CH": ".SW", # SIX Swiss Exchange (ABBN.CH → ABBN.SW)
    ".UK": ".L",  # London Stock Exchange (ISLN.UK → ISLN.L)
    ".DE": ".DE", # Xetra — beze změny (SAP.DE → SAP.DE)
    ".FR": ".PA", # Euronext Paris
    ".NL": ".AS", # Euronext Amsterdam
    ".IT": ".MI", # Borsa Italiana
    ".ES": ".MC", # Madrid
}

# ── In-memory TTL cache ───────────────────────────────────────────────────────
# Klíč: ledger ticker (ne YF symbol) — cache nikdy nevrací cenu pro špatný ticker.
# Položka: (Decimal price, float unix timestamp)
_CACHE_TTL: float = 300.0  # 5 minut

_price_cache: Dict[str, Tuple[Decimal, float]] = {}
_eurusd_cache: List = [None, 0.0]   # [Decimal|None, timestamp]


def _cache_get(ticker: str) -> Optional[Decimal]:
    """Vrátí cenu z cache pokud existuje a není starší než TTL."""
    entry = _price_cache.get(ticker)
    if entry and (time.monotonic() - entry[1]) < _CACHE_TTL:
        return entry[0]
    return None


def _cache_set(ticker: str, price: Decimal) -> None:
    _price_cache[ticker] = (price, time.monotonic())


def cache_clear() -> None:
    """Vymaže celou price cache. Určeno pro testy a ruční invalidaci."""
    _price_cache.clear()
    _eurusd_cache[0] = None
    _eurusd_cache[1] = 0.0


# ── Ticker alias ──────────────────────────────────────────────────────────────

def _yf_ticker(ledger_ticker: str) -> str:
    """Převede XTB ledger ticker na Yahoo Finance ticker.

    Pořadí:
      1. explicitní alias (_EXPLICIT_ALIASES)
      2. suffix mapování (_SUFFIX_MAP): XTB přípona → YF přípona
      3. původní ticker beze změny jako fallback
    """
    if ledger_ticker in _EXPLICIT_ALIASES:
        return _EXPLICIT_ALIASES[ledger_ticker]
    for xtb_sfx, yf_sfx in _SUFFIX_MAP.items():
        if ledger_ticker.endswith(xtb_sfx):
            base = ledger_ticker[: -len(xtb_sfx)]
            return base + yf_sfx
    return ledger_ticker


# ── HTTP helpers ──────────────────────────────────────────────────────────────

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


def _fetch_ticker(session, ledger_ticker: str) -> Tuple[str, Optional[Decimal]]:
    """Načte cenu jednoho ledger tickeru. Vrátí (ticker, price|None).

    Bezpečné pro ThreadPoolExecutor — výjimky zachytí a vrátí None.
    Zachovává fallback na původní ticker pokud YF alias selhal.
    """
    yf_sym = _yf_ticker(ledger_ticker)
    try:
        price = _fetch_one(session, yf_sym)
        if price is None and yf_sym != ledger_ticker:
            logger.debug("Alias %s → %s selhal, zkouším původní", ledger_ticker, yf_sym)
            price = _fetch_one(session, ledger_ticker)
        if price is not None:
            d = Decimal(str(round(price, 4)))
            logger.debug("Cena %s (yf: %s): %s", ledger_ticker, yf_sym, d)
            return ledger_ticker, d
    except Exception as exc:
        logger.debug("_fetch_ticker %s selhal: %s", ledger_ticker, exc)
    return ledger_ticker, None


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_eurusd() -> Optional[Decimal]:
    """Načte aktuální EUR/USD kurz z Yahoo Finance (ticker EURUSD=X).

    Výsledek se cachuje na _CACHE_TTL sekund (5 min).
    Vrátí None pokud fetch selže — caller zobrazí '—' pro EUR-dependent pole.
    """
    now = time.monotonic()
    if _eurusd_cache[0] is not None and (now - _eurusd_cache[1]) < _CACHE_TTL:
        logger.debug("eurusd cache hit: %s", _eurusd_cache[0])
        return _eurusd_cache[0]

    session = _make_session()
    if session is None:
        return None
    rate = _fetch_one(session, "EURUSD=X")
    if rate is not None and rate > 0:
        result = Decimal(str(round(rate, 6)))
        _eurusd_cache[0] = result
        _eurusd_cache[1] = now
        return result
    return None


def fetch_prices(tickers: List[str]) -> Dict[str, Decimal]:
    """Načte aktuální ceny z Yahoo Finance chart API.

    Optimalizace:
      - TTL cache: ceny starší méně než 5 min se netahají znovu.
      - Paralelní fetch: tickery bez cache se stahují souběžně (max 6 workerů).
      - Izolace chyb: selhání jednoho tickeru nesmí shodit ostatní.

    Klíče výsledku jsou vždy původní ledger tickery (ne Yahoo Finance symboly).
    Vrátí prázdný dict pokud requests není dostupné nebo fetch selže.
    """
    if not tickers:
        return {}

    # ── 1. Rozděl na cache-hit a to-fetch ────────────────────────────────────
    result: Dict[str, Decimal] = {}
    to_fetch: List[str] = []

    for ticker in tickers:
        cached = _cache_get(ticker)
        if cached is not None:
            result[ticker] = cached
            logger.debug("Cena %s: cache hit (%s)", ticker, cached)
        else:
            to_fetch.append(ticker)

    if not to_fetch:
        return result

    # ── 2. Paralelní HTTP fetch pro zbývající tickery ─────────────────────────
    session = _make_session()
    if session is None:
        logger.debug("requests není nainstalováno — ceny nedostupné")
        return result

    workers = min(6, len(to_fetch))
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_fetch_ticker, session, t): t
            for t in to_fetch
        }
        for future in as_completed(futures, timeout=30):
            try:
                ticker, price = future.result()
                if price is not None:
                    result[ticker] = price
                    _cache_set(ticker, price)
            except Exception as exc:
                failed_ticker = futures[future]
                logger.debug("Future pro %s selhala: %s", failed_ticker, exc)

    return result
