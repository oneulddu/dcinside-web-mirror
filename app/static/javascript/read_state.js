(function () {
    "use strict";

    var STORAGE_KEY = "read_posts_v1";
    var MAX_ENTRIES = 1500;

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
        var store = loadStore();
        store[key] = Date.now();
        saveStore(pruneStore(store));
    }

    function markCurrentRead() {
        if (window.location.pathname !== "/read") {
            return;
        }
        var params = new URLSearchParams(window.location.search || "");
        markRead(toReadKey(params.get("board"), params.get("pid")));
    }

    function applyReadState(root) {
        var scope = root || document;
        var store = loadStore();
        var links = scope.querySelectorAll("a.feed-item[href*=\"/read?\"]");
        var i;
        for (i = 0; i < links.length; i += 1) {
            var link = links[i];
            var key = parseReadHref(link.getAttribute("href"));
            if (!key) {
                continue;
            }
            link.classList.toggle("is-read", !!store[key]);
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
        var observer = new MutationObserver(function (mutations) {
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
                        applyReadState(node);
                    }
                }
            }
        });
        observer.observe(document.body, { childList: true, subtree: true });
    }

    function boot() {
        markCurrentRead();
        applyReadState(document);
        wireClickMarking();
        wireDynamicApply();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", boot, { once: true });
    } else {
        boot();
    }
})();
