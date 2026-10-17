from app import ai, analyzer, vzd
from app.db import settings

SS_PAGE = """<html><head><title>SS.LV Dzīvokļi - Rīga, Purvciems, Pārdod</title></head><body>
<div id="msg_div_msg">Pārdod gaišu 2-istabu dzīvokli. Nepieciešams kosmētiskais remonts.<br>Logi uz pagalmu.
<table class="options_list"><tr><td>
<table>
<tr><td class="ads_opt_name">Pilsēta:</td><td class="ads_opt" id="tdo_20"><b>Rīga</b></td></tr>
<tr><td class="ads_opt_name">Rajons:</td><td class="ads_opt" id="tdo_856"><b>Purvciems</b></td></tr>
<tr><td class="ads_opt_name">Iela:</td><td class="ads_opt" id="tdo_11"><b>Dzelzavas 10</b> [Karte]</td></tr>
<tr><td class="ads_opt_name">Istabas:</td><td class="ads_opt" id="tdo_1">2</td></tr>
<tr><td class="ads_opt_name">Platība:</td><td class="ads_opt" id="tdo_3">52 m²</td></tr>
<tr><td class="ads_opt_name">Stāvs / Stāvi:</td><td class="ads_opt" id="tdo_4">3/9/lifts</td></tr>
<tr><td class="ads_opt_name">Sērija:</td><td class="ads_opt" id="tdo_6">602.</td></tr>
<tr><td class="ads_opt_name">Mājas tips:</td><td class="ads_opt" id="tdo_2">Paneļu</td></tr>
</table></td></tr></table>
<table><tr><td class="ads_price" id="tdo_8">62 000 € (1 192.31 €/m²)</td></tr></table>
</div></body></html>"""

SALES = "DeaId,DeaDate,DeaAmount,PregCadNr,PregCount,PregArea,PregRoomCount,BuildExploitYear,Address\n" + "\n".join(
    f"{i},2026-0{1 + i % 8}-1{i % 9},{int(a * ppm)},0100{i:013d},1,{a},2,1979,\"Dzelzavas iela {i}-1, Rīga, LV-1084\""
    for i, (a, ppm) in enumerate([(50, 1300), (54, 1400), (48, 1350), (55, 1250), (52, 1500), (49, 1450),
                                  (51, 1380), (53, 1420), (50, 1320), (52, 1360)], start=1))


def test_parse_ss():
    d = analyzer.parse_ss(SS_PAGE)
    assert d["price"] == 62000 and d["area_m2"] == 52 and d["rooms"] == 2
    assert (d["floor"], d["floors_total"]) == (3, 9)
    assert d["city"] == "Rīga" and d["district"] == "Purvciems" and d["street"] == "Dzelzavas 10"
    assert d["building_series"] == "602." and "kosmētiskais" in d["description"]


def test_era_and_defaults(db):
    assert analyzer.era_of("602.", None) == "soviet"
    assert analyzer.era_of("Jaunais projekts", None) == "new"
    assert analyzer.era_of(None, 1935) == "pre"
    inp = analyzer.default_inputs({"price": 62000, "area_m2": 52, "city": "Rīga", "building_series": "602.",
                                   "condition": "needs_cosmetic"}, settings(db), db)
    assert inp["rent"] == 495 and inp["renovation"] == 7800


def test_analyze_with_market(db):
    vzd.import_bytes(db, SALES.encode(), "TG_2026.csv")
    s = settings(db)
    inp = analyzer.default_inputs({"price": 62000, "area_m2": 52, "rooms": 2, "city": "Rīga",
                                   "street": "Dzelzavas 10", "building_series": "602."}, s, db)
    r = analyzer.analyze(db, inp, s)
    m = r["market"]
    assert m["n"] == 10 and 1300 < m["median_m2"] < 1450
    assert r["price_m2"] == 62000 / 52
    assert 0 < r["returns"]["gross_yield"] < 0.2 and r["returns"]["y10"]["irr"] is not None
    pa = r["price_advice"]
    assert pa["max_price"] <= m["fair_value"] + 500 and pa["opening_offer"] <= pa["max_price"]
    assert 0 <= r["score"] <= 100 and r["verdict"] in ("good", "fair", "poor")


def test_flow_ss_link_without_ai(client, monkeypatch):
    monkeypatch.setattr(analyzer, "fetch", lambda url: SS_PAGE)
    monkeypatch.setattr(ai, "available", lambda: False)
    r = client.post("/analyzer", data={"url": "https://www.ss.lv/msg/lv/real-estate/flats/riga/purvciems/abc.html"})
    assert r.status_code == 302
    page = client.get(r.headers["Location"]).get_data(as_text=True)
    assert "Recommended max price" in page and "€62" in page
    # edit assumptions and recalc
    r = client.post(r.headers["Location"], data={"price": "58000", "area_m2": "52", "rent": "520", "renovation": "5000",
                                                 "management_m2": "0.6", "ltv": "70", "loan_rate": "5", "loan_years": "25",
                                                 "target_yield": "6", "city": "Rīga", "condition": "good", "kind": "apartment",
                                                 "use_loan": "1"})
    assert r.status_code == 302
    assert "€58" in client.get(r.headers["Location"]).get_data(as_text=True)
    assert "Purvciems" in client.get("/analyzer").get_data(as_text=True)


def test_flow_pasted_text_with_ai(client, monkeypatch):
    monkeypatch.setattr(ai, "available", lambda: True)
    monkeypatch.setattr(ai, "extract_listing", lambda text, url=None: {
        "is_listing": True, "deal_type": "sale", "kind": "apartment", "title": "2-istabu Purvciemā",
        "price": 65000, "area_m2": 50, "rooms": 2, "city": "Rīga", "district": "Purvciems", "street": None,
        "building_series": "602", "build_year": None, "condition": "good", "red_flags": []})
    monkeypatch.setattr(ai, "recommend", lambda listing, analysis, desc="": {
        "verdict": "fair_deal", "headline": "Decent flat, slightly pricey", "summary": "Yield is close to target.",
        "risks": ["Soviet panel building"], "negotiation": "Offer €60 000."})
    r = client.post("/analyzer", data={"text": "Pārdod 2-istabu dzīvokli Purvciemā, 50 m², 65 000 EUR"})
    page = client.get(r.headers["Location"]).get_data(as_text=True)
    assert "Decent flat, slightly pricey" in page and "Offer €60 000." in page


def test_facebook_login_wall(monkeypatch):
    monkeypatch.setattr(analyzer, "fetch", lambda url: "<html><title>Facebook</title>Log in to Facebook</html>")
    listing, text, notes = analyzer.extract("https://www.facebook.com/marketplace/item/123", None, use_ai=False)
    assert any("Facebook requires login" in n for n in notes)


def test_fetch_rejects_private_hosts():
    import pytest
    for bad in ("http://localhost/x", "http://127.0.0.1/x", "file:///etc/passwd", "http://192.168.1.1/"):
        with pytest.raises(RuntimeError):
            analyzer.fetch(bad)
