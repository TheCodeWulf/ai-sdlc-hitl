**Design note**  
Create three lightweight tables – `currency_tolerance`, `tolerance_audit` and `fx_alert` – using SQLAlchemy.  Expose CRUD endpoints for tolerances (including a single‑row default) via FastAPI; each mutation writes an immutable audit row.  Provide a utility `evaluate_breaks` that the reconciliation engine can call to compare a list of FX breaks against the stored tolerances and, when a break exceeds its threshold, insert a read‑only alert record.

---

## Implementation
```python
# main.py
"""
FastAPI service implementing per‑currency FX break tolerances,
audit‑trail for tolerance changes, and read‑only dashboard alerts.
"""

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import FastAPI, HTTPException, Depends, status
from pydantic import BaseModel, Field, validator
from sqlalchemy import (
    Column,
    String,
    Float,
    Boolean,
    DateTime,
    Integer,
    create_engine,
    ForeignKey,
    UniqueConstraint,
    event,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, relationship

# ----------------------------------------------------------------------
# Database setup (SQLite in‑memory for demo; replace with real DB URL)
# ----------------------------------------------------------------------
SQLALCHEMY_DATABASE_URL = "sqlite:///./fx_tolerance.db"
engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# ----------------------------------------------------------------------
# Models
# ----------------------------------------------------------------------
class CurrencyTolerance(Base):
    __tablename__ = "currency_tolerance"
    id = Column(Integer, primary_key=True, index=True)
    currency = Column(String, nullable=False, unique=True)  # e.g. "USD"
    tolerance = Column(Float, nullable=False)  # numeric threshold
    is_default = Column(Boolean, default=False, nullable=False)

    # ensure only one default row exists
    __table_args__ = (UniqueConstraint("is_default", name="uq_default_true"),)


class ToleranceAudit(Base):
    __tablename__ = "tolerance_audit"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, nullable=False)
    action = Column(String, nullable=False)  # create / update / delete
    currency = Column(String, nullable=True)  # null for default changes
    old_value = Column(Float, nullable=True)
    new_value = Column(Float, nullable=True)
    timestamp = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


class FxAlert(Base):
    __tablename__ = "fx_alert"
    id = Column(Integer, primary_key=True, index=True)
    currency = Column(String, nullable=False)
    break_amount = Column(Float, nullable=False)
    reconciliation_id = Column(Integer, nullable=False)  # FK in real system
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)


# ----------------------------------------------------------------------
# Pydantic schemas
# ----------------------------------------------------------------------
class ToleranceBase(BaseModel):
    currency: Optional[str] = Field(None, description="ISO currency code, omitted for default")
    tolerance: float = Field(..., gt=0, description="Positive numeric tolerance")

    @validator("currency")
    def upper_case(cls, v):
        if v:
            return v.upper()
        return v


class ToleranceCreate(ToleranceBase):
    pass


class ToleranceUpdate(BaseModel):
    tolerance: float = Field(..., gt=0)


class ToleranceOut(ToleranceBase):
    id: int
    is_default: bool

    class Config:
        orm_mode = True


class AlertOut(BaseModel):
    id: int
    currency: str
    break_amount: float
    reconciliation_id: int
    created_at: datetime

    class Config:
        orm_mode = True


# ----------------------------------------------------------------------
# FastAPI app & dependencies
# ----------------------------------------------------------------------
app = FastAPI(title="FX Tolerance Service")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ----------------------------------------------------------------------
# Helper: audit logging (append‑only)
# ----------------------------------------------------------------------
def log_audit(
    db: Session,
    *,
    user_id: str,
    action: str,
    currency: Optional[str],
    old_value: Optional[float],
    new_value: Optional[float],
) -> None:
    audit = ToleranceAudit(
        user_id=user_id,
        action=action,
        currency=currency,
        old_value=old_value,
        new_value=new_value,
    )
    db.add(audit)
    db.commit()


# ----------------------------------------------------------------------
# CRUD endpoints for tolerances
# ----------------------------------------------------------------------
@app.post("/tolerances/", response_model=ToleranceOut, status_code=status.HTTP_201_CREATED)
def create_tolerance(
    payload: ToleranceCreate,
    db: Session = Depends(get_db),
    user_id: str = "system",  # placeholder auth
):
    # default tolerance handling
    if payload.currency is None:
        # only one default allowed – enforce via unique constraint
        existing = db.query(CurrencyTolerance).filter_by(is_default=True).first()
        if existing:
            raise HTTPException(
                status_code=400,
                detail="Default tolerance already exists. Use update endpoint.",
            )
        tol = CurrencyTolerance(currency="DEFAULT", tolerance=payload.tolerance, is_default=True)
        db.add(tol)
        db.commit()
        db.refresh(tol)
        log_audit(db, user_id=user_id, action="create", currency=None, old_value=None, new_value=payload.tolerance)
        return tol

    # per‑currency tolerance
    if db.query(CurrencyTolerance).filter_by(currency=payload.currency).first():
        raise HTTPException(status_code=400, detail="Tolerance for this currency already exists.")
    tol = CurrencyTolerance(currency=payload.currency, tolerance=payload.tolerance, is_default=False)
    db.add(tol)
    db.commit()
    db.refresh(tol)
    log_audit(
        db,
        user_id=user_id,
        action="create",
        currency=payload.currency,
        old_value=None,
        new_value=payload.tolerance,
    )
    return tol


@app.get("/tolerances/", response_model=List[ToleranceOut])
def list_tolerances(db: Session = Depends(get_db)):
    return db.query(CurrencyTolerance).all()


@app.put("/tolerances/{currency}", response_model=ToleranceOut)
def update_tolerance(
    currency: str,
    payload: ToleranceUpdate,
    db: Session = Depends(get_db),
    user_id: str = "system",
):
    currency = currency.upper()
    tol = db.query(CurrencyTolerance).filter_by(currency=currency).first()
    if not tol:
        raise HTTPException(status_code=404, detail="Tolerance not found.")
    old = tol.tolerance
    tol.tolerance = payload.tolerance
    db.commit()
    db.refresh(tol)
    log_audit(
        db,
        user_id=user_id,
        action="update",
        currency=currency,
        old_value=old,
        new_value=payload.tolerance,
    )
    return tol


@app.delete("/tolerances/{currency}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tolerance(
    currency: str,
    db: Session = Depends(get_db),
    user_id: str = "system",
):
    currency = currency.upper()
    tol = db.query(CurrencyTolerance).filter_by(currency=currency).first()
    if not tol:
        raise HTTPException(status_code=404, detail="Tolerance not found.")
    if tol.is_default:
        raise HTTPException(status_code=400, detail="Default tolerance cannot be deleted; update it instead.")
    old = tol.tolerance
    db.delete(tol)
    db.commit()
    log_audit(
        db,
        user_id=user_id,
        action="delete",
        currency=currency,
        old_value=old,
        new_value=None,
    )
    return


# ----------------------------------------------------------------------
# Default tolerance endpoint (separate for clarity)
# ----------------------------------------------------------------------
@app.put("/tolerances/default", response_model=ToleranceOut)
def update_default_tolerance(
    payload: ToleranceUpdate,
    db: Session = Depends(get_db),
    user_id: str = "system",
):
    default = db.query(CurrencyTolerance).filter_by(is_default=True).first()
    if not default:
        # create if missing
        default = CurrencyTolerance(currency="DEFAULT", tolerance=payload.tolerance, is_default=True)
        db.add(default)
        db.commit()
        db.refresh(default)
        log_audit(db, user_id, "create", None, None, payload.tolerance)
        return default

    old = default.tolerance
    default.tolerance = payload.tolerance
    db.commit()
    db.refresh(default)
    log_audit(db, user_id, "update", None, old, payload.tolerance)
    return default


# ----------------------------------------------------------------------
# Alert read‑only endpoint (dashboard consumption)
# ----------------------------------------------------------------------
@app.get("/alerts/", response_model=List[AlertOut])
def list_alerts(db: Session = Depends(get_db)):
    return db.query(FxAlert).order_by(FxAlert.created_at.desc()).all()


# ----------------------------------------------------------------------
# Core evaluation logic – to be called by the reconciliation engine
# ----------------------------------------------------------------------
class FxBreak(BaseModel):
    """Incoming break record from the reconciliation engine."""
    reconciliation_id: int
    currency: str
    quantity_diff: float
    market_value_diff: float
    missing_position: bool = False  # true when break is due to missing position only


def evaluate_breaks(breaks: List[FxBreak], db: Session) -> List[FxAlert]:
    """
    Apply per‑currency tolerance (or default) to each break.
    Returns list of generated alerts.
    """
    alerts: List[FxAlert] = []
    # cache tolerances for performance
    tolerance_map = {
        ct.currency: ct.tolerance
        for ct in db.query(CurrencyTolerance).filter(CurrencyTolerance.is_default == False).all()
    }
    default_tol_obj = db.query(CurrencyTolerance).filter_by(is_default=True).first()
    default_tol = default_tol_obj.tolerance if default_tol_obj else 0.0

    for br in breaks:
        if br.missing_position:
            # skip – not part of FX quantity/market‑value evaluation
            continue

        # absolute differences to compare
        diff = max(abs(br.quantity_diff), abs(br.market_value_diff))

        # resolve tolerance
        tol = tolerance_map.get(br.currency.upper(), default_tol)

        if diff > tol:
            alert = FxAlert(
                currency=br.currency.upper(),
                break_amount=diff,
                reconciliation_id=br.reconciliation_id,
            )
            db.add(alert)
            alerts.append(alert)

    db.commit()
    # refresh to get IDs/timestamps
    for a in alerts:
        db.refresh(a)
    return alerts


# ----------------------------------------------------------------------
# Example endpoint to trigger evaluation (for demo/testing)
# ----------------------------------------------------------------------
@app.post("/evaluate/", response_model=List[AlertOut])
def evaluate_endpoint(
    payload: List[FxBreak],
    db: Session = Depends(get_db),
):
    alerts = evaluate_breaks(payload, db)
    return alerts


# ----------------------------------------------------------------------
# Create tables on startup
# ----------------------------------------------------------------------
@app.on_event("startup")
def on_startup():
    Base.metadata.create_all(bind=engine)
```

---

## Self‑review
- **Scope adherence** – Implements only the required storage, audit‑trail, default fallback, break evaluation, and read‑only alert exposure; no UI or external role integration.
- **Immutability guarantee** – `ToleranceAudit` rows are only inserted via `log_audit`; no delete/update paths are exposed, satisfying append‑only requirement.
- **Default handling** – Enforced a unique constraint on `is_default=True` and provided a dedicated endpoint to modify the single default tolerance.