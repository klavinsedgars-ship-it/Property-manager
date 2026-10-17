"""Latvian market data connectors (no API keys needed).

Sources
-------
* CSP (Central Statistical Bureau) — house price index, table PCI050c,
  PxWeb API v2 https://api.stat.gov.lv/api/v2 (fallback: v1 data.stat.gov.lv).
* ECB Data Portal — monthly Euribor 3M/6M/12M (SDMX, CSV).
* data.gov.lv (CKAN) — State Land Service (VZD) NĪTIS open data: registered
  real-estate transaction prices. Datasets are discovered via the CKAN API and
  imported into `market_deals`; a CSV downloaded manually can be imported too.

Everything is cached in SQLite, so the app works offline after one refresh.
"""
from __future__ import annotations

import csv
import io
import json
import re
import statistics
import unicodedata
import urllib.parse
import urllib.request
import zipfile
from datetime import date, datetime

from . import finance as fin

UA = {"User-Agent": "PropertyManager/1.0 (+self-hosted)"}
CSP_V2 = "https://api.stat.gov.lv/api/v2/tables/{table}"
CSP_V1 = "https://data.stat.gov.lv/api/v1/en/OSP_PUB/START/VEK/PC/PCI/{table}"
HPI_TABLE = "PCI050c"
ECB = "https://data-api.ecb.europa.eu/service/data/FM/M.U2.EUR.RT.MM.{key}_.HSTA?format=csvdata&detail=dataonly"
EURIBOR = {"euribor_3m": "EURIBOR3MD", "euribor_6m": "EURIBOR6MD", "euribor_12m": "EURIBOR1YD"}
CKAN = "https://data.gov.lv/dati/api/3/action/"

# Published benchmarks (no API) — shown as reference, with source.
BENCHMARKS = {
    "gross_yield": {"Rīga": 0.0747, "Jūrmala": 0.0693, "Latvia": 0.072,
                    "_src": "Global Property Guide, 2026 Q2"},
}


