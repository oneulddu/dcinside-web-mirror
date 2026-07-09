import logging
import os
from urllib.parse import parse_qs, urlparse

import pytest

from app import create_app


def test_development_allows_missing_secret_key(monkeypatch):
    monkeypatch.setenv("MIRROR_ENV", "development")
    monkeypatch.delenv("MIRROR_SECRET_KEY", raising=False)

    app = create_app()

    assert app.config["DEBUG"] is True
    assert app.config["SECRET_KEY"]


def test_production_requires_secret_key(monkeypatch):
    monkeypatch.setenv("MIRROR_ENV", "production")
    monkeypatch.delenv("MIRROR_SECRET_KEY", raising=False)

    with pytest.raises(RuntimeError, match="MIRROR_SECRET_KEY"):
        create_app()


def test_production_uses_configured_secret_key(monkeypatch):
    monkeypatch.setenv("MIRROR_ENV", "production")
    monkeypatch.setenv("MIRROR_SECRET_KEY", "stable-secret")

    app = create_app()

    assert app.config["DEBUG"] is False
    assert app.config["SECRET_KEY"] == "stable-secret"


def test_static_url_adds_mtime_version(monkeypatch):
    monkeypatch.setenv("MIRROR_ENV", "development")
    app = create_app()

    with app.test_request_context():
        url = app.jinja_env.globals["static_url"]("css/main.css")

    parsed = urlparse(url)
    version = parse_qs(parsed.query).get("v")

    assert parsed.path == "/static/css/main.css"
    assert version == [str(int(os.path.getmtime(os.path.join(app.static_folder, "css/main.css"))))]


def test_static_url_memoizes_mtime_in_production(monkeypatch):
    monkeypatch.setenv("MIRROR_ENV", "production")
    monkeypatch.setenv("MIRROR_SECRET_KEY", "stable-secret")
    mtimes = iter([1000, 2000])
    monkeypatch.setattr(os.path, "getmtime", lambda _path: next(mtimes))
    app = create_app()

    with app.test_request_context():
        first_url = app.jinja_env.globals["static_url"]("css/main.css")
        second_url = app.jinja_env.globals["static_url"]("css/main.css")

    assert parse_qs(urlparse(first_url).query).get("v") == ["1000"]
    assert second_url == first_url


def test_static_url_rechecks_mtime_in_debug(monkeypatch):
    monkeypatch.setenv("MIRROR_ENV", "development")
    mtimes = iter([1000, 2000])
    monkeypatch.setattr(os.path, "getmtime", lambda _path: next(mtimes))
    app = create_app()

    with app.test_request_context():
        first_url = app.jinja_env.globals["static_url"]("css/main.css")
        second_url = app.jinja_env.globals["static_url"]("css/main.css")

    assert parse_qs(urlparse(first_url).query).get("v") == ["1000"]
    assert parse_qs(urlparse(second_url).query).get("v") == ["2000"]


def test_static_files_use_immutable_cache_headers(monkeypatch):
    monkeypatch.setenv("MIRROR_ENV", "development")
    app = create_app()

    with app.test_request_context():
        static_path = app.jinja_env.globals["static_url"]("css/main.css")
    response = app.test_client().get(static_path)

    assert response.status_code == 200
    assert "public" in response.headers["Cache-Control"]
    assert "max-age=31536000" in response.headers["Cache-Control"]
    assert "immutable" in response.headers["Cache-Control"]


def test_production_static_files_do_not_force_revalidation(monkeypatch):
    monkeypatch.setenv("MIRROR_ENV", "production")
    monkeypatch.setenv("MIRROR_SECRET_KEY", "stable-secret")
    app = create_app()

    with app.test_request_context():
        static_path = app.jinja_env.globals["static_url"]("css/main.css")

    client = app.test_client()
    response = client.get(static_path, headers={"Accept-Encoding": "gzip"})

    assert response.status_code == 200
    assert "no-cache" not in response.headers["Cache-Control"]
    assert "public" in response.headers["Cache-Control"]
    assert "max-age=31536000" in response.headers["Cache-Control"]
    assert "immutable" in response.headers["Cache-Control"]
    etag = response.headers["ETag"]

    conditional_response = client.get(
        static_path,
        headers={"Accept-Encoding": "gzip", "If-None-Match": etag},
    )

    assert conditional_response.status_code == 304
    assert conditional_response.headers["ETag"] == etag
    assert "no-cache" not in conditional_response.headers["Cache-Control"]


