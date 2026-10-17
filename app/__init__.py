import os
from pathlib import Path

from flask import Flask, Response, request

from . import db as dbmod
from .formatting import register_filters


def create_app(test_config=None):
    app = Flask(__name__)
    data_dir = Path(os.environ.get("PM_DATA_DIR", Path(__file__).resolve().parent.parent / "data"))
    app.config.update(
        SECRET_KEY=os.environ.get("PM_SECRET_KEY", "dev-change-me"),
        DATABASE=str(data_dir / "property.db"),
        UPLOAD_DIR=str(data_dir / "uploads"),
        MAX_CONTENT_LENGTH=50 * 1024 * 1024,
        PASSWORD=os.environ.get("PM_PASSWORD"),
    )
    if test_config:
        app.config.update(test_config)
    Path(app.config["UPLOAD_DIR"]).mkdir(parents=True, exist_ok=True)
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
        if a and a.password == pw:
            return None
        return Response("Login required", 401, {"WWW-Authenticate": 'Basic realm="Property Manager"'})

    from .views import bp
    app.register_blueprint(bp)
    return app
