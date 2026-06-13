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
_CACHE_TTL: float = 300.0  # 5 minut

# Klíč: ledger ticker  Hodnota: (raw_price, native_currency, unix_timestamp)
_price_cache: Dict[str, Tuple[Decimal, str, float]] = {}

# FX rate cache: name → [Optional[Decimal], timestamp]
_FX_NAMES: Tuple[str, ...] = ("EURUSD", "GBPUSD", "CHFUSD")
_FX_SYMBOLS: Dict[str, str] = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "CHFUSD": "CHFUSD=X",
}
_fx_cache: Dict[str, List] = {name: [None, 0.0] for name in _FX_NAMES}

# Backward-compat alias so tools/price_diag.py and any external reader still work
_eurusd_cache = _fx_cache["EURUSD"]


def _cache_get(ticker: str) -> Optional[Tuple[Decimal, str]]:
    """Vrátí (price, currency) z cache pokud existuje a není starší než TTL."""
    entry = _price_cache.get(ticker)
    if entry and (time.monotonic() - entry[2]) < _CACHE_TTL:
        return entry[0], entry[1]
    return None


def _cache_set(ticker: str, price: Decimal, currency: str) -> None:
    _price_cache[ticker] = (price, currency, time.monotonic())


def cache_clear() -> None:
    """Vymaže celou price cache. Určeno pro testy a ruční invalidaci."""
    _price_cache.clear()
    for v in _fx_cache.values():
        v[0] = None
        v[1] = 0.0


# ── Ticker alias ──────────────────────────────────────────────────────────────

def _yf_ticker(ledger_ticker: str) -> str:
    """Převede XTB ledger ticker na Yahoo Finance ticker."""
    if ledger_ticker in _EXPLICIT_ALIASES:
        return _EXPLICIT_ALIASES[ledger_ticker]
    for xtb_sfx, yf_sfx in _SUFFIX_MAP.items():
        if ledger_ticker.endswith(xtb_sfx):
            base = ledger_ticker[: -len(xtb_sfx)]
            return base + yf_sfx
    return ledger_ticker


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _make_session():
    """Requests session se zakázanou SSL verifikací."""
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


def _fetch_one(session, yf_sym: str) -> Tuple[Optional[float], Optional[str]]:
    """Načte regularMarketPrice + currency pro jeden Yahoo Finance symbol.

    Vrátí (price, currency). Oba prvky mohou být None při selhání.
    """
    try:
        url = _YF_CHART_URL.format(ticker=yf_sym)
        r = session.get(url, timeout=10)
        if r.status_code != 200:
            logger.debug("Cena %s: HTTP %s", yf_sym, r.status_code)
            return None, None
        meta = r.json().get("chart", {}).get("result", [{}])[0].get("meta", {})
        price = meta.get("regularMarketPrice") or meta.get("previousClose")
        currency = meta.get("currency")
        if price and float(price) > 0:
            return float(price), currency
    except Exception as exc:
        logger.debug("Fetch ceny %s selhal: %s", yf_sym, exc)
    return None, None


def _fetch_ticker(
    session, ledger_ticker: str
) -> Tuple[str, Optional[Decimal], Optional[str]]:
    """Načte cenu + nativní měnu jednoho ledger tickeru.

    Vrátí (ticker, price|None, currency|None).
    Bezpečné pro ThreadPoolExecutor — výjimky zachytí a vrátí None.
    """
    yf_sym = _yf_ticker(ledger_ticker)
    try:
        price, ccy = _fetch_one(session, yf_sym)
        if price is None and yf_sym != ledger_ticker:
            logger.debug("Alias %s → %s selhal, zkouším původní", ledger_ticker, yf_sym)
            price, ccy = _fetch_one(session, ledger_ticker)
        if price is not None:
            d = Decimal(str(round(price, 4)))
            ccy = ccy or "USD"   # fallback: assume USD when Yahoo omits currency field
            logger.debug("Cena %s (yf: %s): %s %s", ledger_ticker, yf_sym, d, ccy)
            return ledger_ticker, d, ccy
    except Exception as exc:
        logger.debug("_fetch_ticker %s selhal: %s", ledger_ticker, exc)
    return ledger_ticker, None, None


# ── EUR conversion (pure) ─────────────────────────────────────────────────────

