from app import market

VZD_CSV = """﻿Darījuma datums;Kadastra numurs;Adrese;Novads;Telpu grupas platība, m2;Istabu skaits;Stāvs;Darījuma summa, EUR
2025-03-14;01009999001;Dzelzavas iela 12-4, Rīga;Rīga;52,1;2;3;58 000,00
14.05.2025;01009999002;Brīvības iela 1-1, Rīga;Rīga;48;2;1;62000
2025-06-01;01009999003;Share;Rīga;50;2;1;500
""".encode("utf-8")


def test_map_columns_latvian_headers():
    header = VZD_CSV.decode("utf-8-sig").splitlines()[0].split(";")
    cols = market.map_columns(header)
    assert header[cols["date"]] == "Darījuma datums"
    assert header[cols["price"]] == "Darījuma summa, EUR"
    assert header[cols["area"]].startswith("Telpu grupas platība")
    assert header[cols["cadastre"]] == "Kadastra numurs"
    assert header[cols["territory"]] == "Novads"


def test_import_deals(db):
    msg = market.import_deals_bytes(db, VZD_CSV, "test.csv")
    assert "Imported 2" in msg
    rows = db.execute("SELECT * FROM market_deals ORDER BY deal_date").fetchall()
    assert rows[0]["deal_date"] == "2025-03-14"
    assert rows[0]["price"] == 58000
    assert rows[0]["area_m2"] == 52.1
    assert rows[1]["deal_date"] == "2025-05-14"
    # re-import is idempotent
    assert "Imported 0" in market.import_deals_bytes(db, VZD_CSV, "test.csv")


def test_parse_jsonstat2_and_pick_hpi():
    d = {
        "id": ["ContentsCode", "TIME"], "size": [3, 2],
        "dimension": {
            "ContentsCode": {"category": {"index": {"A": 0, "B": 1, "C": 2},
                                          "label": {"A": "House price index (2015=100), total",
                                                    "B": "House price index (2025=100), total",
                                                    "C": "Change over previous quarter, %"}}},
            "TIME": {"category": {"index": ["2025Q1", "2025Q2"], "label": {"2025Q1": "2025Q1", "2025Q2": "2025Q2"}}},
        },
        "role": {"time": ["TIME"]},
        "value": [180, 185, 98, 100.5, 1.2, 2.8],
    }
    flat = market.parse_jsonstat2(d)
    assert len(flat) == 6
    picked = market.pick_hpi_series(d)
    assert list(picked) == ["hpi_total"]
    label, pts = picked["hpi_total"]
    assert "2025=100" in label
    assert pts == [("2025-Q1", 98), ("2025-Q2", 100.5)]


def test_index_estimate(db):
    db.executemany("INSERT INTO market_data (series, period, value) VALUES ('hpi_total', ?, ?)",
                   [("2020-Q1", 100), ("2020-Q2", 102), ("2025-Q2", 150)])
    p = {"purchase_date": "2020-02-10", "purchase_price": 40000, "building_series": "602"}
    v, src = market.index_estimate(db, p, 5000)
    assert v == 40000 * 1.5 + 5000
    assert "2025-Q2" in src
