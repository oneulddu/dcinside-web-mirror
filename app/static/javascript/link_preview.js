(function () {
    "use strict";

    // 스크립트가 중복 로드돼도 조회가 두 번 일어나지 않게 한다.
    if (window.__mirrorLinkPreviewLoaded) {
        return;
    }
    window.__mirrorLinkPreviewLoaded = true;
    if (typeof window.fetch !== "function") {
        return;
    }

    var ENDPOINT = "/embed/link-preview";
    var TARGET_SELECTOR = ".article-body a.link-preview-target";
    var MAX_PREVIEWS = 6;
    var LOADING_TEXT = "미리보기 불러오는 중";
    var ERROR_TEXT = "미리보기를 불러오지 못했습니다";

    function buildPlaceholder() {
        var placeholder = document.createElement("span");
        placeholder.className = "link-preview is-loading";
        placeholder.textContent = LOADING_TEXT;
        return placeholder;
    }

    function buildCard(href, data) {
        // 모든 텍스트는 textContent로만 주입한다(원격 메타데이터는 신뢰하지 않는다).
        var card = document.createElement("a");
        card.className = "link-preview";
        card.href = href;
        card.target = "_blank";
        card.rel = "noopener noreferrer";

        var title = document.createElement("span");
        title.className = "link-preview-title";
        title.textContent = data.title;
        card.appendChild(title);

        if (data.description) {
            var desc = document.createElement("span");
            desc.className = "link-preview-desc";
            desc.textContent = data.description;
            card.appendChild(desc);
        }

        var host = document.createElement("span");
        host.className = "link-preview-host";
        host.textContent = data.site_name || data.host || "";
        card.appendChild(host);
        return card;
    }

    function queryPreview(anchor) {
        var href = anchor.href;
        var placeholder = buildPlaceholder();
        anchor.parentNode.insertBefore(placeholder, anchor.nextSibling);
        fetch(ENDPOINT + "?url=" + encodeURIComponent(href), { credentials: "same-origin" })
            .then(function (response) {
                return response.ok ? response.json() : null;
            })
            .then(function (data) {
                if (data && data.ok && data.title) {
                    placeholder.parentNode.replaceChild(buildCard(href, data), placeholder);
                } else {
                    // 실패해도 원래 링크는 그대로 동작한다. 문구만 남긴다.
                    placeholder.className = "link-preview is-error";
                    placeholder.textContent = ERROR_TEXT;
                }
            })
            .catch(function () {
                placeholder.className = "link-preview is-error";
                placeholder.textContent = ERROR_TEXT;
            });
    }

    var anchors = document.querySelectorAll(TARGET_SELECTOR);
    var seen = {};
    // 서버(og-wrap 정규화)가 이미 같은 URL의 카드를 만들어 둔 경우 중복 조회하지 않는다.
    var existingCards = document.querySelectorAll(".article-body a.link-preview");
    for (var c = 0; c < existingCards.length; c += 1) {
        if (existingCards[c].href) {
            seen[existingCards[c].href] = true;
        }
    }
    var targets = [];
    for (var i = 0; i < anchors.length && targets.length < MAX_PREVIEWS; i += 1) {
        var href = anchors[i].href;
        // 서버 조회는 https만 지원하므로 http 링크가 조회 슬롯을 소진하지 않게 거른다.
        if (!href || seen[href] || anchors[i].protocol !== "https:") {
            continue;
        }
        seen[href] = true;
        targets.push(anchors[i]);
    }
    if (!targets.length) {
        return;
    }

    // 뷰포트 근처에서만 조회해 불필요한 서버 outbound를 줄인다.
    if (typeof IntersectionObserver === "function") {
        var pending = new IntersectionObserver(function (entries) {
            for (var e = 0; e < entries.length; e += 1) {
                if (entries[e].isIntersecting) {
                    pending.unobserve(entries[e].target);
                    queryPreview(entries[e].target);
                }
            }
        }, { rootMargin: "300px 0px" });
        for (var j = 0; j < targets.length; j += 1) {
            pending.observe(targets[j]);
        }
    } else {
        for (var k = 0; k < targets.length; k += 1) {
            queryPreview(targets[k]);
        }
    }
}());
