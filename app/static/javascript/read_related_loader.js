(function () {
    "use strict";

    function clearChildren(node) {
        while (node.firstChild) {
            node.removeChild(node.firstChild);
        }
    }

    function appendEmptyRow(list, text) {
        var li = document.createElement("li");
        li.className = "empty-row";
        li.textContent = text;
        list.appendChild(li);
    }

    function buildReadHref(board, pid, kind) {
        var href = "/read?board=" + encodeURIComponent(board) + "&pid=" + encodeURIComponent(String(pid));
        if (kind) {
            href += "&kind=" + encodeURIComponent(kind);
        }
        return href;
    }

    function createItemNode(item, board, kind) {
        var li = document.createElement("li");
        var link = document.createElement("a");
        link.className = "feed-item";
        link.href = buildReadHref(board, item.id, kind);

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

    function renderItems(list, items, board, kind) {
        clearChildren(list);
        if (!Array.isArray(items) || items.length === 0) {
            appendEmptyRow(list, "다른 게시글이 없습니다.");
            return;
        }
        for (var i = 0; i < items.length; i += 1) {
            var item = items[i];
            if (!item || !item.id) {
                continue;
            }
            list.appendChild(createItemNode(item, board, kind));
        }
        if (!list.firstChild) {
            appendEmptyRow(list, "다른 게시글이 없습니다.");
        }
    }

    async function loadRelated() {
        var section = document.getElementById("related-section");
        if (!section) {
            return;
        }
        var list = document.getElementById("related-list");
        if (!list) {
            return;
        }

        var board = section.dataset.board || "";
        var pid = section.dataset.pid || "";
        var kind = section.dataset.kind || "";
        var limit = section.dataset.limit || "12";

        if (!board || !pid) {
            clearChildren(list);
            appendEmptyRow(list, "다른 게시글이 없습니다.");
            return;
        }

        var params = new URLSearchParams();
        params.set("board", board);
        params.set("pid", pid);
        params.set("limit", limit);
        if (kind) {
            params.set("kind", kind);
        }

        try {
            var response = await fetch("/read/related?" + params.toString(), {
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
            renderItems(list, payload.items, board, kind);
        } catch (err) {
            clearChildren(list);
            appendEmptyRow(list, "다른 게시글을 불러오지 못했습니다.");
        }
    }

    function scheduleLoad() {
        if (window.requestIdleCallback) {
            window.requestIdleCallback(loadRelated, { timeout: 1000 });
            return;
        }
        window.setTimeout(loadRelated, 0);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", scheduleLoad, { once: true });
    } else {
        scheduleLoad();
    }
})();
