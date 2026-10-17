"""State Land Service (VZD) open data from data.gov.lv.

1. **Registered sales** — NĪTIS open data: every purchase registered in the
   Land Book, published yearly as ZIPs of CSV files per object type:
   TG = room groups (apartments), ZVB = land with buildings (houses), ZV = land.
   Imported into the `sales` table and explored on the "Sold properties" page.
2. **Official statistics** — average transaction prices (apartments, houses)
   and market indicators, stored as raw tables and shown as published.

Column names in VZD files are codes such as DeaDate / DeaAmount / PregCadNr
(older or derived files use Latvian headers). Columns are matched by pattern
so both styles work; the mapping used is reported after every import.
"""
from __future__ import annotations

import csv
import io
import json
import re
import statistics
import time
import unicodedata
import urllib.parse
import zipfile
from collections import Counter, defaultdict
from datetime import date, datetime

from . import finance as fin
from . import geo
from .db import set_setting

DEALS_PACKAGE = "nekustama-ipasuma-tirgus-datu-bazes-atvertie-dati"
STATS_PACKAGES = {
    "videjas-darijumu-cenas-dz": "Average apartment prices",
    "videjas-darijumu-cenas-ar-savrupmajam": "Average house prices",
    "tirgus-statistika": "Market statistics",
}
KINDS = {"apartment": "Apartments", "house": "Houses (land + building)", "land": "Land"}
PPM_BOUNDS = {"apartment": (100, 15000), "house": (30, 10000), "land": (0.05, 2000)}
ALLOWED_HOSTS = ("data.gov.lv", "vzd.gov.lv", "www.vzd.gov.lv")


def _http():
    from . import market  # shared HTTP helper / CKAN client
    return market


