import io
import json

from app import finance as fin
from app import portfolio

DATA = {"version": 1, "properties": [
    {"name": "Bruņinieku 28", "kind": "basement", "purchase_date": "2024-09-01", "purchase_price": 10000,
     "purchase_costs": 1050, "transactions": [
        {"date": "2025-03-01", "category": "Renovation – materials", "amount": 3100, "description": "Estrich"},
        {"date": "2025-07-15", "category": "Sale of part / unit", "amount": 4000, "description": "Noliktava Nr.2"}]},
    {"name": "Brīvības 95-23A", "purchase_date": "2022-08-01", "purchase_price": 62471, "transactions": [
        {"date": "2022-11-01", "category": "Rent", "amount": 520}]},
]}


def test_import_is_idempotent_and_keeps_manual_entries(db):
    portfolio.import_data(db, DATA)
    pid = db.execute("SELECT id FROM properties WHERE name='Bruņinieku 28'").fetchone()[0]
    db.execute("INSERT INTO transactions (property_id, date, kind, category, amount) VALUES (?,?,?,?,?)",
               (pid, "2025-09-01", "expense", "Utilities", 30))
    db.commit()
    report = portfolio.import_data(db, DATA)
    assert "replaced 2" in report[0]
    assert db.execute("SELECT count(*) FROM properties").fetchone()[0] == 2
    assert db.execute("SELECT count(*) FROM transactions WHERE property_id=?", (pid,)).fetchone()[0] == 3
    sale = db.execute("SELECT kind FROM transactions WHERE category='Sale of part / unit'").fetchone()[0]
    assert sale == "income"


def test_partial_sale_counts_as_cash_not_rent():
    from datetime import date
    p = {"purchase_date": "2024-09-01", "purchase_price": 10000, "purchase_costs": 0, "declared_residence": 0,
         "loan_amount": 0, "loan_rate": 0, "loan_term_months": 0, "loan_start": None, "sale_date": None,
         "sale_price": None, "sale_costs": 0, "expected_rent": None}
    txs = [{"date": "2025-07-15", "kind": "income", "category": "Sale of part / unit", "amount": 4000, "lease_id": None}]
    s = {"selling_cost_pct": "0", "cgt_rate": "25.5"}
    m = fin.property_metrics(p, txs, [], 8000, "manual", s, date(2025, 12, 1))
    assert m.income_12m == 0 and m.gross_yield is None          # not rental income
    assert m.cash_flow_total == 4000 and m.irr is not None
    row = [r for r in fin.monthly_table(txs, None, date(2025, 7, 1), date(2025, 7, 31))][0]
    assert row["sale"] == 4000 and row["cash_flow"] == 4000


def test_export_roundtrip(client, app):
    with app.app_context():
        from app.db import get_db
        portfolio.import_data(get_db(), DATA)
    exported = client.get("/data/export.json").get_json()
    assert len(exported["properties"]) == 2
    assert exported["properties"][0]["transactions"][0]["amount"] == 3100
    r = client.post("/data/import", data={"file": (io.BytesIO(json.dumps(exported).encode()), "p.json")},
                    content_type="multipart/form-data")
    assert r.status_code == 302


def test_rejects_wrong_file(db):
    import pytest
    with pytest.raises(ValueError):
        portfolio.import_data(db, {"foo": 1})
