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
