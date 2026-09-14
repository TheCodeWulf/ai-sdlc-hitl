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
