import io
import json

import pytest

from app import create_app, storage


class FakeBlob:
    """Stands in for urllib.request.urlopen against the Vercel Blob API."""

    def __init__(self):
        self.files, self.calls = {}, []

    def __call__(self, req, timeout=None):
        url, method = req.full_url, req.get_method()
        headers = {k.lower(): v for k, v in req.header_items()}
        self.calls.append((method, url, headers))
        assert headers["authorization"] == "Bearer vercel_blob_rw_STORE123_secret"
        if method == "PUT":
            assert headers["x-vercel-blob-access"] == "private"
            assert headers["x-vercel-blob-store-id"] == "STORE123"
            path = url.split("pathname=")[1]
            blob_url = f"https://store123.private.blob.vercel-storage.com/{path}"
            self.files[blob_url] = (req.data, headers["x-content-type"])
            return Resp(json.dumps({"url": blob_url}).encode(), {})
        if method == "POST" and url.endswith("/delete"):
            for u in json.loads(req.data)["urls"]:
                self.files.pop(u, None)
            return Resp(b"{}", {})
        body, ctype = self.files[url]
        return Resp(body, {"content-type": ctype})


class Resp(io.BytesIO):
    def __init__(self, body, headers):
        super().__init__(body)
        self.headers = headers

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


@pytest.fixture
def blob_client(tmp_path, monkeypatch, database):
    fake = FakeBlob()
    monkeypatch.setattr(storage.urllib.request, "urlopen", fake)
    app = create_app({"DATABASE": database, "UPLOAD_DIR": str(tmp_path / "up"),
                      "BLOB_TOKEN": "vercel_blob_rw_STORE123_secret", "BLOB_ACCESS": "private"})
    return app.test_client(), fake, tmp_path


def test_documents_go_to_private_blob(blob_client):
    c, fake, tmp = blob_client
    c.post("/properties/new", data={"name": "Flat"})
    r = c.post("/documents", data={"property_id": "1", "title": "Deed", "category": "Purchase deed",
                                   "file": (io.BytesIO(b"%PDF-deed"), "deed.pdf", "application/pdf")},
               content_type="multipart/form-data")
    assert r.status_code == 302
    assert len(fake.files) == 1
    assert not (tmp / "up").exists()            # nothing written to local disk

    r = c.get("/documents/1/file?download=1")
    assert r.status_code == 200 and r.data == b"%PDF-deed"
    assert "attachment" in r.headers["Content-Disposition"]
    assert r.headers["Content-Type"] == "application/pdf"

    c.post("/documents/1/delete")
    assert fake.files == {}
