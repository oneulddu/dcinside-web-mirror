(function (root) {
    "use strict";

    // 디시 글쓰기 폼. 확인은 서버가 다시 하지만 같은 기준으로 먼저 알려 준다.
    var IDENTITY_KEY = "mirror_write_identity_v1";
    var FIELDS = ["nickname", "password", "subject", "body", "captcha"];

    function charLength(value) {
        return Array.from(String(value == null ? "" : value)).length;
    }

    function validateFields(values, limits) {
        var nickname = String(values.nickname || "").trim();
        var password = String(values.password || "");
        var subject = String(values.subject || "").trim();
        var body = String(values.body || "");
        if (!nickname) {
            return { field: "nickname", message: "닉네임을 입력해 주세요." };
        }
        if (charLength(nickname) > limits.nickname_max) {
            return { field: "nickname", message: "닉네임은 " + limits.nickname_max + "자까지 쓸 수 있어요." };
        }
        if (charLength(password) < limits.password_min || charLength(password) > limits.password_max) {
            return { field: "password", message: "글 비밀번호는 " + limits.password_min + "~" + limits.password_max + "자로 입력해 주세요." };
        }
        if (!subject) {
            return { field: "subject", message: "제목을 입력해 주세요." };
        }
        if (charLength(subject) > limits.subject_max) {
            return { field: "subject", message: "제목은 " + limits.subject_max + "자까지 쓸 수 있어요." };
        }
        if (!body.trim()) {
            return { field: "body", message: "본문을 입력해 주세요." };
        }
        if (charLength(body) > limits.body_max) {
            return { field: "body", message: "본문은 " + limits.body_max + "자까지 쓸 수 있어요." };
        }
        if (values.captcha) {
            if (values.captcha.imageFailed) {
                return { field: "captcha", message: "문자 이미지를 불러온 뒤 입력해 주세요." };
            }
            var code = values.captcha.code;
            if (!code || charLength(code) > 10) {
                return { field: "captcha", message: "보이는 문자를 입력해 주세요." };
            }
        }
        return null;
    }

    function readIdentity(storage) {
        try {
            var parsed = JSON.parse(storage.getItem(IDENTITY_KEY) || "null");
            return parsed && typeof parsed === "object" ? parsed : {};
        } catch (err) {
            return {};
        }
    }

    // 동의한 항목만 남긴다. 둘 다 없으면 저장값을 지운다. 실패하면 false.
    function writeIdentity(storage, identity) {
        var data = {};
        if (identity.nickname) {
            data.nickname = identity.nickname;
        }
        if (identity.password) {
            data.password = identity.password;
        }
        try {
            if (data.nickname || data.password) {
                storage.setItem(IDENTITY_KEY, JSON.stringify(data));
            } else {
                storage.removeItem(IDENTITY_KEY);
            }
            return true;
        } catch (err) {
            return false;
        }
    }

    root.MirrorWriteForm = {
        validateFields: validateFields,
        readIdentity: readIdentity,
        writeIdentity: writeIdentity,
        IDENTITY_KEY: IDENTITY_KEY
    };

    var document = root.document;
    var post = root.MirrorDcPost;
    if (!document || !post || typeof post.postJson !== "function") {
        return;
    }

    var form = document.getElementById("write-form");
    if (!form) {
        return;
    }

    var el = {
        nickname: document.getElementById("write-nickname"),
        password: document.getElementById("write-password"),
        subject: document.getElementById("write-subject"),
        body: document.getElementById("write-body"),
        head: document.getElementById("write-head"),
        submit: document.getElementById("write-submit"),
        status: document.getElementById("write-status"),
        rememberNickname: document.getElementById("write-remember-nickname"),
        rememberPassword: document.getElementById("write-remember-password"),
        rememberStatus: document.getElementById("write-remember-status"),
        subjectCount: document.getElementById("write-subject-count"),
        bodyCount: document.getElementById("write-body-count")
    };
    var limits = {
        subject_max: Number(form.getAttribute("data-subject-max")) || 40,
        body_max: Number(form.getAttribute("data-body-max")) || 10000,
        nickname_max: Number(form.getAttribute("data-nickname-max")) || 20,
        password_min: Number(form.getAttribute("data-password-min")) || 2,
        password_max: Number(form.getAttribute("data-password-max")) || 20
    };
    var board = form.getAttribute("data-board") || "";
    var kind = form.getAttribute("data-kind") || "";
    var backUrl = post.safePath(form.getAttribute("data-back-url")) || "/";
    var captcha = post.createCaptcha("write-captcha", "article", board);
    var state = { sending: false, retryUntil: 0, retryTimer: null, leaving: false, initial: "" };
    var storage = null;
    try {
        storage = root.localStorage;
    } catch (err) {
        storage = null;
    }

    function draftSnapshot() {
        return [el.subject.value, el.body.value, el.head ? el.head.value : ""].join("\u0000");
    }

    function isDirty() {
        return draftSnapshot() !== state.initial;
    }

    function setStatus(text, tone, link) {
        el.status.textContent = text || "";
        el.status.className = "write-status" + (tone ? " is-" + tone : "");
        if (link) {
            el.status.appendChild(document.createTextNode(" "));
            var anchor = document.createElement("a");
            anchor.href = link.href;
            anchor.textContent = link.text;
            if (link.newTab) {
                anchor.target = "_blank";
                anchor.rel = "noopener";
            }
            el.status.appendChild(anchor);
        }
    }

    function fieldElement(field) {
        if (field === "captcha") {
            return document.getElementById("write-captcha-code");
        }
        return el[field] || null;
    }

    // 캡챠 칸의 #write-captcha-error는 이미지 실패 안내라서 입력 오류 칸으로 쓰지 않는다.
    function errorElement(field) {
        return field === "captcha" ? null : document.getElementById("write-" + field + "-error");
    }

    function clearErrors() {
        FIELDS.forEach(function (field) {
            var input = fieldElement(field);
            var error = errorElement(field);
            if (input) {
                input.removeAttribute("aria-invalid");
            }
            if (error) {
                error.hidden = true;
                error.textContent = "";
            }
        });
    }

    // 알려진 칸이면 그 칸 아래에, 아니면 상태 줄에 보인다.
    function showFieldError(field, message) {
        var input = fieldElement(field);
        var error = errorElement(field);
        if (!input) {
            setStatus(message, "error");
            return;
        }
        input.setAttribute("aria-invalid", "true");
        if (error) {
            error.textContent = message;
            error.hidden = false;
        } else {
            setStatus(message, "error");
        }
        input.focus();
    }

    function updateCount(input, output, max) {
        var length = charLength(input.value);
        output.textContent = length + "/" + max;
        output.classList.toggle("is-over", length > max);
    }

    function setBusy(busy) {
        state.sending = busy;
        form.setAttribute("aria-busy", busy ? "true" : "false");
        [el.nickname, el.password, el.subject, el.body, el.head, el.rememberNickname, el.rememberPassword].forEach(function (input) {
            if (input) {
                input.disabled = busy;
            }
        });
        if (captcha) {
            captcha.setDisabled(busy);
        }
        syncSubmit();
    }

    function syncSubmit() {
        var waiting = state.retryUntil > Date.now();
        el.submit.disabled = state.sending || waiting;
        el.submit.textContent = state.sending ? "등록 중…" : "등록";
    }

    function startRetryWait(seconds, message) {
        clearTimeout(state.retryTimer);
        state.retryUntil = Date.now() + seconds * 1000;
        setStatus((message ? message + " " : "") + seconds + "초 뒤에 다시 등록할 수 있어요.", "error");
        syncSubmit();
        state.retryTimer = setTimeout(function () {
            state.retryUntil = 0;
            syncSubmit();
        }, seconds * 1000);
    }

    function saveIdentity() {
        if (!storage) {
            if (el.rememberNickname.checked || el.rememberPassword.checked) {
                el.rememberStatus.textContent = "이 브라우저에는 저장할 수 없어요. 입력한 값은 그대로 써요.";
            }
            return;
        }
        var ok = writeIdentity(storage, {
            nickname: el.rememberNickname.checked ? el.nickname.value.trim() : "",
            password: el.rememberPassword.checked ? el.password.value : ""
        });
        el.rememberStatus.textContent = ok ? "" : "저장하지 못했어요. 입력한 값은 그대로 써요.";
    }

    function restoreIdentity() {
        if (!storage) {
            return;
        }
        var saved = readIdentity(storage);
        if (typeof saved.nickname === "string" && saved.nickname) {
            el.nickname.value = saved.nickname;
            el.rememberNickname.checked = true;
        }
        if (typeof saved.password === "string" && saved.password) {
            el.password.value = saved.password;
            el.rememberPassword.checked = true;
        }
    }

    function headPayload() {
        if (!el.head || !el.head.value) {
            return {};
        }
        var option = el.head.options[el.head.selectedIndex];
        return { head_id: el.head.value, head_name: option ? option.getAttribute("data-head-name") || option.textContent : "" };
    }

    function submit(event) {
        event.preventDefault();
        if (state.sending || state.retryUntil > Date.now()) {
            return;
        }
        clearErrors();
        setStatus("");
        var answer = captcha ? captcha.answer() : null;
        var problem = validateFields({
            nickname: el.nickname.value,
            password: el.password.value,
            subject: el.subject.value,
            body: el.body.value,
            captcha: answer
        }, limits);
        if (problem) {
            showFieldError(problem.field, problem.message);
            return;
        }
        var body = {
            board: board,
            subject: el.subject.value.trim(),
            body: el.body.value,
            nickname: el.nickname.value.trim(),
            password: el.password.value
        };
        if (kind) {
            body.kind = kind;
        }
        Object.assign(body, headPayload());
        if (answer) {
            body.captcha_key = answer.key;
            body.captcha_code = answer.code;
        }
        setBusy(true);
        setStatus("등록하고 있어요.");
        post.postJson("/write", body, 45000).then(function (result) {
            setBusy(false);
            handle(post.interpret(result.status, result.payload));
        });
    }

    function handle(outcome) {
        var payload = outcome.payload || {};
        var message = typeof payload.message === "string" ? payload.message : "";
        // 한 번 보낸 캡챠 답은 다시 쓰지 않는다. 다시 필요하면 서버가 새 문제를 준다.
        if (outcome.action !== "captcha" && captcha) {
            captcha.hide();
        }
        if (outcome.action === "success") {
            saveIdentity();
            state.leaving = true;
            setStatus("등록했어요. 목록으로 이동해요.", "success");
            root.location.assign(post.safePath(payload.read_url) || post.safePath(payload.board_url) || backUrl);
            return;
        }
        if (outcome.action === "captcha") {
            setStatus(message || "보이는 문자를 입력하고 다시 등록해 주세요.", "error");
            captcha.show(payload.captcha);
            return;
        }
        if (outcome.action === "invalid") {
            showFieldError(String(payload.field || ""), message || "입력한 내용을 확인해 주세요.");
            return;
        }
        if (outcome.action === "rejected") {
            setStatus(message || "디시에서 글을 받지 않았어요.", "error");
            return;
        }
        if (outcome.action === "retry") {
            startRetryWait(outcome.seconds, message);
            return;
        }
        if (outcome.action === "locked") {
            setStatus("이 브라우저의 글쓰기가 잠겨 있어요.", "error", { href: post.unlockUrl(), text: "새 탭에서 잠금 해제", newTab: true });
            return;
        }
        if (outcome.action === "unavailable" && message) {
            setStatus(message, "error");
            return;
        }
        // 응답을 받지 못했으면 글이 올라갔는지 알 수 없으므로 다시 보내라고 하지 않는다.
        setStatus("등록 여부를 확인하지 못했어요. 목록에서 먼저 확인해 주세요.", "error", { href: backUrl, text: "목록 보기", newTab: true });
    }

    restoreIdentity();
    state.initial = draftSnapshot();
    updateCount(el.subject, el.subjectCount, limits.subject_max);
    updateCount(el.body, el.bodyCount, limits.body_max);
    el.subject.addEventListener("input", function () {
        updateCount(el.subject, el.subjectCount, limits.subject_max);
    });
    el.body.addEventListener("input", function () {
        updateCount(el.body, el.bodyCount, limits.body_max);
    });
    el.rememberNickname.addEventListener("change", saveIdentity);
    el.rememberPassword.addEventListener("change", saveIdentity);
    form.addEventListener("submit", submit);
    root.addEventListener("beforeunload", function (event) {
        if (!state.leaving && isDirty()) {
            event.preventDefault();
            event.returnValue = "";
        }
    });
    syncSubmit();
})(typeof window !== "undefined" ? window : globalThis);
