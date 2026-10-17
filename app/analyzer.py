"""Deal analyzer: paste a listing link (ss.lv, Facebook, …) or ad text and get a
market comparison, return projections, a recommended offer and a verdict.

Pipeline: fetch → extract (ss.lv parser, else Claude) → analyse (pure maths on
VZD sales + CSP index + Euribor + your settings) → optional AI opinion.
"""
from __future__ import annotations

import html as htmllib
import re
import statistics
import urllib.parse
import urllib.request
from datetime import date

from . import ai
from . import finance as fin
from . import market, vzd

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/128.0 Safari/537.36")

# --------------------------------------------------------------------------- assumptions

# Long-term rent, €/m² per month (2025–2026 listings; editable per analysis).
RENT_M2 = {"riga": {"pre": 11.0, "stalin": 10.0, "soviet": 9.5, "modern": 11.0, "new": 13.0},
           "jurmala": {"pre": 11.0, "stalin": 10.0, "soviet": 9.5, "modern": 11.0, "new": 13.0},
           "other": {"pre": 6.5, "stalin": 6.5, "soviet": 6.0, "modern": 7.0, "new": 8.5}}
RENOVATION_M2 = {"new_or_renovated": 0, "good": 30, "needs_cosmetic": 150, "needs_full": 400, "unknown": 80}
CONDITIONS = {"new_or_renovated": "New / renovated", "good": "Good, move-in ready", "needs_cosmetic": "Needs cosmetic repair",
              "needs_full": "Needs full renovation", "unknown": "Unknown"}

# Latvian building series -> era (used to pick comparable sales by build year)
ERAS = {"pre": (1800, 1944), "stalin": (1945, 1959), "soviet": (1960, 1992), "modern": (1993, 2009), "new": (2010, 2100)}


def era_of(series: str | None, year: int | None) -> str | None:
    if year:
        return next(k for k, (a, b) in ERAS.items() if a <= year <= b)
    s = vzd.norm(series or "")
    if not s:
        return None
    if any(w in s for w in ("pirmskara", "prewar", "pkara", "довоен", "vecriga", "jugendstil")):
        return "pre"
    if any(w in s for w in ("stalin", "stalina", "сталин")):
        return "stalin"
    if any(w in s for w in ("jaun", "new", "нов", "renov")):
        return "new" if "renov" not in s else None
    if any(w in s for w in ("103", "104", "119", "316", "318", "467", "602", "lit", "hrus", "hrusc", "хрущ",
                            "specpr", "mgim", "malgim", "gostink")):
        return "soviet"
    return None


def city_group(city: str | None) -> str:
    c = vzd.norm(city or "")
    if c.startswith("rig"):
        return "riga"
    if c.startswith("jurm"):
        return "jurmala"
    return "other"


# --------------------------------------------------------------------------- fetching & parsing

def fetch(url: str) -> str:
    p = urllib.parse.urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname or p.hostname in ("localhost",) or \
            re.match(r"^(127\.|10\.|192\.168\.|169\.254\.|0\.)", p.hostname):
        raise RuntimeError("Please paste a public listing link (https://…)")
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "lv,en;q=0.8,ru;q=0.6"})
    with urllib.request.urlopen(req, timeout=25) as r:
        raw = r.read(3_000_000)
        charset = r.headers.get_content_charset() or "utf-8"
    return raw.decode(charset, "replace")


def html_to_text(page: str) -> str:
    page = re.sub(r"(?is)<(script|style|noscript|svg)[^>]*>.*?</\1>", " ", page)
    page = re.sub(r"(?i)<br\s*/?>|</(p|div|tr|li|h\d)>", "\n", page)
    text = htmllib.unescape(re.sub(r"<[^>]+>", " ", page))
    text = re.sub(r"[ \t ]+", " ", text)
    return re.sub(r"\n\s*\n+", "\n", text).strip()


def _num(s):
    if s is None:
        return None
    m = re.search(r"\d[\d\s .,]*", str(s))
    return vzd.to_float(m.group(0).strip()) if m else None


