(function (root) {
    "use strict";

    // 글 본문 이미지를 눌러 크게 본다. 이미지 차단 설정이 실제로 보여 준 이미지만 대상이며,
    // 숨겨진 이미지의 주소를 직접 불러오지 않는다.
    var SWIPE_MIN_PX = 48;
    var LOADING_DELAY_MS = 300;
    var IMAGE_SELECTOR = "img.body-image[data-body-image-src]";

    // 화면에 실제로 보이는 본문 이미지인지. 링크 안 이미지는 링크 이동을 그대로 둔다.
    function isViewable(image) {
        if (!image || image.hidden || !image.getAttribute("src")) {
            return false;
        }
        if (image.closest("a, [hidden]")) {
            return false;
        }
        return image.getClientRects().length > 0;
    }

    function stepIndex(current, length, delta) {
        var next = current + delta;
        return next >= 0 && next < length ? next : current;
    }

    function isSwipe(dx, dy) {
        return Math.abs(dx) >= SWIPE_MIN_PX && Math.abs(dx) > Math.abs(dy) * 1.5;
    }

    root.MirrorImageViewer = { isViewable: isViewable, stepIndex: stepIndex, isSwipe: isSwipe };

    var document = root.document;
    if (!document || typeof document.querySelector !== "function") {
        return;
    }

    var state = {
        body: null,
        dialog: null,
        images: [],
        index: -1,
        trigger: null,
        token: 0,
        loadingTimer: null,
        pointer: null
    };

    function viewableImages() {
        return Array.prototype.filter.call(state.body.querySelectorAll(IMAGE_SELECTOR), isViewable);
    }

    // 보이는 이미지에만 키보드 진입과 버튼 이름을 준다.
    function syncTriggers() {
        Array.prototype.forEach.call(state.body.querySelectorAll(IMAGE_SELECTOR), function (image) {
            if (isViewable(image)) {
                if (image.getAttribute("data-image-viewer") !== "true") {
                    image.setAttribute("data-image-viewer", "true");
                    image.setAttribute("tabindex", "0");
                    image.setAttribute("role", "button");
                    image.setAttribute("aria-haspopup", "dialog");
                    var alt = String(image.getAttribute("alt") || "").trim();
                    image.setAttribute("aria-label", alt ? "이미지 크게 보기: " + alt : "이미지 크게 보기");
                }
            } else if (image.getAttribute("data-image-viewer") === "true") {
                ["data-image-viewer", "tabindex", "role", "aria-haspopup", "aria-label"].forEach(function (name) {
                    image.removeAttribute(name);
                });
            }
        });
    }

    function svgIcon(path) {
        return '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="' + path + '"/></svg>';
    }

    function buildDialog() {
        var dialog = document.createElement("dialog");
        dialog.className = "image-viewer";
        dialog.setAttribute("aria-labelledby", "image-viewer-title");
        dialog.innerHTML =
            '<div class="image-viewer-bar">' +
                '<h2 class="image-viewer-title" id="image-viewer-title">이미지 <span class="image-viewer-count"></span></h2>' +
                '<a class="image-viewer-original" target="_blank" rel="noopener noreferrer">원본 열기</a>' +
                '<button class="image-viewer-close" type="button" aria-label="닫기">' + svgIcon("M6 6l12 12M18 6L6 18") + '</button>' +
            '</div>' +
            '<div class="image-viewer-stage">' +
                '<img class="image-viewer-image" alt="" decoding="async">' +
                '<div class="image-viewer-message" hidden>' +
                    '<p class="image-viewer-message-text"></p>' +
                    '<button class="image-viewer-retry" type="button" hidden>다시 시도</button>' +
                '</div>' +
            '</div>' +
            '<button class="image-viewer-nav is-prev" type="button" aria-label="이전 이미지">' + svgIcon("M15 5l-7 7 7 7") + '</button>' +
            '<button class="image-viewer-nav is-next" type="button" aria-label="다음 이미지">' + svgIcon("M9 5l7 7-7 7") + '</button>' +
            '<p class="sr-only image-viewer-status" role="status" aria-live="polite"></p>';
        document.body.appendChild(dialog);

        dialog.querySelector(".image-viewer-close").addEventListener("click", close);
        dialog.querySelector(".is-prev").addEventListener("click", function () { step(-1); });
        dialog.querySelector(".is-next").addEventListener("click", function () { step(1); });
        dialog.querySelector(".image-viewer-retry").addEventListener("click", function () { show(state.index, true); });
        dialog.addEventListener("close", afterClose);
        dialog.addEventListener("keydown", function (event) {
            if (event.altKey || event.ctrlKey || event.metaKey) {
                return;
            }
            if (event.key === "ArrowLeft" || event.key === "ArrowRight") {
                event.preventDefault();
                step(event.key === "ArrowLeft" ? -1 : 1);
            }
        });
        // 이미지·버튼이 아닌 빈 곳을 누르면 닫는다.
        dialog.addEventListener("click", function (event) {
            var target = event.target;
            if (target === dialog || target.classList.contains("image-viewer-stage")) {
                close();
            }
        });
        var stage = dialog.querySelector(".image-viewer-stage");
        stage.addEventListener("pointerdown", function (event) {
            if (event.pointerType === "mouse") {
                return;
            }
            state.pointer = { id: event.pointerId, x: event.clientX, y: event.clientY };
        });
        stage.addEventListener("pointerup", function (event) {
            var start = state.pointer;
            state.pointer = null;
            if (!start || start.id !== event.pointerId) {
                return;
            }
            var dx = event.clientX - start.x;
            if (isSwipe(dx, event.clientY - start.y)) {
                step(dx < 0 ? 1 : -1);
            }
        });
        stage.addEventListener("pointercancel", function () {
            state.pointer = null;
        });
        var viewerImage = dialog.querySelector(".image-viewer-image");
        viewerImage.addEventListener("load", function () {
            if (viewerImage.getAttribute("data-token") === String(state.token)) {
                setMessage("", false);
            }
        });
        viewerImage.addEventListener("error", function () {
            if (viewerImage.getAttribute("data-token") === String(state.token) && viewerImage.getAttribute("src")) {
                setMessage("이미지를 불러오지 못했어요.", true);
            }
        });
        return dialog;
    }

    function setMessage(text, canRetry) {
        clearTimeout(state.loadingTimer);
        state.loadingTimer = null;
        var dialog = state.dialog;
        var box = dialog.querySelector(".image-viewer-message");
        box.hidden = !text;
        dialog.querySelector(".image-viewer-message-text").textContent = text;
        dialog.querySelector(".image-viewer-retry").hidden = !canRetry;
        dialog.querySelector(".image-viewer-image").classList.toggle("is-failed", !!canRetry);
    }

    function show(index, retry) {
        var dialog = state.dialog;
        var source = state.images[index];
        var url = source.getAttribute("src");
        var viewerImage = dialog.querySelector(".image-viewer-image");
        state.index = index;
        state.token += 1;
        var token = state.token;
        setMessage("", false);
        viewerImage.setAttribute("data-token", String(token));
        viewerImage.alt = String(source.getAttribute("alt") || "").trim() || "본문 이미지 " + (index + 1);
        if (retry || viewerImage.getAttribute("src") !== url) {
            viewerImage.removeAttribute("src");
            viewerImage.setAttribute("src", url);
        }
        // 이미 받아 둔 이미지면 안내 없이 바로 보인다.
        if (!(viewerImage.complete && viewerImage.naturalWidth > 0)) {
            state.loadingTimer = setTimeout(function () {
                if (token === state.token && !(viewerImage.complete && viewerImage.naturalWidth > 0)) {
                    setMessage("불러오는 중…", false);
                }
            }, LOADING_DELAY_MS);
        }
        dialog.querySelector(".image-viewer-original").href = url;
        var total = state.images.length;
        dialog.querySelector(".image-viewer-count").textContent = (index + 1) + " / " + total;
        dialog.querySelector(".is-prev").disabled = index <= 0;
        dialog.querySelector(".is-next").disabled = index >= total - 1;
        dialog.querySelector(".is-prev").hidden = total < 2;
        dialog.querySelector(".is-next").hidden = total < 2;
        dialog.querySelector(".image-viewer-status").textContent = total > 1 ? total + "장 중 " + (index + 1) + "번째 이미지" : "";
        // 끝에 닿아 누르던 이동 버튼이 사라지면 포커스를 남은 버튼으로 옮긴다.
        var active = document.activeElement;
        if (active && active.classList && active.classList.contains("image-viewer-nav") && active.disabled) {
            var other = dialog.querySelector(active.classList.contains("is-prev") ? ".is-next" : ".is-prev");
            (other && !other.disabled && !other.hidden ? other : dialog.querySelector(".image-viewer-close")).focus();
        }
    }

    function open(image) {
        var images = viewableImages();
        var index = images.indexOf(image);
        if (index < 0) {
            return;
        }
        state.dialog = state.dialog || buildDialog();
        state.images = images;
        state.trigger = image;
        show(index, false);
        if (!state.dialog.open) {
            state.dialog.showModal();
        }
        state.dialog.querySelector(".image-viewer-close").focus();
    }

    // 이동할 때마다 차단 상태를 다시 본다. 보던 이미지가 숨겨졌으면 닫는다.
    function step(delta) {
        var current = state.images[state.index];
        var images = viewableImages();
        var index = images.indexOf(current);
        if (index < 0) {
            close();
            return;
        }
        state.images = images;
        var next = stepIndex(index, images.length, delta);
        // 끝에서는 지금 이미지의 로딩·오류 상태를 그대로 둔다.
        if (next === index) {
            state.index = index;
            return;
        }
        state.trigger = images[next];
        show(next, false);
    }

    function close() {
        if (state.dialog && state.dialog.open) {
            state.dialog.close();
        }
    }

    function afterClose() {
        clearTimeout(state.loadingTimer);
        state.loadingTimer = null;
        state.token += 1;
        var viewerImage = state.dialog.querySelector(".image-viewer-image");
        viewerImage.removeAttribute("src");
        state.dialog.querySelector(".image-viewer-original").removeAttribute("href");
        var trigger = state.trigger;
        state.images = [];
        state.index = -1;
        state.trigger = null;
        if (trigger && isViewable(trigger)) {
            trigger.focus();
            return;
        }
        // 보던 이미지가 다시 차단됐으면 그 이미지의 보기 버튼, 없으면 글 제목으로 돌아간다.
        var id = trigger && trigger.getAttribute("id");
        var toggle = id ? document.querySelector('.body-media-item-toggle[aria-controls="' + id + '"]') : null;
        if (toggle && !toggle.hidden) {
            toggle.focus();
            return;
        }
        var heading = document.querySelector(".article-head h1");
        if (heading) {
            heading.setAttribute("tabindex", "-1");
            heading.focus();
        }
    }

    // 차단 설정이 바뀌면 진입 표시를 고치고, 열린 이미지가 숨겨졌으면 닫는다.
    function onBodyMutation() {
        syncTriggers();
        if (state.dialog && state.dialog.open && !isViewable(state.images[state.index])) {
            close();
        }
    }

    function triggerFrom(target) {
        var image = target && target.closest ? target.closest(IMAGE_SELECTOR) : null;
        return image && state.body.contains(image) && isViewable(image) ? image : null;
    }

    function boot() {
        state.body = document.querySelector("#article-body.article-body");
        if (!state.body || typeof root.HTMLDialogElement !== "function") {
            return;
        }
        syncTriggers();
        state.body.addEventListener("click", function (event) {
            if (event.defaultPrevented || event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) {
                return;
            }
            var image = triggerFrom(event.target);
            if (image) {
                event.preventDefault();
                open(image);
            }
        });
        state.body.addEventListener("keydown", function (event) {
            if (event.key !== "Enter" && event.key !== " ") {
                return;
            }
            var image = triggerFrom(event.target);
            if (image && event.target === image) {
                event.preventDefault();
                open(image);
            }
        });
        if (typeof root.MutationObserver === "function") {
            new root.MutationObserver(onBodyMutation).observe(state.body, {
                subtree: true,
                attributes: true,
                attributeFilter: ["src", "hidden"]
            });
        }
        // 이미지 차단 설정은 본문 바깥 속성으로도 바뀌므로 함께 본다.
        if (typeof root.MutationObserver === "function") {
            new root.MutationObserver(onBodyMutation).observe(document.documentElement, {
                attributes: true,
                attributeFilter: ["data-media-block-mode"]
            });
        }
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", boot, { once: true });
    } else {
        boot();
    }
})(typeof window !== "undefined" ? window : globalThis);
