(function (root) {
    "use strict";

    // 글 보기 추천 버튼. 서버가 새 추천 수를 주지 않으므로 화면의 숫자는 바꾸지 않는다.
    var document = root.document;
    var post = root.MirrorDcPost;
    if (!document || !post || typeof post.postJson !== "function") {
        return;
    }
    var panel = document.getElementById("vote-panel");
    if (!panel) {
        return;
    }
    var button = document.getElementById("vote-submit");
    var label = button.querySelector("[data-vote-label]");
    var status = document.getElementById("vote-status");
    var board = panel.getAttribute("data-board") || "";
    var kind = panel.getAttribute("data-kind") || "";
    var pid = panel.getAttribute("data-pid") || "";
    var captcha = post.createCaptcha("vote-captcha", "recommend", board);
    var state = { sending: false, done: false, retryUntil: 0, retryTimer: null };

    function setStatus(text, tone, link) {
        status.textContent = text || "";
        status.className = "vote-status" + (tone ? " is-" + tone : "");
        // 성공은 버튼 글자가 이미 보여 주므로 화면 낭독기에만 알린다.
        status.classList.toggle("sr-only", tone === "success");
        if (link) {
            status.appendChild(document.createTextNode(" "));
            var anchor = document.createElement("a");
            anchor.href = link.href;
            anchor.textContent = link.text;
            status.appendChild(anchor);
        }
    }

    function sync() {
        button.disabled = state.sending || state.done || state.retryUntil > Date.now();
        button.setAttribute("aria-busy", state.sending ? "true" : "false");
        button.classList.toggle("is-done", state.done);
        label.textContent = state.done ? "추천했어요" : state.sending ? "추천 중…" : "추천";
        if (captcha) {
            captcha.setDisabled(state.sending);
        }
    }

    function send() {
        if (state.sending || state.done || state.retryUntil > Date.now()) {
            return;
        }
        var body = { board: board, pid: pid };
        if (kind) {
            body.kind = kind;
        }
        var answer = captcha ? captcha.answer() : null;
        if (answer) {
            if (answer.imageFailed || !answer.code) {
                setStatus(answer.imageFailed ? "문자 이미지를 불러온 뒤 입력해 주세요." : "보이는 문자를 입력해 주세요.", "error");
                captcha.focus();
                return;
            }
            body.captcha_key = answer.key;
            body.captcha_code = answer.code;
        }
        state.sending = true;
        setStatus("");
        sync();
        post.postJson("/read/vote", body, 45000).then(function (result) {
            state.sending = false;
            sync();
            // 캡챠 입력칸을 다시 켠 뒤에 결과를 다뤄야 포커스가 들어간다.
            handle(post.interpret(result.status, result.payload));
            sync();
        });
    }

    function handle(outcome) {
        var payload = outcome.payload || {};
        var message = typeof payload.message === "string" ? payload.message : "";
        if (outcome.action !== "captcha" && captcha) {
            captcha.hide();
        }
        if (outcome.action === "success") {
            state.done = true;
            setStatus(message || "추천했어요.", "success");
            return;
        }
        if (outcome.action === "captcha") {
            setStatus(message || "보이는 문자를 입력하고 다시 추천해 주세요.", "error");
            captcha.show(payload.captcha);
            return;
        }
        if (outcome.action === "rejected" || outcome.action === "invalid") {
            setStatus(message || "디시에서 추천을 받지 않았어요.", "error");
            return;
        }
        if (outcome.action === "retry") {
            clearTimeout(state.retryTimer);
            state.retryUntil = Date.now() + outcome.seconds * 1000;
            setStatus((message ? message + " " : "") + outcome.seconds + "초 뒤에 다시 누를 수 있어요.", "error");
            state.retryTimer = setTimeout(function () {
                state.retryUntil = 0;
                sync();
            }, outcome.seconds * 1000);
            return;
        }
        if (outcome.action === "locked") {
            setStatus("이 브라우저의 추천이 잠겨 있어요.", "error", { href: post.unlockUrl(), text: "잠금 해제" });
            return;
        }
        if (outcome.action === "unavailable" && message) {
            setStatus(message, "error");
            return;
        }
        setStatus("추천 결과를 확인하지 못했어요. 잠시 뒤 글을 새로 열어 추천 수를 확인해 주세요.", "error");
    }

    button.addEventListener("click", send);
    var code = document.getElementById("vote-captcha-code");
    if (code) {
        code.addEventListener("keydown", function (event) {
            if (event.key === "Enter" && !event.isComposing) {
                event.preventDefault();
                send();
            }
        });
    }
    sync();
})(typeof window !== "undefined" ? window : globalThis);
