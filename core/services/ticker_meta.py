"""Ticker metadata — company names, purely presentational.

JSON cache at ~/.stocks_ledger/ticker_names.json.
Populated on-demand from yfinance; works offline once cached.
Ledger and DB are never touched.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Dict, List

logger = logging.getLogger(__name__)

_NAMES_FILE = "ticker_names.json"


def names_path(db_path: str) -> str:
    return os.path.join(os.path.dirname(os.path.abspath(db_path)), _NAMES_FILE)


def load_names(db_path: str) -> Dict[str, str]:
    """Načte uložená jména ze JSON cache. Vrátí {} pokud soubor neexistuje."""
    path = names_path(db_path)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return {k: v for k, v in data.items() if isinstance(k, str) and isinstance(v, str)}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_names(db_path: str, names: Dict[str, str]) -> None:
    """Uloží jména do JSON cache. Tiše ignoruje chyby zápisu."""
    path = names_path(db_path)
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(dict(sorted(names.items())), f, indent=2, ensure_ascii=False)
    except Exception as exc:
        logger.debug("Nelze uložit %s: %s", _NAMES_FILE, exc)


_YF_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=1d&interval=1d"


def fetch_names(tickers: List[str]) -> Dict[str, str]:
    """Načte názvy společností z Yahoo Finance chart API (meta.longName).

    Používá stejný alias layer jako price_provider (_yf_ticker).
    Klíče výsledku jsou vždy původní ledger tickery.
    Vrátí pouze tickery, pro které se podařilo jméno najít.
    Nevyvolá výjimku — chyby jsou tiše ignorovány.
    """
    if not tickers:
        return {}

    from core.services.price_provider import _make_session, _yf_ticker

    session = _make_session()
    if session is None:
        return {}

    result: Dict[str, str] = {}
    for ticker in tickers:
        yf_sym = _yf_ticker(ticker)
        try:
            url = _YF_CHART_URL.format(ticker=yf_sym)
            r = session.get(url, timeout=10)
            if r.status_code != 200:
                continue
            meta = r.json().get("chart", {}).get("result", [{}])[0].get("meta", {})
            name = meta.get("longName") or meta.get("shortName")
            if name:
                result[ticker] = name  # klíč = ledger ticker
                logger.debug("Jméno %s (yf: %s): %s", ticker, yf_sym, name)
        except Exception as exc:
            logger.debug("Fetch jméno %s selhal: %s", ticker, exc)

    return result
