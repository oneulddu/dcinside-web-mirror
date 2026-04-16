(function () {
    "use strict";

    var STORAGE_KEY = "read_posts_v1";
    var THEME_STORAGE_KEY = "mirror_theme_v1";
    var MAX_ENTRIES = 1500;
    var DEFAULT_THEME = "dark";
    var THEME_VARIABLES = {
        dark: {
            "--page-bg": "#09090b",
            "--shell-bg": "#18181b",
            "--shell-border": "rgba(255, 255, 255, 0.08)",
            "--header-top": "rgba(24, 24, 27, 0.85)",
            "--header-tab": "#18181b",
            "--text-main": "#e4e4e7",
            "--text-title": "#ffffff",
            "--text-sub": "#a1a1aa",
            "--text-soft": "#71717a",
            "--blue": "#3b82f6",
            "--blue-hover": "#60a5fa"
        },
        light: {
            "--page-bg": "#f4f4f5",
            "--shell-bg": "#ffffff",
            "--shell-border": "rgba(24, 24, 27, 0.10)",
            "--header-top": "rgba(255, 255, 255, 0.88)",
            "--header-tab": "#ffffff",
            "--text-main": "#27272a",
            "--text-title": "#09090b",
            "--text-sub": "#52525b",
            "--text-soft": "#71717a",
            "--blue": "#2563eb",
            "--blue-hover": "#1d4ed8"
        }
    };

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


    function applyThemeVariables(theme) {
        var variables = THEME_VARIABLES[theme] || THEME_VARIABLES[DEFAULT_THEME];
        var rootStyle = document.documentElement.style;
        var name;
        for (name in variables) {
            if (Object.prototype.hasOwnProperty.call(variables, name)) {
                rootStyle.setProperty(name, variables[name]);
            }
        }
    }

    function updateThemeToggle(theme) {
        var button = document.querySelector(".theme-toggle");
        if (!button) {
            return;
        }
        var isLight = theme === "light";
        button.textContent = isLight ? "☾" : "☀";
        button.setAttribute("aria-pressed", isLight ? "true" : "false");
        button.setAttribute(
            "aria-label",
            isLight ? "밝은 테마 사용 중, 어두운 테마로 전환" : "어두운 테마 사용 중, 밝은 테마로 전환"
        );
        button.title = isLight ? "어두운 테마로 전환" : "밝은 테마로 전환";
    }

    function applyTheme(theme, shouldSave) {
        var nextTheme = normalizeTheme(theme);
        var body = document.body;

        document.documentElement.dataset.theme = nextTheme;
        document.documentElement.style.colorScheme = nextTheme;
        applyThemeVariables(nextTheme);

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
        applyTheme(loadTheme(), false);
        wireThemeToggle();
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
