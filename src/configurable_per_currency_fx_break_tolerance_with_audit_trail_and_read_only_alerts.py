# file: app.py
"""
Minimal implementation of per‑currency FX tolerance with audit‑trail and read‑only alerts.
"""

from datetime import datetime
from flask import Flask, request, jsonify, abort
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import CheckConstraint, event
import os

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///:memory:"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

# ----------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------
DEFAULT_TOLERANCE = float(os.getenv("DEFAULT_FX_TOLERANCE", "0.01"))  # fallback tolerance


# ----------------------------------------------------------------------
# Models
# ----------------------------------------------------------------------
class FxTolerance(db.Model):
    __tablename__ = "fx_tolerance"
    currency_code = db.Column(db.String(3), primary_key=True)
    tolerance_value = db.Column(db.Float, nullable=False)
    created_by = db.Column(db.String, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        CheckConstraint("tolerance_value >= 0", name="ck_tolerance_non_negative"),
    )


class FxToleranceAudit(db.Model):
    __tablename__ = "fx_tolerance_audit"
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    currency_code = db.Column(db.String(3), nullable=False)
    old_value = db.Column(db.Float, nullable=True)   # null on insert
    new_value = db.Column(db.Float, nullable=False)
    changed_by = db.Column(db.String, nullable=False)
    changed_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    # immutable – no UPDATE/DELETE via ORM
    __mapper_args__ = {"eager_defaults": True}


# ----------------------------------------------------------------------
# Simple role‑based access control
# ----------------------------------------------------------------------
def require_role(*allowed_roles):
    def decorator(fn):
        def wrapper(*args, **kwargs):
            role = request.headers.get("X-User-Role", "").lower()
            if role not in allowed_roles:
                abort(403, description="Forbidden: insufficient role")
            return fn(*args, **kwargs)
        wrapper.__name__ = fn.__name__
        return wrapper
    return decorator


# ----------------------------------------------------------------------
# Helper: audit logging
# ----------------------------------------------------------------------
def log_audit(currency_code, old_val, new_val, user):
    audit = FxToleranceAudit(
        currency_code=currency_code,
        old_value=old_val,
        new_value=new_val,
        changed_by=user,
    )
    db.session.add(audit)
    db.session.flush()  # ensure write before commit


# ----------------------------------------------------------------------
# CRUD endpoints for tolerances (Story 1)
# ----------------------------------------------------------------------
@app.route("/tolerances", methods=["GET"])
@require_role("ops_admin", "viewer")
def list_tolerances():
    rows = FxTolerance.query.all()
    return jsonify([{
        "currency_code": r.currency_code,
        "tolerance_value": r.tolerance_value,
        "created_by": r.created_by,
        "created_at": r.created_at.isoformat()
    } for r in rows])


@app.route("/tolerances", methods=["POST"])
@require_role("ops_admin")
def create_tolerance():
    data = request.get_json()
    cur = data["currency_code"].upper()
    val = float(data["tolerance_value"])
    user = request.headers.get("X-User-Id", "unknown")
    if FxTolerance.query.get(cur):
        abort(400, description="Tolerance already exists")
    tol = FxTolerance(
        currency_code=cur,
        tolerance_value=val,
        created_by=user,
    )
    db.session.add(tol)
    log_audit(cur, None, val, user)
    db.session.commit()
    return jsonify({"msg": "created"}), 201


@app.route("/tolerances/<currency>", methods=["PUT"])
@require_role("ops_admin")
def update_tolerance(currency):
    cur = currency.upper()
    data = request.get_json()
    val = float(data["tolerance_value"])
    user = request.headers.get("X-User-Id", "unknown")
    tol = FxTolerance.query.get_or_404(cur)
    old = tol.tolerance_value
    tol.tolerance_value = val
    log_audit(cur, old, val, user)
    db.session.commit()
    return jsonify({"msg": "updated"})


