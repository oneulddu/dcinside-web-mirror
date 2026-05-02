from collections import defaultdict, deque
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
from flask import url_for


HTML_ALLOWED_TAGS = {
    "a", "abbr", "b", "blockquote", "br", "code", "dd", "del", "div", "dl", "dt",
    "em", "figcaption", "figure", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "iframe",
    "img", "li", "ol", "p", "pre", "s", "span", "strong", "sub", "sup", "table",
    "tbody", "td", "th", "thead", "tr", "u", "ul",
}
HTML_DROP_TAGS = {"script", "style", "object", "embed", "link", "meta", "base", "form", "input", "button"}
HTML_GLOBAL_ATTRS = {"class", "title"}
HTML_TAG_ATTRS = {
    "a": {"href", "target", "rel"},
    "iframe": {"src", "title", "loading", "width", "height", "frameborder", "scrolling", "allow", "allowfullscreen"},
    "img": {"src", "alt", "loading", "decoding", "width", "height"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
}
YOUTUBE_IFRAME_HOSTS = {"youtube.com", "www.youtube.com", "youtube-nocookie.com", "www.youtube-nocookie.com"}
DC_MOVIE_VIEW_URL = "https://gall.dcinside.com/board/movie/movie_view?no={}"


def is_safe_href(value):
    url = (value or "").strip()
    if not url:
        return False
    parsed = urlparse(url)
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
    url = (value or "").strip()
    if not url:
        return None
    return dc_movie_id_from_parsed_url(urlparse(url))


def normalize_dc_movie_iframe_src(parsed):
    movie_id = dc_movie_id_from_parsed_url(parsed)
    if movie_id:
        # The mobile player returns an empty body to desktop iframe requests.
        # If route context is unavailable for a same-origin player rewrite,
        # keep the iframe usable by falling back to DCInside's PC movie URL.
        return DC_MOVIE_VIEW_URL.format(movie_id)
    return None


def normalize_safe_iframe_src(value):
    url = (value or "").strip()
    if not url:
        return None
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()

    if not parsed.scheme and not host:
        if parsed.path == "/poll":
            return url
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

    if host in YOUTUBE_IFRAME_HOSTS and is_safe_youtube_embed_path(parsed.path):
        return parsed._replace(scheme="https").geturl()

    return None


def is_safe_iframe_src(value):
    return normalize_safe_iframe_src(value) is not None


def default_iframe_title(src):
    parsed = urlparse(src or "")
    if parsed.netloc == "m.dcinside.com" and parsed.path == "/poll":
        return "DCInside 투표"
    if (
        (parsed.netloc == "gall.dcinside.com" and parsed.path == "/board/movie/movie_view")
        or (parsed.netloc == "m.dcinside.com" and parsed.path == "/movie/player")
    ):
        return "DCInside 동영상"
    if (parsed.netloc or "").lower() in YOUTUBE_IFRAME_HOSTS:
        return "YouTube 동영상"
    return "첨부 콘텐츠"


def sanitize_html_fragment(raw_html):
    soup = BeautifulSoup(raw_html or "", "html.parser")
    for tag in list(soup.find_all(True)):
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
    return str(soup)


def pick_soup_image_src(tag):
    for key in ("data-gif", "data-original", "src"):
        src = tag.get(key)
        if src:
            return src
    return None


def rewrite_content_images(soup, images, board, pid, kind):
    image_urls = defaultdict(deque)
    for image_src in images:
        image_urls[image_src].append(url_for("main.media", src=image_src, board=board, pid=pid, kind=kind))

    for img in soup.find_all("img"):
        original_src = pick_soup_image_src(img)
        if not original_src or not image_urls[original_src]:
            img.decompose()
            continue
        img["src"] = image_urls[original_src].popleft()
        img["loading"] = "lazy"
        img["decoding"] = "async"
        for attr in ("data-original", "data-gif", "srcset"):
            img.attrs.pop(attr, None)

    for iframe in soup.find_all("iframe"):
        movie_id = dc_movie_id_from_iframe_src(iframe.get("src"))
        if not movie_id:
            continue
        iframe["src"] = url_for("main.movie", no=movie_id, board=board, pid=pid, kind=kind)
        iframe["loading"] = "lazy"
        iframe["title"] = iframe.get("title") or "DCInside 동영상"
    return soup
