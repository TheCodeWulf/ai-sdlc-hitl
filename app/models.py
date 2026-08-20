"""
models.py — core domain types for the reconciliation service.

Context: a fund administrator (e.g. a Northern-Trust-style middle/back office)
receives position records from two independent sources each day — the fund
manager's own book and the custodian/counterparty's book — and must reconcile
them. Any mismatch is a "break" that operations must investigate and clear.

This is a SYNTHETIC, representative model. It mirrors the shape of the problem
(positions, dual sources, breaks, tolerances) — it is NOT Omnium code.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date
from enum import Enum


class Source(str, Enum):
    """The two record sets we reconcile against each other."""
    MANAGER = "MANAGER"        # the fund manager's book of record
    CUSTODIAN = "CUSTODIAN"    # the custodian / counterparty statement


class BreakType(str, Enum):
    QUANTITY = "QUANTITY"      # share/unit count disagrees
    MARKET_VALUE = "MARKET_VALUE"  # valuation disagrees
    MISSING = "MISSING"        # position present in one source, absent in the other


@dataclass(frozen=True)
class PositionRecord:
    """A single holding as reported by one source, for one fund, on one date."""
    fund_id: str          # e.g. "HF-ALPHA"
    account_id: str       # sub-account / strategy book
    security_id: str      # ISIN/CUSIP-style identifier
    currency: str         # ISO 4217, e.g. "USD", "EUR", "JPY"
    quantity: float       # units / shares held
    market_value: float   # valuation in `currency`
    source: Source
    as_of: date           # the valuation date (T)


@dataclass
class Break:
    """A reconciliation difference between the two sources for one position."""
    fund_id: str
    account_id: str
    security_id: str
    currency: str
    break_type: BreakType
    manager_value: float | None
    custodian_value: float | None
    difference: float             # signed: manager - custodian
    as_of: date
    within_tolerance: bool = False  # set by the break detector using the rule set
    note: str = ""

    @property
    def abs_difference(self) -> float:
        return abs(self.difference)