def _get(url, data=None, timeout=60, headers=None):
    req = urllib.request.Request(url, data=data, headers={**UA, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().lower()


def _save_series(db, series, points, label=None, source=None):
    for period, value in points:
        db.execute("INSERT OR REPLACE INTO market_data (series, period, value, fetched_at) VALUES (?,?,?,?)",
                   (series, period, value, datetime.now().isoformat(timespec="seconds")))
    if label:
        _meta_set(db, f"label:{series}", json.dumps({"label": label, "source": source}))


def _meta_set(db, key, value):
    db.execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (f"mkt:{key}", value))


def _meta_get(db, key, default=None):
    r = db.execute("SELECT value FROM settings WHERE key = ?", (f"mkt:{key}",)).fetchone()
    return r["value"] if r else default


def series(db, name) -> list[tuple[str, float]]:
    return [(r["period"], r["value"]) for r in
            db.execute("SELECT period, value FROM market_data WHERE series=? ORDER BY period", (name,))]


# --------------------------------------------------------------------------- json-stat2

def parse_jsonstat2(d: dict) -> list[tuple[dict, float]]:
    """Flatten a json-stat2 dataset into [({dim: (code, label)}, value)]."""
    ids, sizes = d["id"], d["size"]
    cats = []
    for dim in ids:
        c = d["dimension"][dim]["category"]
        idx = c.get("index")
        if isinstance(idx, list):
            codes = idx
        elif isinstance(idx, dict):
            codes = [k for k, _ in sorted(idx.items(), key=lambda kv: kv[1])]
        else:
            codes = list(c.get("label", {}).keys())
        labels = c.get("label", {})
        cats.append([(code, labels.get(code, code)) for code in codes])
    values = d["value"]
    if isinstance(values, dict):
        values = {int(k): v for k, v in values.items()}
    out = []
    total = 1
    for s in sizes:
        total *= s
    for flat in range(total):
        v = values.get(flat) if isinstance(values, dict) else values[flat]
        if v is None:
            continue
        rem, combo = flat, {}
        for dim, size, cat in reversed(list(zip(ids, sizes, cats))):
            combo[dim] = cat[rem % size]
            rem //= size
        out.append((combo, float(v)))
    return out


def _time_dim(d):
    for dim in d["id"]:
        if dim.upper() in ("TIME", "TID") or d.get("role", {}).get("time", [None])[0] == dim:
            return dim
    return d["id"][-1]


def _period(code: str) -> str:
    # CSP uses e.g. 2025Q3 / 2025K3 / 2025M07 -> 2025-Q3 / 2025-07
    m = re.match(r"(\d{4})[QK](\d)", code)
    if m:
        return f"{m.group(1)}-Q{m.group(2)}"
    m = re.match(r"(\d{4})M(\d{2})", code)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    return code


def fetch_hpi(db):
    """House price index levels: all / new / existing dwellings (not % changes)."""
    try:
        meta = json.loads(_get(CSP_V2.format(table=HPI_TABLE) + "/metadata?lang=en&outputFormat=json-stat2"))
        sel = "".join(f"&valueCodes[{urllib.parse.quote(dim)}]=*" for dim in meta["id"])
        d = json.loads(_get(CSP_V2.format(table=HPI_TABLE) + "/data?lang=en&outputFormat=json-stat2" + sel))
    except Exception:
        body = json.dumps({"query": [], "response": {"format": "json-stat2"}}).encode()
        d = json.loads(_get(CSP_V1.format(table=HPI_TABLE), data=body,
                            headers={"Content-Type": "application/json"}))
    picked = pick_hpi_series(d)
    if not picked:
        raise RuntimeError("No index series found in PCI050c response")
    for name, (label, pts) in picked.items():
        db.execute("DELETE FROM market_data WHERE series=?", (name,))
        _save_series(db, name, pts, label, f"CSP {HPI_TABLE}")
    db.commit()
    return f"{len(picked.get('hpi_total', ('', []))[1])} quarters of house price index"


def pick_hpi_series(d) -> dict[str, tuple[str, list]]:
    """From a PCI050c json-stat2 response choose one index-level series per
    dwelling group, preferring the newest base year (e.g. 2025=100)."""
    tdim = _time_dim(d)
    groups: dict[tuple, list] = {}
    labels: dict[tuple, str] = {}
    for combo, v in parse_jsonstat2(d):
        key = tuple(code for dim, (code, _l) in sorted(combo.items()) if dim != tdim)
        groups.setdefault(key, []).append((_period(combo[tdim][0]), v))
        labels[key] = " · ".join(lbl for dim, (_c, lbl) in sorted(combo.items()) if dim != tdim)
    best: dict[str, tuple[int, str, list]] = {}
    for key, pts in groups.items():
        low = labels[key].lower()
        if "change" in low or "%" in low or "pārmai" in low:
            continue
        name = ("hpi_new" if "new" in low else
                "hpi_existing" if ("existing" in low or "previously" in low) else "hpi_total")
        m = re.search(r"(20\d\d)\s*=\s*100", labels[key])
        base = int(m.group(1)) if m else 0
        if name not in best or base > best[name][0]:
            best[name] = (base, labels[key], sorted(pts))
    return {n: (lbl, pts) for n, (_b, lbl, pts) in best.items()}


def fetch_euribor(db):
    n = 0
    for name, key in EURIBOR.items():
        text = _get(ECB.format(key=key)).decode("utf-8")
        pts = [(r["TIME_PERIOD"], float(r["OBS_VALUE"])) for r in csv.DictReader(io.StringIO(text))
               if r.get("OBS_VALUE")]
        _save_series(db, name, pts, f"Euribor {name.split('_')[1].upper()} (monthly avg)", "ECB")
        n = len(pts)
    db.commit()
    return f"Euribor 3M/6M/12M, {n} months"


# --------------------------------------------------------------------------- data.gov.lv / VZD

def ckan(action, **params):
    url = CKAN + action + "?" + urllib.parse.urlencode(params)
    d = json.loads(_get(url))
    if not d.get("success"):
        raise RuntimeError(d.get("error"))
    return d["result"]


def discover_vzd(db):
    """Find VZD transaction-price datasets on data.gov.lv and remember their resources."""
    found = []
    seen = set()
    for q in ("darījumu cenas", "darījumi", "NĪTIS", "tirgus"):
        res = ckan("package_search", q=q, fq="organization:valsts-zemes-dienests", rows=50)
        for pkg in res["results"]:
            if pkg["id"] in seen:
                continue
            seen.add(pkg["id"])
            for r in pkg.get("resources", []):
                fmt = (r.get("format") or "").upper()
                if fmt not in ("CSV", "ZIP", "XLSX") and not r.get("url", "").lower().endswith((".csv", ".zip")):
                    continue
                found.append({"package": pkg["title"], "package_name": pkg["name"], "name": r.get("name") or "",
                              "url": r["url"], "format": fmt, "modified": (r.get("last_modified") or
                                                                           r.get("created") or "")[:10]})
    found.sort(key=lambda r: (r["package"], r["modified"]), reverse=True)
    _meta_set(db, "vzd_resources", json.dumps(found))
    db.commit()
    return f"{len(found)} VZD files found on data.gov.lv"


# Header keyword matching — VZD headers are Latvian; matched without diacritics.
COLS = {
    "date": [["darijuma datums"], ["datums"], ["date"]],
    "price": [["darijuma summa"], ["darijuma cena"], ["summa"], ["cena", "eur"], ["cena"], ["price"]],
    "area": [["telpu grupas platiba"], ["kopplatiba"], ["platiba"], ["area"]],
    "territory": [["novads"], ["pilseta"], ["teritorij"], ["administrativ"], ["municipal"]],
    "address": [["adrese"], ["address"]],
    "cadastre": [["kadastra numurs"], ["kadastra apzimejums"], ["kadastr"]],
    "rooms": [["istabu"], ["rooms"]],
    "floor": [["stavs"], ["floor"]],
    "kind": [["lietosanas veids"], ["telpu grupas veids"], ["objekta veids"], ["veids"]],
}


def map_columns(header: list[str]) -> dict[str, int]:
    norm = [_norm(h) for h in header]
    out = {}
    for field, options in COLS.items():
        for words in options:
            hit = next((i for i, h in enumerate(norm) if all(w in h for w in words) and i not in out.values()),
                       None)
            if hit is not None:
                out[field] = hit
                break
    return out


def _to_float(v):
    if v is None:
        return None
    s = str(v).strip().replace(" ", "").replace(" ", "").replace("€", "")
    if not s:
        return None
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    else:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _to_date(v):
    s = str(v or "").strip()[:10]
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%Y.%m.%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def _decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp1257", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "ignore")


def import_deals_bytes(db, raw: bytes, source: str) -> str:
    """Import a VZD transactions CSV (or a ZIP of CSVs). Returns a summary."""
    texts = []
    if raw[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            texts = [_decode(z.read(n)) for n in z.namelist() if n.lower().endswith(".csv")]
    else:
        texts = [_decode(raw)]
    added = skipped = 0
    for text in texts:
        sample = text[:5000]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=";,\t|")
        except csv.Error:
            dialect = csv.excel
            dialect.delimiter = ";" if sample.count(";") > sample.count(",") else ","
        reader = csv.reader(io.StringIO(text), dialect)
        header = next(reader, None)
        if not header:
            continue
        cols = map_columns(header)
        if not {"price", "area"} <= cols.keys():
            raise RuntimeError(f"Could not find price/area columns in header: {header[:15]}")
        get = lambda row, k: row[cols[k]] if k in cols and cols[k] < len(row) else None  # noqa: E731
        for row in reader:
            price, area = _to_float(get(row, "price")), _to_float(get(row, "area"))
            if not price or not area or area < 8:
                skipped += 1
                continue
            ppm = price / area
            if not 50 <= ppm <= 15000:   # shares, symbolic prices, data errors
                skipped += 1
                continue
            rooms = _to_float(get(row, "rooms"))
            cur = db.execute("""INSERT OR IGNORE INTO market_deals (source, deal_date, price, area_m2, price_m2,
                                territory, address, cadastre_no, rooms, floor, kind)
                                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                             (source, _to_date(get(row, "date")), price, area, round(ppm, 2),
                              (get(row, "territory") or "").strip() or None, (get(row, "address") or "").strip() or None,
                              (get(row, "cadastre") or "").strip() or None, int(rooms) if rooms else None,
                              get(row, "floor"), get(row, "kind")))
            added += cur.rowcount
    db.commit()
    return f"Imported {added} deals ({skipped} skipped as incomplete/outliers)"


def import_deals_url(db, url: str) -> str:
    if not url.startswith("https://data.gov.lv/") and not url.startswith("https://www.vzd.gov.lv/"):
        raise RuntimeError("Only data.gov.lv / vzd.gov.lv URLs are allowed")
    return import_deals_bytes(db, _get(url, timeout=300), url.rsplit("/", 1)[-1])


# --------------------------------------------------------------------------- refresh

def refresh_all(db):
    out = []
    for name, fn in (("House price index (CSP)", fetch_hpi), ("Euribor (ECB)", fetch_euribor),
                     ("VZD datasets (data.gov.lv)", discover_vzd)):
        try:
            out.append((name, True, fn(db)))
        except Exception as e:  # network errors are shown to the user, never fatal
            out.append((name, False, f"failed — {e}"))
    _meta_set(db, "last_refresh", datetime.now().isoformat(timespec="minutes"))
    db.commit()
    return out


# --------------------------------------------------------------------------- analysis helpers

def _quarter(d: date) -> str:
    return f"{d.year}-Q{(d.month - 1) // 3 + 1}"


def index_at(db, d: date, name="hpi_total"):
    pts = dict(series(db, name))
    if not pts:
        return None, None
    q = _quarter(d)
    if q in pts:
        return pts[q], q
    earlier = [k for k in pts if k <= q]
    k = max(earlier) if earlier else min(pts)
    return pts[k], k


def latest(db, name):
    s = series(db, name)
    return s[-1] if s else (None, None)


def index_cagr(db, years=5):
    return fin.appreciation_from_index(series(db, "hpi_total"), years)


def _hpi_name(p):
    s = (p["building_series"] or "").lower()
    if "new project" in s:
        return "hpi_new" if series_exists_cache.get("hpi_new") else "hpi_total"
    return "hpi_existing" if series_exists_cache.get("hpi_existing") else "hpi_total"


series_exists_cache: dict = {}


def index_estimate(db, p, capex):
    """Purchase price moved with the CSP index since purchase, plus improvements."""
    pdate = fin.to_date(p["purchase_date"])
    if not pdate or not p["purchase_price"]:
        return None
    for n in ("hpi_new", "hpi_existing"):
        series_exists_cache[n] = bool(db.execute("SELECT 1 FROM market_data WHERE series=? LIMIT 1", (n,)).fetchone())
    name = _hpi_name(p)
    then, _ = index_at(db, pdate, name)
    now_period, now = latest(db, name)
    if not then or not now:
        return None
    v = float(p["purchase_price"]) * now / then + capex
    return v, f"index estimate ({name.replace('hpi_', '')} dwellings, {now_period})"


def _city_match(p) -> str:
    return _norm(p["city"] or "").split(" ")[0][:5]


def comparables(db, p, months=24):
    """Recent registered deals in the same city with similar size."""
    if not p["area_m2"]:
        return []
    since = fin.add_months(date.today(), -months).isoformat()
    key = _city_match(p)
    area = float(p["area_m2"])
    rows = db.execute("""SELECT * FROM market_deals WHERE deal_date >= ? AND area_m2 BETWEEN ? AND ?
                         ORDER BY deal_date DESC LIMIT 5000""", (since, area * 0.7, area * 1.3)).fetchall()
    out = [r for r in rows if key and key in _norm((r["territory"] or "") + " " + (r["address"] or ""))]
    if p["district"]:
        dk = _norm(p["district"])[:5]
        local = [r for r in out if dk in _norm(r["address"] or "")]
        if len(local) >= 5:
            return local
    return out


def property_market_context(db, p, m):
    ctx = {"has_data": False}
    comps = comparables(db, p)
    if comps:
        ppm = [r["price_m2"] for r in comps]
        med = statistics.median(ppm)
        q = statistics.quantiles(ppm, n=4) if len(ppm) >= 4 else [min(ppm), med, max(ppm)]
        ctx.update(has_data=True, comps=comps[:12], comps_n=len(comps), median_m2=med, p25=q[0], p75=q[-1],
                   comps_value=med * float(p["area_m2"]))
        if p["purchase_price"] and p["area_m2"]:
            ctx["bought_m2"] = float(p["purchase_price"]) / float(p["area_m2"])
        if m.value and p["area_m2"]:
            ctx["value_m2"] = m.value / float(p["area_m2"])
    pdate = fin.to_date(p["purchase_date"])
    if pdate:
        then, q0 = index_at(db, pdate)
        q1, now = latest(db, "hpi_total")
        if then and now:
            ctx.update(has_data=True, hpi_change=now / then - 1, hpi_from=q0, hpi_to=q1)
    period, eur6 = latest(db, "euribor_6m")
    if eur6 is not None:
        ctx.update(euribor6=eur6, euribor_period=period)
    city = "Rīga" if "rig" in _norm(p["city"]) else ("Jūrmala" if "jurm" in _norm(p["city"]) else "Latvia")
    ctx["bench_yield"] = BENCHMARKS["gross_yield"][city]
    ctx["bench_city"] = city
    ctx["bench_src"] = BENCHMARKS["gross_yield"]["_src"]
    return ctx


def city_ppm_trend(db, city_key, kind_hint=None):
    """Median €/m² per quarter for deals matching city."""
    rows = db.execute("SELECT deal_date, price_m2, territory, address FROM market_deals WHERE deal_date IS NOT NULL")
    buckets: dict[str, list] = {}
    for r in rows:
        if city_key and city_key not in _norm((r["territory"] or "") + " " + (r["address"] or "")):
            continue
        buckets.setdefault(_quarter(fin.to_date(r["deal_date"])), []).append(r["price_m2"])
    return [(q, statistics.median(v), len(v)) for q, v in sorted(buckets.items()) if len(v) >= 5]


def overview(db):
    res = json.loads(_meta_get(db, "vzd_resources", "[]"))
    terr = db.execute("""SELECT territory, count(*) n, min(deal_date) d0, max(deal_date) d1
                         FROM market_deals GROUP BY territory ORDER BY n DESC LIMIT 15""").fetchall()
    cities = [r["city"] for r in db.execute("SELECT DISTINCT city FROM properties WHERE city IS NOT NULL")]
    trends = {c: city_ppm_trend(db, _norm(c).split(" ")[0][:5]) for c in cities}
    return {"hpi": series(db, "hpi_total"), "hpi_new": series(db, "hpi_new"),
            "hpi_existing": series(db, "hpi_existing"), "euribor6": series(db, "euribor_6m")[-60:],
            "euribor3": series(db, "euribor_3m")[-60:], "euribor12": series(db, "euribor_12m")[-60:],
            "resources": res, "territories": terr, "trends": trends,
            "deals": db.execute("SELECT count(*) FROM market_deals").fetchone()[0],
            "last_refresh": _meta_get(db, "last_refresh"), "bench": BENCHMARKS}
