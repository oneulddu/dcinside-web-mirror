import os


def _as_bool(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


class Config:
    SECRET_KEY = os.getenv("MIRROR_SECRET_KEY", "change-me-in-production")
    JSON_AS_ASCII = False
    TEMPLATES_AUTO_RELOAD = _as_bool(os.getenv("MIRROR_TEMPLATES_AUTO_RELOAD"), False)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    PREFERRED_URL_SCHEME = os.getenv("MIRROR_PREFERRED_URL_SCHEME", "http")


class DevelopmentConfig(Config):
    DEBUG = True


class ProductionConfig(Config):
    DEBUG = False
