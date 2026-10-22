"""Opportunistic JS-cookie gate, not proof that a visitor is human.

A browser without the cookie gets a tiny page whose script stores a server-signed
token and reloads. This deters HTML-only scrapers regardless of IP, but a scraper
can extract the token from the HTML without executing JavaScript. No obfuscation,
IP/UA binding, or upstream requests are involved in issuing the constant payload.
"""
from flask import current_app, jsonify, make_response, render_template, request, url_for
from itsdangerous import BadSignature, URLSafeTimedSerializer

from app.config import _as_bool

COOKIE = "mirror_visitor"
COOKIE_AGE = 180 * 24 * 3600
_EXEMPT_ENDPOINTS = frozenset({"static", "main.healthz", "main.favicon"})
_MEDIA_ENDPOINTS = frozenset({
    "main.media", "main.movie", "main.embed_link_preview_image", "main.write_captcha",
    "poker.media",
})
_JSON_ENDPOINTS = frozenset({
    "main.board_updates", "main.board_times", "main.youtube_size", "main.embed_link_preview",
    "main.read_related", "main.read_vote_options", "main.read_vote", "main.read_comment",
    "main.read_delete", "main.read_comment_delete", "main.write_modify_load",
    "poker.post_list", "poker.comments",
})
FETCH_HEADER = "X-Mirror-Fetch"
MESSAGE = "방문 확인이 필요해요. 아무것도 전송하지 않았어요. 입력한 내용은 따로 보관한 뒤 페이지를 새로고침해 주세요."


def _signer():
    return URLSafeTimedSerializer(current_app.secret_key, salt="mirror-visitor")


def _has_valid_cookie():
    try:
        return _signer().loads(request.cookies.get(COOKIE, ""), max_age=COOKIE_AGE) == "v1"
    except BadSignature:
        return False


def _wants_json():
    return (
        request.endpoint in _JSON_ENDPOINTS
        or (request.endpoint == "main.write" and request.method == "POST")
        or request.is_json
        or FETCH_HEADER in request.headers
        or request.headers.get("Sec-Fetch-Dest") == "empty"
        or request.headers.get("X-Requested-With") == "XMLHttpRequest"
        or request.accept_mimetypes.best_match(
            ["text/html", "application/xhtml+xml", "application/json"]
        ) == "application/json"
    )


def _wants_page():
    return (
        request.method in ("GET", "HEAD")
        and request.headers.get("Sec-Fetch-Dest", "document") == "document"
        and any(request.accept_mimetypes[mime] > 0
                for mime in ("text/html", "application/xhtml+xml"))
    )


def check_visitor():
    if not _as_bool(current_app.config.get("VISITOR_CHECK")) or request.endpoint in _EXEMPT_ENDPOINTS:
        return None
    if _has_valid_cookie():
        return None
    if request.endpoint in _MEDIA_ENDPOINTS:
        response = make_response("visitor check required", 403)
        response.mimetype = "text/plain"
    elif _wants_json():
        # Match DC's ok/code/message contract and Poker's error message contract.
        # The protected view has not run: this is never an uncertain write.
        response = jsonify(ok=False, code="visitor_check_required", message=MESSAGE,
                           error=MESSAGE, sent=False)
        response.status_code = 403
    elif _wants_page():
        response = make_response(render_template(
            "visitor_check.html",
            cookie=COOKIE,
            token=_signer().dumps("v1"),
            max_age=COOKIE_AGE,
            secure=request.is_secure,
        ))
    elif request.method == "POST":
        # Never run a challenge/reload on a form POST: even a reload could replay it.
        response = make_response(render_template(
            "visitor_check.html", terminal=True, message=MESSAGE,
            back_url=url_for("main.recent"),
        ), 403)
    else:
        response = make_response("visitor check required", 403)
        response.mimetype = "text/plain"
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def init_visitor_check(app):
    app.before_request(check_visitor)
