(function (root) {
    "use strict";

    // 글쓰기·추천이 함께 쓰는 요청·캡챠 도구. 원본 응답을 자동으로 다시 보내지 않는다.
    var CAPTCHA_KEY_PATTERN = /^[0-9a-f]{16}$/;

    // 서버(Python len)와 같게 코드 포인트 단위로 센다.
    function charLength(value) {
        return Array.from(String(value == null ? "" : value)).length;
    }

    function newCaptchaKey(cryptoObject) {
        var source = cryptoObject || root.crypto;
        var bytes = new Uint8Array(8);
        source.getRandomValues(bytes);
        return Array.prototype.map.call(bytes, function (byte) {
            return (byte < 16 ? "0" : "") + byte.toString(16);
        }).join("");
    }

    function captchaImageUrl(kind, board, key) {
        var params = new URLSearchParams();
        params.set("kind", kind);
        params.set("board", board);
        params.set("key", key);
        return "/write/captcha?" + params.toString();
    }

    // 서버가 준 이동 주소는 같은 사이트의 경로만 따른다.
    function safePath(value) {
        var text = String(value || "");
        if (!/^\/(?![\/\\])/.test(text)) {
            return "";
        }
        return text;
    }

    // 응답을 화면 동작 하나로 바꾼다. payload가 없으면(통신 실패·JSON 아님) 결과를 모른다고 본다.
    function interpret(status, payload) {
        if (!payload || typeof payload !== "object") {
            return { action: "unknown" };
        }
        if (payload.ok === true) {
            return { action: "success", payload: payload };
        }
        var code = String(payload.code || "");
        // 디시에 보냈지만 결과를 모르는 경우. 다시 보내면 중복될 수 있다.
        if (code === "uncertain") {
            return { action: "unknown", payload: payload };
        }
        if (code === "captcha_required" && payload.captcha && CAPTCHA_KEY_PATTERN.test(String(payload.captcha.key || ""))) {
            return { action: "captcha", payload: payload };
        }
        if (code === "invalid" || code === "rejected" || code === "locked" || code === "unavailable") {
            return { action: code, payload: payload };
        }
        if (code === "retry") {
            var seconds = Math.ceil(Number(payload.retry_after));
            return { action: "retry", payload: payload, seconds: isFinite(seconds) && seconds > 0 ? Math.min(seconds, 600) : 10 };
        }
        return { action: status >= 500 ? "unavailable" : "unknown", payload: payload };
    }

    function unlockUrl() {
        var next = root.location ? root.location.pathname + root.location.search : "/";
        return "/write/unlock?" + new URLSearchParams({ next: next }).toString();
    }

    root.MirrorDcPost = {
        charLength: charLength,
        newCaptchaKey: newCaptchaKey,
        captchaImageUrl: captchaImageUrl,
        safePath: safePath,
        interpret: interpret,
        unlockUrl: unlockUrl
    };

    var document = root.document;
    if (!document || typeof root.fetch !== "function") {
        return;
    }

    function postJson(url, body, timeoutMs) {
        var controller = typeof AbortController === "function" ? new AbortController() : null;
        var timer = setTimeout(function () {
            if (controller) {
                controller.abort();
            }
        }, timeoutMs || 45000);
        return root.fetch(url, {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json", "Accept": "application/json" },
            body: JSON.stringify(body),
            signal: controller ? controller.signal : undefined
        }).then(function (response) {
            return response.json().then(function (payload) {
                return { status: response.status, payload: payload };
            }, function () {
                return { status: response.status, payload: null };
            });
        }, function () {
            return { status: 0, payload: null };
        }).then(function (result) {
            clearTimeout(timer);
            return result;
        });
    }

    // 사진이 있는 글은 XHR로 보내 올리는 진행률을 보여 준다. 다 올린 뒤에는 서버 답만 기다린다.
    // 프록시가 본문을 받기 전에 거절한 413은 디시에 보내지 않은 것이 확실하므로 입력 오류로 바꾼다.
    function postForm(url, formData, options) {
        var settings = options || {};
        return new Promise(function (resolve) {
            var xhr = new XMLHttpRequest();
            var timer = null;
            var done = false;
            var uploaded = false;
            var stallMs = settings.stallTimeoutMs || 60000;
            // 올리는 동안 진행이 멈추면 끊는다. 다 올리기 전이라 디시에는 가지 않았다.
            function watchStall() {
                clearTimeout(timer);
                timer = setTimeout(function () {
                    xhr.abort();
                }, stallMs);
            }
            function finish(result) {
                if (done) {
                    return;
                }
                done = true;
                clearTimeout(timer);
                resolve(result);
            }
            xhr.open("POST", url);
            xhr.withCredentials = true;
            xhr.setRequestHeader("Accept", "application/json");
            if (xhr.upload) {
                xhr.upload.addEventListener("progress", function (event) {
                    if (!uploaded) {
                        watchStall();
                    }
                    if (event.lengthComputable && typeof settings.onProgress === "function") {
                        settings.onProgress(event.loaded / event.total);
                    }
                });
                xhr.upload.addEventListener("load", function () {
                    uploaded = true;
                    if (typeof settings.onUploaded === "function") {
                        settings.onUploaded();
                    }
                    clearTimeout(timer);
                    timer = setTimeout(function () {
                        xhr.abort();
                    }, settings.responseTimeoutMs || 70000);
                });
            }
            xhr.addEventListener("load", function () {
                var payload = null;
                try {
                    payload = JSON.parse(xhr.responseText);
                } catch (err) {
                    payload = null;
                }
                if (xhr.status === 413 && (!payload || typeof payload !== "object")) {
                    payload = { ok: false, code: "invalid", field: "images", message: "사진 용량이 너무 커서 서버가 받지 않았어요. 사진을 줄이거나 빼 주세요." };
                }
                finish({ status: xhr.status, payload: payload });
            });
            ["error", "abort", "timeout"].forEach(function (name) {
                xhr.addEventListener(name, function () {
                    // 다 올리기 전에 끊겼으면 서버가 요청 전체를 받지 못했으니 디시에도 가지 않았다.
                    finish({ status: 0, payload: null, notSent: !uploaded });
                });
            });
            xhr.send(formData);
            watchStall();
        });
    }

    // 캡챠 칸 하나를 다룬다. 서버가 준 문제를 보여 주고, 새로고침은 새 키로 이미지만 다시 받는다.
    function createCaptcha(prefix, kind, board) {
        var box = document.getElementById(prefix);
        if (!box) {
            return null;
        }
        var image = document.getElementById(prefix + "-image");
        var input = document.getElementById(prefix + "-code");
        var refresh = document.getElementById(prefix + "-refresh");
        var error = document.getElementById(prefix + "-error");
        var key = "";

        function load(nextKey, url) {
            key = nextKey;
            input.value = "";
            error.hidden = true;
            image.hidden = false;
            image.src = url || captchaImageUrl(kind, board, nextKey);
        }

        image.addEventListener("error", function () {
            if (!box.hidden) {
                error.hidden = false;
                image.hidden = true;
            }
        });
        refresh.addEventListener("click", function () {
            load(newCaptchaKey());
            input.focus();
        });

        return {
            show: function (captcha) {
                var url = safePath(captcha.image_url);
                load(String(captcha.key), url && url.indexOf("/write/captcha?") === 0 ? url : "");
                box.hidden = false;
                input.focus();
            },
            hide: function () {
                box.hidden = true;
                key = "";
                input.value = "";
                image.removeAttribute("src");
            },
            // 보이는 문제가 있을 때만 답을 붙인다. 이미지를 못 받았으면 답을 보내지 않는다.
            answer: function () {
                if (box.hidden || !key) {
                    return null;
                }
                return { key: key, code: input.value.trim(), imageFailed: !error.hidden };
            },
            focus: function () {
                input.focus();
            },
            setDisabled: function (disabled) {
                input.disabled = disabled;
                refresh.disabled = disabled;
            }
        };
    }

    root.MirrorDcPost.postJson = postJson;
    root.MirrorDcPost.postForm = postForm;
    root.MirrorDcPost.createCaptcha = createCaptcha;
})(typeof window !== "undefined" ? window : globalThis);
