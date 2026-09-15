# app.py
"""
Flask app implementing Story 1 – Configure Tolerances.
"""

import os
import sqlite3
from datetime import datetime
from flask import Flask, render_template_string, request, redirect, url_for, flash

app = Flask(__name__)
app.secret_key = 'dev-secret'  # For flash messages

DB_PATH = 'tolerances.db'
SUPPORTED_CURRENCIES = ['USD', 'EUR', 'JPY']
DEFAULT_TOLERANCE = 0.01  # 1% default

# ----------------------------------------------------------------------
# Database helpers
# ----------------------------------------------------------------------
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Create tables if they don't exist."""
    conn = get_db()
    conn.execute('''
        CREATE TABLE IF NOT EXISTS tolerances (
            currency TEXT PRIMARY KEY,
            value REAL NOT NULL,
            last_updated TIMESTAMP NOT NULL
        )
    ''')
    conn.commit()
    conn.close()

def get_tolerance(currency):
    """Return the stored tolerance or the default if missing."""
    conn = get_db()
    cur = conn.execute('SELECT value FROM tolerances WHERE currency = ?', (currency,))
    row = cur.fetchone()
    conn.close()
    return row['value'] if row else DEFAULT_TOLERANCE

def set_tolerance(currency, value):
    """Insert or replace tolerance for a currency."""
    conn = get_db()
    conn.execute('''
        INSERT INTO tolerances (currency, value, last_updated)
        VALUES (?, ?, ?)
        ON CONFLICT(currency) DO UPDATE SET
            value = excluded.value,
            last_updated = excluded.last_updated
    ''', (currency, value, datetime.utcnow()))
    conn.commit()
    conn.close()

# ----------------------------------------------------------------------
# Routes
# ----------------------------------------------------------------------
@app.route('/config', methods=['GET', 'POST'])
def config():
    if request.method == 'POST':
        # Process form submission
        for cur in SUPPORTED_CURRENCIES:
            val_str = request.form.get(cur)
            if val_str:
                try:
                    val = float(val_str)
                    set_tolerance(cur, val)
                except ValueError:
                    flash(f'Invalid value for {cur}', 'error')
        flash('Tolerances updated', 'success')
        return redirect(url_for('config'))

    # GET: display current tolerances
    tolerances = {c: get_tolerance(c) for c in SUPPORTED_CURRENCIES}
    return render_template_string(TEMPLATE, tolerances=tolerances, default=DEFAULT_TOLERANCE)

# ----------------------------------------------------------------------
# Minimal HTML template
# ----------------------------------------------------------------------
TEMPLATE = """
<!doctype html>
<title>FX Tolerance Configuration</title>
<h1>FX Tolerance Configuration</h1>
{% with messages = get_flashed_messages(with_categories=true) %}
  {% if messages %}
    <ul class=flashes>
    {% for category, msg in messages %}
      <li class="{{ category }}">{{ msg }}</li>
    {% endfor %}
    </ul>
  {% endif %}
{% endwith %}
<form method="post">
  <table border="1" cellpadding="5">
    <tr><th>Currency</th><th>Tolerance</th></tr>
    {% for cur, val in tolerances.items() %}
    <tr>
      <td>{{ cur }}</td>
      <td><input type="text" name="{{ cur }}" value="{{ val }}"></td>
    </tr>
    {% endfor %}
  </table>
  <p>Default tolerance (applied to any unspecified currency): {{ default }}</p>
  <p><input type="submit" value="Save"></p>
</form>
"""

# ----------------------------------------------------------------------
# Application entry point
# ----------------------------------------------------------------------
if __name__ == '__main__':
    if not os.path.exists(DB_PATH):
        init_db()
    app.run(debug=True)
