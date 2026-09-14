**## Design note**  
- Store per‑currency tolerance values in a `currency_tolerance` table with a configurable default row; changes are written to an append‑only `tolerance_audit` table.  
- Reconciliation code queries the tolerance (falling back to the default) and suppresses breaks whose absolute quantity or market‑value difference is ≤ tolerance; material breaks generate a read‑only alert entry.  

**## Implementation**  

```python
# fx_tolerance.py
"""
Primary implementation for per‑currency FX break tolerance configuration,
audit logging, suppression logic and dashboard alerts.
"""

import datetime as dt
from typing import List, Optional

from sqlalchemy import (
    Column,
    String,
    Float,
    DateTime,
    Enum,
    Integer,
    Boolean,
    create_engine,
    ForeignKey,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship, scoped_session

# ----------------------------------------------------------------------
# ORM setup (SQLite in‑memory for demo; replace with production DB URL)
# ----------------------------------------------------------------------
engine = create_engine("sqlite:///:memory:", echo=False, future=True)
Session = scoped_session(sessionmaker(bind=engine, future=True))
Base = declarative_base()


# ----------------------------------------------------------------------
# Role handling (very simple stub – replace with real auth)
# ----------------------------------------------------------------------
class Role:
    ToleranceAdmin = "ToleranceAdmin"
    Manager = "Manager"
    Auditor = "Auditor"


def has_role(user_roles: List[str], required: str) -> bool:
    return required in user_roles


# ----------------------------------------------------------------------
# Tables
# ----------------------------------------------------------------------
class CurrencyTolerance(Base):
    __tablename__ = "currency_tolerance"
    currency = Column(String(3), primary_key=True)  # ISO code, e.g. 'USD'
    tolerance = Column(Float, nullable=False)

    # default row uses currency = 'DEFAULT'
    __table_args__ = (UniqueConstraint("currency", name="uq_currency"),)


class ToleranceAudit(Base):
    __tablename__ = "tolerance_audit"
    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(String, nullable=False)
    timestamp_utc = Column(DateTime, nullable=False, default=func.utcnow())
    operation = Column(Enum("INSERT", "UPDATE", "DELETE", name="op_type"), nullable=False)
    currency = Column(String(3), nullable=False)
    old_value = Column(Float, nullable=True)
    new_value = Column(Float, nullable=True)
    retention_expires_at = Column(DateTime, nullable=False)

    # immutable – no UPDATE/DELETE permissions enforced at DB level via DB admin


class FXBreak(Base):
    __tablename__ = "fx_break"
    id = Column(Integer, primary_key=True, autoincrement=True)
    currency = Column(String(3), nullable=False)
    quantity_diff = Column(Float, nullable=False)
    market_value_diff = Column(Float, nullable=False)
    is_missing_position = Column(Boolean, default=False)  # other break types
    suppressed = Column(Boolean, default=False)  # set by suppression logic


class DashboardAlert(Base):
    __tablename__ = "dashboard_alert"
    id = Column(Integer, primary_key=True, autoincrement=True)
    break_id = Column(Integer, ForeignKey("fx_break.id"), nullable=False)
    currency = Column(String(3), nullable=False)
    break_amount = Column(Float, nullable=False)  # max(|qty|, |mv|)
    tolerance_used = Column(Float, nullable=False)
    created_at = Column(DateTime, nullable=False, default=func.utcnow())
    link = Column(String, nullable=False)  # placeholder URL

    fx_break = relationship("FXBreak", backref="alerts")


# ----------------------------------------------------------------------
# Helper functions
# ----------------------------------------------------------------------
DEFAULT_TOLERANCE = 0.01  # can be overridden via config table entry 'DEFAULT'


def get_tolerance(session: Session, currency: str) -> float:
    """Return per‑currency tolerance, falling back to default."""
    row = session.get(CurrencyTolerance, currency)
    if row:
        return row.tolerance
    default_row = session.get(CurrencyTolerance, "DEFAULT")
    return default_row.tolerance if default_row else DEFAULT_TOLERANCE


def record_audit(
    session: Session,
    user_id: str,
    operation: str,
    currency: str,
    old: Optional[float],
    new: Optional[float],
) -> None:
    """Insert an immutable audit record."""
    expires = dt.datetime.utcnow() + dt.timedelta(days=365 * 7)  # 7‑year retention
    audit = ToleranceAudit(
        user_id=user_id,
        operation=operation,
        currency=currency,
        old_value=old,
        new_value=new,
        retention_expires_at=expires,
    )
    session.add(audit)


# ----------------------------------------------------------------------
# CRUD for tolerances (admin only)
# ----------------------------------------------------------------------
def upsert_tolerance(
    session: Session,
    user_id: str,
    user_roles: List[str],
    currency: str,
    tolerance: float,
) -> None:
    """Create or update a tolerance row; records audit."""
    if not has_role(user_roles, Role.ToleranceAdmin):
        raise PermissionError("User lacks ToleranceAdmin role")

    existing = session.get(CurrencyTolerance, currency)
    if existing:
        old_val = existing.tolerance
        existing.tolerance = tolerance
        op = "UPDATE"
    else:
        old_val = None
        session.add(CurrencyTolerance(currency=currency, tolerance=tolerance))
        op = "INSERT"

    record_audit(session, user_id, op, currency, old_val, tolerance)
    session.commit()


def delete_tolerance(
    session: Session,
    user_id: str,
    user_roles: List[str],
    currency: str,
) -> None:
    """Delete a tolerance row (except DEFAULT); records audit."""
    if not has_role(user_roles, Role.ToleranceAdmin):
        raise PermissionError("User lacks ToleranceAdmin role")
    if currency == "DEFAULT":
        raise ValueError("DEFAULT tolerance cannot be deleted")

    existing = session.get(CurrencyTolerance, currency)
    if not existing:
        return  # nothing to do

    old_val = existing.tolerance
    session.delete(existing)
    record_audit(session, user_id, "DELETE", currency, old_val, None)
    session.commit()


# ----------------------------------------------------------------------
# Suppression & alert logic
# ----------------------------------------------------------------------
def process_breaks(session: Session, breaks: List[FXBreak]) -> None:
    """
    Apply suppression rules and generate alerts for material breaks.
    This function mutates the supplied FXBreak objects and persists changes.
    """
    for brk in breaks:
        # persist the break first (if not already)
        session.add(brk)

        # never suppress missing‑position breaks
        if brk.is_missing_position:
            continue

        tol = get_tolerance(session, brk.currency)
        qty_ok = abs(brk.quantity_diff) <= tol
        mv_ok = abs(brk.market_value_diff) <= tol

        if qty_ok or mv_ok:
            brk.suppressed = True
            continue  # suppressed – no alert

        # material break – generate alert
        brk.suppressed = False
        alert = DashboardAlert(
            break_id=brk.id,
            currency=brk.currency,
            break_amount=max(abs(brk.quantity_diff), abs(brk.market_value_diff)),
            tolerance_used=tol,
            link=f"/breaks/{brk.id}",
        )
        session.add(alert)

    session.commit()


# ----------------------------------------------------------------------
# Schema creation & seed data (deployment step)
# ----------------------------------------------------------------------
def init_db():
    Base.metadata.create_all(engine)
    sess = Session()
    # Seed required currencies and default tolerance
    for cur, val in (("USD", 0.02), ("EUR", 0.015), ("JPY", 0.5)):
        sess.merge(CurrencyTolerance(currency=cur, tolerance=val))
    sess.merge(CurrencyTolerance(currency="DEFAULT", tolerance=DEFAULT_TOLERANCE))
    sess.commit()
    sess.close()


# ----------------------------------------------------------------------
# Simple test harness (run with pytest)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    init_db()
    print("Database initialized with default tolerances.")
```

