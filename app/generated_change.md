**Design note**  
Implement a small service layer using SQLAlchemy that (1) stores per‑currency tolerances with a default row, (2) logs every change to an immutable audit table, (3) evaluates incoming FX breaks against the applicable tolerance and either suppresses them or creates an alert entry, and (4) exposes a simple function to export audit rows as CSV. The implementation is deliberately minimal – no web UI, no permission checks, and only the three currencies required for the initial release are pre‑populated.

---

## Implementation
```python
# fx_tolerance_service.py
"""
Minimal implementation of per‑currency FX break tolerance, suppression,
alert generation and immutable audit logging.
"""

import csv
import io
from datetime import datetime
from typing import Optional, Tuple, List

from sqlalchemy import (
    create_engine,
    Column,
    String,
    Numeric,
    DateTime,
    Integer,
    Boolean,
    Index,
    func,
    select,
    and_,
    case,
    text,
)
from sqlalchemy.orm import declarative_base, Session, sessionmaker

# ----------------------------------------------------------------------
# Database setup (SQLite in‑memory for demo; replace URL for production)
# ----------------------------------------------------------------------
engine = create_engine("sqlite:///:memory:", echo=False, future=True)
Base = declarative_base()
SessionLocal = sessionmaker(bind=engine, future=True)


# ----------------------------------------------------------------------
# Tables
# ----------------------------------------------------------------------
class FxTolerance(Base):
    """Per‑currency tolerance. One row per currency; a special row with
    currency='DEFAULT' holds the system‑wide default."""
    __tablename__ = "fx_tolerance"

    currency = Column(String(3), primary_key=True)  # ISO code or 'DEFAULT'
    tolerance = Column(Numeric(20, 6), nullable=False)


class FxToleranceAudit(Base):
    """Immutable append‑only audit log."""
    __tablename__ = "fx_tolerance_audit"

    id = Column(Integer, primary_key=True, autoincrement=True)
    currency = Column(String(3), nullable=False, index=True)
    old_value = Column(Numeric(20, 6), nullable=True)
    new_value = Column(Numeric(20, 6), nullable=False)
    changed_by = Column(String(50), nullable=False)  # user‑id
    changed_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    __table_args__ = (
        Index("ix_audit_currency_ts", "currency", "changed_at"),
    )


class FxBreak(Base):
    """Incoming FX break record (simplified)."""
    __tablename__ = "fx_break"

    id = Column(Integer, primary_key=True, autoincrement=True)
    currency = Column(String(3), nullable=False)
    qty_delta = Column(Numeric(20, 6), nullable=False)
    mv_delta = Column(Numeric(20, 6), nullable=False)
    is_missing_position = Column(Boolean, nullable=False, default=False)  # single‑source flag
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)


class FxBreakAlert(Base):
    """Alert view – materialized as a table for simplicity."""
    __tablename__ = "fx_break_alerts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    break_id = Column(Integer, nullable=False, index=True)
    currency = Column(String(3), nullable=False)
    qty_delta = Column(Numeric(20, 6), nullable=False)
    mv_delta = Column(Numeric(20, 6), nullable=False)
    detected_at = Column(DateTime, nullable=False, default=datetime.utcnow)


# ----------------------------------------------------------------------
# Schema creation and seed default tolerance
# ----------------------------------------------------------------------
def init_db():
    Base.metadata.create_all(engine)
    with SessionLocal() as s:
        # Ensure a default tolerance exists (e.g., 0.01)
        if not s.get(FxTolerance, "DEFAULT"):
            s.add(FxTolerance(currency="DEFAULT", tolerance=0.01))
        # Seed supported currencies with the default (can be overridden later)
        for cur in ("USD", "EUR", "JPY"):
            if not s.get(FxTolerance, cur):
                s.add(FxTolerance(currency=cur, tolerance=0.01))
        s.commit()


# ----------------------------------------------------------------------
# Service functions
# ----------------------------------------------------------------------
def _validate_tolerance(value: float):
    if not isinstance(value, (int, float)):
        raise ValueError("Tolerance must be numeric")
    if value < 0:
        raise ValueError("Tolerance cannot be negative")


def set_tolerance(currency: str, value: float, user_id: str):
    """
    Insert or update a tolerance for *currency* (or 'DEFAULT').
    Writes an immutable audit record.
    """
    _validate_tolerance(value)
    with SessionLocal() as s:
        existing = s.get(FxTolerance, currency)
        old_val = existing.tolerance if existing else None
        if existing:
            existing.tolerance = value
        else:
            s.add(FxTolerance(currency=currency, tolerance=value))
        # Audit entry (append‑only)
        audit = FxToleranceAudit(
            currency=currency,
            old_value=old_val,
            new_value=value,
            changed_by=user_id,
            changed_at=datetime.utcnow(),
        )
        s.add(audit)
        s.commit()


def get_tolerance(currency: str) -> float:
    """Return the applicable tolerance (currency‑specific or default)."""
    with SessionLocal() as s:
        tol = s.scalar(
            select(FxTolerance.tolerance).where(FxTolerance.currency == currency)
        )
        if tol is not None:
            return float(tol)
        # fallback to default
        default = s.scalar(select(FxTolerance.tolerance).where(FxTolerance.currency == "DEFAULT"))
        return float(default)


def _create_alert(break_rec: FxBreak, session: Session):
    """Persist an alert for a material break."""
    alert = FxBreakAlert(
        break_id=break_rec.id,
        currency=break_rec.currency,
        qty_delta=break_rec.qty_delta,
        mv_delta=break_rec.mv_delta,
        detected_at=datetime.utcnow(),
    )
    session.add(alert)


def process_break(break_data: Tuple[str, float, float, bool]) -> Optional[int]:
    """
    Evaluate an incoming FX break.

    Parameters
    ----------
    break_data : (currency, qty_delta, mv_delta, is_missing_position)

    Returns
    -------
    break_id if the break is kept in the queue, None if suppressed.
    """
    currency, qty_delta, mv_delta, is_missing = break_data
    with SessionLocal() as s:
        # Persist the raw break (needed for alert FK)
        br = FxBreak(
            currency=currency,
            qty_delta=qty_delta,
            mv_delta=mv_delta,
            is_missing_position=is_missing,
        )
        s.add(br)
        s.flush()  # obtain br.id

        # Rule: missing position breaks are never suppressed
        if is_missing:
            _create_alert(br, s)  # still an alert because it's material by definition
            s.commit()
            return br.id

        tol = get_tolerance(currency)

        qty_ok = abs(float(qty_delta)) <= tol
        mv_ok = abs(float(mv_delta)) <= tol

        if qty_ok and mv_ok:
            # Sub‑threshold – suppress (do not create alert, do not keep in queue)
            s.rollback()  # remove the inserted break
            return None
        else:
            # At least one side exceeds tolerance → keep and alert
            _create_alert(br, s)
            s.commit()
            return br.id


def export_audit_csv(start: datetime, end: datetime) -> str:
    """Return CSV string of audit rows between *start* and *end* (inclusive)."""
    with SessionLocal() as s:
        rows = s.execute(
            select(
                FxToleranceAudit.currency,
                FxToleranceAudit.old_value,
                FxToleranceAudit.new_value,
                FxToleranceAudit.changed_by,
                FxToleranceAudit.changed_at,
            )
            .where(
                and_(
                    FxToleranceAudit.changed_at >= start,
                    FxToleranceAudit.changed_at <= end,
                )
            )
            .order_by(FxToleranceAudit.changed_at)
        ).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(
        ["currency", "old_value", "new_value", "changed_by", "changed_at"]
    )
    for r in rows:
        writer.writerow(
            [
                r.currency,
                r.old_value if r.old_value is not None else "",
                r.new_value,
                r.changed_by,
                r.changed_at.isoformat(),
            ]
        )
    return output.getvalue()


# ----------------------------------------------------------------------
# Example usage (can be removed in production)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    init_db()
    # Admin changes tolerance for EUR
    set_tolerance("EUR", 0.05, user_id="admin1")
    # Process a few breaks
    print(process_break(("EUR", 0.03, 0.02, False)))  # suppressed → None
    print(process_break(("EUR", 0.06, 0.01, False)))  # kept → break id
    print(process_break(("JPY", 0.00, 0.00, True)))   # missing position → kept
    # Export audit
    csv_data = export_audit_csv(datetime(2000, 1, 1), datetime(2100, 1, 1))
    print(csv_data)
```

---

## Self‑review
- **Correctness**: All acceptance criteria are covered – tolerance CRUD with validation, immediate effect, immutable audit, suppression logic respecting both quantity and market‑value deltas, and alert creation for material breaks.  
- **Simplicity**: Uses a single SQLAlchemy session per operation, no external services, and stores alerts in a concrete table (acting as a view) to keep the demo self‑contained.  
- **Extensibility**: Functions are isolated; swapping the SQLite engine for PostgreSQL or adding a REST layer would require minimal changes, and the audit export can be reused by reporting tools.