"""Shared DTOs pro reporty (připraveno pro M2+)."""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, FrozenSet, List, Literal, Optional

Bucket = Literal["day", "week", "month"]


@dataclass
class ReportMeta:
    bucket: str
    fiat: FrozenSet[str]
    kind: str = "timeseries"


@dataclass
class TimeSeriesRow:
    date: str
    currency: str
    values: Dict[str, Decimal]


@dataclass
class TimeSeriesReport:
    meta: ReportMeta
    rows: List[TimeSeriesRow]
    totals: Optional[Dict[str, Dict[str, Decimal]]] = field(default=None)


@dataclass
class TableRow:
    key: str
    values: Dict[str, Any]


@dataclass
class TableReport:
    meta: ReportMeta
    rows: List[TableRow]
    totals: Optional[Dict[str, Any]] = field(default=None)
