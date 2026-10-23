(function (root) {
    "use strict";

    // 글 보기 댓글 입력칸. 한 번에 하나만 보내고, 결과를 모르면 자동으로 다시 보내지 않는다.
    // 닉네임·비밀번호 기억은 글쓰기 화면과 같은 저장값을 쓴다.
    var IDENTITY_KEY = "mirror_write_identity_v1";

    function charLength(value) {
        return Array.from(String(value == null ? "" : value)).length;
    }

    // 서버와 같은 규칙으로 먼저 확인한다. 문제가 없으면 null.
    function validateComment(values, limits) {
        var content = String(values.content || "");
        if (!content.trim() || charLength(content) > limits.comment_max) {
            return { field: "content", message: content.trim() ? "댓글은 " + limits.comment_max + "자까지 쓸 수 있어요." : "댓글을 입력해 주세요." };
        }
        // 디시 계정으로 쓰면 닉네임·비밀번호를 보내지 않는다.
        if (values.account) {
            return null;
        }
        var nickname = String(values.nickname || "").trim();
        if (!nickname || charLength(nickname) > limits.nickname_max) {
            return { field: "nickname", message: "닉네임은 1~" + limits.nickname_max + "자로 입력해 주세요." };
        }
        var password = String(values.password || "");
        var length = charLength(password);
        if (!password.trim() || length < limits.password_min || length > limits.password_max) {
            return { field: "password", message: "비밀번호는 " + limits.password_min + "~" + limits.password_max + "자로 입력해 주세요." };
        }
        return null;
    }

    root.MirrorCommentForm = { validateComment: validateComment, IDENTITY_KEY: IDENTITY_KEY };

    var document = root.document;
    var post = root.MirrorDcPost;
    if (!document || !post || typeof post.postJson !== "function") {
        return;
    }
    var form = document.getElementById("comment-form");
    if (!form) {
        return;
    }
    var list = document.querySelector(".comment-shell .comment-list");
    var home = form.parentNode;
    var homeNext = form.nextSibling;
    var limits = {
        comment_max: Number(form.getAttribute("data-comment-max")) || 400,
        nickname_max: Number(form.getAttribute("data-nickname-max")) || 20,
        password_min: Number(form.getAttribute("data-password-min")) || 2,
        password_max: Number(form.getAttribute("data-password-max")) || 20
    };
    var board = form.getAttribute("data-board") || "";
    var kind = form.getAttribute("data-kind") || "";
    var pid = form.getAttribute("data-pid") || "";
    var el = {
        nickname: document.getElementById("comment-nickname"),
        password: document.getElementById("comment-password"),
        content: document.getElementById("comment-content"),
        identityError: document.getElementById("comment-identity-error"),
        contentError: document.getElementById("comment-content-error"),
        rememberNickname: document.getElementById("comment-remember-nickname"),
        rememberPassword: document.getElementById("comment-remember-password"),
        passwordHint: document.getElementById("comment-password-hint"),
        length: document.getElementById("comment-length"),
        submit: document.getElementById("comment-submit"),
        status: document.getElementById("comment-status"),
        target: document.getElementById("comment-form-target"),
        targetText: document.getElementById("comment-form-target-text"),
        cancel: document.getElementById("comment-form-cancel"),
        modeInputs: Array.prototype.slice.call(form.querySelectorAll('input[name="comment-mode"]')),
        identity: form.querySelector(".comment-form-identity"),
        remember: form.querySelector(".comment-form-remember"),
        accountNote: document.getElementById("comment-account-note")
    };
    var captcha = post.createCaptcha("comment-captcha", "comment", board, null);
    var storage = null;
    try {
        storage = root.localStorage;
    } catch (err) {
        storage = null;
    }
    // parent: 답글을 다는 댓글({id, authorCode, author, item}). 없으면 일반 댓글.
    // mode: 계정 선택이 있을 때만 마지막으로 고른 값을 쓴다.
    // 기억한 계정이 선택지에 없으면 빈 값으로 두고 작성자를 다시 고르게 한다.
    var state = { sending: false, retryUntil: 0, retryTimer: null, parent: null, sentAnswer: false,
        mode: post.pickMode(post.readMode(storage), el.modeInputs) };

    function accountSlot() {
        return post.modeAccount(state.mode);
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

    // 계정으로 쓰면 닉네임·비밀번호 칸과 기억 선택을 숨긴다.
    function applyMode() {
        var account = Boolean(accountSlot());
        el.modeInputs.forEach(function (input) {
            input.checked = input.value === state.mode;
        });
        [el.identity, el.remember].forEach(function (node) {
            if (node) {
                node.hidden = account;
            }
        });
        if (el.accountNote) {
            el.accountNote.hidden = !account;
        }
        el.passwordHint.hidden = account || !el.rememberPassword.checked;
    }

    function showError(field, message) {
        var identity = field === "nickname" || field === "password";
        var box = identity ? el.identityError : el.contentError;
        box.textContent = message || "";
        box.hidden = !message;
        if (field === "nickname" || field === "password" || field === "content") {
            el[field].setAttribute("aria-invalid", "true");
            el[field].focus();
        }
    }

    function clearErrors() {
        [el.identityError, el.contentError].forEach(function (box) {
            box.textContent = "";
            box.hidden = true;
        });
        [el.nickname, el.password, el.content].forEach(function (input) {
            input.removeAttribute("aria-invalid");
        });
    }

    function updateLength() {
        var length = charLength(el.content.value);
        el.length.textContent = length.toLocaleString("ko-KR") + "/" + limits.comment_max.toLocaleString("ko-KR");
        el.length.classList.toggle("is-over", length > limits.comment_max);
    }

    function sync() {
        var waiting = state.retryUntil > Date.now();
        [el.nickname, el.password, el.content, el.cancel].concat(el.modeInputs).forEach(function (input) {
            input.disabled = state.sending;
        });
        el.submit.disabled = state.sending || waiting;
        el.submit.setAttribute("aria-busy", state.sending ? "true" : "false");
        el.submit.textContent = state.sending ? "등록 중" : "등록";
        if (captcha) {
            captcha.setDisabled(state.sending);
        }
    }

    function readIdentity() {
        try {
            var parsed = JSON.parse(storage.getItem(IDENTITY_KEY) || "null");
            return parsed && typeof parsed === "object" ? parsed : {};
        } catch (err) {
            return {};
        }
    }

    function restoreIdentity() {
        if (!storage) {
            return;
        }
        var saved = readIdentity();
        if (typeof saved.nickname === "string" && saved.nickname) {
            el.nickname.value = saved.nickname;
            el.rememberNickname.checked = true;
        }
        if (typeof saved.password === "string" && saved.password) {
            el.password.value = saved.password;
            el.rememberPassword.checked = true;
        }
    }

    // 체크한 항목만 남긴다. 글쓰기 화면과 같은 규칙이다.
    function saveIdentity() {
        if (!storage) {
            return;
        }
        var data = {};
        if (el.rememberNickname.checked && el.nickname.value.trim()) {
            data.nickname = el.nickname.value.trim();
        }
        if (el.rememberPassword.checked && el.password.value) {
            data.password = el.password.value;
        }
        try {
            if (data.nickname || data.password) {
                storage.setItem(IDENTITY_KEY, JSON.stringify(data));
            } else {
                storage.removeItem(IDENTITY_KEY);
            }
        } catch (err) {
            // 저장하지 못해도 입력한 값으로 보낸다.
        }
    }

    // 부모 댓글과 그 답글들 다음 자리. 새 답글과 답글 입력칸이 여기에 들어간다.
    function threadEnd(item) {
        var last = item;
        while (last.nextElementSibling && last.nextElementSibling.classList.contains("is-reply")) {
            last = last.nextElementSibling;
        }
        return last;
    }

    function setParent(item) {
        if (state.sending) {
            return;
        }
        var reset = !item;
        if (captcha) {
            captcha.hide();
        }
        if (list) {
            Array.prototype.forEach.call(list.querySelectorAll("[data-comment-reply]"), function (button) {
                button.setAttribute("aria-expanded", button.closest(".comment-item") === item ? "true" : "false");
            });
        }
        if (reset) {
            state.parent = null;
            el.target.hidden = true;
            var slot = list ? list.querySelector(".comment-reply-slot") : null;
            home.insertBefore(form, homeNext);
            if (slot) {
                slot.remove();
            }
            form.classList.remove("is-reply");
            return;
        }
        var authorNode = item.querySelector(".comment-meta .author-text");
        state.parent = {
            id: item.getAttribute("data-comment-id"),
            authorCode: authorNode ? authorNode.getAttribute("data-author-code") || "" : "",
            author: authorNode ? authorNode.getAttribute("data-author") || "" : "",
            item: item
        };
        var holder = list.querySelector(".comment-reply-slot");
        if (!holder) {
            holder = document.createElement("li");
            holder.className = "comment-reply-slot";
        }
        var end = threadEnd(item);
        end.parentNode.insertBefore(holder, end.nextSibling);
        holder.appendChild(form);
        form.classList.add("is-reply");
        el.targetText.textContent = (state.parent.author || "이 댓글") + "에게 답글";
        el.target.hidden = false;
        setStatus("");
        el.content.focus();
    }

    // 디시에 올라간 댓글을 새로 읽지 않고 목록에 바로 붙인다. 시각은 '방금'으로 둔다.
    // commentId: 디시가 돌려준 새 댓글 번호. 있을 때만 바로 지울 수 있게 한다.
    // who: {name, code, account}. 계정 댓글은 식별 코드(아이디)를 함께 보이고 비밀번호 없이 지운다.
    function appendComment(content, who, commentId) {
        if (!list) {
            return;
        }
        var empty = list.querySelector(":scope > .empty-row");
        if (empty) {
            empty.remove();
        }
        var item = document.createElement("li");
        item.className = "comment-item is-new" + (state.parent ? " is-reply" : "");
        if (commentId) {
            item.setAttribute("data-comment-id", commentId);
        }
        if (state.parent) {
            item.setAttribute("data-parent-id", state.parent.id);
            var mark = document.createElement("span");
            mark.className = "reply-mark";
            mark.setAttribute("aria-hidden", "true");
            mark.innerHTML = '<svg viewBox="0 0 24 24" focusable="false"><path d="M6 5v6a4 4 0 0 0 4 4h8"/><path d="M15 12l3 3-3 3"/></svg>';
            item.appendChild(mark);
        }
        var main = document.createElement("div");
        main.className = "comment-main";
        var text = document.createElement("p");
        text.textContent = content;
        main.appendChild(text);
        var meta = document.createElement("div");
        meta.className = "comment-meta";
        var author = document.createElement("span");
        author.className = "author-text";
        author.setAttribute("data-author", who.name);
        author.textContent = who.name;
        if (who.code) {
            author.setAttribute("data-author-code", who.code);
            author.textContent = who.name + "(" + who.code + ")";
        }
        var time = document.createElement("span");
        time.textContent = "방금";
        meta.appendChild(author);
        meta.appendChild(time);
        if (commentId) {
            var remove = document.createElement("button");
            remove.className = "comment-reply-btn";
            remove.type = "button";
            remove.setAttribute("data-comment-delete", "");
            remove.setAttribute("aria-haspopup", "dialog");
            remove.setAttribute("aria-label", "내 댓글 삭제");
            // 계정 댓글은 쓴 계정으로만 지운다.
            if (who.account) {
                remove.setAttribute("data-owner", "account");
                remove.setAttribute("data-account", who.account);
            }
            remove.textContent = "삭제";
            meta.appendChild(remove);
        }
        item.appendChild(main);
        item.appendChild(meta);
        if (state.parent && state.parent.item.isConnected) {
            var end = threadEnd(state.parent.item);
            end.parentNode.insertBefore(item, end.nextSibling);
        } else {
            list.appendChild(item);
        }
        var count = document.querySelector(".comment-shell .comment-count");
        if (count && /^[0-9]+$/.test(count.textContent.trim())) {
            count.textContent = String(Number(count.textContent.trim()) + 1);
        }
    }

    function startRetry(seconds, message) {
        clearTimeout(state.retryTimer);
        state.retryUntil = Date.now() + seconds * 1000;
        setStatus((message ? message + " " : "") + seconds + "초 뒤에 다시 등록할 수 있어요.", "error");
        state.retryTimer = setTimeout(function () {
            state.retryUntil = 0;
            sync();
        }, seconds * 1000);
    }

    function handle(outcome, sent) {
        var payload = outcome.payload || {};
        var message = typeof payload.message === "string" ? payload.message : "";
        if (outcome.action === "visitor_check_required") {
            setStatus(message || "아무것도 전송하지 않았어요. 입력을 보관한 뒤 페이지를 새로고침해 주세요.", "error");
            return;
        }
        if (outcome.action !== "captcha" && captcha) {
            captcha.hide();
        }
        if (outcome.action === "success") {
            var newId = /^[1-9][0-9]{0,14}$/.test(String(payload.comment_id)) ? String(payload.comment_id) : "";
            var who = { name: sent.nickname, code: "", account: "" };
            if (sent.account) {
                var author = payload.author && typeof payload.author === "object" ? payload.author : {};
                var code = String(author.user_id || "");
                who = { name: String(author.name || "").trim() || "내 계정", code: /^[A-Za-z0-9_]{1,40}$/.test(code) ? code : "", account: sent.account };
            }
            appendComment(sent.content, who, newId);
            el.content.value = "";
            updateLength();
            setParent(null);
            setStatus(message || "댓글을 달았어요.", "success");
            return;
        }
        if (outcome.action === "captcha") {
            setStatus(state.sentAnswer
                ? "입력한 문자가 맞지 않아요. 새 문자를 입력하고 다시 등록해 주세요."
                : (message || "보이는 문자를 입력하고 다시 등록해 주세요."), "error");
            // 처음 요구받을 때는 문자 칸이 같은 안내를 보여 주므로 화면 낭독기에만 알린다.
            el.status.classList.toggle("sr-only", !state.sentAnswer);
            captcha.show(payload.captcha);
            return;
        }
        if (outcome.action === "invalid") {
            if (["content", "nickname", "password"].indexOf(payload.field) >= 0) {
                showError(payload.field, message);
                setStatus("");
                return;
            }
            setStatus(message || "입력한 내용을 확인해 주세요.", "error");
            return;
        }
        if (outcome.action === "rejected") {
            setStatus(message || "디시가 댓글을 받지 않았어요.", "error");
            return;
        }
        if (outcome.action === "retry") {
            startRetry(outcome.seconds, message);
            return;
        }
        if (outcome.action === "locked") {
            setStatus("이 브라우저의 댓글 쓰기가 잠겨 있어요.", "error", { href: post.unlockUrl(), text: "잠금 해제" });
            return;
        }
        if (outcome.action === "login_needed") {
            setStatus(message || "디시 계정에 다시 로그인해야 해요. 댓글은 보내지 않았어요.", "error", post.accountLink(payload));
            return;
        }
        if (outcome.action === "unavailable" && message) {
            setStatus(message, "error");
            return;
        }
        setStatus(message || "댓글 결과를 확인하지 못했어요. 다시 쓰기 전에 글을 새로 열어 댓글을 확인해 주세요.", "error");
    }

    function submit() {
        if (state.sending || state.retryUntil > Date.now()) {
            return;
        }
        clearErrors();
        if (!state.mode) {
            if (!el.modeInputs.length) {
                // 고를 수 있는 것이 유동뿐이다. 알린 뒤 다시 누르면 유동으로 보낸다.
                state.mode = "anonymous";
                post.saveMode(storage, state.mode);
                setStatus("전에 고른 디시 계정이 서버에 없어요. 유동으로 달려면 다시 등록을 눌러 주세요.", "error");
                return;
            }
            setStatus("작성자를 다시 골라 주세요. 전에 고른 계정이 서버에 없어요.", "error");
            return;
        }
        // 보낼 때 고른 계정을 붙잡아 둔다. 결과를 받기 전에 선택을 바꿔도 바뀌지 않는다.
        var account = accountSlot();
        var values = { content: el.content.value, nickname: el.nickname.value, password: el.password.value, account: Boolean(account) };
        var problem = validateComment(values, limits);
        if (problem) {
            showError(problem.field, problem.message);
            return;
        }
        var body = account
            ? { board: board, pid: pid, content: values.content, mode: "account", account: account }
            : { board: board, pid: pid, content: values.content, nickname: values.nickname.trim(), password: values.password };
        if (kind) {
            body.kind = kind;
        }
        if (state.parent) {
            body.parent_id = state.parent.id;
            body.parent_author_code = state.parent.authorCode;
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
        if (!account) {
            saveIdentity();
        }
        state.sending = true;
        state.sentAnswer = Boolean(answer);
        setStatus("댓글을 보내고 있어요.", "busy");
        sync();
        post.postJson("/read/comment", body, 45000).then(function (result) {
            state.sending = false;
            sync();
            handle(post.interpret(result.status, result.payload), { content: values.content, nickname: body.nickname, account: account });
            sync();
        });
    }

    form.addEventListener("submit", function (event) {
        event.preventDefault();
        submit();
    });
    el.content.addEventListener("input", function () {
        updateLength();
        if (el.content.getAttribute("aria-invalid") === "true") {
            el.content.removeAttribute("aria-invalid");
            el.contentError.hidden = true;
        }
    });
    el.content.addEventListener("keydown", function (event) {
        if (event.key === "Enter" && (event.metaKey || event.ctrlKey) && !event.isComposing) {
            event.preventDefault();
            submit();
        }
    });
    [el.nickname, el.password].forEach(function (input) {
        input.addEventListener("input", function () {
            input.removeAttribute("aria-invalid");
            el.identityError.hidden = true;
        });
    });
    el.rememberPassword.addEventListener("change", function () {
        el.passwordHint.hidden = !el.rememberPassword.checked;
    });
    var code = document.getElementById("comment-captcha-code");
    if (code) {
        code.addEventListener("keydown", function (event) {
            if (event.key === "Enter" && !event.isComposing) {
                event.preventDefault();
                submit();
            }
        });
    }
    el.cancel.addEventListener("click", function () {
        setParent(null);
        el.content.focus();
    });
    if (list) {
        list.addEventListener("click", function (event) {
            var button = event.target.closest ? event.target.closest("[data-comment-reply]") : null;
            var item = button ? button.closest(".comment-item") : null;
            if (!item) {
                return;
            }
            if (state.parent && state.parent.item === item) {
                setParent(null);
                return;
            }
            setParent(item);
        });
    }
    // 답글을 달던 댓글이 지워지면 입력칸을 원래 자리로 돌린다.
    document.addEventListener("mirror:comment-removed", function (event) {
        if (state.parent && event.detail && state.parent.item === event.detail.item) {
            setParent(null);
        }
    });
    el.modeInputs.forEach(function (input) {
        input.addEventListener("change", function () {
            if (!input.checked) {
                return;
            }
            state.mode = post.normalizeMode(input.value);
            post.saveMode(storage, state.mode);
            clearErrors();
            applyMode();
        });
    });
    restoreIdentity();
    applyMode();
    if (el.modeInputs.length && typeof post.labelModes === "function") {
        post.labelModes(el.modeInputs);
    }
    updateLength();
    sync();
})(typeof window !== "undefined" ? window : globalThis);
