from collections import defaultdict, deque
import re
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup
from flask import current_app, url_for

from .dc_links import dcinside_internal_href
from .highlight import highlight_soup_text
from .link_preview import normalize_preview_image_url, preview_image_signature


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
    "iframe": {
        "src", "title", "loading", "width", "height", "frameborder", "scrolling",
        "allow", "allowfullscreen", "referrerpolicy",
    },
    "img": {
        "src", "alt", "loading", "decoding", "fetchpriority", "width", "height",
        "data-body-image-src", "data-dccon-src", "hidden",
    },
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
                    src = str(value)
                    if not src.startswith(("/media?", "/embed/link-preview-image?")):
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
            elif attr_name in {"data-body-image-src", "data-dccon-src"}:
                if name != "img" or not str(value).startswith("/media?"):
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
    normalize_twitter_blockquotes(soup)
    normalize_twitter_status_links(soup)
    normalize_og_wraps(soup)
    rewrite_content_images(soup, images, board, pid, kind)
    rewrite_dcinside_links(soup)
    sanitize_html_tree(soup)
    wrap_twitter_iframes(soup)
    mark_link_preview_targets(soup)
    highlight_soup_text(soup, search_keyword)
    return serialize_html_fragment(soup)


def _class_tokens(tag):
    return {str(value).lower() for value in (tag.get("class") or [])}


def _find_og_text(anchor, class_names):
    for tag in anchor.find_all(True):
        classes = _class_tokens(tag)
        if classes.intersection(class_names):
            text = " ".join(tag.stripped_strings).strip()
            if text:
                return text
    return None


def normalize_og_wraps(soup):
    for anchor in list(soup.select("a.og-wrap")):
        href = str(anchor.get("href") or "").strip()
        title = _find_og_text(anchor, {"og-tit", "og-title"})
        if not is_safe_href(href) or not title:
            anchor.decompose()
            continue
        parsed = _safe_urlparse(href)
        host = (parsed.hostname or "").lower() if parsed else ""

        preview = soup.new_tag("a", href=href)
        preview["class"] = ["link-preview"]
        preview["target"] = "_blank"
        preview["rel"] = "noopener noreferrer"

        copy = soup.new_tag("span")
        copy["class"] = ["link-preview-copy"]

        title_tag = soup.new_tag("span")
        title_tag["class"] = ["link-preview-title"]
        title_tag.string = title
        copy.append(title_tag)

        description = _find_og_text(anchor, {"og-desc", "og-description", "og-summary", "og-txt"})
        if description:
            description_tag = soup.new_tag("span")
            description_tag["class"] = ["link-preview-desc"]
            description_tag.string = description
            copy.append(description_tag)

        host_tag = soup.new_tag("span")
        host_tag["class"] = ["link-preview-host"]
        host_tag.string = host
        copy.append(host_tag)
        preview.append(copy)

        image = anchor.find("img")
        raw_image_src = pick_soup_image_src(image) if image else None
        image_url = normalize_preview_image_url(raw_image_src, base_url=href)
        image_token = preview_image_signature(image_url, current_app.secret_key) if image_url else None
        if image_url and image_token:
            media = soup.new_tag("span")
            media["class"] = ["link-preview-media"]
            thumbnail = soup.new_tag("img")
            thumbnail["class"] = ["link-preview-image"]
            thumbnail["src"] = url_for(
                "main.embed_link_preview_image",
                url=image_url,
                token=image_token,
            )
            thumbnail["alt"] = f"{title} 미리보기"
            thumbnail["loading"] = "lazy"
            thumbnail["decoding"] = "async"
            media.append(thumbnail)
            preview.append(media)
            preview["class"].append("has-media")
        anchor.replace_with(preview)
    return soup


def _twitter_status_id_from_tag(tag):
    anchors = tag.find_all("a", href=True) if tag else []
    for anchor in reversed(anchors):
        parsed = _safe_urlparse(anchor.get("href"))
        if parsed is None or (parsed.netloc or "").lower() not in TWITTER_STATUS_HOSTS:
            continue
        tweet_id = tweet_id_from_status_path(parsed.path)
        if tweet_id:
            return tweet_id
    return None


