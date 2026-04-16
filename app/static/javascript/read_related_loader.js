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
        return String(value);
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

    function getRenderedPostIds(list) {
        var ids = {};
        var links = list.querySelectorAll("a.feed-item");
        for (var i = 0; i < links.length; i += 1) {
            var link = links[i];
            var postId = normalizePostId(
                link.dataset.postId ||
                (link.closest("li") && link.closest("li").dataset.postId) ||
                extractPostIdFromHref(link.getAttribute("href"))
            );
            if (postId) {
                ids[postId] = true;
            }
        }
        return ids;
    }

    function buildReadHref(board, item, kind, recommend) {
        var pid = item && item.id;
        var href = "/read?board=" + encodeURIComponent(board) + "&pid=" + encodeURIComponent(String(pid));
        if (recommend === "1") {
            href += "&recommend=1";
        }
        if (item && item.source_page) {
            href += "&source_page=" + encodeURIComponent(String(item.source_page));
        }
        if (kind) {
            href += "&kind=" + encodeURIComponent(kind);
        }
        return href;
    }

    function createItemNode(item, board, kind, recommend) {
        var li = document.createElement("li");
        li.dataset.postId = normalizePostId(item && item.id);
        var link = document.createElement("a");
        link.className = "feed-item";
        link.dataset.postId = li.dataset.postId;
        link.href = buildReadHref(board, item, kind, recommend);

        var titleWrap = document.createElement("div");
        titleWrap.className = "feed-title-wrap";

        var title = document.createElement("h2");
        title.className = "feed-title";
        title.textContent = item.title || "제목 없음";
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

    function appendItems(list, items, board, kind, recommend) {
        var appended = 0;
        var renderedIds = getRenderedPostIds(list);

        if (!Array.isArray(items) || items.length === 0) {
            return appended;
        }

        for (var i = 0; i < items.length; i += 1) {
            var item = items[i];
            var postId = normalizePostId(item && item.id);
            if (!postId || renderedIds[postId]) {
                continue;
            }
            list.appendChild(createItemNode(item, board, kind, recommend));
            renderedIds[postId] = true;
            appended += 1;
        }
        return appended;
    }

    function hasRenderedItems(list) {
        return !!(list && list.querySelector("a.feed-item"));
    }

    function readSessionCache(key) {
        try {
            var raw = window.sessionStorage && window.sessionStorage.getItem(key);
            if (!raw) {
                return null;
            }
            var cached = JSON.parse(raw);
            if (!cached || !Array.isArray(cached.items)) {
                return null;
            }
            return {
                items: cached.items,
                noMore: cached.noMore === true
            };
        } catch (err) {
            return null;
        }
    }

    function writeSessionCache(key, items, noMore) {
        try {
            if (window.sessionStorage) {
                if (!Array.isArray(items) || items.length === 0) {
                    window.sessionStorage.removeItem(key);
                    return;
                }
                window.sessionStorage.setItem(key, JSON.stringify({
                    items: items,
                    noMore: noMore === true
                }));
            }
        } catch (err) {
            // 저장 공간이 없거나 차단된 환경에서는 캐시 없이 동작한다.
        }
    }

    function setButtonState(button, state) {
        if (!button) {
            return;
        }
        if (state === "loading") {
            button.disabled = true;
            button.textContent = "불러오는 중...";
            return;
        }
        if (state === "loaded") {
            button.disabled = true;
            button.textContent = "불러옴";
            return;
        }
        if (state === "no-more") {
            button.disabled = true;
            button.textContent = "더 없음";
            return;
        }
        if (state === "refresh") {
            button.disabled = false;
            button.textContent = "다시 확인";
            return;
        }
        if (state === "retry") {
            button.disabled = false;
            button.textContent = "다시 시도";
            return;
        }
        button.disabled = false;
        button.textContent = button.dataset.defaultLabel || "불러오기";
    }

    function hasOwn(obj, key) {
        return !!obj && Object.prototype.hasOwnProperty.call(obj, key);
    }

    function responseHasNoNextCandidate(payload) {
        if (!payload || typeof payload !== "object") {
            return false;
        }
        if (payload.has_more === false || payload.hasMore === false || payload.has_next === false || payload.hasNext === false) {
            return true;
        }
        if (hasOwn(payload, "next_cursor") && !payload.next_cursor) {
            return true;
        }
        if (hasOwn(payload, "nextCursor") && !payload.nextCursor) {
            return true;
        }
        return false;
    }

    function isNoMoreResponse(payload, items) {
        if (!Array.isArray(items) || items.length === 0) {
            return false;
        }
        return responseHasNoNextCandidate(payload);
    }

    function applyLoadedItems(context, button, items, payload) {
        var noMore = isNoMoreResponse(payload, items);

        removeStatusRows(context.list);
        var appended = appendItems(context.list, items, context.board, context.kind, context.recommend);

        if (noMore) {
            appendStatusRow(context.list, "더 불러올 게시글이 없습니다.");
            setButtonState(button, "no-more");
            return { appended: appended, noMore: true };
        }

        if (appended > 0) {
            setButtonState(button, "loaded");
            return { appended: appended, noMore: false };
        }

        appendStatusRow(context.list, "새로 추가된 게시글은 아직 없습니다. 다시 확인할 수 있어요.");
        setButtonState(button, "refresh");
        return { appended: appended, noMore: false };
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

        return {
            board: board,
            pid: pid,
            kind: kind,
            recommend: recommend,
            limit: limit,
            list: list,
            params: params,
            cacheKey: "mirror:related:" + params.toString()
        };
    }

    async function loadRelated() {
        var button = document.getElementById("related-load-button");
        var context = buildRequestContext();
        if (!context) {
            return;
        }

        var cachedResult = hasRenderedItems(context.list) ? null : readSessionCache(context.cacheKey);
        if (cachedResult !== null && cachedResult.items.length > 0) {
            applyLoadedItems(context, button, cachedResult.items, { has_more: cachedResult.noMore !== true });
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
            var result = applyLoadedItems(context, button, items, payload);
            writeSessionCache(context.cacheKey, items, result.noMore);
        } catch (err) {
            appendStatusRow(context.list, "다른 게시글을 불러오지 못했습니다. 다시 시도할 수 있어요.");
            setButtonState(button, "retry");
        }
    }

    function bindRelatedLoader() {
        var button = document.getElementById("related-load-button");
        if (!button) {
            return;
        }
        if (!button.dataset.defaultLabel) {
            button.dataset.defaultLabel = button.textContent || "불러오기";
        }
        button.addEventListener("click", loadRelated);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", bindRelatedLoader, { once: true });
    } else {
        bindRelatedLoader();
    }
})();
