import re
from datetime import datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlparse

import lxml.etree
import lxml.html

from .models import Comment, DocumentIndex, Image


def to_int(value, default=0):
    if value is None:
        return default
    digits = re.sub(r"[^0-9-]", "", str(value))
    if not digits:
        return default
    try:
        return int(digits)
    except ValueError:
        return default


def has_gallery_image_icon(value):
    classes = set(str(value or "").split())
    return bool(
        classes.intersection({"sp-lst-img", "sp-lst-recoimg", "icon_pic", "icon_recomimg", "icon_recoimg"})
        or any(token.startswith("icon_pic") for token in classes)
    )


def has_gallery_video_icon(value):
    classes = set(str(value or "").split())
    return bool(
        classes.intersection({
            "sp-lst-play",
            "sp-lst-recoplay",
            "icon_play",
            "icon_movie",
            "icon_video",
        })
        or any(token.startswith(("icon_play", "icon_movie", "icon_video")) for token in classes)
    )


class ParserMixin:
    def __parse_mobile_headtext_tabs(self, parsed):
        tabs = []
        seen = set()
        tab_nodes = parsed.xpath("//ul[contains(@class, 'mal-lst')]//li[a]")

        for node in tab_nodes:
            link_nodes = node.xpath("./a[1]")
            if not link_nodes:
                continue
            link = link_nodes[0]
            label = " ".join(link.text_content().split())
            if not label:
                continue

            href = link.get("href", "") or ""
            head_id = None
            matched = re.search(r"headText_change\(\s*(\d+)\s*\)", href)
            if matched:
                head_id = matched.group(1)

            key = "" if head_id is None else head_id
            if key in seen:
                continue
            seen.add(key)

            node_class = " {} ".format(node.get("class", ""))
            tabs.append(
                {
                    "head_id": head_id,
                    "label": label,
                    "active": " on " in node_class,
                }
            )

        return tabs

    def __is_usable_board_page(self, parsed, text, url):
        if "등록된 게시물이 없습니다." in text:
            return True
        mobile_rows = parsed.xpath(
            "//ul[contains(@class, 'gall-detail-lst')]/li["
            "not(contains(concat(' ', normalize-space(@class), ' '), ' ad ')) and "
            "(.//a[contains(@class, 'lt') and (contains(@href, '/board/') or contains(@href, '/mini/'))])"
            "]"
        )
        pc_rows = parsed.xpath("//tr[contains(@class, 'ub-content') and contains(@class, 'us-post')]")
        search_navigation = parsed.xpath(
            "//a[contains(@href, 's_pos') or contains(@href, 'search_pos')]"
        )
        return bool(mobile_rows or pc_rows or search_navigation)

    def __is_usable_document_page(self, parsed, text, url):
        doc_head_containers = parsed.xpath("//div[contains(@class, 'gallview-tit-box')]")
        if not doc_head_containers:
            doc_head_containers = parsed.xpath("//div[@class='gall-tit-box']")
        if not doc_head_containers:
            doc_head_containers = parsed.xpath("//div[contains(@class, 'gallview_head')]")

        doc_content_container = parsed.xpath("//div[@class='thum-txtin']")
        if not doc_content_container:
            doc_content_container = parsed.xpath("//div[contains(@class, 'writing_view_box')]")
        if not doc_content_container:
            doc_content_container = parsed.xpath("//div[contains(@class, 'thum-txt-area')]")
        return bool(doc_head_containers and doc_content_container)

    def __compact_text(self, node):
        if node is None:
            return ""
        if hasattr(node, "text_content"):
            return " ".join(node.text_content().split())
        return " ".join(str(node).split())

    def __mobile_document_id_from_href(self, href):
        match = re.search(r"/(\d+)(?:[?#]|$)", href or "")
        return match.group(1) if match else None

    def __extract_gallog_author_id(self, value):
        match = re.search(r"(?:gallog\.dcinside\.com/|/gallog/)([^/?'\"#]+)", value or "")
        return match.group(1) if match else None

    def __extract_mobile_author_id(self, row, include_onclick=False):
        block_info = row.xpath(".//*[contains(@class, 'blockInfo')]/@data-info")
        if block_info:
            author_id = (block_info[0] or "").strip()
            if author_id:
                return author_id

        gallog_hrefs = row.xpath(
            ".//a[contains(@href, 'gallog.dcinside.com/') or contains(@href, '/gallog/')]/@href"
        )
        for href in gallog_hrefs:
            author_id = self.__extract_gallog_author_id(href)
            if author_id:
                return author_id

        if include_onclick:
            for onclick_text in row.xpath(".//*[@onclick]/@onclick"):
                author_id = self.__extract_gallog_author_id(onclick_text)
                if author_id:
                    return author_id
        return None

    def __extract_author_role(self, node):
        if node is None:
            return None
        values = []
        for attr in ("class", "src", "data-original", "title", "alt"):
            values.extend(str(value or "") for value in node.xpath(f".//@{attr}"))
        return self.__extract_author_role_from_text(" ".join(values))

    def __extract_author_role_from_text(self, value):
        marker = str(value or "").lower()
        if not marker:
            return None
        if (
            "fix_sub_managernik" in marker
            or "sub_managernik" in marker
            or re.search(r"\bsub-(?:go|nogo)nick\b", marker)
        ):
            return "submanager"
        if re.search(r"(?<!sub_)managernik\.gif", marker) or re.search(r"\bm-(?:go|nogo)nick\b", marker):
            return "manager"
        return None

    def __find_mobile_list_link(self, row):
        link_nodes = row.xpath(
            ".//a[contains(@class, 'lt') and "
            "(contains(@href, '/board/') or contains(@href, '/mini/'))]"
        )
        if not link_nodes:
            link_nodes = row.xpath(
                ".//a[(contains(@href, '/board/') or contains(@href, '/mini/')) "
                "and not(contains(@href, '#comment_box'))]"
            )
        return link_nodes[0] if link_nodes else None

    def __extract_mobile_title_subject(self, link, prefer_icon_sibling=True):
        subject = None
        subject_el = link.xpath(".//span[contains(@class, 'subjectin')]")
        if subject_el:
            title = self.__compact_text(subject_el[0])
            subject_tag = subject_el[0].xpath(".//b")
            if subject_tag:
                subject = self.__compact_text(subject_tag[0]) or None
            return title, subject

        if prefer_icon_sibling:
            title_nodes = link.xpath(".//*[contains(@class,'sp-lst')]/following-sibling::*[1]")
            if title_nodes:
                return self.__compact_text(title_nodes[0]), None
        return self.__compact_text(link), None

    def __extract_mobile_ginfo(self, link, subject=None, allow_subject_cell=True):
        ginfo = [
            self.__compact_text(node)
            for node in link.xpath(".//ul[contains(@class, 'ginfo')]/li")
        ]
        author = "익명"
        post_time = self.__parse_time("")
        time_text = ""
        view_count = 0
        voteup_count = 0
        meta_offset = 0
        if allow_subject_cell and len(ginfo) >= 5:
            subject = subject or ginfo[0] or None
            meta_offset = 1
        if len(ginfo) > meta_offset:
            author = ginfo[meta_offset] or "익명"
        if len(ginfo) > meta_offset + 1:
            time_text = ginfo[meta_offset + 1]
            post_time = self.__parse_time(time_text)
        if len(ginfo) > meta_offset + 2:
            view_count = to_int(ginfo[meta_offset + 2], 0)
        if len(ginfo) > meta_offset + 3:
            voteup_count = to_int(ginfo[meta_offset + 3], 0)
        return subject, author, post_time, view_count, voteup_count, time_text

    def __extract_mobile_author_role_from_ginfo(self, link, allow_subject_cell=True):
        nodes = link.xpath(".//ul[contains(@class, 'ginfo')]/li")
        if not nodes:
            return None
        named_nodes = [
            node
            for node in nodes
            if " list-nick " in " {} ".format(node.get("class", ""))
        ]
        if named_nodes:
            return self.__extract_author_role(named_nodes[0])
        author_offset = 1 if allow_subject_cell and len(nodes) >= 5 else 0
        if author_offset >= len(nodes):
            return None
        return self.__extract_author_role(nodes[author_offset])

    def __extract_mobile_comment_count(self, row, full_text_fallback=False):
        comment_nodes = row.xpath(".//a[contains(@class, 'rt')]//*[contains(@class, 'ct')]")
        if comment_nodes:
            return to_int(self.__compact_text(comment_nodes[0]), 0)
        if full_text_fallback:
            rt_nodes = row.xpath(".//a[contains(@class, 'rt')]")
            if rt_nodes:
                return to_int(self.__compact_text(rt_nodes[0]), 0)
        return 0

    def __mobile_icon_flags(self, link):
        return " ".join(link.xpath(".//span[contains(@class,'sp-lst')]/@class"))

    def __gallery_flags(self, flags, board_id=None, recommend_marker="reco", include_board_best=True, include_issue_hit=False):
        flags = flags or ""
        return {
            "isimage": has_gallery_image_icon(flags),
            "isvideo": has_gallery_video_icon(flags),
            "isrecommend": recommend_marker in flags,
            "isdcbest": ("best" in flags) or (include_board_best and board_id == "dcbest"),
            "ishit": ("hit" in flags) or (include_issue_hit and "issue" in flags),
        }

    def __make_board_index(
        self,
        document_id,
        board_id,
        title,
        author,
        author_id,
        post_time,
        view_count,
        voteup_count,
        comment_count,
        subject,
        flags,
        kind=None,
        recommend=False,
        is_mobile_source=False,
        recommend_marker="reco",
        include_board_best=True,
        include_issue_hit=False,
        time_text=None,
        author_role=None,
    ):
        parsed_flags = self.__gallery_flags(
            flags,
            board_id=board_id,
            recommend_marker=recommend_marker,
            include_board_best=include_board_best,
            include_issue_hit=include_issue_hit,
        )
        return DocumentIndex(
            id=document_id,
            board_id=board_id,
            title=title,
            has_image=parsed_flags["isimage"],
            has_video=parsed_flags["isvideo"],
            author=author,
            author_id=author_id,
            view_count=view_count,
            voteup_count=voteup_count,
            comment_count=comment_count,
            document=lambda b=board_id, d=document_id, k=kind, r=recommend: self.document(b, d, kind=k, recommend=r),
            comments=lambda b=board_id, d=document_id, k=kind: self.comments(b, d, kind=k),
            time=post_time,
            subject=subject,
            isimage=parsed_flags["isimage"],
            isvideo=parsed_flags["isvideo"],
            isrecommend=parsed_flags["isrecommend"],
            isdcbest=parsed_flags["isdcbest"],
            ishit=parsed_flags["ishit"],
            is_mobile_source=is_mobile_source,
            time_text=time_text,
            author_role=author_role,
        )

    def __parse_mobile_list_item(self, row, board_id, kind=None, is_mobile_source=True, recommend=False):
        row_class = " {} ".format(row.get("class", ""))
        if " ad " in row_class:
            return None

        link = self.__find_mobile_list_link(row)
        if link is None:
            return None

        href = link.get("href", "")
        document_id = self.__mobile_document_id_from_href(href)
        if not document_id:
            return None

        title, subject = self.__extract_mobile_title_subject(link)
        if not title:
            return None

        subject, author, post_time, view_count, voteup_count, time_text = self.__extract_mobile_ginfo(
            link,
            subject=subject,
            allow_subject_cell=True,
        )
        author_role = self.__extract_mobile_author_role_from_ginfo(link, allow_subject_cell=True)
        return self.__make_board_index(
            document_id=document_id,
            board_id=board_id,
            title=title,
            author=author,
            author_id=self.__extract_mobile_author_id(row),
            post_time=post_time,
            view_count=view_count,
            voteup_count=voteup_count,
            comment_count=self.__extract_mobile_comment_count(row),
            subject=subject,
            flags=self.__mobile_icon_flags(link),
            kind=kind,
            recommend=recommend,
            is_mobile_source=is_mobile_source,
            time_text=time_text,
            author_role=author_role,
        )

    def __parse_embedded_mobile_posts(self, parsed, board_id, current_document_id, kind=None, recommend=False):
        posts = []
        seen_ids = {str(current_document_id)}
        rows = parsed.xpath(
            "//*[@id='view_next' and "
            "contains(concat(' ', normalize-space(@class), ' '), ' gall-detail-lst ')]/li"
        )
        for row in rows:
            item = self.__parse_mobile_list_item(
                row,
                board_id,
                kind=kind,
                is_mobile_source=True,
                recommend=recommend,
            )
            if item is None or item.id in seen_ids:
                continue
            seen_ids.add(item.id)
            posts.append(item)
        return posts

    def __parse_mobile_comment_li(self, li):
        li_classes = set((li.get("class") or "").split())
        nick_node = li[0] if len(li) > 0 else None
        content_node = li[1] if len(li) > 1 else li
        time_node = li[2] if len(li) > 2 else None

        author_id = None
        if nick_node is not None:
            block_id_nodes = nick_node.xpath(".//*[contains(@class, 'blockCommentId')]")
            if block_id_nodes:
                author_id = block_id_nodes[0].get("data-info", None)
            if not author_id:
                block_ip_nodes = nick_node.xpath(".//*[contains(@class, 'blockCommentIp')]")
                if block_ip_nodes:
                    author_id = "".join(block_ip_nodes[0].itertext()).strip()

        author = "익명"
        if nick_node is not None:
            nick_buttons = nick_node.xpath(".//*[contains(@class, 'nick')]")
            if nick_buttons:
                author = " ".join(nick_buttons[0].itertext()).strip() or "익명"
            else:
                author = " ".join(nick_node.itertext()).strip() or "익명"
        author_role = self.__extract_author_role(nick_node)

        dccon_images = content_node.xpath(
            ".//img[contains(@src, 'dccon') or contains(@data-original, 'dccon') "
            "or contains(@data-gif, 'dccon') or contains(@src, 'dicad')]"
        )
        dccon = None
        if dccon_images:
            dccon = (
                dccon_images[0].get("data-gif")
                or dccon_images[0].get("data-original")
                or dccon_images[0].get("src")
            )

        voice = None
        voice_nodes = content_node.xpath(".//iframe/@src")
        if voice_nodes:
            voice = voice_nodes[0]

        return Comment(
            id=li.get("no"),
            parent_id=li.get("m_no"),
            author=author,
            author_id=author_id,
            contents="\n".join(i.strip() for i in content_node.itertext() if i.strip()),
            dccon=dccon,
            voice=voice,
            time=self.__parse_time(time_node.text_content() if time_node is not None else ""),
            is_reply="comment-add" in li_classes,
            author_role=author_role,
        )

    def __mobile_comment_rows(self, parsed):
        rows = parsed.xpath(
            ".//ul[contains(@class, 'all-comment-lst')]/li["
            "contains(concat(' ', normalize-space(@class), ' '), ' comment ') "
            "or contains(concat(' ', normalize-space(@class), ' '), ' comment-add ') "
            "or (@no and @m_no)"
            "]"
        )
        if rows:
            return rows
        if len(parsed) >= 2:
            return parsed[1].xpath(
                ".//li[contains(concat(' ', normalize-space(@class), ' '), ' comment ') "
                "or contains(concat(' ', normalize-space(@class), ' '), ' comment-add ') "
                "or (@no and @m_no)]"
            )
        return []

    def __parse_embedded_mobile_comments(self, parsed):
        comments = []
        seen_ids = set()
        for li in self.__mobile_comment_rows(parsed):
            comment = self.__parse_mobile_comment_li(li)
            comment_id = str(comment.id or "").strip()
            if comment_id and comment_id in seen_ids:
                continue
            if comment_id:
                seen_ids.add(comment_id)
            comments.append(comment)

        total = 0
        def parse_count_text(value):
            digits = re.sub(r"[^0-9]", "", value or "")
            return int(digits) if digits else 0

        total_nodes = parsed.xpath("string((//input[@id='reple_totalCnt'])[1]/@value)")
        if total_nodes:
            total = parse_count_text(total_nodes)
        if total <= 0:
            title_text = " ".join(
                parsed.xpath("//div[contains(@class, 'all-comment-tit')]//*[contains(@class, 'ct')]/text()")
            )
            total = parse_count_text(title_text)
        return comments, total

    def __extract_top_level_redirect_url(self, text):
        if not text:
            return None

        # Some board pages include login/menu actions with "location.href" deep in the
        # document. Only treat a redirect as real when it appears in the initial payload
        # as a top-level redirect script or meta refresh.
        try:
            parsed = lxml.html.document_fromstring(text)
        except (lxml.etree.ParserError, ValueError):
            return None

        for meta in parsed.xpath("/html/head/meta | /html/body/meta"):
            redirect_url = self.__extract_meta_refresh_url(meta)
            if redirect_url:
                return redirect_url

        for script in parsed.xpath("/html/head/script | /html/body/script"):
            redirect_url = self.__extract_script_redirect_url(script.text_content() or "")
            if redirect_url:
                return redirect_url
        return None

    def __extract_meta_refresh_url(self, meta):
        http_equiv = (meta.get("http-equiv") or "").strip().lower()
        if http_equiv != "refresh":
            return None
        content = (meta.get("content") or "").strip()
        if not content:
            return None
        match = re.search(r"url\s*=\s*([^;]+)", content, re.IGNORECASE)
        if not match:
            return None
        return match.group(1).strip().strip("'\"")

    def __extract_script_redirect_url(self, script_text):
        if not script_text:
            return None

        location_prefix = r"(?:(?:window|top|document)\.)*location"
        patterns = [
            rf"{location_prefix}(?:\.href)?\s*=\s*['\"]([^'\"]+)['\"]",
            rf"{location_prefix}\.(?:replace|assign)\(\s*['\"]([^'\"]+)['\"]\s*\)",
        ]
        for pattern in patterns:
            match = re.search(pattern, script_text, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return None

    def __parse_legacy_mobile_board_row(self, doc, board_id, kind=None, recommend=False, is_mobile_source=True):
        href = ""
        title = ""
        subject = None
        author = "익명"
        author_id = None
        post_time = self.__parse_time("")
        time_text = ""
        view_count = 0
        voteup_count = 0
        comment_count = 0
        classname = ""
        flags = ""

        try:
            # Legacy mobile structure
            href = doc[0].get("href", "")
            if href:
                document_id = href.split("/")[-1].split("?")[0]
            else:
                document_id = ""
            if len(doc[0][1]) == 5:
                subject = doc[0][1][0].text
                author = " ".join(doc[0][1][1].text_content().split()) if len(doc[0][1]) > 1 else "익명"
                time_text = doc[0][1][2].text or ""
                post_time = self.__parse_time(time_text)
                view_count = to_int(doc[0][1][3].text.split()[-1] if doc[0][1][3].text else 0, 0)
                voteup_count = to_int(doc[0][1][4][0].text.split()[-1] if doc[0][1][4][0].text else 0, 0)
            else:
                subject = None
                author = " ".join(doc[0][1][0].text_content().split()) if len(doc[0][1]) > 0 else "익명"
                time_text = doc[0][1][1].text or ""
                post_time = self.__parse_time(time_text)
                view_count = to_int(doc[0][1][2].text.split()[-1] if doc[0][1][2].text else 0, 0)
                voteup_count = to_int(doc[0][1][3].text_content().split()[-1], 0)
            classname = doc[0][0][0].get("class", "")
            title_node = doc[0][0][1]
            title = self.__compact_text(title_node)
            comment_count = to_int(doc[1][0].text if len(doc) > 1 and len(doc[1]) else 0, 0)
            author_id = self.__extract_mobile_author_id(doc, include_onclick=True)
            flags = classname
        except Exception:
            # Best board uses a different mobile list markup.
            link = self.__find_mobile_list_link(doc)
            if link is None:
                return None
            href = link.get("href", "")
            document_id = self.__mobile_document_id_from_href(href)
            if not document_id:
                return None

            title, subject = self.__extract_mobile_title_subject(link, prefer_icon_sibling=False)
            if not title:
                title = self.__compact_text(link)
            subject, author, post_time, view_count, voteup_count, time_text = self.__extract_mobile_ginfo(
                link,
                subject=subject,
                allow_subject_cell=False,
            )
            comment_count = self.__extract_mobile_comment_count(doc, full_text_fallback=True)
            flags = self.__mobile_icon_flags(link)
        author_role = None
        link = self.__find_mobile_list_link(doc)
        if link is not None:
            author_role = self.__extract_mobile_author_role_from_ginfo(link, allow_subject_cell=False)

        if not href:
            return None
        if not document_id or not document_id.isdigit():
            return None

        return self.__make_board_index(
            document_id=document_id,
            board_id=board_id,
            title=title,
            author=author,
            author_id=author_id,
            post_time=post_time,
            view_count=view_count,
            voteup_count=voteup_count,
            comment_count=comment_count,
            subject=subject,
            flags=flags,
            kind=kind,
            recommend=recommend,
            is_mobile_source=is_mobile_source,
            time_text=time_text,
            author_role=author_role,
        )

    def __extract_pc_board_author(self, row):
        author_el = row.xpath(".//td[contains(@class, 'gall_writer')]")
        author = "익명"
        author_id = None
        author_role = None
        if author_el:
            author = (author_el[0].get("data-nick") or "").strip()
            if not author:
                author = self.__compact_text(author_el[0]) or "익명"
            author_id = (author_el[0].get("data-uid") or "").strip()
            if not author_id:
                author_id = (author_el[0].get("data-ip") or "").strip()
            author_id = author_id or None
            author_role = self.__extract_author_role(author_el[0])
        return author, author_id, author_role

    def __extract_pc_board_counts(self, row):
        view_count = to_int("".join(row.xpath(".//td[contains(@class, 'gall_count')]/text()") or []), 0)
        voteup_count = to_int("".join(row.xpath(".//td[contains(@class, 'gall_recommend')]/text()") or []), 0)
        comment_count = to_int("".join(row.xpath(".//a[contains(@class, 'reply_numbox')]//span[contains(@class, 'reply_num')]/text()") or []), 0)
        return view_count, voteup_count, comment_count

    def __pc_board_flags(self, row):
        return " ".join([
            row.get("data-type", ""),
            " ".join(row.xpath(".//td[contains(@class, 'gall_tit')]//em/@class")),
        ])

    def __parse_pc_board_row(self, row, board_id, kind=None, recommend=False, is_mobile_source=False):
        data_no = row.get("data-no", "")
        href_els = row.xpath(".//td[contains(@class, 'gall_tit')]//a[contains(@href, 'view')]")
        if not href_els:
            return None
        href = href_els[0].get("href", "")
        no_match = re.search(r"[?&]no=(\d+)", href)
        document_id = no_match.group(1) if no_match else data_no
        if not document_id or not document_id.isdigit():
            return None

        title = self.__compact_text(href_els[0])
        author, author_id, author_role = self.__extract_pc_board_author(row)

        date_el = row.xpath(".//td[contains(@class, 'gall_date')]")
        time_text = ""
        if date_el:
            time_text = (date_el[0].get("title") or date_el[0].text_content() or "").strip()
        view_count, voteup_count, comment_count = self.__extract_pc_board_counts(row)

        return self.__make_board_index(
            document_id=document_id,
            board_id=board_id,
            title=title,
            author=author,
            author_id=author_id,
            post_time=self.__parse_time(time_text),
            view_count=view_count,
            voteup_count=voteup_count,
            comment_count=comment_count,
            subject=None,
            flags=self.__pc_board_flags(row),
            kind=kind,
            recommend=recommend,
            is_mobile_source=is_mobile_source,
            recommend_marker="recom",
            include_board_best=False,
            include_issue_hit=True,
            time_text=time_text,
            author_role=author_role,
        )

    def __first_text(self, parsed, xpath_expr):
        nodes = parsed.xpath(xpath_expr)
        if not nodes:
            return None
        node = nodes[0]
        if hasattr(node, "text_content"):
            return node.text_content().strip()
        return (node.text or "").strip()

    def __parse_document_header(self, doc_head_container):
        subject = None
        title_subject_el = doc_head_container.xpath(".//span[contains(@class, 'title_subject')]")
        title_headtext_el = doc_head_container.xpath(".//span[contains(@class, 'title_headtext')]")
        title_el = doc_head_container.xpath(".//span[contains(@class, 'tit')]")
        if title_subject_el:
            title = title_subject_el[0].text_content().strip()
            if title_headtext_el:
                subject = title_headtext_el[0].text_content().strip().strip("[]") or None
        elif title_el:
            title = title_el[0].text_content().strip()
        else:
            title = " ".join(doc_head_container.text_content().split()) if doc_head_container.text_content() else "제목 없음"

        author = "익명"
        author_id = None
        author_role = self.__extract_author_role(doc_head_container)

        # Mobile view often exposes writer in ginfo2 first item:
        # <li><a href="/gallog/{id}">닉네임</a></li> or plain "ㅇㅇ(1.2)" text.
        ginfo_author = doc_head_container.xpath(".//ul[contains(@class, 'ginfo2')]/li[1]")
        if ginfo_author:
            author = ginfo_author[0].text_content().strip() or "익명"
            gallog_href = ginfo_author[0].xpath("string((.//a[contains(@href, '/gallog/')])[1]/@href)")
            if gallog_href:
                match = re.search(r"/gallog/([^/?'\"#]+)", gallog_href)
                if match:
                    author_id = match.group(1)

        if author == "익명":
            author_el = doc_head_container.xpath(".//span[@class='nickname'] | .//span[contains(@class, 'nickname')]")
            if author_el:
                author = author_el[0].text_content().strip() or "익명"

        author_id_el = doc_head_container.xpath(".//span[@class='ip']")
        if author_id is None and author_id_el:
            author_id = author_id_el[0].text_content().strip() or None
        if not author_id:
            gallog_hrefs = doc_head_container.xpath(".//a[contains(@href, 'gallog.dcinside.com/')]/@href")
            if gallog_hrefs:
                match = re.search(r"gallog\.dcinside\.com/([^/?'\"#]+)", gallog_hrefs[0])
                if match:
                    author_id = match.group(1)
        if not author_id:
            gallog_hrefs = doc_head_container.xpath(".//a[contains(@href, '/gallog/')]/@href")
            if gallog_hrefs:
                match = re.search(r"/gallog/([^/?'\"#]+)", gallog_hrefs[0])
                if match:
                    author_id = match.group(1)
        if not author_id:
            onclick_nodes = doc_head_container.xpath(".//*[@onclick]/@onclick")
            for onclick_text in onclick_nodes:
                match = re.search(r"gallog\.dcinside\.com/([^/?'\"#]+)", onclick_text)
                if match:
                    author_id = match.group(1)
                    break

        meta_text = " ".join(doc_head_container.text_content().split())
        time_str = None
        # Mobile markup
        time_el = doc_head_container.xpath(".//span[@class='date'] | .//span[contains(@class, 'time')]")
        if time_el:
            time_str = time_el[0].text_content().strip()
        # PC markup
        if not time_str:
            time_el = doc_head_container.xpath(".//span[@class='gall_date'] | .//span[contains(@class, 'gall_date')]")
            if time_el:
                time_str = time_el[0].text_content().strip()
        # Final fallback: parse date-like text in header block.
        if not time_str:
            m = re.search(r"\d{4}\.\d{2}\.\d{2}\s+\d{2}:\d{2}(?::\d{2})?", meta_text)
            if m:
                time_str = m.group(0)
        if not time_str:
            time_str = datetime.now().strftime("%Y.%m.%d %H:%M:%S")

        return {
            "title": title,
            "subject": subject,
            "author": author,
            "author_id": author_id,
            "author_role": author_role,
            "time_str": time_str,
            "meta_text": meta_text,
        }

    def __parse_document_counts(self, parsed, document_id, meta_text):
        # Some boards use different markup and omit legacy ids/classes.
        view_count = to_int(self.__first_text(parsed, "//ul[@class='ginfo2']/li[contains(., '조회')]"), default=-1)
        voteup_count = to_int(self.__first_text(parsed, "//span[@id='recomm_btn']"), default=-1)
        votedown_count = to_int(self.__first_text(parsed, "//span[@id='nonrecomm_btn']"), default=-1)
        logined_voteup_count = to_int(self.__first_text(parsed, "//span[@id='recomm_btn_member']"), default=0)

        if view_count < 0:
            m = re.search(r"조회\s*([0-9,]+)", meta_text)
            view_count = to_int(m.group(1), 0) if m else 0
        if voteup_count < 0:
            # Newer pages expose up count as recommend_view_up_{document_id}
            voteup_count = to_int(self.__first_text(parsed, f"//*[@id='recommend_view_up_{document_id}']"), default=-1)
        if voteup_count < 0:
            m = re.search(r"추천\s*([0-9,]+)", meta_text)
            voteup_count = to_int(m.group(1), 0) if m else 0
        if votedown_count < 0:
            m = re.search(r"(?:비추|비추천)\s*([0-9,]+)", meta_text)
            votedown_count = to_int(m.group(1), 0) if m else 0
        if logined_voteup_count < 0:
            logined_voteup_count = 0

        return view_count, voteup_count, votedown_count, logined_voteup_count

    def __prepare_document_content(self, doc_content):
        for adv in doc_content.xpath("div[@class='adv-groupin']"):
            adv.getparent().remove(adv)
        for adv in doc_content.xpath(".//img"):
            src = adv.get("src", "")
            if (
                src.startswith("https://nstatic")
                and not adv.get("data-original")
                and not self.__is_placeholder_document_image_src(src)
            ):
                adv.getparent().remove(adv)
        return doc_content

    def __pick_document_image_src(self, el):
        # Some posts (e.g. gif/mp4 lazy media) keep the real URL in data-gif.
        for key in ("data-gif", "data-original", "data-src", "src"):
            src = el.get(key)
            if src:
                return src
        return None

    def __pick_document_video_src(self, el):
        tag = (getattr(el, "tag", "") or "").lower()
        if tag == "video":
            for source in el.xpath(".//source"):
                for key in ("src", "data-src", "data-original", "data-mp4"):
                    src = source.get(key)
                    if src:
                        return src
        for key in ("src", "data-src", "data-original", "data-mp4"):
            src = el.get(key)
            if src:
                return src
        return None

    def __pick_change_gif_fallback_image_src(self, video):
        if (getattr(video, "tag", "") or "").lower() != "video":
            return None
        fallback_src = video.get("data-src") or video.get("data-original") or video.get("data-gif")
        if not fallback_src or self.__is_placeholder_document_image_src(fallback_src):
            return None

        direct_sources = video.xpath("./source")
        if not any("change_gif" in (source.get("onerror") or "") for source in direct_sources):
            return None

        primary_video_src = None
        for source in direct_sources:
            primary_video_src = self.__pick_document_video_src(source)
            if primary_video_src:
                break
        if primary_video_src and primary_video_src == fallback_src:
            return None
        return fallback_src

    def __is_placeholder_document_image_src(self, src):
        if not src:
            return True
        parsed = urlparse(src)
        host = (parsed.netloc or "").lower()
        path = parsed.path or ""
        if host == "nstatic.dcinside.com" and (
            path.endswith("/gallview_loading_ori.gif")
            or path.endswith("/m_webp.png")
            or "/dc/m/img/" in path
        ):
            return True
        return False

    def __document_image_elements(self, doc_content):
        return [
            img
            for img in doc_content.xpath(".//img")
            if not (self.__pick_document_image_src(img) or "").startswith("https://img.iacstatic.co.kr")
        ]

    def __real_document_video_sources(self, doc_content):
        sources = []
        for video in doc_content.xpath(".//video"):
            src = self.__pick_document_video_src(video)
            if src and not self.__is_placeholder_document_image_src(src):
                sources.append(src)
        for source in doc_content.xpath(".//source[not(ancestor::video)]"):
            src = self.__pick_document_video_src(source)
            if src and not self.__is_placeholder_document_image_src(src):
                sources.append(src)
        return sources

    def __real_document_video_poster_sources(self, doc_content):
        sources = []
        for video in doc_content.xpath(".//video"):
            poster = video.get("poster")
            if poster and not self.__is_placeholder_document_image_src(poster):
                sources.append(poster)
        return sources

    def __real_document_media_sources(self, doc_content):
        sources = []
        for el in doc_content.xpath(".//img | .//video | .//source[not(ancestor::video)]"):
            tag = (getattr(el, "tag", "") or "").lower()
            if tag == "img":
                src = self.__pick_document_image_src(el)
                if (
                    src
                    and not self.__is_placeholder_document_image_src(src)
                    and not src.startswith("https://img.iacstatic.co.kr")
                ):
                    sources.append({"type": "image", "src": src})
                continue

            if tag == "video":
                fallback_src = self.__pick_change_gif_fallback_image_src(el)
                if fallback_src:
                    sources.append({"type": "image", "src": fallback_src})
                    continue

            src = self.__pick_document_video_src(el)
            if src and not self.__is_placeholder_document_image_src(src):
                sources.append({"type": "video", "src": src})
        return sources

    def __has_placeholder_document_images(self, doc_content):
        return any(
            self.__is_placeholder_document_image_src(self.__pick_document_image_src(img))
            for img in self.__document_image_elements(doc_content)
        )

    def __document_video_element(self, src):
        video = lxml.html.Element("video")
        for attr in ("controls", "autoplay", "loop", "muted", "playsinline"):
            video.set(attr, attr)
        video.set("preload", "metadata")
        source = lxml.html.Element("source")
        source.set("src", src)
        source.set("type", "video/mp4")
        video.append(source)
        return video

    def __document_contents_text(self, doc_content):
        return '\n'.join(i.strip() for i in doc_content.itertext() if i.strip() and not i.strip().startswith("이미지 광고"))

    def __document_images(self, doc_content, board_id, document_id):
        sources = []
        sources.extend(
            src
            for i in doc_content.xpath(".//img")
            for src in [self.__pick_document_image_src(i)]
            if src
            and not self.__is_placeholder_document_image_src(src)
            and not src.startswith("https://img.iacstatic.co.kr")
        )
        sources.extend(self.__real_document_video_sources(doc_content))
        sources.extend(self.__real_document_video_poster_sources(doc_content))
        return [Image(
            src=src,
            board_id=board_id,
            document_id=document_id,
            session=self.session)
            for src in sources]

    def __normalize_poll_url(self, src):
        parsed = urlparse(src or "")
        if parsed.path != "/poll":
            return None
        if parsed.scheme == "https" and parsed.netloc == "m.dcinside.com":
            return parsed.geturl()
        if not parsed.scheme and not parsed.netloc:
            return parsed._replace(scheme="https", netloc="m.dcinside.com").geturl()
        return None

    def __poll_card_element(self, src, poll=None):
        card = lxml.html.Element("div")
        card.set("class", "dc-poll-card")

        title = lxml.html.Element("h3")
        title.set("class", "dc-poll-title")
        title.text = (poll or {}).get("title") or "투표"
        card.append(title)

        meta_items = list((poll or {}).get("meta") or [])
        participant = (poll or {}).get("participant")
        if participant:
            meta_items.insert(0, participant)
        if meta_items:
            meta = lxml.html.Element("ul")
            meta.set("class", "dc-poll-meta")
            for value in meta_items:
                li = lxml.html.Element("li")
                li.text = value
                meta.append(li)
            card.append(meta)

        options = list((poll or {}).get("options") or [])
        results = list((poll or {}).get("results") or [])
        if results:
            result_wrap = lxml.html.Element("div")
            result_wrap.set("class", "dc-poll-results")
            result_title = lxml.html.Element("div")
            result_title.set("class", "dc-poll-results-title")
            result_title.text = "결과 미리보기"
            result_wrap.append(result_title)

            result_list = lxml.html.Element("ol")
            result_list.set("class", "dc-poll-result-list")
            for result in results:
                li = lxml.html.Element("li")
                option = lxml.html.Element("span")
                option.set("class", "dc-poll-result-option")
                option.text = result.get("option") or "-"
                li.append(option)

                stats = lxml.html.Element("span")
                stats.set("class", "dc-poll-result-stats")
                stat_parts = [value for value in [result.get("percent"), result.get("count")] if value]
                stats.text = " · ".join(stat_parts)
                li.append(stats)
                result_list.append(li)
            result_wrap.append(result_list)
            card.append(result_wrap)
        elif options:
            option_list = lxml.html.Element("ol")
            option_list.set("class", "dc-poll-options")
            for option in options:
                li = lxml.html.Element("li")
                li.text = option
                option_list.append(li)
            card.append(option_list)

        actions = lxml.html.Element("div")
        actions.set("class", "dc-poll-actions")
        link = lxml.html.Element("a")
        link.set("class", "dc-poll-link")
        link.set("href", src)
        link.set("rel", "noopener noreferrer")
        link.text = "원본에서 투표하기"
        actions.append(link)
        card.append(actions)
        return card

    def __poll_preview_url(self, src):
        parsed = urlparse(self.__normalize_poll_url(src) or src or "")
        query_items = [(key, value) for key, value in parse_qsl(parsed.query, keep_blank_values=True) if key != "preview"]
        query_items.append(("preview", "1"))
        return parsed._replace(query=urlencode(query_items)).geturl()

    def __parse_poll_summary(self, text):
        parsed = lxml.html.document_fromstring(text or "")
        title = self.__first_text(parsed, "//*[contains(@class, 'vote-tit-inner')]") or "투표"
        meta = [
            " ".join(node.text_content().split())
            for node in parsed.xpath("//*[contains(@class, 'vote-date-lst')]/li")
            if " ".join(node.text_content().split())
        ]
        participant = self.__first_text(parsed, "//*[contains(@class, 'vote-join')]")
        options = [
            " ".join(node.text_content().split())
            for node in parsed.xpath("//*[contains(@class, 'vote-ask-lst')]//*[contains(@class, 'vote-txt')]")
            if " ".join(node.text_content().split())
        ]
        results = []
        for node in parsed.xpath("//*[contains(@class, 'vote-gp-lst')]/li"):
            option = " ".join(node.xpath("string(.//*[contains(@class, 'vote-txt')])").split())
            percent = " ".join(node.xpath("string(.//*[contains(@class, 'percent')])").split())
            count = " ".join(node.xpath("string(.//*[contains(@class, 'vote-ct')])").split())
            if option:
                results.append({"option": option, "percent": percent, "count": count})
        return {
            "title": title,
            "meta": meta,
            "participant": participant,
            "options": options,
            "results": results,
        }

    def __parse_pc_comment(self, raw):
        memo = raw.get("memo") or ""
        contents = memo
        dccon = None
        voice = raw.get("voice") or None

        if memo and "<" in memo:
            try:
                fragment = lxml.html.fragment_fromstring(memo, create_parent="div")
                dccon_candidates = fragment.xpath(
                    ".//img[contains(@src, 'dccon') or contains(@src, 'dicad') or contains(@class, 'written_dccon')]"
                )
                if dccon_candidates:
                    dccon = (
                        dccon_candidates[0].get("data-gif")
                        or dccon_candidates[0].get("data-original")
                        or dccon_candidates[0].get("src")
                    )
                if not voice:
                    voice_nodes = fragment.xpath(".//iframe/@src")
                    if voice_nodes:
                        voice = voice_nodes[0]
                contents = "\n".join(i.strip() for i in fragment.itertext() if i.strip())
            except Exception:
                contents = re.sub(r"<[^>]+>", "", memo).strip()

        author_id = (raw.get("user_id") or "").strip() or (raw.get("ip") or "").strip() or None
        parent_id = str(raw.get("c_no") or raw.get("parent") or "").strip() or None
        author_role = self.__extract_author_role_from_text(
            " ".join(str(raw.get(key) or "") for key in ("nick_icon", "user_icon", "icon", "member_icon"))
        )

        return Comment(
            id=str(raw.get("no") or "").strip() or None,
            parent_id=parent_id,
            author=(raw.get("name") or "").strip() or "익명",
            author_id=author_id,
            contents=contents,
            dccon=dccon,
            voice=voice,
            time=self.__parse_time((raw.get("reg_date") or "").strip()),
            is_reply=str(raw.get("depth") or "0").strip() != "0",
            author_role=author_role,
        )

    def __parse_time(self, time): 
        def fill_missing_year(parsed):
            value = parsed.replace(year=today.year)
            if value > today + timedelta(days=1):
                value = value.replace(year=today.year - 1)
            return value

        try:
            today = datetime.now() 
            if len(time) <= 5: 
                if time.find(":") > 0:
                    return datetime.strptime(time, "%H:%M").replace(year=today.year, month=today.month, day=today.day)
                else:
                    return fill_missing_year(datetime.strptime(time, "%m.%d").replace(hour=23, minute=59, second=59))
            elif len(time) <= 11:
                if time.find(":") > 0:
                    return fill_missing_year(datetime.strptime(time, "%m.%d %H:%M"))
                else:
                    try:
                        return datetime.strptime(time, "%y.%m.%d").replace(hour=23, minute=59, second=59)
                    except ValueError:
                        return datetime.strptime(time, "%Y.%m.%d").replace(hour=23, minute=59, second=59)
            elif len(time) <= 16:
                if time.count(".") >= 2:
                    return datetime.strptime(time, "%Y.%m.%d %H:%M")
                else:
                    return fill_missing_year(datetime.strptime(time, "%m.%d %H:%M:%S"))
            else:
                if "." in time:
                    return datetime.strptime(time, "%Y.%m.%d %H:%M:%S")
                else:
                    return datetime.strptime(time, "%Y-%m-%d %H:%M:%S")
        except Exception:
            return datetime.now()
