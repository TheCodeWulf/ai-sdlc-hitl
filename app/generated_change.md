## Design note
- Use Flask + SQLAlchemy with a lightweight SQLite DB to store per‑currency tolerances, a single default tolerance, and an immutable audit log.  
- CRUD endpoints enforce the **ToleranceAdmin** role via a simple request‑header check; all other callers receive read‑only data.  
- The reconciliation engine is exposed as `/process_breaks`; it applies the configured tolerance (or default) to each break, suppresses material‑level breaks, and marks non‑suppressible missing‑position breaks.

## Implementation
```python
# app.py
import os
from datetime import datetime
from flask import Flask, request, jsonify, abort
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import CheckConstraint, desc

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///fx_tolerance.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

# ----------------------------------------------------------------------
# Models
# ----------------------------------------------------------------------
class Tolerance(db.Model):
    __tablename__ = "tolerances"
    currency = db.Column(db.String(3), primary_key=True)  # ISO code or 'DEFAULT'
    value = db.Column(db.Float, nullable=False)
    __table_args__ = (CheckConstraint("value >= 0", name="ck_positive_value"),)

class AuditLog(db.Model):
    __tablename__ = "audit_log"
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    currency = db.Column(db.String(3), nullable=False)
    old_value = db.Column(db.Float, nullable=True)
    new_value = db.Column(db.Float, nullable=True)
    user_id = db.Column(db.String, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow, nullable=False)
    operation = db.Column(db.String, nullable=False)  # CREATE, UPDATE, DELETE

# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------
def is_admin():
    return request.headers.get("X-User-Role") == "ToleranceAdmin"

def current_user():
    return request.headers.get("X-User-Id", "unknown")

def log_audit(currency, old, new, op):
    entry = AuditLog(
        currency=currency,
        old_value=old,
        new_value=new,
        user_id=current_user(),
        operation=op,
    )
    db.session.add(entry)
    db.session.commit()

def get_tolerance(currency):
    tol = Tolerance.query.filter_by(currency=currency).first()
    if tol:
        return tol.value
    # fallback to default
    default = Tolerance.query.filter_by(currency="DEFAULT").first()
    return default.value if default else 0.0  # safe default if not set

# ----------------------------------------------------------------------
# API – Tolerance CRUD
# ----------------------------------------------------------------------
@app.route("/tolerances", methods=["GET"])
def list_tolerances():
    records = Tolerance.query.all()
    return jsonify([{"currency": r.currency, "value": r.value} for r in records])

@app.route("/tolerances", methods=["POST"])
def create_tolerance():
    if not is_admin():
        abort(403)
    data = request.get_json()
    cur = data.get("currency")
    val = data.get("value")
    if not cur or not isinstance(val, (int, float)) or val < 0:
        abort(400, "Invalid payload")
    if Tolerance.query.get(cur):
        abort(400, "Currency already exists")
    tol = Tolerance(currency=cur, value=val)
    db.session.add(tol)
    db.session.commit()
    log_audit(cur, None, val, "CREATE")
    return jsonify({"currency": cur, "value": val}), 200

@app.route("/tolerances/<currency>", methods=["PUT"])
def update_tolerance(currency):
    if not is_admin():
        abort(403)
    data = request.get_json()
    val = data.get("value")
    if not isinstance(val, (int, float)) or val < 0:
        abort(400, "Invalid value")
    tol = Tolerance.query.get(currency)
    if not tol:
        abort(404)
    old = tol.value
    tol.value = val
    db.session.commit()
    log_audit(currency, old, val, "UPDATE")
    return jsonify({"currency": currency, "value": val}), 200

@app.route("/tolerances/<currency>", methods=["DELETE"])
def delete_tolerance(currency):
    if not is_admin():
        abort(403)
    tol = Tolerance.query.get(currency)
    if not tol:
        abort(404)
    old = tol.value
    db.session.delete(tol)
    db.session.commit()
    log_audit(currency, old, None, "DELETE")
    return "", 204

# ----------------------------------------------------------------------
# API – Default tolerance (treated as a special record)
# ----------------------------------------------------------------------
@app.route("/default_tolerance", methods=["GET"])
def get_default():
    default = Tolerance.query.filter_by(currency="DEFAULT").first()
    val = default.value if default else None
    return jsonify({"default_tolerance": val})

@app.route("/default_tolerance", methods=["POST"])
def set_default():
    if not is_admin():
        abort(403)
    data = request.get_json()
    val = data.get("value")
    if not isinstance(val, (int, float)) or val < 0:
        abort(400, "Invalid value")
    default = Tolerance.query.filter_by(currency="DEFAULT").first()
    if default:
        old = default.value
        default.value = val
        op = "UPDATE"
    else:
        old = None
        default = Tolerance(currency="DEFAULT", value=val)
        db.session.add(default)
        op = "CREATE"
    db.session.commit()
    log_audit("DEFAULT", old, val, op)
    return jsonify({"default_tolerance": val}), 200

# ----------------------------------------------------------------------
# API – Audit log query (read‑only)
# ----------------------------------------------------------------------
@app.route("/audit", methods=["GET"])
def audit_query():
    page = int(request.args.get("page", 1))
    size = int(request.args.get("size", 20))
    q = AuditLog.query.order_by(desc(AuditLog.timestamp))
    entries = q.paginate(page=page, per_page=size, error_out=False).items
    result = [
        {
            "currency": e.currency,
            "old_value": e.old_value,
            "new_value": e.new_value,
            "user_id": e.user_id,
            "timestamp": e.timestamp.isoformat(),
            "operation": e.operation,
        }
        for e in entries
    ]
    return jsonify(result)

# ----------------------------------------------------------------------
# API – Reconciliation break processing (suppression & alert)
# ----------------------------------------------------------------------
@app.route("/process_breaks", methods=["POST"])
def process_breaks():
    """
    Expected payload:
    [
        {
            "id": "break1",
            "currency": "USD",
            "qty_diff": 10.5,
            "mv_diff": 2500.0,
            "missing_position": false   # true if present in only one source
        },
        ...
    ]
    Returns list of breaks that survive suppression with an `alert` flag.
    """
    breaks = request.get_json()
    if not isinstance(breaks, list):
        abort(400, "Payload must be a list")
    output = []
    for b in breaks:
        cur = b.get("currency")
        qty = abs(b.get("qty_diff", 0))
        mv = abs(b.get("mv_diff", 0))
        missing = b.get("missing_position", False)

        # missing‑position breaks are never suppressed
        if missing:
            b["alert"] = True
            output.append(b)
            continue

        tol = get_tolerance(cur)
        if qty <= tol and mv <= tol:
            # suppressed – do not add to output
            continue
        # material break – include with alert flag
        b["alert"] = True
        output.append(b)
    return jsonify(output)

# ----------------------------------------------------------------------
# App bootstrap
# ----------------------------------------------------------------------
if __name__ == "__main__":
    # Ensure DB and tables exist
    if not os.path.exists("fx_tolerance.db"):
        db.create_all()
        # seed a default tolerance of 0.0 to avoid null look‑ups
        db.session.add(Tolerance(currency="DEFAULT", value=0.0))
        db.session.commit()
    app.run(host="0.0.0.0", port=5000, debug=True)
```

## Self-review
- **Scope adherence:** Implements per‑currency CRUD, default tolerance, immutable audit logging, and break‑suppression logic exactly as described; no UI or external integrations added.  
- **Security & validation:** Role check is simple but meets the requirement; tolerance values are validated to be non‑negative numeric via both model constraint and request validation.  
- **Immutability:** AuditLog has no update/delete endpoints, ensuring append‑only behavior; foreign‑key constraints are unnecessary given the simple design.  
- **Extensibility:** The tolerance lookup falls back to the `DEFAULT` record, making future extensions (e.g., per‑region overrides) straightforward.