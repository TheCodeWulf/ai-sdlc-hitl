## Design note
- Use **FastAPI** + **SQLAlchemy** with an SQLite DB to expose a small REST API for tolerance management, audit logging, and break filtering.  
- A single `tolerance` table stores per‑currency thresholds; a constant `DEFAULT_TOLERANCE` is applied when a currency is missing.  
- Every change to the tolerance table creates an immutable record in `tolerance_audit`. The audit table has no UPDATE/DELETE endpoints, guaranteeing append‑only behavior.  
- The `/breaks` endpoint demonstrates the dashboard logic: it hides sub‑tolerance breaks and returns material breaks with a read‑only alert payload.

```python
# main.py
"""
FastAPI service implementing:
1. Per‑currency FX tolerance table with default fallback.
2. Append‑only audit log for tolerance changes.
3. Dashboard‑style break suppression & read‑only material‑break alerts.
"""

import os
from datetime import datetime
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Depends, Query, status
from pydantic import BaseModel, Field, validator
from sqlalchemy import (
    Column,
    String,
    Float,
    DateTime,
    Enum,
    create_engine,
    select,
    func,
    and_,
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session

# ----------------------------------------------------------------------
# DB setup
# ----------------------------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./fx_tolerance.db")
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

Base = declarative_base()


# ----------------------------------------------------------------------
# Models
# ----------------------------------------------------------------------
class Tolerance(Base):
    __tablename__ = "tolerance"
    currency = Column(String, primary_key=True, index=True)  # ISO code
    value = Column(Float, nullable=False)


class AuditOp(str):
    INSERT = "INSERT"
    UPDATE = "UPDATE"
    DELETE = "DELETE"


class ToleranceAudit(Base):
    __tablename__ = "tolerance_audit"
    id = Column(String, primary_key=True, index=True)  # UUID string
    user_id = Column(String, nullable=False)
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow)
    currency = Column(String, nullable=False)
    old_value = Column(Float, nullable=True)
    new_value = Column(Float, nullable=True)
    operation = Column(Enum(AuditOp), nullable=False)


# Simple break model – in a real system this would be a view or separate service.
class Break(Base):
    __tablename__ = "break"
    id = Column(String, primary_key=True, index=True)  # UUID
    currency = Column(String, nullable=False)
    qty_diff = Column(Float, nullable=False)  # absolute quantity diff
    mv_diff = Column(Float, nullable=False)   # absolute market‑value diff


# ----------------------------------------------------------------------
# Pydantic schemas
# ----------------------------------------------------------------------
class ToleranceIn(BaseModel):
    currency: str = Field(..., min_length=3, max_length=3, description="ISO currency code")
    value: float = Field(..., ge=0, description="Non‑negative tolerance")

    @validator("currency")
    def upper_currency(cls, v):
        return v.upper()


class ToleranceOut(ToleranceIn):
    pass


class AuditRecordOut(BaseModel):
    id: str
    user_id: str
    timestamp: datetime
    currency: str
    old_value: Optional[float]
    new_value: Optional[float]
    operation: AuditOp


class BreakIn(BaseModel):
    id: str
    currency: str
    qty_diff: float = Field(..., ge=0)
    mv_diff: float = Field(..., ge=0)


class MaterialBreakAlert(BaseModel):
    currency: str
    measured_difference: float
    configured_tolerance: float
    alert: str = "Material FX Break"


class BreakOut(BaseModel):
    id: str
    currency: str
    qty_diff: float
    mv_diff: float
    alert: Optional[MaterialBreakAlert] = None


# ----------------------------------------------------------------------
# FastAPI app & dependencies
# ----------------------------------------------------------------------
app = FastAPI(title="FX Tolerance Service")

DEFAULT_TOLERANCE = 0.01  # system‑wide fallback (could be env‑configurable)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ----------------------------------------------------------------------
# Helper functions
# ----------------------------------------------------------------------
def get_tolerance(db: Session, currency: str) -> float:
    """Return configured tolerance or default."""
    cur = currency.upper()
    row = db.get(Tolerance, cur)
    return row.value if row else DEFAULT_TOLERANCE


def record_audit(
    db: Session,
    *,
    user_id: str,
    currency: str,
    old_value: Optional[float],
    new_value: Optional[float],
    operation: AuditOp,
) -> None:
    """Insert immutable audit record."""
    import uuid

    audit = ToleranceAudit(
        id=str(uuid.uuid4()),
        user_id=user_id,
        timestamp=datetime.utcnow(),
        currency=currency.upper(),
        old_value=old_value,
        new_value=new_value,
        operation=operation,
    )
    db.add(audit)
    db.commit()


# ----------------------------------------------------------------------
# API Endpoints – Story 1 (Tolerance CRUD + validation)
# ----------------------------------------------------------------------
@app.post("/tolerances", response_model=ToleranceOut, status_code=status.HTTP_201_CREATED)
def create_tolerance(tol: ToleranceIn, db: Session = Depends(get_db)):
    if db.get(Tolerance, tol.currency):
        raise HTTPException(
            status_code=400, detail=f"Tolerance for {tol.currency} already exists."
        )
    # Validation already enforced by Pydantic (non‑negative)
    db.add(Tolerance(currency=tol.currency, value=tol.value))
    db.commit()
    record_audit(
        db,
        user_id="system",  # placeholder – replace with auth context
        currency=tol.currency,
        old_value=None,
        new_value=tol.value,
        operation=AuditOp.INSERT,
    )
    return tol


@app.get("/tolerances/{currency}", response_model=ToleranceOut)
def read_tolerance(currency: str, db: Session = Depends(get_db)):
    row = db.get(Tolerance, currency.upper())
    if not row:
        raise HTTPException(status_code=404, detail="Tolerance not found.")
    return ToleranceOut(currency=row.currency, value=row.value)


@app.put("/tolerances/{currency}", response_model=ToleranceOut)
def update_tolerance(
    currency: str, tol: ToleranceIn, db: Session = Depends(get_db)
):
    cur = currency.upper()
    row = db.get(Tolerance, cur)
    if not row:
        raise HTTPException(status_code=404, detail="Tolerance not found.")
    old_val = row.value
    row.value = tol.value
    db.commit()
    record_audit(
        db,
        user_id="system",
        currency=cur,
        old_value=old_val,
        new_value=tol.value,
        operation=AuditOp.UPDATE,
    )
    return ToleranceOut(currency=cur, value=row.value)


@app.delete("/tolerances/{currency}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tolerance(currency: str, db: Session = Depends(get_db)):
    cur = currency.upper()
    row = db.get(Tolerance, cur)
    if not row:
        raise HTTPException(status_code=404, detail="Tolerance not found.")
    old_val = row.value
    db.delete(row)
    db.commit()
    record_audit(
        db,
        user_id="system",
        currency=cur,
        old_value=old_val,
        new_value=None,
        operation=AuditOp.DELETE,
    )
    return


# ----------------------------------------------------------------------
# API Endpoints – Story 3 (Audit retrieval)
# ----------------------------------------------------------------------
@app.get("/audit", response_model=List[AuditRecordOut])
def get_audit(
    currency: Optional[str] = Query(None, min_length=3, max_length=3),
    start: Optional[datetime] = Query(None),
    end: Optional[datetime] = Query(None),
    db: Session = Depends(get_db),
):
    stmt = select(ToleranceAudit)
    if currency:
        stmt = stmt.where(ToleranceAudit.currency == currency.upper())
    if start:
        stmt = stmt.where(ToleranceAudit.timestamp >= start)
    if end:
        stmt = stmt.where(ToleranceAudit.timestamp <= end)
    stmt = stmt.order_by(ToleranceAudit.timestamp.asc())
    records = db.scalars(stmt).all()
    return records


# ----------------------------------------------------------------------
# API Endpoints – Story 2 (Break suppression & alert)
# ----------------------------------------------------------------------
@app.post("/breaks", response_model=BreakOut, status_code=status.HTTP_201_CREATED)
def ingest_break(brk: BreakIn, db: Session = Depends(get_db)):
    """Store a break and immediately return its dashboard view."""
    # Persist for completeness – in real system this would be a separate store.
    db.add(Break(**brk.dict()))
    db.commit()

    tolerance = get_tolerance(db, brk.currency)
    max_diff = max(brk.qty_diff, brk.mv_diff)

    if max_diff <= tolerance:
        # Sub‑tolerance: hide from manager view – we return 204 No Content in real UI.
        # Here we still acknowledge creation but no alert.
        return BreakOut(**brk.dict(), alert=None)

    # Material break – include read‑only alert.
    alert = MaterialBreakAlert(
        currency=brk.currency.upper(),
        measured_difference=max_diff,
        configured_tolerance=tolerance,
    )
    return BreakOut(**brk.dict(), alert=alert)


@app.get("/breaks", response_model=List[BreakOut])
def list_material_breaks(
    db: Session = Depends(get_db),
    currency: Optional[str] = Query(None, min_length=3, max_length=3),
):
    """Return only material breaks (differences > tolerance)."""
    stmt = select(Break)
    if currency:
        stmt = stmt.where(Break.currency == currency.upper())
    breaks = db.scalars(stmt).all()
    result: List[BreakOut] = []
    for brk in breaks:
        tol = get_tolerance(db, brk.currency)
        max_diff = max(brk.qty_diff, brk.mv_diff)
        if max_diff > tol:
            alert = MaterialBreakAlert(
                currency=brk.currency,
                measured_difference=max_diff,
                configured_tolerance=tol,
            )
            result.append(
                BreakOut(
                    id=brk.id,
                    currency=brk.currency,
                    qty_diff=brk.qty_diff,
                    mv_diff=brk.mv_diff,
                    alert=alert,
                )
            )
    return result


# ----------------------------------------------------------------------
# Initialize DB (run once)
# ----------------------------------------------------------------------
def init_db():
    Base.metadata.create_all(bind=engine)


if __name__ == "__main__":
    init_db()
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
```

## Self-review
- **Correctness:** Implements all three stories: per‑currency tolerance CRUD with validation, immutable audit logging, and dashboard‑style break suppression/alerting using the configured tolerance (fallback default applied when missing).  
- **Safety & Immutability:** Audit table has no update/delete endpoints; records are only inserted via `record_audit`, satisfying append‑only requirement.  
- **Minimalism:** Single-file FastAPI service, no external dependencies beyond FastAPI/SQLAlchemy, keeping the implementation clean and focused on the primary backlog items.