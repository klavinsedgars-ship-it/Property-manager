"""File storage for documents: local folder, or Vercel Blob (private store)
when BLOB_READ_WRITE_TOKEN is set.

The `documents.filename` column holds the storage key: a file name for local
storage, or the blob URL for Vercel Blob. Private blobs are only readable
with the token, so downloads are proxied through the app (behind its login).
"""
import json
import mimetypes
import os
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

from flask import Response, current_app, send_from_directory
from werkzeug.utils import secure_filename

BLOB_API = "https://vercel.com/api/blob"
BLOB_API_VERSION = "13"   # as used by @vercel/blob 2.x


def _blob_token():
    return current_app.config.get("BLOB_TOKEN")


def _blob_headers(extra=None):
    token = _blob_token()
    store_id = token.split("_")[3] if token.count("_") >= 4 else ""
    return {"authorization": f"Bearer {token}", "x-api-version": BLOB_API_VERSION,
            "x-vercel-blob-store-id": store_id, **(extra or {})}


def _blob_request(url, method="GET", data=None, headers=None, timeout=60):
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(), r.headers


def save(file_storage) -> str:
    """Store an uploaded werkzeug FileStorage, return its storage key."""
    name = f"{uuid.uuid4().hex}_{secure_filename(file_storage.filename) or 'file'}"
    if _blob_token():
        ctype = file_storage.mimetype or mimetypes.guess_type(name)[0] or "application/octet-stream"
        body, _ = _blob_request(
            f"{BLOB_API}/?{urllib.parse.urlencode({'pathname': 'documents/' + name})}", method="PUT",
            data=file_storage.read(),
            headers=_blob_headers({"x-vercel-blob-access": current_app.config["BLOB_ACCESS"],
                                   "x-add-random-suffix": "1", "x-content-type": ctype}))
        return json.loads(body)["url"]
    folder = Path(current_app.config["UPLOAD_DIR"])
    folder.mkdir(parents=True, exist_ok=True)
    file_storage.save(folder / name)
    return name


def send(key, download_name, as_attachment=False):
    if key.startswith("https://"):
        if not urllib.parse.urlparse(key).hostname.endswith(".blob.vercel-storage.com"):
            return Response("Invalid document location", 400)
        body, headers = _blob_request(key, headers={"authorization": f"Bearer {_blob_token()}"})
        disp = "attachment" if as_attachment else "inline"
        quoted = urllib.parse.quote(download_name or "file")
        return Response(body, mimetype=headers.get("content-type") or "application/octet-stream",
                        headers={"Content-Disposition": f"{disp}; filename*=UTF-8''{quoted}",
                                 "Cache-Control": "private, no-store"})
    return send_from_directory(current_app.config["UPLOAD_DIR"], key, download_name=download_name,
                               as_attachment=as_attachment)


def delete(key):
    if key.startswith("https://"):
        if _blob_token():
            _blob_request(f"{BLOB_API}/delete", method="POST", data=json.dumps({"urls": [key]}).encode(),
                          headers=_blob_headers({"content-type": "application/json"}))
        return
    (Path(current_app.config["UPLOAD_DIR"]) / key).unlink(missing_ok=True)


def max_upload_mb():
    # Vercel functions reject request bodies over 4.5 MB.
    return 4 if os.environ.get("VERCEL") else 50
