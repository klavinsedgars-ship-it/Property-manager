import io
import zipfile

from app import vzd

# Coded headers as in VZD NĪTIS files (DeaId, DeaDate, DeaAmount, PregCadNr ...), comma separated.
TG = """﻿DeaId,DeaDate,DeaAmount,PregCadNr,PregCount,PregArea,PregRoomCount,PregFloor,BuildFloorCount,BuildExploitYear,Address
1001,2025-03-14,58000.00,01000123456001001,1,52.1,2,3,5,1968,"Dzelzavas iela 12-4, Rīga, LV-1084"
1002,2025-05-02,62000.00,01000123456001002,1,48,2,1,5,1968,"Brīvības iela 1-1, Rīga, LV-1010"
1003,2025-06-01,90000.00,01000123456001003,2,40,1,2,5,1968,"Brīvības iela 1-2, Rīga, LV-1010"
1004,2025-06-03,500.00,01000123456001004,1,50,2,1,5,1968,"Share, Rīga, LV-1010"
1005,2025-07-09,140000.00,13000123456001001,1,61.3,3,4,9,2019,"Jomas iela 5-7, Jūrmala, LV-2015"
""".encode("utf-8")

ZVB = """DeaId,DeaDate,DeaAmount,PropCadNr,ParCadNr,ParArea,BuildCadNr,BuildArea,BuildExploitYear,Address
2001,2024-11-20,185000,80760030001,80760030001,1200,80760030001001,145.5,2005,"Liepu iela 3, Ādaži, Ādažu pag., Ādažu nov., LV-2164"
2002,2024-12-01,300000,80760030002,80760030002,1500,80760030002001,120,1999,"Bērzu iela 1, Ādaži, Ādažu nov., LV-2164"
2002,2024-12-01,300000,80760030002,80760030003,900,80760030003001,80,1999,"Bērzu iela 1, Ādaži, Ādažu nov., LV-2164"
""".encode("utf-8")

ZV = """DeaId,DeaDate,DeaAmount,ParCadNr,ParArea,Address
3001,2024-10-01,15000,80760030009,1500,"Ādažu nov., LV-2164"
""".encode("utf-8")

LATVIAN = """Darījuma datums;Telpu grupas kadastra apzīmējums;Adrese;Telpu grupas platība, m2;Istabu skaits;Stāvs;Darījuma summa, EUR
14.05.2023;01009999002001;Ģertrūdes iela 10-3, Rīga, LV-1010;48;2;1;62 000,00
""".encode("utf-8")


def make_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("NITIS_2025/TG_2025.csv", TG)
        z.writestr("NITIS_2025/ZVB_2025.csv", ZVB)
        z.writestr("NITIS_2025/ZV_2025.csv", ZV)
    return buf.getvalue()


def test_column_mapping_coded_headers():
    header = TG.decode("utf-8-sig").splitlines()[0].split(",")
    cols = vzd.map_columns(header, "apartment")
    names = {k: header[i] for k, i in cols.items()}
    assert names["date"] == "DeaDate" and names["price"] == "DeaAmount"
    assert names["area"] == "PregArea" and names["cadastre"] == "PregCadNr"
    assert names["rooms"] == "PregRoomCount" and names["floor"] == "PregFloor"
    assert names["floors_total"] == "BuildFloorCount" and names["build_year"] == "BuildExploitYear"
    assert names["parts"] == "PregCount" and names["deal_ref"] == "DeaId"

    hdr = ZVB.decode().splitlines()[0].split(",")
    names = {k: hdr[i] for k, i in vzd.map_columns(hdr, "house").items()}
    assert names["area"] == "BuildArea" and names["land_area"] == "ParArea" and names["cadastre"] == "BuildCadNr"


def test_territory_from_address():
    assert vzd.territory_from_address("Brīvības iela 1-5, Rīga, LV-1010") == "Rīga"
    assert vzd.territory_from_address("Liepu iela 3, Ādaži, Ādažu pag., Ādažu nov., LV-2164") == "Ādažu nov."
    assert vzd.territory_from_address(None) is None


