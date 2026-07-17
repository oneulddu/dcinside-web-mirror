(function () {
    "use strict";

    var REFRESH_PARAM = "refresh";

    function currentUrl() {
        try {
            return new URL(window.location.href);
        } catch (err) {
            return null;
        }
    }

    function hasRefreshMarker() {
        var url = currentUrl();
        return Boolean(url && url.searchParams.get(REFRESH_PARAM) === "1");
    }

    function removeRefreshMarker() {
        var url = currentUrl();
        if (!url || url.searchParams.get(REFRESH_PARAM) !== "1") {
            return;
        }
        url.searchParams.delete(REFRESH_PARAM);
        window.history.replaceState(
            window.history.state,
            "",
            url.pathname + url.search + url.hash
        );
    }

    function isHistoryNavigation(event) {
        if (event.persisted) {
            return true;
        }
        if (!window.performance || typeof window.performance.getEntriesByType !== "function") {
            return false;
        }
        var entries = window.performance.getEntriesByType("navigation");
        return entries.length > 0 && entries[0].type === "back_forward";
    }

    function markCurrentBoardForRefresh(event) {
        if (event.defaultPrevented || event.button !== 0) {
            return;
        }
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
            return;
        }

        var link = event.target.closest("a[href]");
        if (!link || link.target === "_blank" || link.hasAttribute("download")) {
            return;
        }

        var target;
        try {
            target = new URL(link.href, window.location.href);
        } catch (err) {
            return;
        }
        if (target.origin !== window.location.origin || target.pathname !== "/read") {
            return;
        }

        var url = currentUrl();
        if (!url || url.pathname !== "/board") {
            return;
        }
        url.searchParams.set(REFRESH_PARAM, "1");
        window.history.replaceState(
            window.history.state,
            "",
            url.pathname + url.search + url.hash
        );
    }

    function refreshAfterHistoryNavigation(event) {
        if (enteredWithRefreshMarker) {
            return;
        }
        if (!isHistoryNavigation(event)) {
            return;
        }
        var url = currentUrl();
        if (!url) {
            return;
        }
        url.searchParams.set(REFRESH_PARAM, "1");
        window.location.replace(url.toString());
    }

    var enteredWithRefreshMarker = hasRefreshMarker();
    removeRefreshMarker();
    document.addEventListener("click", markCurrentBoardForRefresh);
    window.addEventListener("pageshow", refreshAfterHistoryNavigation);
})();
