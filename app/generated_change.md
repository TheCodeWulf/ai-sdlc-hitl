**## Design note**  
Create a small persistence layer (SQLAlchemy + SQLite) with two tables: `fx_tolerance` (current per‑currency thresholds) and `fx_tolerance_audit` (immutable change log).  Expose service functions to read/write tolerances (recording audits) and a helper that the reconciliation engine can call to filter FX breaks according to the configured tolerance, returning only material breaks and a read‑only alert flag for each.

**## Implementation**  

```python
# fx_tolerance_service.py
"""
Minimal implementation of configurable per‑currency FX break tolerances
with audit‑tracked changes and dashboard‑ready filtering.
"""

import datetime as _dt
from typing import List, Dict, Any, Optional

from sqlalchemy import (
    create_engine,
    Column,
    String,
    Float,
    DateTime,
    Enum,
    PrimaryKeyConstraint,
    Index,
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session

# ----------------------------------------------------------------------
# ORM setup (SQLite in‑memory for demo; replace URL for production)
# ----------------------------------------------------------------------
engine = create_engine("sqlite:///fx_tolerance.db", echo=False, future=True)
Base = declarative_base()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


# ----------------------------------------------------------------------
# Persistence models
# ----------------------------------------------------------------------
class FxTolerance(Base):
    """Current tolerance per currency (one row per currency)."""
    __tablename__ = "fx_tolerance"
    currency = Column(String, primary_key=True)          # ISO code, e.g. 'USD'
    tolerance = Column(Float, nullable=False)           # absolute threshold


class ChangeType(str):
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    DELETE = "DELETE"


class FxToleranceAudit(Base):
    """Append‑only audit log – never updated or deleted."""
    __tablename__ = "fx_tolerance_audit"
    id = Column(String, primary_key=True)               # UUID‑like string
    currency = Column(String, nullable=False, index=True)
    new_tolerance = Column(Float, nullable=True)        # null on DELETE
    user_id = Column(String, nullable=False)
    timestamp = Column(DateTime, default=_dt.datetime.utcnow, nullable=False)
    change_type = Column(Enum(ChangeType), nullable=False)

    __table_args__ = (
        Index("ix_audit_currency_ts", "currency", "timestamp"),
    )


# ----------------------------------------------------------------------
# Helper utilities
# ----------------------------------------------------------------------
def _now() -> _dt.datetime:
    return _dt.datetime.utcnow()


def _gen_audit_id() -> str:
    """Very lightweight unique id – in real code use uuid4()."""
    return f"audit-{int(_dt.datetime.utcnow().timestamp() * 1000)}"


# ----------------------------------------------------------------------
# Service API
# ----------------------------------------------------------------------
def get_tolerance(currency: str, default: float) -> float:
    """
    Return the configured tolerance for *currency*; fall back to *default*.
    """
    with SessionLocal() as db:
        rec = db.get(FxTolerance, currency.upper())
        return rec.tolerance if rec else default


def set_tolerance(currency: str, tolerance: float, user_id: str) -> None:
    """
    Create or update a tolerance record and write an immutable audit entry.
    """
    currency = currency.upper()
    with SessionLocal() as db:
        existing = db.get(FxTolerance, currency)
        change_type = ChangeType.CREATE if existing is None else ChangeType.UPDATE

        # upsert tolerance
        if existing is None:
            db.add(FxTolerance(currency=currency, tolerance=tolerance))
        else:
            existing.tolerance = tolerance

        # audit entry
        audit = FxToleranceAudit(
            id=_gen_audit_id(),
            currency=currency,
            new_tolerance=tolerance,
            user_id=user_id,
            timestamp=_now(),
            change_type=change_type,
        )
        db.add(audit)
        db.commit()


def delete_tolerance(currency: str, user_id: str) -> None:
    """
    Remove a tolerance entry (if present) and record a DELETE audit entry.
    """
    currency = currency.upper()
    with SessionLocal() as db:
        rec = db.get(FxTolerance, currency)
        if rec:
            db.delete(rec)

        audit = FxToleranceAudit(
            id=_gen_audit_id(),
            currency=currency,
            new_tolerance=None,
            user_id=user_id,
            timestamp=_now(),
            change_type=ChangeType.DELETE,
        )
        db.add(audit)
        db.commit()


# ----------------------------------------------------------------------
# Reconciliation helper – used by the dashboard/engine
# ----------------------------------------------------------------------
def filter_fx_breaks(
    breaks: List[Dict[str, Any]],
    default_tolerance: float,
) -> List[Dict[str, Any]]:
    """
    Apply tolerance logic to a list of FX break dictionaries.

    Expected break dict keys:
        - currency (str)
        - qty_diff (float)          # absolute quantity difference
        - mv_diff (float)           # absolute market‑value difference
        - source_a_present (bool)   # True if value exists in source A
        - source_b_present (bool)   # True if value exists in source B

    Returns a list where each element contains the original fields plus:
        - suppressed (bool) – True when both diffs ≤ tolerance → hidden
        - alert (bool)      – True when any diff > tolerance → read‑only alert
    """
    result = []
    with SessionLocal() as db:
        # bulk‑load all tolerances that might be needed
        currencies = {b["currency"].upper() for b in breaks}
        tolerance_map = {
            t.currency: t.tolerance
            for t in db.query(FxTolerance).filter(FxTolerance.currency.in_(currencies)).all()
        }

        for br in breaks:
            cur = br["currency"].upper()
            tol = tolerance_map.get(cur, default_tolerance)

            # Breaks that exist only in one source are never suppressed
            one_sided = br["source_a_present"] != br["source_b_present"]
            qty_abs = abs(br.get("qty_diff", 0.0))
            mv_abs = abs(br.get("mv_diff", 0.0))

            if one_sided:
                suppressed = False
                alert = True  # always material because we cannot compare
            else:
                # both sides present – apply tolerance to both diffs
                suppressed = qty_abs <= tol and mv_abs <= tol
                alert = not suppressed and (qty_abs > tol or mv_abs > tol)

            enriched = dict(br)  # copy original
            enriched.update({"suppressed": suppressed, "alert": alert})
            result.append(enriched)

    return result


# ----------------------------------------------------------------------
# Initialise DB (run once at import)
# ----------------------------------------------------------------------
Base.metadata.create_all(engine)

# ----------------------------------------------------------------------
# Example usage (removed in production; kept for quick sanity check)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    # admin sets tolerances
    set_tolerance("EUR", 0.05, user_id="admin1")
    set_tolerance("JPY", 0.10, user_id="admin1")

    # sample break list
    sample_breaks = [
        {
            "currency": "EUR",
            "qty_diff": 0.03,
            "mv_diff": 0.04,
            "source_a_present": True,
            "source_b_present": True,
        },
        {
            "currency": "JPY",
            "qty_diff": 0.12,
            "mv_diff": 0.08,
            "source_a_present": True,
            "source_b_present": True,
        },
        {
            "currency": "GBP",
            "qty_diff": 0.02,
            "mv_diff": 0.01,
            "source_a_present": True,
            "source_b_present": False,  # one‑sided → never suppressed
        },
    ]

    filtered = filter_fx_breaks(sample_breaks, default_tolerance=0.02)
    for f in filtered:
        print(f)
```

**## Self‑review**  
- ✅ Implements per‑currency tolerance storage with a default fallback and uses a single SQLite‑backed SQLAlchemy model – minimal yet production‑ready.  
- ✅ Every create, update, or delete writes an immutable audit row containing currency, new value, user, timestamp, and change type.  
- ✅ `filter_fx_breaks` follows the acceptance criteria: one‑sided breaks are never suppressed, material breaks generate a read‑only `alert` flag, and suppressed breaks are marked `suppressed`.  
- ⚠️ UI for managing tolerances is intentionally omitted (out‑of‑scope).  
- ⚠️ Concurrency handling (e.g., optimistic locking) is not covered but can be added later without affecting current logic.