def test_import_zip_all_kinds(db):
    added, report = vzd.import_bytes(db, make_zip(), "NITIS_CSV_2025.zip")
    rows = {r["cadastre_no"]: r for r in db.execute("SELECT * FROM sales")}
    # TG: 1003 is a 2-apartment deal, 1004 a symbolic price -> skipped
    assert "01000123456001001" in rows and "01000123456001003" not in rows and "01000123456001004" not in rows
    a = rows["01000123456001001"]
    assert (a["kind"], a["rooms"], a["floors_total"], a["build_year"], a["territory"]) == ("apartment", 2, 5, 1968, "Rīga")
    assert round(a["price_m2"]) == round(58000 / 52.1)
    # ZVB: deal 2002 covers two buildings -> skipped
    houses = [r for r in rows.values() if r["kind"] == "house"]
    assert len(houses) == 1 and houses[0]["land_area_m2"] == 1200 and houses[0]["territory"] == "Ādažu nov."
    assert any(r["kind"] == "land" for r in rows.values())
    assert added == 5 and len(report) == 3
    # idempotent
    assert vzd.import_bytes(db, make_zip(), "again.zip")[0] == 0


def test_import_latvian_headers(db):
    added, _ = vzd.import_bytes(db, LATVIAN, "TG_2023.csv")
    r = db.execute("SELECT * FROM sales").fetchone()
    assert added == 1 and r["deal_date"] == "2023-05-14" and r["price"] == 62000 and r["rooms"] == 2


def test_query_and_summary(db):
    vzd.import_bytes(db, make_zip(), "x.zip")
    rows = vzd.query(db, {"kind": "apartment", "territory": "Rīga"})
    assert len(rows) == 2
    assert len(vzd.query(db, {"kind": "apartment", "q": "dzelzavas"})) == 1
    assert len(vzd.query(db, {"kind": "apartment", "rooms": "3+"})) == 1
    s = vzd.summarize(vzd.query(db, {"kind": "apartment"}))
    assert s["count"] == 3 and s["quarters"][0]["q"] == "2025-Q1"
    assert {r["key"] for r in s["by_territory"]} == {"Rīga", "Jūrmala"}


def test_stat_table(db):
    db.execute("INSERT INTO stat_tables (name, grp, url, header, rows, fetched_at) VALUES (?,?,?,?,?,?)",
               ("Rīga", "Average apartment prices", "https://data.gov.lv/x.csv",
                '["Periods", "Vidējā cena EUR/m2", "Darījumu skaits"]',
                '[["2024 1. pusg.", "1 150", "800"], ["2024 2. pusg.", "1 210", "850"]]', "now"))
    t = vzd.stat_table(db, "Rīga")
    assert t["labels"] == ["2024 1. pusg.", "2024 2. pusg."]
    assert [s["label"] for s in t["series"]] == ["Vidējā cena EUR/m2"]
    assert t["series"][0]["data"] == [1150, 1210]


def test_check_url():
    vzd.check_url("https://data.gov.lv/dati/dataset/x/resource/y/download/a.zip")
    for bad in ("http://data.gov.lv/a", "https://evil.com/data.gov.lv", "https://data.gov.lv.evil.com/a"):
        try:
            vzd.check_url(bad)
            raise AssertionError(bad)
        except RuntimeError:
            pass


def test_pages(client, app):
    with app.app_context():
        from app.db import get_db
        vzd.import_bytes(get_db(), make_zip(), "x.zip")
    for url in ("/market/sales?kind=apartment&since=", "/market/sales?kind=house&since=", "/market/sales?kind=",
                "/market/sales", "/market/sales.csv?kind=&since=", "/market/stats", "/market"):
        r = client.get(url)
        assert r.status_code == 200, url
    page = client.get("/market/sales?kind=apartment&since=").get_data(as_text=True)
    assert "Dzelzavas" in page and "Median €/m²" in page


