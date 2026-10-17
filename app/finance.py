"""Pure financial calculations. No Flask / DB imports so it is easy to test.

Conventions
-----------
* Amounts are positive; direction comes from the category group.
* Category groups:
    capex     - renovation / improvements / furnishing. Added to cost basis
                (deductible from capital gain on sale in Latvia).
    opex      - running costs that reduce NOI.
    tax       - income tax paid on rent (below NOI, part of cash flow).
    income    - rent and other income.
* Mortgage debt service is computed from the loan fields on the property,
  so the user does not have to type loan payments as transactions.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime

# (name, group). Order is the order shown in forms.
CATEGORIES = [
    ("Rent", "income"),
    ("Utility reimbursement", "income"),
    ("Other income", "income"),
    ("Renovation – materials", "capex"),
    ("Renovation – labour", "capex"),
    ("Furniture & appliances", "capex"),
    ("Improvement (other)", "capex"),
    ("Repairs & maintenance", "opex"),
    ("Building management", "opex"),     # apsaimniekošana
    ("Utilities", "opex"),
    ("Property tax (NĪN)", "opex"),
    ("Insurance", "opex"),
    ("Management / agent fee", "opex"),
    ("Accounting & legal", "opex"),
    ("Bank fees", "opex"),
    ("Other expense", "opex"),
    ("Income tax on rent", "tax"),
    ("Sale of part / unit", "sale"),       # e.g. selling storage units of a basement; not rental income
]
CATEGORY_GROUP = dict(CATEGORIES)
INCOME_CATEGORIES = [c for c, g in CATEGORIES if g in ("income", "sale")]
EXPENSE_CATEGORIES = [c for c, g in CATEGORIES if g not in ("income", "sale")]


def is_inflow(category: str, kind: str = "expense") -> bool:
    return group_of(category, kind) in ("income", "sale")


def group_of(category: str, kind: str = "expense") -> str:
    return CATEGORY_GROUP.get(category, "income" if kind == "income" else "opex")


# --------------------------------------------------------------------------- dates

def to_date(v) -> date | None:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return datetime.strptime(str(v)[:10], "%Y-%m-%d").date()


def ym(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def add_months(d: date, n: int) -> date:
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    # clamp day
    for day in (d.day, 30, 29, 28):
        try:
            return date(y, m, day)
        except ValueError:
            continue
    raise ValueError(d)


def months_between(a: date, b: date) -> int:
    """Whole months from a to b (b >= a)."""
    n = (b.year - a.year) * 12 + (b.month - a.month)
    if b.day < a.day:
        n -= 1
    return max(n, 0)


def month_range(start: date, end: date) -> list[str]:
    out, cur = [], date(start.year, start.month, 1)
    stop = date(end.year, end.month, 1)
    while cur <= stop:
        out.append(ym(cur))
        cur = add_months(cur, 1)
    return out


# --------------------------------------------------------------------------- loans

def monthly_payment(principal: float, annual_rate_pct: float, months: int) -> float:
    if principal <= 0 or months <= 0:
        return 0.0
    r = annual_rate_pct / 100 / 12
    if r == 0:
        return principal / months
    return principal * r / (1 - (1 + r) ** -months)


def loan_balance(principal: float, annual_rate_pct: float, months: int, paid_months: int) -> float:
    if principal <= 0 or months <= 0:
        return 0.0
    paid_months = max(0, min(paid_months, months))
    r = annual_rate_pct / 100 / 12
    if r == 0:
        return principal * (1 - paid_months / months)
    pmt = monthly_payment(principal, annual_rate_pct, months)
    return principal * (1 + r) ** paid_months - pmt * ((1 + r) ** paid_months - 1) / r


@dataclass
class Loan:
    amount: float = 0.0
    rate: float = 0.0
    term: int = 0
    start: date | None = None

    @classmethod
    def from_property(cls, p) -> "Loan":
        return cls(float(p["loan_amount"] or 0), float(p["loan_rate"] or 0),
                   int(p["loan_term_months"] or 0), to_date(p["loan_start"]) or to_date(p["purchase_date"]))

    @property
    def active(self) -> bool:
        return self.amount > 0 and self.term > 0 and self.start is not None

    @property
    def payment(self) -> float:
        return monthly_payment(self.amount, self.rate, self.term) if self.active else 0.0

    def balance_at(self, d: date) -> float:
        if not self.active or d < self.start:
            return 0.0 if not self.active else self.amount
        return loan_balance(self.amount, self.rate, self.term, months_between(self.start, d))

    def schedule(self, until: date) -> list[tuple[date, float, float]]:
        """[(date, interest, principal)] for payments made up to `until`."""
        if not self.active:
            return []
        out, bal = [], self.amount
        r = self.rate / 100 / 12
        pmt = self.payment
        for i in range(1, self.term + 1):
            d = add_months(self.start, i)
            if d > until:
                break
            interest = bal * r
            princ = min(pmt - interest, bal)
            bal -= princ
            out.append((d, interest, princ))
        return out


# --------------------------------------------------------------------------- IRR

def xnpv(rate: float, flows: list[tuple[date, float]]) -> float:
    t0 = flows[0][0]
    return sum(cf / (1 + rate) ** ((d - t0).days / 365.0) for d, cf in flows)


def xirr(flows: list[tuple[date, float]]) -> float | None:
    """Annualised IRR for irregular dated cash flows. None if undefined."""
    flows = sorted([(d, cf) for d, cf in flows if cf], key=lambda x: x[0])
    if len(flows) < 2 or not any(cf > 0 for _, cf in flows) or not any(cf < 0 for _, cf in flows):
        return None
    if (flows[-1][0] - flows[0][0]).days < 1:
        return None
    lo, hi = -0.9999, 10.0
    f_lo, f_hi = xnpv(lo, flows), xnpv(hi, flows)
    if f_lo * f_hi > 0:
        return None
    for _ in range(200):
        mid = (lo + hi) / 2
        f_mid = xnpv(mid, flows)
        if abs(f_mid) < 1e-7:
            break
        if f_lo * f_mid < 0:
            hi, f_hi = mid, f_mid
        else:
            lo, f_lo = mid, f_mid
    return mid


# --------------------------------------------------------------------------- leases

def lease_active_in_month(lease, month: str) -> bool:
    s = to_date(lease["start_date"])
    e = to_date(lease["end_date"])
    first = datetime.strptime(month + "-01", "%Y-%m-%d").date()
    last = add_months(first, 1)
    return s < last and (e is None or e >= first)


def current_rent(leases, today: date) -> float:
    return sum(float(l["rent"] or 0) for l in leases if lease_active_in_month(l, ym(today)))


def rent_roll(leases, txs, today: date) -> list[dict]:
    """Expected vs received rent per lease per month (for arrears)."""
    received = defaultdict(float)
    for t in txs:
        if t["kind"] == "income" and t["category"] == "Rent":
            key = (t["lease_id"], t["date"][:7])
            received[key] += float(t["amount"])
    out = []
    for l in leases:
        s = to_date(l["start_date"])
        e = min(to_date(l["end_date"]) or today, today)
        if s > today:
            continue
        for m in month_range(s, e):
            exp = float(l["rent"] or 0)
            got = received.get((l["id"], m), 0.0)
            out.append({"lease_id": l["id"], "month": m, "expected": exp, "received": got,
                        "due": max(exp - got, 0.0)})
    return out


# --------------------------------------------------------------------------- aggregation

def monthly_table(txs, loan: Loan | None, start: date, end: date) -> list[dict]:
    """Per-month totals. Debt service comes from the loan schedule."""
    rows = {m: defaultdict(float) for m in month_range(start, end)}
    for t in txs:
        m = t["date"][:7]
        if m not in rows:
            continue
        g = group_of(t["category"], t["kind"])
        rows[m][g] += float(t["amount"])
    if loan and loan.active:
        for d, i, p in loan.schedule(end):
            m = ym(d)
            if m in rows:
                rows[m]["interest"] += i
                rows[m]["principal"] += p
    out = []
    for m, r in rows.items():
        noi = r["income"] - r["opex"]
        cash = noi + r["sale"] - r["tax"] - r["capex"] - r["interest"] - r["principal"]
        out.append({"month": m, "income": r["income"], "sale": r["sale"], "opex": r["opex"], "capex": r["capex"],
                    "tax": r["tax"], "interest": r["interest"], "principal": r["principal"],
                    "noi": noi, "cash_flow": cash})
    return out


def yearly_table(monthly: list[dict]) -> list[dict]:
    years = {}
    for r in monthly:
        y = r["month"][:4]
        acc = years.setdefault(y, defaultdict(float))
        for k, v in r.items():
            if k != "month":
                acc[k] += v
    return [{"year": y, **dict(v)} for y, v in sorted(years.items())]


def category_totals(txs, since: date | None = None) -> dict[str, float]:
    out = defaultdict(float)
    for t in txs:
        if since and to_date(t["date"]) < since:
            continue
        out[t["category"]] += float(t["amount"])
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


# --------------------------------------------------------------------------- property metrics

@dataclass
class Metrics:
    purchase_total: float = 0.0
    capex: float = 0.0
    cost_basis: float = 0.0
    own_cash: float = 0.0
    value: float = 0.0
    value_source: str = ""
    loan_balance: float = 0.0
    equity: float = 0.0
    income_total: float = 0.0
    opex_total: float = 0.0
    tax_total: float = 0.0
    interest_total: float = 0.0
    principal_total: float = 0.0
    cash_flow_total: float = 0.0      # operating cash after tax & debt service (excl. capex)
    rent_monthly: float = 0.0
    income_12m: float = 0.0
    opex_12m: float = 0.0
    noi_12m: float = 0.0
    debt_service_12m: float = 0.0
    gross_yield: float | None = None  # annual rent / cost basis
    net_yield: float | None = None    # NOI / cost basis
    cap_rate: float | None = None     # NOI / current value
    cash_on_cash: float | None = None
    unrealized_gain: float = 0.0
    total_return: float = 0.0         # cash flows + net equity gain
    roi: float | None = None          # total_return / own cash
    irr: float | None = None          # equity IRR incl. hypothetical sale today
    occupancy_12m: float | None = None
    payback_years: float | None = None
    months_owned: int = 0
    sold: bool = False
    realized_profit: float | None = None
    extra: dict = field(default_factory=dict)


def property_metrics(p, txs, leases, value: float, value_source: str, s: dict, today: date) -> Metrics:
    m = Metrics()
    loan = Loan.from_property(p)
    pdate = to_date(p["purchase_date"]) or today
    sold_on = to_date(p["sale_date"]) if p["sale_price"] else None
    end = sold_on or today
    m.sold = sold_on is not None
    m.months_owned = months_between(pdate, end)

    m.purchase_total = float(p["purchase_price"] or 0) + float(p["purchase_costs"] or 0)
    for t in txs:
        g, a = group_of(t["category"], t["kind"]), float(t["amount"])
        if g == "capex":
            m.capex += a
        elif g == "income":
            m.income_total += a
        elif g == "opex":
            m.opex_total += a
        elif g == "tax":
            m.tax_total += a
        elif g == "sale":
            m.extra["partial_sales"] = m.extra.get("partial_sales", 0.0) + a
    m.cost_basis = m.purchase_total + m.capex

    sched = loan.schedule(end)
    m.interest_total = sum(i for _, i, _ in sched)
    m.principal_total = sum(pr for _, _, pr in sched)
    m.loan_balance = 0.0 if m.sold else loan.balance_at(end)
    m.own_cash = m.cost_basis - (loan.amount if loan.active else 0.0)
    m.cash_flow_total = (m.income_total + m.extra.get("partial_sales", 0.0) - m.opex_total - m.tax_total
                         - m.interest_total - m.principal_total)

    m.value = float(p["sale_price"]) if m.sold else value
    m.value_source = "sale" if m.sold else value_source
    m.equity = m.value - m.loan_balance

    # trailing 12 months
    since = add_months(end, -12)
    for t in txs:
        d = to_date(t["date"])
        if d <= since or d > end:
            continue
        g, a = group_of(t["category"], t["kind"]), float(t["amount"])
        if g == "income":
            m.income_12m += a
        elif g == "opex":
            m.opex_12m += a
    m.noi_12m = m.income_12m - m.opex_12m
    m.debt_service_12m = sum(i + pr for d, i, pr in sched if d > since)
    m.rent_monthly = current_rent(leases, today) if not m.sold else 0.0
    m.extra["rent_basis"] = "lease"
    if not m.rent_monthly and not m.sold and _f(p, "expected_rent"):
        m.rent_monthly = _f(p, "expected_rent")
        m.extra["rent_basis"] = "expected"

    annual_rent = m.rent_monthly * 12 or m.income_12m
    if m.cost_basis > 0:
        m.gross_yield = annual_rent / m.cost_basis if annual_rent else None
        # Use trailing NOI once there is a year of history, otherwise annualise current rent
        noi = m.noi_12m if m.months_owned >= 12 or not m.rent_monthly else annual_rent - m.opex_12m
        m.net_yield = noi / m.cost_basis if annual_rent else None
        if m.value:
            m.cap_rate = noi / m.value if annual_rent else None
        if m.own_cash > 0 and annual_rent:
            m.cash_on_cash = (noi - m.debt_service_12m) / m.own_cash

    # occupancy over trailing 12 months (only months after first lease start)
    first_lease = min((to_date(l["start_date"]) for l in leases), default=None)
    if first_lease:
        months = [x for x in month_range(add_months(end, -11), end) if x >= ym(first_lease)]
        if months:
            occ = sum(1 for x in months if any(lease_active_in_month(l, x) for l in leases))
            m.occupancy_12m = occ / len(months)

    sell_cost_pct = float(s.get("selling_cost_pct") or 0) / 100
    selling_costs = float(p["sale_costs"] or 0) if m.sold else m.value * sell_cost_pct
    m.unrealized_gain = m.value - selling_costs - m.cost_basis
    cgt = capital_gains_tax(m.unrealized_gain, p, end, s)
    m.extra["selling_costs"] = selling_costs
    m.extra["cgt"] = cgt
    # Everything received so far + what a sale would return now − own cash put in.
    # (Principal repaid is in cash_flow_total as an outflow and comes back via lower loan balance.)
    m.total_return = (m.cash_flow_total + m.value - selling_costs - cgt - m.loan_balance) - m.own_cash
    if m.own_cash > 0:
        m.roi = m.total_return / m.own_cash
    if m.sold:
        m.realized_profit = m.total_return

    # Equity IRR: actual dated flows + (hypothetical) sale at `end`.
    flows = [(pdate, -m.purchase_total + (loan.amount if loan.active else 0.0))]
    for t in txs:
        g, a, d = group_of(t["category"], t["kind"]), float(t["amount"]), to_date(t["date"])
        flows.append((d, a if g in ("income", "sale") else -a))
    for d, i, pr in sched:
        flows.append((d, -(i + pr)))
    flows.append((end, m.value - selling_costs - cgt - m.loan_balance))
    if m.months_owned >= 1:
        m.irr = xirr(flows)

    annual_cf = (m.noi_12m - m.debt_service_12m - m.tax_total / max(m.months_owned / 12, 1)
                 if m.months_owned >= 12 else annual_rent - m.opex_12m - loan.payment * 12)
    if annual_cf > 0 and m.own_cash > 0:
        m.payback_years = m.own_cash / annual_cf
    return m


def _f(row, key, default=0.0):
    try:
        v = row[key]
    except (KeyError, IndexError):
        return default
    return float(v) if v not in (None, "") else default


def capital_gains_tax(gain: float, p, sale_date: date, s: dict) -> float:
    """Latvian IIN on capital gain (20%). Exempt when held > 60 months AND the
    owner had declared residence there >= 12 months (flag on property)."""
    if gain <= 0:
        return 0.0
    pdate = to_date(p["purchase_date"])
    if pdate and months_between(pdate, sale_date) >= 60 and int(p["declared_residence"] or 0):
        return 0.0
    return gain * float(s.get("cgt_rate") or 25.5) / 100


# --------------------------------------------------------------------------- projections

def appreciation_from_index(index: list[tuple[str, float]], years: int = 5) -> float | None:
    """CAGR over the last `years` of a quarterly index [(YYYY-Qn, value)]."""
    pts = sorted(index)
    if len(pts) < 5:
        return None
    n = min(years * 4, len(pts) - 1)
    a, b = pts[-1 - n][1], pts[-1][1]
    if a <= 0:
        return None
    return (b / a) ** (4 / n) - 1


def sale_scenarios(p, metrics: Metrics, txs, s: dict, today: date, appreciation: float,
                   horizon: int = 15) -> list[dict]:
    """Projected outcome if the property is sold at the end of year 0..horizon.

    Each row: sale date, value, net proceeds, CGT, cumulative cash, total profit,
    equity IRR, and the *marginal* return on equity of holding that year
    (used for the sell/hold recommendation).
    """
    loan = Loan.from_property(p)
    pdate = to_date(p["purchase_date"]) or today
    rent_g = float(s.get("rent_growth") or 0) / 100
    exp_g = float(s.get("expense_growth") or 0) / 100
    vac = float(s.get("vacancy_pct") or 0) / 100
    rtax = float(s.get("rental_tax_rate") or 0) / 100
    sell_pct = float(s.get("selling_cost_pct") or 0) / 100

    base_rent = metrics.rent_monthly * 12 or metrics.income_12m
    base_opex = metrics.opex_12m
    if metrics.months_owned < 12 and metrics.months_owned > 0:
        base_opex = metrics.opex_12m * 12 / max(metrics.months_owned, 1)

    # Historic actual flows (equity view)
    hist = [(pdate, -metrics.purchase_total + (loan.amount if loan.active else 0.0))]
    for t in txs:
        g, a, d = group_of(t["category"], t["kind"]), float(t["amount"]), to_date(t["date"])
        hist.append((d, a if g in ("income", "sale") else -a))
    for d, i, pr in loan.schedule(today):
        hist.append((d, -(i + pr)))

    rows, future, cum_future = [], [], 0.0
    prev_net = None
    for y in range(0, horizon + 1):
        d = add_months(today, 12 * y)
        rent = 0.0
        if y > 0:
            rent = base_rent * (1 + rent_g) ** (y - 1) * (1 - vac)
            opex = base_opex * (1 + exp_g) ** (y - 1)
            tax = rent * rtax
            ds = sum(i + pr for dd, i, pr in loan.schedule(d) if dd > add_months(d, -12))
            cf = rent - opex - tax - ds
            cum_future += cf
            future.append((add_months(today, 12 * y - 6), cf))  # mid-year convention
        value = metrics.value * (1 + appreciation) ** y
        sell_costs = value * sell_pct
        gain = value - sell_costs - metrics.cost_basis
        cgt = capital_gains_tax(gain, p, d, s)
        bal = loan.balance_at(d)
        net = value - sell_costs - cgt - bal
        total_profit = metrics.cash_flow_total + cum_future + net - metrics.own_cash
        irr = xirr(hist + future + [(d, net)]) if (d - pdate).days > 30 else None
        marginal = None
        if prev_net and prev_net > 0:
            marginal = (net - prev_net + (future[-1][1] if future else 0)) / prev_net
        rows.append({"year": y, "date": d, "value": value, "rent": rent, "selling_costs": sell_costs,
                     "cgt": cgt, "loan_balance": bal, "net_proceeds": net,
                     "cum_cash_flow": metrics.cash_flow_total + cum_future,
                     "total_profit": total_profit, "irr": irr, "marginal": marginal,
                     "years_held": months_between(pdate, d) / 12})
        prev_net = net
    return rows


def recommendation(p, rows: list[dict], s: dict, today: date) -> dict:
    """Plain-language hold/sell advice.

    Logic (simple and explainable):
      1. If CGT exemption is reachable soon (declared residence, < 60 months),
         don't sell before the 60-month mark.
      2. Compare the marginal return on equity of holding each next year with
         the hurdle rate (what the freed cash could earn elsewhere). The first
         year where it falls below the hurdle is the suggested sale window.
      3. The year with the best equity IRR is shown as a reference.
    """
    hurdle = float(s.get("hurdle_rate") or 6) / 100
    notes = []
    pdate = to_date(p["purchase_date"]) or today
    held = months_between(pdate, today)
    exempt_ready = add_months(pdate, 60)
    if int(p["declared_residence"] or 0) and held < 60:
        notes.append(f"Capital-gains exemption becomes available on {exempt_ready.isoformat()} "
                     f"(owned 60 months with declared residence). Selling earlier costs CGT.")
    elif held < 60:
        notes.append("Sale will be subject to capital-gains tax on (price − purchase − "
                     "improvements − selling costs). Keep all renovation invoices.")

    if not any(r["rent"] for r in rows):
        return {"verdict": "unknown", "text": "No rent yet — enter the expected monthly rent on the property "
                "(Edit → Expected rent) to get a projection and sell/hold advice.", "notes": notes,
                "best": None, "window": None, "hurdle": hurdle}
    best = max((r for r in rows if r["irr"] is not None), key=lambda r: r["irr"], default=None)
    window = next((r for r in rows[1:] if r["marginal"] is not None and r["marginal"] < hurdle), None)
    if rows and rows[1:] and rows[1]["marginal"] is not None and rows[1]["marginal"] < hurdle:
        verdict = "consider_selling"
        text = (f"Holding another year is projected to return {rows[1]['marginal']*100:.1f}% on your "
                f"equity, below your {hurdle*100:.0f}% hurdle. Consider selling or refinancing to free equity.")
    elif window:
        verdict = "hold"
        text = (f"Hold. Return on equity stays above your {hurdle*100:.0f}% hurdle until about "
                f"{window['date'].year} — review a sale around then.")
    else:
        verdict = "hold"
        text = f"Hold. Projected return on equity stays above your {hurdle*100:.0f}% hurdle over the horizon."
    return {"verdict": verdict, "text": text, "notes": notes, "best": best, "window": window,
            "hurdle": hurdle}
