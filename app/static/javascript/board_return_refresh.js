(function () {
    "use strict";

    var REFRESH_PARAM = "refresh";
    var RETURN_MARKER_KEY = "mirror_board_return_refresh_v1";
    var refreshInFlight = false;

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

    function currentBoardKey() {
        var url = currentUrl();
        if (!url || url.pathname !== "/board") {
            return "";
        }
        url.searchParams.delete(REFRESH_PARAM);
        return url.pathname + url.search;
    }

    function rememberBoardReturn(event) {
        if (event.defaultPrevented || event.button !== 0) {
            return;
        }
        if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) {
            return;
        }
        var target = event.target;
        var link = target && typeof target.closest === "function" ? target.closest("a[href]") : null;
        if (!link || link.target === "_blank" || link.hasAttribute("download")) {
            return;
        }
        var destination;
        try {
            destination = new URL(link.href, window.location.href);
        } catch (err) {
            return;
        }
        if (destination.origin !== window.location.origin || destination.pathname !== "/read") {
            return;
        }
        var boardKey = currentBoardKey();
        if (!boardKey) {
            return;
        }
        try {
            window.sessionStorage.setItem(RETURN_MARKER_KEY, boardKey);
        } catch (err) {
        }
    }

    function consumeBoardReturn() {
        var boardKey = currentBoardKey();
        if (!boardKey) {
            return false;
        }
        try {
            if (window.sessionStorage.getItem(RETURN_MARKER_KEY) !== boardKey) {
                return false;
            }
            window.sessionStorage.removeItem(RETURN_MARKER_KEY);
            return true;
        } catch (err) {
            return false;
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

    function replaceBoardList(html) {
        var parsed = new DOMParser().parseFromString(html, "text/html");
        var nextBoardList = parsed.getElementById("board-list");
        var currentBoardList = document.getElementById("board-list");
        if (!nextBoardList || !currentBoardList) {
            throw new Error("board list missing");
        }
        currentBoardList.replaceWith(nextBoardList);
        document.dispatchEvent(new CustomEvent("mirror:board-refreshed", {
            detail: { root: nextBoardList }
        }));
    }

    function canonicalizeBoardUrl(responseUrl) {
        var url;
        try {
            url = new URL(responseUrl, window.location.href);
        } catch (err) {
            return;
        }
        if (url.origin !== window.location.origin || url.pathname !== "/board") {
            return;
        }
        url.searchParams.delete(REFRESH_PARAM);
        var current = currentUrl();
        window.history.replaceState(
            window.history.state,
            "",
            url.pathname + url.search + (url.hash || (current && current.hash) || "")
        );
    }

    function refreshAfterHistoryNavigation(event) {
        var isMarkedReturn = consumeBoardReturn();
        if (enteredWithRefreshMarker) {
            enteredWithRefreshMarker = false;
            return;
        }
        if ((!isMarkedReturn && !isHistoryNavigation(event)) || refreshInFlight) {
            return;
        }
        var url = currentUrl();
        if (!url) {
            return;
        }
        url.searchParams.set(REFRESH_PARAM, "1");
        removeRefreshMarker();
        refreshInFlight = true;
        fetch(url.toString(), {
            credentials: "same-origin",
            headers: {
                "Accept": "text/html"
            }
        })
            .then(function (response) {
                if (!response.ok) {
                    throw new Error("board refresh failed");
                }
                return response.text().then(function (html) {
                    return {
                        html: html,
                        url: response.url
                    };
                });
            })
            .then(function (payload) {
                canonicalizeBoardUrl(payload.url);
                replaceBoardList(payload.html);
            })
            .catch(function () {
            })
            .finally(function () {
                refreshInFlight = false;
            });
    }

    var enteredWithRefreshMarker = hasRefreshMarker();
    removeRefreshMarker();
    document.addEventListener("click", rememberBoardReturn);
    window.addEventListener("pageshow", refreshAfterHistoryNavigation);
})();
