**## Design note**  
Use a lightweight FastAPI service with in‑memory dictionaries to store per‑currency tolerances, a default tolerance, and an append‑only audit log. Expose CRUD endpoints for tolerances (role‑checked via a simple header), an audit query endpoint, and a reconciliation endpoint that evaluates a break against the appropriate tolerance and creates a dashboard alert when the break is material. Alerts are kept in an in‑memory list to simulate persistence.

**## Implementation**  
```python
# app.py
"""
Minimal implementation of per‑currency FX tolerance management,
audit logging, and dashboard alerts.
"""

from fastapi import FastAPI, HTTPException, Depends, Header, BackgroundTasks
from pydantic import BaseModel, Field, validator
from typing import Dict, List, Optional
from datetime import datetime, timezone
import uuid

app = FastAPI(title="FX Tolerance Service")

# ------------------------------
# In‑memory stores (replace with DB in prod)
# ------------------------------
tolerances: Dict[str, float] = {}          # currency_code -> tolerance
default_tolerance: float = 0.01           # example default (1%)
audit_log: List[Dict] = []                # append‑only audit records
alerts: List[Dict] = []                   # persisted dashboard alerts

# ------------------------------
# Security stub (role check)
# ------------------------------
def get_current_user(x_user_id: str = Header(...), x_roles: str = Header("")) -> Dict:
    """Extract user info from headers. In real life use proper auth."""
    roles = {r.strip() for r in x_roles.split(",")} if x_roles else set()
    return {"user_id": x_user_id, "roles": roles}

def require_tolerance_admin(user: Dict = Depends(get_current_user)):
    if "tolerance-admin" not in user["roles"]:
        raise HTTPException(status_code=403, detail="Insufficient permissions")
    return user

# ------------------------------
# Pydantic models
# ------------------------------
class ToleranceDTO(BaseModel):
    currency: Optional[str] = Field(None, description="ISO currency code, omitted for default")
    tolerance: float = Field(..., gt=0, description="Tolerance value (absolute)")

    @validator("currency")
    def uppercase_currency(cls, v):
        if v is not None and not v.isalpha():
            raise ValueError("currency must be alphabetic")
        return v.upper() if v else v

class AuditRecord(BaseModel):
    id: str
    currency: Optional[str]
    old_value: Optional[float]
    new_value: Optional[float]
    user_id: str
    action: str
    timestamp: datetime

class BreakDTO(BaseModel):
    currency: str
    quantity_diff: float
    market_value_diff: float
    source: str = Field(..., description="Identifier of the source system")
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

class AlertDTO(BaseModel):
    id: str
    currency: str
    break_amount: float
    timestamp: datetime
    created_at: datetime

# ------------------------------
# Helper functions
# ------------------------------
def log_audit(currency: Optional[str], old: Optional[float], new: Optional[float],
              user_id: str, action: str):
    record = AuditRecord(
        id=str(uuid.uuid4()),
        currency=currency,
        old_value=old,
        new_value=new,
        user_id=user_id,
        action=action,
        timestamp=datetime.now(timezone.utc)
    )
    audit_log.append(record.dict())

def get_effective_tolerance(currency: str) -> float:
    return tolerances.get(currency.upper(), default_tolerance)

def create_alert(break_: BreakDTO):
    alert = AlertDTO(
        id=str(uuid.uuid4()),
        currency=break_.currency.upper(),
        break_amount=max(abs(break_.quantity_diff), abs(break_.market_value_diff)),
        timestamp=break_.timestamp,
        created_at=datetime.now(timezone.utc)
    )
    alerts.append(alert.dict())

# ------------------------------
# API Endpoints
# ------------------------------

# 1️⃣ Tolerance CRUD (admin only)
@app.post("/tolerances", dependencies=[Depends(require_tolerance_admin)])
def create_tolerance(dto: ToleranceDTO, user: Dict = Depends(get_current_user)):
    if dto.currency is None:
        # default tolerance
        global default_tolerance
        old = default_tolerance
        default_tolerance = dto.tolerance
        log_audit(None, old, dto.tolerance, user["user_id"], "update-default")
        return {"message": "Default tolerance updated"}
    cur = dto.currency.upper()
    if cur in tolerances:
        raise HTTPException(status_code=409, detail="Tolerance already exists")
    tolerances[cur] = dto.tolerance
    log_audit(cur, None, dto.tolerance, user["user_id"], "create")
    return {"currency": cur, "tolerance": dto.tolerance}

@app.put("/tolerances/{currency}", dependencies=[Depends(require_tolerance_admin)])
def update_tolerance(currency: str, dto: ToleranceDTO, user: Dict = Depends(get_current_user)):
    cur = currency.upper()
    if cur not in tolerances:
        raise HTTPException(status_code=404, detail="Tolerance not found")
    old = tolerances[cur]
    tolerances[cur] = dto.tolerance
    log_audit(cur, old, dto.tolerance, user["user_id"], "update")
    return {"currency": cur, "tolerance": dto.tolerance}

@app.delete("/tolerances/{currency}", dependencies=[Depends(require_tolerance_admin)])
def delete_tolerance(currency: str, user: Dict = Depends(get_current_user)):
    cur = currency.upper()
    if cur not in tolerances:
        raise HTTPException(status_code=404, detail="Tolerance not found")
    old = tolerances.pop(cur)
    log_audit(cur, old, None, user["user_id"], "delete")
    return {"message": f"Tolerance for {cur} deleted"}

@app.get("/tolerances")
def list_tolerances():
    return {"default": default_tolerance, "per_currency": tolerances}

# 2️⃣ Audit query (read‑only)
@app.get("/audit")
def get_audit(currency: Optional[str] = None):
    if currency:
        filtered = [r for r in audit_log if r["currency"] == currency.upper()]
    else:
        filtered = audit_log
    return filtered

# 3️⃣ Dashboard alerts view (read‑only)
@app.get("/alerts")
def list_alerts():
    return alerts

# 4️⃣ Reconciliation endpoint – evaluates a break and creates alert if material
@app.post("/reconcile")
def reconcile_break(break_: BreakDTO,
                    background_tasks: BackgroundTasks,
                    user: Dict = Depends(get_current_user)):
    """
    Simulates a single break evaluation. In a real system this would be part of a batch job.
    """
    # If break exists only in one source (simulated by source field), treat as material
    material = break_.source == "single"
    if not material:
        tol = get_effective_tolerance(break_.currency)
        if abs(break_.quantity_diff) <= tol and abs(break_.market_value_diff) <= tol:
            # suppressed break
            return {"status": "suppressed", "reason": "within tolerance"}
    # Material break – schedule alert creation (immediate for demo)
    background_tasks.add_task(create_alert, break_)
    return {"status": "alert_created"}

# ------------------------------
# Startup seed (optional)
# ------------------------------
@app.on_event("startup")
def seed():
    # Example seed values
    tolerances.update({"USD": 0.05, "EUR": 0.04, "JPY": 0.10})
    # default already set to 0.01
```

**## Self‑review**  
- ✅ Implements CRUD for per‑currency and default tolerances with role‑based check (`tolerance-admin`).  
- ✅ Every create/update/delete writes an immutable audit record; audit endpoint is read‑only.  
- ✅ Reconciliation logic applies per‑currency or default tolerance, suppresses non‑material breaks, and generates a persisted alert for material ones.  
- ⚠️ In‑memory stores are used; a production system would replace them with a relational DB and proper migrations.  
- ⚠️ Role enforcement is a simple header check; real authentication/authorization (OAuth/JWT) is out of scope.  
- ⚠️ Alert timing is immediate; a real implementation would schedule the alert after ≤5 minutes using a task queue.