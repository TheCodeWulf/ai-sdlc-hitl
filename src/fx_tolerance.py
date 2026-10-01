# fx_tolerance.py
"""
Primary implementation for:
- Define Currency Tolerances (CRUD + audit)
- Suppress Tolerable Breaks
- Alert on Excess Breaks
- Export immutable audit log
"""

import csv
import threading
import uuid
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

# ----------------------------------------------------------------------
# Simple immutable audit log (append‑only)
# ----------------------------------------------------------------------
class AuditLogEntry:
    __slots__ = ("id", "user_id", "role", "op", "currency", "old", "new", "ts")

    def __init__(
        self,
        user_id: str,
        role: str,
        op: str,
        currency: Optional[str],
        old: Optional[float],
        new: Optional[float],
    ):
        self.id = str(uuid.uuid4())
        self.user_id = user_id
        self.role = role
        self.op = op  # create, update, delete
        self.currency = currency
        self.old = old
        self.new = new
        self.ts = datetime.now(timezone.utc)

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "role": self.role,
            "operation": self.op,
            "currency": self.currency or "",
            "old_value": "" if self.old is None else str(self.old),
            "new_value": "" if self.new is None else str(self.new),
            "timestamp_utc": self.ts.isoformat(),
        }


class AuditLog:
    """Append‑only, thread‑safe, read‑only for callers."""

    def __init__(self):
        self._entries: List[AuditLogEntry] = []
        self._lock = threading.Lock()

    def record(
        self,
        user_id: str,
        role: str,
        op: str,
        currency: Optional[str],
        old: Optional[float],
        new: Optional[float],
    ) -> None:
        with self._lock:
            self._entries.append(AuditLogEntry(user_id, role, op, currency, old, new))

    def all(self) -> List[AuditLogEntry]:
        # Return a copy to preserve immutability
        with self._lock:
            return list(self._entries)

    def export_csv(self, file_path: str) -> None:
        """Export the whole log to CSV (RFC‑4180 compatible)."""
        with open(file_path, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(
                f,
                fieldnames=[
                    "id",
                    "user_id",
                    "role",
                    "operation",
                    "currency",
                    "old_value",
                    "new_value",
                    "timestamp_utc",
                ],
            )
            writer.writeheader()
            for entry in self.all():
                writer.writerow(entry.to_dict())


# ----------------------------------------------------------------------
# Tolerance manager (CRUD + default fallback)
# ----------------------------------------------------------------------
class ToleranceError(ValueError):
    """Raised for validation problems."""


class ToleranceManager:
    """Manages per‑currency tolerance values with audit logging."""

    MIN_REQUIRED = {"USD", "EUR", "JPY"}

    def __init__(self, audit_log: AuditLog, default_tolerance: float = 0.0):
        self._tolerances: Dict[str, float] = {}
        self._default = default_tolerance
        self._audit = audit_log
        self._lock = threading.Lock()

    # ---------- public API ----------
    def set_default(self, value: float) -> None:
        if not self._is_valid(value):
            raise ToleranceError("Default tolerance must be non‑negative numeric")
        self._default = float(value)

    def get(self, currency: str) -> float:
        """Return configured tolerance or fallback default."""
        with self._lock:
            return self._tolerances.get(currency.upper(), self._default)

    def create(self, user_id: str, role: str, currency: str, value: float) -> None:
        """Add a new entry; error if already exists."""
        self._validate_user(role)
        cur = currency.upper()
        if not self._is_valid(value):
            raise ToleranceError("Tolerance must be a non‑negative number")
        with self._lock:
            if cur in self._tolerances:
                raise ToleranceError(f"Tolerance for {cur} already exists")
            self._tolerances[cur] = float(value)
        self._audit.record(user_id, role, "create", cur, None, float(value))

    def update(self, user_id: str, role: str, currency: str, value: float) -> None:
        self._validate_user(role)
        cur = currency.upper()
        if not self._is_valid(value):
            raise ToleranceError("Tolerance must be a non‑negative number")
        with self._lock:
            old = self._tolerances.get(cur)
            if old is None:
                raise ToleranceError(f"No existing tolerance for {cur}")
            self._tolerances[cur] = float(value)
        self._audit.record(user_id, role, "update", cur, old, float(value))

    def delete(self, user_id: str, role: str, currency: str) -> None:
        self._validate_user(role)
        cur = currency.upper()
        with self._lock:
            old = self._tolerances.pop(cur, None)
            if old is None:
                raise ToleranceError(f"No existing tolerance for {cur}")
        self._audit.record(user_id, role, "delete", cur, old, None)

    def list(self) -> Dict[str, float]:
        """Return a shallow copy of the current table."""
        with self._lock:
            return dict(self._tolerances)

    # ---------- helpers ----------
    @staticmethod
    def _is_valid(value) -> bool:
        try:
            v = float(value)
            return v >= 0
        except Exception:
            return False

    @staticmethod
    def _validate_user(role: str) -> None:
        # Permission model is out‑of‑scope; placeholder for future checks.
        if not role:
            raise ToleranceError("User role must be supplied")


# ----------------------------------------------------------------------
# Alert manager (simple in‑memory store, 30‑day retention)
# ----------------------------------------------------------------------
class Alert:
    __slots__ = ("id", "currency", "break_amount", "tolerance", "created_at", "cleared")

    def __init__(self, currency: str, break_amount: float, tolerance: float):
        self.id = str(uuid.uuid4())
        self.currency = currency.upper()
        self.break_amount = break_amount
        self.tolerance = tolerance
        self.created_at = datetime.now(timezone.utc)
        self.cleared = False

    def to_dict(self):
        return {
            "id": self.id,
            "currency": self.currency,
            "break_amount": self.break_amount,
            "tolerance": self.tolerance,
            "created_at_utc": self.created_at.isoformat(),
            "cleared": self.cleared,
        }


class AlertManager:
    """Stores alerts for at least 30 days; provides search."""

    RETENTION = timedelta(days=30)

    def __init__(self):
        self._alerts: List[Alert] = []
        self._lock = threading.Lock()

    def add(self, currency: str, break_amount: float, tolerance: float) -> Alert:
        alert = Alert(currency, break_amount, tolerance)
        with self._lock:
            self._alerts.append(alert)
        return alert

    def clear(self, alert_id: str) -> None:
        with self._lock:
            for a in self._alerts:
                if a.id == alert_id:
                    a.cleared = True
                    break

    def search(
        self,
        currency: Optional[str] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> List[Alert]:
        """Return alerts matching criteria and still within retention."""
        now = datetime.now(timezone.utc)
        with self._lock:
            result = []
            for a in self._alerts:
                if now - a.created_at > self.RETENTION:
                    continue  # expired, drop from view
                if currency and a.currency != currency.upper():
                    continue
                if start and a.created_at < start:
                    continue
                if end and a.created_at > end:
                    continue
                result.append(a)
            return result

    def purge_expired(self) -> None:
        """Remove alerts older than retention period."""
        now = datetime.now(timezone.utc)
        with self._lock:
            self._alerts = [
                a for a in self._alerts if now - a.created_at <= self.RETENTION
            ]


# ----------------------------------------------------------------------
# Break processor – suppression + alert generation
# ----------------------------------------------------------------------
class BreakProcessor:
    """
    Core logic used by the reconciliation system.
    - `evaluate_break` returns (suppress: bool, alert: Optional[Alert])
    - Logs suppression events via supplied logger.
    """

    def __init__(
        self,
        tolerance_mgr: ToleranceManager,
        alert_mgr: AlertManager,
        logger,
    ):
        self.tolerance_mgr = tolerance_mgr
        self.alert_mgr = alert_mgr
        self.logger = logger

    def evaluate_break(
        self,
        *,
        currency: str,
        qty_diff: float,
        mv_diff: float,
        source_missing: bool,
        user_id: str,
    ) -> Tuple[bool, Optional[Alert]]:
        """
        Parameters
        ----------
        currency: ISO code of the FX pair.
        qty_diff, mv_diff: absolute differences (positive numbers).
        source_missing: True if the break exists in only one source.
        user_id: identifier of the system/user evaluating the break (for logging).

        Returns
        -------
        (suppress, alert) – exactly one of them will be truthy.
        """
        cur = currency.upper()
        tolerance = self.tolerance_mgr.get(cur)

        # 1️⃣ Missing‑source breaks are never suppressed
        if source_missing:
            self.logger.info(
                f"[{user_id}] Break NOT suppressed (source missing) – {cur}"
            )
            # Still generate alert if diff > tolerance (requirement)
            if max(qty_diff, mv_diff) > tolerance:
                alert = self.alert_mgr.add(cur, max(qty_diff, mv_diff), tolerance)
                self.logger.info(
                    f"[{user_id}] Alert generated for excess break – {alert.id}"
                )
                return False, alert
            return False, None

        # 2️⃣ Compare against tolerance
        if max(qty_diff, mv_diff) <= tolerance:
            # Suppressed
            self.logger.info(
                f"[{user_id}] Suppressed break – {cur} diff={max(qty_diff,mv_diff)} tol={tolerance}"
            )
            # Record suppression (audit‑style but not in audit log)
            self.logger.debug(
                f"SuppressedBreak|currency={cur}|diff={max(qty_diff,mv_diff)}|tolerance={tolerance}"
            )
            return True, None

        # 3️⃣ Not suppressed → generate alert
        alert = self.alert_mgr.add(cur, max(qty_diff, mv_diff), tolerance)
        self.logger.info(
            f"[{user_id}] Alert generated for excess break – {alert.id}"
        )
        return False, alert


# ----------------------------------------------------------------------
# Example logger (can be swapped with real logging framework)
# ----------------------------------------------------------------------
class SimpleLogger:
    def info(self, msg: str):
        print(f"[INFO] {msg}")

    def debug(self, msg: str):
        print(f"[DEBUG] {msg}")

    def error(self, msg: str):
        print(f"[ERROR] {msg}")


# ----------------------------------------------------------------------
# Convenience factory for quick usage in tests or scripts
# ----------------------------------------------------------------------
def create_service(default_tolerance: float = 0.0) -> Tuple[
    ToleranceManager, AlertManager, BreakProcessor, AuditLog
]:
    audit = AuditLog()
    tol_mgr = ToleranceManager(audit, default_tolerance)
    alert_mgr = AlertManager()
    processor = BreakProcessor(tol_mgr, alert_mgr, SimpleLogger())
    return tol_mgr, alert_mgr, processor, audit
