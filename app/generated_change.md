## Design note  
Implement a minimal in‑memory service that (1) stores per‑currency FX tolerances with validation and role‑based write access, (2) falls back to a configurable default tolerance, (3) evaluates breaks and produces read‑only alerts for material differences, and (4) records every tolerance change in an immutable audit log searchable by currency, user and date range.

## Implementation
```python
# fx_tolerance.py
"""
Core implementation for configurable per‑currency FX break tolerances,
default fallback, dashboard alerts and an immutable audit log.
"""

from __future__ import annotations
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Dict, List, Optional, Tuple, Iterable

# ----------------------------------------------------------------------
# Configuration & role handling
# ----------------------------------------------------------------------
ALLOWED_WRITE_ROLES = {"Ops", "Manager"}  # minimal role set for this sprint
_default_tolerance: float = 0.01  # system‑wide default, can be changed at runtime


def set_default_tolerance(value: float) -> None:
    """Update the global default tolerance (must be non‑negative)."""
    if not isinstance(value, (int, float)):
        raise ValueError("Default tolerance must be numeric")
    if value < 0:
        raise ValueError("Default tolerance cannot be negative")
    global _default_tolerance
    _default_tolerance = float(value)


def get_default_tolerance() -> float:
    """Return the current global default tolerance."""
    return _default_tolerance


# ----------------------------------------------------------------------
# Data models
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class ToleranceRecord:
    currency: str
    tolerance: float


@dataclass(frozen=True)
class AuditEntry:
    timestamp: datetime
    user_id: str
    role: str
    operation: str  # "create", "update", "delete"
    currency: str
    old_value: Optional[float]
    new_value: Optional[float]


@dataclass(frozen=True)
class BreakAlert:
    timestamp: datetime
    currency: str
    qty_diff: float
    mv_diff: float
    tolerance: float
    reason: str  # "exceeds_tolerance" or "missing_position"


# ----------------------------------------------------------------------
# Core stores (in‑memory for this minimal implementation)
# ----------------------------------------------------------------------
class ToleranceStore:
    """Manages per‑currency tolerance records with validation and audit."""

    def __init__(self, audit_log: "AuditLog"):
        self._store: Dict[str, float] = {}
        self._audit = audit_log

    def _check_write_permission(self, role: str) -> None:
        if role not in ALLOWED_WRITE_ROLES:
            raise PermissionError(f"Role '{role}' not permitted to modify tolerances")

    @staticmethod
    def _validate_currency(currency: str) -> None:
        if not isinstance(currency, str) or len(currency) != 3:
            raise ValueError("Currency must be a 3‑letter ISO code")

    @staticmethod
    def _validate_tolerance(value: float) -> None:
        if not isinstance(value, (int, float)):
            raise ValueError("Tolerance must be numeric")
        if value < 0:
            raise ValueError("Tolerance cannot be negative")

    def set_tolerance(self, user_id: str, role: str, currency: str, tolerance: float) -> None:
        """Create or update a tolerance record."""
        self._check_write_permission(role)
        self._validate_currency(currency)
        self._validate_tolerance(tolerance)

        old = self._store.get(currency)
        operation = "update" if old is not None else "create"
        self._store[currency] = float(tolerance)

        self._audit.record(
            user_id=user_id,
            role=role,
            operation=operation,
            currency=currency,
            old_value=old,
            new_value=tolerance,
        )

    def delete_tolerance(self, user_id: str, role: str, currency: str) -> None:
        """Remove a tolerance record."""
        self._check_write_permission(role)
        self._validate_currency(currency)

        if currency not in self._store:
            raise KeyError(f"No tolerance defined for currency {currency}")

        old = self._store.pop(currency)
        self._audit.record(
            user_id=user_id,
            role=role,
            operation="delete",
            currency=currency,
            old_value=old,
            new_value=None,
        )

    def get_tolerance(self, currency: str) -> float:
        """Return the configured tolerance or the system default."""
        return self._store.get(currency.upper(), get_default_tolerance())


class AuditLog:
    """Append‑only, immutable audit log."""

    def __init__(self):
        self._entries: List[AuditEntry] = []

    def record(
        self,
        user_id: str,
        role: str,
        operation: str,
        currency: str,
        old_value: Optional[float],
        new_value: Optional[float],
    ) -> None:
        entry = AuditEntry(
            timestamp=datetime.utcnow(),
            user_id=user_id,
            role=role,
            operation=operation,
            currency=currency.upper(),
            old_value=old_value,
            new_value=new_value,
        )
        self._entries.append(entry)

    def query(
        self,
        currency: Optional[str] = None,
        user_id: Optional[str] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> List[AuditEntry]:
        """Searchable by currency, user and date range."""
        result = self._entries
        if currency:
            result = [e for e in result if e.currency == currency.upper()]
        if user_id:
            result = [e for e in result if e.user_id == user_id]
        if start:
            result = [e for e in result if e.timestamp >= start]
        if end:
            result = [e for e in result if e.timestamp <= end]
        return list(result)


class ReconciliationEngine:
    """Evaluates breaks against tolerances and produces alerts."""

    def __init__(self, tolerance_store: ToleranceStore):
        self._tolerance_store = tolerance_store
        self._alerts: List[BreakAlert] = []

    def process_break(
        self,
        currency: str,
        qty_diff: float,
        mv_diff: float,
        missing_position: bool = False,
    ) -> Optional[BreakAlert]:
        """
        Evaluate a single break. Returns a BreakAlert if the break is material,
        otherwise None.
        """
        tolerance = self._tolerance_store.get_tolerance(currency)

        # Determine if alert is needed
        if missing_position:
            reason = "missing_position"
            alert_needed = True
        elif abs(qty_diff) > tolerance or abs(mv_diff) > tolerance:
            reason = "exceeds_tolerance"
            alert_needed = True
        else:
            alert_needed = False

        if not alert_needed:
            return None

        alert = BreakAlert(
            timestamp=datetime.utcnow(),
            currency=currency.upper(),
            qty_diff=qty_diff,
            mv_diff=mv_diff,
            tolerance=tolerance,
            reason=reason,
        )
        self._alerts.append(alert)
        return alert

    def get_alerts(self) -> List[BreakAlert]:
        """Read‑only view of all generated alerts."""
        return list(self._alerts)


# ----------------------------------------------------------------------
# Minimal unit‑test suite (covers default fallback & alert generation)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import unittest

    class FxToleranceTests(unittest.TestCase):
        def setUp(self):
            self.audit = AuditLog()
            self.store = ToleranceStore(self.audit)
            self.engine = ReconciliationEngine(self.store)
            # ensure a known default for each test
            set_default_tolerance(0.05)

        def test_default_fallback(self):
            # No per‑currency entry for GBP; should use default 0.05
            self.assertAlmostEqual(self.store.get_tolerance("GBP"), 0.05)

        def test_configured_tolerance_overrides_default(self):
            self.store.set_tolerance("alice", "Ops", "EUR", 0.02)
            self.assertAlmostEqual(self.store.get_tolerance("EUR"), 0.02)

        def test_alert_when_exceeds_configured_tolerance(self):
            self.store.set_tolerance("bob", "Manager", "JPY", 0.01)
            alert = self.engine.process_break("JPY", qty_diff=0.02, mv_diff=0.005)
            self.assertIsNotNone(alert)
            self.assertEqual(alert.currency, "JPY")
            self.assertEqual(alert.reason, "exceeds_tolerance")

        def test_no_alert_when_within_tolerance(self):
            self.store.set_tolerance("carol", "Ops", "USD", 0.03)
            alert = self.engine.process_break("USD", qty_diff=0.01, mv_diff=0.02)
            self.assertIsNone(alert)

        def test_alert_for_missing_position_regardless_of_tolerance(self):
            alert = self.engine.process_break("CHF", qty_diff=0.0, mv_diff=0.0, missing_position=True)
            self.assertIsNotNone(alert)
            self.assertEqual(alert.reason, "missing_position")

        def test_audit_log_records_create_update_delete(self):
            self.store.set_tolerance("dave", "Ops", "CAD", 0.04)  # create
            self.store.set_tolerance("dave", "Ops", "CAD", 0.06)  # update
            self.store.delete_tolerance("dave", "Ops", "CAD")    # delete
            entries = self.audit.query(user_id="dave")
            ops = [e.operation for e in entries]
            self.assertEqual(ops, ["create", "update", "delete"])

        def test_audit_log_is_immutable(self):
            self.store.set_tolerance("eve", "Manager", "AUD", 0.02)
            with self.assertRaises(AttributeError):
                # Attempt to modify an entry should raise because dataclass is frozen
                self.audit._entries[0].operation = "tamper"

    unittest.main(verbosity=2, exit=False)
```

## Self-review
- **Scope adherence:** Implements only the core logic (tolerance store, default fallback, alert generation, immutable audit log) without any UI or external integrations, matching the primary story.
- **Correctness:** Validation, role checks, and default handling follow the acceptance criteria; unit tests verify fallback, alert conditions, and audit immutability.
- **Maintainability:** Clear separation of concerns (store, audit, engine) and use of frozen dataclasses ensure future extensions (e.g., persistence) can be added with minimal impact.