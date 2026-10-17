import io


def test_full_flow(client):
    assert client.get("/").status_code == 200
    r = client.post("/properties/new", data={"name": "Test flat", "city": "Rīga", "status": "renovating",
                                             "purchase_date": "2022-01-10", "purchase_price": "50 000",
                                             "purchase_costs": "900", "area_m2": "50,5"})
    assert r.status_code == 302
    loc = r.headers["Location"]
    pid = int(loc.rstrip("/").split("/")[-1])

    r = client.post(f"/properties/{pid}/transactions/new",
                    data={"date": "2022-02-01", "category": "Renovation – materials", "amount": "4000",
                          "receipt": (io.BytesIO(b"%PDF-1.4"), "invoice.pdf")},
                    content_type="multipart/form-data")
    assert r.status_code == 302

    r = client.post(f"/properties/{pid}/leases/new", data={"tenant_id": "new", "new_tenant_name": "Ilze",
                                                          "start_date": "2022-03-01", "rent": "450",
                                                          "deposit": "450", "payment_day": "1"})
    assert r.status_code == 302
    client.post(f"/properties/{pid}/transactions/new",
                data={"date": "2022-03-02", "category": "Rent", "amount": "450", "lease_id": "1"})

    for tab in ("overview", "money", "tenants", "documents", "sale"):
        r = client.get(f"/properties/{pid}?tab={tab}")
        assert r.status_code == 200, tab
    page = client.get(f"/properties/{pid}").get_data(as_text=True)
    assert "€54" in page  # cost basis 50 000 + 900 + 4 000
    assert "invoice.pdf" in client.get("/documents").get_data(as_text=True)

    for url in ("/analytics", "/analytics?period=year", "/tenants", "/tenants/1", "/market", "/settings",
                "/transactions.csv"):
        assert client.get(url).status_code == 200, url


def test_password_protection(tmp_path):
    from app import create_app
    app = create_app({"DATABASE": str(tmp_path / "x.db"), "UPLOAD_DIR": str(tmp_path / "u"), "PASSWORD": "s3cret"})
    c = app.test_client()
    assert c.get("/").status_code == 401
    import base64
    h = {"Authorization": "Basic " + base64.b64encode(b"me:s3cret").decode()}
    assert c.get("/", headers=h).status_code == 200
