"""
test_reconciler.py — tests for the break-detection engine.

These pass against the CURRENT engine. They deliberately do NOT yet cover
per-currency tolerances or manager alerting — that's the gap the demo feature
(and Copilot) will close. See the SKIPPED test at the bottom for the target.
"""
from datetime import date
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

import pytest
from models import PositionRecord, Source, BreakType
from reconciler import reconcile

T = date(2026, 8, 3)


def mgr(sec, ccy, qty, mv):
    return PositionRecord("HF-ALPHA", "BOOK-1", sec, ccy, qty, mv, Source.MANAGER, T)


def cust(sec, ccy, qty, mv):
    return PositionRecord("HF-ALPHA", "BOOK-1", sec, ccy, qty, mv, Source.CUSTODIAN, T)


def test_matching_positions_produce_no_breaks():
    m = [mgr("US0378331005", "USD", 1000, 190000.0)]
    c = [cust("US0378331005", "USD", 1000, 190000.0)]
    assert reconcile(m, c) == []


def test_quantity_mismatch_is_a_break():
    m = [mgr("US0378331005", "USD", 1000, 190000.0)]
    c = [cust("US0378331005", "USD", 998, 189620.0)]
    breaks = reconcile(m, c)
    types = {b.break_type for b in breaks}
    assert BreakType.QUANTITY in types
    assert BreakType.MARKET_VALUE in types


def test_position_missing_in_custodian_is_a_break():
    m = [mgr("JP3633400001", "JPY", 5000, 7500000.0)]
    c = []
    breaks = reconcile(m, c)
    assert len(breaks) == 1
    assert breaks[0].break_type == BreakType.MISSING


def test_global_tolerance_suppresses_small_breaks():
    # a 1.5 market-value difference is ignored when tolerance is 2.0
    m = [mgr("US0378331005", "USD", 1000, 190000.0)]
    c = [cust("US0378331005", "USD", 1000, 189998.5)]
    assert reconcile(m, c, tolerance=2.0) == []


@pytest.mark.skip(reason="TARGET FEATURE: per-currency tolerance + manager alert — to be built")
def test_per_currency_tolerance_and_alert():
    """
    Target behaviour the demo feature must satisfy:
      * Tolerances are configured PER CURRENCY (e.g. USD=2.0, JPY=500.0).
      * A break within its currency's tolerance is flagged within_tolerance=True
        and does NOT alert.
      * A break exceeding its currency's tolerance alerts the manager.
    """
    # e.g. reconcile_with_rules(m, c, tolerances={"USD": 2.0, "JPY": 500.0})
    ...
