"""Vercel entrypoint (Flask preset looks for `app` in index.py)."""
from app import create_app

app = create_app()
