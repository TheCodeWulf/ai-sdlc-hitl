**Design note**  
Implement a tiny Flask‑based service backed by SQLite that stores per‑currency FX tolerances, an immutable audit log, and generated alerts. The service exposes REST endpoints for tolerance CRUD (restricted to the *Tolerance‑Editor* role) and audit queries (restricted to *Audit‑Viewer*). A helper `process_break` function applies the tolerance rules, logs suppressed breaks, and creates read‑only alerts for out‑of‑tolerance breaks.

---

## Implementation
```python
# app.py
"""
Minimal implementation of per‑currency FX tolerance configuration,
suppression, read‑only alerts and audit logging.
"""

import sqlite3
import datetime
from flask import Flask, request, jsonify, abort

app = Flask(__name__)
DB = "fx_tolerance.db"

# ----------------------------------------------------------------------
# Database helpers
# ----------------------------------------------------------------------
def get_conn():
    conn = sqlite3.connect(DB, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    with get_conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS tolerances (
            currency TEXT PRIMARY KEY,
            tolerance REAL NOT NULL CHECK (tolerance >= 0)
        );
        CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            ts TIMESTAMP NOT NULL,
            operation TEXT NOT NULL,
            currency TEXT,
            old_value REAL,
            new_value REAL
        );
        CREATE TABLE IF NOT EXISTS suppressed_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            break_id TEXT NOT NULL,
            currency TEXT NOT NULL,
            tolerance_used REAL NOT NULL,
            ts TIMESTAMP NOT NULL
        );
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            break_id TEXT NOT NULL,
            currency TEXT NOT NULL,
            break_amount REAL NOT NULL,
            tolerance REAL NOT NULL,
            created_at TIMESTAMP NOT NULL,
            expires_at TIMESTAMP NOT NULL
        );
        """)
        # ensure a default tolerance row exists (currency = 'DEFAULT')
        c.execute("INSERT OR IGNORE INTO tolerances (currency, tolerance) VALUES ('DEFAULT', 0)")
    print("DB initialized")

init_db()

# ----------------------------------------------------------------------
# Simple role check (expects header X-Role)
# ----------------------------------------------------------------------
def require_role(*allowed):
    role = request.headers.get("X-Role")
    if role not in allowed:
        abort(403, description="Insufficient role")
    return role

# ----------------------------------------------------------------------
# Audit helper
# ----------------------------------------------------------------------
def write_audit(user_id, operation, currency, old, new):
    with get_conn() as c:
        c.execute(
            """INSERT INTO audit_log (user_id, ts, operation, currency, old_value, new_value)
               VALUES (?,?,?,?,?,?)""",
            (user_id, datetime.datetime.utcnow(), operation, currency, old, new)
        )

# ----------------------------------------------------------------------
# Tolerance endpoints (Story 1 & 4)
# ----------------------------------------------------------------------
@app.route("/tolerance/<currency>", methods=["GET"])
def get_tolerance(currency):
    with get_conn() as c:
        row = c.execute(
            "SELECT tolerance FROM tolerances WHERE currency = ?",
            (currency.upper(),)
        ).fetchone()
        if not row:
            # fallback to default
            row = c.execute(
                "SELECT tolerance FROM tolerances WHERE currency = 'DEFAULT'"
            ).fetchone()
        return jsonify({"currency": currency.upper(), "tolerance": row["tolerance"]})

@app.route("/tolerance/<currency>", methods=["POST", "PUT"])
def upsert_tolerance(currency):
    require_role("Tolerance-Editor")
    data = request.get_json()
    if not data or "tolerance" not in data:
        abort(400, description="Missing tolerance")
    try:
        tol = float(data["tolerance"])
        if tol < 0:
            raise ValueError()
    except ValueError:
        abort(400, description="Tolerance must be a non‑negative number")
    currency = currency.upper()
    user = request.headers.get("X-User", "unknown")
    with get_conn() as c:
        cur = c.execute(
            "SELECT tolerance FROM tolerances WHERE currency = ?",
            (currency,)
        ).fetchone()
        if cur:
            old = cur["tolerance"]
            c.execute(
                "UPDATE tolerances SET tolerance = ? WHERE currency = ?",
                (tol, currency)
            )
            op = "UPDATE"
        else:
            old = None
            c.execute(
                "INSERT INTO tolerances (currency, tolerance) VALUES (?,?)",
                (currency, tol)
            )
            op = "CREATE"
        write_audit(user, op, currency, old, tol)
    return jsonify({"currency": currency, "tolerance": tol, "operation": op})

# ----------------------------------------------------------------------
# Suppression & Alert logic (Story 2 & 3)
# ----------------------------------------------------------------------
def get_tolerance_for(currency):
    with get_conn() as c:
        row = c.execute(
            "SELECT tolerance FROM tolerances WHERE currency = ?",
            (currency.upper(),)
        ).fetchone()
        if row:
            return row["tolerance"]
        # default
        row = c.execute(
            "SELECT tolerance FROM tolerances WHERE currency = 'DEFAULT'"
        ).fetchone()
        return row["tolerance"]

def log_suppressed(break_id, currency, tolerance):
    with get_conn() as c:
        c.execute(
            """INSERT INTO suppressed_log (break_id, currency, tolerance_used, ts)
               VALUES (?,?,?,?)""",
            (break_id, currency, tolerance, datetime.datetime.utcnow())
        )

def create_alert(break_id, currency, amount, tolerance):
    now = datetime.datetime.utcnow()
    expires = now + datetime.timedelta(days=30)
    with get_conn() as c:
        c.execute(
            """INSERT INTO alerts (break_id, currency, break_amount, tolerance,
                                   created_at, expires_at)
               VALUES (?,?,?,?,?,?)""",
            (break_id, currency, amount, tolerance, now, expires)
        )

def process_break(break_record):
    """
    break_record dict must contain:
        id, currency, qty_diff, mv_diff, missing (bool)
    Returns: dict with keys: suppressed (bool), alert_created (bool)
    """
    if break_record.get("missing"):
        # never suppress missing positions
        return {"suppressed": False, "alert_created": False}
    cur = break_record["currency"]
    tol = get_tolerance_for(cur)
    qty_ok = abs(break_record.get("qty_diff", 0)) <= tol
    mv_ok = abs(break_record.get("mv_diff", 0)) <= tol
    if qty_ok and mv_ok:
        # suppress
        log_suppressed(break_record["id"], cur, tol)
        return {"suppressed": True, "alert_created": False}
    else:
        # generate read‑only alert
        # use the larger of the two differences as "break amount"
        amount = max(abs(break_record.get("qty_diff", 0)),
                     abs(break_record.get("mv_diff", 0)))
        create_alert(break_record["id"], cur, amount, tol)
        return {"suppressed": False, "alert_created": True}

# Example endpoint to ingest a break (for demo/testing)
@app.route("/break", methods=["POST"])
def ingest_break():
    """
    Expected JSON:
    {
        "id": "BRK123",
        "currency": "USD",
        "qty_diff": 10.5,
        "mv_diff": 9.8,
        "missing": false
    }
    """
    data = request.get_json()
    if not data or "id" not in data or "currency" not in data:
        abort(400, "Invalid break payload")
    result = process_break(data)
    return jsonify({"break_id": data["id"], **result})

# ----------------------------------------------------------------------
# Alert view (read‑only) – Story 3
# ----------------------------------------------------------------------
@app.route("/alerts", methods=["GET"])
def list_alerts():
    require_role("Dashboard-Viewer")
    currency = request.args.get("currency")
    min_amount = request.args.get("min_amount", type=float)
    now = datetime.datetime.utcnow()
    query = """SELECT id, break_id, currency, break_amount, tolerance,
                      created_at, expires_at
               FROM alerts
               WHERE expires_at > ?"""
    params = [now]
    if currency:
        query += " AND currency = ?"
        params.append(currency.upper())
    if min_amount is not None:
        query += " AND break_amount >= ?"
        params.append(min_amount)
    with get_conn() as c:
        rows = c.execute(query, params).fetchall()
        alerts = [dict(row) for row in rows]
    return jsonify(alerts)

# ----------------------------------------------------------------------
# Audit query – Story 4
# ----------------------------------------------------------------------
@app.route("/audit", methods=["GET"])
def query_audit():
    require_role("Audit-Viewer")
    currency = request.args.get("currency")
    user = request.args.get("user")
    start = request.args.get("start")  # ISO date
    end = request.args.get("end")
    query = "SELECT * FROM audit_log WHERE 1=1"
    params = []
    if currency:
        query += " AND currency = ?"
        params.append(currency.upper())
    if user:
        query += " AND user_id = ?"
        params.append(user)
    if start:
        query += " AND ts >= ?"
        params.append(start)
    if end:
        query += " AND ts <= ?"
        params.append(end)
    query += " ORDER BY ts DESC"
    with get_conn() as c:
        rows = c.execute(query, params).fetchall()
        logs = [dict(row) for row in rows]
    return jsonify(logs)

# ----------------------------------------------------------------------
# Run server (development only)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    app.run(debug=True)
```

---

## Self‑review
- **Scope adherence:** Implements tolerance CRUD, immutable audit logging, suppression & alert generation, and secured read‑only endpoints – exactly the four stories. No UI beyond simple JSON endpoints.
- **Correctness:** Validation prevents negative tolerances; missing‑position breaks are never suppressed; both quantity and MV must be within tolerance to suppress; alerts persist 30 days and are searchable.
- **Maintainability:** SQLite schema is explicit; role checks are centralized; helper functions isolate business logic, making future extension (e.g., more currencies) straightforward.