def norm(s) -> str:
    s = unicodedata.normalize("NFKD", str(s or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]", "", s)


def meta_get(db, key, default=None):
    r = db.execute("SELECT value FROM settings WHERE key = ?", (f"vzd:{key}",)).fetchone()
    return json.loads(r["value"]) if r else default


def meta_set(db, key, value):
    set_setting(db, f"vzd:{key}", json.dumps(value))


# --------------------------------------------------------------------------- discovery

def _resource_info(pkg, r):
    name = r.get("name") or r.get("url", "").rsplit("/", 1)[-1]
    fmt = (r.get("format") or "").upper()
    m = re.search(r"(20\d\d)", name) or re.search(r"(20\d\d)", r.get("url", ""))
    return {"package": pkg.get("title") or pkg.get("name"), "name": name, "url": r.get("url", ""), "format": fmt,
            "year": int(m.group(1)) if m else None,
            "modified": (r.get("last_modified") or r.get("metadata_modified") or r.get("created") or "")[:10]}


def _importable(r):
    u = r["url"].lower()
    return r["format"] in ("CSV", "ZIP") or u.endswith((".csv", ".zip"))


def discover(db):
    """List NĪTIS sales files and official statistics resources on data.gov.lv."""
    m = _http()
    pkgs = []
    try:
        pkgs.append(m.ckan("package_show", id=DEALS_PACKAGE))
    except Exception:
        pass
    for q in ("NĪTIS darījumi", "Darījumi ar telpu grupām", "darījumi ar zemi", "darījumi ar būvēm"):
        try:
            res = m.ckan("package_search", q=q, fq="organization:valsts-zemes-dienests", rows=20)
        except Exception:
            continue
        pkgs += [p for p in res["results"] if not any(p["id"] == x["id"] for x in pkgs)
                 and p["name"] not in STATS_PACKAGES]
    sales = [_resource_info(p, r) for p in pkgs for r in p.get("resources", [])]
    sales = [r for r in sales if _importable(r)]
    sales.sort(key=lambda r: (r["year"] or 0, r["modified"]), reverse=True)

    stats = []
    for name, label in STATS_PACKAGES.items():
        try:
            p = m.ckan("package_show", id=name)
        except Exception:
            continue
        stats += [{**_resource_info(p, r), "group": label} for r in p.get("resources", [])
                  if (r.get("format") or "").upper() == "CSV" or r.get("url", "").lower().endswith(".csv")]
    meta_set(db, "sales_resources", sales)
    meta_set(db, "stats_resources", stats)
    db.commit()
    if not sales and not stats:
        raise RuntimeError("no VZD datasets found on data.gov.lv")
    return f"{len(sales)} sales files and {len(stats)} statistics tables found"


# --------------------------------------------------------------------------- CSV helpers

def to_float(v):
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


def to_date(v):
    s = str(v or "").strip()[:10]
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y", "%Y.%m.%d"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    return None


def decode(raw: bytes) -> str:
    for enc in ("utf-8-sig", "cp1257"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def read_csv(text: str):
    sample = text[:20000]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=";,\t|")
    except csv.Error:
        class dialect(csv.excel):
            delimiter = ";" if sample.count(";") > sample.count(",") else ","
    rows = list(csv.reader(io.StringIO(text), dialect))
    return (rows[0], rows[1:]) if rows else ([], [])


def csv_members(raw: bytes, name: str):
    """Yield (member_name, text) for a CSV or a ZIP (possibly nested) of CSVs."""
    if raw[:2] != b"PK":
        yield name, decode(raw)
        return
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        for n in z.namelist():
            low = n.lower()
            if low.endswith(".csv"):
                yield n, decode(z.read(n))
            elif low.endswith(".zip"):
                yield from csv_members(z.read(n), n)


# --------------------------------------------------------------------------- column mapping

# Ordered regex preferences per field, matched against normalised headers
# (lowercase ascii, no spaces). First match wins; a column is used once.
COMMON = {
    "deal_ref": [r"^deaid$", r"darijuma(unikalais)?id"],
    "date": [r"^deadate$", r"^dea.*date", r"darijumadatums", r"darijuma.*datums", r"date$", r"datums$"],
    "price": [r"^deaamount$", r"^dea.*amount", r"darijumasumma", r"darijuma.*(summa|cena)", r"amount",
              r"summa", r"price", r"cena"],
    "address": [r"address", r"adrese", r"addr"],
    "territory": [r"atvk.*(name|nosaukums)", r"territor.*name", r"novads", r"pilseta", r"municip",
                  r"admin.*(name|nosaukums)"],
    "parts": [r"pregcount", r"telpugrupuskaits", r"objectcount", r"objcount"],
    # location: decimal degrees (VZD: DdN / DdE) or LKS grid X / Y
    "lat": [r"^ddn$", r"latitude", r"^lat$", r"platums"],
    "lon": [r"^dde$", r"longitude", r"^lon$", r"^lng$", r"garums"],
    "x": [r"^x$", r"^koordx$", r"^xkoord", r"^coordx$", r"lks.*x$"],
    "y": [r"^y$", r"^koordy$", r"^ykoord", r"^coordy$", r"lks.*y$"],
}
BY_KIND = {
    "apartment": {
        "area": [r"^preg.*area", r"^preg.*space", r"telpugrup.*platib", r"^apt.*area", r"dzivok.*platib",
                 r"kopplatib"],
        "cadastre": [r"^pregcadnr$", r"^preg.*cad", r"telpugrup.*kadastr"],
        "rooms": [r"^preg.*room", r"room", r"istab"],
        "floors_total": [r"floorcount", r"floors$", r"stavuskaits", r"virszemes.*stav"],
        "floor": [r"^preg.*floor", r"floor", r"stavs$", r"stava"],
        "build_year": [r"^(build|bui).*(year|yr)", r"ekspluat", r"exploit", r"buvniec.*gad", r"ekas.*gad"],
    },
    "house": {
        "area": [r"^(build|bui).*(area|space)", r"ekas.*platib", r"buves.*platib", r"kopplatib"],
        "land_area": [r"^par.*area", r"zemes.*platib", r"land.*area"],
        "cadastre": [r"^(build|bui).*cad", r"ekas.*kadastr", r"buves.*kadastr", r"^propcadnr$"],
        "rooms": [r"room", r"istab"],
        "floors_total": [r"floorcount", r"floors$", r"stavuskaits", r"virszemes.*stav"],
        "build_year": [r"^(build|bui).*(year|yr)", r"ekspluat", r"exploit", r"buvniec.*gad", r"ekas.*gad"],
    },
    "land": {
        "area": [r"^par.*area", r"zemes.*platib", r"land.*area", r"platib", r"area"],
        "cadastre": [r"^parcadnr$", r"^par.*cad", r"zemes.*kadastr"],
    },
}
GENERIC = {"area": [r"area", r"platib"], "cadastre": [r"^propcadnr$", r"cadnr", r"kadastr"]}


def map_columns(header, kind):
    normed = [norm(h) for h in header]
    used, out = set(), {}
    fields = {**COMMON, **BY_KIND.get(kind, {})}
    for field, extra in GENERIC.items():
        fields[field] = fields.get(field, []) + extra
    for field, patterns in fields.items():
        for pat in patterns:
            hit = next((i for i, h in enumerate(normed) if i not in used and re.search(pat, h)), None)
            if hit is not None:
                out[field] = hit
                used.add(hit)
                break
    return out


def kind_from_name(name: str, header) -> str | None:
    base = re.sub(r"[^A-Z]", " ", name.rsplit("/", 1)[-1].upper()).split()
    if "TG" in base or "TELPU" in name.upper():
        return "apartment"
    if "ZVB" in base or "BUVES" in norm(name).upper():
        return "house"
    if "ZV" in base:
        return "land"
    h = " ".join(norm(x) for x in header)
    if "preg" in h or "telpugrup" in h:
        return "apartment"
    if "build" in h or "ekas" in h:
        return "house"
    if "par" in h or "zemes" in h:
        return "land"
    return None


def territory_from_address(addr: str | None) -> str | None:
    """'Brīvības iela 1-5, Rīga, LV-1010' -> 'Rīga'; '…, Ādažu pag., Ādažu nov., LV-2164' -> 'Ādažu nov.'"""
    if not addr:
        return None
    parts = [p.strip() for p in addr.split(",") if p.strip() and not re.match(r"^LV-?\d{4}$", p.strip())]
    return parts[-1] if len(parts) >= 2 else None


# --------------------------------------------------------------------------- import

def import_bytes(db, raw: bytes, name: str, kinds=("apartment", "house", "land")) -> str:
    report = []
    total_added = 0
    members = [(m, *read_csv(text)) for m, text in csv_members(raw, name)]
    # Coordinate files (DeaId / cadastre -> location) are applied to the sales files.
    coords: dict[str, tuple[float, float]] = {}
    for member, header, rows in members:
        if header and _is_coordinate_file(header):
            n = _read_coordinates(header, rows, coords)
            report.append(f"{member.rsplit('/', 1)[-1]}: {n} locations")
    for member, header, rows in members:
        if not header or _is_coordinate_file(header):
            continue
        kind = kind_from_name(member, header)
        if kind not in kinds:
            report.append(f"{member.rsplit('/', 1)[-1]}: skipped ({kind or 'unknown type'})")
            continue
        cols = map_columns(header, kind)
        if not {"price", "area"} <= cols.keys():
            report.append(f"{member.rsplit('/', 1)[-1]}: no price/area columns in {header[:20]}")
            continue
        added, skipped, multi, located = _import_rows(db, rows, cols, kind, member, coords)
        total_added += added
        mapping = ", ".join(f"{k}←{header[i]}" for k, i in cols.items())
        report.append(f"{member.rsplit('/', 1)[-1]} ({KINDS[kind]}): {added} sales imported/updated "
                      f"({located} with map location), {multi} multi-property deals and {skipped} "
                      f"incomplete/outlier rows skipped. Columns: {mapping}")
    db.commit()
    meta_set(db, "last_import", {"name": name, "at": datetime.now().isoformat(timespec="minutes"),
                                 "report": report})
    if not report:
        raise RuntimeError("no CSV files found")
    return total_added, report


def _is_coordinate_file(header) -> bool:
    cols = map_columns(header, None)
    has_loc = {"lat", "lon"} <= cols.keys() or {"x", "y"} <= cols.keys()
    return has_loc and "price" not in cols and ("deal_ref" in cols or "cadastre" in cols)


def _row_locations(rows, cols):
    """Yield a (lat, lon) or None per row, from degrees or LKS grid columns."""
    def num(row, k):
        i = cols.get(k)
        return to_float(row[i]) if i is not None and i < len(row) else None

    if {"lat", "lon"} <= cols.keys():
        for r in rows:
            lat, lon = num(r, "lat"), num(r, "lon")
            yield (round(lat, 6), round(lon, 6)) if geo.in_latvia(lat, lon) else None
    elif {"x", "y"} <= cols.keys():
        sample = rows[:2000]
        x_east = geo.x_is_easting([num(r, "x") for r in sample], [num(r, "y") for r in sample])
        for r in rows:
            yield geo.to_latlon(num(r, "x"), num(r, "y"), x_east)
    else:
        for _ in rows:
            yield None


def _read_coordinates(header, rows, coords) -> int:
    cols = map_columns(header, None)
    n = 0
    for row, loc in zip(rows, _row_locations(rows, cols)):
        if not loc:
            continue
        for key in ("deal_ref", "cadastre"):
            i = cols.get(key)
            if i is not None and i < len(row) and row[i].strip():
                coords.setdefault(f"{key}:{row[i].strip()}", loc)
        n += 1
    return n


def _lookup(coords, ref, cadastre):
    if ref and f"deal_ref:{ref}" in coords:
        return coords[f"deal_ref:{ref}"]
    if cadastre:
        # an apartment (17 digits) lies in the building with the first 14 digits
        for c in (cadastre, cadastre[:14], cadastre[:11]):
            if f"cadastre:{c}" in coords:
                return coords[f"cadastre:{c}"]
    return None


def _import_rows(db, rows, cols, kind, source, coords=None):
    coords = coords or {}

    def get(row, k):
        i = cols.get(k)
        return row[i].strip() if i is not None and i < len(row) and row[i] is not None else None

    # A deal (DeaId) covering several objects has one total price — €/m² would be wrong.
    refs = Counter(get(r, "deal_ref") for r in rows) if "deal_ref" in cols else Counter()
    lo, hi = PPM_BOUNDS[kind]
    batch, added, skipped, multi, located = [], 0, 0, 0, 0
    for row, own_loc in zip(rows, _row_locations(rows, cols)):
        ref = get(row, "deal_ref")
        parts = to_float(get(row, "parts"))
        if (ref and refs[ref] > 1) or (parts and parts > 1):
            multi += 1
            continue
        price, area = to_float(get(row, "price")), to_float(get(row, "area"))
        d = to_date(get(row, "date"))
        if not price or not area or not d or (kind != "land" and area < 8):
            skipped += 1
            continue
        ppm = price / area
        if not lo <= ppm <= hi:   # symbolic prices, shares, data errors
            skipped += 1
            continue
        address = get(row, "address")
        territory = get(row, "territory") or territory_from_address(address)
        rooms = to_float(get(row, "rooms"))
        floors = to_float(get(row, "floors_total"))
        year = to_float(get(row, "build_year"))
        cadastre = get(row, "cadastre") or ""
        loc = own_loc or _lookup(coords, ref, cadastre)
        located += bool(loc)
        batch.append((kind, ref, d, price, area, round(ppm, 2), territory, address, cadastre,
                      int(rooms) if rooms else None, get(row, "floor"), int(floors) if floors else None,
                      int(year) if year and 1800 < year < 2100 else None, to_float(get(row, "land_area")),
                      source.rsplit("/", 1)[-1], loc[0] if loc else None, loc[1] if loc else None))
        if len(batch) >= 2000:
            added += _insert(db, batch)
            batch = []
    added += _insert(db, batch)
    return added, skipped, multi, located


def _insert(db, batch):
    if not batch:
        return 0
    # Re-importing a file fills in locations for sales imported before coordinates were available.
    cur = db.executemany("""INSERT INTO sales (kind, deal_ref, deal_date, price, area_m2, price_m2, territory, address,
                            cadastre_no, rooms, floor, floors_total, build_year, land_area_m2, source, lat, lon)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                            ON CONFLICT (kind, deal_date, cadastre_no, price, area_m2) DO UPDATE SET
                            lat = COALESCE(excluded.lat, sales.lat), lon = COALESCE(excluded.lon, sales.lon)
                            WHERE sales.lat IS NULL AND excluded.lat IS NOT NULL""", batch)
    return max(cur.rowcount, 0)


def check_url(url: str):
    host = urllib.parse.urlparse(url).hostname or ""
    if not url.startswith("https://") or not any(host == h or host.endswith("." + h) for h in ALLOWED_HOSTS):
        raise RuntimeError("Only data.gov.lv / vzd.gov.lv files can be imported")


def import_url(db, url: str):
    check_url(url)
    result = import_bytes(db, _http()._get(url, timeout=240), url.rsplit("/", 1)[-1])
    modified = next((r["modified"] for r in meta_get(db, "sales_resources", []) if r["url"] == url), None)
    mark_imported(db, url, modified)
    db.commit()
    return result


# --------------------------------------------------------------------------- official statistics tables

def sync_stats(db, deadline_s=200) -> str:
    start, done, failed = time.time(), 0, 0
    for r in meta_get(db, "stats_resources", []):
        if time.time() - start > deadline_s:
            break
        try:
            check_url(r["url"])
            header, rows = read_csv(decode(_http()._get(r["url"], timeout=30)))
        except Exception:
            failed += 1
            continue
        db.execute("""INSERT INTO stat_tables (name, grp, url, header, rows, fetched_at) VALUES (?,?,?,?,?,?)
                      ON CONFLICT (name) DO UPDATE SET grp = excluded.grp, url = excluded.url,
                      header = excluded.header, rows = excluded.rows, fetched_at = excluded.fetched_at""",
                   (r["name"], r.get("group"), r["url"], json.dumps(header), json.dumps(rows),
                    datetime.now().isoformat(timespec="minutes")))
        done += 1
    db.commit()
    if not done:
        raise RuntimeError("no statistics tables could be downloaded" if failed else "nothing to download")
    return f"{done} official statistics tables updated" + (f", {failed} failed" if failed else "")


def stat_table(db, name):
    r = db.execute("SELECT * FROM stat_tables WHERE name = ?", (name,)).fetchone()
    if not r:
        return None
    header, rows = json.loads(r["header"]), json.loads(r["rows"])
    # numeric columns -> chart series (label column = first non-numeric column)
    numeric = [i for i in range(len(header))
               if rows and sum(to_float(x[i]) is not None for x in rows if i < len(x)) >= 0.8 * len(rows)]
    label_col = next((i for i in range(len(header)) if i not in numeric), 0)
    series = [{"label": header[i], "data": [to_float(x[i]) if i < len(x) else None for x in rows]}
              for i in numeric if not re.search(r"skaits|count|gads|year", norm(header[i]))][:6]
    return {"name": r["name"], "group": r["grp"], "url": r["url"], "fetched_at": r["fetched_at"],
            "header": header, "rows": rows, "labels": [x[label_col] if label_col < len(x) else "" for x in rows],
            "series": series}


def stat_tables(db):
    return db.execute("SELECT name, grp, fetched_at FROM stat_tables ORDER BY grp, name").fetchall()


# --------------------------------------------------------------------------- explorer

FILTERS = ("kind", "territory", "q", "rooms", "area_min", "area_max", "price_min", "price_max", "since", "until",
           "year_min", "year_max")


STAT_COLS = "deal_date, price, area_m2, price_m2, rooms, territory, build_year"


def query(db, f, limit=200000, columns="*"):
    where, params = ["1=1"], []
    if f.get("kind"):
        where.append("kind = ?"), params.append(f["kind"])
    if f.get("territory"):
        where.append("territory = ?"), params.append(f["territory"])
    if f.get("q"):
        where.append("(LOWER(address) LIKE ? OR cadastre_no LIKE ?)")
        params += [f"%{f['q'].lower()}%", f"{f['q']}%"]
    if f.get("rooms"):
        if str(f["rooms"]).endswith("+"):
            where.append("rooms >= ?"), params.append(int(str(f["rooms"])[:-1]))
        else:
            where.append("rooms = ?"), params.append(int(f["rooms"]))
    for key, col, op in (("area_min", "area_m2", ">="), ("area_max", "area_m2", "<="),
                         ("price_min", "price", ">="), ("price_max", "price", "<="),
                         ("year_min", "build_year", ">="), ("year_max", "build_year", "<=")):
        v = to_float(f.get(key))
        if v is not None:
            where.append(f"{col} {op} ?"), params.append(v)
    if f.get("located"):
        where.append("lat IS NOT NULL")
    if f.get("since"):
        where.append("deal_date >= ?"), params.append(f["since"])
    if f.get("until"):
        where.append("deal_date <= ?"), params.append(f["until"])
    sql = f"SELECT {columns} FROM sales WHERE {' AND '.join(where)} ORDER BY deal_date DESC LIMIT {int(limit)}"
    return db.execute(sql, params).fetchall()


def _q(d: str) -> str:
    return f"{d[:4]}-Q{(int(d[5:7]) - 1) // 3 + 1}"


def _stats(vals):
    vals = sorted(v for v in vals if v is not None)
    if not vals:
        return None
    q = statistics.quantiles(vals, n=4) if len(vals) >= 4 else [vals[0], statistics.median(vals), vals[-1]]
    return {"median": statistics.median(vals), "mean": statistics.fmean(vals), "p25": q[0], "p75": q[-1],
            "min": vals[0], "max": vals[-1]}


def summarize(rows):
    if not rows:
        return None
    names = list(rows[0].keys())
    D, P, A, M, R, T, Y = (names.index(c) for c in
                           ("deal_date", "price", "area_m2", "price_m2", "rooms", "territory", "build_year"))
    data = [tuple(r) for r in rows]   # plain tuples: much faster than by-name access on 100k rows
    med = statistics.median
    out = {"count": len(data), "price": _stats([r[P] for r in data]),
           "ppm": _stats([r[M] for r in data]), "area": _stats([r[A] for r in data])}

    def breakdown(key):
        groups = defaultdict(list)
        for r in data:
            groups[key(r)].append(r)
        return [{"key": k, "count": len(v), "ppm": med([r[M] for r in v]), "price": med([r[P] for r in v]),
                 "area": sum(r[A] for r in v) / len(v)} for k, v in groups.items()]

    out["quarters"] = sorted(({**g, "q": g["key"]} for g in breakdown(lambda r: _q(r[D]))), key=lambda g: g["q"])
    out["by_rooms"] = sorted(breakdown(lambda r: min(r[R], 5) if r[R] else None),
                             key=lambda x: (x["key"] is None, x["key"] or 0))
    out["by_territory"] = sorted(breakdown(lambda r: r[T] or "—"), key=lambda x: -x["count"])[:20]
    out["by_era"] = sorted(breakdown(lambda r: _era(r[Y])), key=lambda x: x["key"])
    return out


def _era(y):
    if not y:
        return "9 unknown"
    for limit, label in ((1945, "1 before 1945"), (1960, "2 1945–1959"), (1993, "3 1960–1992 (Soviet series)"),
                         (2010, "4 1993–2009"), (2100, "5 2010+ (new)")):
        if y < limit:
            return label
    return "9 unknown"


def territories(db, kind=None):
    sql = "SELECT territory, count(*) AS n FROM sales WHERE territory IS NOT NULL"
    params = []
    if kind:
        sql += " AND kind = ?"
        params.append(kind)
    return db.execute(sql + " GROUP BY territory ORDER BY n DESC LIMIT 300", params).fetchall()


def coverage(db):
    return db.execute("""SELECT kind, count(*) AS n, min(deal_date) AS d0, max(deal_date) AS d1
                         FROM sales GROUP BY kind ORDER BY kind""").fetchall()


# --------------------------------------------------------------------------- comparables for a property

def comparables(db, p, months=24):
    """Recent apartment sales in the same city (and district if enough) with similar size."""
    if not p["area_m2"]:
        return []
    since = fin.add_months(date.today(), -months).isoformat()
    area = float(p["area_m2"])
    kind = "house" if (p["kind"] or "") == "house" else "apartment"
    rows = db.execute("""SELECT * FROM sales WHERE kind = ? AND deal_date >= ? AND area_m2 BETWEEN ? AND ?
                         ORDER BY deal_date DESC LIMIT 20000""", (kind, since, area * 0.7, area * 1.3)).fetchall()
    key = norm(p["city"] or "")[:5]
    out = [r for r in rows if key and key in norm((r["territory"] or "") + (r["address"] or ""))]
    if p["rooms"]:
        same = [r for r in out if r["rooms"] == p["rooms"]]
        if len(same) >= 8:
            out = same
    if p["address"]:
        street = norm(re.split(r"\d", p["address"])[0])
        local = [r for r in out if street and street in norm(r["address"])]
        if len(local) >= 5:
            return local
    return out


# --------------------------------------------------------------------------- map

MAP_LIMIT = 40000


def located_count(db):
    return db.execute("SELECT count(*) FROM sales WHERE lat IS NOT NULL").fetchone()[0]


def map_points(db, f):
    """Sales grouped per building location, compact for the map:
    {"points": [[lat, lon, [[date, address, area, rooms, floor, price, ppm, year, cadastre], ...]], ...]}"""
    rows = query(db, {**f, "located": True}, limit=MAP_LIMIT,
                 columns="lat, lon, deal_date, address, area_m2, rooms, floor, floors_total, price, price_m2, "
                         "build_year, cadastre_no")
    groups = defaultdict(list)
    for r in rows:
        r = tuple(r)
        floor = f"{r[6] or ''}{'/' + str(r[7]) if r[7] else ''}"
        groups[(r[0], r[1])].append([r[2], r[3] or "", round(r[4], 1), r[5], floor, round(r[8]), round(r[9]),
                                     r[10], r[11]])
    return {"points": [[lat, lon, items] for (lat, lon), items in groups.items()],
            "count": len(rows), "truncated": len(rows) >= MAP_LIMIT}


# --------------------------------------------------------------------------- automatic sync

def _hours_since(iso):
    if not iso:
        return 1e9
    return (datetime.now() - datetime.fromisoformat(iso)).total_seconds() / 3600


def pending_files(db, years=5):
    """Sales files not imported yet (or changed since), newest first, limited to the last `years`."""
    done = meta_get(db, "imported", {})
    newest = max((r["year"] or 0 for r in meta_get(db, "sales_resources", [])), default=0)
    out = []
    for r in meta_get(db, "sales_resources", []):
        if r["year"] and r["year"] <= newest - years:
            continue
        prev = done.get(r["url"])
        changed = prev is not None and r["modified"] and r["modified"] != prev.get("modified")
        # the running year's file grows every month: refresh it monthly even if `modified` is missing
        stale = prev is not None and r["year"] == date.today().year and _hours_since(prev.get("at")) > 24 * 25
        if prev is None or changed or stale:
            out.append(r)
    return out


def mark_imported(db, url, modified=None):
    done = meta_get(db, "imported", {})
    done[url] = {"modified": modified, "at": datetime.now().isoformat(timespec="minutes")}
    meta_set(db, "imported", done)


def auto_sync(db, years=5, budget_s=200) -> list[str]:
    """One step of hands-free syncing (run hourly by Vercel Cron):
    market series and dataset list once a day, then import pending sales files
    while time allows. Each step commits, so an interrupted run loses nothing."""
    from . import market
    start, log = time.time(), []
    state = meta_get(db, "sync", {})
    if _hours_since(state.get("daily")) > 23:
        for name, ok, msg in market.refresh_all(db):
            log.append(f"{name}: {msg}")
        state["daily"] = datetime.now().isoformat(timespec="minutes")
        meta_set(db, "sync", state)
        db.commit()
    for r in pending_files(db, years):
        if time.time() - start > budget_s * 0.4:   # leave room for one more large file
            break
        try:
            added, _report = import_url(db, r["url"])
            mark_imported(db, r["url"], r["modified"])
            log.append(f"{r['name']}: {added} sales imported/updated")
        except Exception as e:
            db.rollback()
            log.append(f"{r['name']}: failed — {e}")
            state.setdefault("errors", {})[r["url"]] = str(e)[:300]
            break
        finally:
            db.commit()
    state["last_run"] = datetime.now().isoformat(timespec="minutes")
    state["last_log"] = log[-12:]
    state["pending"] = len(pending_files(db, years))
    meta_set(db, "sync", state)
    db.commit()
    return log
