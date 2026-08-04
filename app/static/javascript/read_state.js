(function () {
    "use strict";

    var STORAGE_KEY = "read_posts_v1";
    var THEME_STORAGE_KEY = "mirror_theme_v1";
    var MEDIA_BLOCK_STORAGE_KEY = "mirror_media_block_mode_v1";
    var LEGACY_DCCON_BLOCK_STORAGE_KEY = "mirror_dccon_block_v1";
    var MEDIA_BLOCK_MODES = { none: true, dccon: true, body: true, all: true };
    var MAX_ENTRIES = 1500;
    var DEFAULT_THEME = "dark";
    var readStore = null;
    var dcconFoldShowing = false;

    function safeParse(jsonText) {
        if (!jsonText) {
            return {};
        }
        try {
            var parsed = JSON.parse(jsonText);
            if (parsed && typeof parsed === "object") {
                return parsed;
            }
        } catch (err) {
        }
        return {};
    }

    function loadStore() {
        try {
            return safeParse(window.localStorage.getItem(STORAGE_KEY));
        } catch (err) {
            return {};
        }
    }

    function saveStore(store) {
        try {
            window.localStorage.setItem(STORAGE_KEY, JSON.stringify(store));
        } catch (err) {
        }
    }

    function pruneStore(store) {
        var entries = [];
        var key;
        for (key in store) {
            if (Object.prototype.hasOwnProperty.call(store, key)) {
                entries.push([key, Number(store[key]) || 0]);
            }
        }
        if (entries.length <= MAX_ENTRIES) {
            return store;
        }
        entries.sort(function (a, b) {
            return b[1] - a[1];
        });
        var next = {};
        var i;
        for (i = 0; i < MAX_ENTRIES; i += 1) {
            next[entries[i][0]] = entries[i][1];
        }
        return next;
    }

    function toReadKey(board, pid) {
        var b = (board || "").trim();
        var p = (pid || "").trim();
        if (!b || !p) {
            return null;
        }
        return b + "|" + p;
    }

    function normalizeTheme(theme) {
        return theme === "light" ? "light" : "dark";
    }

    function loadTheme() {
        try {
            return normalizeTheme(window.localStorage.getItem(THEME_STORAGE_KEY) || DEFAULT_THEME);
        } catch (err) {
            return DEFAULT_THEME;
        }
    }

    function saveTheme(theme) {
        try {
            window.localStorage.setItem(THEME_STORAGE_KEY, normalizeTheme(theme));
        } catch (err) {
        }
    }

    function normalizeMediaBlockMode(mode) {
        return MEDIA_BLOCK_MODES[mode] ? mode : "none";
    }

    function loadMediaBlockMode() {
        try {
            var saved = window.localStorage.getItem(MEDIA_BLOCK_STORAGE_KEY);
            if (MEDIA_BLOCK_MODES[saved]) {
                return saved;
            }
            return window.localStorage.getItem(LEGACY_DCCON_BLOCK_STORAGE_KEY) === "1" ? "dccon" : "none";
        } catch (err) {
            return "none";
        }
    }

    function saveMediaBlockMode(mode) {
        try {
            window.localStorage.setItem(MEDIA_BLOCK_STORAGE_KEY, normalizeMediaBlockMode(mode));
            window.localStorage.removeItem(LEGACY_DCCON_BLOCK_STORAGE_KEY);
        } catch (err) {
        }
    }


    function updateThemeToggle(theme) {
        var button = document.querySelector(".theme-toggle");
        if (!button) {
            return;
        }
        // 아이콘은 html[data-theme] 기반 CSS가 그린다. 여기서는 레이블만 맞춘다.
        var isLight = theme === "light";
        var actionLabel = isLight ? "어두운 테마로 전환" : "밝은 테마로 전환";
        button.setAttribute("aria-label", actionLabel);
        button.title = actionLabel;
    }

    function applyTheme(theme, shouldSave) {
        var nextTheme = normalizeTheme(theme);
        var body = document.body;

        document.documentElement.dataset.theme = nextTheme;
        document.documentElement.style.colorScheme = nextTheme;

        if (body) {
            body.dataset.theme = nextTheme;
            body.classList.toggle("theme-light", nextTheme === "light");
            body.classList.toggle("theme-dark", nextTheme === "dark");
        }
        updateThemeToggle(nextTheme);

        if (shouldSave) {
            saveTheme(nextTheme);
        }
    }

    function mediaModeBlocksDccons(mode) {
        return mode === "dccon" || mode === "all";
    }

    function mediaModeBlocksBodyImages(mode) {
        return mode === "body" || mode === "all";
    }

    function mediaBlockModeLabel(mode) {
        var labels = {
            none: "차단 없음",
            dccon: "디시콘만",
            body: "본문 이미지만",
            all: "본문 이미지까지"
        };
        return labels[normalizeMediaBlockMode(mode)];
    }

    function updateMediaBlockControl(mode) {
        var normalized = normalizeMediaBlockMode(mode);
        var button = document.querySelector(".dccon-toggle");
        if (!button) {
            return;
        }
        var label = "이미지 차단 설정: " + mediaBlockModeLabel(normalized);
        button.setAttribute("aria-label", label);
        button.title = label;
        var options = document.querySelectorAll(".media-block-option[data-media-block-mode]");
        var i;
        for (i = 0; i < options.length; i += 1) {
            var selected = options[i].getAttribute("data-media-block-mode") === normalized;
            options[i].setAttribute("aria-checked", selected ? "true" : "false");
            options[i].classList.toggle("is-selected", selected);
        }
    }

    function hydrateDeferredImages(root, selector, sourceAttribute, isBlocked) {
        var scope = root || document;
        var images = scope.querySelectorAll(selector);
        var i;
        for (i = 0; i < images.length; i += 1) {
            var image = images[i];
            if (isBlocked) {
                image.removeAttribute("src");
                image.hidden = true;
                continue;
            }
            if (!image.getAttribute("src")) {
                image.setAttribute("src", image.getAttribute(sourceAttribute));
            }
            image.hidden = false;
        }
    }

    function hydrateDccons(root, isBlocked) {
        hydrateDeferredImages(root, "img.dccon[data-dccon-src]", "data-dccon-src", isBlocked);
    }

    function hydrateBodyImages(root, isBlocked) {
        hydrateDeferredImages(root, "img.body-image[data-body-image-src]", "data-body-image-src", isBlocked);
    }

    function dcconCommentItems() {
        var images = document.querySelectorAll("img.dccon[data-dccon-src]");
        var items = [];
        var i;
        for (i = 0; i < images.length; i += 1) {
            var item = images[i].closest(".comment-item");
            if (item && items.indexOf(item) === -1) {
                items.push(item);
            }
        }
        return items;
    }

    function removeDcconFoldToggle() {
        var button = document.querySelector(".comment-dccon-block-toggle");
        if (button) {
            button.remove();
        }
    }

    function updateDcconFoldToggle(button, count) {
        button.setAttribute("aria-expanded", dcconFoldShowing ? "true" : "false");
        button.textContent = dcconFoldShowing ? "차단된 이모티콘 숨기기" : "차단된 이모티콘 보기 (" + count + ")";
    }

    function ensureDcconFoldToggle(count) {
        var shell = document.querySelector(".comment-shell");
        if (!shell || !count) {
            removeDcconFoldToggle();
            return null;
        }
        var button = document.querySelector(".comment-dccon-block-toggle");
        if (!button) {
            button = document.createElement("button");
            button.type = "button";
            button.className = "comment-spam-toggle comment-dccon-block-toggle";
            button.addEventListener("click", function () {
                dcconFoldShowing = !dcconFoldShowing;
                syncDcconCommentFold(true);
            });
            var title = shell.querySelector("h2");
            if (title) {
                title.insertAdjacentElement("afterend", button);
            } else {
                shell.prepend(button);
            }
        }
        updateDcconFoldToggle(button, count);
        return button;
    }

    function syncDcconCommentFold(isBlocked) {
        var items = dcconCommentItems();
        var i;
        if (!isBlocked || !items.length) {
            removeDcconFoldToggle();
            for (i = 0; i < items.length; i += 1) {
                items[i].classList.remove("comment-dccon-block-hidden", "comment-spam-highlight");
            }
            var commentShell = document.querySelector(".comment-shell");
            if (commentShell) {
                hydrateDccons(commentShell, false);
            }
            return;
        }

        ensureDcconFoldToggle(items.length);
        for (i = 0; i < items.length; i += 1) {
            items[i].classList.toggle("comment-dccon-block-hidden", !dcconFoldShowing);
            items[i].classList.toggle("comment-spam-highlight", dcconFoldShowing);
            hydrateDccons(items[i], !dcconFoldShowing);
        }
    }

    function applyMediaBlockMode(mode, shouldSave) {
        var normalized = normalizeMediaBlockMode(mode);
        var dcconBlocked = mediaModeBlocksDccons(normalized);
        var bodyBlocked = mediaModeBlocksBodyImages(normalized);
        document.documentElement.dataset.mediaBlockMode = normalized;
        document.documentElement.dataset.dcconBlocked = dcconBlocked ? "true" : "false";
        if (document.body) {
            document.body.dataset.mediaBlockMode = normalized;
            document.body.dataset.dcconBlocked = dcconBlocked ? "true" : "false";
        }
        if (!dcconBlocked) {
            dcconFoldShowing = false;
        }
        hydrateBodyImages(document, bodyBlocked);
        var articleBody = document.querySelector(".article-body");
        if (articleBody) {
            hydrateDccons(articleBody, dcconBlocked || bodyBlocked);
        }
        syncDcconCommentFold(dcconBlocked);
        updateMediaBlockControl(normalized);
        if (shouldSave) {
            saveMediaBlockMode(normalized);
        }
    }

    function wireThemeToggle() {
        var button = document.querySelector(".theme-toggle");
        if (!button) {
            return;
        }
        button.addEventListener("click", function () {
            var currentTheme = normalizeTheme(document.documentElement.dataset.theme || loadTheme());
            applyTheme(currentTheme === "light" ? "dark" : "light", true);
        });
    }

    function closeMediaBlockMenu(shouldFocus) {
        var button = document.querySelector(".dccon-toggle");
        var menu = document.querySelector(".media-block-menu");
        if (!button || !menu) {
            return;
        }
        menu.hidden = true;
        button.setAttribute("aria-expanded", "false");
        if (shouldFocus) {
            button.focus();
        }
    }

    function openMediaBlockMenu() {
        var button = document.querySelector(".dccon-toggle");
        var menu = document.querySelector(".media-block-menu");
        if (!button || !menu) {
            return;
        }
        menu.hidden = false;
        button.setAttribute("aria-expanded", "true");
        var selected = menu.querySelector('.media-block-option[aria-checked="true"]');
        if (selected) {
            selected.focus();
        }
    }

    function wireMediaBlockControl() {
        var button = document.querySelector(".dccon-toggle");
        var control = document.querySelector(".media-block-control");
        var menu = document.querySelector(".media-block-menu");
        if (!button || !control || !menu) {
            return;
        }
        button.addEventListener("click", function () {
            if (menu.hidden) {
                openMediaBlockMenu();
            } else {
                closeMediaBlockMenu(false);
            }
        });
        menu.addEventListener("click", function (event) {
            var option = event.target.closest(".media-block-option[data-media-block-mode]");
            if (!option) {
                return;
            }
            applyMediaBlockMode(option.getAttribute("data-media-block-mode"), true);
            closeMediaBlockMenu(true);
        });
        menu.addEventListener("keydown", function (event) {
            if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
                return;
            }
            var options = Array.prototype.slice.call(menu.querySelectorAll(".media-block-option"));
            if (!options.length) {
                return;
            }
            event.preventDefault();
            var currentIndex = options.indexOf(document.activeElement);
            var nextIndex = currentIndex;
            if (event.key === "Home") {
                nextIndex = 0;
            } else if (event.key === "End") {
                nextIndex = options.length - 1;
            } else if (event.key === "ArrowDown") {
                nextIndex = (currentIndex + 1 + options.length) % options.length;
            } else {
                nextIndex = (currentIndex - 1 + options.length) % options.length;
            }
            options[nextIndex].focus();
        });
        document.addEventListener("click", function (event) {
            if (!control.contains(event.target)) {
                closeMediaBlockMenu(false);
            }
        });
        document.addEventListener("keydown", function (event) {
            if (event.key === "Escape" && !menu.hidden) {
                closeMediaBlockMenu(true);
            }
        });
    }

    function parseReadHref(href) {
        if (!href) {
            return null;
        }
        var url;
        try {
            url = new URL(href, window.location.origin);
        } catch (err) {
            return null;
        }
        if (url.pathname !== "/read") {
            return null;
        }
        return toReadKey(url.searchParams.get("board"), url.searchParams.get("pid"));
    }

    function markRead(key) {
        if (!key) {
            return;
        }
        // 다른 탭이나 이전 방문에서 쌓인 기록을 덮어쓰지 않도록 항상 최신 저장소를 다시 읽는다.
        var store = loadStore();
        store[key] = Date.now();
        readStore = pruneStore(store);
        saveStore(readStore);
    }

    function markCurrentRead() {
        if (window.location.pathname !== "/read") {
            return;
        }
        var params = new URLSearchParams(window.location.search || "");
        markRead(toReadKey(params.get("board"), params.get("pid")));
    }

    function applyReadState(root, store) {
        var scope = root || document;
        var currentStore = store || readStore || loadStore();
        var links = scope.querySelectorAll("a.feed-item[href*=\"/read?\"]");
        var i;
        for (i = 0; i < links.length; i += 1) {
            var link = links[i];
            var key = parseReadHref(link.getAttribute("href"));
            if (!key) {
                continue;
            }
            link.classList.toggle("is-read", !!currentStore[key]);
        }
    }

    function wireClickMarking() {
        document.addEventListener("click", function (event) {
            var target = event.target;
            if (!target) {
                return;
            }
            var link = target.closest("a.feed-item[href*=\"/read?\"]");
            if (!link) {
                return;
            }
            markRead(parseReadHref(link.getAttribute("href")));
            link.classList.add("is-read");
        }, true);
    }

    function wireDynamicApply() {
        var relatedList = document.getElementById("related-list");
        if (!relatedList) {
            return;
        }
        var observer = new MutationObserver(function (mutations) {
            readStore = loadStore();
            var i;
            for (i = 0; i < mutations.length; i += 1) {
                var m = mutations[i];
                if (!m.addedNodes || !m.addedNodes.length) {
                    continue;
                }
                var j;
                for (j = 0; j < m.addedNodes.length; j += 1) {
                    var node = m.addedNodes[j];
                    if (node && node.nodeType === 1) {
                        applyReadState(node, readStore);
                    }
                }
            }
        });
        observer.observe(relatedList, { childList: true });
    }

    function boot() {
        applyTheme(loadTheme(), false);
        wireThemeToggle();
        applyMediaBlockMode(loadMediaBlockMode(), false);
        wireMediaBlockControl();
        readStore = loadStore();
        markCurrentRead();
        applyReadState(document, readStore);
        wireClickMarking();
        wireDynamicApply();
    }

    function refreshReadState() {
        readStore = loadStore();
        applyReadState(document, readStore);
    }

    document.addEventListener("mirror:board-refreshed", function (event) {
        readStore = loadStore();
        applyReadState(event.detail && event.detail.root, readStore);
    });

    // 뒤로 가기로 bfcache에서 복원되면 스크립트가 다시 실행되지 않는다.
    // 그동안 다른 탭이나 상세 화면에서 늘어난 읽음 기록을 즉시 반영한다.
    window.addEventListener("pageshow", function (event) {
        if (event.persisted) {
            refreshReadState();
        }
    });

    // 다른 탭에서 글을 읽은 경우 저장소 변경을 즉시 반영한다.
    window.addEventListener("storage", function (event) {
        if (!event.key || event.key === STORAGE_KEY) {
            refreshReadState();
        }
    });

    document.addEventListener("visibilitychange", function () {
        if (document.visibilityState === "visible") {
            refreshReadState();
        }
    });

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", boot, { once: true });
    } else {
        boot();
    }
})();
