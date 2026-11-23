import json
import os
import secrets
import shutil


def _env_secret_key():
    value = os.getenv("MIRROR_SECRET_KEY")
    if value is None:
        return None
    value = value.strip()
    return value or None


def _as_bool(value, default=False):
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


class Config:
    JSON_AS_ASCII = False
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    # 사진+동영상 합계 30MiB + multipart 여유; ops nginx 예제의 32m와 동일.
    MAX_CONTENT_LENGTH = 32 * 1024 * 1024

    def __init__(self):
        self.SECRET_KEY = _env_secret_key()
        self.TEMPLATES_AUTO_RELOAD = _as_bool(os.getenv("MIRROR_TEMPLATES_AUTO_RELOAD"))
        self.PREFERRED_URL_SCHEME = os.getenv("MIRROR_PREFERRED_URL_SCHEME", "http")
        self.PUBLIC_BASE_URL = os.getenv("MIRROR_PUBLIC_BASE_URL", "").strip()
        self.WRITE_KEY = os.getenv("MIRROR_WRITE_KEY", "").strip()
        self.PUSH_STATE = os.getenv("MIRROR_PUSH_STATE", "").strip() or None
        self.READ_SYNC_STATE = os.getenv("MIRROR_READ_SYNC_STATE", "").strip() or None
        self.DC_APP_NODE = os.getenv("MIRROR_DC_APP_NODE", "").strip() or shutil.which("node")
        self.DC_APP_DIR = os.getenv("MIRROR_DC_APP_DIR", "").strip() or None
        self.DC_APP_STATE = os.getenv("MIRROR_DC_APP_STATE", "").strip() or None
        self.DC_APP_UPLOADS = os.getenv("MIRROR_DC_APP_UPLOADS", "").strip() or None
        self.DC_APP_PROXY = os.getenv("MIRROR_DC_APP_PROXY", "").strip() or None
        self.VISITOR_CHECK = _as_bool(os.getenv("MIRROR_VISITOR_CHECK"), not self.DEBUG)
        self.COMPRESS_REGISTER = _as_bool(os.getenv("MIRROR_COMPRESS_REGISTER"))
        types = os.getenv("MIRROR_COMPRESS_MIMETYPES")
        self.COMPRESS_MIMETYPES = (
            ["text/html", "application/json", "text/css", "application/javascript", "text/javascript"]
            if types is None else json.loads(types)
        )
        if not isinstance(self.COMPRESS_MIMETYPES, list) or not all(
            isinstance(value, str) for value in self.COMPRESS_MIMETYPES
        ):
            raise ValueError("MIRROR_COMPRESS_MIMETYPES must be a JSON list of strings")


class DevelopmentConfig(Config):
    DEBUG = True
    _fallback_key = secrets.token_hex(32)

    def __init__(self):
        super().__init__()
        self.SECRET_KEY = self.SECRET_KEY or self._fallback_key


class ProductionConfig(Config):
    DEBUG = False

    def __init__(self):
        super().__init__()
        if not self.SECRET_KEY:
            raise RuntimeError("MIRROR_SECRET_KEY must be set when MIRROR_ENV is production.")
