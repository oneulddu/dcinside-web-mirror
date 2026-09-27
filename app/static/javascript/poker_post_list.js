// 포커고수 글 아래 같은 게시판 목록.
// 본문과 따로 한 번만 불러오고(화면 800px 앞에서), 이전·다음은 목록 부분만 바꾼다.
// 실패·시간 초과는 목록 자리에만 안내를 두며 자동 재시도는 하지 않는다.
(function () {
    "use strict";

    var REQUEST_TIMEOUT_MS = 26000;
    var ROOT_MARGIN = "800px 0px";
    var MAX_PAGE = 10000;
    var LOADING_MESSAGE = "목록을 불러오는 중이에요.";
    var GENERIC_ERROR = "목록을 불러오지 못했어요. 잠시 후 다시 시도해 주세요.";
    var TIMEOUT_ERROR = "목록 응답이 너무 늦어요. 다시 시도해 주세요.";

    function parsePage(value) {
        var text = String(value === undefined || value === null ? "" : value).trim();
        if (!/^\d{1,5}$/.test(text)) {
            return null;
        }
        var page = parseInt(text, 10);
        return page >= 1 && page <= MAX_PAGE ? page : null;
    }

    function isJsonResponse(response) {
        var type = response && response.headers && typeof response.headers.get === "function"
            ? response.headers.get("content-type") || ""
            : "";
        return /^application\/json\b/i.test(type.trim());
    }

    function isPlainPrimaryClick(event) {
        return !event.defaultPrevented && event.button === 0 &&
            !event.metaKey && !event.ctrlKey && !event.shiftKey && !event.altKey;
    }

    function bind(section) {
        if (!section || section.dataset.pokerListBound === "1") {
            return;
        }
        var body = section.querySelector("[data-poker-list-body]");
        var message = section.querySelector("[data-poker-list-message]");
        var actions = section.querySelector("[data-poker-list-actions]");
        var retryButton = section.querySelector("[data-poker-list-retry]");
        var heading = section.querySelector("[data-poker-list-heading]");
        var fallbackLink = actions ? actions.querySelector("a[href]") : null;
        var listUrl = section.dataset.listUrl || "";
        var currentPid = /^\d{1,12}$/.test(section.dataset.currentPid || "") ? section.dataset.currentPid : "";
        var sourcePage = parsePage(section.dataset.page) || 1;
        if (!body || !message || !listUrl) {
            return;
        }
        section.dataset.pokerListBound = "1";

        var serial = 0;
        var controller = null;
        var timer = null;
        var started = false;
        var failedPage = null;
        var failedFallback = null;

        function setMessage(text) {
            message.textContent = text || "";
        }

        function showActions(show) {
            if (actions) {
                actions.hidden = !show;
            }
        }

        // 다시 시도 중에는 버튼을 숨기지 않고 잠가 둔다. 숨기면 키보드 포커스가 문서 처음으로 튄다.
        function lockRetry(locked) {
            if (retryButton) {
                if (locked) {
                    retryButton.setAttribute("aria-disabled", "true");
                } else {
                    retryButton.removeAttribute("aria-disabled");
                }
            }
        }

        function setState(state) {
            section.dataset.state = state;
            body.setAttribute("aria-busy", state === "loading" ? "true" : "false");
        }

        function stopTimer() {
            if (timer !== null) {
                clearTimeout(timer);
                timer = null;
            }
        }

        function buildUrl(page) {
            var url = new URL(listUrl, window.location.href);
            url.searchParams.set("page", String(page));
            if (currentPid) {
                url.searchParams.set("current_pid", currentPid);
            }
            return url.pathname + url.search;
        }

        function fail(page, fallbackHref, text) {
            failedPage = page;
            failedFallback = fallbackHref;
            if (fallbackLink && fallbackHref) {
                fallbackLink.setAttribute("href", fallbackHref);
            }
            setState("error");
            setMessage(text);
            lockRetry(false);
            showActions(true);
        }

        function succeed(page, html, moveFocus) {
            body.innerHTML = html;
            if (typeof CustomEvent === "function") {
                document.dispatchEvent(new CustomEvent("poker:list-rendered", { detail: { root: body } }));
            }
            failedPage = null;
            failedFallback = null;
            section.dataset.shownPage = String(page);
            setState("ready");
            lockRetry(false);
            showActions(false);
            // 처음 불러올 때는 읽기를 방해하지 않도록 조용히 둔다.
            setMessage(moveFocus ? page + "페이지 목록이에요." : "");
            if (moveFocus && heading && typeof heading.focus === "function") {
                heading.focus();
            }
        }

        function load(page, moveFocus, fallbackHref) {
            serial += 1;
            var mine = serial;
            if (controller) {
                controller.abort();
            }
            stopTimer();
            var own = typeof AbortController === "function" ? new AbortController() : null;
            var timedOut = false;
            controller = own;
            setState("loading");
            lockRetry(true);
            setMessage(LOADING_MESSAGE);
            timer = setTimeout(function () {
                if (mine !== serial) {
                    return;
                }
                timedOut = true;
                timer = null;
                if (own) {
                    own.abort();
                }
                controller = null;
                fail(page, fallbackHref, TIMEOUT_ERROR);
            }, REQUEST_TIMEOUT_MS);

            var init = { headers: { Accept: "application/json" }, credentials: "same-origin", cache: "no-store" };
            if (own) {
                init.signal = own.signal;
            }

            function current() {
                return mine === serial && !timedOut;
            }

            fetch(buildUrl(page), init).then(function (response) {
                if (!current()) {
                    return null;
                }
                if (!isJsonResponse(response)) {
                    return { failed: GENERIC_ERROR };
                }
                return response.json().then(function (payload) {
                    if (!payload || typeof payload !== "object") {
                        return { failed: GENERIC_ERROR };
                    }
                    if (!response.ok) {
                        var text = typeof payload.error === "string" && payload.error.trim()
                            ? payload.error.trim()
                            : GENERIC_ERROR;
                        return { failed: text };
                    }
                    if (typeof payload.html !== "string") {
                        return { failed: GENERIC_ERROR };
                    }
                    return { html: payload.html };
                }, function () {
                    return { failed: GENERIC_ERROR };
                });
            }).catch(function () {
                return { failed: GENERIC_ERROR };
            }).then(function (result) {
                if (!result || !current()) {
                    return;
                }
                stopTimer();
                controller = null;
                if (result.failed) {
                    fail(page, fallbackHref, result.failed);
                    return;
                }
                succeed(page, result.html, moveFocus);
            });
        }

        function start() {
            if (started) {
                return;
            }
            started = true;
            load(sourcePage, false, fallbackLink ? fallbackLink.getAttribute("href") : null);
        }

        section.addEventListener("click", function (event) {
            var target = event.target;
            var link = target && typeof target.closest === "function" ? target.closest("a[data-list-page]") : null;
            if (!link || !isPlainPrimaryClick(event)) {
                return;
            }
            var page = parsePage(link.getAttribute("data-list-page"));
            if (page === null) {
                return;
            }
            event.preventDefault();
            load(page, true, link.getAttribute("href"));
        });

        if (retryButton) {
            retryButton.addEventListener("click", function () {
                if (failedPage === null || section.dataset.state === "loading") {
                    return;
                }
                load(failedPage, true, failedFallback);
            });
        }

        setState("idle");
        if (typeof IntersectionObserver === "function") {
            var observer = new IntersectionObserver(function (entries) {
                for (var i = 0; i < entries.length; i += 1) {
                    if (entries[i].isIntersecting) {
                        observer.disconnect();
                        start();
                        return;
                    }
                }
            }, { rootMargin: ROOT_MARGIN });
            observer.observe(section);
        } else {
            start();
        }
    }

    function init() {
        bind(document.getElementById("poker-post-list"));
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
