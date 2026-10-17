from app import create_app, find_blob_token, find_database_url


def test_find_database_url_with_prefixes():
    assert find_database_url({"DATABASE_URL": "postgresql://a"}) == "DATABASE_URL"
    env = {"STORAGE_DATABASE_URL_UNPOOLED": "postgresql://direct", "STORAGE_DATABASE_URL": "postgresql://pooled",
           "STORAGE_PGHOST": "x"}
    assert find_database_url(env) == "STORAGE_DATABASE_URL"
    assert find_database_url({"neon_POSTGRES_URL": "postgres://p"}) == "neon_POSTGRES_URL"
    assert find_database_url({"FOO": "bar"}) is None


def test_find_blob_token_with_prefix():
    assert find_blob_token({"DOCS_READ_WRITE_TOKEN": "vercel_blob_rw_abc_def"}) == "DOCS_READ_WRITE_TOKEN"
    assert find_blob_token({"BLOB_READ_WRITE_TOKEN": "vercel_blob_rw_x_y"}) == "BLOB_READ_WRITE_TOKEN"
    assert find_blob_token({}) is None


def test_bad_database_shows_status_page(tmp_path):
    app = create_app({"DATABASE": "postgresql://nobody@127.0.0.1:1/none", "UPLOAD_DIR": str(tmp_path)})
    c = app.test_client()
    r = c.get("/")
    assert r.status_code == 503
    assert b"Database connection" in r.data and b"OperationalError" in r.data
    assert c.get("/status").status_code == 200


def test_concurrent_startup_does_not_deadlock(database):
    """Many serverless instances starting at once must not deadlock on schema changes."""
    import threading
    from app import db as dbmod
    errors = []

    def boot():
        try:
            dbmod.init_db(database)
        except Exception as e:
            errors.append(e)

    dbmod.init_db(database)
    # simulate an older schema so every instance wants to migrate
    conn = dbmod.connect(database)
    conn.execute("UPDATE settings SET value = 'old' WHERE key = 'schema_version'")
    conn.commit()
    conn.close()
    threads = [threading.Thread(target=boot) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors, errors


def test_blob_oidc_auth(app):
    from app import storage
    app.config.update(BLOB_TOKEN=None, BLOB_STORE_ID="store_ABC123")
    with app.test_request_context(headers={"x-vercel-oidc-token": "jwt-token"}):
        assert storage.blob_auth() == ("jwt-token", "ABC123")
    with app.test_request_context():
        assert storage.blob_auth() is None and storage.configured()
