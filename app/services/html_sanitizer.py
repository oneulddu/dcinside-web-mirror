from collections import defaultdict, deque
from urllib.parse import urlparse

from bs4 import BeautifulSoup
from flask import url_for


HTML_ALLOWED_TAGS = {
    "a", "abbr", "b", "blockquote", "br", "code", "dd", "del", "div", "dl", "dt",
    "em", "figcaption", "figure", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i",
    "img", "li", "ol", "p", "pre", "s", "span", "strong", "sub", "sup", "table",
    "tbody", "td", "th", "thead", "tr", "u", "ul",
}
HTML_DROP_TAGS = {"script", "style", "iframe", "object", "embed", "link", "meta", "base", "form", "input", "button"}
HTML_GLOBAL_ATTRS = {"class", "title"}
HTML_TAG_ATTRS = {
    "a": {"href", "target", "rel"},
    "img": {"src", "alt", "loading", "decoding", "width", "height"},
    "td": {"colspan", "rowspan"},
    "th": {"colspan", "rowspan"},
}


def is_safe_href(value):
    url = (value or "").strip()
    if not url:
        return False
    parsed = urlparse(url)
    if not parsed.scheme:
        return url.startswith(("#", "/")) and not url.startswith("//")
    return parsed.scheme in {"http", "https", "mailto"}


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
                if name != "img" or not str(value).startswith("/media?"):
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
    return soup
