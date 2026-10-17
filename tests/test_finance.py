from datetime import date

import pytest

from app import finance as fin

S = {"rental_tax_rate": "10", "cgt_rate": "25.5", "selling_cost_pct": "3", "hurdle_rate": "6",
     "rent_growth": "2", "expense_growth": "3", "vacancy_pct": "5"}


def prop(**kw):
    base = {"id": 1, "purchase_date": "2020-01-01", "purchase_price": 50000, "purchase_costs": 1000,
            "declared_residence": 0, "loan_amount": 0, "loan_rate": 0, "loan_term_months": 0, "loan_start": None,
            "sale_date": None, "sale_price": None, "sale_costs": 0, "expected_rent": None}
    base.update(kw)
    return base


def tx(d, cat, amount, lease_id=None):
    return {"date": d, "kind": "income" if fin.group_of(cat) == "income" else "expense", "category": cat,
            "amount": amount, "lease_id": lease_id}


def test_annuity_payment_and_balance():
    pmt = fin.monthly_payment(100000, 6, 360)
    assert pmt == pytest.approx(599.55, abs=0.01)
    assert fin.loan_balance(100000, 6, 360, 0) == pytest.approx(100000)
    assert fin.loan_balance(100000, 6, 360, 360) == pytest.approx(0, abs=0.01)
    assert fin.loan_balance(100000, 0, 100, 50) == pytest.approx(50000)


def test_xirr_simple_doubling():
    r = fin.xirr([(date(2020, 1, 1), -1000), (date(2021, 1, 1), 1100)])
    assert r == pytest.approx(0.0997, abs=0.002)   # 2020 is a leap year (366 days)
    assert fin.xirr([(date(2020, 1, 1), 100)]) is None


def test_months_helpers():
    assert fin.add_months(date(2024, 1, 31), 1) == date(2024, 2, 29)
    assert fin.months_between(date(2020, 1, 15), date(2025, 1, 14)) == 59
    assert fin.months_between(date(2020, 1, 15), date(2025, 1, 15)) == 60
    assert fin.month_range(date(2024, 11, 3), date(2025, 2, 1)) == ["2024-11", "2024-12", "2025-01", "2025-02"]


def test_capital_gains_exemption():
    p = prop(declared_residence=1)
    assert fin.capital_gains_tax(10000, p, date(2024, 12, 31), S) == pytest.approx(2550)
    assert fin.capital_gains_tax(10000, p, date(2025, 1, 2), S) == 0
    assert fin.capital_gains_tax(-500, prop(), date(2030, 1, 1), S) == 0


def test_property_metrics_cash_purchase():
    today = date(2024, 1, 1)
    p = prop()
    lease = {"id": 7, "start_date": "2020-03-01", "end_date": None, "rent": 400}
    txs = [tx("2020-02-01", "Renovation – labour", 9000)]
    d = date(2020, 3, 5)
    while d < today:
        txs.append(tx(d.isoformat(), "Rent", 400, 7))
        txs.append(tx(d.isoformat(), "Building management", 50))
        d = fin.add_months(d, 1)
    m = fin.property_metrics(p, txs, [lease], 70000, "manual", S, today)
    assert m.cost_basis == 60000
    assert m.own_cash == 60000
    assert m.rent_monthly == 400
    assert m.gross_yield == pytest.approx(4800 / 60000)
    assert m.noi_12m == pytest.approx(12 * 350)
    assert m.net_yield == pytest.approx(4200 / 60000)
    assert m.occupancy_12m == 1
    # sell now: 70000 - 3% - CGT 25.5% of (67900 - 60000)
    cgt = (70000 * 0.97 - 60000) * 0.255
    assert m.extra["cgt"] == pytest.approx(cgt)
    assert m.total_return == pytest.approx(m.cash_flow_total + 70000 * 0.97 - cgt - 60000)
    assert m.irr is not None and 0.05 < m.irr < 0.15


def test_mortgage_total_return_counts_principal_as_equity():
    today = date(2024, 1, 1)
    p = prop(loan_amount=40000, loan_rate=5, loan_term_months=240, loan_start="2020-01-01")
    m = fin.property_metrics(p, [], [], 51000 / 0.97, "manual", S, today)
    # No rent, no appreciation beyond costs: loss should be roughly interest paid, not interest + principal
    assert m.total_return == pytest.approx(-m.interest_total, rel=0.02)


def test_rent_roll_detects_arrears():
    lease = {"id": 1, "start_date": "2024-01-01", "end_date": None, "rent": 500}
    txs = [tx("2024-01-03", "Rent", 500, 1), tx("2024-02-04", "Rent", 300, 1)]
    roll = fin.rent_roll([lease], txs, date(2024, 3, 10))
    due = {r["month"]: r["due"] for r in roll}
    assert due == {"2024-01": 0, "2024-02": 200, "2024-03": 500}


def test_sale_scenarios_and_recommendation():
    today = date(2024, 1, 1)
    p = prop()
    lease = {"id": 7, "start_date": "2020-03-01", "end_date": None, "rent": 400}
    txs = [tx("2023-06-01", "Rent", 400, 7)]
    m = fin.property_metrics(p, txs, [lease], 70000, "manual", S, today)
    rows = fin.sale_scenarios(p, m, txs, S, today, 0.03, horizon=10)
    assert len(rows) == 11
    assert rows[5]["value"] == pytest.approx(70000 * 1.03 ** 5)
    assert all(r["marginal"] is not None for r in rows[1:])
    rec = fin.recommendation(p, rows, S, today)
    assert rec["verdict"] in ("hold", "consider_selling")


def test_appreciation_from_index():
    idx = [(f"{2019 + i // 4}-Q{i % 4 + 1}", 100 * 1.01 ** i) for i in range(24)]
    assert fin.appreciation_from_index(idx, 5) == pytest.approx(1.01 ** 4 - 1, rel=1e-6)
