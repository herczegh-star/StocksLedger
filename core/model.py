"""Datový model: unified_format_raw řádek pro StocksLedger."""
from dataclasses import dataclass, asdict
from datetime import datetime
from decimal import Decimal
from typing import Optional
import hashlib
import uuid


VALID_TYPES = {"BUY", "SELL", "DIVIDEND", "FEE", "TAX", "CASH_IN", "CASH_OUT", "REVERSAL"}


@dataclass
class RawRow:
    timestamp: datetime
    type: str
    asset: str
    amount: Decimal
    currency: str
    price: Optional[Decimal]
    venue: str
    note: Optional[str] = None
    id: Optional[str] = None

    def __post_init__(self):
        if self.id is None:
            self.id = str(uuid.uuid4())
        if isinstance(self.amount, (int, float)):
            self.amount = Decimal(str(self.amount))
        if self.price is not None and isinstance(self.price, (int, float)):
            self.price = Decimal(str(self.price))
        if isinstance(self.timestamp, str):
            self.timestamp = datetime.fromisoformat(self.timestamp)

    def fingerprint(self) -> str:
        parts = "|".join([
            self.timestamp.isoformat(),
            self.type.upper(),
            self.venue.lower(),
            self.asset.upper(),
            self.currency.upper(),
            f"{self.amount:.8f}",
        ])
        return hashlib.sha256(parts.encode()).hexdigest()

    def to_dict(self) -> dict:
        d = asdict(self)
        d["timestamp"] = self.timestamp.isoformat()
        d["amount"] = str(self.amount)
        d["price"] = str(self.price) if self.price is not None else None
        return d