SS_LABELS = {  # label (lv / en / ru, normalised) -> field
    "city": ("pilseta", "city", "город", "gorod"), "district": ("rajons", "district", "район", "rajon"),
    "street": ("iela", "street", "улица", "ulica"), "rooms": ("istabas", "istabu", "rooms", "комнат", "komnat"),
    "area_m2": ("platiba", "area", "площадь", "ploschad"), "floor": ("stavs", "floor", "этаж", "etazh"),
    "building_series": ("serija", "series", "серия", "serija"), "house_type": ("majastips", "housetype", "типдома"),
    "land_area_m2": ("zemesplatiba", "landarea", "площадьземли"), "floors_total": ("stavuskaits", "floors", "этажей"),
}


def parse_ss(page: str) -> dict:
    """ss.lv / ss.com ad page: label/value table + price cell + description."""
    out = {}
    for label, value in re.findall(r'(?is)<td[^>]*class="ads_opt_name[^"]*"[^>]*>(.*?)</td>\s*'
                                   r'<td[^>]*class="ads_opt"[^>]*>(.*?)</td>', page):
        lab = vzd.norm(html_to_text(label)).rstrip(":")
        lab_ru = re.sub(r"[^а-яё]", "", html_to_text(label).lower())
        val = html_to_text(value).replace("[Karte]", "").replace("[Map]", "").replace("[Карта]", "").strip()
        for field, keys in SS_LABELS.items():
            if field in out:
                continue
            if any(lab == k or (len(k) > 4 and lab.startswith(k)) for k in keys) or (lab_ru and lab_ru in keys):
                out[field] = val
                break
    m = re.search(r'(?is)<td[^>]*class="ads_price"[^>]*>(.*?)</td>', page) or \
        re.search(r'(?is)id="tdo_8"[^>]*>(.*?)</td>', page)
    if m:
        price_txt = html_to_text(m.group(1))
        out["price_text"] = price_txt
        out["price"] = _num(price_txt.split("(")[0])
        if re.search(r"(?i)mēn|mon|мес", price_txt):
            out["deal_type"] = "rent"
    d = re.search(r'(?is)<div[^>]*id="msg_div_msg"[^>]*>(.*?)<table', page)
    if d:
        out["description"] = html_to_text(d.group(1))[:6000]
    t = re.search(r"(?is)<title>(.*?)</title>", page)
    if t:
        out["title"] = html_to_text(t.group(1))[:200]
    # normalise numbers
    if out.get("area_m2"):
        out["area_m2"] = _num(out["area_m2"])
    if out.get("land_area_m2"):
        out["land_area_m2"] = _num(out["land_area_m2"])
    if out.get("rooms"):
        out["rooms"] = int(_num(out["rooms"]) or 0) or None
    if out.get("floor"):
        parts = re.findall(r"\d+", out["floor"])
        out["floor"], out["floors_total"] = (int(parts[0]) if parts else None), (int(parts[1]) if len(parts) > 1 else None)
    return out if out.get("price") and out.get("area_m2") else {}


def extract(url: str | None, text: str | None, use_ai: bool) -> tuple[dict, str, list[str]]:
    """Return (listing fields, source text, notes)."""
    notes, page, source_text = [], None, (text or "").strip()
    listing: dict = {}
    if url:
        host = urllib.parse.urlparse(url).hostname or ""
        try:
            page = fetch(url)
        except Exception as e:
            notes.append(f"Could not open the link ({e}).")
        if page:
            if "ss.lv" in host or "ss.com" in host:
                listing = parse_ss(page)
                if listing:
                    listing.setdefault("kind", "house" if listing.get("land_area_m2") else "apartment")
            if not source_text:
                source_text = html_to_text(page)
            if "facebook.com" in host and (len(source_text) < 400 or re.search(r"(?i)log in|pieslēgties", source_text[:3000])):
                notes.append("Facebook requires login, so the page can't be read automatically — copy the ad text "
                             "and paste it into the text box.")
                source_text = text or ""
    if use_ai and source_text and (not listing or not listing.get("city")):
        try:
            ai_fields = ai.extract_listing(source_text, url)
            listing = {**{k: v for k, v in ai_fields.items() if v not in (None, "", [])}, **listing}
            notes.append("Details extracted by AI — check them below.")
        except Exception as e:
            notes.append(f"AI extraction failed: {e}")
    if listing.get("description") is None and source_text:
        listing["description"] = source_text[:6000]
    return listing, source_text, notes


# --------------------------------------------------------------------------- analysis

