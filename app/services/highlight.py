import re

from bs4 import NavigableString
from markupsafe import Markup, escape


SEARCH_HIGHLIGHT_CLASS = "search-highlight"
HTML_HIGHLIGHT_IGNORED_PARENTS = {"script", "style", "textarea", "code", "pre", "mark"}
COMMENT_URL_RE = re.compile(r"(?P<url>(?:https?://|www\.)[^\s<]+)", re.IGNORECASE)
COMMENT_LINK_TRAILING_CHARS = ".,!?;:)]}>'\""


def _search_pattern(keyword):
    term = (keyword or "").strip()
    if not term:
        return None
    return re.compile(re.escape(term), re.IGNORECASE)


def highlight_search_term(value, keyword=None):
    text = "" if value is None else str(value)
    pattern = _search_pattern(keyword)
    if not pattern:
        return escape(text)

    pieces = []
    last = 0
    for match in pattern.finditer(text):
        start, end = match.span()
        pieces.append(str(escape(text[last:start])))
        pieces.append(f'<mark class="{SEARCH_HIGHLIGHT_CLASS}">')
        pieces.append(str(escape(text[start:end])))
        pieces.append("</mark>")
        last = end
    if not pieces:
        return escape(text)
    pieces.append(str(escape(text[last:])))
    return Markup("".join(pieces))


def _split_comment_link_trailing_text(url):
    trailing = []
    while url and url[-1] in COMMENT_LINK_TRAILING_CHARS:
        trailing.append(url[-1])
        url = url[:-1]
    trailing.reverse()
    return url, "".join(trailing)


def linkify_comment_text(value):
    text = "" if value is None else str(value)
    pieces = []
    last = 0

    for match in COMMENT_URL_RE.finditer(text):
        start, end = match.span("url")
        raw_url = match.group("url")
        url, trailing = _split_comment_link_trailing_text(raw_url)
        if not url:
            continue

        pieces.append(str(escape(text[last:start])))
        href = url if url.lower().startswith(("http://", "https://")) else f"https://{url}"
        pieces.append(
            '<a href="{}" target="_blank" rel="noopener noreferrer nofollow">'.format(
                escape(href)
            )
        )
        pieces.append(str(escape(url)))
        pieces.append("</a>")
        pieces.append(str(escape(trailing)))
        last = end

    if not pieces:
        return escape(text)

    pieces.append(str(escape(text[last:])))
    return Markup("".join(pieces).replace("\n", "<br>"))


def highlight_soup_text(soup, keyword):
    pattern = _search_pattern(keyword)
    if not pattern:
        return soup

    for node in list(soup.find_all(string=pattern)):
        if not isinstance(node, NavigableString):
            continue
        parent = node.parent
        if parent and parent.name in HTML_HIGHLIGHT_IGNORED_PARENTS:
            continue
        text = str(node)
        parts = []
        last = 0
        for match in pattern.finditer(text):
            start, end = match.span()
            if start > last:
                parts.append(NavigableString(text[last:start]))
            mark = soup.new_tag("mark")
            mark["class"] = SEARCH_HIGHLIGHT_CLASS
            mark.string = text[start:end]
            parts.append(mark)
            last = end
        if last < len(text):
            parts.append(NavigableString(text[last:]))
        if parts:
            node.replace_with(*parts)
    return soup
