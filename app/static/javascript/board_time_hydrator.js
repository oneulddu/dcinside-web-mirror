(function () {
    "use strict";

    function trim(value) {
        return String(value || "").trim();
    }

    function buildParams(section, postIds) {
        var params = new URLSearchParams();
        params.set("board", trim(section.dataset.board));
        params.set("page", trim(section.dataset.page) || "1");
        params.set("recommend", trim(section.dataset.recommend) === "1" ? "1" : "0");
        if (postIds.length) {
            params.set("ids", postIds.join(","));
        }

        var kind = trim(section.dataset.kind);
        var headId = trim(section.dataset.headId);
        var searchType = trim(section.dataset.searchType);
        var searchKeyword = trim(section.dataset.searchKeyword);

        if (kind) {
            params.set("kind", kind);
        }
        if (headId) {
            params.set("headid", headId);
        }
        if (searchKeyword) {
            params.set("s_type", searchType || "subject_m");
            params.set("serval", searchKeyword);
        }
        return params;
    }

    function collectTargets(section) {
        var nodes = section.querySelectorAll("[data-board-time][data-needs-time-hydrate='1'][data-post-id]");
        var byId = {};
        for (var i = 0; i < nodes.length; i += 1) {
            var postId = trim(nodes[i].dataset.postId);
            if (!postId) {
                continue;
            }
            if (!byId[postId]) {
                byId[postId] = [];
            }
            byId[postId].push(nodes[i]);
        }
        return byId;
    }

    function applyTimes(targets, times, titles, datetimes) {
        titles = titles || {};
        datetimes = datetimes || {};
        Object.keys(times || {}).forEach(function (postId) {
            var value = trim(times[postId]);
            var nodes = targets[postId];
            if (!value || !nodes) {
                return;
            }
            var title = trim(titles[postId]);
            var datetime = trim(datetimes[postId]);
            for (var i = 0; i < nodes.length; i += 1) {
                var node = nodes[i];
                if (datetime && node.tagName !== "TIME" && node.parentNode) {
                    var replacement = document.createElement("time");
                    for (var j = 0; j < node.attributes.length; j += 1) {
                        replacement.setAttribute(node.attributes[j].name, node.attributes[j].value);
                    }
                    node.parentNode.replaceChild(replacement, node);
                    node = replacement;
                    nodes[i] = replacement;
                }
                node.textContent = value;
                if (title) {
                    node.title = title;
                }
                if (datetime) {
                    node.setAttribute("datetime", datetime);
                }
                delete node.dataset.needsTimeHydrate;
            }
        });
    }

    function hydrateBoardTimes() {
        var section = document.getElementById("board-list");
        if (!section) {
            return;
        }

        var targets = collectTargets(section);
        var postIds = Object.keys(targets);
        if (!postIds.length) {
            return;
        }

        fetch("/board/times?" + buildParams(section, postIds).toString(), {
            credentials: "same-origin",
            headers: {
                "Accept": "application/json"
            }
        })
            .then(function (response) {
                if (!response.ok) {
                    throw new Error("board time fetch failed");
                }
                return response.json();
            })
            .then(function (payload) {
                if (!payload || payload.ok === false) {
                    return;
                }
                applyTimes(targets, payload.times || {}, payload.titles, payload.datetimes);
            })
            .catch(function () {
            });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", hydrateBoardTimes, { once: true });
    } else {
        hydrateBoardTimes();
    }
    document.addEventListener("mirror:board-refreshed", hydrateBoardTimes);
})();