def default_inputs(listing: dict, s: dict, db) -> dict:
    """Assumptions for one analysis (all editable on the result page)."""
    area = listing.get("area_m2") or 0
    era = era_of(listing.get("building_series"), listing.get("build_year"))
    cg = city_group(listing.get("city"))
    cond = listing.get("condition") or "unknown"
    rent = round(area * RENT_M2[cg][era or "soviet"] / 5) * 5 if area else 0
    _p, eur6 = market.latest(db, "euribor_6m")
    return {
        "price": listing.get("price") or 0, "area_m2": area, "rooms": listing.get("rooms"),
        "floor": listing.get("floor"), "floors_total": listing.get("floors_total"),
        "city": listing.get("city") or "", "district": listing.get("district") or "",
        "street": listing.get("street") or "", "building_series": listing.get("building_series") or "",
        "build_year": listing.get("build_year"), "kind": listing.get("kind") or "apartment",
        "condition": cond, "renovation": round(area * RENOVATION_M2.get(cond, 80) / 100) * 100,
        "rent": rent, "management_m2": 0.6 if cg != "other" else 0.5,
        "use_loan": True, "ltv": 70.0, "loan_rate": round((eur6 if eur6 is not None else 2.3) + 2.8, 2),
        "loan_years": 25, "target_yield": float(s.get("hurdle_rate") or 6),
    }


def _irr_for(inp, s, g, years, today):
    price, reno = inp["price"], inp["renovation"]
    costs = price * 0.015 + 400
    basis = price + costs + reno
    loan_amt = price * inp["ltv"] / 100 if inp["use_loan"] else 0.0
    loan = fin.Loan(loan_amt, inp["loan_rate"], int(inp["loan_years"]) * 12, today)
    own = basis - loan_amt
    rent_g, exp_g = float(s.get("rent_growth") or 0) / 100, float(s.get("expense_growth") or 0) / 100
    vac, rtax = float(s.get("vacancy_pct") or 0) / 100, float(s.get("rental_tax_rate") or 0) / 100
    flows, cum = [(today, -own)], 0.0
    for y in range(1, years + 1):
        d = fin.add_months(today, 12 * y)
        rent = inp["rent"] * 12 * (1 + rent_g) ** (y - 1) * (1 - vac)
        opex = _opex(inp) * (1 + exp_g) ** (y - 1)
        ds = sum(i + p for dd, i, p in loan.schedule(d) if dd > fin.add_months(d, -12))
        cf = rent - opex - rent * rtax - ds
        cum += cf
        flows.append((fin.add_months(today, 12 * y - 6), cf))
    end = fin.add_months(today, 12 * years)
    value = (price + reno) * (1 + g) ** years
    sell = value * float(s.get("selling_cost_pct") or 3) / 100
    gain = value - sell - basis
    cgt = max(gain, 0) * float(s.get("cgt_rate") or 25.5) / 100
    net = value - sell - cgt - loan.balance_at(end)
    flows.append((end, net))
    return {"irr": fin.xirr(flows), "profit": cum + net - own, "value": value, "cash_flow": cum, "net_sale": net}


def _opex(inp):
    area, price = inp["area_m2"] or 0, inp["price"] or 0
    management = area * inp["management_m2"] * 12            # apsaimniekošana
    property_tax = price * 0.002                             # NĪN with declared tenant (~0.2% of cadastral value)
    insurance = 60
    repairs = inp["rent"] * 12 * 0.05
    return management + property_tax + insurance + repairs


