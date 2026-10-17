"""Glue between DB rows and finance calculations."""
from datetime import date

from . import finance as fin
from . import market
from .db import get_db, settings


def load_property(pid):
    db = get_db()
    p = db.execute("SELECT * FROM properties WHERE id = ?", (pid,)).fetchone()
    if not p:
        return None
    txs = db.execute("SELECT * FROM transactions WHERE property_id = ? ORDER BY date DESC, id DESC",
                     (pid,)).fetchall()
    leases = db.execute("""SELECT l.*, t.name AS tenant_name, t.phone, t.email FROM leases l
                           JOIN tenants t ON t.id = l.tenant_id WHERE l.property_id = ?
                           ORDER BY l.start_date DESC""", (pid,)).fetchall()
    vals = db.execute("SELECT * FROM valuations WHERE property_id = ? ORDER BY date DESC", (pid,)).fetchall()
    return {"p": p, "txs": txs, "leases": leases, "valuations": vals}


def current_value(bundle, capex, today):
    """Best available value: latest valuation, else market-index estimate, else cost."""
    p = bundle["p"]
    if bundle["valuations"]:
        v = bundle["valuations"][0]
        return float(v["value"]), f"{v['source']} {v['date']}"
    est = market.index_estimate(get_db(), p, capex)
    if est:
        return est
    cost = float(p["purchase_price"] or 0) + capex
    return cost, "cost (no valuation yet)"


def compute(bundle, today=None):
    today = today or date.today()
    s = settings()
    capex = sum(float(t["amount"]) for t in bundle["txs"] if fin.group_of(t["category"], t["kind"]) == "capex")
    value, src = current_value(bundle, capex, today)
    return fin.property_metrics(bundle["p"], bundle["txs"], bundle["leases"], value, src, s, today)


def all_properties(include_sold=True):
    db = get_db()
    q = "SELECT id FROM properties" + ("" if include_sold else " WHERE status != 'sold'") + " ORDER BY name"
    out = []
    for r in db.execute(q).fetchall():
        b = load_property(r["id"])
        b["m"] = compute(b)
        out.append(b)
    return out


def portfolio_totals(bundles):
    keys = ["cost_basis", "own_cash", "value", "loan_balance", "equity", "income_total", "opex_total",
            "cash_flow_total", "rent_monthly", "income_12m", "opex_12m", "noi_12m", "debt_service_12m",
            "unrealized_gain", "total_return", "capex"]
    active = [b for b in bundles if not b["m"].sold]
    t = {k: sum(getattr(b["m"], k) for b in active) for k in keys}
    t["count"] = len(active)
    t["sold_count"] = len(bundles) - len(active)
    t["realized"] = sum(b["m"].realized_profit or 0 for b in bundles if b["m"].sold)
    t["gross_yield"] = t["rent_monthly"] * 12 / t["cost_basis"] if t["cost_basis"] else None
    t["net_yield"] = t["noi_12m"] / t["cost_basis"] if t["cost_basis"] else None
    t["ltv"] = t["loan_balance"] / t["value"] if t["value"] else None
    t["roi"] = (t["total_return"] / t["own_cash"]) if t["own_cash"] > 0 else None
    return t


def appreciation(s=None):
    """(rate, explanation) used for projections."""
    s = s or settings()
    if (s.get("default_appreciation") or "").strip():
        r = float(s["default_appreciation"]) / 100
        return r, f"{r*100:.1f}%/yr (your setting)"
    r = market.index_cagr(get_db())
    if r is not None:
        return r, f"{r*100:.1f}%/yr (5-yr trend of CSP house price index)"
    return 0.03, "3.0%/yr (fallback — fetch market data or set it in Settings)"
