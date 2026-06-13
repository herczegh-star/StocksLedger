"""
tools/price_diag.py
-------------------
Diagnostic script: exposes per-ticker price discrepancies between StocksLedger
and an external broker (e.g. XTB) by printing a comparison table from raw
Yahoo Finance chart API data.

Why this exists:
    The production price_provider always computes EUR price as raw_price / EURUSD,
    which is wrong for non-USD instruments (EUR, GBp/pence, CHF, ...).
    This script makes the error visible without touching production code.

Columns printed:
    Ticker        - ledger ticker as stored
    YF Symbol     - mapped Yahoo Finance symbol (after suffix translation)
    RegMktPrice   - Yahoo Finance regularMarketPrice (last traded)
    PrevClose     - Yahoo Finance previousClose (fallback the app uses)
    Ccy           - raw currency as reported by Yahoo Finance (often ignored by app)
    EURUSD        - EUR/USD rate fetched at run time
    Code EUR      - what the app computes: raw_price / EURUSD (wrong for non-USD)
    Aware EUR     - currency-aware correct EUR price
    Notes         - diagnostic flags

Usage:
    python tools/price_diag.py
    python tools/price_diag.py --db C:/path/to/stocks_ledger.db
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

# Allow running from project root or from tools/ directly
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.config import get_resolved_config_path, load_config
from core.ledger_store import LedgerStore
from core.services.holdings_engine import FIAT, _REV_PREFIX

# Import symbol-mapping helpers from production code (read-only, no side effects)
from core.services.price_provider import (
    _EXPLICIT_ALIASES,
    _SUFFIX_MAP,
    _make_session,
    _yf_ticker,
)

# ── Constants ─────────────────────────────────────────────────────────────────

_YF_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{ticker}?range=1d&interval=1d"

# FX rates we fetch to cover common non-USD instrument currencies
_FX_SYMBOLS: Dict[str, str] = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "CHFUSD": "CHFUSD=X",
}

# Currencies that the current app code handles incorrectly by dividing by EURUSD
_WRONG_IF_DIV_EURUSD = frozenset({"EUR", "GBp", "GBX", "GBP", "CHF", "SEK", "DKK", "NOK", "PLN", "CZK", "HUF"})


# ── Ledger helpers ────────────────────────────────────────────────────────────

def _get_tickers_from_ledger(db_path: str) -> List[str]:
    """Return unique non-FIAT tickers from non-reversed BUY/SELL rows, sorted."""
    store = LedgerStore(db_path)
    try:
        rows = store.timeline()
    finally:
        store.close()

    reversed_ids: set = set()
    for r in rows:
        if r.type == "REVERSAL" and r.note and r.note.startswith(_REV_PREFIX):
            reversed_ids.add(r.note[len(_REV_PREFIX):])

    seen: set = set()
    tickers: List[str] = []
    for r in rows:
        if r.type not in ("BUY", "SELL"):
            continue
        if r.id in reversed_ids:
            continue
        if r.asset in FIAT:
            continue
        if r.asset not in seen:
            seen.add(r.asset)
            tickers.append(r.asset)

    return sorted(tickers)


# ── HTTP fetch ────────────────────────────────────────────────────────────────

def _fetch_meta(session, yf_sym: str) -> dict:
    """Fetch full chart meta dict for one Yahoo Finance symbol.

    Returns a dict with a special '_http_error' or '_parse_error' key on failure
    so callers can distinguish fetch error from simply missing fields.
    """
    try:
        url = _YF_CHART_URL.format(ticker=yf_sym)
        r = session.get(url, timeout=10)
        if r.status_code != 200:
            return {"_http_error": f"HTTP {r.status_code}"}
        payload = r.json().get("chart", {})
        error = payload.get("error")
        if error:
            return {"_parse_error": str(error)}
        result = payload.get("result") or []
        if not result:
            return {"_parse_error": "empty result array"}
        return result[0].get("meta", {})
    except Exception as exc:
        return {"_parse_error": str(exc)}


def _fetch_fx_rates(session) -> Dict[str, Optional[Decimal]]:
    """Fetch EURUSD, GBPUSD, CHFUSD in sequence. None = fetch failed."""
    rates: Dict[str, Optional[Decimal]] = {}
    for name, sym in _FX_SYMBOLS.items():
        meta = _fetch_meta(session, sym)
        raw = meta.get("regularMarketPrice") or meta.get("previousClose")
        if raw and float(raw) > 0:
            rates[name] = Decimal(str(round(float(raw), 6)))
        else:
            rates[name] = None
    return rates


# ── EUR conversion ────────────────────────────────────────────────────────────

def _current_code_eur(
    raw_price: Optional[float],
    fx: Dict[str, Optional[Decimal]],
) -> Optional[Decimal]:
    """Reproduces what the app currently computes: always raw_price / EURUSD.
    Wrong for non-USD instruments — shown here to make the error visible."""
    if raw_price is None:
        return None
    eurusd = fx.get("EURUSD")
    if not eurusd:
        return None
    return (Decimal(str(raw_price)) / eurusd).quantize(Decimal("0.0001"))


def _currency_aware_eur(
    raw_price: Optional[float],
    raw_currency: Optional[str],
    fx: Dict[str, Optional[Decimal]],
) -> Tuple[Optional[Decimal], str]:
    """Compute correct EUR price based on raw_currency.

    Returns (eur_price, method_note).
    method_note describes the formula used, or the reason conversion failed.
    """
    if raw_price is None or raw_price <= 0:
        return None, "no price"

    p = Decimal(str(raw_price))
    eurusd = fx.get("EURUSD")
    gbpusd = fx.get("GBPUSD")
    chfusd = fx.get("CHFUSD")

    if raw_currency == "USD":
        if eurusd:
            return (p / eurusd).quantize(Decimal("0.0001")), "USD / EURUSD"
        return None, "EURUSD unavailable"

    if raw_currency == "EUR":
        return p.quantize(Decimal("0.0001")), "EUR (no conversion needed)"

    if raw_currency in ("GBp", "GBX"):   # pence → divide by 100 first
        if gbpusd and eurusd:
            return (p / 100 * gbpusd / eurusd).quantize(Decimal("0.0001")), "GBp / 100 * GBPUSD / EURUSD"
        return None, "GBPUSD unavailable"

    if raw_currency == "GBP":
        if gbpusd and eurusd:
            return (p * gbpusd / eurusd).quantize(Decimal("0.0001")), "GBP * GBPUSD / EURUSD"
        return None, "GBPUSD unavailable"

    if raw_currency == "CHF":
        if chfusd and eurusd:
            return (p * chfusd / eurusd).quantize(Decimal("0.0001")), "CHF * CHFUSD / EURUSD"
        return None, "CHFUSD unavailable"

    return None, f"no conversion formula for {raw_currency}"


# ── Diagnostic flags ──────────────────────────────────────────────────────────

def _compute_flags(
    ledger_ticker: str,
    meta: dict,
    raw_price: Optional[float],
    raw_currency: Optional[str],
) -> List[str]:
    flags: List[str] = []

    # Fetch errors (most critical — stop here, remaining flags are not meaningful)
    for err_key in ("_http_error", "_parse_error"):
        if err_key in meta:
            flags.append(f"FETCH_ERROR({meta[err_key]})")
            return flags

    if raw_price is None:
        flags.append("NO_PRICE")

    if not raw_currency:
        flags.append("NO_CURRENCY")
    elif raw_currency in _WRONG_IF_DIV_EURUSD:
        flags.append(f"WRONG_EUR_CONV({raw_currency}/EURUSD is incorrect)")

    if raw_currency in ("GBp", "GBX"):
        flags.append("PENCE_NEEDS_DIV_100")

    if raw_currency and raw_currency not in (
        "USD", "EUR", "GBp", "GBX", "GBP", "CHF"
    ):
        flags.append(f"EXOTIC_CCY({raw_currency})")

    # Detect missing suffix mapping (ticker has a dot but no known suffix)
    if "." in ledger_ticker:
        has_map = ledger_ticker in _EXPLICIT_ALIASES or any(
            ledger_ticker.endswith(sfx) for sfx in _SUFFIX_MAP
        )
        if not has_map:
            flags.append("NO_SUFFIX_MAP")

    # Price source fallback
    if meta.get("regularMarketPrice") is None and raw_price is not None:
        flags.append("PREV_CLOSE_USED")

    return flags


# ── Table printing ────────────────────────────────────────────────────────────

def _f(value, width: int, decimals: int = 4) -> str:
    """Right-align a numeric value in a fixed-width field; 'n/a' if None."""
    if value is None:
        return "n/a".rjust(width)
    try:
        return f"{float(value):.{decimals}f}".rjust(width)
    except Exception:
        return str(value).rjust(width)


def _print_table(rows: List[dict]) -> None:
    # Column widths
    CW = {
        "ticker":  14,
        "yf_sym":  14,
        "reg":     11,
        "prev":    11,
        "ccy":      5,
        "eurusd":   8,
        "code":    10,
        "aware":   10,
    }

    header = (
        f"{'Ticker':<{CW['ticker']}} {'YF Symbol':<{CW['yf_sym']}} "
        f"{'RegMktPrice':>{CW['reg']}} {'PrevClose':>{CW['prev']}} "
        f"{'Ccy':>{CW['ccy']}} {'EURUSD':>{CW['eurusd']}} "
        f"{'Code EUR':>{CW['code']}} {'Aware EUR':>{CW['aware']}}  Notes"
    )
    sep = "-" * 100

    print(header)
    print(sep)

    for r in rows:
        alert = "!! " if r["flags"] else "   "
        notes = ", ".join(r["flags"]) if r["flags"] else "ok"
        ccy_str = (r["raw_currency"] or "?").rjust(CW["ccy"])

        line = (
            f"{r['ticker']:<{CW['ticker']}} {r['yf_sym']:<{CW['yf_sym']}} "
            f"{_f(r['reg_mkt'], CW['reg'])} {_f(r['prev_close'], CW['prev'])} "
            f"{ccy_str} {_f(r['eurusd'], CW['eurusd'], 5)} "
            f"{_f(r['code_eur'], CW['code'])} {_f(r['aware_eur'], CW['aware'])}  "
            f"{alert}{notes}"
        )
        print(line)


def _print_fx_summary(fx: Dict[str, Optional[Decimal]]) -> None:
    print()
    print("FX rates fetched:")
    for name, val in fx.items():
        status = str(val) if val is not None else "UNAVAILABLE"
        print(f"  {name:<10} {status}")


def _print_legend() -> None:
    print()
    print("Legend:")
    print("  RegMktPrice   Yahoo Finance regularMarketPrice (last traded price)")
    print("  PrevClose     Yahoo Finance previousClose (app fallback when market closed)")
    print("  Ccy           Raw currency reported by Yahoo Finance (app currently ignores this)")
    print("  Code EUR      raw_price / EURUSD  — what the app computes for every ticker")
    print("  Aware EUR     Currency-aware EUR price (correct conversion per raw Ccy)")
    print()
    print("  Flags:")
    print("  WRONG_EUR_CONV    raw currency is not USD — Code EUR is incorrect for this ticker")
    print("  PENCE_NEEDS_DIV_100  GBp/GBX price is in pence, must divide by 100 before converting")
    print("  NO_SUFFIX_MAP     ticker has a dot-suffix not in the mapping table")
    print("  PREV_CLOSE_USED   regularMarketPrice was None — app fell back to previousClose")
    print("  NO_PRICE          no usable price returned by Yahoo Finance")
    print("  NO_CURRENCY       Yahoo Finance did not return meta.currency")
    print("  EXOTIC_CCY        currency not covered by any conversion formula")
    print("  FETCH_ERROR       HTTP or parse error — symbol may be wrong or API is down")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="StocksLedger price diagnostic — compare YF raw data with app-computed EUR prices."
    )
    parser.add_argument(
        "--db",
        default=None,
        metavar="PATH",
        help="Path to stocks_ledger.db (overrides config)",
    )
    args = parser.parse_args()

    # ── Resolve DB path ───────────────────────────────────────────────────────
    if args.db:
        db_path = args.db
    else:
        cfg = load_config(get_resolved_config_path())
        db_path = cfg.get("db_path", "").strip()

    if not db_path or not os.path.exists(db_path):
        print(f"ERROR: DB not found: {db_path!r}")
        print("  Pass --db /path/to/stocks_ledger.db  or set up stocks_ledger.ini first.")
        sys.exit(1)

    print(f"DB:  {db_path}")
    print()

    # ── Load tickers from ledger ──────────────────────────────────────────────
    tickers = _get_tickers_from_ledger(db_path)
    if not tickers:
        print("No non-FIAT tickers found in ledger.")
        sys.exit(0)

    print(f"Tickers in ledger ({len(tickers)}):  {', '.join(tickers)}")
    print()

    # ── HTTP session ──────────────────────────────────────────────────────────
    session = _make_session()
    if session is None:
        print("ERROR: 'requests' library is not installed.")
        print("  Install with:  pip install requests")
        sys.exit(1)

    # ── Fetch FX rates ────────────────────────────────────────────────────────
    print("Fetching FX rates (EURUSD, GBPUSD, CHFUSD)...", flush=True)
    fx = _fetch_fx_rates(session)

    # ── Fetch per-ticker chart meta ───────────────────────────────────────────
    print("Fetching ticker chart meta from Yahoo Finance...", flush=True)
    print()

    table_rows: List[dict] = []

    for ledger_ticker in tickers:
        yf_sym = _yf_ticker(ledger_ticker)
        meta   = _fetch_meta(session, yf_sym)

        reg_mkt    = meta.get("regularMarketPrice")
        prev_close = meta.get("previousClose")
        raw_ccy    = meta.get("currency")              # field the app never reads
        raw_price  = reg_mkt if reg_mkt is not None else prev_close

        code_eur           = _current_code_eur(raw_price, fx)
        aware_eur, _method = _currency_aware_eur(raw_price, raw_ccy, fx)
        flags              = _compute_flags(ledger_ticker, meta, raw_price, raw_ccy)

        table_rows.append({
            "ticker":       ledger_ticker,
            "yf_sym":       yf_sym,
            "reg_mkt":      reg_mkt,
            "prev_close":   prev_close,
            "raw_currency": raw_ccy,
            "eurusd":       fx.get("EURUSD"),
            "code_eur":     code_eur,
            "aware_eur":    aware_eur,
            "flags":        flags,
        })

        time.sleep(0.15)   # gentle rate limiting — avoid Yahoo Finance 429

    # ── Print results ─────────────────────────────────────────────────────────
    _print_table(table_rows)
    _print_fx_summary(fx)
    _print_legend()

    # ── Summary counts ────────────────────────────────────────────────────────
    n_flagged     = sum(1 for r in table_rows if r["flags"])
    n_wrong_conv  = sum(1 for r in table_rows if any("WRONG_EUR_CONV" in f for f in r["flags"]))
    n_no_price    = sum(1 for r in table_rows if any("NO_PRICE" in f or "FETCH_ERROR" in f for f in r["flags"]))

    print()
    print(
        f"Summary: {len(tickers)} tickers | "
        f"{n_flagged} flagged | "
        f"{n_wrong_conv} with wrong EUR conversion | "
        f"{n_no_price} with missing/failed price"
    )


if __name__ == "__main__":
    main()
