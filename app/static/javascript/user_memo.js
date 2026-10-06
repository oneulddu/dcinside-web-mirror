(function (root) {
    "use strict";

    // 작성자별 메모. 작성자마다 localStorage 키를 따로 두어 다른 탭의 저장과 서로 덮어쓰지 않는다.
    var PREFIX = "mirror_user_memo_v1:";
    var VERSION = 1;
    var MAX_MEMOS = 200;
    var MAX_MEMO_LENGTH = 120;
    var MAX_NAME_LENGTH = 80;
    var MAX_CODE_LENGTH = 64;
    var MAX_RAW_LENGTH = 4096;
    var CONTROL_CHARS = /[\u0000-\u001F\u007F-\u009F]/;
    var GUEST_CODE = /^\d{1,3}\.\d{1,3}$/;

    function nfc(value) {
        var text = String(value == null ? "" : value);
        return typeof text.normalize === "function" ? text.normalize("NFC") : text;
    }

    function normalize(value) {
        return nfc(value).trim().toLowerCase();
    }

    function codePoints(text) {
        return Array.from(text).length;
    }

    // 식별 코드가 있으면 코드, 없으면 원본 닉네임으로 구분한다. 둘 다 없으면 메모를 쓸 수 없다.
    function identityFor(author) {
        var code = normalize(author && author.code);
        if (code && !/\s/.test(code) && codePoints(code) <= MAX_CODE_LENGTH && !CONTROL_CHARS.test(code)) {
            return "code:" + code;
        }
        var name = normalize(author && author.searchName);
        if (name && codePoints(name) <= MAX_NAME_LENGTH && !CONTROL_CHARS.test(name)) {
            return "name:" + name;
        }
        return null;
    }

    function storageKey(identity) {
        return PREFIX + encodeURIComponent(identity);
    }

    function cleanMemo(value) {
        var text = nfc(value).replace(/[\r\n\t]+/g, " ").trim();
        if (!text) {
            return { ok: false, error: "메모를 입력해 주세요." };
        }
        if (codePoints(text) > MAX_MEMO_LENGTH) {
            return { ok: false, error: MAX_MEMO_LENGTH + "자 이하로 입력해 주세요." };
        }
        if (CONTROL_CHARS.test(text)) {
            return { ok: false, error: "쓸 수 없는 문자가 들어 있어요." };
        }
        return { ok: true, value: text };
    }

    function buildEntry(identity, nickname, memo, now) {
        var name = nfc(nickname).trim();
        if (codePoints(name) > MAX_NAME_LENGTH) {
            name = Array.from(name).slice(0, MAX_NAME_LENGTH).join("");
        }
        return { version: VERSION, identity: identity, nickname: name, memo: memo, updatedAt: now };
    }

    // 키와 내용이 맞지 않거나 형식이 다르면 null. 원래 저장값은 그대로 둔다.
    function parseEntry(key, raw) {
        if (typeof raw !== "string" || !raw || raw.length > MAX_RAW_LENGTH) {
            return null;
        }
        var data;
        try {
            data = JSON.parse(raw);
        } catch (err) {
            return null;
        }
        if (!data || typeof data !== "object" || Array.isArray(data) || data.version !== VERSION) {
            return null;
        }
        if (typeof data.identity !== "string" || storageKey(data.identity) !== key) {
            return null;
        }
        if (!/^(code|name):./.test(data.identity)) {
            return null;
        }
        if (typeof data.nickname !== "string" || codePoints(data.nickname) > MAX_NAME_LENGTH) {
            return null;
        }
        if (typeof data.updatedAt !== "number" || !isFinite(data.updatedAt)) {
            return null;
        }
        var memo = cleanMemo(data.memo);
        if (!memo.ok || memo.value !== data.memo) {
            return null;
        }
        return { version: VERSION, identity: data.identity, nickname: data.nickname, memo: data.memo, updatedAt: data.updatedAt };
    }

    function isGuestCode(code) {
        return GUEST_CODE.test(String(code || "").trim());
    }

    var api = {
        PREFIX: PREFIX,
        MAX_MEMOS: MAX_MEMOS,
        MAX_MEMO_LENGTH: MAX_MEMO_LENGTH,
        identityFor: identityFor,
        storageKey: storageKey,
        cleanMemo: cleanMemo,
        buildEntry: buildEntry,
        parseEntry: parseEntry
    };
    root.MirrorUserMemo = api;

    var document = root.document;
    if (!document || typeof document.querySelector !== "function") {
        return;
    }

    // ---------- 저장소 ----------

    var state = {
        memos: Object.create(null),      // identity -> entry (저장소에서 읽은 값)
        overrides: Object.create(null),  // 저장에 실패해 이 페이지에만 적용한 값(null은 삭제)
        brokenKeys: [],
        dialog: null,
        target: null,
        opener: null
    };

    function storage() {
        try {
            return root.localStorage;
        } catch (err) {
            return null;
        }
    }

    function loadAll() {
        var store = storage();
        state.memos = Object.create(null);
        state.brokenKeys = [];
        if (!store) {
            return;
        }
        var keys = [];
        try {
            for (var i = 0; i < store.length; i += 1) {
                var key = store.key(i);
                if (key && key.indexOf(PREFIX) === 0) {
                    keys.push(key);
                }
            }
        } catch (err) {
            return;
        }
        keys.forEach(function (key) {
            var raw = null;
            try {
                raw = store.getItem(key);
            } catch (err) {
                raw = null;
            }
            var entry = parseEntry(key, raw);
            if (entry) {
                state.memos[entry.identity] = entry;
            } else {
                state.brokenKeys.push(key);
            }
        });
    }

    function memoFor(identity) {
        if (!identity) {
            return null;
        }
        if (Object.prototype.hasOwnProperty.call(state.overrides, identity)) {
            return state.overrides[identity];
        }
        return state.memos[identity] || null;
    }

    function allMemos() {
        var map = Object.create(null);
        Object.keys(state.memos).forEach(function (identity) {
            map[identity] = state.memos[identity];
        });
        Object.keys(state.overrides).forEach(function (identity) {
            if (state.overrides[identity]) {
                map[identity] = state.overrides[identity];
            } else {
                delete map[identity];
            }
        });
        return Object.keys(map).map(function (identity) { return map[identity]; })
            .sort(function (a, b) { return b.updatedAt - a.updatedAt; });
    }

    function authorOf(element) {
        return {
            author: element.getAttribute("data-author") || "",
            code: element.getAttribute("data-author-code") || "",
            searchName: element.getAttribute("data-author-search-name") || ""
        };
    }

    // ---------- 화면 표시 ----------

    var AUTHOR_SELECTOR = "#board-list [data-author], #related-list [data-author], .article-meta [data-author], .comment-meta [data-author]";

    function renderMemos(scope) {
        var elements = (scope && scope.querySelectorAll ? scope : document).querySelectorAll(AUTHOR_SELECTOR);
        if (scope && scope.matches && scope.matches("[data-author]")) {
            elements = [scope];
        }
        Array.prototype.forEach.call(elements, function (element) {
            var entry = memoFor(identityFor(authorOf(element)));
            var next = element.nextElementSibling;
            var existing = next && next.classList.contains("author-memo") ? next : null;
            if (!entry) {
                if (existing) {
                    existing.remove();
                }
                return;
            }
            if (!existing) {
                existing = document.createElement("span");
                existing.className = "author-memo";
                var label = document.createElement("span");
                label.className = "sr-only";
                label.textContent = "메모: ";
                existing.appendChild(label);
                existing.appendChild(document.createElement("span"));
                element.insertAdjacentElement("afterend", existing);
            }
            existing.lastChild.textContent = entry.memo;
            existing.title = "메모: " + entry.memo;
        });
    }

    // ---------- 편집 대화상자 ----------

    var DIALOG_HTML =
        '<div class="ufd-head">' +
        '<h2 id="user-memo-title">메모</h2>' +
        '<button type="button" class="icon-btn ufd-close" aria-label="닫기"><svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M6 6l12 12M18 6L6 18"/></svg></button>' +
        "</div>" +
        '<div class="ufd-body">' +
        '<p class="umd-who"></p>' +
        '<p class="ufd-hint umd-scope" id="umd-scope"></p>' +
        '<form class="umd-form" novalidate>' +
        '<div class="ufd-add-row">' +
        '<input class="board-search-input umd-input" type="text" autocomplete="off" enterkeyhint="done" placeholder="예: 질문에 잘 답해 줌" aria-label="메모 내용" aria-describedby="umd-scope umd-error">' +
        '<button type="submit" class="board-search-submit">저장</button>' +
        "</div>" +
        '<p class="ufd-field-error" id="umd-error" hidden></p>' +
        "</form>" +
        '<div class="umd-conflict" hidden><p>다른 탭에서 이 메모가 바뀌었어요.</p><button type="button" class="user-filter-reveal umd-reload">바뀐 메모 불러오기</button></div>' +
        '<button type="button" class="user-filter-reveal umd-delete" hidden>이 메모 삭제</button>' +
        '<h3 class="ufd-section-title">저장한 메모 <span class="ufd-count"></span></h3>' +
        '<p class="ufd-empty">저장한 메모가 없어요.</p>' +
        '<ul class="ufd-rules umd-list"></ul>' +
        '<div class="umd-broken" hidden><p class="ufd-hint umd-broken-text"></p><button type="button" class="user-filter-reveal umd-broken-clear">읽지 못한 메모 지우기</button></div>' +
        '<p class="ufd-note">메모는 이 브라우저에만 저장되고 다른 사람에게 보이지 않아요.</p>' +
        '<p class="ufd-status" role="status" aria-live="polite"></p>' +
        "</div>";

    function part(selector) {
        return state.dialog ? state.dialog.querySelector(selector) : null;
    }

    function setStatus(message) {
        var status = part(".ufd-status");
        if (status) {
            status.textContent = message || "";
        }
    }

    function setError(message) {
        var error = part("#umd-error");
        error.hidden = !message;
        error.textContent = message || "";
        part(".umd-input").setAttribute("aria-invalid", message ? "true" : "false");
    }

    function scopeText(target) {
        if (target.code && isGuestCode(target.code)) {
            return "IP 앞자리 " + target.code + "를 쓰는 다른 유동에게도 이 메모가 보여요.";
        }
        if (target.code) {
            return "식별 코드 " + target.code + " 기준으로 모든 게시판에서 보여요.";
        }
        return "식별 코드가 없어 닉네임 기준이에요. 같은 닉네임을 쓰는 사람에게도 보여요.";
    }

    function describeEntry(entry) {
        var value = entry.identity.slice(5);
        var name = entry.nickname || "";
        if (entry.identity.indexOf("code:") === 0) {
            return name ? name + "(" + value + ")" : value;
        }
        return name || value;
    }

    function renderList() {
        var list = part(".umd-list");
        var focused = document.activeElement;
        var focusedKey = focused && list.contains(focused) ? focused.getAttribute("data-memo-identity") : null;
        list.textContent = "";
        var entries = allMemos();
        entries.forEach(function (entry) {
            var li = document.createElement("li");
            li.className = "ufd-rule umd-item";
            var who = document.createElement("span");
            who.className = "ufd-rule-type umd-item-who";
            who.textContent = describeEntry(entry);
            var memo = document.createElement("span");
            memo.className = "ufd-rule-value";
            memo.textContent = entry.memo;
            var remove = document.createElement("button");
            remove.type = "button";
            remove.className = "ufd-rule-remove";
            remove.textContent = "삭제";
            remove.setAttribute("data-memo-identity", entry.identity);
            remove.setAttribute("aria-label", describeEntry(entry) + " 메모 삭제");
            remove.addEventListener("click", function () {
                var buttons = Array.prototype.slice.call(list.querySelectorAll(".ufd-rule-remove"));
                var index = buttons.indexOf(remove);
                deleteMemo(entry.identity);
                var after = list.querySelectorAll(".ufd-rule-remove");
                (after[Math.min(index, after.length - 1)] || part(".umd-input")).focus();
            });
            li.appendChild(who);
            li.appendChild(memo);
            li.appendChild(remove);
            list.appendChild(li);
        });
        part(".ufd-count").textContent = entries.length ? String(entries.length) : "";
        part(".ufd-empty").hidden = entries.length > 0;
        list.hidden = entries.length === 0;
        var broken = part(".umd-broken");
        broken.hidden = state.brokenKeys.length === 0;
        part(".umd-broken-text").textContent = "읽지 못한 메모 " + state.brokenKeys.length + "개가 있어요. 형식이 맞지 않아 표시하지 않아요.";
        if (focusedKey !== null) {
            var same = Array.prototype.filter.call(list.querySelectorAll(".ufd-rule-remove"), function (button) {
                return button.getAttribute("data-memo-identity") === focusedKey;
            })[0];
            (same || part(".umd-input")).focus();
        }
    }

    function renderTarget() {
        var target = state.target;
        var form = part(".umd-form");
        if (!target) {
            part(".umd-who").textContent = "";
            part(".umd-scope").textContent = "";
            form.hidden = true;
            part(".umd-delete").hidden = true;
            return;
        }
        form.hidden = false;
        part(".umd-who").textContent = target.author + (target.code ? "(" + target.code + ")" : "");
        part(".umd-scope").textContent = scopeText(target);
        part(".umd-delete").hidden = !memoFor(target.identity);
    }

    function writeEntry(identity, entry) {
        var store = storage();
        try {
            if (!store) {
                throw new Error("storage unavailable");
            }
            if (entry) {
                store.setItem(storageKey(identity), JSON.stringify(entry));
                state.memos[identity] = entry;
            } else {
                store.removeItem(storageKey(identity));
                delete state.memos[identity];
            }
            delete state.overrides[identity];
            return true;
        } catch (err) {
            state.overrides[identity] = entry || null;
            return false;
        }
    }

    function afterChange(message, saved) {
        renderMemos(document);
        renderTarget();
        renderList();
        setStatus(saved ? message : "저장할 수 없어 이 페이지에서만 적용됐어요.");
    }

    function saveMemo() {
        var target = state.target;
        if (!target) {
            return;
        }
        var input = part(".umd-input");
        var cleaned = cleanMemo(input.value);
        if (!cleaned.ok) {
            setError(cleaned.error);
            input.focus();
            return;
        }
        var isNew = !memoFor(target.identity);
        if (isNew && allMemos().length >= MAX_MEMOS) {
            setError("메모는 " + MAX_MEMOS + "개까지 저장할 수 있어요. 안 쓰는 메모를 지워 주세요.");
            input.focus();
            return;
        }
        setError("");
        part(".umd-conflict").hidden = true;
        var entry = buildEntry(target.identity, target.author, cleaned.value, Date.now());
        input.value = cleaned.value;
        afterChange(isNew ? "메모를 저장했어요." : "메모를 고쳤어요.", writeEntry(target.identity, entry));
    }

    function deleteMemo(identity) {
        var saved = writeEntry(identity, null);
        if (state.target && state.target.identity === identity) {
            part(".umd-input").value = "";
            part(".umd-conflict").hidden = true;
        }
        afterChange("메모를 삭제했어요.", saved);
    }

    function buildDialog() {
        var dialog = document.createElement("dialog");
        dialog.className = "user-filter-dialog user-memo-dialog";
        dialog.setAttribute("aria-labelledby", "user-memo-title");
        dialog.innerHTML = DIALOG_HTML;
        document.body.appendChild(dialog);
        state.dialog = dialog;
        part(".ufd-close").addEventListener("click", function () {
            dialog.close();
        });
        dialog.addEventListener("click", function (event) {
            if (event.target === dialog) {
                dialog.close();
            }
        });
        dialog.addEventListener("close", restoreFocus);
        part(".umd-form").addEventListener("submit", function (event) {
            event.preventDefault();
            saveMemo();
        });
        part(".umd-input").addEventListener("input", function () {
            setError("");
        });
        part(".umd-delete").addEventListener("click", function () {
            if (state.target) {
                deleteMemo(state.target.identity);
                part(".umd-input").focus();
            }
        });
        part(".umd-reload").addEventListener("click", function () {
            var entry = state.target ? memoFor(state.target.identity) : null;
            part(".umd-input").value = entry ? entry.memo : "";
            part(".umd-conflict").hidden = true;
            part(".umd-input").focus();
        });
        part(".umd-broken-clear").addEventListener("click", function () {
            var store = storage();
            state.brokenKeys.forEach(function (key) {
                try {
                    store.removeItem(key);
                } catch (err) {
                    // 지우지 못한 항목은 다음에 다시 보인다.
                }
            });
            loadAll();
            renderList();
            setStatus("읽지 못한 메모를 지웠어요.");
            part(".umd-input").focus();
        });
        return dialog;
    }

    function isVisible(element) {
        return !!(element && element.isConnected && element.getClientRects().length);
    }

    function restoreFocus() {
        var target = state.opener;
        state.opener = null;
        if (!isVisible(target)) {
            target = document.querySelector("[data-user-filter-header]");
        }
        if (target && typeof target.focus === "function") {
            target.focus();
        }
    }

    function openEditor(button) {
        var author = authorOf(button);
        var identity = identityFor(author);
        var dialog = state.dialog || buildDialog();
        state.opener = button || null;
        state.target = identity ? { identity: identity, author: author.author, code: author.code } : null;
        setError("");
        setStatus("");
        part(".umd-conflict").hidden = true;
        var entry = identity ? memoFor(identity) : null;
        part(".umd-input").value = entry ? entry.memo : "";
        renderTarget();
        renderList();
        if (!dialog.open) {
            if (typeof dialog.showModal === "function") {
                dialog.showModal();
            } else {
                dialog.setAttribute("open", "");
            }
        }
        (identity ? part(".umd-input") : part(".ufd-close")).focus();
    }

    function refreshFromStorage(changedKey) {
        var before = state.target ? JSON.stringify(memoFor(state.target.identity)) : null;
        loadAll();
        renderMemos(document);
        if (state.dialog && state.dialog.open) {
            var after = state.target ? JSON.stringify(memoFor(state.target.identity)) : null;
            var sameKey = state.target && (!changedKey || changedKey === storageKey(state.target.identity));
            // 편집 중인 입력은 덮어쓰지 않고 바뀌었다고만 알린다.
            if (sameKey && before !== after) {
                part(".umd-conflict").hidden = false;
            }
            renderTarget();
            renderList();
        }
    }

    api.openEditor = openEditor;
    api.memoFor = function (element) {
        return memoFor(identityFor(authorOf(element)));
    };
    api.canMemo = function (element) {
        return !!identityFor(authorOf(element));
    };
    api.render = renderMemos;

    function boot() {
        loadAll();
        renderMemos(document);
        document.addEventListener("mirror:board-refreshed", function (event) {
            renderMemos((event.detail && event.detail.root) || document);
        });
        var related = document.getElementById("related-list");
        if (related && typeof root.MutationObserver === "function") {
            new root.MutationObserver(function (mutations) {
                mutations.forEach(function (mutation) {
                    Array.prototype.forEach.call(mutation.addedNodes, function (node) {
                        if (node.nodeType === 1 && !node.classList.contains("author-memo")) {
                            renderMemos(node);
                        }
                    });
                });
            }).observe(related, { childList: true });
        }
        root.addEventListener("storage", function (event) {
            if (!event.key || event.key.indexOf(PREFIX) === 0) {
                refreshFromStorage(event.key);
            }
        });
        root.addEventListener("pageshow", function (event) {
            if (event.persisted) {
                refreshFromStorage(null);
            }
        });
        document.addEventListener("visibilitychange", function () {
            if (document.visibilityState === "visible") {
                refreshFromStorage(null);
            }
        });
    }

    boot();
})(typeof window !== "undefined" ? window : globalThis);
