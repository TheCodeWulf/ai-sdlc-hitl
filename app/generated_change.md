**## Design note**  
Implement a minimal Flask service with SQLAlchemy models to store per‑currency FX tolerances, a single default tolerance, and an immutable audit log. Expose CRUD endpoints for tolerances and default, automatically log every change, and provide a simple alert‑generation helper that records material‑break alerts for later dashboard consumption.

**## Implementation**  
```python
# app.py
"""
Minimal Flask service implementing:
- per‑currency FX tolerance configuration
- default tolerance fallback
- immutable audit log of tolerance changes
- alert persistence for material breaks
"""

from datetime import datetime, timedelta
from flask import Flask, request, jsonify, abort
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import CheckConstraint, UniqueConstraint, func

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///fx_tolerance.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)


# ----------------------------------------------------------------------
# Models
# ----------------------------------------------------------------------
class CurrencyTolerance(db.Model):
    """Explicit tolerance for a given currency."""
    __tablename__ = "currency_tolerance"
    currency = db.Column(db.String(3), primary_key=True)  # ISO code
    tolerance = db.Column(db.Float, nullable=False)

    __table_args__ = (
        CheckConstraint("tolerance >= 0", name="ck_tolerance_non_negative"),
    )


class DefaultTolerance(db.Model):
    """Singleton row holding the system‑wide default tolerance."""
    __tablename__ = "default_tolerance"
    id = db.Column(db.Integer, primary_key=True)  # always 1
    tolerance = db.Column(db.Float, nullable=False)

    __table_args__ = (
        CheckConstraint("tolerance >= 0", name="ck_default_non_negative"),
    )


class AuditLog(db.Model):
    """Append‑only audit trail for tolerance changes."""
    __tablename__ = "audit_log"
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.String(64), nullable=False)
    timestamp_utc = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    operation = db.Column(db.String(10), nullable=False)  # CREATE, UPDATE, DELETE
    currency = db.Column(db.String(3), nullable=True)    # null for default changes
    old_value = db.Column(db.Float, nullable=True)
    new_value = db.Column(db.Float, nullable=True)


class Alert(db.Model):
    """Persisted alerts for material FX breaks."""
    __tablename__ = "alert"
    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    currency = db.Column(db.String(3), nullable=False)
    break_amount = db.Column(db.Float, nullable=False)
    tolerance = db.Column(db.Float, nullable=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_alert_currency_created", "currency", "created_at"),
    )


# ----------------------------------------------------------------------
# Helper utilities
# ----------------------------------------------------------------------
def get_effective_tolerance(currency: str) -> float:
    """Return the configured tolerance for a currency, falling back to default."""
    ct = CurrencyTolerance.query.filter_by(currency=currency).first()
    if ct:
        return ct.tolerance
    default = DefaultTolerance.query.get(1)
    return default.tolerance if default else 0.0


def log_audit(user_id: str, operation: str, currency: str | None,
              old: float | None, new: float | None):
    entry = AuditLog(
        user_id=user_id,
        operation=operation,
        currency=currency,
        old_value=old,
        new_value=new,
    )
    db.session.add(entry)
    db.session.commit()


def create_alert(currency: str, break_amount: float, tolerance: float):
    """Persist an alert when a break exceeds its tolerance."""
    alert = Alert(currency=currency, break_amount=break_amount, tolerance=tolerance)
    db.session.add(alert)
    db.session.commit()


# ----------------------------------------------------------------------
# API Endpoints – Tolerances
# ----------------------------------------------------------------------
@app.route("/tolerances/<currency>", methods=["GET"])
def get_tolerance(currency):
    ct = CurrencyTolerance.query.get(currency.upper())
    if not ct:
        abort(404, description="Currency tolerance not found")
    return jsonify({"currency": ct.currency, "tolerance": ct.tolerance})


@app.route("/tolerances/<currency>", methods=["POST"])
def create_tolerance(currency):
    data = request.get_json()
    tol = data.get("tolerance")
    if not isinstance(tol, (int, float)) or tol < 0:
        abort(400, description="Tolerance must be a non‑negative number")
    currency = currency.upper()
    if CurrencyTolerance.query.get(currency):
        abort(409, description="Tolerance already exists for this currency")
    ct = CurrencyTolerance(currency=currency, tolerance=tol)
    db.session.add(ct)
    db.session.commit()
    log_audit(user_id=data.get("user_id", "system"),
              operation="CREATE",
              currency=currency,
              old=None,
              new=tol)
    return jsonify({"message": "Created"}), 201


@app.route("/tolerances/<currency>", methods=["PUT"])
def update_tolerance(currency):
    data = request.get_json()
    new_tol = data.get("tolerance")
    if not isinstance(new_tol, (int, float)) or new_tol < 0:
        abort(400, description="Tolerance must be a non‑negative number")
    currency = currency.upper()
    ct = CurrencyTolerance.query.get(currency)
    if not ct:
        abort(404, description="Currency tolerance not found")
    old = ct.tolerance
    ct.tolerance = new_tol
    db.session.commit()
    log_audit(user_id=data.get("user_id", "system"),
              operation="UPDATE",
              currency=currency,
              old=old,
              new=new_tol)
    return jsonify({"message": "Updated"})


@app.route("/tolerances/<currency>", methods=["DELETE"])
def delete_tolerance(currency):
    data = request.get_json(silent=True) or {}
    currency = currency.upper()
    ct = CurrencyTolerance.query.get(currency)
    if not ct:
        abort(404, description="Currency tolerance not found")
    old = ct.tolerance
    db.session.delete(ct)
    db.session.commit()
    log_audit(user_id=data.get("user_id", "system"),
              operation="DELETE",
              currency=currency,
              old=old,
              new=None)
    return jsonify({"message": "Deleted"})


# ----------------------------------------------------------------------
# API Endpoints – Default tolerance
# ----------------------------------------------------------------------
@app.route("/default", methods=["GET"])
def get_default():
    default = DefaultTolerance.query.get(1)
    if not default:
        abort(404, description="Default tolerance not configured")
    return jsonify({"default_tolerance": default.tolerance})


@app.route("/default", methods=["PUT"])
def set_default():
    data = request.get_json()
    new_tol = data.get("tolerance")
    if not isinstance(new_tol, (int, float)) or new_tol < 0:
        abort(400, description="Tolerance must be a non‑negative number")
    default = DefaultTolerance.query.get(1)
    old = default.tolerance if default else None
    if not default:
        default = DefaultTolerance(id=1, tolerance=new_tol)
        db.session.add(default)
    else:
        default.tolerance = new_tol
    db.session.commit()
    log_audit(user_id=data.get("user_id", "system"),
              operation="UPDATE" if old is not None else "CREATE",
              currency=None,
              old=old,
              new=new_tol)
    return jsonify({"message": "Default tolerance set"})


# ----------------------------------------------------------------------
# API Endpoints – Audit log
# ----------------------------------------------------------------------
@app.route("/audit", methods=["GET"])
def get_audit():
    """Optional query params: user_id, currency, start, end (ISO dates)."""
    q = AuditLog.query
    user = request.args.get("user_id")
    cur = request.args.get("currency")
    start = request.args.get("start")
    end = request.args.get("end")
    if user:
        q = q.filter_by(user_id=user)
    if cur:
        q = q.filter_by(currency=cur.upper())
    if start:
        q = q.filter(AuditLog.timestamp_utc >= datetime.fromisoformat(start))
    if end:
        q = q.filter(AuditLog.timestamp_utc <= datetime.fromisoformat(end))
    entries = q.order_by(AuditLog.timestamp_utc.desc()).all()
    result = [
        {
            "id": e.id,
            "user_id": e.user_id,
            "timestamp_utc": e.timestamp_utc.isoformat(),
            "operation": e.operation,
            "currency": e.currency,
            "old_value": e.old_value,
            "new_value": e.new_value,
        }
        for e in entries
    ]
    return jsonify(result)


# ----------------------------------------------------------------------
# API Endpoints – Alerts (read‑only)
# ----------------------------------------------------------------------
@app.route("/alerts", methods=["GET"])
def get_alerts():
    """Filters: currency, start, end – returns alerts from last 30 days by default."""
    q = Alert.query
    cur = request.args.get("currency")
    start = request.args.get("start")
    end = request.args.get("end")
    if cur:
        q = q.filter_by(currency=cur.upper())
    now = datetime.utcnow()
    default_start = now - timedelta(days=30)
    if start:
        q = q.filter(Alert.created_at >= datetime.fromisoformat(start))
    else:
        q = q.filter(Alert.created_at >= default_start)
    if end:
        q = q.filter(Alert.created_at <= datetime.fromisoformat(end))
    alerts = q.order_by(Alert.created_at.desc()).all()
    return jsonify(
        [
            {
                "id": a.id,
                "currency": a.currency,
                "break_amount": a.break_amount,
                "tolerance": a.tolerance,
                "created_at": a.created_at.isoformat(),
            }
            for a in alerts
        ]
    )


# ----------------------------------------------------------------------
# Example break‑processing hook (to be called by the reconciliation engine)
# ----------------------------------------------------------------------
def process_break(currency: str, break_amount: float, user_id: str = "system"):
    """
    Evaluate a break; if it exceeds tolerance, persist an alert.
    Returns True if the break should be shown in the queue, False otherwise.
    """
    tol = get_effective_tolerance(currency)
    if abs(break_amount) > tol:
        create_alert(currency.upper(), break_amount, tol)
        return True  # material break – keep in queue
    return False  # suppressed


# ----------------------------------------------------------------------
# Application bootstrap
# ----------------------------------------------------------------------
if __name__ == "__main__":
    # Create tables if they don't exist
    with app.app_context():
        db.create_all()
        # Ensure a default tolerance row exists (e.g., 0.0)
        if not DefaultTolerance.query.get(1):
            db.session.add(DefaultTolerance(id=1, tolerance=0.0))
            db.session.commit()
    app.run(debug=True)
```

**## Self-review**  
- **Scope adherence:** Implements only the core persistence, audit logging, default fallback, and alert storage required by the four stories; no UI or role‑based permission system beyond a simple `user_id` field.  
- **Data integrity:** Uses SQL `CHECK` constraints to forbid negative tolerances and an immutable `AuditLog` (no update/delete routes).  
- **Extensibility:** `process_break` can be invoked by any external reconciliation component, keeping the service focused on configuration and alert persistence.