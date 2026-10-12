(function (root) {
    "use strict";

    // 글 보기 추천·비추천 버튼. 한 번에 하나만 보내고, 결과를 모르면 다시 보내라고 하지 않는다.
    var NAMES = { up: "추천", down: "비추천" };

    // 성공하면 디시 수가 하나 늘어난다. 원래 수를 모르면(빈 값) 그대로 둔다.
    function nextCount(raw) {
        var text = String(raw == null ? "" : raw);
        if (!/^[0-9]+$/.test(text)) {
            return null;
        }
        return Number(text) + 1;
    }

    root.MirrorVote = { nextCount: nextCount };

    var document = root.document;
    var post = root.MirrorDcPost;
    if (!document || !post || typeof post.postJson !== "function") {
        return;
    }
    var panel = document.getElementById("vote-panel");
    if (!panel) {
        return;
    }
    var buttons = Array.prototype.slice.call(panel.querySelectorAll("[data-vote]"));
    var status = document.getElementById("vote-status");
    var board = panel.getAttribute("data-board") || "";
    var kind = panel.getAttribute("data-kind") || "";
    var pid = panel.getAttribute("data-pid") || "";
    var captcha = post.createCaptcha("vote-captcha", "recommend", board);
    // pending: 캡챠를 요구받은 버튼. 캡챠 칸에서 Enter를 누르면 그 버튼으로 다시 보낸다.
    var state = { sending: "", done: {}, pending: "", retryUntil: 0, retryTimer: null };

    function buttonFor(direction) {
        return buttons.filter(function (button) {
            return button.getAttribute("data-vote") === direction;
        })[0] || null;
    }

    function setStatus(text, tone, link) {
        status.textContent = text || "";
        status.className = "vote-status" + (tone ? " is-" + tone : "");
        // 성공은 버튼 모양과 숫자가 이미 보여 주므로 화면 낭독기에만 알린다.
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
        var waiting = state.retryUntil > Date.now();
        buttons.forEach(function (button) {
            var direction = button.getAttribute("data-vote");
            var done = state.done[direction] === true;
            button.disabled = Boolean(state.sending) || done || waiting;
            button.setAttribute("aria-busy", state.sending === direction ? "true" : "false");
            button.setAttribute("aria-pressed", done ? "true" : "false");
            button.classList.toggle("is-done", done);
        });
        if (captcha) {
            captcha.setDisabled(Boolean(state.sending));
        }
    }

    function bumpCount(direction) {
        var button = buttonFor(direction);
        var next = button ? nextCount(button.getAttribute("data-count")) : null;
        if (next === null) {
            return;
        }
        var text = next.toLocaleString("ko-KR");
        button.setAttribute("data-count", String(next));
        var count = button.querySelector("[data-vote-count]");
        if (count) {
            count.textContent = text;
        }
        var meta = document.querySelector('[data-vote-meta="' + direction + '"]');
        if (meta) {
            meta.textContent = text;
        }
    }

    function send(direction) {
        if (!NAMES[direction] || state.sending || state.done[direction] || state.retryUntil > Date.now()) {
            return;
        }
        var body = { board: board, pid: pid, direction: direction };
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
        state.sending = direction;
        setStatus(NAMES[direction] + "을 보내고 있어요.", "busy");
        status.classList.add("sr-only");
        sync();
        post.postJson("/read/vote", body, 45000).then(function (result) {
            state.sending = "";
            sync();
            // 캡챠 입력칸을 다시 켠 뒤에 결과를 다뤄야 포커스가 들어간다.
            handle(direction, post.interpret(result.status, result.payload));
            sync();
        });
    }

    function handle(direction, outcome) {
        var payload = outcome.payload || {};
        var message = typeof payload.message === "string" ? payload.message : "";
        var name = NAMES[direction];
        if (outcome.action !== "captcha" && captcha) {
            captcha.hide();
            state.pending = "";
        }
        if (outcome.action === "success") {
            state.done[direction] = true;
            bumpCount(direction);
            setStatus(message || name + "했어요.", "success");
            return;
        }
        if (outcome.action === "captcha") {
            state.pending = direction;
            setStatus(message || "보이는 문자를 입력하고 " + name + " 버튼을 다시 눌러 주세요.", "error");
            // 캡챠 칸이 같은 안내를 보여 주므로 위 줄은 화면 낭독기에만 남긴다.
            status.classList.add("sr-only");
            captcha.show(payload.captcha);
            return;
        }
        if (outcome.action === "rejected" || outcome.action === "invalid") {
            setStatus(message || "디시에서 " + name + "을 받지 않았어요.", "error");
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
        setStatus(name + " 결과를 확인하지 못했어요. 잠시 뒤 글을 새로 열어 수를 확인해 주세요.", "error");
    }

    buttons.forEach(function (button) {
        button.addEventListener("click", function () {
            send(button.getAttribute("data-vote"));
        });
    });
    var code = document.getElementById("vote-captcha-code");
    if (code) {
        code.addEventListener("keydown", function (event) {
            if (event.key === "Enter" && !event.isComposing) {
                event.preventDefault();
                send(state.pending || "up");
            }
        });
    }
    sync();
})(typeof window !== "undefined" ? window : globalThis);
