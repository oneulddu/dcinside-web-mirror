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
    // 유동 추천은 인물 갤러리 말고는 노이즈를 지운 PC 보안 문자를 쓰고, 답도 그 문자를 받은 PC 세션으로 보낸다.
    // 계정 추천은 로그인으로 보내야 해서 언제나 앱 보안 문자를 쓴다.
    var captchaKind = kind === "person" ? "recommend" : "vote";
    var captchaExtra = captchaKind === "vote" ? { pid: pid, gallery_kind: kind } : null;
    var captcha = post.createCaptcha("vote-captcha", captchaKind, board, captchaExtra);
    // 작성자는 상단 작성자 메뉴의 값. 메뉴가 없으면 유동이다.
    var author = typeof post.authorMode === "function" ? post.authorMode() : "anonymous";

    function sourceFor(mode) {
        return post.modeAccount(mode) ? { kind: "recommend", extra: null } : { kind: captchaKind, extra: captchaExtra };
    }
    // pending: 캡챠를 요구받은 버튼. 캡챠 칸에서 Enter를 누르면 그 버튼으로 다시 보낸다.
    // options: 디시 앱 기준 갤러리 추천 설정. 모르면 null이고, 그때는 누른 뒤 디시 응답을 따른다.
    var state = { sending: "", done: {}, pending: "", retryUntil: 0, retryTimer: null, options: null };

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
            if (link.newTab) {
                anchor.target = "_blank";
                anchor.rel = "noopener";
            }
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

    // 비추천 버튼은 디시 앱의 갤러리 설정을 따른다. 모바일 페이지의 버튼·수는 이 설정과 다를 때가 있다.
    function showDown(visible) {
        var button = buttonFor("down");
        if (button) {
            button.hidden = !visible;
        }
        // 비추천을 끈 갤러리면 제목 아래 비추천 수도 함께 숨긴다.
        var meta = document.querySelector('[data-vote-meta-row="down"]');
        if (meta) {
            meta.hidden = !visible;
        }
    }

    function loadOptions() {
        if (typeof root.fetch !== "function") {
            return;
        }
        var params = new URLSearchParams({ board: board, pid: pid });
        root.fetch("/read/vote/options?" + params.toString(), {
            credentials: "same-origin",
            headers: { Accept: "application/json" }
        }).then(function (response) {
            return response.json();
        }).then(function (data) {
            if (data && data.code === "visitor_check_required") {
                setStatus(data.message || "아무것도 전송하지 않았어요. 페이지를 새로고침해 주세요.", "error");
                return;
            }
            if (!data || data.ok !== true) {
                return;
            }
            state.options = { down: data.down === true, captcha: data.captcha === true };
            showDown(state.options.down);
        }).catch(function () {
            // 설정을 못 읽어도 버튼은 그대로 쓴다.
        });
    }

    function send(direction) {
        if (!NAMES[direction] || state.sending || state.done[direction] || state.retryUntil > Date.now()) {
            return;
        }
        if (!author) {
            if (!post.authorModes().length) {
                // 상단 메뉴가 없어 고를 수 있는 것이 유동뿐이다. 알린 뒤 다시 누르면 유동으로 보낸다.
                post.setAuthorMode("anonymous");
                author = "anonymous";
                setStatus("전에 고른 디시 계정이 서버에 없어요. 유동으로 " + NAMES[direction] + "하려면 다시 눌러 주세요.", "error");
                return;
            }
            setStatus("작성자를 다시 골라 주세요. 전에 고른 계정이 서버에 없어요. 상단 작성자 메뉴에서 골라 주세요.", "error");
            return;
        }
        // 보낼 때의 작성자를 붙잡아 둔다. 결과를 받기 전에 상단 메뉴를 바꿔도 바뀌지 않는다.
        var sentAuthor = author;
        var slot = post.modeAccount(sentAuthor);
        var body = { board: board, pid: pid, direction: direction };
        if (slot) {
            body.mode = "account";
            body.account = slot;
        }
        if (kind) {
            body.kind = kind;
        }
        var answer = captcha ? captcha.answer() : null;
        // 유동 보안 문자 설정을 보고 미리 문자 칸을 연다. 계정은 디시가 실제로 요구할 때만 연다.
        if (!slot && !answer && captcha && state.options && state.options.captcha) {
            var key = post.newCaptchaKey();
            state.pending = direction;
            setStatus("이 갤러리는 " + NAMES[direction] + "할 때 보이는 문자를 입력해야 해요.", "error");
            status.classList.add("sr-only");
            captcha.show({ key: key, image_url: post.captchaImageUrl(captchaKind, board, key, captchaExtra) });
            sync();
            return;
        }
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
        state.sentAnswer = Boolean(answer);
        setStatus(NAMES[direction] + "을 보내고 있어요.", "busy");
        status.classList.add("sr-only");
        sync();
        post.postJson("/read/vote", body, 45000).then(function (result) {
            state.sending = "";
            sync();
            // 캡챠 입력칸을 다시 켠 뒤에 결과를 다뤄야 포커스가 들어간다.
            handle(direction, post.interpret(result.status, result.payload), sentAuthor);
            sync();
        });
    }

    function handle(direction, outcome, sentAuthor) {
        var payload = outcome.payload || {};
        var message = typeof payload.message === "string" ? payload.message : "";
        if (outcome.action === "visitor_check_required") {
            setStatus(message || "아무것도 전송하지 않았어요. 페이지를 새로고침해 주세요.", "error");
            return;
        }
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
            // 보내는 사이 작성자를 바꿨으면 지난 작성자의 문제는 띄우지 않는다.
            if (sentAuthor !== author) {
                captcha.hide();
                state.pending = "";
                setStatus("작성자를 바꿔서 " + name + "을 보내지 않았어요. 다시 눌러 주세요.", "error");
                return;
            }
            captcha.setSource(sourceFor(sentAuthor).kind, sourceFor(sentAuthor).extra);
            state.pending = direction;
            setStatus(message || "보이는 문자를 입력하고 " + name + " 버튼을 다시 눌러 주세요.", "error");
            // 처음 요구받을 때는 캡챠 칸이 같은 안내를 보여 주므로 화면 낭독기에만 남긴다.
            // 입력한 문자가 틀렸을 때는 새 문자로 바뀐 이유를 눈으로도 보여 준다.
            if (state.sentAnswer) {
                setStatus("입력한 문자가 맞지 않아요. 새 문자를 입력하고 " + name + " 버튼을 다시 눌러 주세요.", "error");
            } else {
                status.classList.add("sr-only");
            }
            captcha.show(payload.captcha);
            return;
        }
        if (outcome.action === "rejected" || outcome.action === "invalid") {
            if (payload.reason === "down_disabled") {
                showDown(false);
            }
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
        if (outcome.action === "login_needed") {
            setStatus(message || "디시 계정에 다시 로그인해야 해요. " + name + "은 보내지 않았어요.", "error", post.accountLink(payload));
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
    // 상단 작성자 메뉴를 바꾸면 다음 추천부터 그 작성자로 보낸다. 지난 작성자의 보안 문자는 지운다.
    // 이미 한 추천 표시와 기다림 시간은 그대로 둔다.
    if (typeof post.onAuthorChange === "function") {
        post.onAuthorChange(function (mode) {
            if (mode === author) {
                return;
            }
            author = mode;
            if (captcha) {
                // 보내는 중이어도 문자 종류는 바꿔 둔다. 늦게 온 지난 작성자의 문제는 handle이 띄우지 않는다.
                captcha.setSource(sourceFor(author).kind, sourceFor(author).extra);
                if (!state.sending) {
                    captcha.hide();
                    state.pending = "";
                }
            }
        });
    }
    if (captcha) {
        captcha.setSource(sourceFor(author).kind, sourceFor(author).extra);
    }
    sync();
    loadOptions();
})(typeof window !== "undefined" ? window : globalThis);
