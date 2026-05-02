(function () {
    "use strict";

    function removeStatusRows(list) {
        var rows = list.querySelectorAll("[data-related-loader-status='1'], .empty-row");
        for (var i = 0; i < rows.length; i += 1) {
            rows[i].remove();
        }
    }

    function appendStatusRow(list, text) {
        removeStatusRows(list);
        var li = document.createElement("li");
        li.className = "empty-row";
        li.dataset.relatedLoaderStatus = "1";
        li.textContent = text;
        list.appendChild(li);
    }

    function normalizePostId(value) {
        if (value === null || value === undefined) {
            return "";
        }
        return String(value).trim();
    }

    function getItemPostId(item) {
        if (!item) {
            return "";
        }
        return normalizePostId(item.id || item.doc_id || item.no || item.pid);
    }

    function extractPostIdFromHref(href) {
        if (!href) {
            return "";
        }
        try {
            return new URL(href, window.location.href).searchParams.get("pid") || "";
        } catch (err) {
            return "";
        }
    }

    function getPostIdFromLink(link) {
        if (!link) {
            return "";
        }
        var parent = link.closest("li");
        return normalizePostId(
            link.dataset.postId ||
            (parent && parent.dataset.postId) ||
            extractPostIdFromHref(link.getAttribute("href"))
        );
    }

    function getRenderedPostIds(list) {
        var ids = {};
        var links = list.querySelectorAll("a.feed-item");
        for (var i = 0; i < links.length; i += 1) {
            var postId = getPostIdFromLink(links[i]);
            if (postId) {
                ids[postId] = true;
            }
        }
        return ids;
    }

    function getLastRenderedPostId(list) {
        var links = list.querySelectorAll("a.feed-item");
        for (var i = links.length - 1; i >= 0; i -= 1) {
            var postId = getPostIdFromLink(links[i]);
            if (postId) {
                return postId;
            }
        }
        return "";
    }

    function escapeHtml(value) {
        return String(value || "").replace(/[&<>"']/g, function (char) {
            return {
                "&": "&amp;",
                "<": "&lt;",
                ">": "&gt;",
                "\"": "&quot;",
                "'": "&#39;"
            }[char];
        });
    }

    function escapeRegExp(value) {
        return String(value || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
    }

    function highlightSearchTerm(value, keyword) {
        var text = String(value || "제목 없음");
        var term = String(keyword || "").trim();
        if (!term) {
            return escapeHtml(text);
        }
        return escapeHtml(text).replace(
            new RegExp(escapeRegExp(escapeHtml(term)), "gi"),
            function (matched) {
                return '<mark class="search-highlight">' + matched + "</mark>";
            }
        );
    }

    function buildReadHref(board, item, kind, recommend, sourcePage, searchType, searchKeyword) {
        var pid = getItemPostId(item);
        var href = "/read?board=" + encodeURIComponent(board) + "&pid=" + encodeURIComponent(pid);
        var itemSourcePage = item && item.source_page ? String(item.source_page) : "";
        if (recommend === "1") {
            href += "&recommend=1";
        }
        if (itemSourcePage || sourcePage) {
            href += "&source_page=" + encodeURIComponent(itemSourcePage || sourcePage);
        }
        if (kind) {
            href += "&kind=" + encodeURIComponent(kind);
        }
        if (searchKeyword) {
            href += "&s_type=" + encodeURIComponent(searchType || "subject_m");
            href += "&serval=" + encodeURIComponent(searchKeyword);
        }
        return href;
    }

    function postHasImage(item) {
        return normalizeBoolean(item && item.has_image) === true || normalizeBoolean(item && item.isimage) === true;
    }

    function postHasVideo(item) {
        return normalizeBoolean(item && item.has_video) === true || normalizeBoolean(item && item.isvideo) === true;
    }

    function postIsRecommend(item) {
        return normalizeBoolean(item && item.isrecommend) === true;
    }

    function createFeedStatusIcon(item) {
        var hasImage = postHasImage(item);
        var hasVideo = postHasVideo(item);
        var isRecommend = postIsRecommend(item);
        var span = document.createElement("span");

        if (isRecommend) {
            span.className = "feed-recommend-icon" + (hasImage || hasVideo ? " is-hot" : " is-plain");
            span.setAttribute("aria-label", "개념글");
            span.setAttribute("title", "개념글");
            span.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path class="flame-outer" d="M12 22c4.4 0 7.5-3.2 7.5-7.7 0-3.2-1.7-6-4.6-8.5-.4 2.3-1.5 3.7-3.1 4.7.2-3.1-.9-5.8-3.3-8.1.2 3.4-1.2 5.1-2.6 6.9-1.1 1.4-2.1 2.8-2.1 5C3.8 18.8 7 22 12 22z"></path><path class="flame-inner" d="M12.1 19.2c2 0 3.4-1.4 3.4-3.4 0-1.5-.8-2.7-2.2-3.8-.2 1.1-.8 1.8-1.7 2.3.1-1.5-.5-2.8-1.7-3.9.1 1.7-.6 2.5-1.2 3.3-.5.7-.9 1.3-.9 2.2 0 2 1.5 3.3 4.3 3.3z"></path></svg>';
            return span;
        }

        if (hasVideo) {
            span.className = "feed-play-icon";
            span.setAttribute("aria-label", "동영상 첨부");
            span.setAttribute("title", "동영상 첨부");
            span.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><circle cx="12" cy="12" r="8.5"></circle><path d="M10 8.8v6.4L15.2 12z"></path></svg>';
            return span;
        }

        if (hasImage) {
            span.className = "feed-image-icon";
            span.setAttribute("aria-label", "사진 첨부");
            span.setAttribute("title", "사진 첨부");
            span.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><rect x="3" y="5" width="18" height="14" rx="2.4"></rect><circle cx="8.5" cy="10" r="1.8"></circle><path d="M5.5 17l4.4-4.6 3.1 3.1 2.2-2.4 3.3 3.9"></path></svg>';
            return span;
        }

        return null;
    }

    function createItemNode(item, board, kind, recommend, sourcePage, searchType, searchKeyword) {
        var postId = getItemPostId(item);
        var li = document.createElement("li");
        li.dataset.postId = postId;

        var link = document.createElement("a");
        link.className = "feed-item";
        link.dataset.postId = postId;
        link.href = buildReadHref(board, item, kind, recommend, sourcePage, searchType, searchKeyword);

        var titleWrap = document.createElement("div");
        titleWrap.className = "feed-title-wrap";

        var icon = createFeedStatusIcon(item);
        if (icon) {
            titleWrap.appendChild(icon);
        }

        var title = document.createElement("h2");
        title.className = "feed-title";
        title.innerHTML = highlightSearchTerm(item.title || "제목 없음", searchKeyword);
        titleWrap.appendChild(title);

        if ((item.comment_count || 0) > 0) {
            var reply = document.createElement("span");
            reply.className = "reply-count";
            reply.textContent = "[" + String(item.comment_count) + "]";
            titleWrap.appendChild(reply);
        }

        var metaRow = document.createElement("div");
        metaRow.className = "feed-meta-row";

        var metaLeft = document.createElement("div");
        metaLeft.className = "feed-meta-left";

        if (item.subject) {
            var subject = document.createElement("span");
            subject.className = "post-subject";
            subject.textContent = "[" + String(item.subject) + "]";
            metaLeft.appendChild(subject);
        }

        var author = document.createElement("span");
        author.className = "author-text";
        author.textContent = (item.author || "익명") + (item.author_code ? "(" + String(item.author_code) + ")" : "");
        metaLeft.appendChild(author);

        var sep = document.createElement("span");
        sep.className = "sep";
        sep.textContent = "|";
        metaLeft.appendChild(sep);

        var time = document.createElement("span");
        time.textContent = item.time || "-";
        metaLeft.appendChild(time);

        var metaRight = document.createElement("div");
        metaRight.className = "feed-meta-right soft";
        metaRight.textContent = "추천 " + String(item.voteup_count || 0);

        metaRow.appendChild(metaLeft);
        metaRow.appendChild(metaRight);

        link.appendChild(titleWrap);
        link.appendChild(metaRow);
        li.appendChild(link);
        return li;
    }

    function appendItems(list, items, board, kind, recommend, sourcePage, searchType, searchKeyword) {
        var appended = 0;
        var renderedIds = getRenderedPostIds(list);

        if (!Array.isArray(items) || items.length === 0) {
            return appended;
        }

        for (var i = 0; i < items.length; i += 1) {
            var item = items[i];
            var postId = getItemPostId(item);
            if (!postId || renderedIds[postId]) {
                continue;
            }
            list.appendChild(createItemNode(item, board, kind, recommend, sourcePage, searchType, searchKeyword));
            renderedIds[postId] = true;
            appended += 1;
        }
        return appended;
    }

    function setButtonLabel(button, text) {
        var label = button && button.querySelector("[data-related-more-label]");
        if (label) {
            label.textContent = text;
            return;
        }
        if (button) {
            button.textContent = text;
        }
    }

    function setButtonState(button, state) {
        if (!button) {
            return;
        }

        button.dataset.state = state || "idle";
        button.classList.toggle("is-loading", state === "loading");
        button.classList.toggle("is-terminal", state === "no-more");

        if (state === "loading") {
            button.disabled = true;
            setButtonLabel(button, "불러오는 중");
            return;
        }
        if (state === "no-more") {
            button.disabled = true;
            setButtonLabel(button, "더 없음");
            return;
        }
        if (state === "refresh") {
            button.disabled = false;
            setButtonLabel(button, "다시 확인");
            return;
        }
        if (state === "retry") {
            button.disabled = false;
            setButtonLabel(button, "다시 시도");
            return;
        }

        button.disabled = false;
        setButtonLabel(button, button.dataset.defaultLabel || "더보기");
    }

    function hasOwn(obj, key) {
        return !!obj && Object.prototype.hasOwnProperty.call(obj, key);
    }

    function normalizeBoolean(value) {
        if (value === true || value === 1 || value === "1" || value === "true") {
            return true;
        }
        if (value === false || value === 0 || value === "0" || value === "false" || value === null) {
            return false;
        }
        return null;
    }

    function responseHasMore(payload) {
        var fields = ["has_more", "hasMore", "has_next", "hasNext"];
        for (var i = 0; i < fields.length; i += 1) {
            if (hasOwn(payload, fields[i])) {
                var parsed = normalizeBoolean(payload[fields[i]]);
                if (parsed !== null) {
                    return parsed;
                }
            }
        }
        if (hasOwn(payload, "next_cursor")) {
            return !!payload.next_cursor;
        }
        if (hasOwn(payload, "nextCursor")) {
            return !!payload.nextCursor;
        }
        return null;
    }

    function applyLoadedItems(context, button, items, payload) {
        removeStatusRows(context.list);

        var appended = appendItems(
            context.list,
            items,
            context.board,
            context.kind,
            context.recommend,
            context.sourcePage,
            context.searchType,
            context.searchKeyword
        );
        var hasMore = responseHasMore(payload || {});

        if (hasMore === false) {
            appendStatusRow(context.list, "더 불러올 게시글이 없습니다.");
            setButtonState(button, "no-more");
            return { appended: appended, hasMore: false };
        }

        if (appended > 0) {
            setButtonState(button, "idle");
            return { appended: appended, hasMore: hasMore };
        }

        if (hasMore === true) {
            appendStatusRow(context.list, "새로 추가된 게시글은 아직 없습니다. 다시 더보기를 누를 수 있어요.");
            setButtonState(button, "idle");
            return { appended: appended, hasMore: true };
        }

        appendStatusRow(context.list, "새로 추가된 게시글은 아직 없습니다. 다시 확인할 수 있어요.");
        setButtonState(button, "refresh");
        return { appended: appended, hasMore: hasMore };
    }

    function buildRequestContext() {
        var section = document.getElementById("related-section");
        var list = document.getElementById("related-list");
        if (!section || !list) {
            return null;
        }

        var board = section.dataset.board || "";
        var pid = section.dataset.pid || "";
        var kind = section.dataset.kind || "";
        var recommend = section.dataset.recommend || "";
        var limit = section.dataset.limit || "12";
        var sourcePage = section.dataset.sourcePage || "";
        var searchType = section.dataset.searchType || "";
        var searchKeyword = section.dataset.searchKeyword || "";
        var afterPid = getLastRenderedPostId(list);

        if (!board || !pid) {
            if (!list.querySelector("a.feed-item")) {
                appendStatusRow(list, "다른 게시글이 없습니다.");
            }
            return null;
        }

        var params = new URLSearchParams();
        params.set("board", board);
        params.set("pid", pid);
        params.set("limit", limit);
        if (kind) {
            params.set("kind", kind);
        }
        if (recommend === "1") {
            params.set("recommend", "1");
        }
        if (sourcePage) {
            params.set("source_page", sourcePage);
        }
        if (afterPid) {
            params.set("after_pid", afterPid);
        }
        if (searchKeyword) {
            params.set("s_type", searchType || "subject_m");
            params.set("serval", searchKeyword);
        }

        return {
            board: board,
            pid: pid,
            kind: kind,
            recommend: recommend,
            limit: limit,
            sourcePage: sourcePage,
            searchType: searchType,
            searchKeyword: searchKeyword,
            afterPid: afterPid,
            list: list,
            params: params
        };
    }

    async function loadRelated() {
        var button = document.getElementById("related-load-button");
        var context = buildRequestContext();
        if (!context) {
            return;
        }

        setButtonState(button, "loading");
        appendStatusRow(context.list, "다른 게시글을 불러오는 중...");

        try {
            var response = await fetch("/read/related?" + context.params.toString(), {
                method: "GET",
                credentials: "same-origin",
                headers: {
                    "Accept": "application/json"
                }
            });
            if (!response.ok) {
                throw new Error("Failed to fetch related posts");
            }
            var payload = await response.json();
            if (payload && payload.ok === false) {
                throw new Error(payload.error || "Failed to fetch related posts");
            }
            var items = Array.isArray(payload.items) ? payload.items : [];
            applyLoadedItems(context, button, items, payload);
        } catch (err) {
            appendStatusRow(context.list, "다른 게시글을 불러오지 못했습니다. 다시 시도할 수 있어요.");
            setButtonState(button, "retry");
        }
    }

    function clearLegacySessionCache() {
        try {
            if (!window.sessionStorage) {
                return;
            }
            for (var i = window.sessionStorage.length - 1; i >= 0; i -= 1) {
                var key = window.sessionStorage.key(i);
                if (key && key.indexOf("mirror:related:") === 0) {
                    window.sessionStorage.removeItem(key);
                }
            }
        } catch (err) {
            // 저장 공간 접근이 차단된 환경에서는 캐시 정리 없이 동작한다.
        }
    }

    function bindRelatedLoader() {
        var button = document.getElementById("related-load-button");
        if (!button) {
            return;
        }
        clearLegacySessionCache();
        if (!button.dataset.defaultLabel) {
            button.dataset.defaultLabel = "더보기";
        }
        setButtonState(button, "idle");
        button.addEventListener("click", loadRelated);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", bindRelatedLoader, { once: true });
    } else {
        bindRelatedLoader();
    }
})();