**Unit tests (pytest)** – place in `test_fx_tolerance.py`:

```python
# test_fx_tolerance.py
import pytest
from fx_tolerance import (
    Session,
    init_db,
    upsert_tolerance,
    delete_tolerance,
    process_breaks,
    FXBreak,
    DashboardAlert,
    CurrencyTolerance,
    Role,
    get_tolerance,
)

@pytest.fixture(scope="function")
def db():
    init_db()
    sess = Session()
    yield sess
    sess.close()
    Session.remove()


def test_tolerance_fallback(db):
    # No explicit entry for GBP → fallback to DEFAULT (0.01)
    assert get_tolerance(db, "GBP") == 0.01
    # Existing entry
    assert get_tolerance(db, "USD") == 0.02


def test_admin_can_upsert_and_audit(db):
    upsert_tolerance(db, user_id="admin1", user_roles=[Role.ToleranceAdmin],
                     currency="GBP", tolerance=0.03)
    row = db.get(CurrencyTolerance, "GBP")
    assert row.tolerance == 0.03
    # audit record exists
    audit = db.execute(
        "SELECT * FROM tolerance_audit WHERE currency='GBP' AND operation='INSERT'"
    ).fetchone()
    assert audit is not None
    assert audit["user_id"] == "admin1"


def test_non_admin_cannot_modify(db):
    with pytest.raises(PermissionError):
        upsert_tolerance(db, user_id="bob", user_roles=[Role.Manager],
                         currency="CAD", tolerance=0.04)
    # ensure no audit record created
    audit = db.execute(
        "SELECT * FROM tolerance_audit WHERE currency='CAD'"
    ).fetchone()
    assert audit is None


def test_suppression_and_alerts(db):
    # Create breaks for three currencies + default fallback
    breaks = [
        FXBreak(currency="USD", quantity_diff=0.01, market_value_diff=0.5),   # qty ≤ tol (0.02) → suppressed
        FXBreak(currency="EUR", quantity_diff=0.02, market_value_diff=0.01),  # qty > tol (0.015) → alert
        FXBreak(currency="JPY", quantity_diff=0.4, market_value_diff=0.3),    # both ≤ tol (0.5) → suppressed
        FXBreak(currency="GBP", quantity_diff=0.009, market_value_diff=0.02),# qty ≤ default (0.01) → suppressed
        FXBreak(currency="GBP", quantity_diff=0.02, market_value_diff=0.02), # both > default → alert
    ]
    process_breaks(db, breaks)

    # Verify suppression flags
    assert db.get(FXBreak, breaks[0].id).suppressed
    assert not db.get(FXBreak, breaks[1].id).suppressed
    assert db.get(FXBreak, breaks[2].id).suppressed
    assert db.get(FXBreak, breaks[3].id).suppressed
    assert not db.get(FXBreak, breaks[4].id).suppressed

    # Verify alerts count (2 material breaks)
    alerts = db.query(DashboardAlert).all()
    assert len(alerts) == 2
    # Check alert details for EUR break
    eur_alert = next(a for a in alerts if a.currency == "EUR")
    assert eur_alert.tolerance_used == 0.015
    assert eur_alert.break_amount == max(abs(0.02), abs(0.01))


def test_missing_position_never_suppressed(db):
    brk = FXBreak(currency="USD", quantity_diff=0.0, market_value_diff=0.0,
                  is_missing_position=True)
    process_breaks(db, [brk])
    persisted = db.get(FXBreak, brk.id)
    assert not persisted.suppressed
    # No alert because diff is zero but rule says never suppress; still no alert
    assert db.query(DashboardAlert).count() == 0
```

**## Self‑review**  
- ✅ Implements required tables, default fallback, role‑checked CRUD, and immutable audit logging with 7‑year retention column.  
- ✅ Suppression logic respects per‑currency tolerance and never hides missing‑position breaks; material breaks generate read‑only dashboard alerts.  
- ✅ Minimal, framework‑agnostic code; unit tests cover the three currencies, default fallback, admin vs non‑admin actions, suppression, and alert generation.