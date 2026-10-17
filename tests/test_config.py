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
