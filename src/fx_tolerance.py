# fx_tolerance.py
"""
A minimal FX tolerance and alerting system.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Tuple, Optional


# --------------------------------------------------------------------------- #
# Data structures
# --------------------------------------------------------------------------- #

@dataclass
class Break:
    """Represents a reconciliation break."""
    id: int
    currency: str
    quantity_diff: float
    market_value_diff: float
    missing_position: bool = False


@dataclass
class AuditEntry:
    """Immutable audit log entry for tolerance changes."""
    timestamp: datetime
    user: str
    currency: Optional[str]  # None for default tolerance
    old_value: float
    new_value: float


# --------------------------------------------------------------------------- #
# Core logic
# --------------------------------------------------------------------------- #

class ToleranceConfig:
    """Manages per‑currency tolerances and audit trail."""
    def __init__(self, default_tolerance: float = 0.01):
        self._tolerances: Dict[str, float] = {}
        self._default = default_tolerance
        self.audit_log: List[AuditEntry] = []

    def set_tolerance(self, user: str, currency: Optional[str], value: float):
        """Set tolerance for a specific currency or the default."""
        old = self._tolerances.get(currency, self._default if currency is None else None)
        if currency is None:
            self._default = value
        else:
            self._tolerances[currency] = value
        self.audit_log.append(
            AuditEntry(
                timestamp=datetime.utcnow(),
                user=user,
                currency=currency,
                old_value=old,
                new_value=value,
            )
        )

    def get_tolerance(self, currency: str) -> float:
        """Return tolerance for the given currency."""
        return self._tolerances.get(currency, self._default)


class ReconciliationEngine:
    """Evaluates breaks against tolerances and generates alerts."""
    def __init__(self, config: ToleranceConfig):
        self.config = config
        self.alerts: List[Break] = []

    def process_breaks(self, breaks: List[Break]) -> List[Break]:
        """Return list of breaks that should be hidden (i.e., below tolerance)."""
        visible: List[Break] = []
        for br in breaks:
            if br.missing_position:
                # Always flag missing positions
                visible.append(br)
                continue

            tol = self.config.get_tolerance(br.currency)
            if abs(br.quantity_diff) <= tol and abs(br.market_value_diff) <= tol:
                # Hidden: do not add to visible list
                continue
            else:
                # Exceeds tolerance: alert
                self.alerts.append(br)
                visible.append(br)
        return visible

    def get_alerts(self) -> List[Break]:
        """Return all alerts generated in the last run."""
        return self.alerts


# --------------------------------------------------------------------------- #
# Example usage (would normally be in tests or application code)
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    # Setup
    cfg = ToleranceConfig(default_tolerance=0.02)
    cfg.set_tolerance(user="alice", currency="USD", value=0.01)
    cfg.set_tolerance(user="bob", currency=None, value=0.03)  # change default

    engine = ReconciliationEngine(cfg)

    # Sample breaks
    breaks = [
        Break(id=1, currency="USD", quantity_diff=0.005, market_value_diff=0.004),
        Break(id=2, currency="EUR", quantity_diff=0.02, market_value_diff=0.025),
        Break(id=3, currency="JPY", quantity_diff=0.04, market_value_diff=0.05),
        Break(id=4, currency="GBP", quantity_diff=0.01, market_value_diff=0.015),
        Break(id=5, currency="USD", quantity_diff=0.0, market_value_diff=0.0, missing_position=True),
    ]

    visible = engine.process_breaks(breaks)
    print("Visible breaks:", [b.id for b in visible])
    print("Alerts:", [b.id for b in engine.get_alerts()])

    # Audit log
    for entry in cfg.audit_log:
        print(entry)
