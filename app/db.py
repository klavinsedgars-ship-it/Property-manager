import sqlite3
from pathlib import Path

from flask import current_app, g

SCHEMA = Path(__file__).with_name("schema.sql")

# Defaults are Latvia-oriented; all editable on the Settings page.
DEFAULT_SETTINGS = {
    "rental_tax_rate": "10",        # % IIN on gross rent (registered rental, non-business)
    "cgt_rate": "25.5",             # % IIN on capital gain (25.5% from 2025; 20% for older deals)
    "selling_cost_pct": "3",        # % agent + notary on sale
    "hurdle_rate": "6",             # % alternative return used for sell/hold advice
    "default_appreciation": "",     # % / year; blank = derive from CSP house price index
    "rent_growth": "2",             # % / year
    "expense_growth": "3",          # % / year
    "vacancy_pct": "5",             # % of gross rent lost to vacancy
    "currency": "EUR",
}


def get_db():
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def connect(path):
    conn = sqlite3.connect(path, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(path)
    conn.executescript(SCHEMA.read_text())
    _migrate(conn)
    for k, v in DEFAULT_SETTINGS.items():
        conn.execute("INSERT OR IGNORE INTO settings(key, value) VALUES (?, ?)", (k, v))
    conn.commit()
    conn.close()


# Columns added after the first release: (table, column, type). Applied to older databases.
MIGRATIONS = [
    ("properties", "expected_rent", "REAL"),
]


def _migrate(conn):
    for table, col, typ in MIGRATIONS:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")


def settings(db=None):
    db = db or get_db()
    out = dict(DEFAULT_SETTINGS)
    out.update({r["key"]: r["value"] for r in db.execute("SELECT key, value FROM settings")})
    return out


def setting_float(s, key, default=0.0):
    try:
        return float(s.get(key) or default)
    except (TypeError, ValueError):
        return default