def analyze(db, inp: dict, s: dict, today: date | None = None) -> dict:
    today = today or date.today()
    price, area, reno = inp["price"] or 0, inp["area_m2"] or 0, inp["renovation"] or 0
    if not price or not area:
        raise ValueError("Price and area are needed for the analysis")
    res: dict = {"inputs": inp, "price_m2": price / area}

    # --- market comparison (VZD registered sales)
    kind = "house" if inp.get("kind") == "house" else "apartment"
    p = {"area_m2": area, "city": inp.get("city"), "rooms": inp.get("rooms"), "kind": kind,
         "address": inp.get("street"), "district": inp.get("district")}
    comps = vzd.comparables(db, p, months=24)
    era = era_of(inp.get("building_series"), inp.get("build_year"))
    if era and comps:
        a, b = ERAS[era]
        same_era = [r for r in comps if r["build_year"] and a <= r["build_year"] <= b]
        if len(same_era) >= 8:
            comps, res["comps_era"] = same_era, era
    m = {"n": len(comps)}
    if len(comps) >= 3:
        ppm = sorted(r["price_m2"] for r in comps)
        q = statistics.quantiles(ppm, n=4) if len(ppm) >= 4 else [ppm[0], statistics.median(ppm), ppm[-1]]
        med = statistics.median(ppm)
        m.update(median_m2=med, p25=q[0], p75=q[-1], fair_value=med * area,
                 percentile=sum(1 for v in ppm if v < res["price_m2"]) / len(ppm),
                 vs_median=res["price_m2"] / med - 1,
                 sample=[{"date": r["deal_date"], "address": r["address"], "area": r["area_m2"], "rooms": r["rooms"],
                          "price": r["price"], "ppm": r["price_m2"], "year": r["build_year"]} for r in comps[:10]])
    hpi = market.series(db, "hpi_total")
    if len(hpi) >= 5:
        m["hpi_yoy"] = hpi[-1][1] / hpi[-5][1] - 1
        m["hpi_period"] = hpi[-1][0]
    res["market"] = m

    # --- income & returns
    costs = price * 0.015 + 400
    basis = price + costs + reno
    vac, rtax = float(s.get("vacancy_pct") or 0) / 100, float(s.get("rental_tax_rate") or 0) / 100
    gross_rent = inp["rent"] * 12
    opex = _opex(inp)
    noi = gross_rent * (1 - vac) - opex
    loan_amt = price * inp["ltv"] / 100 if inp["use_loan"] else 0.0
    payment = fin.monthly_payment(loan_amt, inp["loan_rate"], int(inp["loan_years"]) * 12) if loan_amt else 0.0
    own = basis - loan_amt
    cash_flow = noi - gross_rent * (1 - vac) * rtax - payment * 12
    from . import services
    g, g_why = services.appreciation(s)
    res["returns"] = {
        "purchase_costs": costs, "total_cost": basis, "own_cash": own, "loan": loan_amt, "payment": payment,
        "gross_yield": gross_rent / basis, "net_yield": noi / basis, "noi": noi, "opex": opex,
        "cash_flow_year": cash_flow, "cash_flow_month": cash_flow / 12,
        "cash_on_cash": cash_flow / own if own > 0 else None,
        "appreciation": g, "appreciation_why": g_why,
        "y5": _irr_for(inp, s, g, 5, today), "y10": _irr_for(inp, s, g, 10, today),
        "dscr": noi / (payment * 12) if payment else None,
    }

    # --- recommended price
    target = (inp["target_yield"] or 6) / 100
    yield_price = max(((noi + price * 0.002) / target - 400 - reno) / (1.015 + 0.002 / target), 0)
    fair = m.get("fair_value")
    max_price = min(yield_price, fair) if fair else yield_price
    res["price_advice"] = {
        "yield_price": yield_price, "fair_value": fair, "max_price": _round(max_price),
        "opening_offer": _round(min(max_price * 0.93, price * 0.95)),
        "discount_needed": 1 - max_price / price if price else None, "target_yield": target,
    }

    # --- score
    res["score"], res["verdict"], res["reasons"] = _score(res, s)
    return res


def _round(v, step=500):
    return round(v / step) * step if v else v


def _score(res, s):
    reasons, score = [], 0.0
    m, r = res["market"], res["returns"]
    hurdle = float(s.get("hurdle_rate") or 6) / 100
    if "vs_median" in m:
        d = m["vs_median"]               # -0.2 = 20% below market
        score += max(0, min(40, 20 - d * 100))
        reasons.append(f"Asking €/m² is {abs(d) * 100:.0f}% {'below' if d < 0 else 'above'} the median of "
                       f"{m['n']} comparable sales")
    else:
        score += 15
        reasons.append("Not enough comparable registered sales to judge the price — import more VZD data")
    ny = r["net_yield"]
    score += max(0, min(35, 17.5 + (ny - hurdle) * 100 * 7))
    reasons.append(f"Net yield {ny * 100:.1f}% vs your {hurdle * 100:.0f}% target")
    irr = r["y10"]["irr"]
    if irr is not None:
        score += max(0, min(25, 12.5 + (irr - hurdle) * 100 * 2.5))
        reasons.append(f"Projected 10-year equity IRR {irr * 100:.1f}%")
    score = round(min(100, score))
    verdict = "good" if score >= 70 else "fair" if score >= 45 else "poor"
    return score, verdict, reasons