def _twitter_figure(soup, tweet_id):
    figure = soup.new_tag("figure")
    figure["class"] = ["embed-card", "embed-card-twitter"]

    head = soup.new_tag("figcaption")
    head["class"] = ["embed-card-head"]
    label = soup.new_tag("span")
    label["class"] = ["embed-card-label"]
    label.string = "X 게시물"
    source = soup.new_tag("a", href=f"https://x.com/i/status/{tweet_id}")
    source["class"] = ["embed-card-source"]
    source["target"] = "_blank"
    source["rel"] = "noopener noreferrer"
    source.string = "안 보이면 X에서 열기"
    head.extend([label, source])

    iframe = soup.new_tag("iframe", src=TWITTER_EMBED_URL.format(tweet_id))
    iframe["title"] = "X 게시물"
    iframe["loading"] = "lazy"
    iframe["referrerpolicy"] = "strict-origin-when-cross-origin"

    fallback = soup.new_tag("blockquote")
    fallback["class"] = ["embed-card-fallback"]
    fallback_text = soup.new_tag("p")
    fallback_text.append("X 미리보기를 표시할 수 없습니다. ")
    fallback_link = soup.new_tag("a", href=f"https://x.com/i/status/{tweet_id}")
    fallback_link["target"] = "_blank"
    fallback_link["rel"] = "noopener noreferrer"
    fallback_link.string = "X에서 원문 열기"
    fallback_text.append(fallback_link)
    fallback.append(fallback_text)

    figure.extend([head, iframe, fallback])
    return figure, iframe, fallback


def _remove_empty_anchor_wrappers(anchor):
    parent = anchor.parent
    anchor.decompose()
    for _ in range(3):
        if (
            parent is None
            or getattr(parent, "name", None) not in {"div", "p", "span"}
            or parent.get_text("", strip=True)
            or parent.find(True)
        ):
            break
        next_parent = parent.parent
        parent.decompose()
        parent = next_parent


def normalize_twitter_blockquotes(soup):
    converted_ids = set()
    for quote in list(soup.select("blockquote.twitter-tweet")):
        tweet_id = _twitter_status_id_from_tag(quote)
        if not tweet_id:
            continue
        figure, _, fallback = _twitter_figure(soup, tweet_id)
        quote.replace_with(figure)
        quote["class"] = ["embed-card-fallback"]
        fallback.replace_with(quote)
        converted_ids.add(tweet_id)

    if not converted_ids:
        return soup

    for anchor in list(soup.find_all("a", href=True)):
        if anchor.find_parent("figure", class_="embed-card-twitter"):
            continue
        parsed = _safe_urlparse(anchor.get("href"))
        if parsed is None or (parsed.netloc or "").lower() not in TWITTER_STATUS_HOSTS:
            continue
        tweet_id = tweet_id_from_status_path(parsed.path)
        text = " ".join(anchor.stripped_strings).strip()
        href = str(anchor.get("href") or "").strip()
        if tweet_id in converted_ids and text in {href, href.split("?", 1)[0]}:
            _remove_empty_anchor_wrappers(anchor)
    return soup


def _is_bare_twitter_status_link(anchor, href):
    parsed = _safe_urlparse(href)
    if (
        parsed is None
        or parsed.scheme != "https"
        or (parsed.netloc or "").lower() not in TWITTER_STATUS_HOSTS
        or not tweet_id_from_status_path(parsed.path)
    ):
        return False

    text = " ".join(anchor.stripped_strings).strip()
    without_fragment = href.split("#", 1)[0]
    without_query = without_fragment.split("?", 1)[0]
    without_scheme = href.split("://", 1)[1]
    without_scheme_fragment = without_scheme.split("#", 1)[0]
    without_scheme_query = without_scheme_fragment.split("?", 1)[0]
    return text in {
        href,
        without_fragment,
        without_query,
        without_scheme,
        without_scheme_fragment,
        without_scheme_query,
    }


def _standalone_twitter_link_container(anchor):
    """p/span 안의 단독 링크만 안전한 블록 컨테이너까지 끌어올린다."""
    container = anchor
    parent = anchor.parent
    while getattr(parent, "name", None) in {"p", "span"}:
        if any(
            child is not container
            and getattr(child, "name", None) != "br"
            and str(child).strip()
            for child in parent.contents
        ):
            return None
        container = parent
        parent = parent.parent
    return container


def _twitter_iframe_ids(soup):
    tweet_ids = set()
    for iframe in soup.find_all("iframe", src=True):
        parsed = _safe_urlparse(iframe.get("src"))
        if parsed is None:
            continue
        normalized_src = normalize_twitter_iframe_src(parsed)
        if not normalized_src:
            continue
        normalized = _safe_urlparse(normalized_src)
        values = parse_qs(normalized.query).get("id", []) if normalized else []
        if values and values[0].isdigit():
            tweet_ids.add(values[0])
    return tweet_ids


def normalize_twitter_status_links(soup):
    """DC가 위젯 마크업을 만들지 않은 맨몸 X status 링크도 공식 카드로 승격한다."""
    embedded_ids = _twitter_iframe_ids(soup)
    for anchor in list(soup.find_all("a", href=True)):
        if anchor.find_parent("figure", class_="embed-card-twitter"):
            continue
        href = str(anchor.get("href") or "").strip()
        if not _is_bare_twitter_status_link(anchor, href):
            continue
        container = _standalone_twitter_link_container(anchor)
        if container is None:
            continue
        parsed = _safe_urlparse(href)
        tweet_id = tweet_id_from_status_path(parsed.path)
        if tweet_id in embedded_ids:
            container.decompose()
            continue
        figure, _, _ = _twitter_figure(soup, tweet_id)
        container.replace_with(figure)
        embedded_ids.add(tweet_id)
    return soup


