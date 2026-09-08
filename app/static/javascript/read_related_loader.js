(function () {
    "use strict";

    var REQUEST_TIMEOUT_MS = 15000;
    var END_MESSAGE = "더 불러올 게시글이 없습니다.";
    var LOADING_MESSAGE = "다른 게시글을 불러오는 중...";

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
        li.setAttribute("aria-hidden", "true");
        li.textContent = text;
        list.appendChild(li);
    }

    function announce(state, message) {
        if (state && state.statusRegion) {
            state.statusRegion.textContent = message || "";
        }
    }

    function showStatus(state, message) {
        appendStatusRow(state.list, message);
        announce(state, message);
    }

    function setListBusy(state, busy) {
        if (state && state.list) {
            state.list.setAttribute("aria-busy", busy ? "true" : "false");
        }
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

    function getRenderedPostState(list) {
        var ids = {};
        var links = list.querySelectorAll("a.feed-item");
        var lastPostId = "";
        var lastSourcePage = "";
        for (var i = 0; i < links.length; i += 1) {
            var postId = getPostIdFromLink(links[i]);
            if (postId) {
                ids[postId] = true;
                lastPostId = postId;
                try {
                    lastSourcePage = normalizeSourcePage(new URL(links[i].getAttribute("href"), window.location.href).searchParams.get("source_page"));
                } catch (err) {
                    lastSourcePage = "";
                }
            }
        }
        return {
            ids: ids,
            lastPostId: lastPostId,
            lastSourcePage: lastSourcePage
        };
    }

    function normalizeSourcePage(value) {
        var page = String(value || "").trim();
        return /^[1-9]\d*$/.test(page) ? page : "";
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
        var pattern = new RegExp(escapeRegExp(term), "gi");
        var result = "";
        var offset = 0;
        var match;
        while ((match = pattern.exec(text)) !== null) {
            result += escapeHtml(text.slice(offset, match.index));
            result += '<mark class="search-highlight">' + escapeHtml(match[0]) + "</mark>";
            offset = match.index + match[0].length;
        }
        return result + escapeHtml(text.slice(offset));
    }

    function formatSubject(value) {
        var subject = String(value || "").trim();
        if (!subject) {
            return "";
        }
        if (subject.charAt(0) === "[" && subject.charAt(subject.length - 1) === "]") {
            return subject;
        }
        return "[" + subject + "]";
    }

    function buildReadHref(board, item, kind, recommend, sourcePage, searchType, searchKeyword, headId, galleryName) {
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
        if (headId) {
            href += "&headid=" + encodeURIComponent(headId);
        }
        if (searchKeyword) {
            href += "&s_type=" + encodeURIComponent(searchType || "subject_m");
            href += "&serval=" + encodeURIComponent(searchKeyword);
        }
        if (galleryName) {
            href += "&gallery_name=" + encodeURIComponent(galleryName);
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
            span.className = "feed-recommend-icon" + (hasVideo ? " is-video" : (hasImage ? " is-hot" : " is-plain"));
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

    function getAuthorRoleClass(item) {
        var role = String((item && item.author_role) || "").trim();
        if (role === "manager" || role === "submanager") {
            return " author-role-" + role;
        }
        return "";
    }

    function createItemNode(item, board, kind, recommend, sourcePage, searchType, searchKeyword, headId, galleryName) {
        var postId = getItemPostId(item);
        var li = document.createElement("li");
        li.dataset.postId = postId;

        var link = document.createElement("a");
        link.className = "feed-item";
        link.dataset.postId = postId;
        link.href = buildReadHref(board, item, kind, recommend, sourcePage, searchType, searchKeyword, headId, galleryName);

        var titleWrap = document.createElement("div");
        titleWrap.className = "feed-title-wrap";

        var icon = createFeedStatusIcon(item);
        if (icon) {
            titleWrap.appendChild(icon);
        }

        var title = document.createElement("span");
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
            subject.textContent = formatSubject(item.subject);
            metaLeft.appendChild(subject);
        }

        var author = document.createElement("span");
        author.className = "author-text" + getAuthorRoleClass(item);
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
        metaRight.className = "feed-meta-right";
        metaRight.textContent = "추천 " + String(item.voteup_count || 0);

        metaRow.appendChild(metaLeft);
        metaRow.appendChild(metaRight);

        link.appendChild(titleWrap);
        link.appendChild(metaRow);
        li.appendChild(link);
        return li;
    }

    function appendItems(context, items) {
        var appended = 0;
        var renderedIds = context.renderedIds || {};

        if (!Array.isArray(items) || items.length === 0) {
            return appended;
        }

        for (var i = 0; i < items.length; i += 1) {
            var item = items[i];
            var postId = getItemPostId(item);
            if (!postId || renderedIds[postId]) {
                continue;
            }
            context.list.appendChild(createItemNode(
                item,
                context.board,
                context.kind,
                context.recommend,
                context.sourcePage,
                context.searchType,
                context.searchKeyword,
                context.headId,
                context.galleryName
            ));
            renderedIds[postId] = true;
            context.lastPostId = postId;
            context.lastSourcePage = normalizeSourcePage(item.source_page) || context.sourcePage;
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
        if (value === false || value === 0 || value === "0" || value === "false") {
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

    function applyLoadedItems(context, state, items, payload) {
        var button = state.button;
        removeStatusRows(context.list);

        var appended = appendItems(context, items);
        var hasMore = responseHasMore(payload || {});
        var loaded = appended > 0 ? "게시글 " + String(appended) + "개를 더 불러왔습니다." : "";

        if (hasMore === false) {
            appendStatusRow(context.list, END_MESSAGE);
            announce(state, loaded ? loaded + " " + END_MESSAGE : END_MESSAGE);
            setButtonState(button, "no-more");
            return { appended: appended, hasMore: false };
        }

        if (appended > 0) {
            announce(state, loaded);
            setButtonState(button, "idle");
            return { appended: appended, hasMore: hasMore };
        }

        if (hasMore === true) {
            showStatus(state, "새로 추가된 게시글은 아직 없습니다. 다시 더보기를 누를 수 있어요.");
            setButtonState(button, "idle");
            return { appended: appended, hasMore: true };
        }

        showStatus(state, "새로 추가된 게시글은 아직 없습니다. 다시 확인할 수 있어요.");
        setButtonState(button, "refresh");
        return { appended: appended, hasMore: hasMore };
    }

    function buildRequestContext(state) {
        var section = state.section;
        var list = state.list;
        if (!section || !list) {
            return null;
        }

        var board = section.dataset.board || "";
        var pid = section.dataset.pid || "";
        var kind = section.dataset.kind || "";
        var recommend = section.dataset.recommend || "";
        var limit = section.dataset.limit || "12";
        var sourcePage = state.lastSourcePage || normalizeSourcePage(section.dataset.sourcePage);
        var headId = section.dataset.headId || "";
        var searchType = section.dataset.searchType || "";
        var searchKeyword = section.dataset.searchKeyword || "";
        var galleryName = section.dataset.galleryName || "";
        var afterPid = state.lastPostId || "";

        if (!board || !pid) {
            if (!list.querySelector("a.feed-item")) {
                showStatus(state, "다른 게시글이 없습니다.");
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
        if (headId) {
            params.set("headid", headId);
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
            headId: headId,
            searchType: searchType,
            searchKeyword: searchKeyword,
            afterPid: afterPid,
            galleryName: galleryName,
            list: list,
            renderedIds: state.renderedIds,
            params: params
        };
    }

    function createError(code) {
        var err = new Error(code || "related_fetch_failed");
        err.code = code || "related_fetch_failed";
        return err;
    }

    function failureMessage(err) {
        var code = (err && err.code) || "";
        if (code === "related_request_timeout") {
            return "다른 게시글을 불러오는 데 시간이 너무 오래 걸렸습니다. 다시 시도할 수 있어요.";
        }
        if (code === "related_position_unavailable") {
            return "이 위치에서 다음 게시글을 찾지 못했습니다. 다시 시도할 수 있어요.";
        }
        return "다른 게시글을 불러오지 못했습니다. 다시 시도할 수 있어요.";
    }

    async function readPayload(url, signal) {
        var response = await fetch(url, {
            method: "GET",
            credentials: "same-origin",
            headers: {
                "Accept": "application/json"
            },
            signal: signal
        });
        var payload = null;
        try {
            payload = await response.json();
        } catch (err) {
            payload = null;
        }
        if (!payload || typeof payload !== "object") {
            throw createError("related_fetch_failed");
        }
        if (!response.ok || payload.ok === false) {
            throw createError(payload.error || "related_fetch_failed");
        }
        if (!Array.isArray(payload.items)) {
            throw createError("related_fetch_failed");
        }
        return payload;
    }

    async function loadRelated(state) {
        if (state.loading || state.terminal) {
            return;
        }
        var button = state.button;
        var context = buildRequestContext(state);
        if (!context) {
            return;
        }

        var requestId = state.requestId + 1;
        state.requestId = requestId;
        state.loading = true;
        setButtonState(button, "loading");
        setListBusy(state, true);
        showStatus(state, LOADING_MESSAGE);

        var controller = typeof AbortController === "function" ? new AbortController() : null;
        var timer = null;
        var timeout = new Promise(function (resolve, reject) {
            timer = setTimeout(function () {
                if (controller) {
                    try {
                        controller.abort();
                    } catch (err) {
                        // 중단을 지원하지 않는 환경에서는 타임아웃 처리만 이어간다.
                    }
                }
                reject(createError("related_request_timeout"));
            }, REQUEST_TIMEOUT_MS);
        });

        try {
            var payload = await Promise.race([
                readPayload("/read/related?" + context.params.toString(), controller ? controller.signal : undefined),
                timeout
            ]);
            if (requestId !== state.requestId) {
                return;
            }
            var result = applyLoadedItems(context, state, payload.items, payload);
            if (context.lastPostId) {
                state.lastPostId = context.lastPostId;
                state.lastSourcePage = context.lastSourcePage;
            }
            if (result.hasMore === false) {
                state.terminal = true;
            }
        } catch (err) {
            if (requestId !== state.requestId) {
                return;
            }
            showStatus(state, failureMessage(err));
            setButtonState(button, "retry");
        } finally {
            clearTimeout(timer);
            if (requestId === state.requestId) {
                state.loading = false;
                setListBusy(state, false);
            }
        }
    }

    function bindRelatedLoader() {
        var button = document.getElementById("related-load-button");
        var section = document.getElementById("related-section");
        var list = document.getElementById("related-list");
        if (!button || !section || !list) {
            return;
        }
        var renderedState = getRenderedPostState(list);
        var hasMoreAttr = String(section.dataset.hasMore || "").toLowerCase();
        var state = {
            button: button,
            section: section,
            list: list,
            statusRegion: document.getElementById("related-status"),
            renderedIds: renderedState.ids,
            lastPostId: renderedState.lastPostId,
            lastSourcePage: renderedState.lastSourcePage,
            loading: false,
            terminal: hasMoreAttr === "false",
            autoLoaded: false,
            requestId: 0
        };
        if (!button.dataset.defaultLabel) {
            button.dataset.defaultLabel = "더보기";
        }
        button.addEventListener("click", function () {
            loadRelated(state);
        });
        setListBusy(state, false);

        var hasRenderedPosts = !!list.querySelector("a.feed-item");
        if (state.terminal) {
            setButtonState(button, "no-more");
            if (!hasRenderedPosts) {
                showStatus(state, END_MESSAGE);
            }
            return;
        }

        setButtonState(button, "idle");
        if (!hasRenderedPosts && !state.autoLoaded) {
            state.autoLoaded = true;
            loadRelated(state);
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", bindRelatedLoader, { once: true });
    } else {
        bindRelatedLoader();
    }
})();