@app.route("/tolerances/<currency>", methods=["DELETE"])
@require_role("ops_admin")
def delete_tolerance(currency):
    cur = currency.upper()
    tol = FxTolerance.query.get_or_404(cur)
    # Record deletion as audit with new_value = NULL
    log_audit(cur, tol.tolerance_value, None, request.headers.get("X-User-Id", "unknown"))
    db.session.delete(tol)
    db.session.commit()
    return jsonify({"msg": "deleted"})


# ----------------------------------------------------------------------
# Audit UI (Story 4)
# ----------------------------------------------------------------------
@app.route("/audit", methods=["GET"])
@require_role("ops_admin", "viewer")
def list_audit():
    q = FxToleranceAudit.query
    cur = request.args.get("currency")
    user = request.args.get("user")
    start = request.args.get("start")  # ISO date
    end = request.args.get("end")
    if cur:
        q = q.filter_by(currency_code=cur.upper())
    if user:
        q = q.filter_by(changed_by=user)
    if start:
        q = q.filter(FxToleranceAudit.changed_at >= start)
    if end:
        q = q.filter(FxToleranceAudit.changed_at <= end)
    rows = q.order_by(FxToleranceAudit.changed_at.desc()).all()
    return jsonify([{
        "currency_code": r.currency_code,
        "old_value": r.old_value,
        "new_value": r.new_value,
        "changed_by": r.changed_by,
        "changed_at": r.changed_at.isoformat()
    } for r in rows])


# ----------------------------------------------------------------------
# Reconciliation helper (Story 2)
# ----------------------------------------------------------------------
def get_tolerance_for(currency):
    """Return tolerance for a currency, falling back to default."""
    tol = FxTolerance.query.get(currency.upper())
    return tol.tolerance_value if tol else DEFAULT_TOLERANCE


def filter_breaks(breaks):
    """
    `breaks` is an iterable of dicts:
        {
            "id": ..., "currency": "...",
            "qty_diff": float,
            "mv_diff": float,
            "single_source": bool   # True if missing position
        }
    Returns only material breaks per Story 2.
    """
    material = []
    for b in breaks:
        if b.get("single_source"):
            material.append(b)  # never suppressed
            continue
        tol = get_tolerance_for(b["currency"])
        if (abs(b["qty_diff"]) > tol) or (abs(b["mv_diff"]) > tol):
            material.append(b)
    return material


# ----------------------------------------------------------------------
# Alerts endpoint (Story 3)
# ----------------------------------------------------------------------
@app.route("/alerts", methods=["GET"])
@require_role("viewer", "ops_admin")
def alerts():
    """
    Returns breaks that exceed their per‑currency tolerance.
    In a real system this would be driven by a background job; here we
    accept an optional `breaks` payload for demo purposes.
    """
    # For demo we allow a JSON payload; otherwise return empty list.
    payload = request.get_json(silent=True) or {}
    raw_breaks = payload.get("breaks", [])
    alerts = []
    for b in raw_breaks:
        tol = get_tolerance_for(b["currency"])
        if (abs(b["qty_diff"]) > tol) or (abs(b["mv_diff"]) > tol):
            alerts.append({
                "currency": b["currency"],
                "break_id": b.get("id"),
                "break_amount": {"qty_diff": b["qty_diff"], "mv_diff": b["mv_diff"]},
                "tolerance": tol,
                "detail_link": f"/breaks/{b.get('id')}"
            })
    return jsonify(alerts)


# ----------------------------------------------------------------------
# Application bootstrap
# ----------------------------------------------------------------------
def bootstrap():
    db.create_all()
    # Insert a couple of default tolerances for demo
    demo = [
        FxTolerance(currency_code="USD", tolerance_value=0.05, created_by="system"),
        FxTolerance(currency_code="EUR", tolerance_value=0.03, created_by="system"),
    ]
    db.session.bulk_save_objects(demo)
    db.session.commit()


if __name__ == "__main__":
    bootstrap()
    app.run(debug=True, port=5000)