def wrap_twitter_iframes(soup):
    for iframe in list(soup.find_all("iframe", src=True)):
        parsed = _safe_urlparse(iframe.get("src"))
        if parsed is None or (parsed.netloc or "").lower() not in TWITTER_PLATFORM_HOSTS:
            continue
        tweet_ids = parse_qs(parsed.query).get("id", [])
        if not tweet_ids or not tweet_ids[0].isdigit():
            continue
        if iframe.find_parent("figure", class_="embed-card-twitter"):
            continue
        tweet_id = tweet_ids[0]

        figure, normalized_iframe, _ = _twitter_figure(soup, tweet_id)
        normalized_iframe.attrs.update(iframe.attrs)
        normalized_iframe["src"] = TWITTER_EMBED_URL.format(tweet_id)
        normalized_iframe["title"] = iframe.get("title") or "X 게시물"
        normalized_iframe["loading"] = "lazy"
        iframe.replace_with(figure)
    return soup


def _excluded_preview_host(host):
    excluded = ("dcinside.com", "youtube.com", "youtu.be", "x.com", "twitter.com")
    return any(host == domain or host.endswith("." + domain) for domain in excluded)


def _is_matching_preview(tag, href):
    return bool(
        tag
        and getattr(tag, "name", None) == "a"
        and "link-preview" in _class_tokens(tag)
        and str(tag.get("href") or "").strip() == href
    )


def _has_adjacent_preview(anchor, href):
    if _is_matching_preview(anchor.find_next_sibling(), href):
        return True
    block_names = {
        "blockquote", "dd", "div", "dl", "dt", "figcaption", "figure", "li",
        "ol", "p", "pre", "table", "td", "th", "tr", "ul",
    }
    parent = anchor.parent
    while parent and getattr(parent, "name", None):
        if parent.name in block_names:
            return _is_matching_preview(parent.find_next_sibling(), href)
        parent = parent.parent
    return False


def mark_link_preview_targets(soup):
    for anchor in soup.find_all("a", href=True):
        href = str(anchor.get("href") or "").strip()
        parsed = _safe_urlparse(href)
        if parsed is None or parsed.scheme not in {"http", "https"} or not parsed.hostname:
            continue
        host = parsed.hostname.lower()
        if _excluded_preview_host(host):
            continue
        text = " ".join(anchor.stripped_strings).strip()
        without_scheme = href.split("://", 1)[1] if "://" in href else href
        if text not in {href, without_scheme}:
            continue
        if _has_adjacent_preview(anchor, href):
            continue
        classes = list(anchor.get("class") or [])
        if "link-preview-target" not in classes:
            anchor["class"] = classes + ["link-preview-target"]
    return soup


def pick_soup_image_src(tag):
    for key in ("data-gif", "data-original", "data-src", "src"):
        src = tag.get(key)
        if src:
            return src
    return None


def is_dccon_image(tag, src):
    classes = {value.lower() for value in (tag.get("class") or [])}
    if classes.intersection({"dccon", "written_dccon"}):
        return True
    parsed = _safe_urlparse(src)
    if parsed is None:
        return False
    host = (parsed.hostname or "").lower()
    return host == "dccon.dcinside.com"


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
        if (
            "link-preview-image" in (img.get("class") or [])
            and str(img.get("src") or "").startswith("/embed/link-preview-image?")
        ):
            continue
        original_src = pick_soup_image_src(img)
        if not original_src or not image_urls[original_src]:
            img.decompose()
            continue
        proxied_src = image_urls[original_src].popleft()
        classes = list(img.get("class") or [])
        if is_dccon_image(img, original_src):
            img["class"] = list(dict.fromkeys(classes + ["dccon", "body-dccon"]))
            img["data-dccon-src"] = proxied_src
        else:
            img["class"] = list(dict.fromkeys(classes + ["body-image"]))
            img["data-body-image-src"] = proxied_src
        img.attrs.pop("src", None)
        img["hidden"] = ""
        img["decoding"] = "async"
        if image_index == 0:
            img["loading"] = "eager"
            img["fetchpriority"] = "high"
        else:
            img["loading"] = "lazy"
            img.attrs.pop("fetchpriority", None)
        image_index += 1
        for attr in ("data-original", "data-gif", "data-src", "srcset"):
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
        if "link-preview" in (anchor.get("class") or []):
            continue
        href = dcinside_internal_href(anchor.get("href"))
        if not href:
            continue
        anchor["href"] = href
        anchor.attrs.pop("target", None)
    return soup
