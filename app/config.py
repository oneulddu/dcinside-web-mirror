import os
import secrets


def _env_secret_key():
    value = os.getenv("MIRROR_SECRET_KEY")
    if value is None:
        return None
    value = value.strip()
    return value or None


def _development_secret_key():
    return _env_secret_key() or secrets.token_hex(32)


class _ProductionConfigMeta(type):
    def __getattribute__(cls, name):
        if name == "SECRET_KEY":
            secret_key = _env_secret_key()
            if not secret_key:
                raise RuntimeError("MIRROR_SECRET_KEY must be set when MIRROR_ENV is production.")
            return secret_key
        return super().__getattribute__(name)


def _as_bool(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


class Config:
    SECRET_KEY = None
    JSON_AS_ASCII = False
    TEMPLATES_AUTO_RELOAD = _as_bool(os.getenv("MIRROR_TEMPLATES_AUTO_RELOAD"), False)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    PREFERRED_URL_SCHEME = os.getenv("MIRROR_PREFERRED_URL_SCHEME", "http")


class DevelopmentConfig(Config):
    DEBUG = True
    SECRET_KEY = _development_secret_key()


class ProductionConfig(Config, metaclass=_ProductionConfigMeta):
    DEBUG = False
