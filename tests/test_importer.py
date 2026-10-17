import io

from app import importer

SHEET = ("Darba veids\tNosaukums\tApjoms\tDarba alga\tMateriāli\tSumma kopā\n"
         "Virtuve IKEA\t\t\t\t\t1900\nLedusskapis\t\t\t\t\t200\nFlīzes vannasistaba\t\t\t\t\t450.00\n"
         "Darbs Ukrainis\t\t0.00\t0.00\t0.00\t1870.00\n\t\t0.00\t0.00\t0.00\t2700.00\n"
         "\t\t0\t0\t0\t7120.00\n")


def test_parse_pasted_sheet():
    rows = importer.parse(SHEET)
    assert [r["amount"] for r in rows] == [1900, 200, 450, 1870, 2700]          # total row dropped
    cats = {r["description"]: r["category"] for r in rows}
    assert cats["Ledusskapis"] == "Furniture & appliances"
    assert cats["Flīzes vannasistaba"] == "Renovation – materials"
    assert cats["Darbs Ukrainis"] == cats["Darbs Ukrainis (cont.)"] == "Renovation – labour"


def test_parse_csv_with_categories_and_dates():
    rows = importer.parse("date,category,amount,description\n2026-05-02,Renovation – labour,400,Santehniķis\n"
                          "02.05.2026,Repairs & maintenance,\"1 234,50\",Boileris\n")
    assert rows[0]["date"] == "2026-05-02" and rows[0]["category"] == "Renovation – labour"
    assert rows[1]["date"] == "2026-05-02" and rows[1]["amount"] == 1234.5 and rows[1]["category"] == "Repairs & maintenance"


def test_import_and_undo(client):
    client.post("/properties/new", data={"name": "Tallinas 41-24", "purchase_price": "60000"})
    r = client.post("/properties/1/transactions/import", data={"date": "2026-04-01", "text": SHEET})
    assert r.status_code == 302 and "batch=" in r.headers["Location"]
    page = client.get(r.headers["Location"]).get_data(as_text=True)
    assert "Undo this import" in page and "Darbs Ukrainis" in page
    overview = client.get("/properties/1").get_data(as_text=True)
    assert "€67" in overview                       # cost basis 60 000 + 7 120 improvements
    batch = r.headers["Location"].split("batch=")[1]
    client.post(f"/properties/1/transactions/undo/{batch}")
    assert "Darbs Ukrainis" not in client.get("/properties/1?tab=money").get_data(as_text=True)


def test_import_csv_file(client):
    client.post("/properties/new", data={"name": "X"})
    data = {"date": "2026-01-10", "file": (io.BytesIO("amount,description\n100,Krāsa\n".encode()), "x.csv")}
    r = client.post("/properties/1/transactions/import", data=data, content_type="multipart/form-data")
    assert r.status_code == 302
    assert "Krāsa" in client.get("/properties/1?tab=money").get_data(as_text=True)
