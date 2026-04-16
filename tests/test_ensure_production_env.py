import os

from scripts.ensure_production_env import ensure_production_env


def test_ensure_production_env_creates_missing_env_file(tmp_path, monkeypatch):
    monkeypatch.delenv("MIRROR_ENV", raising=False)
    monkeypatch.delenv("MIRROR_SECRET_KEY", raising=False)
    env_path = tmp_path / ".env"

    ensure_production_env(env_path)

    content = env_path.read_text(encoding="utf-8")
    assert "MIRROR_ENV=production" in content
    assert "MIRROR_SECRET_KEY=" in content
    assert env_path.stat().st_mode & 0o777 == 0o600


def test_ensure_production_env_preserves_existing_secret(tmp_path, monkeypatch):
    monkeypatch.delenv("MIRROR_ENV", raising=False)
    monkeypatch.delenv("MIRROR_SECRET_KEY", raising=False)
    env_path = tmp_path / ".env"
    env_path.write_text("MIRROR_SECRET_KEY=keepme\nMIRROR_BIND=0.0.0.0:6100\n", encoding="utf-8")

    ensure_production_env(env_path)

    content = env_path.read_text(encoding="utf-8")
    assert content.count("MIRROR_SECRET_KEY=") == 1
    assert "MIRROR_SECRET_KEY=keepme" in content
    assert "MIRROR_ENV=production" in content
    assert "MIRROR_BIND=0.0.0.0:6100" in content


def test_ensure_production_env_respects_existing_environment(tmp_path, monkeypatch):
    monkeypatch.setenv("MIRROR_ENV", "production")
    monkeypatch.setenv("MIRROR_SECRET_KEY", "from-env")
    env_path = tmp_path / ".env"

    ensure_production_env(env_path)

    content = env_path.read_text(encoding="utf-8")
    assert "MIRROR_ENV=" not in content
    assert "MIRROR_SECRET_KEY=" not in content
    assert os.environ["MIRROR_SECRET_KEY"] == "from-env"
