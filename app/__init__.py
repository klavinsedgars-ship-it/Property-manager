import hashlib
import hmac
import os
import re
from pathlib import Path

from flask import Flask, Response, render_template, request

from . import db as dbmod
from .formatting import register_filters


def find_database_url(env=os.environ):
    """Postgres URL from env. Vercel storage integrations may prefix variable
    names (e.g. STORAGE_DATABASE_URL), so match by suffix and by value.
    Pooled URLs are preferred over *_UNPOOLED / *_NON_POOLING ones."""
    for key in ("DATABASE_URL", "POSTGRES_URL"):
        if env.get(key, "").startswith(("postgres://", "postgresql://")):
            return key
    keys = [k for k, v in env.items() if v.startswith(("postgres://", "postgresql://"))
            and not re.search(r"UNPOOLED|NON_POOLING|NO_SSL", k)]
    keys.sort(key=lambda k: (not k.endswith("DATABASE_URL"), not k.endswith("POSTGRES_URL"), k))
    return keys[0] if keys else None


def find_blob_token(env=os.environ):
    if env.get("BLOB_READ_WRITE_TOKEN", "").startswith("vercel_blob_rw_"):
        return "BLOB_READ_WRITE_TOKEN"
    keys = sorted(k for k, v in env.items() if v.startswith("vercel_blob_rw_"))
    return keys[0] if keys else None


def create_app(test_config=None):
    app = Flask(__name__)
    on_vercel = bool(os.environ.get("VERCEL"))
    db_key, blob_key = find_database_url(), find_blob_token()
    db_url = os.environ[db_key] if db_key else None
    # Vercel's filesystem is read-only except /tmp (and /tmp is wiped between invocations).
    default_dir = "/tmp/property-manager" if on_vercel else Path(__file__).resolve().parent.parent / "data"
    data_dir = Path(os.environ.get("PM_DATA_DIR", default_dir))
    password = os.environ.get("PM_PASSWORD")
    secret = os.environ.get("PM_SECRET_KEY") or hashlib.sha256(
        f"pm:{password or ''}:{db_url or data_dir}".encode()).hexdigest()
    app.config.update(
        SECRET_KEY=secret,
        DATABASE=db_url or str(data_dir / "property.db"),
        UPLOAD_DIR=str(data_dir / "uploads"),
        BLOB_TOKEN=os.environ[blob_key] if blob_key else None,
        BLOB_ACCESS=os.environ.get("PM_BLOB_ACCESS", "private"),
        MAX_CONTENT_LENGTH=50 * 1024 * 1024,
        PASSWORD=password,
        ON_VERCEL=on_vercel,
        STORAGE_STATUS={"database": db_key, "blob": blob_key},
        DB_ERROR=None,
    )
    if test_config:
        app.config.update(test_config)
    # Data would be lost on restart: warn in the UI.
    app.config["EPHEMERAL"] = on_vercel and (not dbmod.is_postgres(app.config["DATABASE"])
                                             or not app.config["BLOB_TOKEN"])
    try:
        dbmod.init_db(app.config["DATABASE"])
    except Exception as e:  # show a readable status page instead of a crashed function
        app.config["DB_ERROR"] = f"{type(e).__name__}: {e}"
    app.teardown_appcontext(dbmod.close_db)
    register_filters(app)

    # Optional single-user protection: set PM_PASSWORD to enable HTTP basic auth.
    @app.before_request
    def _auth():
        if request.path == "/cron/sync" and request.method == "GET":
            return _cron_auth()
        pw = app.config.get("PASSWORD")
        if not pw:
            return None
        a = request.authorization
        if a and a.password and hmac.compare_digest(a.password.encode(), pw.encode()):
            return None
        return Response("Login required", 401, {"WWW-Authenticate": 'Basic realm="Property Manager"'})

    @app.before_request
    def _db_error():
        if app.config["DB_ERROR"] and request.endpoint not in ("static", "main.status"):
            return render_template("status.html", **status_context(app)), 503
        return None

    from .views import bp
    app.register_blueprint(bp)
    return app


def _cron_auth():
    """Vercel Cron sends `Authorization: Bearer $CRON_SECRET` when CRON_SECRET is set.
    Without a secret, only Vercel's cron user agent is accepted (the job only
    downloads public data, so this is low risk)."""
    secret = os.environ.get("CRON_SECRET")
    if secret:
        ok = hmac.compare_digest(request.headers.get("Authorization", "").encode(), f"Bearer {secret}".encode())
    else:
        ok = request.headers.get("User-Agent", "").startswith("vercel-cron")
    return None if ok else Response("Forbidden", 403)


def status_context(app):
    """Storage diagnostics — variable names only, never their values."""
    env_names = sorted(k for k in os.environ if re.search(r"DATABASE|POSTGRES|(^|_)PG|NEON|BLOB|READ_WRITE_TOKEN", k))
    return {"st": app.config["STORAGE_STATUS"], "db_error": app.config["DB_ERROR"],
            "backend": "PostgreSQL" if dbmod.is_postgres(app.config["DATABASE"]) else "SQLite (temporary on Vercel)",
            "blob": bool(app.config["BLOB_TOKEN"]), "on_vercel": app.config["ON_VERCEL"],
            "env_names": env_names, "password_set": bool(app.config["PASSWORD"])}
