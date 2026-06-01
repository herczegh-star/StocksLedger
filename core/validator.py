"""Validator: syntaktická kontrola RAW řádku. Žádná sémantika."""
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import List, Tuple
from core.model import RawRow, VALID_TYPES


def validate_row(row: RawRow) -> Tuple[bool, List[str]]:
    errors = []

    if not row.id:
        errors.append("Chybí id.")

    if not isinstance(row.timestamp, datetime):
        errors.append(f"timestamp není datetime: {row.timestamp}")

    if row.type not in VALID_TYPES:
        errors.append(f"Neplatný type: '{row.type}'. Povolené: {VALID_TYPES}")

    if not row.asset or not isinstance(row.asset, str):
        errors.append("Chybí nebo neplatný asset.")

    try:
        amt = Decimal(str(row.amount))
        if amt == 0:
            errors.append("amount je 0.")
    except (InvalidOperation, TypeError):
        errors.append(f"amount není platné číslo: {row.amount}")

    if not row.currency or not isinstance(row.currency, str):
        errors.append("Chybí nebo neplatný currency.")

    if row.price is not None:
        try:
            Decimal(str(row.price))
        except (InvalidOperation, TypeError):
            errors.append(f"price není platné číslo: {row.price}")

    if not row.venue or not isinstance(row.venue, str):
        errors.append("Chybí nebo neplatný venue.")

    return (len(errors) == 0, errors)


def validate_rows(rows: List[RawRow]) -> Tuple[List[RawRow], List[Tuple[int, List[str]]]]:
    valid = []
    invalid = []
    for i, row in enumerate(rows):
        ok, errs = validate_row(row)
        if ok:
            valid.append(row)
        else:
            invalid.append((i, errs))
    return valid, invalid
