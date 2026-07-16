(function () {
    "use strict";

    // 스크립트가 중복 로드돼도 리스너와 조회가 두 번 등록되지 않게 한다.
    if (window.__mirrorEmbedResizerLoaded) {
        return;
    }
    window.__mirrorEmbedResizerLoaded = true;

    var META_TYPE = "mirror:movie-meta";
    var REQUEST_TYPE = "mirror:movie-meta-request";
    var CANDIDATE_SELECTOR = '.article-body iframe[src^="/movie"]';
    var YOUTUBE_SELECTOR = '.article-body iframe[src*="youtube.com/embed/"], .article-body iframe[src*="youtube-nocookie.com/embed/"]';
    var YOUTUBE_SIZE_ENDPOINT = "/embed/youtube-size";
    var YOUTUBE_ID_PATTERN = /\/embed\/([A-Za-z0-9_-]{11})(?:[/?#&]|$)/;
    var YOUTUBE_SIZE_BATCH = 12;
    var TWITTER_ORIGIN = "https://platform.twitter.com";
    var TWITTER_SELECTOR = '.article-body iframe[src^="https://platform.twitter.com/embed/"]';
    var TWITTER_MIN_HEIGHT = 100;
    var TWITTER_MAX_HEIGHT = 3000;
    var TWITTER_TIMEOUT_MS = 8000;
    var TWITTER_ERROR_TEXT = "X 게시물을 불러오지 못했습니다";
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
            // 유튜브 등 CSS margin auto가 없는 임베드도 중앙에 놓이게 인라인으로 보강한다.
            iframe.style.marginLeft = "auto";
            iframe.style.marginRight = "auto";
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

    // 유튜브 임베드: 서버가 frame0 썸네일에서 읽은 실제 영상 비율을 조회해 적용한다.
    // (oEmbed는 세로 영상도 16:9로 보고하므로 서버측 판별 결과를 쓴다.)
    function youtubeVideoId(iframe) {
        var src = iframe.getAttribute("src") || "";
        var match = YOUTUBE_ID_PATTERN.exec(src);
        return match ? match[1] : null;
    }

    function fetchYoutubeSizes(ids, framesById) {
        fetch(YOUTUBE_SIZE_ENDPOINT + "?ids=" + ids.join(","), { credentials: "same-origin" })
            .then(function (response) {
                return response.ok ? response.json() : null;
            })
            .then(function (sizes) {
                if (!sizes) {
                    return;
                }
                for (var k = 0; k < ids.length; k += 1) {
                    var size = sizes[ids[k]];
                    if (!size || !isFinitePositive(size.width) || !isFinitePositive(size.height)) {
                        continue;
                    }
                    var targets = framesById[ids[k]];
                    for (var m = 0; m < targets.length; m += 1) {
                        applySize(targets[m], size.width, size.height);
                    }
                }
            })
            .catch(function () {
                // 조회 실패 시 16:9 기본 프레임을 유지한다.
            });
    }

    function initYoutubeSizes() {
        if (typeof window.fetch !== "function") {
            return;
        }
        var frames = document.querySelectorAll(YOUTUBE_SELECTOR);
        var framesById = {};
        var ids = [];
        for (var j = 0; j < frames.length; j += 1) {
            var videoId = youtubeVideoId(frames[j]);
            if (!videoId) {
                continue;
            }
            if (!framesById[videoId]) {
                framesById[videoId] = [];
                ids.push(videoId);
            }
            framesById[videoId].push(frames[j]);
        }
        // 서버가 요청당 12개까지만 받으므로 초과분은 배치로 나눠 조회한다.
        for (var n = 0; n < ids.length; n += YOUTUBE_SIZE_BATCH) {
            fetchYoutubeSizes(ids.slice(n, n + YOUTUBE_SIZE_BATCH), framesById);
        }
    }

    initYoutubeSizes();

    // --- X(트위터) 임베드: 테마 일치, 실제 높이 적용, 무응답 폴백 ---
    // platform.twitter.com은 성공 렌더링 시 twttr.private.resize 메시지를 보낸다(실측).
    // 유효 resize 수신이 곧 성공 신호이고, 일정 시간 무응답이면 공통 오류 상태로
    // 접는다. 늦게 신호가 오면 오류 상태를 되돌린다(복구 허용).

    function twitterFrames() {
        return document.querySelectorAll(TWITTER_SELECTOR);
    }

    function currentTheme() {
        return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
    }

    function ensureTwitterTheme(iframe, theme) {
        var src = iframe.getAttribute("src") || "";
        var desired = "theme=" + theme;
        if (src.indexOf("theme=") === -1) {
            if (theme === "light") {
                return; // 기본 테마가 light이므로 재로드하지 않는다.
            }
            iframe.setAttribute("src", src + (src.indexOf("?") === -1 ? "?" : "&") + desired);
            resetTwitterState(iframe);
            return;
        }
        if (src.indexOf(desired) !== -1) {
            return; // 이미 일치하면 재로드하지 않는다.
        }
        iframe.setAttribute("src", src.replace(/theme=(dark|light)/, desired));
        resetTwitterState(iframe);
    }

    function twitterWrapper(iframe) {
        var parent = iframe.parentElement;
        return parent && parent.classList && parent.classList.contains("embed-card") ? parent : null;
    }

    function clearTwitterError(iframe) {
        var wrapper = twitterWrapper(iframe);
        if (!wrapper) {
            return;
        }
        wrapper.classList.remove("is-embed-timeout");
        var status = wrapper.querySelector(".embed-card-status");
        if (status) {
            status.parentNode.removeChild(status);
        }
    }

    function markTwitterError(iframe) {
        var wrapper = twitterWrapper(iframe);
        if (!wrapper || wrapper.classList.contains("is-embed-timeout")) {
            return;
        }
        wrapper.classList.add("is-embed-timeout");
        var status = document.createElement("p");
        status.className = "embed-card-status";
        status.setAttribute("aria-live", "polite");
        status.textContent = TWITTER_ERROR_TEXT;
        wrapper.appendChild(status);
    }

    function armTwitterWatchdog(iframe) {
        if (iframe.__twitterWatchdog) {
            clearTimeout(iframe.__twitterWatchdog);
        }
        iframe.__twitterWatchdog = setTimeout(function () {
            if (iframe.dataset.twitterState !== "sized" && document.contains(iframe)) {
                markTwitterError(iframe);
            }
        }, TWITTER_TIMEOUT_MS);
    }

    function resetTwitterState(iframe) {
        delete iframe.dataset.twitterState;
        clearTwitterError(iframe);
        if (iframe.__twitterWatchdog) {
            clearTimeout(iframe.__twitterWatchdog);
            iframe.__twitterWatchdog = null;
        }
    }

    function applyTwitterHeight(iframe, height) {
        var clamped = Math.min(Math.max(Math.round(height), TWITTER_MIN_HEIGHT), TWITTER_MAX_HEIGHT);
        iframe.dataset.twitterState = "sized";
        iframe.style.height = clamped + "px";
        clearTwitterError(iframe);
    }

    window.addEventListener("message", function (event) {
        if (event.origin !== TWITTER_ORIGIN || !event.source) {
            return;
        }
        var data = event.data;
        var embed = data && typeof data === "object" ? data["twttr.embed"] : null;
        if (!embed || embed.method !== "twttr.private.resize") {
            return;
        }
        var params = embed.params && embed.params[0];
        var height = params ? params.height : null;
        if (!isFinitePositive(height)) {
            return;
        }
        var frames = twitterFrames();
        for (var t = 0; t < frames.length; t += 1) {
            if (frames[t].contentWindow && frames[t].contentWindow === event.source) {
                applyTwitterHeight(frames[t], height);
                return;
            }
        }
    });

    function initTwitterEmbeds() {
        var frames = twitterFrames();
        if (!frames.length) {
            return;
        }
        var theme = currentTheme();
        for (var t = 0; t < frames.length; t += 1) {
            // lazy 로딩 iframe은 뷰포트에 접근하기 전까지 로드되지 않으므로,
            // 워치독은 실제 load 이후에만 무장한다(화면 밖 임베드 오탐 방지).
            (function (iframe) {
                iframe.addEventListener("load", function () {
                    if (iframe.dataset.twitterState !== "sized") {
                        armTwitterWatchdog(iframe);
                    }
                });
            }(frames[t]));
            ensureTwitterTheme(frames[t], theme);
        }
        // 테마 전환 시 X iframe만 필요한 경우 재로드한다. 포커스·스크롤은 건드리지 않는다.
        if (typeof MutationObserver === "function") {
            new MutationObserver(function () {
                var nextTheme = currentTheme();
                var current = twitterFrames();
                for (var m = 0; m < current.length; m += 1) {
                    ensureTwitterTheme(current[m], nextTheme);
                }
            }).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
        }
    }

    initTwitterEmbeds();
}());
