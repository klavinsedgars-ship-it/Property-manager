"""Fill an empty database with demo properties so you can explore the app.

    python scripts/seed_demo.py            # uses data/property.db (or PM_DATA_DIR)
"""
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app  # noqa: E402
from app import finance as fin  # noqa: E402
from app.db import get_db  # noqa: E402


def main():
    app = create_app()
    with app.app_context():
        db = get_db()
        if db.execute("SELECT count(*) FROM properties").fetchone()[0]:
            print("Database already has properties — not seeding.")
            return
        today = date.today()

        def prop(**kw):
            cols = ",".join(kw)
            return db.execute(f"INSERT INTO properties ({cols}) VALUES ({','.join('?' * len(kw))})",
                              list(kw.values())).lastrowid

        def tx(pid, d, cat, amount, desc=None, lease=None):
            kind = "income" if fin.group_of(cat) == "income" else "expense"
            db.execute("INSERT INTO transactions (property_id, date, kind, category, amount, description, lease_id)"
                       " VALUES (?,?,?,?,?,?,?)", (pid, d.isoformat(), kind, cat, amount, desc, lease))

        def tenant(name, phone):
            return db.execute("INSERT INTO tenants (name, phone) VALUES (?,?)", (name, phone)).lastrowid

        def lease(pid, tid, start, rent, deposit, end=None):
            return db.execute("INSERT INTO leases (property_id, tenant_id, start_date, end_date, rent, deposit)"
                              " VALUES (?,?,?,?,?,?)", (pid, tid, start.isoformat(),
                                                        end.isoformat() if end else None, rent, deposit)).lastrowid

        def monthly_running(pid, start, rent, lease_id, mgmt, util_bill):
            d = start
            while d <= today:
                if lease_id and rent:
                    tx(pid, date(d.year, d.month, min(5, 28)), "Rent", rent, f"Rent {fin.ym(d)}", lease_id)
                    tx(pid, date(d.year, d.month, 6), "Income tax on rent", round(rent * 0.10, 2))
                tx(pid, date(d.year, d.month, 15), "Building management", mgmt)
                if not lease_id:
                    tx(pid, date(d.year, d.month, 15), "Utilities", util_bill)
                d = fin.add_months(d, 1)

        # 1. Soviet-series flat, bought 2021, renovated, rented
        p1 = prop(name="Purvciems 2-room", address="Dzelzavas iela 10-25", city="Rīga", district="Purvciems",
                  kind="apartment", building_series="602", area_m2=51, rooms=2, floor="5/9", status="rented",
                  purchase_date=fin.add_months(today, -54).isoformat(), purchase_price=52000, purchase_costs=1100)
        rs = fin.add_months(today, -54)
        tx(p1, fin.add_months(rs, 1), "Renovation – materials", 6200, "Bathroom & kitchen materials")
        tx(p1, fin.add_months(rs, 2), "Renovation – labour", 5400, "Builder — full renovation")
        tx(p1, fin.add_months(rs, 2), "Furniture & appliances", 3100, "Kitchen appliances, beds, sofa")
        t1 = tenant("Jānis Bērziņš", "+371 20000001")
        l1 = lease(p1, t1, fin.add_months(rs, 3), 430, 430)
        monthly_running(p1, fin.add_months(rs, 3), 430, l1, 55, 0)
        for y in range(rs.year + 1, today.year + 1):
            tx(p1, date(y, 3, 31), "Property tax (NĪN)", 64)
            tx(p1, date(y, 4, 10), "Insurance", 45)
        tx(p1, fin.add_months(today, -8), "Repairs & maintenance", 180, "Boiler repair")

        # 2. New-project flat with mortgage, bought 2023
        p2 = prop(name="Skanstes 1-room", address="Skanstes iela 29-71", city="Rīga", district="Skanste",
                  kind="apartment", building_series="New project (post-2000)", area_m2=38, rooms=1, floor="7/12",
                  status="rented", purchase_date=fin.add_months(today, -30).isoformat(), purchase_price=98000,
                  purchase_costs=1800, loan_amount=68000, loan_rate=5.4, loan_term_months=360,
                  loan_start=fin.add_months(today, -30).isoformat())
        ps = fin.add_months(today, -30)
        tx(p2, fin.add_months(ps, 1), "Furniture & appliances", 4800, "Full furnishing")
        t2 = tenant("Anna Kalniņa", "+371 20000002")
        l2 = lease(p2, t2, fin.add_months(ps, 2), 620, 1240, end=fin.add_months(today, 1))
        monthly_running(p2, fin.add_months(ps, 2), 620, l2, 70, 0)

        # 3. Currently under renovation
        p3 = prop(name="Jūrmala Dubulti studio", address="Strēlnieku pr. 5-3", city="Jūrmala", district="Dubulti",
                  kind="apartment", building_series="Lithuanian project", area_m2=33, rooms=1, status="renovating",
                  purchase_date=fin.add_months(today, -3).isoformat(), purchase_price=41000, purchase_costs=900)
        r3 = fin.add_months(today, -3)
        tx(p3, fin.add_months(r3, 1), "Renovation – materials", 3900, "Flooring, tiles, paint")
        tx(p3, fin.add_months(r3, 2), "Renovation – labour", 2600, "Electrician + finishing")
        tx(p3, fin.add_months(r3, 2), "Utilities", 60)
        db.commit()
        print("Demo data created: 3 properties, 2 tenants.")


if __name__ == "__main__":
    main()
