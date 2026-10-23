import os
import secrets
import shutil


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
    PUBLIC_BASE_URL = os.getenv("MIRROR_PUBLIC_BASE_URL", "").strip()
    # 비어 있으면 글쓰기·추천 라우트가 모두 꺼진다. 값은 서버 .env에만 둔다.
    WRITE_KEY = os.getenv("MIRROR_WRITE_KEY", "").strip()
    DC_APP_NODE = os.getenv("MIRROR_DC_APP_NODE", "").strip() or shutil.which("node")
    DC_APP_DIR = os.getenv("MIRROR_DC_APP_DIR", "").strip() or None
    DC_APP_STATE = os.getenv("MIRROR_DC_APP_STATE", "").strip() or None
    DC_APP_UPLOADS = os.getenv("MIRROR_DC_APP_UPLOADS", "").strip() or None
    # DC 워커만 이 HTTP 프록시(집 회선 중계기)를 거쳐 나간다. DC는 클라우드 IP의 추천·글쓰기를 조용히 거절한다.
    DC_APP_PROXY = os.getenv("MIRROR_DC_APP_PROXY", "").strip() or None
    # 사진+동영상 합계 30MiB + multipart 여유; ops nginx 예제의 32m와 동일.
    MAX_CONTENT_LENGTH = 32 * 1024 * 1024
    # Opportunistic JS-cookie gate: HTML-only 수집기 억제용이며 HTML의 토큰을 추출하면 우회할 수 있다.
    VISITOR_CHECK = _as_bool(os.getenv("MIRROR_VISITOR_CHECK"), True)


class DevelopmentConfig(Config):
    DEBUG = True
    SECRET_KEY = _development_secret_key()
    VISITOR_CHECK = _as_bool(os.getenv("MIRROR_VISITOR_CHECK"), False)


class ProductionConfig(Config, metaclass=_ProductionConfigMeta):
    DEBUG = False
