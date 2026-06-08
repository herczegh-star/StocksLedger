# CLAUDE.md – StocksLedger

## Projekt
Investiční portfolio tracker pro akcie a ETF.
Sourozenský projekt CryptoLedger (LEDGER_APP).
Desktop aplikace v Pythonu s Flet UI.

## Jednovětá esence
Aplikace je nástroj pro čtení a vytváření tokového ledgeru akcií; pravda je v datech, nikoli v kódu.

## Architektura – 4 vrstvy
```
I/O Modul  →  CORE (doménová logika)  →  Services/Facade  →  Grafika (Flet)
```

## Datový model: unified_format_raw
| Pole | Typ | Popis |
|------|-----|-------|
| id | String | Sdílené ID skupiny (double-entry nebo single-row) |
| timestamp | ISO 8601 | Čas transakce |
| type | Enum | viz níže |
| asset | String | Ticker (AAPL) nebo měna (EUR) |
| amount | Decimal | Množství (znaménko = směr) |
| currency | String | Měna (EUR, USD, CZK, GBP, PLN) |
| price | Decimal? | Cena za kus (informativní) |
| venue | String | Broker (xtb, degiro, ibkr) |
| note | String? | Volitelná poznámka |

## Typy transakcí (M1)
| Typ | Double-entry? | Popis |
|-----|--------------|-------|
| BUY | ANO | Nákup: +ticker, -měna |
| SELL | ANO | Prodej: -ticker, +měna |
| DIVIDEND | NE | Dividenda: +měna (asset = ticker pro kontext) |
| FEE | NE | Poplatek: -měna |
| TAX | NE | Daň / withholding: -měna |
| CASH_IN | NE | Vklad na brokerský účet: +měna |
| CASH_OUT | NE | Výběr z brokerského účtu: -měna |
| REVERSAL | NE | Storno předchozí transakce |

## Kanonické principy (neměnné)
1. Ledger-centric — jediný zdroj pravdy
2. Append-only — žádný UPDATE, žádný DELETE
3. Opravy pouze přes REVERSAL
4. Broker-agnostic core
5. MVP filozofie — implementovat jen to, co je aktuálně potřeba

## Struktura projektu
```
StocksLedger/
├── main.py                     # Entry point
├── stocks_ledger/__init__.py   # Verze
├── core/
│   ├── model.py                # RawRow dataclass
│   ├── constants.py            # TRADE_TYPES enum
│   ├── validator.py            # Syntaktická validace
│   ├── ledger_store.py         # SQLite append-only DB
│   ├── config.py               # INI konfigurace
│   ├── logging_setup.py        # Logging
│   ├── dto/reporting.py        # Report DTOs (M3+)
│   └── services/
│       ├── trade_service.py    # BUY/SELL double-entry
│       ├── reversal_service.py # REVERSAL
│       ├── holdings_engine.py  # WAC + holdings z ledgeru (M2)
│       ├── price_provider.py   # Yahoo Finance wrapper, volitelné (M2)
│       └── ui_facade.py        # Jediný kontrakt UI↔Core
├── ui/
│   ├── app_flet.py             # Hlavní Flet UI
│   └── modules/
│       ├── add_trade_dialog.py # Formulář (modální dialog)
│       ├── portfolio_view.py   # Portfolio View — výchozí obrazovka (M2)
│       └── ledger_view.py      # Timeline tabulka
└── tests/                      # pytest testy
```

## Technologie
- Python 3.12+
- SQLite (Ledger Store)
- Flet 0.85 (UI)

## Flet 0.85 API — ověřené vzory
- `ft.run(fn)` ne `ft.app(target=fn)`
- `page.show_dialog(dlg)` / `page.pop_dialog()` pro dialogy (`page.open()` neexistuje v 0.85.2)
- `ft.Icons.X` (velké I)
- Vlastní NavigationRail = `ft.Column` s `ft.Icon` + `ft.Text`

## Co M2 řeší
- Holdings Engine: qty + WAC z ledger rows, čistě ledger-centric
- Portfolio View: výchozí obrazovka, pozice, WAC, cost_basis
- Price Provider: volitelný yfinance wrapper (spot, value, P/L, ROI)
- Background price loading (page.run_task async pattern)
- Dashboard odstraněn

## Budoucí milníky
- M3: Cashflow, netto-invested, CSV import
- M4: Corporate actions (dividend reinvestment, split)
- M5: Daňový engine (FIFO, CZ tax report)
- M6: Parser pro XTB, Degiro, IBKR

## Holdings Engine — klíčové principy
- `compute_holdings(List[RawRow]) → List[HoldingRaw]` — čistá funkce, žádný DB write
- Přeskočí stornované skupiny: REVERSAL note = `'REVERSAL of <trade_id>'`
- Filtruje FIAT legs BUY/SELL: `{"EUR", "USD", "CZK", "GBP", "PLN"}`
- WAC reset při uzavření pozice (qty → 0)
- DIVIDEND/FEE/TAX/CASH_IN/CASH_OUT neovlivňují holdings

## Konvence
- Venue vždy lowercase
- Asset vždy uppercase
- Timestamp vždy ISO 8601
- Amount s Decimal přesností
- Uživatel zadává vždy kladné množství; facade přiřadí znaménko
