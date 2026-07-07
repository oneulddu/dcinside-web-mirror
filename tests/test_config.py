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
        url = app.jinja_env.globals["static_url"]("v2/css/main.css")

    parsed = urlparse(url)
    version = parse_qs(parsed.query).get("v")

    assert parsed.path == "/static/v2/css/main.css"
    assert version == [str(int(os.path.getmtime(os.path.join(app.static_folder, "v2/css/main.css"))))]


def test_static_files_use_immutable_cache_headers(monkeypatch):
    monkeypatch.setenv("MIRROR_ENV", "development")
    app = create_app()

    response = app.test_client().get("/static/v2/css/main.css")

    assert response.status_code == 200
    assert "public" in response.headers["Cache-Control"]
    assert "max-age=31536000" in response.headers["Cache-Control"]
    assert "immutable" in response.headers["Cache-Control"]


def test_static_files_are_compressed_when_client_accepts_gzip(monkeypatch):
    monkeypatch.setenv("MIRROR_ENV", "development")
    app = create_app()

    response = app.test_client().get("/static/v2/css/main.css", headers={"Accept-Encoding": "gzip"})

    assert response.status_code == 200
    assert response.headers["Content-Encoding"] == "gzip"


def test_static_range_requests_skip_compression(monkeypatch):
    monkeypatch.setenv("MIRROR_ENV", "development")
    app = create_app()

    response = app.test_client().get(
        "/static/v2/css/main.css",
        headers={"Accept-Encoding": "gzip", "Range": "bytes=0-99"},
    )

    assert response.status_code == 206
    assert "Content-Encoding" not in response.headers
    assert response.headers["Content-Range"].startswith("bytes 0-99/")


def test_request_logging_skips_static_files_but_records_routes(monkeypatch, caplog):
    monkeypatch.setenv("MIRROR_ENV", "development")
    app = create_app()
    caplog.set_level(logging.INFO, logger=app.logger.name)

    static_response = app.test_client().get("/static/v2/css/main.css")
    route_response = app.test_client().get("/healthz")

    assert static_response.status_code == 200
    assert route_response.status_code == 200
    assert not any(
        "request path=/static/v2/css/main.css status=200 duration_ms=" in record.getMessage()
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
