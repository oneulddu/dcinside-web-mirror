(function () {
    "use strict";

    var META_TYPE = "mirror:movie-meta";
    var REQUEST_TYPE = "mirror:movie-meta-request";
    var CANDIDATE_SELECTOR = '.article-body iframe[src^="/movie"]';
    var MIN_RATIO = 0.2;
    var MAX_RATIO = 5;
    var ERROR_TITLE = "동영상을 불러오지 못했습니다";
    var STATE_SIZED = "sized";
    var STATE_ERROR = "error";

    function collectCandidates() {
        return document.querySelectorAll(CANDIDATE_SELECTOR);
    }

    function requestMeta(iframe) {
        if (!iframe || !iframe.contentWindow) {
            return;
        }
        try {
            iframe.contentWindow.postMessage({ type: REQUEST_TYPE }, window.location.origin);
        } catch (err) {
            // 요청은 유실 복구용 보조 신호라 실패해도 무시한다.
        }
    }

    function findSourceIframe(source) {
        if (!source) {
            return null;
        }
        var candidates = collectCandidates();
        for (var i = 0; i < candidates.length; i += 1) {
            var contentWindow = candidates[i].contentWindow;
            if (contentWindow && contentWindow === source) {
                return candidates[i];
            }
        }
        return null;
    }

    function isFinitePositive(value) {
        return typeof value === "number" && isFinite(value) && value > 0;
    }

    function applySize(iframe, width, height) {
        // 크기 메시지는 첫 유효 값만 적용하고, error 확정 후에는 무시한다.
        if (iframe.dataset.movieMetaState) {
            return;
        }
        var ratio = width / height;
        var aspectRatio = width + " / " + height;
        if (ratio < MIN_RATIO) {
            ratio = MIN_RATIO;
            aspectRatio = String(ratio);
        } else if (ratio > MAX_RATIO) {
            ratio = MAX_RATIO;
            aspectRatio = String(ratio);
        }
        iframe.dataset.movieMetaState = STATE_SIZED;
        iframe.style.aspectRatio = aspectRatio;
        if (ratio < 1) {
            // 세로 영상: 폭을 제한해 화면 높이를 넘지 않게 한다. 중앙 정렬은 CSS margin auto가 처리한다.
            iframe.style.width = "min(100%, 360px, calc(82vh * " + ratio + "))";
        } else {
            iframe.style.removeProperty("width");
        }
    }

    function applyError(iframe) {
        // error는 나중에 와도 크기 적용 상태를 덮어쓴다.
        if (iframe.dataset.movieMetaState === STATE_ERROR) {
            return;
        }
        iframe.dataset.movieMetaState = STATE_ERROR;
        iframe.style.removeProperty("aspect-ratio");
        iframe.style.removeProperty("width");
        iframe.classList.add("is-embed-error");
        iframe.title = ERROR_TITLE;
    }

    window.addEventListener("message", function (event) {
        if (event.origin !== window.location.origin) {
            return;
        }
        var data = event.data;
        if (!data || data.type !== META_TYPE) {
            return;
        }
        var iframe = findSourceIframe(event.source);
        if (!iframe) {
            return;
        }
        if (data.error === true) {
            applyError(iframe);
            return;
        }
        if (!isFinitePositive(data.width) || !isFinitePositive(data.height)) {
            return;
        }
        applySize(iframe, data.width, data.height);
    });

    // 리스너 등록 직후와 각 후보 iframe 로드 시점에 재요청해 유실된 메타를 복구한다.
    var initialCandidates = collectCandidates();
    for (var i = 0; i < initialCandidates.length; i += 1) {
        (function (iframe) {
            iframe.addEventListener("load", function () {
                requestMeta(iframe);
            });
            requestMeta(iframe);
        }(initialCandidates[i]));
    }
}());