def test_missing_static_files_are_not_cached_as_immutable(monkeypatch):
    monkeypatch.setenv("MIRROR_ENV", "production")
    monkeypatch.setenv("MIRROR_SECRET_KEY", "stable-secret")
    app = create_app()

    response = app.test_client().get("/static/missing-file.css")

    assert response.status_code == 404
    assert "immutable" not in response.headers.get("Cache-Control", "")
    assert "max-age=31536000" not in response.headers.get("Cache-Control", "")


@pytest.mark.parametrize(
    "static_path",
    [
        "/static/css/main.css",
        "/static/css/main.css?v=bogus",
    ],
)
def test_unversioned_or_mismatched_static_files_require_revalidation(monkeypatch, static_path):
    monkeypatch.setenv("MIRROR_ENV", "production")
    monkeypatch.setenv("MIRROR_SECRET_KEY", "stable-secret")
    app = create_app()

    response = app.test_client().get(static_path)

    assert response.status_code == 200
    assert "no-cache" in response.headers["Cache-Control"]
    assert "immutable" not in response.headers["Cache-Control"]
    assert "max-age=31536000" not in response.headers["Cache-Control"]


def test_stale_production_static_url_is_not_cached_as_immutable(monkeypatch):
    monkeypatch.setenv("MIRROR_ENV", "production")
    monkeypatch.setenv("MIRROR_SECRET_KEY", "stable-secret")
    calls = [0]

    def changing_mtime(_path):
        calls[0] += 1
        return 1000 if calls[0] == 1 else 2000

    monkeypatch.setattr(os.path, "getmtime", changing_mtime)
    app = create_app()
    with app.test_request_context():
        stale_url = app.jinja_env.globals["static_url"]("css/main.css")

    response = app.test_client().get(stale_url)

    assert parse_qs(urlparse(stale_url).query)["v"] == ["1000"]
    assert response.status_code == 200
    assert "no-cache" in response.headers["Cache-Control"]
    assert "immutable" not in response.headers["Cache-Control"]


def test_static_files_are_compressed_when_client_accepts_gzip(monkeypatch):
    monkeypatch.setenv("MIRROR_ENV", "development")
    app = create_app()

    response = app.test_client().get("/static/css/main.css", headers={"Accept-Encoding": "gzip"})

    assert response.status_code == 200
    assert response.headers["Content-Encoding"] == "gzip"


def test_static_range_requests_skip_compression(monkeypatch):
    monkeypatch.setenv("MIRROR_ENV", "development")
    app = create_app()

    response = app.test_client().get(
        "/static/css/main.css",
        headers={"Accept-Encoding": "gzip", "Range": "bytes=0-99"},
    )

    assert response.status_code == 206
    assert "Content-Encoding" not in response.headers
    assert response.headers["Content-Range"].startswith("bytes 0-99/")


def test_request_logging_skips_static_files_but_records_routes(monkeypatch, caplog):
    monkeypatch.setenv("MIRROR_ENV", "development")
    app = create_app()
    caplog.set_level(logging.INFO, logger=app.logger.name)

    static_response = app.test_client().get("/static/css/main.css")
    route_response = app.test_client().get("/healthz")

    assert static_response.status_code == 200
    assert route_response.status_code == 200
    assert not any(
        "request path=/static/css/main.css status=200 duration_ms=" in record.getMessage()
        for record in caplog.records
    )
    assert any(
        "request path=/healthz status=200 duration_ms=" in record.getMessage()
        for record in caplog.records
    )


def test_healthz_is_local_only(monkeypatch):
    monkeypatch.setenv("MIRROR_ENV", "development")
    app = create_app()

    response = app.test_client().get("/healthz")
    external_response = app.test_client().get("/healthz", environ_base={"REMOTE_ADDR": "203.0.113.10"})

    assert response.status_code == 200
    assert response.get_json() == {"ok": True}
    assert external_response.status_code == 404


def test_healthz_accepts_ipv4_mapped_loopback(monkeypatch):
    monkeypatch.setenv("MIRROR_ENV", "development")
    app = create_app()

    response = app.test_client().get("/healthz", environ_base={"REMOTE_ADDR": "::ffff:127.0.0.1"})

    assert response.status_code == 200
    assert response.get_json() == {"ok": True}


def test_healthz_rejects_non_loopback_and_invalid_addresses(monkeypatch):
    monkeypatch.setenv("MIRROR_ENV", "development")
    app = create_app()
    client = app.test_client()

    mapped_external = client.get("/healthz", environ_base={"REMOTE_ADDR": "::ffff:203.0.113.10"})
    invalid_addr = client.get("/healthz", environ_base={"REMOTE_ADDR": "not-an-ip"})

    assert mapped_external.status_code == 404
    assert invalid_addr.status_code == 404
