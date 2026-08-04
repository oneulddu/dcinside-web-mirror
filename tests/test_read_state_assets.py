"""읽음 표시(회색 처리) 반응 속도 회귀 방지 계약.

회색 표시는 `read_state.js`가 실행되어야 붙는다. 그런데 `read_state.js`는
`</body>` 직전의 일반 스크립트라서, `<head>`에 렌더 차단 스타일시트가 하나라도
더 있으면 그 응답이 끝날 때까지 실행되지 못한다. 과거 외부 CDN 폰트 CSS 링크
때문에 회색 표시가 CDN 왕복 시간만큼 통째로 밀렸다.
"""

import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
BASE_TEMPLATE = PROJECT_ROOT / "app" / "templates" / "base.html"
MAIN_CSS = PROJECT_ROOT / "app" / "static" / "css" / "main.css"
READ_STATE_JS = PROJECT_ROOT / "app" / "static" / "javascript" / "read_state.js"


def _stylesheet_links(html):
    return re.findall(r"<link[^>]*rel=[\"']stylesheet[\"'][^>]*>", html)


def test_no_external_render_blocking_stylesheet():
    """외부 스타일시트는 읽음 표시 스크립트 실행을 지연시키므로 금지한다."""
    links = _stylesheet_links(BASE_TEMPLATE.read_text(encoding="utf-8"))

    external = [link for link in links if "//" in link and "static_url" not in link]

    assert external == [], f"외부 렌더 차단 스타일시트가 추가됐다: {external}"


def test_suit_font_declared_locally_with_swap():
    """폰트는 로컬 CSS의 @font-face로 선언하고 font-display: swap을 쓴다."""
    css = MAIN_CSS.read_text(encoding="utf-8")

    assert "@font-face" in css
    assert "SUIT Variable" in css
    assert "SUIT-Variable.woff2" in css
    assert "font-display: swap" in css


def test_mark_read_reloads_store_before_write():
    """읽음 기록 저장 전 항상 최신 저장소를 다시 읽어 다른 탭 기록을 보존한다."""
    js = READ_STATE_JS.read_text(encoding="utf-8")

    mark_read = js.split("function markRead(")[1].split("function ")[0]

    assert "loadStore()" in mark_read
    assert "readStore ||" not in mark_read


def test_read_state_resyncs_on_restore_and_cross_tab_updates():
    """bfcache 복원, 탭 전환, 다른 탭 저장소 변경 시 읽음 상태를 다시 반영한다."""
    js = READ_STATE_JS.read_text(encoding="utf-8")

    assert "pageshow" in js
    assert "event.persisted" in js
    assert '"storage"' in js
    assert "visibilitychange" in js
