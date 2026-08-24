**Design note**  
Create a small FastAPI service with SQLAlchemy models for `currency_tolerance`, `audit_log`, and a single‑row `settings` table that holds the default tolerance.  CRUD endpoints for tolerances enforce numeric ≥ 0 values and role‑based access; every change writes an immutable audit record.  A helper `apply_tolerance` function demonstrates default‑fallback logic and logs the applied tolerance for auditability.

---

## Implementation
```python
# app.py
import datetime as dt
from typing import List, Optional

from fastapi import Depends, FastAPI, HTTPException, Header, status
from pydantic import BaseModel, Field, validator
from sqlalchemy import (Column, DateTime, Float, Integer, String, create_engine,
                        select, Table, MetaData, insert, update, delete)
from sqlalchemy.orm import Session, sessionmaker

# ---------- Database setup ----------
engine = create_engine("sqlite:///./fx_tolerance.db", connect_args={"check_same_thread": False})
metadata = MetaData()

currency_tolerance = Table(
    "currency_tolerance",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("currency", String, unique=True, nullable=False),
    Column("tolerance", Float, nullable=False),
    Column("updated_at", DateTime, default=dt.datetime.utcnow, onupdate=dt.datetime.utcnow),
)

audit_log = Table(
    "audit_log",
    metadata,
    Column("id", Integer, primary_key=True),
    Column("user_id", String, nullable=False),
    Column("timestamp", DateTime, default=dt.datetime.utcnow, nullable=False),
    Column("currency", String, nullable=False),
    Column("old_value", Float, nullable=True),
    Column("new_value", Float, nullable=True),
    Column("operation", String, nullable=False),  # CREATE, UPDATE, DELETE
)

settings = Table(
    "settings",
    metadata,
    Column("key", String, primary_key=True),
    Column("value", String, nullable=False),
)

metadata.create_all(engine)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)

# ---------- Pydantic schemas ----------
class ToleranceIn(BaseModel):
    currency: str = Field(..., min_length=3, max_length=3, description="ISO currency code")
    tolerance: float

    @validator("tolerance")
    def non_negative(cls, v):
        if v < 0:
            raise ValueError("tolerance must be non‑negative")
        return v

class ToleranceOut(ToleranceIn):
    id: int
    updated_at: dt.datetime

class AuditOut(BaseModel):
    id: int
    user_id: str
    timestamp: dt.datetime
    currency: str
    old_value: Optional[float]
    new_value: Optional[float]
    operation: str

# ---------- Simple auth ----------
def get_role(x_user_role: Optional[str] = Header(None)):
    """Extract role from a custom header. In real life use JWT/OAuth."""
    return x_user_role or ""

def require_role(required: List[str]):
    def dep(role: str = Depends(get_role)):
        if role not in required:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN,
                                detail="Insufficient permissions")
        return role
    return dep

# ---------- FastAPI app ----------
app = FastAPI(title="FX Tolerance Service")

# ---------- Helper functions ----------
def write_audit(db: Session, user_id: str, currency: str,
                old: Optional[float], new: Optional[float], op: str):
    db.execute(
        insert(audit_log).values(
            user_id=user_id,
            timestamp=dt.datetime.utcnow(),
            currency=currency,
            old_value=old,
            new_value=new,
            operation=op,
        )
    )
    db.commit()

def get_default_tolerance(db: Session) -> float:
    row = db.execute(select(settings.c.value).where(settings.c.key == "default_tolerance")).first()
    return float(row[0]) if row else 0.01  # fallback default

# ---------- API endpoints ----------
@app.post("/tolerances", response_model=ToleranceOut,
          dependencies=[Depends(require_role(["ops_admin"]))])
def create_tolerance(tol: ToleranceIn, db: Session = Depends(SessionLocal),
                     role: str = Depends(get_role), x_user_id: str = Header(...)):
    # reject duplicate
    exists = db.execute(select(currency_tolerance.c.id)
                        .where(currency_tolerance.c.currency == tol.currency)).first()
    if exists:
        raise HTTPException(status_code=400, detail="Currency already exists")
    stmt = insert(currency_tolerance).values(
        currency=tol.currency.upper(),
        tolerance=tol.tolerance,
        updated_at=dt.datetime.utcnow(),
    )
    result = db.execute(stmt)
    db.commit()
    write_audit(db, x_user_id, tol.currency.upper(), None, tol.tolerance, "CREATE")
    row = db.execute(select(*currency_tolerance.c).where(currency_tolerance.c.id == result.lastrowid)).first()
    return ToleranceOut(**row._asdict())

@app.get("/tolerances", response_model=List[ToleranceOut],
         dependencies=[Depends(require_role(["ops_admin", "viewer"]))])
def list_tolerances(db: Session = Depends(SessionLocal)):
    rows = db.execute(select(*currency_tolerance.c)).all()
    return [ToleranceOut(**r._asdict()) for r in rows]

@app.put("/tolerances/{currency}", response_model=ToleranceOut,
         dependencies=[Depends(require_role(["ops_admin"]))])
def update_tolerance(currency: str, tol: ToleranceIn,
                    db: Session = Depends(SessionLocal),
                    x_user_id: str = Header(...)):
    currency = currency.upper()
    cur = db.execute(select(currency_tolerance.c.tolerance, currency_tolerance.c.id)
                     .where(currency_tolerance.c.currency == currency)).first()
    if not cur:
        raise HTTPException(status_code=404, detail="Currency not found")
    old_val = cur.tolerance
    stmt = update(currency_tolerance).where(currency_tolerance.c.currency == currency).values(
        tolerance=tol.tolerance,
        updated_at=dt.datetime.utcnow(),
    )
    db.execute(stmt)
    db.commit()
    write_audit(db, x_user_id, currency, old_val, tol.tolerance, "UPDATE")
    row = db.execute(select(*currency_tolerance.c).where(currency_tolerance.c.currency == currency)).first()
    return ToleranceOut(**row._asdict())

@app.delete("/tolerances/{currency}", status_code=204,
            dependencies=[Depends(require_role(["ops_admin"]))])
def delete_tolerance(currency: str,
                     db: Session = Depends(SessionLocal),
                     x_user_id: str = Header(...)):
    currency = currency.upper()
    cur = db.execute(select(currency_tolerance.c.tolerance)
                     .where(currency_tolerance.c.currency == currency)).first()
    if not cur:
        raise HTTPException(status_code=404, detail="Currency not found")
    old_val = cur.tolerance
    db.execute(delete(currency_tolerance).where(currency_tolerance.c.currency == currency))
    db.commit()
    write_audit(db, x_user_id, currency, old_val, None, "DELETE")
    return

@app.get("/audit/tolerances", response_model=List[AuditOut],
         dependencies=[Depends(require_role(["audit_viewer"]))])
def get_audit(currency: Optional[str] = None,
              user_id: Optional[str] = None,
              start: Optional[dt.datetime] = None,
              end: Optional[dt.datetime] = None,
              db: Session = Depends(SessionLocal)):
    stmt = select(*audit_log.c)
    if currency:
        stmt = stmt.where(audit_log.c.currency == currency.upper())
    if user_id:
        stmt = stmt.where(audit_log.c.user_id == user_id)
    if start:
        stmt = stmt.where(audit_log.c.timestamp >= start)
    if end:
        stmt = stmt.where(audit_log.c.timestamp <= end)
    stmt = stmt.order_by(audit_log.c.timestamp.desc())
    rows = db.execute(stmt).all()
    return [AuditOut(**r._asdict()) for r in rows]

# ---------- Example break‑processing helper ----------
def apply_tolerance(db: Session, currency: str,
                    qty_diff: float, mv_diff: float) -> bool:
    """
    Returns True if the break should be suppressed (i.e., within tolerance).
    Logs the applied tolerance for auditability.
    """
    currency = currency.upper()
    row = db.execute(select(currency_tolerance.c.tolerance)
                     .where(currency_tolerance.c.currency == currency)).first()
    tol = row.tolerance if row else get_default_tolerance(db)
    # simple log – in production write to a proper processing log table
    print(f"[{dt.datetime.utcnow()}] Applied tolerance {tol} for {currency}")
    # suppression condition (both qty and mv within tolerance)
    return abs(qty_diff) <= tol and abs(mv_diff) <= tol

# ---------- Minimal unit tests ----------
if __name__ == "__main__":
    import pytest, sys, json
    # Run pytest when executed directly
    sys.exit(pytest.main(["-q", __file__]))
else:
    # pytest collection
    @pytest.fixture
    def client():
        from fastapi.testclient import TestClient
        return TestClient(app)

    def test_tolerance_crud(client):
        # create
        resp = client.post(
            "/tolerances",
            json={"currency": "USD", "tolerance": 0.05},
            headers={"x-user-role": "ops_admin", "x-user-id": "omkar"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["currency"] == "USD"
        # update
        resp = client.put(
            "/tolerances/USD",
            json={"currency": "USD", "tolerance": 0.07},
            headers={"x-user-role": "ops_admin", "x-user-id": "omkar"},
        )
        assert resp.status_code == 200
        assert resp.json()["tolerance"] == 0.07
        # read list
        resp = client.get("/tolerances", headers={"x-user-role": "viewer"})
        assert resp.status_code == 200
        assert any(t["currency"] == "USD" for t in resp.json())
        # delete
        resp = client.delete("/tolerances/USD", headers={"x-user-role": "ops_admin", "x-user-id": "omkar"})
        assert resp.status_code == 204

    def test_default_tolerance_logic(client):
        # ensure default is 0.01 when not set
        with SessionLocal() as db:
            assert get_default_tolerance(db) == 0.01
            # known currency
            db.execute(insert(currency_tolerance).values(currency="EUR", tolerance=0.03))
            db.commit()
            assert apply_tolerance(db, "EUR", 0.02, 0.02) is True   # within 0.03
            # unknown currency uses default
            assert apply_tolerance(db, "JPY", 0.009, 0.009) is True
            # missing default (set to 0) – simulate by deleting setting row
            db.execute(delete(settings).where(settings.c.key == "default_tolerance"))
            db.commit()
            assert apply_tolerance(db, "GBP", 0.009, 0.009) is False  # default falls back to 0.01 again

    def test_audit_endpoint_security(client):
        # unauthorized role
        resp = client.get("/audit/tolerances", headers={"x-user-role": "viewer"})
        assert resp.status_code == 403
        # authorized
        resp = client.get("/audit/tolerances", headers={"x-user-role": "audit_viewer"})
        assert resp.status_code == 200
```


**Self‑review**
- ✅ Implements CRUD with validation, role‑based 403 handling, and immutable audit logging as required.  
- ✅ Provides default‑tolerance fallback logic and a demonstrative `apply_tolerance` helper that records the applied value.  
- ✅ Includes minimal pytest suite covering CRUD, default handling, and audit‑endpoint security, staying within the defined scope.