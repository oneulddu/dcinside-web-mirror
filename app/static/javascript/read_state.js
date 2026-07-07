(function () {
    "use strict";

    var STORAGE_KEY = "read_posts_v1";
    var THEME_STORAGE_KEY = "mirror_theme_v1";
    var DCCON_BLOCK_STORAGE_KEY = "mirror_dccon_block_v1";
    var MAX_ENTRIES = 1500;
    var DEFAULT_THEME = "dark";
    var readStore = null;

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

    function loadDcconBlocked() {
        try {
            return window.localStorage.getItem(DCCON_BLOCK_STORAGE_KEY) === "1";
        } catch (err) {
            return false;
        }
    }

    function saveDcconBlocked(isBlocked) {
        try {
            window.localStorage.setItem(DCCON_BLOCK_STORAGE_KEY, isBlocked ? "1" : "0");
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

    function updateDcconToggle(isBlocked) {
        var button = document.querySelector(".dccon-toggle");
        if (!button) {
            return;
        }
        var label = isBlocked ? "디시콘 차단 중, 표시로 전환" : "디시콘 표시 중, 차단으로 전환";
        button.setAttribute("aria-label", label);
        button.setAttribute("aria-pressed", isBlocked ? "true" : "false");
        button.title = label;
    }

    function hydrateDccons(root, isBlocked) {
        var scope = root || document;
        var images = scope.querySelectorAll("img.dccon[data-dccon-src]");
        var i;
        for (i = 0; i < images.length; i += 1) {
            var image = images[i];
            if (isBlocked) {
                image.removeAttribute("src");
                image.hidden = true;
                continue;
            }
            if (!image.getAttribute("src")) {
                image.setAttribute("src", image.getAttribute("data-dccon-src"));
            }
            image.hidden = false;
        }
    }

    function applyDcconBlock(isBlocked, shouldSave) {
        var blocked = !!isBlocked;
        document.documentElement.dataset.dcconBlocked = blocked ? "true" : "false";
        if (document.body) {
            document.body.dataset.dcconBlocked = blocked ? "true" : "false";
        }
        hydrateDccons(document, blocked);
        updateDcconToggle(blocked);
        if (shouldSave) {
            saveDcconBlocked(blocked);
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

    function wireDcconToggle() {
        var button = document.querySelector(".dccon-toggle");
        if (!button) {
            return;
        }
        button.addEventListener("click", function () {
            var isBlocked = document.documentElement.dataset.dcconBlocked === "true";
            applyDcconBlock(!isBlocked, true);
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
        if (url.pathname !== "/read" && url.pathname !== "/v2/read") {
            return null;
        }
        return toReadKey(url.searchParams.get("board"), url.searchParams.get("pid"));
    }

    function markRead(key) {
        if (!key) {
            return;
        }
        var store = readStore || loadStore();
        store[key] = Date.now();
        readStore = pruneStore(store);
        saveStore(readStore);
    }

    function markCurrentRead() {
        if (window.location.pathname !== "/read" && window.location.pathname !== "/v2/read") {
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
        applyDcconBlock(loadDcconBlocked(), false);
        wireDcconToggle();
        readStore = loadStore();
        markCurrentRead();
        applyReadState(document, readStore);
        wireClickMarking();
        wireDynamicApply();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", boot, { once: true });
    } else {
        boot();
    }
})();
