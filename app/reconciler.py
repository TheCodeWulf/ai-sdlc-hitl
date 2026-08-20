"""
reconciler.py — the break-detection engine.

Given the manager's and custodian's position records for a valuation date,
it pairs them up and emits a Break for every mismatch in quantity or market
value, plus MISSING breaks where a position appears in only one source.

CURRENT STATE (intentionally basic — this is what Copilot will be asked to extend):
  * Quantity and market-value comparisons use a single hard-coded tolerance.
  * There is NO per-currency tolerance, and NO manager alerting.

The demo feature request: make tolerances configurable PER CURRENCY, and raise
an alert to the fund manager when a break exceeds its tolerance.
"""
from __future__ import annotations
from collections import defaultdict

from models import PositionRecord, Break, BreakType, Source

# TODO(demo): replace this single global tolerance with a per-currency rule set.
# Ops currently wants tiny FX rounding differences ignored, but the threshold
# should differ by currency (e.g. JPY vs USD). See tests/test_reconciler.py.
DEFAULT_TOLERANCE = 0.0


def _key(r: PositionRecord):
    return (r.fund_id, r.account_id, r.security_id, r.currency)


def reconcile(
    manager_records: list[PositionRecord],
    custodian_records: list[PositionRecord],
    tolerance: float = DEFAULT_TOLERANCE,
) -> list[Break]:
    """Compare two record sets and return the list of breaks."""
    by_key: dict[tuple, dict[Source, PositionRecord]] = defaultdict(dict)
    for r in manager_records:
        by_key[_key(r)][Source.MANAGER] = r
    for r in custodian_records:
        by_key[_key(r)][Source.CUSTODIAN] = r

    breaks: list[Break] = []
    for key, sides in by_key.items():
        fund_id, account_id, security_id, currency = key
        mgr = sides.get(Source.MANAGER)
        cust = sides.get(Source.CUSTODIAN)
        as_of = (mgr or cust).as_of

        # position present in only one source
        if mgr is None or cust is None:
            present = mgr or cust
            breaks.append(Break(
                fund_id=fund_id, account_id=account_id, security_id=security_id,
                currency=currency, break_type=BreakType.MISSING,
                manager_value=(mgr.market_value if mgr else None),
                custodian_value=(cust.market_value if cust else None),
                difference=(present.market_value if present else 0.0),
                as_of=as_of,
                note=f"Position only present in {present.source.value}",
            ))
            continue

        # quantity break
        qty_diff = mgr.quantity - cust.quantity
        if abs(qty_diff) > tolerance:
            breaks.append(Break(
                fund_id=fund_id, account_id=account_id, security_id=security_id,
                currency=currency, break_type=BreakType.QUANTITY,
                manager_value=mgr.quantity, custodian_value=cust.quantity,
                difference=qty_diff, as_of=as_of,
                within_tolerance=False,
            ))

        # market-value break
        mv_diff = mgr.market_value - cust.market_value
        if abs(mv_diff) > tolerance:
            breaks.append(Break(
                fund_id=fund_id, account_id=account_id, security_id=security_id,
                currency=currency, break_type=BreakType.MARKET_VALUE,
                manager_value=mgr.market_value, custodian_value=cust.market_value,
                difference=mv_diff, as_of=as_of,
                within_tolerance=False,
            ))

    return breaks
