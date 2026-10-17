from app import market

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
