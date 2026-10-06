(function (root) {
    "use strict";

    // 게시판 전체 글 1페이지에서만 새 글을 확인해 "새 글 N개"를 알린다.
    // 확인은 /board/updates(JSON), 실제 갱신은 목록 HTML 교체로 한다.
    var BASE_INTERVAL_MS = 60000;
    var JITTER_MS = 10000;
    var BACKOFF_STEPS_MS = [120000, 240000, 300000];
    var REQUEST_TIMEOUT_MS = 20000;

    function toId(value) {
        var text = String(value == null ? "" : value).trim();
        return /^\d{1,15}$/.test(text) ? Number(text) : 0;
    }

    // baseline보다 큰 글 중 화면에 없고 차단되지 않은 글 수. 응답이 baseline까지 닿지 않으면 more=true.
    function computeNew(baseline, existingIds, items, isBlocked) {
        var seen = Object.create(null);
        (existingIds || []).forEach(function (id) {
            seen[String(id)] = true;
        });
        var count = 0;
        var reachedBaseline = false;
        var newer = 0;
        (items || []).forEach(function (item) {
            var id = toId(item && item.id);
            if (!id) {
                return;
            }
            if (id <= baseline) {
                reachedBaseline = true;
                return;
            }
            if (seen[String(id)]) {
                return;
            }
            seen[String(id)] = true;
            newer += 1;
            if (isBlocked && isBlocked(item)) {
                return;
            }
            count += 1;
        });
        return { count: count, newer: newer, more: newer > 0 && !reachedBaseline };
    }

    root.MirrorBoardUpdates = { computeNew: computeNew };

    var document = root.document;
    if (!document || typeof document.querySelector !== "function") {
        return;
    }

    var state = {
        timer: null,
        controller: null,
        failures: 0,
        lastCheckedAt: 0,
        lastItems: null,
        generation: 0,
        refreshing: false,
        bar: null
    };

    function boardList() {
        return document.getElementById("board-list");
    }

    // 전체 글 1페이지(검색·분류·추천·공지가 아닐 때)에서만 동작한다.
    function eligible() {
        var list = boardList();
        if (!list) {
            return false;
        }
        return list.getAttribute("data-page") === "1"
            && (list.getAttribute("data-recommend") || "0") === "0"
            && !list.getAttribute("data-head-id")
            && !list.getAttribute("data-search-keyword")
            && (list.getAttribute("data-notice") || "0") !== "1";
    }

    function currentIds() {
        var list = boardList();
        if (!list) {
            return [];
        }
        return Array.prototype.map.call(list.querySelectorAll(":scope > ul > li a.feed-item[data-post-id]"), function (link) {
            return toId(link.getAttribute("data-post-id"));
        }).filter(Boolean);
    }

    function baseline() {
        return currentIds().reduce(function (max, id) {
            return id > max ? id : max;
        }, 0);
    }

    function isBlocked(item) {
        var filter = root.MirrorUserFilter;
        if (!filter || typeof filter.matchesCurrent !== "function") {
            return false;
        }
        return !!filter.matchesCurrent({ author: item.author, code: item.author_code, title: item.title });
    }

    function ensureBar() {
        if (state.bar && state.bar.isConnected) {
            return state.bar;
        }
        var list = boardList();
        if (!list) {
            return null;
        }
        var bar = document.createElement("div");
        bar.className = "board-updates";
        bar.hidden = true;
        var button = document.createElement("button");
        button.type = "button";
        button.className = "board-updates-btn";
        button.setAttribute("aria-controls", "board-list");
        button.addEventListener("click", refreshList);
        var status = document.createElement("p");
        status.className = "sr-only";
        status.setAttribute("role", "status");
        status.setAttribute("aria-live", "polite");
        bar.appendChild(button);
        bar.appendChild(status);
        list.parentNode.insertBefore(bar, list);
        state.bar = bar;
        return bar;
    }

    function render() {
        var bar = ensureBar();
        if (!bar || state.refreshing) {
            return;
        }
        var button = bar.querySelector(".board-updates-btn");
        var status = bar.querySelector("[role=status]");
        var result = state.lastItems ? computeNew(baseline(), currentIds(), state.lastItems, isBlocked) : { count: 0, more: false };
        var label = result.count ? "새 글 " + result.count + "개" + (result.more ? " 이상" : "") : "";
        bar.hidden = !result.count;
        button.disabled = false;
        button.textContent = label ? label + " 보기" : "";
        // 숫자가 바뀔 때만 읽어 준다.
        if (status.textContent !== label) {
            status.textContent = label;
        }
    }

    function schedule(delay) {
        clearTimeout(state.timer);
        state.timer = null;
        if (!eligible() || document.visibilityState === "hidden") {
            return;
        }
        state.timer = setTimeout(check, delay);
    }

    function nextDelay() {
        if (state.failures > 0) {
            return BACKOFF_STEPS_MS[Math.min(state.failures - 1, BACKOFF_STEPS_MS.length - 1)];
        }
        return BASE_INTERVAL_MS + Math.floor(Math.random() * JITTER_MS);
    }

    function abortCheck() {
        if (state.controller) {
            state.controller.abort();
            state.controller = null;
        }
    }

    function check() {
        state.timer = null;
        if (!eligible() || document.visibilityState === "hidden") {
            return;
        }
        if (root.navigator && root.navigator.onLine === false) {
            schedule(nextDelay());
            return;
        }
        var list = boardList();
        var params = new URLSearchParams();
        params.set("board", list.getAttribute("data-board") || "");
        if (list.getAttribute("data-kind")) {
            params.set("kind", list.getAttribute("data-kind"));
        }
        abortCheck();
        var controller = typeof AbortController === "function" ? new AbortController() : null;
        state.controller = controller;
        var generation = state.generation;
        var timeout = setTimeout(function () {
            if (controller) {
                controller.abort();
            }
        }, REQUEST_TIMEOUT_MS);
        state.lastCheckedAt = Date.now();
        fetch("/board/updates?" + params.toString(), {
            credentials: "same-origin",
            headers: { "Accept": "application/json" },
            signal: controller ? controller.signal : undefined
        })
            .then(function (response) {
                return response.json().then(function (payload) {
                    if (!response.ok || !payload || payload.ok !== true || !Array.isArray(payload.items)) {
                        throw new Error("updates failed");
                    }
                    return payload;
                });
            })
            .then(function (payload) {
                // 그사이 목록이 바뀌었으면 늦은 응답을 버린다.
                if (generation !== state.generation) {
                    return;
                }
                state.failures = 0;
                state.lastItems = payload.items;
                render();
            })
            .catch(function () {
                if (generation === state.generation) {
                    state.failures += 1;
                }
            })
            .then(function () {
                clearTimeout(timeout);
                if (state.controller === controller) {
                    state.controller = null;
                }
                schedule(nextDelay());
            });
    }

    function listReplaced() {
        state.generation += 1;
        state.lastItems = null;
        abortCheck();
        render();
        schedule(nextDelay());
    }

    function refreshList() {
        if (state.refreshing) {
            return;
        }
        var bar = ensureBar();
        var button = bar.querySelector(".board-updates-btn");
        var list = boardList();
        state.refreshing = true;
        button.disabled = true;
        button.textContent = "불러오는 중…";
        list.setAttribute("aria-busy", "true");
        var url = new URL(root.location.href);
        url.searchParams.set("refresh", "1");
        var generation = state.generation;
        // 응답이나 본문 수신이 멈춰도 버튼이 영영 잠기지 않게 본문 읽기까지 시간 제한을 둔다.
        var controller = typeof AbortController === "function" ? new AbortController() : null;
        var timedOut = false;
        var timeout = setTimeout(function () {
            timedOut = true;
            if (controller) {
                controller.abort();
            }
        }, REQUEST_TIMEOUT_MS);
        fetch(url.toString(), {
            credentials: "same-origin",
            headers: { "Accept": "text/html" },
            signal: controller ? controller.signal : undefined
        })
            .then(function (response) {
                if (!response.ok) {
                    throw new Error("refresh failed");
                }
                return response.text();
            })
            .then(function (html) {
                if (timedOut) {
                    throw new Error("refresh timed out");
                }
                var next = new DOMParser().parseFromString(html, "text/html").getElementById("board-list");
                var current = boardList();
                if (!next || !current || generation !== state.generation) {
                    throw new Error("board list missing");
                }
                current.replaceWith(next);
                state.refreshing = false;
                document.dispatchEvent(new CustomEvent("mirror:board-refreshed", { detail: { root: next } }));
                var heading = document.querySelector(".masthead-board-head h1");
                if (heading) {
                    heading.setAttribute("tabindex", "-1");
                    heading.focus();
                }
            })
            .catch(function () {
                state.refreshing = false;
                var current = boardList();
                if (current) {
                    current.setAttribute("aria-busy", "false");
                }
                button.disabled = false;
                button.textContent = "새로고침하지 못했어요. 다시 시도";
            })
            .then(function () {
                clearTimeout(timeout);
            });
    }

    function boot() {
        if (!eligible()) {
            return;
        }
        ensureBar();
        schedule(nextDelay());
        // 복귀 갱신이나 이 스크립트의 갱신으로 목록이 바뀌면 기준을 새로 잡는다.
        document.addEventListener("mirror:board-refreshed", listReplaced);
        document.addEventListener("mirror:user-filter-changed", render);
        document.addEventListener("visibilitychange", function () {
            if (document.visibilityState === "hidden") {
                clearTimeout(state.timer);
                state.timer = null;
                abortCheck();
                return;
            }
            var waited = Date.now() - state.lastCheckedAt;
            schedule(waited >= BASE_INTERVAL_MS ? 0 : BASE_INTERVAL_MS - waited);
        });
        root.addEventListener("pagehide", function () {
            clearTimeout(state.timer);
            state.timer = null;
            abortCheck();
        });
        root.addEventListener("pageshow", function (event) {
            if (event.persisted) {
                schedule(0);
            }
        });
        root.addEventListener("online", function () {
            schedule(0);
        });
        state.lastCheckedAt = Date.now();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", boot, { once: true });
    } else {
        boot();
    }
})(typeof window !== "undefined" ? window : globalThis);
