**Design note**  
Create a lightweight Python module that (1) stores per‑currency tolerances with a default fallback, (2) records every change in an immutable audit‑log, (3) lets the reconciliation engine apply those tolerances while always treating missing‑position breaks as material, and (4) pushes real‑time alerts to a simple in‑memory dashboard for managers.

---

## Implementation
```python
# fx_tolerance.py
"""
Minimal implementation of configurable per‑currency FX break tolerance,
audit logging and manager alerts.
"""

import threading
import time
from collections import defaultdict
from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Tuple

# ----------------------------------------------------------------------
# Simple role‑based access control placeholder (to be replaced by real ACL)
# ----------------------------------------------------------------------
ALLOWED_ROLES = {"ops_manager", "compliance_manager"}  # extend later


def _has_permission(user_roles: List[str]) -> bool:
    return any(role in ALLOWED_ROLES for role in user_roles)


# ----------------------------------------------------------------------
# Data models
# ----------------------------------------------------------------------
@dataclass(frozen=True)
class ToleranceRecord:
    currency: str          # ISO‑4217 code, "DEFAULT" for fallback
    value: float           # tolerance amount (absolute)
    updated_by: str        # user id
    updated_at: float      # epoch seconds
    reason: Optional[str] = None


@dataclass(frozen=True)
class AuditEntry:
    currency: str
    old_value: Optional[float]
    new_value: float
    user_id: str
    timestamp: float
    reason: Optional[str] = None


@dataclass(frozen=True)
class Alert:
    manager_id: str
    currency: str
    break_amount: float
    tolerance_used: float
    position_link: str
    generated_at: float


# ----------------------------------------------------------------------
# Core services
# ----------------------------------------------------------------------
class ToleranceStore:
    """
    Stores per‑currency tolerance records and a default.
    Updates are immutable – old records stay in the audit log.
    """
    def __init__(self, default_value: float = 0.0):
        self._lock = threading.Lock()
        self._tolerances: Dict[str, ToleranceRecord] = {}
        self._default = ToleranceRecord(
            currency="DEFAULT",
            value=default_value,
            updated_by="system",
            updated_at=time.time(),
        )
        self.audit_log = AuditLog()

    def get(self, currency: str) -> float:
        """Return tolerance for currency; fall back to default."""
        rec = self._tolerances.get(currency.upper())
        return rec.value if rec else self._default.value

    def set(self, currency: str, value: float, user_id: str,
            user_roles: List[str], reason: Optional[str] = None) -> None:
        """Create/overwrite tolerance; records immutable audit entry."""
        if not _has_permission(user_roles):
            raise PermissionError("User lacks permission to set tolerances")

        currency = currency.upper()
        with self._lock:
            old = self._tolerances.get(currency)
            old_val = old.value if old else None
            new_rec = ToleranceRecord(
                currency=currency,
                value=value,
                updated_by=user_id,
                updated_at=time.time(),
                reason=reason,
            )
            self._tolerances[currency] = new_rec
            # audit
            self.audit_log.append(
                AuditEntry(
                    currency=currency,
                    old_value=old_val,
                    new_value=value,
                    user_id=user_id,
                    timestamp=time.time(),
                    reason=reason,
                )
            )

    def set_default(self, value: float, user_id: str,
                    user_roles: List[str], reason: Optional[str] = None) -> None:
        """Update the default tolerance (currency='DEFAULT')."""
        self.set("DEFAULT", value, user_id, user_roles, reason)

    def history(self, currency: str) -> List[AuditEntry]:
        """Return full audit history for a currency, newest last."""
        return self.audit_log.query(currency.upper())


class AuditLog:
    """Append‑only in‑memory ledger."""
    def __init__(self):
        self._entries: List[AuditEntry] = []
        self._lock = threading.Lock()

    def append(self, entry: AuditEntry) -> None:
        with self._lock:
            self._entries.append(entry)

    def query(self, currency: str) -> List[AuditEntry]:
        with self._lock:
            return [e for e in self._entries if e.currency == currency]


class AlertService:
    """Collects alerts for managers; in a real system this would push to UI."""
    def __init__(self):
        self._alerts: Dict[str, List[Alert]] = defaultdict(list)
        self._lock = threading.Lock()

    def push(self, manager_id: str, currency: str,
             break_amount: float, tolerance_used: float,
             position_link: str) -> None:
        alert = Alert(
            manager_id=manager_id,
            currency=currency,
            break_amount=break_amount,
            tolerance_used=tolerance_used,
            position_link=position_link,
            generated_at=time.time(),
        )
        with self._lock:
            self._alerts[manager_id].append(alert)

    def get_for_manager(self, manager_id: str) -> List[Alert]:
        with self._lock:
            return list(self._alerts.get(manager_id, []))


class ReconciliationEngine:
    """
    Evaluates a pair of positions and decides if a break is material.
    Logs the applied tolerance for debugging.
    """
    def __init__(self, tolerance_store: ToleranceStore,
                 alert_service: AlertService):
        self.tolerance_store = tolerance_store
        self.alert_service = alert_service
        self._debug_log: List[Dict] = []  # simple in‑memory debug trace

    def evaluate(self,
                 currency: str,
                 qty_diff: float,
                 mv_diff: float,
                 source_a_has: bool,
                 source_b_has: bool,
                 manager_id: str,
                 position_link: str) -> Tuple[bool, str]:
        """
        Returns (is_material_break, reason). Also generates alerts when needed.
        """
        # Missing‑position break – always material
        if source_a_has != source_b_has:
            reason = "Missing position in one source"
            self._record_debug(currency, qty_diff, mv_diff, None, True, reason)
            self.alert_service.push(
                manager_id, currency,
                break_amount=abs(qty_diff) if not source_a_has or not source_b_has else abs(mv_diff),
                tolerance_used=0.0,
                position_link=position_link,
            )
            return True, reason

        # Apply tolerance to both qty and market‑value differences
        tol = self.tolerance_store.get(currency)
        qty_material = abs(qty_diff) > tol
        mv_material = abs(mv_diff) > tol
        is_material = qty_material or mv_material

        reason = "Within tolerance" if not is_material else "Exceeds tolerance"
        self._record_debug(currency, qty_diff, mv_diff, tol, is_material, reason)

        if is_material:
            # generate alert for the manager
            self.alert_service.push(
                manager_id,
                currency,
                break_amount=max(abs(qty_diff), abs(mv_diff)),
                tolerance_used=tol,
                position_link=position_link,
            )
        return is_material, reason

    def _record_debug(self, currency, qty_diff, mv_diff, tol, material, reason):
        entry = {
            "timestamp": time.time(),
            "currency": currency,
            "qty_diff": qty_diff,
            "mv_diff": mv_diff,
            "tolerance_used": tol,
            "material": material,
            "reason": reason,
        }
        self._debug_log.append(entry)

    def get_debug_log(self) -> List[Dict]:
        return list(self._debug_log)


# ----------------------------------------------------------------------
# Example usage (would be removed/relocated in production code)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    store = ToleranceStore(default_value=0.5)
    alerts = AlertService()
    engine = ReconciliationEngine(store, alerts)

    # Ops manager sets specific tolerances
    store.set("USD", 1.0, user_id="alice", user_roles=["ops_manager"], reason="Market noise")
    store.set("EUR", 0.8, user_id="bob", user_roles=["compliance_manager"])

    # Reconcile a pair
    material, msg = engine.evaluate(
        currency="USD",
        qty_diff=0.4,
        mv_diff=0.3,
        source_a_has=True,
        source_b_has=True,
        manager_id="mgr_1",
        position_link="http://example.com/pos/123",
    )
    print("Material break?", material, msg)

    # Missing position case
    material, msg = engine.evaluate(
        currency="JPY",
        qty_diff=0.0,
        mv_diff=0.0,
        source_a_has=True,
        source_b_has=False,
        manager_id="mgr_1",
        position_link="http://example.com/pos/124",
    )
    print("Material break?", material, msg)

    # View alerts for manager
    for a in alerts.get_for_manager("mgr_1"):
        print("ALERT:", asdict(a))

    # Audit history for USD
    for e in store.history("USD"):
        print("AUDIT:", asdict(e))
```

---

## Self‑review
- **Correctness:** Implements per‑currency tolerance with default fallback, immutable audit entries, and always‑material missing‑position handling as required.  
- **Simplicity:** Uses in‑memory dictionaries and lists; no external storage, keeping the implementation minimal while satisfying the acceptance criteria.  
- **Extensibility:** Clear separation of concerns (store, audit, engine, alerts) makes it easy to replace in‑memory stores with a database or message bus later.