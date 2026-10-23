(function (root) {
    "use strict";

    // 유동 글·댓글을 쓸 때 정한 비밀번호로 지운다. 결과를 모르면 자동으로 다시 보내지 않는다.
    var IDENTITY_KEY = "mirror_write_identity_v1";

    function validatePassword(value, limits) {
        var length = Array.from(String(value == null ? "" : value)).length;
        if (!String(value || "").trim() || length < limits.password_min || length > limits.password_max) {
            return "비밀번호는 " + limits.password_min + "~" + limits.password_max + "자로 입력해 주세요.";
        }
        return null;
    }

    root.MirrorPostManage = { validatePassword: validatePassword };

    var document = root.document;
    var post = root.MirrorDcPost;
    if (!document || !post || typeof post.postJson !== "function") {
        return;
    }
    var shell = document.querySelector(".read-shell[data-board-context]");
    if (!shell) {
        return;
    }
    var board = shell.getAttribute("data-board") || "";
    var kind = shell.getAttribute("data-kind") || "";
    var pid = shell.getAttribute("data-pid") || "";
    var limits = {
        password_min: Number(shell.getAttribute("data-password-min")) || 2,
        password_max: Number(shell.getAttribute("data-password-max")) || 20
    };
    var list = document.getElementById("comment-list") || document.querySelector(".comment-shell .comment-list");

    var DIALOG_HTML =
        '<div class="ufd-head">' +
        '<h2 id="owner-dialog-title"></h2>' +
        '<button type="button" class="icon-btn ufd-close" aria-label="닫기"><svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M6 6l12 12M18 6L6 18"/></svg></button>' +
        "</div>" +
        '<div class="ufd-body">' +
        '<form class="owner-form" novalidate>' +
        '<p class="ufd-hint owner-hint" id="owner-hint"></p>' +
        '<label class="sr-only" for="owner-password">비밀번호</label>' +
        '<input class="write-input" id="owner-password" type="password" autocomplete="current-password" placeholder="비밀번호" aria-describedby="owner-hint owner-error">' +
        '<p class="write-error" id="owner-error" hidden></p>' +
        '<p class="write-status" id="owner-status" role="status" aria-live="polite"></p>' +
        '<div class="write-actions owner-actions">' +
        '<button type="button" class="write-cancel owner-cancel">취소</button>' +
        '<button type="submit" class="write-submit owner-submit">삭제</button>' +
        "</div>" +
        "</form>" +
        "</div>";

    // target: {type: "post"} 또는 {type: "comment", id, item}
    var state = { dialog: null, target: null, opener: null, sending: false, retryUntil: 0, retryTimer: null };

    function part(selector) {
        return state.dialog.querySelector(selector);
    }

    function setStatus(text, tone, link) {
        var box = part("#owner-status");
        box.textContent = text || "";
        box.className = "write-status" + (tone ? " is-" + tone : "");
        if (link) {
            box.appendChild(document.createTextNode(" "));
            var anchor = document.createElement("a");
            anchor.href = link.href;
            anchor.textContent = link.text;
            if (link.newTab) {
                anchor.target = "_blank";
                anchor.rel = "noopener";
            }
            box.appendChild(anchor);
        }
    }

    function setError(message) {
        var box = part("#owner-error");
        var input = part("#owner-password");
        box.textContent = message || "";
        box.hidden = !message;
        if (message && input.hidden) {
            return;
        }
        if (message) {
            input.setAttribute("aria-invalid", "true");
            input.focus();
        } else {
            input.removeAttribute("aria-invalid");
        }
    }

    function sync() {
        var waiting = state.retryUntil > Date.now();
        var submit = part(".owner-submit");
        part("#owner-password").disabled = state.sending;
        submit.disabled = state.sending || waiting;
        submit.setAttribute("aria-busy", state.sending ? "true" : "false");
        submit.textContent = state.sending ? "삭제 중" : "삭제";
    }

    function savedPassword() {
        try {
            var saved = JSON.parse(root.localStorage.getItem(IDENTITY_KEY) || "null");
            return saved && typeof saved.password === "string" ? saved.password : "";
        } catch (err) {
            return "";
        }
    }

    function isVisible(element) {
        return !!(element && element.isConnected && element.getClientRects().length);
    }

    function close() {
        // 보내는 중에는 닫지 않는다. 결과를 놓치면 다시 누를 수 있기 때문이다.
        if (!state.sending && state.dialog.open) {
            state.dialog.close();
        }
    }

    function buildDialog() {
        var dialog = document.createElement("dialog");
        dialog.className = "user-filter-dialog owner-dialog";
        dialog.setAttribute("aria-labelledby", "owner-dialog-title");
        dialog.innerHTML = DIALOG_HTML;
        document.body.appendChild(dialog);
        state.dialog = dialog;
        part(".ufd-close").addEventListener("click", close);
        part(".owner-cancel").addEventListener("click", close);
        dialog.addEventListener("click", function (event) {
            if (event.target === dialog) {
                close();
            }
        });
        dialog.addEventListener("cancel", function (event) {
            if (state.sending) {
                event.preventDefault();
            }
        });
        dialog.addEventListener("close", function () {
            var target = state.opener;
            state.opener = null;
            if (!isVisible(target)) {
                target = document.getElementById("comment-title");
            }
            if (target && typeof target.focus === "function") {
                target.focus();
            }
        });
        part(".owner-form").addEventListener("submit", function (event) {
            event.preventDefault();
            submit();
        });
        part("#owner-password").addEventListener("input", function () {
            setError("");
        });
        return dialog;
    }

    function open(target, opener) {
        var dialog = state.dialog || buildDialog();
        state.target = target;
        state.opener = opener || null;
        var isPost = target.type === "post";
        var noun = isPost ? "글" : "댓글";
        var input = part("#owner-password");
        part("#owner-dialog-title").textContent = noun + " 삭제";
        // 디시 계정으로 쓴 글·댓글은 비밀번호 없이 확인만 받는다. 내 것인지는 디시가 판단한다.
        part("#owner-hint").textContent = target.account
            ? "디시 계정으로 쓴 " + noun + "이에요. 삭제하면 되돌릴 수 없어요."
            : noun + "을 쓸 때 정한 비밀번호를 입력하세요. 삭제하면 되돌릴 수 없어요.";
        input.hidden = Boolean(target.account);
        input.value = target.account ? "" : savedPassword();
        setError("");
        setStatus("");
        sync();
        if (!dialog.open) {
            if (typeof dialog.showModal === "function") {
                dialog.showModal();
            } else {
                dialog.setAttribute("open", "");
            }
        }
        if (target.account) {
            part(".owner-submit").focus();
            return;
        }
        input.focus();
        input.select();
    }

    function adjustCount(delta) {
        var count = document.querySelector(".comment-shell .comment-count");
        if (count && /^[0-9]+$/.test(count.textContent.trim())) {
            count.textContent = String(Math.max(0, Number(count.textContent.trim()) + delta));
        }
    }

    // 답글은 부모 바로 뒤에 이어진다(답글 입력칸 자리는 건너뛴다). 스크랩한 답글의 data-parent-id는
    // 0일 때가 있어 번호로는 찾지 않는다.
    function hasReplies(item) {
        if (item.classList.contains("is-reply")) {
            return false;
        }
        var next = item.nextElementSibling;
        while (next && next.classList.contains("comment-reply-slot")) {
            next = next.nextElementSibling;
        }
        return Boolean(next && next.classList.contains("comment-item") && next.classList.contains("is-reply"));
    }

    // 답글이 달린 댓글은 자리를 남겨 답글이 다른 댓글 밑으로 붙어 보이지 않게 한다.
    function removeComment(item) {
        var keepPlace = hasReplies(item);
        document.dispatchEvent(new CustomEvent("mirror:comment-removed", { detail: { item: item } }));
        if (keepPlace) {
            var main = item.querySelector(".comment-main");
            main.textContent = "";
            var text = document.createElement("p");
            text.className = "comment-deleted";
            text.textContent = "삭제된 댓글이에요.";
            main.appendChild(text);
            Array.prototype.forEach.call(item.querySelectorAll("[data-comment-delete], [data-comment-reply]"), function (button) {
                button.remove();
            });
            item.classList.add("is-deleted");
        } else {
            item.remove();
        }
        adjustCount(-1);
        if (!list.querySelector(":scope > .comment-item")) {
            var empty = document.createElement("li");
            empty.className = "empty-row";
            empty.textContent = "아직 댓글이 없어요.";
            list.appendChild(empty);
        }
    }

    function startRetry(seconds, message) {
        clearTimeout(state.retryTimer);
        state.retryUntil = Date.now() + seconds * 1000;
        setStatus((message ? message + " " : "") + seconds + "초 뒤에 다시 삭제할 수 있어요.", "error");
        state.retryTimer = setTimeout(function () {
            state.retryUntil = 0;
            sync();
        }, seconds * 1000);
    }

    function handle(outcome, target) {
        var payload = outcome.payload || {};
        var message = typeof payload.message === "string" ? payload.message : "";
        if (outcome.action === "visitor_check_required") {
            setStatus(message || "아무것도 전송하지 않았어요. 입력을 보관한 뒤 페이지를 새로고침해 주세요.", "error");
            return;
        }
        var noun = target.type === "post" ? "글" : "댓글";
        if (outcome.action === "success") {
            if (target.type === "post") {
                setStatus(message || "글을 삭제했어요. 목록으로 이동해요.", "success");
                var next = post.safePath(payload.board_url);
                root.location.assign(next || "/");
                return;
            }
            state.dialog.close();
            removeComment(target.item);
            // 댓글 입력칸의 상태 줄에 알린다(글쓰기가 열려 있을 때만 이 스크립트가 실린다).
            var announce = document.getElementById("comment-status");
            if (announce) {
                announce.textContent = message || "댓글을 삭제했어요.";
                announce.className = "write-status is-success";
            }
            return;
        }
        if (outcome.action === "invalid") {
            if (payload.field === "password") {
                setError(message);
                setStatus("");
                return;
            }
            setStatus(message || "입력한 내용을 확인해 주세요.", "error");
            return;
        }
        if (outcome.action === "rejected") {
            setStatus(message || "디시가 " + noun + " 삭제를 받지 않았어요.", "error");
            if (!target.account) {
                part("#owner-password").focus();
            }
            return;
        }
        if (outcome.action === "login_needed") {
            setStatus(message || "디시 계정에 다시 로그인해야 해요. " + noun + "은 지우지 않았어요.", "error", post.accountLink(payload));
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
        if (outcome.action === "unavailable" && message) {
            setStatus(message, "error");
            return;
        }
        setStatus(message || noun + " 삭제 결과를 확인하지 못했어요. 다시 누르기 전에 글을 새로 열어 확인해 주세요.", "error");
    }

    function submit() {
        if (state.sending || state.retryUntil > Date.now() || !state.target) {
            return;
        }
        var target = state.target;
        var body = { board: board, pid: pid, mode: "account", account: target.account };
        if (!target.account) {
            var password = part("#owner-password").value;
            var problem = validatePassword(password, limits);
            if (problem) {
                setError(problem);
                return;
            }
            body = { board: board, pid: pid, password: password };
        }
        setError("");
        if (kind) {
            body.kind = kind;
        }
        var url = "/read/delete";
        if (target.type === "comment") {
            url = "/read/comment/delete";
            body.comment_id = target.id;
        }
        state.sending = true;
        setStatus("삭제하고 있어요.", "busy");
        sync();
        post.postJson(url, body, 45000).then(function (result) {
            state.sending = false;
            sync();
            handle(post.interpret(result.status, result.payload), target);
        });
    }

    // 계정 버튼이면 그 버튼에 적힌 계정 번호, 유동이면 빈 값.
    function ownerAccount(button) {
        if (button.getAttribute("data-owner") !== "account") {
            return "";
        }
        return button.getAttribute("data-account") || "1";
    }

    function bindPostButton(button) {
        button.addEventListener("click", function () {
            open({ type: "post", account: ownerAccount(button) }, button);
        });
    }

    var postButton = document.querySelector("[data-post-delete]");
    if (postButton) {
        bindPostButton(postButton);
    }
    if (list) {
        list.addEventListener("click", function (event) {
            var button = event.target.closest ? event.target.closest("[data-comment-delete]") : null;
            var item = button ? button.closest(".comment-item") : null;
            var id = item ? item.getAttribute("data-comment-id") || "" : "";
            if (!item || !/^[1-9][0-9]*$/.test(id)) {
                return;
            }
            open({ type: "comment", id: id, item: item, account: ownerAccount(button) }, button);
        });
    }

    function ownerButton(className, label, ariaLabel, slot) {
        var button = document.createElement("button");
        button.className = className;
        button.type = "button";
        button.setAttribute("data-owner", "account");
        button.setAttribute("data-account", slot);
        button.setAttribute("aria-haspopup", "dialog");
        if (ariaLabel) {
            button.setAttribute("aria-label", ariaLabel);
        }
        button.textContent = label;
        return button;
    }

    // 식별 코드가 로그인한 계정의 코드와 같은 글·댓글에 수정·삭제를 붙인다. owners: {코드: 계정 번호}.
    // 화면에 보이는 것일 뿐, 실제로 지우거나 고칠 수 있는지는 디시가 판단한다.
    function markOwned(owners) {
        var holder = document.querySelector("[data-account-owner-slot]");
        var postAuthor = document.querySelector(".article-meta .author-text");
        var postAccount = postAuthor ? owners[postAuthor.getAttribute("data-author-code") || ""] : "";
        if (holder && postAccount) {
            var params = new URLSearchParams({ board: board, pid: pid, mode: "account", account: postAccount });
            if (kind) {
                params.set("kind", kind);
            }
            var edit = document.createElement("a");
            edit.className = "comment-reply-btn article-owner-action";
            edit.href = "/write?" + params.toString();
            edit.textContent = "수정";
            var remove = ownerButton("comment-reply-btn article-owner-action", "삭제", "", postAccount);
            remove.setAttribute("data-post-delete", "");
            holder.appendChild(edit);
            holder.appendChild(remove);
            holder.hidden = false;
            bindPostButton(remove);
        }
        if (!list) {
            return;
        }
        Array.prototype.forEach.call(list.querySelectorAll(".comment-item[data-comment-id]"), function (item) {
            var author = item.querySelector(".comment-meta .author-text");
            var meta = item.querySelector(".comment-meta");
            var account = author ? owners[author.getAttribute("data-author-code") || ""] : "";
            if (!account || !meta || item.classList.contains("is-deleted") || meta.querySelector("[data-comment-delete]")
                    || !/^[1-9][0-9]*$/.test(item.getAttribute("data-comment-id") || "")) {
                return;
            }
            var button = ownerButton("comment-reply-btn", "삭제", "내 댓글 삭제", account);
            button.setAttribute("data-comment-delete", "");
            meta.appendChild(button);
        });
    }

    // 상태 조회는 디시에 요청하지 않는다. 로그인한 적이 없으면 계정 글 버튼은 보이지 않는다.
    // 두 계정의 코드가 같으면 계정 1로 붙인다. 실패해도 다른 계정으로 다시 보내지 않는다.
    if (shell.getAttribute("data-account-configured") !== null && typeof post.accountStatus === "function") {
        post.accountStatus().then(function (status) {
            var accounts = post.accountList(status);
            var owners = {};
            ["1", "2"].forEach(function (slot) {
                var item = accounts[slot];
                var userId = item && item.logged_in === true ? String(item.user_id || "") : "";
                if (/^[A-Za-z0-9_]{1,40}$/.test(userId) && !Object.prototype.hasOwnProperty.call(owners, userId)) {
                    owners[userId] = slot;
                }
            });
            if (Object.keys(owners).length) {
                markOwned(owners);
            }
        });
    }
})(typeof window !== "undefined" ? window : globalThis);
