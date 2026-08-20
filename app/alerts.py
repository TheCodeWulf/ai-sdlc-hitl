"""
alerts.py — manager alerting stub.

Today this only logs. The demo feature will call it when a break exceeds its
per-currency tolerance, so the fund manager gets a real-time notification in
the reconciliation dashboard (analogous to a Rec-Dashboard break alert).
"""
from __future__ import annotations
import logging

from models import Break

logger = logging.getLogger("rec.alerts")


def alert_manager(brk: Break) -> None:
    """Notify the fund manager that a material break needs attention.

    TODO(demo): this is a stub. In the feature, only breaks that exceed their
    per-currency tolerance should reach here, and the alert should carry the
    tolerance that was breached so the manager sees why it fired.
    """
    logger.warning(
        "BREAK ALERT | fund=%s account=%s security=%s %s diff=%.4f %s (as_of=%s)",
        brk.fund_id, brk.account_id, brk.security_id,
        brk.break_type.value, brk.difference, brk.currency, brk.as_of,
    )
