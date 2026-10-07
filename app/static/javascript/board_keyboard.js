(function (root) {
    "use strict";

    // 게시판 목록에서 J/K로 글을 고르고 O(또는 Enter)로 연다. Esc는 선택을 푼다.
    // 한글 입력 상태에서도 같은 키 위치로 동작하도록 event.code를 먼저 본다.
    var KEY_ACTIONS = { KeyJ: "next", KeyK: "prev", KeyO: "open" };
    var LETTER_ACTIONS = { j: "next", k: "prev", o: "open" };
    var CURRENT_CLASS = "is-keyboard-current";

    function isTypingTarget(element) {
        if (!element) {
            return false;
        }
        var tag = String(element.tagName || "").toUpperCase();
        return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || !!element.isContentEditable;
    }

    function keyAction(event) {
        if (!event || event.defaultPrevented || event.isComposing || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) {
            return null;
        }
        if (event.key === "Escape") {
            return "clear";
        }
        if (KEY_ACTIONS[event.code]) {
            return KEY_ACTIONS[event.code];
        }
        return LETTER_ACTIONS[String(event.key || "").toLowerCase()] || null;
    }

    // 선택이 없으면 J는 첫 글, K는 마지막 글. 끝에서는 멈춘다.
    function stepIndex(current, length, action) {
        if (!length) {
            return -1;
        }
        if (current < 0 || current >= length) {
            return action === "prev" ? length - 1 : 0;
        }
        if (action === "prev") {
            return Math.max(0, current - 1);
        }
        return Math.min(length - 1, current + 1);
    }

    root.MirrorBoardKeyboard = { keyAction: keyAction, stepIndex: stepIndex, isTypingTarget: isTypingTarget };

    var document = root.document;
    if (!document || typeof document.querySelector !== "function") {
        return;
    }

    function boardList() {
        return document.getElementById("board-list");
    }

    // 차단 필터로 접힌 행과 화면에 그려지지 않은 행은 건너뛴다.
    function rowLinks(list) {
        return Array.prototype.filter.call(list.querySelectorAll(":scope > ul > li > a.feed-item[data-post-id]"), function (link) {
            var row = link.parentNode;
            if (row.hidden || row.classList.contains("is-user-filtered")) {
                return false;
            }
            return link.getClientRects().length > 0;
        });
    }

    function overlayOpen() {
        return !!document.querySelector("dialog[open], #media-block-menu:not([hidden]), #author-menu:not([hidden])");
    }

    function currentIndex(links) {
        var active = document.activeElement;
        var index = links.indexOf(active);
        if (index >= 0) {
            return index;
        }
        for (var i = 0; i < links.length; i += 1) {
            if (links[i].classList.contains(CURRENT_CLASS)) {
                return i;
            }
        }
        return -1;
    }

    function clearMarks(list) {
        Array.prototype.forEach.call(list.querySelectorAll("." + CURRENT_CLASS), function (link) {
            link.classList.remove(CURRENT_CLASS);
        });
    }

    function reducedMotion() {
        return !!(root.matchMedia && root.matchMedia("(prefers-reduced-motion: reduce)").matches);
    }

    function select(list, link) {
        clearMarks(list);
        link.classList.add(CURRENT_CLASS);
        link.focus({ preventScroll: true });
        link.scrollIntoView({ block: "nearest", behavior: reducedMotion() ? "auto" : "smooth" });
    }

    function onKeyDown(event) {
        var action = keyAction(event);
        if (!action) {
            return;
        }
        var list = boardList();
        var active = document.activeElement;
        if (!list || isTypingTarget(active) || overlayOpen() || list.getAttribute("aria-busy") === "true") {
            return;
        }
        var links = rowLinks(list);
        var index = currentIndex(links);
        if (action === "clear") {
            if (index < 0) {
                return;
            }
            clearMarks(list);
            if (links[index] === active) {
                active.blur();
            }
            return;
        }
        if (action === "open") {
            if (index < 0) {
                return;
            }
            event.preventDefault();
            links[index].click();
            return;
        }
        var next = stepIndex(index, links.length, action);
        if (next < 0) {
            return;
        }
        event.preventDefault();
        select(list, links[next]);
    }

    // 마우스나 Tab으로 다른 곳을 고르면 키보드 표시를 지운다.
    function onFocusIn(event) {
        var list = boardList();
        if (!list) {
            return;
        }
        var target = event.target;
        if (target && target.classList && target.classList.contains(CURRENT_CLASS)) {
            return;
        }
        clearMarks(list);
    }

    function boot() {
        var list = boardList();
        if (!list) {
            return;
        }
        document.addEventListener("keydown", onKeyDown);
        document.addEventListener("focusin", onFocusIn);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", boot, { once: true });
    } else {
        boot();
    }
})(typeof window !== "undefined" ? window : globalThis);
