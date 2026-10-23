(function (root) {
    "use strict";

    // 상단 작성자 메뉴와 글·댓글 화면이 함께 싣는다. 두 번째로 실리면 아무것도 하지 않는다.
    if (root.MirrorDcPost) {
        return;
    }

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

    // extra: 추천 PC 보안 문자처럼 글 번호·갤러리 종류가 더 필요한 경우의 추가 값.
    function captchaImageUrl(kind, board, key, extra) {
        var params = new URLSearchParams();
        params.set("kind", kind);
        params.set("board", board);
        params.set("key", key);
        Object.keys(extra || {}).forEach(function (name) {
            params.set(name, extra[name]);
        });
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
        if (code === "visitor_check_required") {
            return { action: code, sent: false, payload: payload };
        }
        // 디시에 보냈지만 결과를 모르는 경우. 다시 보내면 중복될 수 있다.
        if (code === "uncertain") {
            return { action: "unknown", payload: payload };
        }
        if (code === "captcha_required" && payload.captcha && CAPTCHA_KEY_PATTERN.test(String(payload.captcha.key || ""))) {
            return { action: "captcha", payload: payload };
        }
        // 디시 계정 로그인이 필요해 보내지 않은 요청. 계정 화면에서 로그인한 뒤 다시 하면 된다.
        if (code === "invalid" || code === "rejected" || code === "locked" || code === "unavailable" || code === "login_needed") {
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

    // 상단 작성자 메뉴에서 마지막에 고른 값: "anonymous" 또는 "account:1"·"account:2". 글쓰기·댓글·추천이 함께 쓴다.
    // 계정이 하나뿐이던 때 저장한 "account"는 계정 1로 읽는다.
    var MODE_KEY = "mirror_write_mode_v1";
    var MODE_PATTERN = /^(anonymous|account:[12])$/;

    function normalizeMode(value) {
        var text = String(value == null ? "" : value);
        if (text === "account") {
            return "account:1";
        }
        return MODE_PATTERN.test(text) ? text : "anonymous";
    }

    function readMode(storage) {
        try {
            return storage ? normalizeMode(storage.getItem(MODE_KEY)) : "anonymous";
        } catch (err) {
            return "anonymous";
        }
    }

    function saveMode(storage, mode) {
        try {
            if (storage) {
                storage.setItem(MODE_KEY, normalizeMode(mode));
            }
        } catch (err) {
            // 기억하지 못해도 지금 고른 값으로 쓴다.
        }
    }

    // "account:N"이면 계정 번호, 아니면 빈 값.
    function modeAccount(mode) {
        var match = /^account:([12])$/.exec(String(mode || ""));
        return match ? match[1] : "";
    }

    // 기억한 값이 지금 고를 수 있는 값(modes)에 없으면(서버에서 그 계정을 뺐으면) 빈 값을 돌려준다.
    // 다른 계정으로 바꿔 보내지 않고, 작성자를 다시 고르게 한다.
    // 고를 값이 아예 없으면(계정을 모두 뺐으면) 유동만 남지만, 계정을 기억하고 있었다면 한 번 알리고 다시 누르게 한다.
    function pickMode(saved, modes) {
        if (!modes.length) {
            return modeAccount(saved) ? "" : "anonymous";
        }
        return modes.indexOf(saved) >= 0 ? saved : "";
    }

    // 작성자 이름. 닉네임이 같을 수 있어 계정 번호는 남기고, 로그인 아이디는 쓰지 않는다.
    // single: 서버에 계정이 하나뿐이면 번호 대신 "디시 계정"이라고 부른다.
    function authorLabel(mode, names, single) {
        var slot = modeAccount(mode);
        if (!slot) {
            return "유동";
        }
        var base = single ? "디시 계정" : "계정 " + slot;
        var name = names && typeof names[slot] === "string" ? names[slot].trim() : "";
        return name ? base + " · " + name : base;
    }

    // 상태 응답에서 계정 번호별 상태만 꺼낸다. 예전 응답(accounts 없음)은 계정 1로 본다.
    function accountList(status) {
        var result = {};
        if (!status || typeof status !== "object") {
            return result;
        }
        var accounts = status.accounts && typeof status.accounts === "object" ? status.accounts : { "1": status };
        ["1", "2"].forEach(function (slot) {
            if (accounts[slot] && typeof accounts[slot] === "object") {
                result[slot] = accounts[slot];
            }
        });
        return result;
    }

    // 입력한 글이 사라지지 않게 계정 화면은 새 탭으로 연다.
    function accountLink(payload) {
        return { href: safePath(payload && payload.account_url) || "/write/account", text: "새 탭에서 계정 화면 열기", newTab: true };
    }

    root.MirrorDcPost = {
        charLength: charLength,
        newCaptchaKey: newCaptchaKey,
        captchaImageUrl: captchaImageUrl,
        safePath: safePath,
        interpret: interpret,
        unlockUrl: unlockUrl,
        normalizeMode: normalizeMode,
        readMode: readMode,
        saveMode: saveMode,
        modeAccount: modeAccount,
        pickMode: pickMode,
        authorLabel: authorLabel,
        accountList: accountList,
        accountLink: accountLink,
        MODE_KEY: MODE_KEY
    };

    var document = root.document;

    // 상단 작성자 선택. 고를 수 있는 계정은 body의 data-author-slots에 있다(없으면 유동만).
    // 다른 탭에서 바꾸면 storage 이벤트로 따라간다. 저장에 실패해도 이 탭에서는 고른 값을 쓴다.
    var AUTHOR_EVENT = "mirror:author-change";
    var localMode = null;

    function modeStorage() {
        try {
            return root.localStorage;
        } catch (err) {
            return null;
        }
    }

    function authorModes() {
        var body = document && document.body;
        var raw = body && typeof body.getAttribute === "function" ? body.getAttribute("data-author-slots") : null;
        var slots = String(raw || "").split(/\s+/).filter(function (slot) {
            return slot === "1" || slot === "2";
        });
        return slots.length ? ["anonymous"].concat(slots.map(function (slot) {
            return "account:" + slot;
        })) : [];
    }

    function authorMode() {
        return pickMode(localMode !== null ? localMode : readMode(modeStorage()), authorModes());
    }

    function notifyAuthor() {
        if (document && typeof document.dispatchEvent === "function" && typeof root.CustomEvent === "function") {
            document.dispatchEvent(new root.CustomEvent(AUTHOR_EVENT));
        }
    }

    function setAuthorMode(mode) {
        localMode = normalizeMode(mode);
        saveMode(modeStorage(), localMode);
        notifyAuthor();
    }

    function onAuthorChange(listener) {
        if (document && typeof document.addEventListener === "function") {
            document.addEventListener(AUTHOR_EVENT, function () {
                listener(authorMode());
            });
        }
    }

    if (document && typeof root.addEventListener === "function") {
        root.addEventListener("storage", function (event) {
            if (event.key === MODE_KEY || event.key === null) {
                localMode = null;
                notifyAuthor();
            }
        });
    }

    root.MirrorDcPost.authorModes = authorModes;
    root.MirrorDcPost.authorMode = authorMode;
    root.MirrorDcPost.setAuthorMode = setAuthorMode;
    root.MirrorDcPost.onAuthorChange = onAuthorChange;
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
    function createCaptcha(prefix, kind, board, extra) {
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
            image.src = url || captchaImageUrl(kind, board, nextKey, extra);
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
            // 작성자에 따라 문자 종류가 바뀐다(유동 추천은 PC 문자, 계정 추천은 앱 문자). 새로고침도 이 값을 쓴다.
            setSource: function (nextKind, nextExtra) {
                kind = nextKind;
                extra = nextExtra;
            },
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

    // 계정 상태는 서버 메모리의 값만 읽는다(디시에 요청하지 않는다). 한 화면에서 한 번만 묻는다.
    var statusPromise = null;

    // fresh: 다른 탭에서 로그인했을 수 있어 다시 묻는다(상단 메뉴를 열 때).
    function accountStatus(fresh) {
        if (!statusPromise || fresh) {
            statusPromise = root.fetch("/write/account/status", { credentials: "same-origin", headers: { "Accept": "application/json" } }).then(function (response) {
                return response.ok ? response.json() : null;
            }).then(function (status) {
                return status && status.ok === true ? status : null;
            }, function () {
                return null;
            });
        }
        return statusPromise;
    }

    // 로그인한 계정의 디시 닉네임({계정 번호: 닉네임}). 작성자 이름에 붙인다.
    function authorNames(fresh) {
        return accountStatus(fresh).then(function (status) {
            var accounts = accountList(status);
            var names = {};
            Object.keys(accounts).forEach(function (slot) {
                var item = accounts[slot];
                var name = item.logged_in === true ? String(item.name || "").trim() : "";
                if (name) {
                    names[slot] = name;
                }
            });
            return names;
        });
    }

    root.MirrorDcPost.accountStatus = accountStatus;
    root.MirrorDcPost.authorNames = authorNames;
    root.MirrorDcPost.postJson = postJson;
    root.MirrorDcPost.postForm = postForm;
    root.MirrorDcPost.createCaptcha = createCaptcha;
})(typeof window !== "undefined" ? window : globalThis);
