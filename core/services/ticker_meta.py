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


def fetch_names(tickers: List[str]) -> Dict[str, str]:
    """Načte názvy společností z Yahoo Finance pro dané tickery.

    Vrátí pouze tickery, pro které se podařilo jméno najít.
    Nevyvolá výjimku — chyby jsou tiše ignorovány.
    """
    if not tickers:
        return {}

    try:
        import yfinance as yf
    except ImportError:
        return {}

    result: Dict[str, str] = {}
    for ticker in tickers:
        try:
            info = yf.Ticker(ticker).info
            name = info.get("longName") or info.get("shortName")
            if name:
                result[ticker] = name
                logger.debug("Jméno %s: %s", ticker, name)
        except Exception as exc:
            logger.debug("Fetch jméno %s selhal: %s", ticker, exc)

    return result