def test_discover_with_fake_ckan(db, monkeypatch):
    from app import market

    def fake_ckan(action, **p):
        if action == "package_show" and p["id"] == vzd.DEALS_PACKAGE:
            return {"id": "1", "name": p["id"], "title": "NĪTIS atvērtie dati", "resources": [
                {"name": "2024. gadā NĪTIS reģistrētie darījumi ar NĪ", "format": "ZIP",
                 "url": "https://data.gov.lv/dati/dataset/x/resource/a/download/nitis_csv_2024.zip"},
                {"name": "2025. gadā NĪTIS reģistrētie darījumi ar NĪ", "format": "ZIP",
                 "url": "https://data.gov.lv/dati/dataset/x/resource/b/download/nitis_csv_2025.zip"},
                {"name": "2025 XLSX", "format": "XLSX", "url": "https://data.gov.lv/x.xlsx"}]}
        if action == "package_show" and p["id"] in vzd.STATS_PACKAGES:
            return {"id": p["id"], "name": p["id"], "title": p["id"], "resources": [
                {"name": "Rīga", "format": "CSV", "url": f"https://data.gov.lv/{p['id']}/riga.csv"}]}
        if action == "package_search":
            return {"results": []}
        raise RuntimeError("unexpected")

    monkeypatch.setattr(market, "ckan", fake_ckan)
    msg = vzd.discover(db)
    res = vzd.meta_get(db, "sales_resources")
    assert [r["year"] for r in res] == [2025, 2024]          # XLSX skipped, newest first
    assert "2 sales files and 3 statistics tables" in msg


COORDS_DD = """DeaId,BuildCadNr,DdN,DdE
1001,01000123456,56.9600,24.1900
1002,01000123457,56.9514,24.1133
""".encode("utf-8")

# Same buildings as LKS-92 grid (X = northing, Y = easting — Latvian convention), keyed by building cadastre
COORDS_XY = """BuildCadNr,X,Y
01000123456,312690.1,511040.2
13000123456,310000.0,470000.0
""".encode("utf-8")


def _zip(**files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for name, data in files.items():
            z.writestr(name.replace("__", "/"), data)
    return buf.getvalue()


def test_locations_from_coordinate_file(db):
    vzd.import_bytes(db, _zip(**{"TG_2025.csv": TG, "TG_koord.csv": COORDS_DD}), "a.zip")
    r = {x["cadastre_no"]: x for x in db.execute("SELECT * FROM sales")}
    assert (r["01000123456001001"]["lat"], r["01000123456001001"]["lon"]) == (56.96, 24.19)
    assert r["13000123456001001"]["lat"] is None     # Jūrmala deal has no coordinates in this file


def test_locations_from_lks_grid_by_building_cadastre(db):
    vzd.import_bytes(db, _zip(**{"TG_2025.csv": TG, "koordinates.csv": COORDS_XY}), "b.zip")
    r = {x["cadastre_no"]: x for x in db.execute("SELECT * FROM sales")}
    lat, lon = r["01000123456001001"]["lat"], r["01000123456001001"]["lon"]
    assert 56.9 < lat < 57.0 and 24.1 < lon < 24.25               # Rīga
    lat, lon = r["13000123456001001"]["lat"], r["13000123456001001"]["lon"]
    assert 56.9 < lat < 57.0 and 23.4 < lon < 23.6                # Jūrmala area


def test_reimport_fills_missing_locations(db):
    vzd.import_bytes(db, TG, "TG_2025.csv")
    assert vzd.located_count(db) == 0
    vzd.import_bytes(db, _zip(**{"TG_2025.csv": TG, "TG_koord.csv": COORDS_DD}), "a.zip")
    assert vzd.located_count(db) == 2
    assert db.execute("SELECT count(*) FROM sales").fetchone()[0] == 3


def test_map_endpoint(client, app):
    with app.app_context():
        from app.db import get_db
        vzd.import_bytes(get_db(), _zip(**{"TG_2025.csv": TG, "TG_koord.csv": COORDS_DD}), "a.zip")
    d = client.get("/market/map.json?kind=apartment&since=").get_json()
    assert d["count"] == 2 and len(d["points"]) == 2
    lat, lon, items = d["points"][0]
    assert items[0][6] > 0 and items[0][1]
    page = client.get("/market/map?kind=apartment&since=").get_data(as_text=True)
    assert 'id="map"' in page and "leaflet.js" in page
