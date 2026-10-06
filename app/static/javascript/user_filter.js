(function (root) {
    "use strict";

    // 디시 게시판 전용 차단 필터. 규칙은 이 브라우저의 localStorage에만 저장한다.
    // 판정 함수는 DOM 없이도 동작하도록 분리해 Node 테스트에서 그대로 쓴다.
    var STORAGE_KEY = "mirror_user_filter_v1";
    var VERSION = 1;
    var MAX_RULES = 200;
    var MAX_VALUE_LENGTH = 64;
    var RULE_TYPES = { nickname: "닉네임", authorCode: "식별 코드", titleKeyword: "제목 단어" };
    var GUEST_CODE = /^\d{1,3}\.\d{1,3}$/;
    var CONTROL_CHARS = /[\u0000-\u001F\u007F-\u009F]/;

    function defaultSettings() {
        return { version: VERSION, enabled: true, blockGuests: false, rules: [] };
    }

    function nfc(value) {
        var text = String(value == null ? "" : value);
        return typeof text.normalize === "function" ? text.normalize("NFC") : text;
    }

    function normalize(value) {
        return nfc(value).trim().toLowerCase();
    }

    function cleanValue(type, value) {
        if (!Object.prototype.hasOwnProperty.call(RULE_TYPES, type)) {
            return { ok: false, error: "규칙 종류를 골라 주세요." };
        }
        var text = nfc(value).trim();
        if (!text) {
            return { ok: false, error: "차단할 값을 입력해 주세요." };
        }
        if (Array.from(text).length > MAX_VALUE_LENGTH) {
            return { ok: false, error: "64자 이하로 입력해 주세요." };
        }
        if (CONTROL_CHARS.test(text)) {
            return { ok: false, error: "쓸 수 없는 문자가 들어 있어요." };
        }
        if (type === "authorCode" && /\s/.test(text)) {
            return { ok: false, error: "식별 코드에는 공백을 넣을 수 없어요." };
        }
        return { ok: true, value: text };
    }

    function ruleKey(type, value) {
        return type + "\u0000" + normalize(value);
    }

    // 손상되거나 모르는 형식이면 error를 돌려준다. 저장값은 자동으로 덮어쓰지 않는다.
    function parseSettings(raw) {
        if (raw === null || raw === undefined || raw === "") {
            return { settings: defaultSettings(), error: null };
        }
        var data;
        try {
            data = JSON.parse(raw);
        } catch (err) {
            return { settings: null, error: "invalid" };
        }
        if (!data || typeof data !== "object" || Array.isArray(data)) {
            return { settings: null, error: "invalid" };
        }
        if (data.version !== VERSION) {
            return { settings: null, error: "version" };
        }
        if (typeof data.enabled !== "boolean" || typeof data.blockGuests !== "boolean" || !Array.isArray(data.rules)) {
            return { settings: null, error: "invalid" };
        }
        if (data.rules.length > MAX_RULES) {
            return { settings: null, error: "invalid" };
        }
        var rules = [];
        var seen = {};
        for (var i = 0; i < data.rules.length; i += 1) {
            var rule = data.rules[i];
            if (!rule || typeof rule !== "object" || typeof rule.value !== "string") {
                return { settings: null, error: "invalid" };
            }
            var cleaned = cleanValue(rule.type, rule.value);
            if (!cleaned.ok) {
                return { settings: null, error: "invalid" };
            }
            var key = ruleKey(rule.type, cleaned.value);
            if (seen[key]) {
                continue;
            }
            seen[key] = true;
            rules.push({ type: rule.type, value: cleaned.value });
        }
        return { settings: { version: VERSION, enabled: data.enabled, blockGuests: data.blockGuests, rules: rules }, error: null };
    }

    function copySettings(settings) {
        return {
            version: VERSION,
            enabled: !!settings.enabled,
            blockGuests: !!settings.blockGuests,
            rules: settings.rules.map(function (rule) { return { type: rule.type, value: rule.value }; })
        };
    }

    function addRule(settings, type, value) {
        var cleaned = cleanValue(type, value);
        if (!cleaned.ok) {
            return { settings: settings, error: cleaned.error };
        }
        var key = ruleKey(type, cleaned.value);
        for (var i = 0; i < settings.rules.length; i += 1) {
            if (ruleKey(settings.rules[i].type, settings.rules[i].value) === key) {
                return { settings: settings, error: "이미 등록된 규칙이에요." };
            }
        }
        if (settings.rules.length >= MAX_RULES) {
            return { settings: settings, error: "규칙은 " + MAX_RULES + "개까지 등록할 수 있어요." };
        }
        var next = copySettings(settings);
        next.rules.push({ type: type, value: cleaned.value });
        return { settings: next, error: null };
    }

    function removeRule(settings, type, value) {
        var key = ruleKey(type, value);
        var next = copySettings(settings);
        next.rules = next.rules.filter(function (rule) { return ruleKey(rule.type, rule.value) !== key; });
        return next;
    }

    function isActive(settings) {
        return !!(settings && settings.enabled && (settings.blockGuests || settings.rules.length > 0));
    }

    function compile(settings) {
        // __proto__ 같은 이름도 일반 키로 저장되도록 프로토타입 없는 객체를 쓴다.
        var compiled = { active: isActive(settings), blockGuests: false, nicknames: Object.create(null), codes: Object.create(null), keywords: [] };
        if (!compiled.active) {
            return compiled;
        }
        compiled.blockGuests = !!settings.blockGuests;
        settings.rules.forEach(function (rule) {
            var value = normalize(rule.value);
            if (rule.type === "nickname") {
                compiled.nicknames[value] = true;
            } else if (rule.type === "authorCode") {
                compiled.codes[value] = true;
            } else if (rule.type === "titleKeyword") {
                compiled.keywords.push(value);
            }
        });
        return compiled;
    }

    function isGuestCode(code) {
        return GUEST_CODE.test(String(code || "").trim());
    }

    // 일치한 이유를 돌려준다: authorCode, guest, nickname, titleKeyword 또는 null.
    function matchItem(compiled, item) {
        if (!compiled || !compiled.active || !item) {
            return null;
        }
        var code = normalize(item.code);
        if (code) {
            if (Object.prototype.hasOwnProperty.call(compiled.codes, code)) {
                return "authorCode";
            }
            if (compiled.blockGuests && isGuestCode(code)) {
                return "guest";
            }
        }
        var author = normalize(item.author);
        if (author && Object.prototype.hasOwnProperty.call(compiled.nicknames, author)) {
            return "nickname";
        }
        var title = normalize(item.title);
        if (title) {
            for (var i = 0; i < compiled.keywords.length; i += 1) {
                if (title.indexOf(compiled.keywords[i]) !== -1) {
                    return "titleKeyword";
                }
            }
        }
        return null;
    }

    var api = {
        STORAGE_KEY: STORAGE_KEY,
        MAX_RULES: MAX_RULES,
        MAX_VALUE_LENGTH: MAX_VALUE_LENGTH,
        RULE_TYPES: RULE_TYPES,
        defaultSettings: defaultSettings,
        normalize: normalize,
        cleanValue: cleanValue,
        parseSettings: parseSettings,
        addRule: addRule,
        removeRule: removeRule,
        isActive: isActive,
        isGuestCode: isGuestCode,
        compile: compile,
        matchItem: matchItem
    };
    root.MirrorUserFilter = api;

    var document = root.document;
    if (!document || typeof document.querySelector !== "function") {
        return;
    }

    // ---------- 화면 적용 ----------

    var REGIONS = {
        board: { noun: "글", allLabel: function (n) { return "이 페이지의 글 " + n + "개가 모두 차단됐어요."; } },
        related: { noun: "글", allLabel: function (n) { return "불러온 글 " + n + "개가 모두 차단됐어요."; } },
        comments: { noun: "댓글", allLabel: function (n) { return "댓글 " + n + "개가 모두 차단됐어요."; } }
    };

    var state = {
        settings: defaultSettings(),
        loadError: null,
        saveFailed: false,
        compiled: compile(defaultSettings()),
        reveal: { board: false, related: false, comments: false, article: false },
        counts: { board: 0, related: 0, comments: 0, article: 0 },
        dialog: null,
        opener: null,
        prefillHint: ""
    };

    function readStorage() {
        try {
            return { raw: root.localStorage.getItem(STORAGE_KEY), ok: true };
        } catch (err) {
            return { raw: null, ok: false };
        }
    }

    function loadSettings() {
        var stored = readStorage();
        var parsed = parseSettings(stored.raw);
        if (parsed.error) {
            state.loadError = parsed.error;
            state.settings = defaultSettings();
            state.settings.enabled = false;
        } else {
            state.loadError = null;
            state.settings = parsed.settings;
        }
        state.compiled = compile(state.settings);
    }

    function saveSettings(next) {
        state.settings = next;
        state.compiled = compile(next);
        try {
            root.localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
            state.saveFailed = false;
        } catch (err) {
            state.saveFailed = true;
        }
    }

    // 다른 탭에서 바뀌었을 수 있으므로 변경 직전에 최신 값을 다시 읽는다.
    function latestSettings() {
        if (state.saveFailed) {
            return state.settings;
        }
        var parsed = parseSettings(readStorage().raw);
        return parsed.error ? state.settings : parsed.settings;
    }

    function authorData(element) {
        var span = element ? element.querySelector("[data-author]") : null;
        if (!span) {
            return { author: "", code: "" };
        }
        return { author: span.getAttribute("data-author") || "", code: span.getAttribute("data-author-code") || "" };
    }

    function feedItemData(li) {
        var data = authorData(li);
        var title = li.querySelector(".feed-title");
        data.title = title ? title.textContent : "";
        return data;
    }

    function regionParts(name) {
        if (name === "board") {
            var boardList = document.getElementById("board-list");
            var ul = boardList ? boardList.querySelector(":scope > ul") : null;
            if (ul && !ul.id) {
                ul.id = "board-list-items";
            }
            return ul ? { container: boardList, list: ul, anchor: ul, position: "beforebegin" } : null;
        }
        if (name === "related") {
            var section = document.getElementById("related-section");
            var related = document.getElementById("related-list");
            var head = section ? section.querySelector(".related-head") : null;
            return related && head ? { container: section, list: related, anchor: head, position: "afterend" } : null;
        }
        if (name === "comments") {
            var shell = document.querySelector(".comment-shell");
            var comments = shell ? shell.querySelector(".comment-list") : null;
            var title = shell ? shell.querySelector("h2") : null;
            if (comments && !comments.id) {
                comments.id = "comment-list";
            }
            return comments && title ? { container: shell, list: comments, anchor: title, position: "afterend" } : null;
        }
        return null;
    }

    function regionItems(name, list) {
        var selector = name === "comments" ? ":scope > li.comment-item" : ":scope > li";
        return Array.prototype.filter.call(list.querySelectorAll(selector), function (li) {
            return name === "comments" || !!li.querySelector("a.feed-item");
        });
    }

    function ensureBar(name, parts) {
        var bar = parts.container.querySelector('[data-user-filter-bar="' + name + '"]');
        if (bar) {
            return bar;
        }
        bar = document.createElement("div");
        bar.className = "user-filter-bar";
        bar.setAttribute("data-user-filter-bar", name);
        var note = document.createElement("p");
        note.className = "user-filter-note";
        note.hidden = true;
        var button = document.createElement("button");
        button.type = "button";
        button.className = "user-filter-reveal";
        button.setAttribute("aria-controls", parts.list.id || "");
        button.addEventListener("click", function () {
            state.reveal[name] = !state.reveal[name];
            applyRegion(name);
        });
        bar.appendChild(note);
        bar.appendChild(button);
        parts.anchor.insertAdjacentElement(parts.position, bar);
        return bar;
    }

    function applyRegion(name) {
        var parts = regionParts(name);
        if (!parts) {
            return 0;
        }
        var items = regionItems(name, parts.list);
        var revealed = state.reveal[name];
        var matched = 0;
        items.forEach(function (li) {
            var data = name === "comments" ? authorData(li.querySelector(".comment-meta")) : feedItemData(li);
            var hit = !!matchItem(state.compiled, data);
            if (hit) {
                matched += 1;
            }
            li.classList.toggle("is-user-filtered", hit && !revealed);
            li.classList.toggle("is-user-filtered-shown", hit && revealed);
        });
        state.counts[name] = matched;

        var existing = parts.container.querySelector('[data-user-filter-bar="' + name + '"]');
        if (!matched) {
            if (existing) {
                existing.remove();
            }
            return 0;
        }
        var bar = existing || ensureBar(name, parts);
        var noun = REGIONS[name].noun;
        var note = bar.querySelector(".user-filter-note");
        var button = bar.querySelector(".user-filter-reveal");
        var allHidden = matched === items.length && !revealed;
        note.hidden = !allHidden;
        note.textContent = allHidden ? REGIONS[name].allLabel(matched) : "";
        button.textContent = revealed ? "차단된 " + noun + " 숨기기" : "차단된 " + noun + " " + matched + "개 보기";
        button.setAttribute("aria-expanded", revealed ? "true" : "false");
        return matched;
    }

    var ARTICLE_REASONS = {
        authorCode: "차단한 사용자의 글이에요",
        nickname: "차단한 닉네임의 글이에요",
        guest: "유동 작성자의 글이에요",
        titleKeyword: "제목에 차단한 단어가 있어요"
    };

    function applyArticle() {
        var shell = document.querySelector(".read-shell");
        var head = shell ? shell.querySelector(":scope > .article-head") : null;
        if (!head) {
            return 0;
        }
        var data = authorData(head.querySelector(".article-meta"));
        var title = head.querySelector("h1");
        data.title = title ? title.textContent : "";
        var reason = matchItem(state.compiled, data);
        var notice = shell.querySelector(":scope > .user-filter-article");
        state.counts.article = reason ? 1 : 0;
        if (!reason) {
            shell.classList.remove("is-article-filtered");
            if (notice) {
                notice.remove();
            }
            return 0;
        }
        if (!notice) {
            notice = document.createElement("div");
            notice.className = "user-filter-article";
            notice.innerHTML = '<p class="user-filter-article-text"></p>' +
                '<div class="user-filter-article-actions">' +
                '<button type="button" class="user-filter-reveal" data-article-toggle aria-controls="article-body"></button>' +
                '<button type="button" class="user-filter-reveal" data-user-filter-open>필터 관리</button>' +
                "</div>";
            notice.querySelector("[data-article-toggle]").addEventListener("click", function () {
                state.reveal.article = !state.reveal.article;
                applyArticle();
            });
            shell.insertBefore(notice, head);
        }
        var revealed = state.reveal.article;
        shell.classList.toggle("is-article-filtered", !revealed);
        notice.querySelector(".user-filter-article-text").textContent = ARTICLE_REASONS[reason] || ARTICLE_REASONS.authorCode;
        var toggle = notice.querySelector("[data-article-toggle]");
        toggle.textContent = revealed ? "다시 접기" : "이번에만 보기";
        toggle.setAttribute("aria-expanded", revealed ? "true" : "false");
        return 1;
    }

    function applyAll() {
        applyRegion("board");
        applyRegion("related");
        applyRegion("comments");
        applyArticle();
        var headerButton = document.querySelector("[data-user-filter-header]");
        var active = isActive(state.settings);
        document.documentElement.setAttribute("data-user-filter-active", active ? "true" : "false");
        if (headerButton) {
            var label = "차단 필터 설정, " + (active ? "켜짐" : "꺼짐");
            headerButton.setAttribute("aria-label", label);
            headerButton.title = label;
        }
        // 새 글 알림처럼 같은 규칙으로 판정해야 하는 스크립트에 알린다.
        try {
            document.dispatchEvent(new root.CustomEvent("mirror:user-filter-changed"));
        } catch (err) {
            // 오래된 브라우저에서는 알림 없이 진행한다.
        }
    }

    function resetReveal() {
        state.reveal = { board: false, related: false, comments: false, article: false };
    }

    function summary() {
        if (state.loadError) {
            return "저장된 설정을 읽지 못해 필터를 껐어요.";
        }
        if (!state.settings.enabled) {
            return "필터를 껐어요. 규칙은 그대로 남아 있어요.";
        }
        var posts = state.counts.board + state.counts.related;
        var parts = [];
        if (state.counts.article) {
            parts.push("이 글");
        }
        if (posts) {
            parts.push("글 " + posts + "개");
        }
        if (state.counts.comments) {
            parts.push("댓글 " + state.counts.comments + "개");
        }
        return parts.length ? "지금 화면에서 " + parts.join(", ") + "를 가렸어요." : "지금 화면에서 가린 항목은 없어요.";
    }

    // ---------- 작성자 메뉴(author_actions.js)에서 쓰는 미리 채우기 ----------

    function prefillFor(button) {
        var author = button.getAttribute("data-author") || "";
        var code = button.getAttribute("data-author-code") || "";
        if (code && isGuestCode(code)) {
            return { type: "authorCode", value: code, hint: "IP 앞자리 " + code + "를 쓰는 다른 유동도 함께 가려져요." };
        }
        if (code) {
            return { type: "authorCode", value: code, hint: "식별 코드 " + code + "로 이 사용자의 글과 댓글을 가려요." };
        }
        return { type: "nickname", value: author, hint: "식별 코드가 없어 닉네임으로 가려요. 같은 닉네임을 쓰는 사람도 함께 가려져요." };
    }

    // ---------- 관리 대화상자 ----------

    var DIALOG_HTML =
        '<div class="ufd-head">' +
        '<h2 id="user-filter-title">차단 필터</h2>' +
        '<button type="button" class="icon-btn ufd-close" aria-label="닫기"><svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M6 6l12 12M18 6L6 18"/></svg></button>' +
        "</div>" +
        '<div class="ufd-body">' +
        '<div class="ufd-error" hidden><p>저장된 설정을 읽지 못해 필터를 껐어요.</p><button type="button" class="user-filter-reveal ufd-reset">필터 설정 초기화</button></div>' +
        '<label class="ufd-switch"><span class="ufd-switch-text"><span class="ufd-switch-name">차단 필터 사용</span><span class="ufd-switch-desc">끄면 규칙은 남기고 모두 보여 줘요.</span></span><input type="checkbox" role="switch" class="ufd-enabled"></label>' +
        '<label class="ufd-switch"><span class="ufd-switch-text"><span class="ufd-switch-name">유동 전체 차단</span><span class="ufd-switch-desc">IP 앞자리가 붙은 작성자를 모두 가려요.</span></span><input type="checkbox" role="switch" class="ufd-guests"></label>' +
        '<form class="ufd-add" novalidate>' +
        '<h3 class="ufd-section-title">규칙 추가</h3>' +
        '<div class="ufd-add-row">' +
        '<span class="board-search-select-wrap ufd-type-wrap"><select class="board-search-select ufd-type" aria-label="규칙 종류">' +
        '<option value="authorCode">식별 코드</option><option value="nickname">닉네임</option><option value="titleKeyword">제목 단어</option>' +
        '</select><svg class="ui-icon ui-chevron is-down" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M6 9.5l6 6 6-6"/></svg></span>' +
        '<input class="board-search-input ufd-value" type="text" autocomplete="off" enterkeyhint="done" aria-label="차단할 값" aria-describedby="ufd-hint ufd-field-error">' +
        '<button type="submit" class="board-search-submit ufd-add-btn">추가</button>' +
        "</div>" +
        '<p class="ufd-hint" id="ufd-hint"></p>' +
        '<p class="ufd-field-error" id="ufd-field-error" hidden></p>' +
        "</form>" +
        '<h3 class="ufd-section-title">등록한 규칙 <span class="ufd-count"></span></h3>' +
        '<p class="ufd-empty">등록한 차단 규칙이 없어요.</p>' +
        '<ul class="ufd-rules"></ul>' +
        '<p class="ufd-note">이 브라우저에만 저장돼요. 포커 게시판에는 적용되지 않아요.</p>' +
        '<p class="ufd-status" role="status" aria-live="polite"></p>' +
        "</div>";

    var TYPE_HINTS = {
        authorCode: { placeholder: "예: noodle4477 또는 175.113", hint: "작성자 이름 옆 괄호 안의 값과 똑같을 때 가려요." },
        nickname: { placeholder: "예: ㅇㅇ", hint: "닉네임이 똑같을 때 가려요. 같은 닉네임을 쓰는 사람도 함께 가려져요." },
        titleKeyword: { placeholder: "예: 스포", hint: "제목에 이 단어가 들어 있으면 가려요. 댓글에는 적용되지 않아요." }
    };

    function dialogPart(selector) {
        return state.dialog ? state.dialog.querySelector(selector) : null;
    }

    function setStatus(message) {
        var status = dialogPart(".ufd-status");
        if (status) {
            status.textContent = message || "";
        }
    }

    function setFieldError(message) {
        var error = dialogPart(".ufd-field-error");
        if (!error) {
            return;
        }
        error.hidden = !message;
        error.textContent = message || "";
        dialogPart(".ufd-value").setAttribute("aria-invalid", message ? "true" : "false");
    }

    function updateTypeHint() {
        var type = dialogPart(".ufd-type").value;
        var info = TYPE_HINTS[type] || TYPE_HINTS.authorCode;
        dialogPart(".ufd-value").placeholder = info.placeholder;
        dialogPart(".ufd-hint").textContent = state.prefillHint || info.hint;
    }

    function renderDialog() {
        if (!state.dialog) {
            return;
        }
        var locked = !!state.loadError;
        dialogPart(".ufd-error").hidden = !locked;
        var enabled = dialogPart(".ufd-enabled");
        var guests = dialogPart(".ufd-guests");
        enabled.checked = state.settings.enabled;
        guests.checked = state.settings.blockGuests;
        enabled.disabled = locked;
        guests.disabled = locked;
        Array.prototype.forEach.call(state.dialog.querySelectorAll(".ufd-add select, .ufd-add input, .ufd-add button"), function (el) {
            el.disabled = locked;
        });

        var list = dialogPart(".ufd-rules");
        // 다시 그린 뒤에도 같은 규칙의 삭제 버튼에 포커스를 돌려준다.
        var focused = document.activeElement;
        var focusedKey = focused && list.contains(focused) ? focused.getAttribute("data-rule-key") : null;
        list.textContent = "";
        state.settings.rules.forEach(function (rule) {
            var li = document.createElement("li");
            li.className = "ufd-rule";
            var type = document.createElement("span");
            type.className = "ufd-rule-type";
            type.textContent = RULE_TYPES[rule.type];
            var value = document.createElement("span");
            value.className = "ufd-rule-value";
            value.textContent = rule.value;
            var remove = document.createElement("button");
            remove.type = "button";
            remove.className = "ufd-rule-remove";
            remove.setAttribute("data-rule-key", ruleKey(rule.type, rule.value));
            remove.textContent = "삭제";
            remove.setAttribute("aria-label", RULE_TYPES[rule.type] + " " + rule.value + " 규칙 삭제");
            remove.addEventListener("click", function () {
                var items = Array.prototype.slice.call(list.querySelectorAll(".ufd-rule-remove"));
                var index = items.indexOf(remove);
                commit(removeRule(latestSettings(), rule.type, rule.value), "규칙을 삭제했어요.");
                var after = list.querySelectorAll(".ufd-rule-remove");
                var focusTarget = after[Math.min(index, after.length - 1)] || dialogPart(".ufd-value");
                focusTarget.focus();
            });
            li.appendChild(type);
            li.appendChild(value);
            li.appendChild(remove);
            list.appendChild(li);
        });
        var count = state.settings.rules.length;
        dialogPart(".ufd-count").textContent = count ? String(count) : "";
        dialogPart(".ufd-empty").hidden = count > 0;
        list.hidden = count === 0;
        if (focusedKey !== null) {
            var same = Array.prototype.filter.call(list.querySelectorAll(".ufd-rule-remove"), function (button) {
                return button.getAttribute("data-rule-key") === focusedKey;
            })[0];
            (same || dialogPart(".ufd-value")).focus();
        }
    }

    function commit(next, message) {
        saveSettings(next);
        resetReveal();
        applyAll();
        renderDialog();
        var text = message ? message + " " : "";
        if (state.saveFailed) {
            text += "설정을 저장할 수 없어 이 페이지에서만 적용돼요.";
        } else if (next.enabled === false && next.rules.length && message === "규칙을 추가했어요.") {
            text += "필터를 켜면 적용돼요.";
        } else {
            text += summary();
        }
        setStatus(text);
    }

    function buildDialog() {
        var dialog = document.createElement("dialog");
        dialog.className = "user-filter-dialog";
        dialog.setAttribute("aria-labelledby", "user-filter-title");
        dialog.innerHTML = DIALOG_HTML;
        document.body.appendChild(dialog);
        state.dialog = dialog;

        dialogPart(".ufd-close").addEventListener("click", function () {
            dialog.close();
        });
        dialog.addEventListener("click", function (event) {
            // 바깥 어두운 영역을 누르면 닫는다.
            if (event.target === dialog) {
                dialog.close();
            }
        });
        dialog.addEventListener("close", restoreFocus);
        dialogPart(".ufd-enabled").addEventListener("change", function (event) {
            var next = copySettings(latestSettings());
            next.enabled = event.target.checked;
            commit(next, next.enabled ? "필터를 켰어요." : "");
        });
        dialogPart(".ufd-guests").addEventListener("change", function (event) {
            var next = copySettings(latestSettings());
            next.blockGuests = event.target.checked;
            commit(next, next.blockGuests ? "유동 전체 차단을 켰어요." : "유동 전체 차단을 껐어요.");
        });
        dialogPart(".ufd-type").addEventListener("change", function () {
            state.prefillHint = "";
            setFieldError("");
            updateTypeHint();
        });
        dialogPart(".ufd-value").addEventListener("input", function () {
            setFieldError("");
        });
        dialogPart(".ufd-add").addEventListener("submit", function (event) {
            event.preventDefault();
            var input = dialogPart(".ufd-value");
            var result = addRule(latestSettings(), dialogPart(".ufd-type").value, input.value);
            if (result.error) {
                setFieldError(result.error);
                input.focus();
                return;
            }
            setFieldError("");
            input.value = "";
            state.prefillHint = "";
            updateTypeHint();
            commit(result.settings, "규칙을 추가했어요.");
            input.focus();
        });
        dialogPart(".ufd-reset").addEventListener("click", function () {
            state.loadError = null;
            commit(defaultSettings(), "필터 설정을 초기화했어요.");
            dialogPart(".ufd-enabled").focus();
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
            target = document.querySelector(".user-filter-article [data-article-toggle]");
        }
        if (!isVisible(target)) {
            target = document.querySelector("[data-user-filter-header]");
        }
        if (target && typeof target.focus === "function") {
            target.focus();
        }
    }

    function openDialog(opener, prefill) {
        var dialog = state.dialog || buildDialog();
        state.opener = opener || null;
        setFieldError("");
        setStatus("");
        renderDialog();
        var type = dialogPart(".ufd-type");
        var input = dialogPart(".ufd-value");
        if (prefill) {
            type.value = prefill.type;
            input.value = prefill.value;
            state.prefillHint = prefill.hint;
        } else {
            state.prefillHint = "";
        }
        updateTypeHint();
        if (!dialog.open) {
            if (typeof dialog.showModal === "function") {
                dialog.showModal();
            } else {
                dialog.setAttribute("open", "");
            }
        }
        if (prefill && !state.loadError) {
            dialogPart(".ufd-add-btn").focus();
        } else {
            (state.loadError ? dialogPart(".ufd-reset") : dialogPart(".ufd-enabled")).focus();
        }
    }

    // ---------- 연결 ----------

    function refreshFromStorage() {
        if (state.saveFailed) {
            return;
        }
        var before = JSON.stringify(state.settings) + String(state.loadError);
        loadSettings();
        if (before === JSON.stringify(state.settings) + String(state.loadError)) {
            return;
        }
        resetReveal();
        applyAll();
        if (state.dialog && state.dialog.open) {
            renderDialog();
        }
    }

    function boot() {
        loadSettings();
        applyAll();

        var header = document.querySelector("[data-user-filter-header]");
        if (header) {
            header.hidden = false;
        }

        document.addEventListener("click", function (event) {
            var target = event.target && event.target.closest ? event.target : null;
            if (!target) {
                return;
            }
            var opener = target.closest("[data-user-filter-open]");
            if (opener) {
                openDialog(opener, null);
            }
        });

        var related = document.getElementById("related-list");
        if (related && typeof root.MutationObserver === "function") {
            // 더보기로 붙는 글도 그리기 전에(마이크로태스크) 판정한다.
            new root.MutationObserver(function () {
                applyRegion("related");
            }).observe(related, { childList: true });
        }

        document.addEventListener("mirror:board-refreshed", function () {
            applyRegion("board");
        });
        root.addEventListener("storage", function (event) {
            if (!event.key || event.key === STORAGE_KEY) {
                refreshFromStorage();
            }
        });
        root.addEventListener("pageshow", function (event) {
            if (event.persisted) {
                refreshFromStorage();
            }
        });
        document.addEventListener("visibilitychange", function () {
            if (document.visibilityState === "visible") {
                refreshFromStorage();
            }
        });
    }

    try {
        // 다른 스크립트가 현재 적용 중인 규칙(저장 실패로 이 페이지에만 적용한 규칙 포함)을 쓰게 한다.
        api.openForAuthor = function (button) {
            openDialog(button, prefillFor(button));
        };
        api.matchesCurrent = function (item) {
            return matchItem(state.compiled, item);
        };
        boot();
    } finally {
        // base.html의 첫 페인트 가림을 항상 해제한다(실패해도 내용은 보여야 한다).
        document.documentElement.setAttribute("data-user-filter", "ready");
    }
})(typeof window !== "undefined" ? window : globalThis);
