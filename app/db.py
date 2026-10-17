"""Database access. SQLite locally; PostgreSQL (e.g. Neon) when DATABASE_URL is set.

Application SQL is written once in a portable subset (`?` placeholders,
`ON CONFLICT ... DO NOTHING/UPDATE`); `PgConnection` adapts it for psycopg
and mimics the bits of the sqlite3 API the app uses (rows by name and
index, `cursor.lastrowid`, `executescript`).
"""
import re
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
    "sync_years": "5",              # years of VZD sales history kept in sync automatically
}

# Tables with an `id SERIAL` key — Postgres INSERTs get `RETURNING id` for lastrowid.
ID_TABLES = {"properties", "tenants", "leases", "transactions", "documents", "valuations", "sales"}


def is_postgres(target: str) -> bool:
    return target.startswith(("postgres://", "postgresql://"))


def get_db():
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def connect(target):
    if is_postgres(target):
        return PgConnection(target)
    conn = sqlite3.connect(target)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def close_db(_exc=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(target):
    if not is_postgres(target):
        Path(target).parent.mkdir(parents=True, exist_ok=True)
    conn = connect(target)
    conn.executescript(SCHEMA.read_text())
    _migrate(conn)
    for k, v in DEFAULT_SETTINGS.items():
        conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO NOTHING", (k, v))
    conn.commit()
    conn.close()


# Columns added after the first release: (table, column, type). Applied to older databases.
MIGRATIONS = [
    ("properties", "expected_rent", "REAL"),
    ("sales", "lat", "REAL"),
    ("sales", "lon", "REAL"),
]


def _migrate(conn):
    for table, col, typ in MIGRATIONS:
        if isinstance(conn, PgConnection):
            conn.execute(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {col} {pg_type(typ)}")
            continue
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if col not in cols:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")


def settings(db=None):
    db = db or get_db()
    out = dict(DEFAULT_SETTINGS)
    out.update({r["key"]: r["value"] for r in db.execute("SELECT key, value FROM settings")})
    return out


def set_setting(db, key, value):
    db.execute("INSERT INTO settings (key, value) VALUES (?, ?) "
               "ON CONFLICT (key) DO UPDATE SET value = excluded.value", (key, value))


def setting_float(s, key, default=0.0):
    try:
        return float(s.get(key) or default)
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------------------- PostgreSQL adapter

def pg_type(t: str) -> str:
    return "DOUBLE PRECISION" if t.upper() == "REAL" else t


def pg_schema(sql: str) -> str:
    sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
    return re.sub(r"\bREAL\b", "DOUBLE PRECISION", sql)


def pg_sql(sql: str) -> str:
    return sql.replace("%", "%%").replace("?", "%s")


class Row(tuple):
    """Tuple that can also be indexed by column name, like sqlite3.Row."""

    def __new__(cls, names, values):
        r = super().__new__(cls, values)
        r._idx = names
        return r

    def __getitem__(self, k):
        if isinstance(k, str):
            return tuple.__getitem__(self, self._idx[k])
        return tuple.__getitem__(self, k)

    def keys(self):
        return list(self._idx)


def _row_factory(cursor):
    names = {c.name: i for i, c in enumerate(cursor.description or [])}
    return lambda values: Row(names, values)


class PgCursor:
    def __init__(self, cur, lastrowid=None, rows=None):
        self._cur, self.lastrowid, self._rows = cur, lastrowid, rows

    @property
    def rowcount(self):
        return self._cur.rowcount

    def fetchone(self):
        if self._rows is not None:
            return self._rows.pop(0) if self._rows else None
        return self._cur.fetchone() if self._cur.description else None

    def fetchall(self):
        if self._rows is not None:
            rows, self._rows = self._rows, []
            return rows
        return self._cur.fetchall() if self._cur.description else []

    def __iter__(self):
        return iter(self.fetchall())


class PgConnection:
    def __init__(self, url):
        import psycopg  # imported lazily: only needed when DATABASE_URL is set
        # prepare_threshold=None: safe behind PgBouncer-style poolers (Neon "-pooler" URLs).
        self._conn = psycopg.connect(url, row_factory=_row_factory, prepare_threshold=None)

    def execute(self, sql, params=()):
        cur = self._conn.cursor()
        m = re.match(r"\s*INSERT\s+INTO\s+(\w+)", sql, re.I)
        if m and m.group(1).lower() in ID_TABLES and "RETURNING" not in sql.upper():
            cur.execute(pg_sql(sql) + " RETURNING id", params)
            row = cur.fetchone()
            return PgCursor(cur, lastrowid=row[0] if row else None, rows=[])
        cur.execute(pg_sql(sql), params)
        return PgCursor(cur)

    def executemany(self, sql, seq):
        cur = self._conn.cursor()
        cur.executemany(pg_sql(sql), seq)
        return PgCursor(cur)

    def executescript(self, sql):
        self._conn.execute(pg_schema(sql))

    def commit(self):
        self._conn.commit()

    def rollback(self):
        self._conn.rollback()

    def close(self):
        self._conn.close()
