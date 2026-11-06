(function () {
    "use strict";

    // 디시 본문 이미지가 실패하면 페이지를 새로 고치지 않고 같은 주소로 다시 받는다.
    // 서버가 이미 한 번 바로 재시도하므로 브라우저는 늦춘 자동 재시도 두 번 뒤에
    // 이미지별 "다시 불러오기" 버튼만 남긴다. 숨겨진(차단된) 이미지는 절대 불러오지 않는다.
    var RETRY_DELAYS_MS = [1500, 4000];
    var RETRY_JITTER_MS = 500;
    var MAX_ACTIVE_REQUESTS = 2;
    // 서버의 원본 요청 두 번(MIRROR_HTTP_TIMEOUT 기본 20초씩)보다 길게 기다린다.
    var REQUEST_WATCHDOG_MS = 45000;
    var FAILED_TEXT = "이미지를 불러오지 못했어요.";
    var RETRYING_TEXT = "이미지를 다시 불러오는 중이에요.";
    var BUTTON_TEXT = "다시 불러오기";
    // 이 모듈이 요청을 끊어 src가 비었다는 표시. read_state.js는 이 표시가 있으면 자동으로
    // 다시 채우지 않는다(같은 이름을 그쪽 RETRY_SUSPENDED_ATTRIBUTE가 쓴다).
    var SUSPENDED_ATTRIBUTE = "data-body-image-retry-suspended";

    var states = new WeakMap();
    var tracked = new Set();
    var queue = [];
    var active = 0;
    var pageActive = true;
    var rowId = 0;

    function sourceOf(image) {
        if (!image || image.tagName !== "IMG" || !image.classList.contains("body-image") || !image.closest("#article-body")) {
            return null;
        }
        var value = image.getAttribute("data-body-image-src");
        return value && value.indexOf("/media?") === 0 ? value : null;
    }

    function isLoaded(image) {
        return image.complete && image.naturalWidth > 0;
    }

    function setText(node, text) {
        if (node.textContent !== text) {
            node.textContent = text;
        }
    }

    function setHidden(node, hidden) {
        if (node.hidden !== hidden) {
            node.hidden = hidden;
        }
    }

    // 버튼은 원본 링크 안에 넣지 않고 이미지(또는 감싼 링크) 바로 뒤에 둔다.
    function createRow(state) {
        var image = state.image;
        var row = document.createElement("span");
        row.className = "body-image-retry";
        var text = document.createElement("span");
        rowId += 1;
        text.id = "body-image-retry-" + rowId;
        text.className = "body-image-retry-text";
        text.setAttribute("role", "status");
        var button = document.createElement("button");
        button.type = "button";
        button.className = "body-image-retry-button";
        button.setAttribute("aria-describedby", text.id);
        button.textContent = BUTTON_TEXT;
        button.addEventListener("click", function () {
            retryNow(state);
        });
        row.appendChild(text);
        row.appendChild(button);
        var anchor = image.closest("a");
        var after = anchor || image;
        after.parentNode.insertBefore(row, after.nextSibling);
        state.row = row;
        state.text = text;
        state.button = button;
    }

    function render(state) {
        var image = state.image;
        var visible = state.phase !== "idle" && image.isConnected && !image.closest("[hidden]");
        if (!state.row) {
            if (!visible) {
                return;
            }
            createRow(state);
        }
        setHidden(state.row, !visible);
        if (!visible) {
            return;
        }
        var busy = state.phase !== "failed";
        state.row.setAttribute("data-state", busy ? (state.manual ? "loading" : "retrying") : "failed");
        setText(state.text, busy ? RETRYING_TEXT : FAILED_TEXT);
        // 자동 재시도 중에는 버튼을 숨기고, 직접 누른 뒤에는 포커스를 지키도록 aria-disabled만 건다.
        setHidden(state.button, busy && !state.manual);
        state.button.setAttribute("aria-disabled", busy ? "true" : "false");
    }

    function releaseSlot(state) {
        clearTimeout(state.watchdog);
        state.watchdog = null;
        active -= 1;
    }

    // 이 모듈이 보낸 요청이 아직 이 이미지에서 진행 중인지: 바뀐 주소나 이미 받은 이미지는 건드리지 않는다.
    function ownsRequest(state) {
        return state.phase === "inflight" && state.image.getAttribute("src") === state.url && !isLoaded(state.image);
    }

    // 대기·진행 중인 작업을 모두 거두고, 차지한 요청 자리는 바로 돌려준다.
    // cancelOwned이면 자리를 넘기기 전에 이 모듈의 요청부터 끊고, 자동 재수화를 보류시킨다.
    function stop(state, nextPhase, cancelOwned) {
        clearTimeout(state.timer);
        state.timer = null;
        var index = queue.indexOf(state);
        if (index !== -1) {
            queue.splice(index, 1);
        }
        var wasActive = state.phase === "inflight";
        if (wasActive && cancelOwned && ownsRequest(state)) {
            state.image.setAttribute(SUSPENDED_ATTRIBUTE, "");
            state.image.removeAttribute("src");
        }
        if (wasActive) {
            releaseSlot(state);
        }
        state.phase = nextPhase;
        render(state);
        if (wasActive) {
            drain();
        }
    }

    // 끊은 요청은 직접 다시 불러오기로만 이어 간다. 이미 받은 이미지는 그대로 둔다.
    function relinquish(state) {
        stop(state, ownsRequest(state) ? "failed" : "idle", true);
    }

    function forget(state) {
        stop(state, "idle", true);
        state.image.removeAttribute(SUSPENDED_ATTRIBUTE);
        if (state.row) {
            state.row.remove();
        }
        tracked.delete(state);
        if (states.get(state.image) === state) {
            states.delete(state.image);
        }
    }

    // 다른 코드가 바꾼 상태를 따라간다: 지워진 이미지·바뀐 주소는 잊고, 숨겨지면 멈춘다.
    function reconcile(state) {
        var image = state.image;
        var src = image.getAttribute("src");
        if (!image.isConnected || sourceOf(image) !== state.url || (src !== null && src !== state.url)) {
            forget(state);
            return;
        }
        if (image.hidden) {
            // 차단 설정이 숨겼다. 다시 보여 줄 때는 read_state.js가 새로 받도록 보류를 푼다.
            if (state.phase !== "idle") {
                stop(state, "idle", true);
            }
            if (image.getAttribute(SUSPENDED_ATTRIBUTE) !== null) {
                image.removeAttribute(SUSPENDED_ATTRIBUTE);
            }
            return;
        }
        if (image.closest("[hidden]")) {
            if (state.phase === "inflight") {
                relinquish(state);
            } else if (state.phase === "waiting" || state.phase === "queued") {
                // src를 끊어 둔 이미지는 다시 보여도 새 이벤트가 없으니 직접 다시 불러오기를 남긴다.
                stop(state, image.getAttribute(SUSPENDED_ATTRIBUTE) !== null ? "failed" : "idle", false);
            } else {
                render(state);
            }
            return;
        }
        if (state.phase === "failed") {
            render(state);
        } else if (state.phase === "idle" && src === state.url && image.complete && !image.naturalWidth) {
            // 감싼 요소만 숨겨졌다 다시 보이면 깨진 이미지에 새 이벤트가 오지 않는다.
            fail(state, false);
        }
    }

    function fail(state, afterManual) {
        if (!afterManual && state.retries < RETRY_DELAYS_MS.length) {
            state.manual = false;
            state.phase = "waiting";
            state.timer = setTimeout(function () {
                state.timer = null;
                enqueue(state);
            }, RETRY_DELAYS_MS[state.retries] + Math.random() * RETRY_JITTER_MS);
        } else {
            state.phase = "failed";
        }
        render(state);
    }

    function enqueue(state) {
        state.phase = "queued";
        render(state);
        queue.push(state);
        drain();
    }

    function retryNow(state) {
        if (state.phase !== "failed" || !pageActive) {
            return;
        }
        state.manual = true;
        enqueue(state);
    }

    // 보내기 직전에 차단·삭제·주소 변경·이미 받은 상태를 다시 확인한다.
    function ready(state) {
        reconcile(state);
        if (!tracked.has(state) || state.phase !== "queued") {
            return false;
        }
        if (isLoaded(state.image)) {
            stop(state, "idle", false);
            return false;
        }
        return true;
    }

    function drain() {
        while (pageActive && active < MAX_ACTIVE_REQUESTS && queue.length) {
            var state = queue.shift();
            if (ready(state)) {
                dispatch(state);
            }
        }
    }

    function dispatch(state) {
        active += 1;
        state.phase = "inflight";
        if (!state.manual) {
            state.retries += 1;
        }
        state.watchdog = setTimeout(function () {
            stalled(state);
        }, REQUEST_WATCHDOG_MS);
        render(state);
        state.image.removeAttribute(SUSPENDED_ATTRIBUTE);
        // 같은 주소를 다시 지정한다. 실패 응답은 no-store라 새 요청이 나간다.
        state.image.setAttribute("src", state.url);
    }

    // 끝나지 않는 요청은 끊고 자리를 돌려준 뒤 직접 다시 불러오게 한다.
    function stalled(state) {
        if (state.phase === "inflight") {
            relinquish(state);
        }
    }

    function onError(event) {
        var image = event.target;
        var url = sourceOf(image);
        if (!url || image.getAttribute("src") !== url || image.closest("[hidden]")) {
            return;
        }
        var state = states.get(image);
        if (state && state.url !== url) {
            forget(state);
            state = null;
        }
        if (!state) {
            state = { image: image, url: url, phase: "idle", retries: 0, manual: false,
                timer: null, watchdog: null, row: null, text: null, button: null };
            states.set(image, state);
            tracked.add(state);
        }
        // 기다리는 동안이나 실패를 알린 뒤에 겹쳐 온 오류는 재시도를 더 만들지 않는다.
        // 차단을 풀어 다시 받는 경우는 숨길 때 idle로 돌아가므로 여기서 걸리지 않는다.
        if (state.phase === "waiting" || state.phase === "queued" || state.phase === "failed") {
            return;
        }
        var afterManual = state.phase === "inflight" && state.manual;
        if (state.phase === "inflight") {
            releaseSlot(state);
            state.phase = "idle";
        }
        fail(state, afterManual);
        drain();
    }

    function onLoad(event) {
        var state = states.get(event.target);
        if (state && event.target.getAttribute("src") === state.url && state.phase !== "idle") {
            stop(state, "idle", false);
        }
    }

    // 캡처 단계로 듣고, 본문 이미지에 처음 주소가 붙기(read_state.js) 전에 설치한다.
    document.addEventListener("error", onError, true);
    document.addEventListener("load", onLoad, true);

    var articleBody = document.getElementById("article-body");
    if (articleBody) {
        new MutationObserver(function () {
            tracked.forEach(reconcile);
        }).observe(articleBody, {
            subtree: true,
            childList: true,
            attributes: true,
            attributeFilter: ["src", "hidden", "data-body-image-src"]
        });
    }

    // 떠나는 페이지에서는 대기열과 요청을 모두 거두고, 돌아오면 직접 다시 불러오게 한다.
    window.addEventListener("pagehide", function () {
        pageActive = false;
        tracked.forEach(function (state) {
            if (state.phase === "inflight") {
                relinquish(state);
            } else if (state.phase === "waiting" || state.phase === "queued") {
                stop(state, "failed", false);
            }
        });
    });
    window.addEventListener("pageshow", function () {
        pageActive = true;
        drain();
    });
})();
