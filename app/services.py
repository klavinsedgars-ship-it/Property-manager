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
    # The CSP index tracks dwellings — don't apply it to land, basements, parking or commercial space.
    if (p["kind"] or "apartment") in ("apartment", "house"):
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
    m = fin.property_metrics(bundle["p"], bundle["txs"], bundle["leases"], value, src, s, today)
    if m.extra.get("partial_sales") and not bundle["valuations"] and not m.sold:
        m.extra["value_warning"] = ("Part of this property is sold, but the value still assumes all of it — "
                                    "add a valuation of what you still own.")
    return m


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
    # Yields over income-producing properties only (renovating / empty ones would dilute them).
    earning = [b["m"] for b in active if b["m"].gross_yield]
    basis = sum(m.cost_basis for m in earning)
    t["gross_yield"] = sum(m.gross_yield * m.cost_basis for m in earning) / basis if basis else None
    t["net_yield"] = sum(m.extra["noi_run_rate"] for m in earning) / basis if basis else None
    t["ltv"] = t["loan_balance"] / t["value"] if t["value"] else None
    t["cash_invested"] = sum(b["m"].extra.get("cash_invested", 0) for b in active)
    t["roi"] = (t["total_return"] / t["cash_invested"]) if t["cash_invested"] > 0 else None
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
