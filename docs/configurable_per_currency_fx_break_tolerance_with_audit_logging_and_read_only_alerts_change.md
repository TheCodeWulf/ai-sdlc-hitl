**## Design note**  
Implement a lightweight FastAPI service backed by SQLite that (1) stores per‑currency FX tolerances with a configurable default, (2) logs every tolerance change in an immutable audit table, (3) evaluates incoming reconciliation records and suppresses “minor” breaks, and (4) creates read‑only alerts for material breaks. All logic is kept in a single module for clarity and testability.

---

**## Implementation**  

```python
# main.py
"""
FastAPI service implementing:
- Currency tolerance CRUD with default fallback
- Immutable audit log of tolerance changes
- Break suppression & material‑break alert generation
- Simple SQLite persistence (no external dependencies)
"""

import asyncio
import datetime as dt
import re
import sqlite3
from typing import List, Optional

import uvicorn
from fastapi import Depends, FastAPI, HTTPException, Query, Request, status
from pydantic import BaseModel, Field, validator

# ----------------------------------------------------------------------
# Database utilities
# ----------------------------------------------------------------------
DB_PATH = "fx_tolerance.db"


def get_db():
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    yield conn
    conn.close()


def init_db():
    conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    cur = conn.cursor()
    # Tolerances table – one row per currency
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS tolerances (
            currency TEXT PRIMARY KEY,
            tolerance REAL NOT NULL,
            updated_at TIMESTAMP NOT NULL
        )
        """
    )
    # Config table – holds default tolerance and retention days
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        )
        """
    )
    # Insert defaults if missing
    cur.execute(
        "INSERT OR IGNORE INTO config (key, value) VALUES ('default_tolerance', '0.01')"
    )
    cur.execute(
        "INSERT OR IGNORE INTO config (key, value) VALUES ('audit_retention_days', '365')"
    )
    # Immutable audit log – append‑only
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            currency TEXT NOT NULL,
            new_tolerance REAL NOT NULL,
            user_id TEXT NOT NULL,
            ts TIMESTAMP NOT NULL,
            operation TEXT NOT NULL
        )
        """
    )
    # Alerts – read‑only objects
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            currency TEXT NOT NULL,
            break_amount REAL NOT NULL,
            tolerance REAL NOT NULL,
            ts TIMESTAMP NOT NULL,
            cleared BOOLEAN NOT NULL DEFAULT 0
        )
        """
    )
    conn.commit()
    conn.close()


# ----------------------------------------------------------------------
# Pydantic models
# ----------------------------------------------------------------------
CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


class ToleranceIn(BaseModel):
    tolerance: float = Field(..., gt=0)
    user_id: str = Field(..., min_length=1)

    @validator("tolerance")
    def round_tolerance(cls, v):
        # store with 6 decimal places – enough for FX
        return round(v, 6)


class ToleranceOut(BaseModel):
    currency: str
    tolerance: float
    effective_tolerance: float
    updated_at: dt.datetime


class AuditEntry(BaseModel):
    id: int
    currency: str
    new_tolerance: float
    user_id: str
    ts: dt.datetime
    operation: str


class BreakRecord(BaseModel):
    currency: str
    quantity_diff: float
    market_value_diff: float


class AlertOut(BaseModel):
    id: int
    currency: str
    break_amount: float
    tolerance: float
    ts: dt.datetime
    cleared: bool


# ----------------------------------------------------------------------
# FastAPI app & routes
# ----------------------------------------------------------------------
app = FastAPI(title="FX Tolerance Service")


@app.on_event("startup")
def startup():
    init_db()


def _get_default_tolerance(conn: sqlite3.Connection) -> float:
    cur = conn.execute("SELECT value FROM config WHERE key='default_tolerance'")
    row = cur.fetchone()
    return float(row["value"])


def _validate_currency(code: str):
    if not CURRENCY_RE.match(code):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid currency code; must be three uppercase letters.",
        )


# ------------------- 1. Define Currency Tolerances --------------------
@app.put("/tolerances/{currency}", response_model=ToleranceOut)
def set_tolerance(
    currency: str,
    payload: ToleranceIn,
    db: sqlite3.Connection = Depends(get_db),
):
    _validate_currency(currency)
    now = dt.datetime.utcnow()
    # Upsert tolerance
    db.execute(
        """
        INSERT INTO tolerances (currency, tolerance, updated_at)
        VALUES (?, ?, ?)
        ON CONFLICT(currency) DO UPDATE SET tolerance=excluded.tolerance,
                                            updated_at=excluded.updated_at
        """,
        (currency, payload.tolerance, now),
    )
    # Audit log (immutable)
    db.execute(
        """
        INSERT INTO audit_log (currency, new_tolerance, user_id, ts, operation)
        VALUES (?, ?, ?, ?, ?)
        """,
        (currency, payload.tolerance, payload.user_id, now, "upsert"),
    )
    db.commit()
    # Effective tolerance = stored value (fallback not needed here)
    return ToleranceOut(
        currency=currency,
        tolerance=payload.tolerance,
        effective_tolerance=payload.tolerance,
        updated_at=now,
    )


@app.get("/tolerances/{currency}", response_model=ToleranceOut)
def get_tolerance(
    currency: str,
    db: sqlite3.Connection = Depends(get_db),
):
    _validate_currency(currency)
    cur = db.execute(
        "SELECT tolerance, updated_at FROM tolerances WHERE currency=?", (currency,)
    )
    row = cur.fetchone()
    if row:
        stored = float(row["tolerance"])
        updated_at = row["updated_at"]
    else:
        stored = _get_default_tolerance(db)
        updated_at = None
    return ToleranceOut(
        currency=currency,
        tolerance=stored,
        effective_tolerance=stored,
        updated_at=updated_at or dt.datetime.utcnow(),
    )


# ------------------- 2. Suppress Minor Breaks --------------------
@app.post("/breaks/evaluate")
def evaluate_breaks(
    records: List[BreakRecord],
    db: sqlite3.Connection = Depends(get_db),
):
    """
    Returns material breaks and creates alerts for them.
    Suppressed (minor) breaks are omitted from the response.
    """
    material = []
    for rec in records:
        _validate_currency(rec.currency)
        # fetch tolerance (fallback to default)
        cur = db.execute(
            "SELECT tolerance FROM tolerances WHERE currency=?", (rec.currency,)
        )
        row = cur.fetchone()
        tol = float(row["tolerance"]) if row else _get_default_tolerance(db)

        # Determine if both diffs are within tolerance
        if abs(rec.quantity_diff) <= tol and abs(rec.market_value_diff) <= tol:
            # suppressed – nothing to do
            continue

        # material break
        break_amount = max(abs(rec.quantity_diff), abs(rec.market_value_diff))
        material.append(
            {
                "currency": rec.currency,
                "break_amount": break_amount,
                "tolerance": tol,
                "timestamp": dt.datetime.utcnow().isoformat(),
            }
        )
        # create alert (read‑only)
        db.execute(
            """
            INSERT INTO alerts (currency, break_amount, tolerance, ts, cleared)
            VALUES (?, ?, ?, ?, 0)
            """,
            (rec.currency, break_amount, tol, dt.datetime.utcnow()),
        )
    db.commit()
    return {"material_breaks": material}


# ------------------- 3. Audit Tolerance Changes --------------------
@app.get("/audit", response_model=List[AuditEntry])
def get_audit(
    currency: Optional[str] = Query(None, regex=r"^[A-Z]{3}$"),
    start: Optional[dt.datetime] = None,
    end: Optional[dt.datetime] = None,
    db: sqlite3.Connection = Depends(get_db),
):
    query = "SELECT * FROM audit_log WHERE 1=1"
    params = []
    if currency:
        query += " AND currency=?"
        params.append(currency)
    if start:
        query += " AND ts>=?"
        params.append(start)
    if end:
        query += " AND ts<=?"
        params.append(end)
    query += " ORDER BY ts ASC"
    cur = db.execute(query, tuple(params))
    rows = cur.fetchall()
    return [AuditEntry(**dict(row)) for row in rows]


# Prevent updates/deletes on audit_log – FastAPI will not expose such routes.
# If a client tries to hit a non‑existent endpoint, FastAPI returns 404/405.


# ------------------- 4. Read‑Only Alerting --------------------
@app.get("/alerts", response_model=List[AlertOut])
def list_alerts(
    cleared: Optional[bool] = None,
    db: sqlite3.Connection = Depends(get_db),
):
    query = "SELECT * FROM alerts"
    params = []
    if cleared is not None:
        query += " WHERE cleared=?"
        params.append(int(cleared))
    cur = db.execute(query, tuple(params))
    rows = cur.fetchall()
    return [AlertOut(**dict(row)) for row in rows]


@app.post("/alerts/{alert_id}/clear")
def clear_alert(alert_id: int, db: sqlite3.Connection = Depends(get_db)):
    # Simple manual clear – marks alert as cleared.
    cur = db.execute("SELECT cleared FROM alerts WHERE id=?", (alert_id,))
    if not cur.fetchone():
        raise HTTPException(status_code=404, detail="Alert not found")
    db.execute("UPDATE alerts SET cleared=1 WHERE id=?", (alert_id,))
    db.commit()
    return {"status": "cleared"}


# ----------------------------------------------------------------------
# Background task – audit retention (archiving placeholder)
# ----------------------------------------------------------------------
@app.on_event("startup")
@asyncio.coroutine
def schedule_retention():
    async def retention_job():
        while True:
            await asyncio.sleep(24 * 3600)  # run daily
            conn = sqlite3.connect(DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
            cur = conn.execute(
                "SELECT value FROM config WHERE key='audit_retention_days'"
            )
            days = int(cur.fetchone()["value"])
            cutoff = dt.datetime.utcnow() - dt.timedelta(days=days)
            # In a real system we would move rows to an archive table.
            # Here we simply keep them (requirement: remain queryable).
            conn.close()

    asyncio.create_task(retention_job())


# ----------------------------------------------------------------------
# Unit tests (pytest) – placed inline for brevity; in practice separate file.
# ----------------------------------------------------------------------
def _test_client():
    from fastapi.testclient import TestClient

    return TestClient(app)


def test_tolerance_crud():
    client = _test_client()
    # create
    resp = client.put(
        "/tolerances/USD",
        json={"tolerance": 0.05, "user_id": "tester"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["currency"] == "USD"
    assert data["tolerance"] == 0.05

    # get
    resp = client.get("/tolerances/USD")
    assert resp.status_code == 200
    assert resp.json()["tolerance"] == 0.05

    # fallback to default
    resp = client.get("/tolerances/JPY")
    assert resp.status_code == 200
    assert resp.json()["tolerance"] == 0.01  # default from init


def test_break_suppression_and_alerts():
    client = _test_client()
    # ensure tolerance for EUR is 0.02
    client.put("/tolerances/EUR", json={"tolerance": 0.02, "user_id": "tester"})
    payload = [
        {"currency": "EUR", "quantity_diff": 0.015, "market_value_diff": 0.018},
        {"currency": "EUR", "quantity_diff": 0.03, "market_value_diff": 0.01},
        {"currency": "USD", "quantity_diff": 0.005, "market_value_diff": 0.005},
    ]
    resp = client.post("/breaks/evaluate", json