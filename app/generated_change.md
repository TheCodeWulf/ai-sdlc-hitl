## Design note
- Use lightweight in‑memory stores (dicts + lists) with dataclasses to model tolerance configurations, immutable audit entries, suppressed‑break logs, and dashboard alerts.  
- Enforce role‑based access via a simple decorator that raises a 403‑style `PermissionError`. All operations that modify tolerances write an immutable audit record. Suppression and alert logic reference the configuration (falling back to a global default) and emit log/alert entries that can be queried by currency and date range.

## Implementation
```python
# fx_tolerance.py
"""
Minimal implementation of per‑currency FX break tolerance configuration,
audit trail, suppression logic, and dashboard alerts.
"""

import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

# ----------------------------------------------------------------------
# Simple role‑checking decorator
# ----------------------------------------------------------------------
def require_role(role: str):
    def decorator(func):
        def wrapper(*args, **kwargs):
            user = kwargs.get("user")
            if not user or role not in user.roles:
                raise PermissionError("403 Forbidden: insufficient role")
            return func(*args, **kwargs)
        return wrapper
    return decorator

# ----------------------------------------------------------------------
# Data models
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class ToleranceEntry:
    currency: str
    tolerance: float  # absolute tolerance applied to both qty & MV diff

@dataclass(frozen=True)
class AuditRecord:
    user_id: str
    timestamp: datetime
    operation: str  # "create", "update", "delete"
    currency: str
    new_value: Optional[float]  # None for delete

@dataclass(frozen=True)
class Break:
    break_id: str
    currency: str
    qty_diff: float
    mv_diff: float
    missing_position: bool = False

@dataclass(frozen=True)
class SuppressedLog:
    break_id: str
    currency: str
    applied_tolerance: float
    timestamp: datetime = field(default_factory=datetime.utcnow)

@dataclass
class Alert:
    break_id: str
    currency: str
    actual_qty_diff: float
    actual_mv_diff: float
    tolerance: float
    timestamp: datetime = field(default_factory=datetime.utcnow)
    cleared: bool = False

# ----------------------------------------------------------------------
# Core service
# ----------------------------------------------------------------------
class FXToleranceService:
    def __init__(self, default_tolerance: float = 0.0):
        self._default_tolerance = default_tolerance
        self._tolerances: Dict[str, ToleranceEntry] = {}
        self._audit_log: List[AuditRecord] = []
        self._suppressed_log: List[SuppressedLog] = []
        self._alerts: List[Alert] = []
        self._lock = threading.RLock()

    # ---------- Tolerance CRUD ----------
    @require_role("ToleranceEditor")
    def create_tolerance(self, currency: str, tolerance: float, *, user):
        with self._lock:
            if currency in self._tolerances:
                raise ValueError(f"Tolerance for {currency} already exists")
            entry = ToleranceEntry(currency.upper(), tolerance)
            self._tolerances[entry.currency] = entry
            self._audit_log.append(
                AuditRecord(
                    user_id=user.id,
                    timestamp=datetime.utcnow(),
                    operation="create",
                    currency=entry.currency,
                    new_value=tolerance,
                )
            )
            return entry

    @require_role("ToleranceEditor")
    def update_tolerance(self, currency: str, tolerance: float, *, user):
        with self._lock:
            key = currency.upper()
            if key not in self._tolerances:
                raise KeyError(f"No tolerance entry for {currency}")
            self._tolerances[key] = ToleranceEntry(key, tolerance)
            self._audit_log.append(
                AuditRecord(
                    user_id=user.id,
                    timestamp=datetime.utcnow(),
                    operation="update",
                    currency=key,
                    new_value=tolerance,
                )
            )
            return self._tolerances[key]

    @require_role("ToleranceEditor")
    def delete_tolerance(self, currency: str, *, user):
        with self._lock:
            key = currency.upper()
            if key not in self._tolerances:
                raise KeyError(f"No tolerance entry for {currency}")
            del self._tolerances[key]
            self._audit_log.append(
                AuditRecord(
                    user_id=user.id,
                    timestamp=datetime.utcnow(),
                    operation="delete",
                    currency=key,
                    new_value=None,
                )
            )

    def get_tolerance(self, currency: str) -> float:
        """Return configured tolerance or fallback to default."""
        with self._lock:
            entry = self._tolerances.get(currency.upper())
            return entry.tolerance if entry else self._default_tolerance

    # ---------- Audit query ----------
    def query_audit(
        self,
        currency: Optional[str] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> List[AuditRecord]:
        with self._lock:
            result = self._audit_log
            if currency:
                result = [r for r in result if r.currency == currency.upper()]
            if start:
                result = [r for r in result if r.timestamp >= start]
            if end:
                result = [r for r in result if r.timestamp <= end]
            return list(result)

    # ---------- Suppression ----------
    def suppress_breaks(self, breaks: List[Break]) -> Tuple[List[Break], int]:
        """
        Returns the list of breaks that survive (not suppressed) and the count
        of suppressed breaks for the current run.
        """
        surviving = []
        suppressed_cnt = 0
        now = datetime.utcnow()

        with self._lock:
            for br in breaks:
                if br.missing_position:
                    surviving.append(br)
                    continue

                tol = self.get_tolerance(br.currency)
                if abs(br.qty_diff) <= tol and abs(br.mv_diff) <= tol:
                    # suppressed
                    self._suppressed_log.append(
                        SuppressedLog(
                            break_id=br.break_id,
                            currency=br.currency,
                            applied_tolerance=tol,
                            timestamp=now,
                        )
                    )
                    suppressed_cnt += 1
                else:
                    surviving.append(br)

        return surviving, suppressed_cnt

    def get_suppressed_log(
        self,
        currency: Optional[str] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> List[SuppressedLog]:
        with self._lock:
            logs = self._suppressed_log
            if currency:
                logs = [l for l in logs if l.currency == currency.upper()]
            if start:
                logs = [l for l in logs if l.timestamp >= start]
            if end:
                logs = [l for l in logs if l.timestamp <= end]
            return list(logs)

    # ---------- Alert handling ----------
    def process_breaks_for_alerts(self, breaks: List[Break]) -> None:
        """
        Generates alerts for material breaks (qty OR mv diff exceeds tolerance).
        Alerts are kept for 30 days; stale alerts are purged automatically.
        """
        now = datetime.utcnow()
        expiry = now - timedelta(days=30)

        with self._lock:
            # purge old alerts
            self._alerts = [a for a in self._alerts if a.timestamp >= expiry]

            for br in breaks:
                if br.missing_position:
                    # missing positions are always material (no suppression)
                    material = True
                else:
                    tol = self.get_tolerance(br.currency)
                    material = (abs(br.qty_diff) > tol) or (abs(br.mv_diff) > tol)

                if material:
                    self._alerts.append(
                        Alert(
                            break_id=br.break_id,
                            currency=br.currency,
                            actual_qty_diff=br.qty_diff,
                            actual_mv_diff=br.mv_diff,
                            tolerance=self.get_tolerance(br.currency),
                        )
                    )

    def get_active_alerts(
        self,
        currency: Optional[str] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> List[Alert]:
        """Return alerts that have not been cleared and match filters."""
        with self._lock:
            alerts = [a for a in self._alerts if not a.cleared]
            if currency:
                alerts = [a for a in alerts if a.currency == currency.upper()]
            if start:
                alerts = [a for a in alerts if a.timestamp >= start]
            if end:
                alerts = [a for a in alerts if a.timestamp <= end]
            return list(alerts)

    def clear_alert(self, break_id: str, *, user) -> None:
        """Mark an alert as cleared; any user may clear (read‑only UI)."""
        with self._lock:
            for a in self._alerts:
                if a.break_id == break_id and not a.cleared:
                    a.cleared = True
                    # optional audit could be added here
                    break

    def alert_badge_count(self) -> int:
        """Real‑time count of active alerts."""
        with self._lock:
            return sum(1 for a in self._alerts if not a.cleared)

# ----------------------------------------------------------------------
# Example user object for role checking (lightweight stub)
# ----------------------------------------------------------------------
@dataclass
class User:
    id: str
    roles: List[str]

# ----------------------------------------------------------------------
# Simple demonstration (not part of production API)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    svc = FXToleranceService(default_tolerance=0.05)
    admin = User(id="u1", roles=["ToleranceEditor"])
    # configure some tolerances
    svc.create_tolerance("USD", 0.1, user=admin)
    svc.create_tolerance("EUR", 0.2, user=admin)

    # sample breaks
    sample_breaks = [
        Break("b1", "USD", qty_diff=0.08, mv_diff=0.04),
        Break("b2", "EUR", qty_diff=0.25, mv_diff=0.15),
        Break("b3", "JPY", qty_diff=0.03, mv_diff=0.02),  # uses default 0.05
        Break("b4", "GBP", qty_diff=0.01, mv_diff=0.01, missing_position=True),
    ]

    # suppression step
    surviving, sup_cnt = svc.suppress_breaks(sample_breaks)
    print(f"Suppressed {sup_cnt} breaks, {len(surviving)} survive.")

    # alert generation
    svc.process_breaks_for_alerts(sample_breaks)
    print(f"Active alerts: {svc.alert_badge_count()}")
    for a in svc.get_active_alerts():
        print(asdict(a))
```

## Self-review
- **Correctness:** All acceptance criteria are covered: CRUD with role check, immutable audit records, default fallback, suppression respecting both qty and MV thresholds, logging of suppressed breaks, and real‑time alert generation with 30‑day retention.
- **Simplicity:** Uses only the Python standard library and in‑memory structures, keeping the implementation minimal while still testable and extensible.
- **Potential improvements:** Replace in‑memory stores with a persistent DB (e.g., SQLite) for production, add pagination to audit/alert queries, and integrate with a proper RBAC service when available.