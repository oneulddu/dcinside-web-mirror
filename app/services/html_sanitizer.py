from collections import defaultdict, deque
import re
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
from flask import url_for

from .dc_links import dcinside_internal_href
from .highlight import highlight_soup_text


HTML_ALLOWED_TAGS = {
    "a", "abbr", "b", "blockquote", "br", "code", "dd", "del", "div", "dl", "dt",
    "em", "figcaption", "figure", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "iframe",
    "img", "li", "ol", "p", "pre", "s", "source", "span", "strong", "sub", "sup", "table",
    "tbody", "td", "th", "thead", "tr", "u", "ul",
    "video",
}
HTML_DROP_TAGS = {"script", "style", "object", "embed", "link", "meta", "base", "form", "input", "button"}
HTML_GLOBAL_ATTRS = {"class", "title"}
HTML_TAG_ATTRS = {
    "a": {"href", "target", "rel"},
    "iframe": {"src", "title", "loading", "width", "height", "frameborder", "scrolling", "allow", "allowfullscreen"},
    "img": {"src", "alt", "loading", "decoding", "fetchpriority", "width", "height"},
    "source": {"src", "type"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
    "video": {"src", "poster", "controls", "autoplay", "loop", "muted", "playsinline", "preload", "width", "height"},
}
YOUTUBE_IFRAME_HOSTS = {"youtube.com", "www.youtube.com", "youtube-nocookie.com", "www.youtube-nocookie.com"}
DC_MOVIE_VIEW_URL = "https://gall.dcinside.com/board/movie/movie_view?no={}"
DC_POLL_URL = "https://m.dcinside.com/poll"
TWITTER_EMBED_URL = "https://platform.twitter.com/embed/Tweet.html?id={}&dnt=true"
TWITTER_PLATFORM_HOSTS = {"platform.twitter.com", "platform.x.com"}
TWITTER_STATUS_HOSTS = {
    "twitter.com", "www.twitter.com", "mobile.twitter.com",
    "x.com", "www.x.com", "mobile.x.com",
}
HTML_PARSER = "lxml"


def _safe_urlparse(value):
    try:
        return urlparse(str(value or "").strip())
    except (TypeError, ValueError):
        return None


def is_safe_href(value):
    url = str(value or "").strip()
    if not url:
        return False
    parsed = _safe_urlparse(url)
    if parsed is None:
        return False
    if not parsed.scheme:
        return url.startswith(("#", "/")) and not url.startswith("//")
    return parsed.scheme in {"http", "https", "mailto"}


def has_dot_path_segment(path):
    return any(segment in {".", ".."} for segment in (path or "").split("/"))


def is_safe_youtube_embed_path(path):
    if has_dot_path_segment(path):
        return False
    if not path.startswith("/embed/"):
        return False
    video_id = path[len("/embed/"):]
    return bool(video_id) and "/" not in video_id


def youtube_shorts_video_id(path):
    if has_dot_path_segment(path):
        return None
    match = re.match(r"^/shorts/([A-Za-z0-9_-]{11})/?$", path or "")
    return match.group(1) if match else None


def dc_movie_id_from_parsed_url(parsed):
    host = (parsed.netloc or "").lower()
    if (
        (host == "gall.dcinside.com" and parsed.path == "/board/movie/movie_view")
        or (host == "m.dcinside.com" and parsed.path == "/movie/player")
    ):
        movie_ids = parse_qs(parsed.query).get("no", [])
        if movie_ids and movie_ids[0].isdigit():
            return movie_ids[0]
    return None


def dc_movie_id_from_iframe_src(value):
    url = str(value or "").strip()
    if not url:
        return None
    parsed = _safe_urlparse(url)
    if parsed is None:
        return None
    return dc_movie_id_from_parsed_url(parsed)


def normalize_dc_movie_iframe_src(parsed):
    movie_id = dc_movie_id_from_parsed_url(parsed)
    if movie_id:
        # The mobile player returns an empty body to desktop iframe requests.
        # If route context is unavailable for a same-origin player rewrite,
        # keep the iframe usable by falling back to DCInside's PC movie URL.
        return DC_MOVIE_VIEW_URL.format(movie_id)
    return None


def tweet_id_from_status_path(path):
    if has_dot_path_segment(path):
        return None
    segments = [segment for segment in (path or "").split("/") if segment]
    if len(segments) >= 3 and segments[-2] in {"status", "statuses"} and segments[-1].isdigit():
        return segments[-1]
    return None


def normalize_twitter_iframe_src(parsed):
    host = (parsed.netloc or "").lower()
    if host in TWITTER_PLATFORM_HOSTS:
        if has_dot_path_segment(parsed.path) or parsed.path != "/embed/Tweet.html":
            return None
        tweet_ids = parse_qs(parsed.query).get("id", [])
        if tweet_ids and tweet_ids[0].isdigit():
            return TWITTER_EMBED_URL.format(tweet_ids[0])
        return None
    if host in TWITTER_STATUS_HOSTS:
        tweet_id = tweet_id_from_status_path(parsed.path)
        if tweet_id:
            return TWITTER_EMBED_URL.format(tweet_id)
    return None


def normalize_safe_iframe_src(value):
    url = str(value or "").strip()
    if not url:
        return None
    parsed = _safe_urlparse(url)
    if parsed is None:
        return None
    host = (parsed.netloc or "").lower()

    if not parsed.scheme and not host:
        if parsed.path == "/poll":
            # 상대 /poll은 원문(m.dcinside.com) 기준 경로라 미러 도메인에서는 404가 된다.
            # DC 모바일 투표 페이지 절대 주소로 되돌려 iframe이 실제 투표를 렌더링하게 한다.
            return f"{DC_POLL_URL}?{parsed.query}" if parsed.query else DC_POLL_URL
        if parsed.path == "/movie":
            movie_ids = parse_qs(parsed.query).get("no", [])
            return url if movie_ids and movie_ids[0].isdigit() else None
        return None

    if parsed.scheme not in {"", "https"}:
        return None

    if host == "m.dcinside.com" and parsed.path == "/poll":
        return parsed._replace(scheme="https").geturl()

    movie_src = normalize_dc_movie_iframe_src(parsed)
    if movie_src:
        return movie_src

    if host in YOUTUBE_IFRAME_HOSTS:
        if is_safe_youtube_embed_path(parsed.path):
            return parsed._replace(scheme="https").geturl()
        shorts_id = youtube_shorts_video_id(parsed.path)
        if shorts_id:
            return f"https://www.youtube.com/embed/{shorts_id}"

    twitter_src = normalize_twitter_iframe_src(parsed)
    if twitter_src:
        return twitter_src

    return None


def default_iframe_title(src):
    parsed = _safe_urlparse(src)
    if parsed is None:
        return "첨부 콘텐츠"
    if parsed.netloc == "m.dcinside.com" and parsed.path == "/poll":
        return "DCInside 투표"
    if (
        (parsed.netloc == "gall.dcinside.com" and parsed.path == "/board/movie/movie_view")
        or (parsed.netloc == "m.dcinside.com" and parsed.path == "/movie/player")
    ):
        return "DCInside 동영상"
    if (parsed.netloc or "").lower() in YOUTUBE_IFRAME_HOSTS:
        return "YouTube 동영상"
    if (parsed.netloc or "").lower() in TWITTER_PLATFORM_HOSTS:
        return "X 게시물"
    return "첨부 콘텐츠"


def parse_html_fragment(raw_html):
    return BeautifulSoup(raw_html or "", HTML_PARSER)


def sanitize_html_tree(soup):
    for tag in list(soup.find_all(True)):
        if tag.parent is None or not tag.name:
            continue
        name = (tag.name or "").lower()
        if name in HTML_DROP_TAGS:
            tag.decompose()
            continue
        if name not in HTML_ALLOWED_TAGS:
            tag.unwrap()
            continue

        allowed_attrs = HTML_GLOBAL_ATTRS | HTML_TAG_ATTRS.get(name, set())
        for attr in list(tag.attrs):
            attr_name = attr.lower()
            if attr_name.startswith("on") or attr_name not in allowed_attrs:
                del tag.attrs[attr]
                continue

            value = tag.attrs.get(attr)
            if attr_name == "href":
                if not is_safe_href(value):
                    del tag.attrs[attr]
                else:
                    tag["rel"] = "noopener noreferrer"
            elif attr_name == "src":
                if name == "img":
                    if not str(value).startswith("/media?"):
                        tag.decompose()
                        break
                elif name == "video":
                    if not str(value).startswith("/media?"):
                        tag.decompose()
                        break
                elif name == "source":
                    if not str(value).startswith("/media?"):
                        tag.decompose()
                        break
                elif name == "iframe":
                    safe_src = normalize_safe_iframe_src(value)
                    if not safe_src:
                        tag.decompose()
                        break
                    tag["src"] = safe_src
                    tag["loading"] = "lazy"
                    tag["title"] = tag.get("title") or default_iframe_title(safe_src)
                else:
                    tag.decompose()
                    break
            elif attr_name == "poster":
                if name != "video" or not str(value).startswith("/media?"):
                    del tag.attrs[attr]
            elif attr_name == "fetchpriority":
                if name != "img" or str(value).strip().lower() not in {"high", "low", "auto"}:
                    del tag.attrs[attr]
    return soup


def serialize_html_fragment(soup):
    return str(soup)


def sanitize_html_fragment(raw_html):
    soup = parse_html_fragment(raw_html)
    sanitize_html_tree(soup)
    return serialize_html_fragment(soup)


def prepare_read_html(raw_html, images, board, pid, kind, search_keyword=None):
    soup = parse_html_fragment(raw_html)
    rewrite_content_images(soup, images, board, pid, kind)
    rewrite_dcinside_links(soup)
    sanitize_html_tree(soup)
    highlight_soup_text(soup, search_keyword)
    return serialize_html_fragment(soup)


def pick_soup_image_src(tag):
    for key in ("data-gif", "data-original", "data-src", "src"):
        src = tag.get(key)
        if src:
            return src
    return None


def pick_soup_media_src(tag):
    if (tag.name or "").lower() == "video":
        for source in tag.find_all("source"):
            for key in ("src", "data-src", "data-original", "data-mp4"):
                src = source.get(key)
                if src:
                    return src
    for key in ("src", "data-src", "data-original", "data-mp4", "data-gif"):
        src = tag.get(key)
        if src:
            return src
    return None


def rewrite_content_images(soup, images, board, pid, kind):
    image_urls = defaultdict(deque)
    for image_src in images:
        image_urls[image_src].append(url_for("main.media", src=image_src, board=board, pid=pid, kind=kind))

    image_index = 0
    for img in soup.find_all("img"):
        original_src = pick_soup_image_src(img)
        if not original_src or not image_urls[original_src]:
            img.decompose()
            continue
        img["src"] = image_urls[original_src].popleft()
        img["decoding"] = "async"
        if image_index == 0:
            img["loading"] = "eager"
            img["fetchpriority"] = "high"
        else:
            img["loading"] = "lazy"
            img.attrs.pop("fetchpriority", None)
        image_index += 1
        for attr in ("data-original", "data-gif", "srcset"):
            img.attrs.pop(attr, None)

    for source in soup.find_all("source"):
        original_src = pick_soup_media_src(source)
        if not original_src or not image_urls[original_src]:
            source.decompose()
            continue
        source["src"] = image_urls[original_src].popleft()

    for video in soup.find_all("video"):
        original_src = pick_soup_media_src(video)
        if original_src:
            if image_urls[original_src]:
                video["src"] = image_urls[original_src].popleft()
            else:
                video.attrs.pop("src", None)
        poster_src = video.get("poster")
        if poster_src:
            if image_urls[poster_src]:
                video["poster"] = image_urls[poster_src].popleft()
            else:
                video.attrs.pop("poster", None)
        for attr in ("data-original", "data-gif", "data-mp4", "data-src", "srcset"):
            video.attrs.pop(attr, None)

    for iframe in soup.find_all("iframe"):
        movie_id = dc_movie_id_from_iframe_src(iframe.get("src"))
        if not movie_id:
            continue
        iframe["src"] = url_for("main.movie", no=movie_id, board=board, pid=pid, kind=kind)
        iframe["loading"] = "lazy"
        iframe["title"] = iframe.get("title") or "DCInside 동영상"
    return soup


def rewrite_dcinside_links(soup):
    for anchor in soup.find_all("a", href=True):
        href = dcinside_internal_href(anchor.get("href"))
        if not href:
            continue
        anchor["href"] = href
        anchor.attrs.pop("target", None)
    return soup
