import hashlib
import hmac
import os
from pathlib import Path

from flask import Flask, Response, request

from . import db as dbmod
from .formatting import register_filters


def create_app(test_config=None):
    app = Flask(__name__)
    on_vercel = bool(os.environ.get("VERCEL"))
    db_url = os.environ.get("DATABASE_URL") or os.environ.get("POSTGRES_URL")
    # Vercel's filesystem is read-only except /tmp (and /tmp is wiped between invocations).
    default_dir = "/tmp/property-manager" if on_vercel else Path(__file__).resolve().parent.parent / "data"
    data_dir = Path(os.environ.get("PM_DATA_DIR", default_dir))
    password = os.environ.get("PM_PASSWORD")
    secret = os.environ.get("PM_SECRET_KEY") or hashlib.sha256(
        f"pm:{password or ''}:{db_url or data_dir}".encode()).hexdigest()
    blob_token = os.environ.get("BLOB_READ_WRITE_TOKEN")
    app.config.update(
        SECRET_KEY=secret,
        DATABASE=db_url or str(data_dir / "property.db"),
        UPLOAD_DIR=str(data_dir / "uploads"),
        BLOB_TOKEN=blob_token,
        BLOB_ACCESS=os.environ.get("PM_BLOB_ACCESS", "private"),
        MAX_CONTENT_LENGTH=50 * 1024 * 1024,
        PASSWORD=password,
        # Data would be lost on restart: warn in the UI.
        EPHEMERAL=on_vercel and (not db_url or not blob_token),
    )
    if test_config:
        app.config.update(test_config)
    dbmod.init_db(app.config["DATABASE"])
    app.teardown_appcontext(dbmod.close_db)
    register_filters(app)

    # Optional single-user protection: set PM_PASSWORD to enable HTTP basic auth.
    @app.before_request
    def _auth():
        pw = app.config.get("PASSWORD")
        if not pw:
            return None
        a = request.authorization
        if a and a.password and hmac.compare_digest(a.password.encode(), pw.encode()):
            return None
        return Response("Login required", 401, {"WWW-Authenticate": 'Basic realm="Property Manager"'})

    from .views import bp
    app.register_blueprint(bp)
    return app
