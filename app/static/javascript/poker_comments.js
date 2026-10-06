// 포커고수 글 보기의 이전 댓글 자동 수집.
// 첫 화면은 원본의 마지막 댓글 페이지만 담는다. DOM 준비 후 이전 페이지를 한 번에 하나씩
// 내림차순으로 가져와 기존 댓글 앞에 붙인다. 페이지 사이 1초, 20페이지마다 5초를 더 쉰다.
// 실패하면 멈추고 자동 재시도는 하지 않으며, 다시 시도는 실패한 페이지부터 이어 간다.
(function () {
    "use strict";

    var REQUEST_TIMEOUT_MS = 26000;
    var PAGE_GAP_MS = 1000;
    var BREAK_EVERY = 20;
    var BREAK_MS = 5000;
    var LIST_WAIT_MS = 300;
    var MAX_PAGE = 10000;
    var ID_PATTERN = /^C\d{1,20}$/;
    var GENERIC_ERROR = "이전 댓글을 불러오지 못했어요. 잠시 후 다시 시도해 주세요.";
    var TIMEOUT_ERROR = "댓글 응답이 너무 늦어요. 다시 시도해 주세요.";
    var ADDED_EVENT = "poker:comments-added";

    function parseInteger(value, min, max) {
        var text = String(value === undefined || value === null ? "" : value).trim();
        if (!/^\d{1,7}$/.test(text)) {
            return null;
        }
        var number = parseInt(text, 10);
        return number >= min && number <= max ? number : null;
    }

    function isInteger(value) {
        return typeof value === "number" && isFinite(value) && Math.floor(value) === value;
    }

    function formatNumber(value) {
        return String(value).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    }

    function isJsonResponse(response) {
        var type = response && response.headers && typeof response.headers.get === "function"
            ? response.headers.get("content-type") || ""
            : "";
        return /^application\/json\b/i.test(type.trim());
    }

    // 서버가 _comment.html 로 만든 한 줄만 받는다. 모양이 다르면 페이지 전체를 버린다.
    function parseRow(row) {
        if (!row || typeof row !== "object" || typeof row.id !== "string" || !ID_PATTERN.test(row.id) ||
            typeof row.html !== "string") {
            return null;
        }
        var template = document.createElement("template");
        template.innerHTML = row.html.trim();
        var content = template.content;
        if (!content || !content.children || content.children.length !== 1) {
            return null;
        }
        var item = content.children[0];
        if (item.tagName !== "LI" || item.getAttribute("data-comment-id") !== row.id) {
            return null;
        }
        var parent = item.getAttribute("data-parent-id");
        if (parent !== null && !ID_PATTERN.test(parent)) {
            return null;
        }
        if (item.querySelector("script, iframe, object, embed")) {
            return null;
        }
        return item;
    }

    function validate(payload, page) {
        if (!payload || typeof payload !== "object" || !Array.isArray(payload.comments) || payload.page !== page) {
            return null;
        }
        var next = payload.next_page;
        if (page === 1 && next === null) {
            next = null;
        } else if (page <= 1 || !isInteger(next) || next !== page - 1) {
            return null;
        }
        var total = payload.total;
        if (total === undefined || total === null) {
            total = null;
        } else if (!isInteger(total) || total < 0) {
            return null;
        }
        var rows = [];
        for (var i = 0; i < payload.comments.length; i += 1) {
            var item = parseRow(payload.comments[i]);
            if (!item) {
                return null;
            }
            rows.push({ id: payload.comments[i].id, item: item });
        }
        return { rows: rows, next: next, total: total, stale: payload.stale === true };
    }

    function dispatchAdded(count) {
        var event;
        if (typeof CustomEvent === "function") {
            event = new CustomEvent(ADDED_EVENT, { detail: { count: count } });
        } else {
            event = document.createEvent("Event");
            event.initEvent(ADDED_EVENT, false, false);
        }
        document.dispatchEvent(event);
    }

    function bind(section) {
        var list = document.getElementById("poker-comment-list");
        if (!section || !list || section.dataset.pokerCommentsBound === "1") {
            return;
        }
        var commentsUrl = section.dataset.commentsUrl || "";
        var cursor = parseInteger(section.dataset.nextPage, 1, MAX_PAGE);
        // 한 페이지로 끝났거나 이어 갈 페이지를 모르면 요청하지 않고 서버 안내를 그대로 둔다.
        if (!commentsUrl || cursor === null) {
            return;
        }
        section.dataset.pokerCommentsBound = "1";

        var statusBox = section.querySelector("[data-poker-comments-status]");
        var progress = section.querySelector("[data-poker-comments-progress]");
        var retryButton = section.querySelector("[data-poker-comments-retry]");
        var live = section.querySelector("[data-poker-comments-live]");
        var noticeText = section.querySelector("[data-poker-comments-notice-text]");
        var notice = section.querySelector("[data-poker-comments-notice]");
        // 지난 캐시로 받은 댓글 페이지가 하나라도 붙어 있으면 안내를 계속 보여 준다.
        var staleNotice = section.querySelector("[data-poker-comments-stale]");
        var heading = document.getElementById("comment-title");
        var countNode = heading ? heading.querySelector(".comment-count") : null;

        var total = parseInteger(section.dataset.total, 0, 9999999);
        var mismatch = false;
        var seen = Object.create(null);
        var count = 0;
        var existing = list.querySelectorAll("li[data-comment-id]");
        for (var i = 0; i < existing.length; i += 1) {
            var id = existing[i].getAttribute("data-comment-id");
            if (id && !seen[id]) {
                seen[id] = true;
                count += 1;
            }
        }

        var state = "waiting";
        var serial = 0;
        var controller = null;
        var requestTimer = null;
        var waitTimer = null;
        var successes = 0;
        var interrupted = false;

        function setState(next) {
            state = next;
            section.dataset.commentsState = next;
        }

        function setText(node, text) {
            if (node) {
                node.textContent = text;
            }
        }

        function stopRequestTimer() {
            if (requestTimer !== null) {
                clearTimeout(requestTimer);
                requestTimer = null;
            }
        }

        function stopWaitTimer() {
            if (waitTimer !== null) {
                clearTimeout(waitTimer);
                waitTimer = null;
            }
        }

        function cancelRequest() {
            serial += 1;
            stopRequestTimer();
            if (controller) {
                controller.abort();
                controller = null;
            }
        }

        function showProgress() {
            if (statusBox) {
                statusBox.hidden = false;
            }
            setText(progress, "댓글을 불러오는 중… " + formatNumber(count) + (total !== null ? "/" + formatNumber(total) : "개"));
        }

        function updateNotice() {
            if (noticeText) {
                setText(noticeText, "댓글 일부만 보여요." + (total !== null
                    ? " 전체 " + formatNumber(total) + "개 중 " + formatNumber(count) + "개예요."
                    : ""));
            }
            if (countNode) {
                setText(countNode, formatNumber(total === null ? count : total));
            }
        }

        function lockRetry(locked) {
            if (!retryButton) {
                return;
            }
            if (locked) {
                retryButton.setAttribute("aria-disabled", "true");
            } else {
                retryButton.removeAttribute("aria-disabled");
            }
        }

        // 다시 시도 버튼을 숨길 때 포커스가 문서 처음으로 튀지 않게 댓글 제목으로 옮긴다.
        function hideRetry() {
            if (!retryButton || retryButton.hidden) {
                return;
            }
            var focused = document.activeElement === retryButton;
            retryButton.hidden = true;
            lockRetry(false);
            if (focused && heading && typeof heading.focus === "function") {
                heading.focus();
            }
        }

        function fail(text) {
            controller = null;
            stopRequestTimer();
            setState("error");
            if (statusBox) {
                statusBox.hidden = false;
            }
            setText(progress, text);
            setText(live, text);
            if (retryButton) {
                retryButton.hidden = false;
                lockRetry(false);
            }
            updateNotice();
        }

        function finish() {
            setState("done");
            if (statusBox) {
                statusBox.hidden = true;
            }
            hideRetry();
            // 원본 전체 개수와 모은 개수가 같을 때만 다 불러왔다고 말한다.
            var complete = !mismatch && total !== null && count === total;
            section.dataset.commentsComplete = complete ? "1" : "0";
            if (complete) {
                if (notice) {
                    notice.hidden = !notice.contains(document.activeElement);
                    if (!notice.hidden) {
                        setText(noticeText, "댓글 " + formatNumber(total) + "개를 모두 불러왔어요.");
                    }
                }
                setText(live, "댓글 " + formatNumber(total) + "개를 모두 불러왔어요.");
            } else {
                updateNotice();
                setText(live, "댓글 일부만 불러왔어요. 원문에서 전체 댓글을 확인해 주세요.");
            }
        }

        // 읽던 자리가 밀리지 않도록 기준 요소를 고른다. 댓글이 화면 아래에 있으면 보정할 필요가 없다.
        function pickAnchor() {
            var listRect = list.getBoundingClientRect();
            // 본문이나 댓글 제목이 화면 위에 남아 있으면 그 자리를 그대로 유지한다.
            if (listRect.top >= 0 || document.activeElement === retryButton) {
                return null;
            }
            if (listRect.bottom > 0) {
                var rows = list.children;
                for (var j = 0; j < rows.length; j += 1) {
                    if (rows[j].getBoundingClientRect().bottom > 0) {
                        return { node: rows[j], edge: "top" };
                    }
                }
            }
            return { node: list, edge: "bottom" };
        }

        function measure(anchor) {
            return anchor.node.getBoundingClientRect()[anchor.edge];
        }

        // 브라우저의 스크롤 고정(overflow-anchor)과 직접 보정이 겹치면 두 번 밀린다.
        // 붙이는 동안만 문서 스크롤 고정을 끄고 잰 차이만큼 한 번 옮긴 뒤, 두 프레임 뒤 원래대로 돌린다.
        function suspendNativeAnchor() {
            var rootStyle = document.documentElement && document.documentElement.style;
            if (!rootStyle) {
                return function () {};
            }
            var previous = rootStyle.overflowAnchor || "";
            rootStyle.overflowAnchor = "none";
            return function () {
                rootStyle.overflowAnchor = previous;
            };
        }

        function keepPosition(anchor, before, restore) {
            var delta = measure(anchor) - before;
            if (Math.abs(delta) >= 1) {
                window.scrollBy(0, delta);
            }
            if (typeof requestAnimationFrame === "function") {
                requestAnimationFrame(function () {
                    requestAnimationFrame(restore);
                });
            } else {
                restore();
            }
        }

        function insert(items) {
            if (!items.length) {
                return;
            }
            var empty = list.querySelectorAll("li.empty-row");
            for (var j = 0; j < empty.length; j += 1) {
                empty[j].parentNode.removeChild(empty[j]);
            }
            var reference = list.firstChild;
            for (var k = 0; k < items.length; k += 1) {
                list.insertBefore(items[k], reference);
            }
            count += items.length;
            dispatchAdded(items.length);
        }

        function apply(data) {
            var anchor = pickAnchor();
            var before = anchor ? measure(anchor) : 0;
            var restore = anchor ? suspendNativeAnchor() : null;
            var fresh = [];
            for (var j = 0; j < data.rows.length; j += 1) {
                var row = data.rows[j];
                if (!seen[row.id]) {
                    seen[row.id] = true;
                    fresh.push(row.item);
                }
            }
            if (data.total !== null) {
                if (total !== null && data.total !== total) {
                    mismatch = true;
                }
                total = data.total;
            } else {
                // 일부 응답에서 전체 수를 잃었다면 이전 숫자로 수집 완료를 단정하지 않는다.
                mismatch = true;
                total = null;
            }
            insert(fresh);
            if (data.stale && staleNotice) {
                staleNotice.hidden = false;
            }
            hideRetry();
            successes += 1;
            cursor = data.next;
            updateNotice();
            if (cursor === null) {
                finish();
                if (anchor) {
                    keepPosition(anchor, before, restore);
                }
                return;
            }
            showProgress();
            if (anchor) {
                keepPosition(anchor, before, restore);
            }
            schedule(successes % BREAK_EVERY === 0 ? PAGE_GAP_MS + BREAK_MS : PAGE_GAP_MS);
        }

        function buildUrl(page) {
            var url = new URL(commentsUrl, window.location.href);
            url.searchParams.set("cpage", String(page));
            return url.pathname + url.search;
        }

        function request(page) {
            cancelRequest();
            stopWaitTimer();
            var mine = serial;
            var own = typeof AbortController === "function" ? new AbortController() : null;
            var timedOut = false;
            controller = own;
            setState("loading");
            lockRetry(true);
            showProgress();
            requestTimer = setTimeout(function () {
                if (mine !== serial) {
                    return;
                }
                timedOut = true;
                requestTimer = null;
                if (own) {
                    own.abort();
                }
                fail(TIMEOUT_ERROR);
            }, REQUEST_TIMEOUT_MS);

            function current() {
                return mine === serial && !timedOut;
            }

            var init = { headers: { Accept: "application/json" }, credentials: "same-origin", cache: "no-store" };
            if (own) {
                init.signal = own.signal;
            }
            fetch(buildUrl(page), init).then(function (response) {
                if (!current()) {
                    return null;
                }
                if (!isJsonResponse(response)) {
                    return { failed: GENERIC_ERROR };
                }
                return response.json().then(function (payload) {
                    if (!response.ok) {
                        var text = response.status !== 429 && payload && typeof payload.error === "string"
                            ? payload.error.trim().slice(0, 200)
                            : "";
                        // 원본이 댓글 페이지를 막고 있으면 다시 시도해도 같으므로 여기서 수집을 끝낸다.
                        return { failed: text || GENERIC_ERROR, final: !!payload && payload.code === "comments_blocked" };
                    }
                    var data = validate(payload, page);
                    return data ? { data: data } : { failed: GENERIC_ERROR };
                }, function () {
                    return { failed: GENERIC_ERROR };
                });
            }).catch(function () {
                return { failed: GENERIC_ERROR };
            }).then(function (result) {
                if (!result || !current()) {
                    return;
                }
                stopRequestTimer();
                controller = null;
                if (result.failed) {
                    if (result.final) {
                        cursor = null;
                        finish();
                        return;
                    }
                    fail(result.failed);
                    return;
                }
                apply(result.data);
            });
        }

        function schedule(delay) {
            stopWaitTimer();
            setState("waiting");
            waitTimer = setTimeout(step, delay);
        }

        // 다음 요청 직전에만 멈춤 조건을 본다. 숨긴 탭은 보일 때까지, 요청 중인 하단 목록은 끝날 때까지 기다린다.
        function step() {
            waitTimer = null;
            if (state !== "waiting" || cursor === null) {
                return;
            }
            if (document.hidden) {
                return;
            }
            var postList = document.getElementById("poker-post-list");
            if (postList && postList.dataset && postList.dataset.state === "loading") {
                schedule(LIST_WAIT_MS);
                return;
            }
            request(cursor);
        }

        if (retryButton) {
            retryButton.addEventListener("click", function () {
                if (state !== "error" || cursor === null) {
                    return;
                }
                request(cursor);
            });
        }

        document.addEventListener("visibilitychange", function () {
            if (!document.hidden && state === "waiting" && waitTimer === null && !interrupted) {
                step();
            }
        });

        window.addEventListener("pagehide", function () {
            stopWaitTimer();
            if (state === "loading" || state === "waiting") {
                interrupted = true;
                cancelRequest();
                setState("waiting");
            }
        });

        window.addEventListener("pageshow", function (event) {
            if (event && event.persisted && interrupted) {
                interrupted = false;
                step();
            }
        });

        setState("waiting");
        showProgress();
        step();
    }

    function init() {
        bind(document.getElementById("comment"));
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