def to_eur(
    raw_price: Optional[Decimal],
    raw_ccy:   Optional[str],
    fx_rates:  Dict[str, Optional[Decimal]],
) -> Optional[Decimal]:
    """Převede Yahoo Finance cenu v nativní měně na EUR.

    Podporované měny:
      USD              → raw / EURUSD
      EUR              → raw (bez konverze)
      GBp / GBX (pence) → raw / 100 * GBPUSD / EURUSD
      GBP              → raw * GBPUSD / EURUSD
      CHF              → raw * CHFUSD / EURUSD

    Vrátí None pokud:
      - raw_price je None nebo ≤ 0
      - potřebný FX kurz není dostupný
      - měna není podporovaná (exotická nebo None)

    Čistá funkce — žádné I/O, žádné side effects.
    """
    if raw_price is None or raw_price <= Decimal("0"):
        return None

    eurusd = fx_rates.get("EURUSD")
    gbpusd = fx_rates.get("GBPUSD")
    chfusd = fx_rates.get("CHFUSD")

    if raw_ccy == "USD":
        return (raw_price / eurusd) if eurusd else None

    if raw_ccy == "EUR":
        return raw_price

    if raw_ccy in ("GBp", "GBX"):       # London pence
        if gbpusd and eurusd:
            return raw_price / Decimal("100") * gbpusd / eurusd
        return None

    if raw_ccy == "GBP":
        if gbpusd and eurusd:
            return raw_price * gbpusd / eurusd
        return None

    if raw_ccy == "CHF":
        if chfusd and eurusd:
            return raw_price * chfusd / eurusd
        return None

    logger.debug("to_eur: nepodporovaná měna %r — vracím None", raw_ccy)
    return None


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_fx_rates() -> Dict[str, Optional[Decimal]]:
    """Načte EURUSD, GBPUSD, CHFUSD z Yahoo Finance.

    Každý kurz se cachuje na _CACHE_TTL sekund. Fetch probíhá jen pro expirované.
    Vrátí dict; hodnota je None pokud se kurz nepodařilo načíst.
    """
    now = time.monotonic()
    to_fetch: List[str] = []

    result: Dict[str, Optional[Decimal]] = {}
    for name in _FX_NAMES:
        entry = _fx_cache[name]
        if entry[0] is not None and (now - entry[1]) < _CACHE_TTL:
            result[name] = entry[0]
            logger.debug("fx cache hit %s: %s", name, entry[0])
        else:
            to_fetch.append(name)

    if not to_fetch:
        return result

    session = _make_session()
    if session is None:
        for name in to_fetch:
            result[name] = None
        return result

    for name in to_fetch:
        yf_sym = _FX_SYMBOLS[name]
        price, _ = _fetch_one(session, yf_sym)
        if price is not None and price > 0:
            rate = Decimal(str(round(price, 6)))
            _fx_cache[name][0] = rate
            _fx_cache[name][1] = now
            result[name] = rate
        else:
            result[name] = None

    return result


def fetch_eurusd() -> Optional[Decimal]:
    """Načte aktuální EUR/USD kurz (backward-compat wrapper nad fetch_fx_rates).

    Výsledek se cachuje na _CACHE_TTL sekund (5 min).
    Vrátí None pokud fetch selže.
    """
    return fetch_fx_rates().get("EURUSD")


def fetch_prices_with_currency(
    tickers: List[str],
) -> Dict[str, Tuple[Decimal, str]]:
    """Načte aktuální ceny včetně nativní měny z Yahoo Finance chart API.

    Vrátí {ledger_ticker: (raw_price, native_currency)}.
    Klíče výsledku jsou vždy původní ledger tickery (ne Yahoo Finance symboly).
    Vrátí prázdný dict pokud requests není dostupné nebo fetch selže.

    Optimalizace:
      - TTL cache (5 min): již stažené ceny se netahají znovu.
      - Paralelní fetch: max 6 workerů.
      - Izolace chyb: selhání jednoho tickeru nesmí shodit ostatní.
    """
    if not tickers:
        return {}

    result: Dict[str, Tuple[Decimal, str]] = {}
    to_fetch: List[str] = []

    for ticker in tickers:
        cached = _cache_get(ticker)
        if cached is not None:
            result[ticker] = cached
            logger.debug("Cena %s: cache hit (%s %s)", ticker, cached[0], cached[1])
        else:
            to_fetch.append(ticker)

    if not to_fetch:
        return result

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
                ticker, price, ccy = future.result()
                if price is not None and ccy is not None:
                    result[ticker] = (price, ccy)
                    _cache_set(ticker, price, ccy)
            except Exception as exc:
                failed_ticker = futures[future]
                logger.debug("Future pro %s selhala: %s", failed_ticker, exc)

    return result


def fetch_prices(tickers: List[str]) -> Dict[str, Decimal]:
    """Backward-compat wrapper — vrátí jen ceny bez nativní měny.

    Nové volající by měly používat fetch_prices_with_currency() + to_eur().
    """
    return {t: v[0] for t, v in fetch_prices_with_currency(tickers).items()}
