"""Whole-portfolio export / import as JSON (backup, moving data, bulk loading).

Format:
{"version": 1, "properties": [{<property fields>, "transactions": [...], "valuations": [...],
                               "leases": [{..., "tenant": {"name", "phone", "email"}}]}]}

Import matches properties by name. An existing property is updated with the
fields given, and its previously *imported* entries (bulk CSV or earlier JSON
imports) are replaced; entries typed in by hand are kept. Re-running the same
import is therefore safe.
"""
from __future__ import annotations

import uuid
from datetime import date

from . import finance as fin
from .vzd import norm

PROPERTY_FIELDS = ["name", "address", "city", "district", "kind", "building_series", "area_m2", "rooms", "floor",
                   "cadastre_no", "status", "expected_rent", "purchase_date", "purchase_price", "purchase_costs",
                   "declared_residence", "loan_amount", "loan_rate", "loan_term_months", "loan_start",
                   "sale_date", "sale_price", "sale_costs", "notes"]
TX_FIELDS = ["date", "kind", "category", "amount", "description", "vendor"]


def export(db) -> dict:
    out = []
    for p in db.execute("SELECT * FROM properties ORDER BY id").fetchall():
        item = {k: p[k] for k in PROPERTY_FIELDS}
        item["transactions"] = [{k: t[k] for k in TX_FIELDS} for t in
                                db.execute("SELECT * FROM transactions WHERE property_id=? ORDER BY date, id", (p["id"],))]
        item["valuations"] = [{k: v[k] for k in ("date", "value", "source", "notes")} for v in
                              db.execute("SELECT * FROM valuations WHERE property_id=? ORDER BY date", (p["id"],))]
        item["leases"] = [{"start_date": l["start_date"], "end_date": l["end_date"], "rent": l["rent"],
                           "deposit": l["deposit"], "payment_day": l["payment_day"], "notes": l["notes"],
                           "tenant": {"name": l["name"], "phone": l["phone"], "email": l["email"]}} for l in
                          db.execute("""SELECT l.*, t.name, t.phone, t.email FROM leases l JOIN tenants t
                                        ON t.id = l.tenant_id WHERE l.property_id=?""", (p["id"],))]
        out.append(item)
    return {"version": 1, "exported_at": date.today().isoformat(), "properties": out}


def import_data(db, data: dict) -> list[str]:
    if not isinstance(data, dict) or not isinstance(data.get("properties"), list):
        raise ValueError("Not a portfolio file (expected {\"properties\": [...]})")
    batch = f"data-{date.today().isoformat()}-{uuid.uuid4().hex[:6]}"
    existing = {norm(r["name"]): r["id"] for r in db.execute("SELECT id, name FROM properties")}
    report = []
    for item in data["properties"]:
        name = (item.get("name") or "").strip()
        if not name:
            continue
        fields = {k: item[k] for k in PROPERTY_FIELDS if k in item}
        pid = existing.get(norm(name))
        if pid:
            if fields:
                db.execute(f"UPDATE properties SET {', '.join(k + '=?' for k in fields)} WHERE id=?",
                           [*fields.values(), pid])
            removed = db.execute("DELETE FROM transactions WHERE property_id=? AND batch IS NOT NULL",
                                 (pid,)).rowcount
            action = f"updated (replaced {removed} previously imported entries)" if removed else "updated"
        else:
            cols = list(fields)
            pid = db.execute(f"INSERT INTO properties ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
                             [fields[c] for c in cols]).lastrowid
            existing[norm(name)] = pid
            action = "created"
        n, total = 0, 0.0
        for t in item.get("transactions", []):
            if not t.get("amount") or not t.get("date"):
                continue
            category = t.get("category") or "Other expense"
            kind = t.get("kind") or ("income" if fin.is_inflow(category) else "expense")
            db.execute("""INSERT INTO transactions (property_id, date, kind, category, amount, description, vendor,
                          batch) VALUES (?,?,?,?,?,?,?,?)""",
                       (pid, t["date"], kind, category, abs(float(t["amount"])), t.get("description"),
                        t.get("vendor"), batch))
            n += 1
            total += float(t["amount"]) * (1 if kind == "income" else -1)
        for v in item.get("valuations", []):
            if not db.execute("SELECT 1 FROM valuations WHERE property_id=? AND date=? AND value=?",
                              (pid, v["date"], v["value"])).fetchone():
                db.execute("INSERT INTO valuations (property_id, date, value, source, notes) VALUES (?,?,?,?,?)",
                           (pid, v["date"], v["value"], v.get("source") or "import", v.get("notes")))
        for lease in item.get("leases", []):
            tenant = lease.get("tenant") or {}
            if not tenant.get("name") or db.execute(
                    "SELECT 1 FROM leases WHERE property_id=? AND start_date=?", (pid, lease["start_date"])).fetchone():
                continue
            tid = db.execute("INSERT INTO tenants (name, phone, email) VALUES (?,?,?)",
                             (tenant["name"], tenant.get("phone"), tenant.get("email"))).lastrowid
            db.execute("""INSERT INTO leases (property_id, tenant_id, start_date, end_date, rent, deposit,
                          payment_day, notes) VALUES (?,?,?,?,?,?,?,?)""",
                       (pid, tid, lease["start_date"], lease.get("end_date"), lease.get("rent") or 0,
                        lease.get("deposit") or 0, lease.get("payment_day") or 1, lease.get("notes")))
        report.append(f"{name}: {action}, {n} entries")
    db.commit()
    return report
