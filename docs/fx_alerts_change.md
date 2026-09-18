## Design note  
A lightweight FastAPI service holds FX break data, per‑currency tolerances, and an immutable audit log in memory.  
`GET /alerts` filters breaks that exceed the current tolerance, returning currency, amount, and timestamp.  
`POST /tolerances` updates a tolerance, records an audit entry, and immediately affects alert filtering.  
Audit entries are append‑only and can be queried via `GET /audit` with optional filters.

```python
# fx_alerts.py
from datetime import datetime
from typing import List, Optional
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

app = FastAPI(title="FX Tolerance & Alert Service")

# ---------- Data Models ----------
class FXBreak(BaseModel):
    currency: str
    amount: float
    timestamp: datetime

class Alert(BaseModel):
    currency: str
    break_amount: float
    timestamp: datetime

class ToleranceUpdate(BaseModel):
    currency: str
    tolerance: float
    user_id: str

class AuditEntry(BaseModel):
    user_id: str
    timestamp: datetime
    currency: str
    old_value: float
    new_value: float

# ---------- In‑memory Stores ----------
# Pre‑populated FX breaks for demo purposes
fx_breaks: List[FXBreak] = [
    FXBreak(currency="USD", amount=1200.0, timestamp=datetime(2024, 9, 1, 10, 0)),
    FXBreak(currency="EUR", amount=800.0, timestamp=datetime(2024, 9, 1, 10, 5)),
    FXBreak(currency="JPY", amount=50000.0, timestamp=datetime(2024, 9, 1, 10, 10)),
    FXBreak(currency="USD", amount=300.0, timestamp=datetime(2024, 9, 1, 10, 15)),
]

# Default tolerance: 1000 for all currencies unless overridden
default_tolerance: float = 1000.0
tolerances: dict[str, float] = {}  # e.g., {"USD": 1500.0}

audit_log: List[AuditEntry] = []

# ---------- Helper Functions ----------
def get_tolerance(currency: str) -> float:
    return tolerances.get(currency, default_tolerance)

def record_audit(user_id: str, currency: str, old: float, new: float):
    audit_log.append(
        AuditEntry(
            user_id=user_id,
            timestamp=datetime.utcnow(),
            currency=currency,
            old_value=old,
            new_value=new,
        )
    )

# ---------- API Endpoints ----------
@app.get("/alerts", response_model=List[Alert])
def get_alerts():
    """Return FX breaks that exceed the configured tolerance."""
    alerts: List[Alert] = []
    for brk in fx_breaks:
        tol = get_tolerance(brk.currency)
        if brk.amount > tol:
            alerts.append(
                Alert(
                    currency=brk.currency,
                    break_amount=brk.amount,
                    timestamp=brk.timestamp,
                )
            )
    return alerts

@app.post("/tolerances")
def set_tolerance(update: ToleranceUpdate):
    """Set or update tolerance for a currency; logs audit."""
    old_val = get_tolerance(update.currency)
    tolerances[update.currency] = update.tolerance
    record_audit(update.user_id, update.currency, old_val, update.tolerance)
    return {"message": "Tolerance updated", "currency": update.currency, "new_tolerance": update.tolerance}

@app.get("/audit", response_model=List[AuditEntry])
def get_audit(
    currency: Optional[str] = Query(None),
    user_id: Optional[str] = Query(None),
    start: Optional[datetime] = Query(None),
    end: Optional[datetime] = Query(None),
):
    """Query immutable audit log with optional filters."""
    results = audit_log
    if currency:
        results = [e for e in results if e.currency == currency]
    if user_id:
        results = [e for e in results if e.user_id == user_id]
    if start:
        results = [e for e in results if e.timestamp >= start]
    if end:
        results = [e for e in results if e.timestamp <= end]
    return results
```

## Self‑review  
- **Completeness**: Covers all acceptance criteria for viewing alerts, setting tolerances, and immutable audit logging.  
- **Simplicity**: Uses in‑memory structures; no external DB, keeping the implementation minimal.  
- **Extensibility**: FastAPI endpoints and Pydantic models make adding persistence or more currencies straightforward.