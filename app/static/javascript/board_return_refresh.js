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

    function refreshAfterHistoryNavigation(event) {
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

    removeRefreshMarker();
    window.addEventListener("pageshow", refreshAfterHistoryNavigation);
})();
