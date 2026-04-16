import os

from env_loader import load_dotenv


def test_load_dotenv_sets_missing_values_without_overriding_existing_env(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "\n".join(
            [
                "MIRROR_SECRET_KEY=from-file",
                "MIRROR_BIND='127.0.0.1:6100'",
                "export MIRROR_LOG_LEVEL=debug",
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.delenv("MIRROR_SECRET_KEY", raising=False)
    monkeypatch.setenv("MIRROR_LOG_LEVEL", "info")

    assert load_dotenv(env_file) is True
    assert load_dotenv(tmp_path / "missing.env") is False

    assert load_dotenv(env_file) is True
    assert os.environ["MIRROR_SECRET_KEY"] == "from-file"
    assert os.environ["MIRROR_BIND"] == "127.0.0.1:6100"
    assert os.environ["MIRROR_LOG_LEVEL"] == "info"
