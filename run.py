import os

from app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host=os.environ.get("PM_HOST", "127.0.0.1"), port=int(os.environ.get("PM_PORT", 5000)),
            debug=bool(os.environ.get("PM_DEBUG")))
