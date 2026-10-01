**Design note**  
Create a lightweight, in‑memory service that (1) stores per‑currency tolerances with a system‑wide default, (2) records every tolerance change in an immutable audit log, (3) evaluates incoming breaks against the tolerance and logs suppressed breaks, and (4) emits read‑only alerts for breaks that exceed the tolerance. Permissions are modelled as a simple user‑id whitelist.

---

## Implementation
```python
# fx_tolerance.py
"""
Minimal implementation of per‑currency FX tolerance configuration,
audit logging, break suppression, and dashboard alerts.
"""

import threading
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

# ----------------------------------------------------------------------
# Simple permission model (replace with real auth in production)
# ----------------------------------------------------------------------
AUTHORIZED_USERS = {"ops_manager", "admin"}  # user IDs allowed to edit tolerances


# ----------------------------------------------------------------------
# Immutable audit log (append‑only)
# ----------------------------------------------------------------------
class AuditRecord:
    __slots__ = ("user_id", "timestamp", "op_type", "currency", "old_value", "new_value")

    def __init__(
        self,
        user_id: str,
        op_type: str,
        currency: str,
        old_value: Optional[float],
        new_value: Optional[float],
    ):
        self.user_id = user_id
        self.timestamp = datetime.now(timezone.utc)
        self.op_type = op_type  # "create", "update", "delete"
        self.currency = currency.upper()
        self.old_value = old_value
        self.new_value = new_value

    def to_dict(self) -> Dict:
        return {
            "user_id": self.user_id,
            "timestamp": self.timestamp.isoformat(),
            "op_type": self.op_type,
            "currency": self.currency,
            "old_value": self.old_value,
            "new_value": self.new_value,
        }


class AuditLog:
    """Append‑only, thread‑safe audit log."""

    def __init__(self):
        self._records: List[AuditRecord] = []
        self._lock = threading.Lock()

    def append(self, record: AuditRecord):
        with self._lock:
            self._records.append(record)

    def query(self) -> List[Dict]:
        """Return a copy of audit data for compliance tools."""
        with self._lock:
            return [r.to_dict() for r in self._records]

    # No delete / update methods – immutable by design


# ----------------------------------------------------------------------
# Tolerance store with default fallback
# ----------------------------------------------------------------------
class ToleranceStore:
    """Thread‑safe store for per‑currency tolerances."""

    def __init__(self, default_tolerance: float = 0.0):
        self._default = default_tolerance
        self._tolerances: Dict[str, float] = {}
        self._lock = threading.Lock()
        self.audit_log = AuditLog()

    def _check_permission(self, user_id: str):
        if user_id not in AUTHORIZED_USERS:
            raise PermissionError(f"User '{user_id}' not authorized to edit tolerances")

    def set_tolerance(self, user_id: str, currency: str, value: float):
        """Create or update a tolerance. Logs audit."""
        self._check_permission(user_id)
        if not isinstance(value, (int, float)):
            raise ValueError("Tolerance must be numeric")
        if value < 0:
            raise ValueError("Tolerance cannot be negative")

        cur = currency.upper()
        with self._lock:
            old = self._tolerances.get(cur)
            op_type = "create" if old is None else "update"
            self._tolerances[cur] = float(value)
        self.audit_log.append(
            AuditRecord(user_id, op_type, cur, old, float(value))
        )

    def delete_tolerance(self, user_id: str, currency: str):
        """Remove a specific currency tolerance (reverts to default)."""
        self._check_permission(user_id)
        cur = currency.upper()
        with self._lock:
            if cur not in self._tolerances:
                raise KeyError(f"No tolerance defined for {cur}")
            old = self._tolerances.pop(cur)
        self.audit_log.append(
            AuditRecord(user_id, "delete", cur, old, None)
        )

    def get_tolerance(self, currency: str) -> float:
        """Return tolerance for currency or default fallback."""
        cur = currency.upper()
        with self._lock:
            return self._tolerances.get(cur, self._default)

    def set_default(self, user_id: str, value: float):
        """Change system‑wide default tolerance."""
        self._check_permission(user_id)
        if not isinstance(value, (int, float)):
            raise ValueError("Default tolerance must be numeric")
        if value < 0:
            raise ValueError("Default tolerance cannot be negative")
        with self._lock:
            old = self._default
            self._default = float(value)
        self.audit_log.append(
            AuditRecord(user_id, "update", "DEFAULT", old, float(value))
        )


# ----------------------------------------------------------------------
# Break processing & suppression
# ----------------------------------------------------------------------
class SuppressionLog:
    """Records suppressed breaks for audit purposes."""

    def __init__(self):
        self._entries: List[Dict] = []
        self._lock = threading.Lock()

    def log(self, break_id: str, reason: str):
        entry = {
            "break_id": break_id,
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        with self._lock:
            self._entries.append(entry)

    def query(self) -> List[Dict]:
        with self._lock:
            return list(self._entries)


class BreakProcessor:
    """
    Evaluates incoming breaks against tolerances.
    Returns (is_suppressed: bool, alert: Optional[Dict]).
    """

    def __init__(self, tolerance_store: ToleranceStore):
        self.tolerance_store = tolerance_store
        self.suppression_log = SuppressionLog()
        self.alerts: List[Dict] = []  # in‑memory dashboard feed
        self._alert_lock = threading.Lock()

    def process_break(self, break_data: Dict) -> Tuple[bool, Optional[Dict]]:
        """
        Expected break_data keys:
            - id: unique identifier (str)
            - currency: ISO code (str)
            - qty_diff: float (absolute quantity difference)
            - mv_diff: float (absolute market‑value difference)
            - source_a_present: bool
            - source_b_present: bool
        """
        # Validate required fields
        required = {"id", "currency", "qty_diff", "mv_diff", "source_a_present", "source_b_present"}
        missing = required - break_data.keys()
        if missing:
            raise ValueError(f"Missing break fields: {missing}")

        # If break exists in only one source, never suppress
        if not (break_data["source_a_present"] and break_data["source_b_present"]):
            return False, self._create_alert(break_data, suppressed=False)

        tol = self.tolerance_store.get_tolerance(break_data["currency"])
        qty_ok = abs(break_data["qty_diff"]) <= tol
        mv_ok = abs(break_data["mv_diff"]) <= tol

        if qty_ok and mv_ok:
            # Suppressed
            self.suppression_log.log(break_data["id"], "within tolerance")
            return True, None

        # Not suppressed – generate alert
        alert = self._create_alert(break_data, suppressed=False, tolerance=tol)
        return False, alert

    def _create_alert(self, break_data: Dict, suppressed: bool, tolerance: Optional[float] = None) -> Dict:
        alert = {
            "break_id": break_data["id"],
            "currency": break_data["currency"].upper(),
            "qty_diff": break_data["qty_diff"],
            "mv_diff": break_data["mv_diff"],
            "tolerance": tolerance
            if tolerance is not None
            else self.tolerance_store.get_tolerance(break_data["currency"]),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "read_only": True,
        }
        # Store in dashboard feed (thread‑safe)
        with self._alert_lock:
            self.alerts.append(alert)
        return alert

    def get_alerts(self) -> List[Dict]:
        """Return a copy of current alerts (read‑only view)."""
        with self._alert_lock:
            return list(self.alerts)


# ----------------------------------------------------------------------
# Example usage (would be replaced by real UI / service endpoints)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    store = ToleranceStore(default_tolerance=0.5)
    store.set_tolerance("ops_manager", "USD", 1.0)
    store.set_tolerance("ops_manager", "EUR", 0.8)

    processor = BreakProcessor(store)

    sample_breaks = [
        {
            "id": "BRK001",
            "currency": "USD",
            "qty_diff": 0.4,
            "mv_diff": 0.3,
            "source_a_present": True,
            "source_b_present": True,
        },
        {
            "id": "BRK002",
            "currency": "EUR",
            "qty_diff": 1.2,
            "mv_diff": 0.6,
            "source_a_present": True,
            "source_b_present": True,
        },
        {
            "id": "BRK003",
            "currency": "JPY",
            "qty_diff": 0.2,
            "mv_diff": 0.1,
            "source_a_present": True,
            "source_b_present": False,  # missing position → never suppressed
        },
    ]

    for brk in sample_breaks:
        suppressed, alert = processor.process_break(brk)
        print(f"Break {brk['id']} suppressed={suppressed}")

    print("\nCurrent alerts:")
    for a in processor.get_alerts():
        print(a)

    print("\nAudit log:")
    for r in store.audit_log.query():
        print(r)

    print("\nSuppression log:")
    for s in processor.suppression_log.query():
        print(s)
```

---

## Self‑review
- **Correctness**: Implements all acceptance criteria – per‑currency tolerance with default, permission checks, validation, immutable audit log, suppression logic, and read‑only alerts with required fields.
- **Thread safety**: Uses `threading.Lock` around mutable shared structures (tolerances, audit log, alerts, suppression log) to be safe in a concurrent service environment.
- **Minimalism**: No external frameworks or persistence layers; all data lives in memory, matching the “clean and minimal” directive while still being easily replaceable with DB adapters.