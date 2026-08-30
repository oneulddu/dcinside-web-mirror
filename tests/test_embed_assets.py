"""읽기 화면 임베드 상태와 썸네일 클라이언트 계약 회귀 테스트."""

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
EMBED_JS = PROJECT_ROOT / "app" / "static" / "javascript" / "embed_resizer.js"
PREVIEW_JS = PROJECT_ROOT / "app" / "static" / "javascript" / "link_preview.js"
MAIN_CSS = PROJECT_ROOT / "app" / "static" / "css" / "main.css"


def test_x_embed_uses_load_as_success_and_never_hides_on_missing_resize_message():
    js = EMBED_JS.read_text(encoding="utf-8")

    assert 'iframe.addEventListener("load"' in js
    assert "markTwitterLoaded(iframe)" in js
    assert "twttr.private.resize" in js
    assert "setTimeout" not in js.split("// --- X(트위터) 임베드:", 1)[1]
    assert "TWITTER_TIMEOUT" not in js


def test_x_embed_error_fallback_and_supported_embed_layouts_are_styled():
    css = MAIN_CSS.read_text(encoding="utf-8")

    for selector in (
        'iframe[src*="youtube.com/embed"]',
        'iframe[src^="/movie"]',
        'iframe[src^="https://m.dcinside.com/poll"]',
        'iframe[src^="https://platform.twitter.com/embed/"]',
        ".embed-card.is-embed-failed .embed-card-fallback",
    ):
        assert selector in css


def test_dynamic_preview_accepts_only_signed_same_origin_image_endpoint():
    js = PREVIEW_JS.read_text(encoding="utf-8")

    assert "parsed.origin === window.location.origin" in js
    assert 'parsed.pathname === "/embed/link-preview-image"' in js
    assert "image.alt = data.title" in js
    assert 'image.loading = "lazy"' in js
