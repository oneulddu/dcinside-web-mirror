(function (root) {
    "use strict";

    // 디시 계정 로그인 화면. 로그인은 누를 때만 하고, 실패해도 자동으로 다시 하지 않는다.
    var OTP_PATTERN = /^[0-9]{6}$/;

    function describe(status) {
        var name = String(status.name || "").trim();
        var id = String(status.user_id || "");
        return (name || id || "디시") + (id ? "(" + id + ")" : "") + " 계정으로 로그인했어요.";
    }

    root.MirrorAccountLogin = { describe: describe, OTP_PATTERN: OTP_PATTERN };

    var document = root.document;
    var post = root.MirrorDcPost;
    if (!document || !post || typeof post.postJson !== "function") {
        return;
    }
    var form = document.getElementById("account-form");
    if (!form) {
        return;
    }
    var el = {
        status: document.getElementById("account-status"),
        otpField: document.getElementById("account-otp-field"),
        otp: document.getElementById("account-otp"),
        otpError: document.getElementById("account-otp-error"),
        submit: document.getElementById("account-submit")
    };
    var captcha = post.createCaptcha("account-captcha", "login", "", null);
    var state = { sending: false, retryUntil: 0, retryTimer: null, loggedIn: /로그인돼 있어요/.test(el.status.textContent) };

    function setStatus(text, tone, link) {
        el.status.textContent = text || "";
        el.status.className = "write-status" + (tone ? " is-" + tone : "");
        if (link) {
            el.status.appendChild(document.createTextNode(" "));
            var anchor = document.createElement("a");
            anchor.href = link.href;
            anchor.textContent = link.text;
            el.status.appendChild(anchor);
        }
    }

    function sync() {
        var waiting = state.retryUntil > Date.now();
        el.submit.disabled = state.sending || waiting || form.getAttribute("data-unconfigured") !== null;
        el.submit.setAttribute("aria-busy", state.sending ? "true" : "false");
        el.submit.textContent = state.sending ? "로그인 중" : (state.loggedIn ? "다시 로그인" : "로그인");
        el.otp.disabled = state.sending;
        if (captcha) {
            captcha.setDisabled(state.sending);
        }
    }

    function setOtpError(message) {
        el.otpError.textContent = message || "";
        el.otpError.hidden = !message;
        if (message) {
            el.otp.setAttribute("aria-invalid", "true");
            el.otp.focus();
        } else {
            el.otp.removeAttribute("aria-invalid");
        }
    }

    function showOtp(show) {
        el.otpField.hidden = !show;
        if (!show) {
            el.otp.value = "";
            setOtpError("");
        }
    }

    function showCaptcha(key) {
        captcha.show({ key: /^[0-9a-f]{16}$/.test(String(key || "")) ? key : post.newCaptchaKey() });
    }

    function startRetry(seconds, message) {
        clearTimeout(state.retryTimer);
        state.retryUntil = Date.now() + seconds * 1000;
        setStatus((message ? message + " " : "") + seconds + "초 뒤에 다시 로그인할 수 있어요.", "error");
        state.retryTimer = setTimeout(function () {
            state.retryUntil = 0;
            sync();
        }, seconds * 1000);
    }

    function handle(outcome) {
        var payload = outcome.payload || {};
        var message = typeof payload.message === "string" ? payload.message : "";
        if (outcome.action === "success") {
            state.loggedIn = payload.logged_in === true;
            captcha.hide();
            showOtp(false);
            setStatus(state.loggedIn ? describe(payload) : (message || "로그인 상태를 확인하지 못했어요."), state.loggedIn ? "success" : "error");
            return;
        }
        if (outcome.action === "login_needed") {
            state.loggedIn = false;
            if (payload.reason === "otp") {
                captcha.hide();
                showOtp(true);
                setStatus(message || "디시 OTP 6자리를 입력하고 로그인해 주세요.", "error");
                el.otp.value = "";
                el.otp.focus();
                return;
            }
            if (payload.reason === "captcha") {
                setStatus(message || "보이는 문자를 입력하고 로그인해 주세요.", "error");
                showCaptcha(payload.captcha_key);
                return;
            }
            captcha.hide();
            showOtp(false);
            setStatus(message || "로그인하지 못했어요. 서버의 계정 설정을 확인해 주세요.", "error");
            return;
        }
        if (outcome.action === "invalid") {
            if (payload.field === "otp" && !el.otpField.hidden) {
                setOtpError(message);
                setStatus("");
                return;
            }
            setStatus(message || "입력한 내용을 확인해 주세요.", "error");
            return;
        }
        if (outcome.action === "retry") {
            startRetry(outcome.seconds, message);
            return;
        }
        if (outcome.action === "locked") {
            setStatus("이 브라우저의 글쓰기가 잠겨 있어요.", "error", { href: post.unlockUrl(), text: "잠금 해제" });
            return;
        }
        setStatus(message || "로그인 결과를 확인하지 못했어요. 잠시 뒤 새로고침해 상태를 확인해 주세요.", "error");
    }

    function submit() {
        if (state.sending || state.retryUntil > Date.now() || form.getAttribute("data-unconfigured") !== null) {
            return;
        }
        setOtpError("");
        var body = {};
        if (!el.otpField.hidden) {
            var otp = el.otp.value.trim();
            if (!OTP_PATTERN.test(otp)) {
                setOtpError("OTP는 숫자 6자리로 입력해 주세요.");
                return;
            }
            body.otp = otp;
        }
        var answer = captcha ? captcha.answer() : null;
        if (answer) {
            if (answer.imageFailed || !answer.code) {
                setStatus(answer.imageFailed ? "문자 이미지를 불러온 뒤 입력해 주세요." : "보이는 문자를 입력해 주세요.", "error");
                captcha.focus();
                return;
            }
            body.captcha = { key: answer.key, code: answer.code };
        }
        state.sending = true;
        setStatus("로그인하고 있어요.", "busy");
        sync();
        post.postJson("/write/account/login", body, 45000).then(function (result) {
            // 칸을 먼저 다시 켠다. 꺼진 칸에는 새로 요구한 입력으로 커서를 옮길 수 없다.
            state.sending = false;
            sync();
            handle(post.interpret(result.status, result.payload));
            sync();
        });
    }

    form.addEventListener("submit", function (event) {
        event.preventDefault();
        submit();
    });
    el.otp.addEventListener("input", function () {
        setOtpError("");
    });
    // 지난 로그인이 보안 문자에서 멈췄으면 새 문자를 보여 준다.
    if (form.getAttribute("data-reason") === "captcha") {
        showCaptcha("");
    }
    sync();
})(typeof window !== "undefined" ? window : globalThis);
