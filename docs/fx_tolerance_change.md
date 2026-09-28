## Design note
- Store per‑currency tolerances and a single default tolerance in a JSON file; updates are written atomically to keep the config consistent.  
- Record every create/update of a tolerance as an append‑only line in a CSV audit log (user, timestamp, currency, old, new) to guarantee immutability.  
- Provide a small API (`set_tolerance`, `set_default`, `evaluate_break`) that the rest of the system (e.g., dashboard) can call to hide in‑tolerance breaks and raise read‑only alerts for out‑of‑tolerance ones.

## Implementation
```python
# fx_tolerance.py
"""
Minimal implementation of per‑currency FX break tolerance management with
audit‑able change logging and evaluation helpers.
"""

import json
import csv
import os
import threading
from datetime import datetime
from typing import Optional, Dict, Tuple

# ---------------------------------------------------------------------------
# Configuration persistence
# ---------------------------------------------------------------------------
_CONFIG_PATH = "tolerance_config.json"
_AUDIT_PATH = "tolerance_audit.log"
_LOCK = threading.Lock()


def _load_config() -> Dict:
    """Load the tolerance configuration from disk; create defaults if missing."""
    if not os.path.exists(_CONFIG_PATH):
        # initialise with empty per‑currency map and a default tolerance of 0.0
        cfg = {"default": 0.0, "currencies": {}}
        _save_config(cfg)
        return cfg
    with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def _save_config(cfg: Dict) -> None:
    """Write the whole config atomically."""
    tmp = _CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2, sort_keys=True)
    os.replace(tmp, _CONFIG_PATH)


def _append_audit(entry: Tuple[str, str, str, Optional[float], float]) -> None:
    """Append a single audit record; never modify existing rows."""
    # entry = (user_id, timestamp, currency, old_value, new_value)
    with open(_AUDIT_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(entry)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def set_default_tolerance(user_id: str, value: float) -> None:
    """
    Set the system‑wide default tolerance.
    Raises ValueError if value is negative.
    """
    if value < 0:
        raise ValueError("Tolerance cannot be negative")
    with _LOCK:
        cfg = _load_config()
        old = cfg["default"]
        cfg["default"] = value
        _save_config(cfg)
        _append_audit((user_id, datetime.utcnow().isoformat(), "DEFAULT", old, value))


def set_currency_tolerance(user_id: str, currency: str, value: float) -> None:
    """
    Create or update a tolerance for a specific ISO currency code.
    Raises ValueError for invalid inputs.
    """
    if not currency or len(currency) != 3:
        raise ValueError("Currency must be a 3‑letter ISO code")
    if value < 0:
        raise ValueError("Tolerance cannot be negative")
    currency = currency.upper()
    with _LOCK:
        cfg = _load_config()
        old = cfg["currencies"].get(currency)
        cfg["currencies"][currency] = value
        _save_config(cfg)
        _append_audit((user_id, datetime.utcnow().isoformat(), currency, old, value))


def get_tolerance(currency: str) -> float:
    """
    Return the tolerance for the given currency, falling back to the default.
    """
    cfg = _load_config()
    return cfg["currencies"].get(currency.upper(), cfg["default"])


def evaluate_break(currency: str, break_amount: float) -> Tuple[bool, float]:
    """
    Determine whether a break is within tolerance.

    Returns:
        (in_tolerance, tolerance_used)
    """
    tol = get_tolerance(currency)
    in_tol = abs(break_amount) <= tol
    return in_tol, tol


def list_audit_log() -> list:
    """
    Read the audit log and return a list of dicts.
    (Read‑only; the file is never mutated by this module.)
    """
    if not os.path.exists(_AUDIT_PATH):
        return []
    with open(_AUDIT_PATH, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f, fieldnames=["user_id", "timestamp", "currency", "old", "new"])
        return [row for row in reader]


# ---------------------------------------------------------------------------
# Example helper for dashboard integration (not part of core logic)
# ---------------------------------------------------------------------------
def filter_breaks(breaks: list) -> Tuple[list, list]:
    """
    Given a list of dicts with keys: 'currency' and 'amount',
    return (in_tolerance_breaks, out_of_tolerance_alerts).

    Alerts are read‑only dicts containing currency, amount, and tolerance.
    """
    in_tol, out_of = [], []
    for br in breaks:
        ok, tol = evaluate_break(br["currency"], br["amount"])
        if ok:
            in_tol.append(br)
        else:
            alert = {
                "currency": br["currency"],
                "break_amount": br["amount"],
                "tolerance": tol,
                "read_only": True,
            }
            out_of.append(alert)
    return in_tol, out_of
```

## Self-review
- **Correctness:** Functions enforce non‑negative tolerances, ISO‑code validation, and proper fallback to the default tolerance, satisfying stories 1‑3.  
- **Audit immutability:** Audit entries are appended to a CSV file only; no code path modifies or deletes existing rows, meeting story 4.  
- **Thread safety & atomicity:** A module‑level lock and atomic file replace guarantee consistent config updates even under concurrent calls.  
- **Scope adherence:** No changes to valuation engines or role‑based access control are introduced; the implementation stays minimal and focused on the primary backlog.