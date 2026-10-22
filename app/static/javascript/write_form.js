(function (root) {
    "use strict";

    // 디시 글쓰기 폼. 확인은 서버가 다시 하지만 같은 기준으로 먼저 알려 준다.
    var IDENTITY_KEY = "mirror_write_identity_v1";
    var FIELDS = ["nickname", "password", "subject", "body", "images", "captcha"];
    var IMAGE_TYPES = { "image/jpeg": true, "image/png": true, "image/gif": true, "image/webp": true };
    var IMAGE_EXTENSION = /\.(jpe?g|png|gif|webp)$/i;
    // 본문 한 줄이 "[사진 N]"뿐이면 그 자리에 N번째 사진이 들어간다. 서버(dc_write.PHOTO_MARKER)와 같은 규칙.
    var MARKER_LINE = /^[ \t]*\[사진 ?([0-9]{1,2})\][ \t]*$/;

    function charLength(value) {
        return Array.from(String(value == null ? "" : value)).length;
    }

    function megabytes(bytes) {
        return Math.round(bytes / 1048576) + "MB";
    }

    // 새로 고른 파일 중 넣을 수 있는 것만 고른다. 형식은 서버가 파일 내용으로 다시 확인한다.
    function pickImages(existing, incoming, limits) {
        var accepted = [];
        var skipped = { type: 0, size: 0, count: 0, total: 0 };
        var total = existing.reduce(function (sum, file) {
            return sum + file.size;
        }, 0);
        Array.prototype.forEach.call(incoming || [], function (file) {
            var typeOk = file.type ? IMAGE_TYPES[file.type] === true : IMAGE_EXTENSION.test(file.name || "");
            if (!typeOk) {
                skipped.type += 1;
            } else if (!file.size || file.size > limits.image_max_bytes) {
                skipped.size += 1;
            } else if (existing.length + accepted.length >= limits.image_max_count) {
                skipped.count += 1;
            } else if (total + file.size > limits.image_total_bytes) {
                skipped.total += 1;
            } else {
                accepted.push(file);
                total += file.size;
            }
        });
        var messages = [];
        if (skipped.type) {
            messages.push("JPG·PNG·GIF·WEBP가 아닌 파일 " + skipped.type + "개는 넣지 않았어요.");
        }
        if (skipped.size) {
            messages.push(megabytes(limits.image_max_bytes) + "를 넘거나 빈 사진 " + skipped.size + "장은 넣지 않았어요.");
        }
        if (skipped.count) {
            messages.push("사진은 " + limits.image_max_count + "장까지라 " + skipped.count + "장은 넣지 않았어요.");
        }
        if (skipped.total) {
            messages.push("모두 합쳐 " + megabytes(limits.image_total_bytes) + "를 넘어서 " + skipped.total + "장은 넣지 않았어요.");
        }
        return { accepted: accepted, message: messages.join(" ") };
    }

    function moveItem(list, from, to) {
        if (from < 0 || from >= list.length || to < 0 || to >= list.length || from === to) {
            return list;
        }
        var copy = list.slice();
        var item = copy.splice(from, 1)[0];
        copy.splice(to, 0, item);
        return copy;
    }

    // label: "사진" 또는 수정 화면의 "원본"(사진이 아닌 원래 블록). 서버(dc_write.MODIFY_MARKER)와 같은 규칙.
    function markerText(number, label) {
        return "[" + (label || "사진") + " " + number + "]";
    }

    function markerPattern(label) {
        return label && label !== "사진" ? new RegExp("^[ \\t]*\\[" + label + " ?([0-9]{1,2})\\][ \\t]*$") : MARKER_LINE;
    }

    // 수정할 글의 블록을 본문 글자와 사진·원본 목록으로 바꾼다. 사진과 원본은 본문에 표시 줄로 남는다.
    function modifyDraft(blocks) {
        var lines = [];
        var photos = [];
        var originals = [];
        (blocks || []).forEach(function (block) {
            if (!block || typeof block !== "object") {
                return;
            }
            if (block.kind === "text" && typeof block.text === "string") {
                lines.push(block.text);
            } else if (block.kind === "photo" && Number.isInteger(block.index)) {
                photos.push({ keep: block.index, url: typeof block.url === "string" ? block.url : "" });
                lines.push(markerText(photos.length, "사진"));
            } else if (block.kind === "html" && Number.isInteger(block.index)) {
                originals.push({ index: block.index, preview: typeof block.preview === "string" ? block.preview : "" });
                lines.push(markerText(originals.length, "원본"));
            }
        });
        return { body: lines.join("\n"), photos: photos, originals: originals };
    }

    // 커서 자리에 사진 줄을 넣는다. 표시 줄은 항상 한 줄을 혼자 차지하고, 커서는 그 다음 줄로 간다.
    function insertMarkers(text, start, end, numbers) {
        var before = text.slice(0, start);
        var after = text.slice(end);
        var lead = before && before.charAt(before.length - 1) !== "\n" ? "\n" : "";
        var tail = after.charAt(0) === "\n" ? "" : "\n";
        var inserted = lead + numbers.map(function (number) {
            return markerText(number);
        }).join("\n") + tail;
        var caret = before.length + inserted.length + (tail ? 0 : 1);
        return { text: before + inserted + after, caret: Math.min(caret, (before + inserted + after).length) };
    }

    // 사진 순서가 바뀌거나 빠지면 본문의 표시 번호를 맞춘다. map 값이 0이면 그 줄을 지운다.
    function renumberMarkers(text, map, label) {
        var pattern = markerPattern(label);
        var out = [];
        String(text).split("\n").forEach(function (line) {
            var match = pattern.exec(line);
            var number = match ? Number(match[1]) : 0;
            if (!match || !Object.prototype.hasOwnProperty.call(map, number)) {
                out.push(line);
            } else if (map[number]) {
                out.push(markerText(map[number], label));
            }
        });
        return out.join("\n");
    }

    function checkMarkers(text, count, label) {
        var pattern = markerPattern(label);
        var name = label || "사진";
        var seen = {};
        var lines = String(text).split("\n");
        for (var i = 0; i < lines.length; i += 1) {
            var match = pattern.exec(lines[i]);
            if (!match) {
                continue;
            }
            var number = Number(match[1]);
            if (number < 1 || number > count) {
                return "본문의 " + markerText(number, label) + "에 맞는 " + name + "이 없어요. 그 줄을 지우거나 " + name + "을 넣어 주세요.";
            }
            if (seen[number]) {
                return "본문에 " + markerText(number, label) + " 줄이 두 번 있어요. 하나만 남겨 주세요.";
            }
            seen[number] = true;
        }
        return null;
    }

    function validateFields(values, limits) {
        var nickname = String(values.nickname || "").trim();
        var password = String(values.password || "");
        var subject = String(values.subject || "").trim();
        var body = String(values.body || "");
        // 디시 계정으로 쓰면 닉네임·비밀번호를 보내지 않는다.
        if (!values.account) {
            if (!nickname) {
                return { field: "nickname", message: "닉네임을 입력해 주세요." };
            }
            if (charLength(nickname) > limits.nickname_max) {
                return { field: "nickname", message: "닉네임은 " + limits.nickname_max + "자까지 쓸 수 있어요." };
            }
            if (charLength(password) < limits.password_min || charLength(password) > limits.password_max) {
                return { field: "password", message: "글 비밀번호는 " + limits.password_min + "~" + limits.password_max + "자로 입력해 주세요." };
            }
        }
        if (!subject) {
            return { field: "subject", message: "제목을 입력해 주세요." };
        }
        if (charLength(subject) > limits.subject_max) {
            return { field: "subject", message: "제목은 " + limits.subject_max + "자까지 쓸 수 있어요." };
        }
        if (!body.trim() && !(values.imageCount > 0)) {
            return { field: "body", message: "본문을 입력하거나 사진을 넣어 주세요." };
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
        pickImages: pickImages,
        moveItem: moveItem,
        insertMarkers: insertMarkers,
        renumberMarkers: renumberMarkers,
        checkMarkers: checkMarkers,
        modifyDraft: modifyDraft,
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
        bodyCount: document.getElementById("write-body-count"),
        rememberNote: document.getElementById("write-remember-password-note"),
        images: document.getElementById("write-images"),
        imageAdd: document.getElementById("write-images-add"),
        imageInput: document.getElementById("write-images-input"),
        imageList: document.getElementById("write-image-list"),
        imageCount: document.getElementById("write-images-count"),
        imageLive: document.getElementById("write-images-live"),
        editor: document.getElementById("write-editor"),
        modifyLoad: document.getElementById("write-modify-load"),
        originals: document.getElementById("write-originals"),
        originalList: document.getElementById("write-original-list"),
        modeInputs: Array.prototype.slice.call(form.querySelectorAll('input[name="write-mode"]')),
        identity: form.querySelector(".write-identity"),
        remember: form.querySelector(".write-remember"),
        passwordHint: document.getElementById("write-password-hint"),
        accountNote: document.getElementById("write-account-note"),
        modeNote: document.getElementById("write-mode-note")
    };
    // 수정 화면이면 원래 글 번호. 비밀번호로 원래 글을 불러온 뒤에만 수정을 보낼 수 있다.
    var modifyPid = form.getAttribute("data-modify-pid") || "";
    var limits = {
        subject_max: Number(form.getAttribute("data-subject-max")) || 40,
        body_max: Number(form.getAttribute("data-body-max")) || 10000,
        nickname_max: Number(form.getAttribute("data-nickname-max")) || 20,
        password_min: Number(form.getAttribute("data-password-min")) || 2,
        password_max: Number(form.getAttribute("data-password-max")) || 20,
        image_max_count: Number(form.getAttribute("data-image-max-count")) || 10,
        image_max_bytes: Number(form.getAttribute("data-image-max-bytes")) || 10485760,
        image_total_bytes: Number(form.getAttribute("data-image-total-bytes")) || 31457280
    };
    var board = form.getAttribute("data-board") || "";
    var kind = form.getAttribute("data-kind") || "";
    var backUrl = post.safePath(form.getAttribute("data-back-url")) || "/";
    var captcha = post.createCaptcha("write-captcha", "article", board);
    // images: 새 사진({file, url})과 수정할 글의 원래 사진({keep, url})이 본문 순서대로 섞인다.
    // originals: 수정할 글의 사진이 아닌 원래 블록({index, preview}).
    var state = { sending: false, retryUntil: 0, retryTimer: null, leaving: false, initial: "", images: [], caret: null,
        originals: [], fingerprint: "", originalHead: null, loaded: !modifyPid };
    var nextImageId = 1;
    var storage = null;
    try {
        storage = root.localStorage;
    } catch (err) {
        storage = null;
    }
    // 수정 화면은 연 방식(주소의 mode)을 끝까지 쓴다. 새 글은 마지막으로 고른 값을 쓴다.
    state.mode = form.getAttribute("data-mode") === "account" ? "account"
        : (el.modeInputs.length ? post.readMode(storage) : "anonymous");

    function draftSnapshot() {
        return [el.subject.value, el.body.value, headValue(), state.images.length, state.originals.length].join("\u0000");
    }

    function newImages() {
        return state.images.filter(function (item) {
            return item.file;
        });
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
        if (field === "images") {
            return el.imageAdd;
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
        [el.nickname, el.password, el.subject, el.body, el.rememberNickname, el.rememberPassword, el.imageAdd, el.modifyLoad].concat(el.modeInputs).forEach(function (input) {
            if (input) {
                input.disabled = busy;
            }
        });
        if (el.originalList) {
            Array.prototype.forEach.call(el.originalList.querySelectorAll("button"), function (control) {
                control.disabled = busy;
            });
        }
        headInputs().forEach(function (input) {
            input.disabled = busy;
        });
        if (el.imageList) {
            Array.prototype.forEach.call(el.imageList.querySelectorAll("button, input"), function (control) {
                control.disabled = busy;
            });
        }
        if (captcha) {
            captcha.setDisabled(busy);
        }
        syncSubmit();
    }

    function syncSubmit() {
        var waiting = state.retryUntil > Date.now();
        var verb = modifyPid ? "수정" : "등록";
        el.submit.disabled = state.sending || waiting || !state.loaded;
        el.submit.textContent = state.sending ? verb + " 중…" : verb;
    }

    function startRetryWait(seconds, message) {
        clearTimeout(state.retryTimer);
        state.retryUntil = Date.now() + seconds * 1000;
        setStatus((message ? message + " " : "") + seconds + "초 뒤에 다시 " + (state.loaded ? (modifyPid ? "수정할" : "등록할") : "불러올") + " 수 있어요.", "error");
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

    function headInputs() {
        return el.head ? Array.prototype.slice.call(el.head.querySelectorAll("input[type=radio]")) : [];
    }

    function checkedHead() {
        return headInputs().filter(function (input) {
            return input.checked;
        })[0] || null;
    }

    function headValue() {
        var input = checkedHead();
        return input ? input.value : "";
    }

    function headPayload() {
        var input = checkedHead();
        if (!input || !input.value) {
            // 말머리 목록에 없는 원래 말머리도 "말머리 없음"으로 조용히 바꾸지 않는다.
            if (!el.head && modifyPid && state.originalHead) {
                return { head_id: state.originalHead.id, head_name: state.originalHead.name };
            }
            return {};
        }
        return { head_id: input.value, head_name: input.getAttribute("data-head-name") || "" };
    }

    // 본문 칸은 쓴 만큼 늘어난다. 줄어들 때도 따라가도록 먼저 높이를 풀었다가 잰다.
    function fitBody() {
        el.body.style.height = "auto";
        el.body.style.height = el.body.scrollHeight + "px";
    }

    function formatSize(bytes) {
        if (bytes < 1048576) {
            return Math.max(1, Math.round(bytes / 1024)) + "KB";
        }
        return (bytes / 1048576).toFixed(1).replace(/\.0$/, "") + "MB";
    }

    function imageButton(className, label, path, onClick) {
        var button = document.createElement("button");
        button.type = "button";
        button.className = "write-image-btn " + className;
        button.setAttribute("aria-label", label);
        button.innerHTML = '<svg class="ui-icon" viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="' + path + '"/></svg>';
        button.addEventListener("click", onClick);
        return button;
    }

    // 목록을 다시 그린 뒤 같은 사진의 같은 버튼(없으면 가까운 버튼)으로 초점을 돌린다.
    function renderImages(focus) {
        if (!el.imageList) {
            return;
        }
        var count = state.images.length;
        el.imageList.textContent = "";
        state.images.forEach(function (item, index) {
            var li = document.createElement("li");
            li.className = "write-image";
            li.setAttribute("data-image-id", String(item.id));
            var frame = document.createElement("div");
            frame.className = "write-image-frame";
            var img = document.createElement("img");
            var kept = !item.file;
            img.alt = (index + 1) + "번째 사진" + (kept ? "(원래 사진)" : "");
            img.decoding = "async";
            // 브라우저가 열지 못하는 파일은 디시에서도 사진이 아니므로 미리 알려 준다.
            // 원래 사진은 디시에 이미 있으니 미리보기만 못 하는 것으로 보고 등록을 막지 않는다.
            img.addEventListener("error", function () {
                if (kept) {
                    li.classList.add("is-unpreviewable");
                    return;
                }
                if (!item.broken) {
                    item.broken = true;
                    showImageNote(brokenMessage());
                }
                li.classList.add("is-broken");
            });
            if (item.url) {
                img.src = item.url;
            } else {
                li.classList.add("is-unpreviewable");
            }
            if (item.broken) {
                li.classList.add("is-broken");
            }
            var badge = document.createElement("span");
            badge.className = "write-image-order";
            badge.setAttribute("aria-hidden", "true");
            badge.textContent = String(index + 1);
            frame.title = kept ? "원래 글의 사진" : formatSize(item.file.size);
            frame.appendChild(img);
            var broken = document.createElement("span");
            broken.className = "write-image-broken";
            broken.textContent = kept ? "원래 사진" : "열 수 없는 파일";
            frame.appendChild(broken);
            frame.appendChild(badge);
            var remove = imageButton("is-remove", (index + 1) + "번째 사진 빼기", "M7 7l10 10M17 7L7 17", function () {
                removeImage(item.id);
            });
            frame.appendChild(remove);
            var bar = document.createElement("div");
            bar.className = "write-image-bar";
            var before = imageButton("is-before", (index + 1) + "번째 사진 앞으로", "M14.5 6l-6 6 6 6", function () {
                shiftImage(item.id, -1);
            });
            var after = imageButton("is-after", (index + 1) + "번째 사진 뒤로", "M9.5 6l6 6-6 6", function () {
                shiftImage(item.id, 1);
            });
            before.disabled = index === 0 || state.sending;
            after.disabled = index === count - 1 || state.sending;
            remove.disabled = state.sending;
            bar.appendChild(before);
            bar.appendChild(after);
            li.appendChild(frame);
            li.appendChild(bar);
            el.imageList.appendChild(li);
        });
        el.imageList.hidden = count === 0;
        // 한 번에 올리는 새 사진만 장수 제한에 센다. 원래 사진은 디시에 이미 있다.
        var uploads = newImages().length;
        if (el.imageCount) {
            el.imageCount.textContent = uploads + "/" + limits.image_max_count;
        }
        el.imageAdd.disabled = state.sending || uploads >= limits.image_max_count;
        if (focus) {
            var target = null;
            var row = el.imageList.querySelector('[data-image-id="' + focus.id + '"]');
            if (row) {
                target = row.querySelector("." + focus.button);
                if (target && target.disabled) {
                    var opposite = { "is-before": ".is-after", "is-after": ".is-before" }[focus.button];
                    target = (opposite && row.querySelector(opposite + ":not(:disabled)")) || row.querySelector(".write-image-btn:not(:disabled)");
                }
            }
            (target || (el.imageAdd.disabled ? el.body : el.imageAdd)).focus();
        }
    }

    function announce(text) {
        if (el.imageLive) {
            el.imageLive.textContent = text;
        }
    }

    function brokenMessage() {
        var numbers = [];
        state.images.forEach(function (item, index) {
            if (item.broken) {
                numbers.push(index + 1);
            }
        });
        return numbers.length ? numbers.join(", ") + "번째 파일은 사진으로 열리지 않아요. 빼고 등록해 주세요." : "";
    }

    function showImageNote(message) {
        var error = errorElement("images");
        if (!error) {
            return;
        }
        error.textContent = message || "";
        error.hidden = !message;
    }

    // 썸네일과 따로 브라우저가 사진으로 여는지 확인한다. 등록은 이 확인이 끝난 뒤에만 보낸다.
    function checkImage(item) {
        item.check = new Promise(function (resolve) {
            var probe = new Image();
            probe.onload = function () {
                item.checked = true;
                resolve();
            };
            probe.onerror = function () {
                item.checked = true;
                if (!item.broken) {
                    item.broken = true;
                    showImageNote(brokenMessage());
                    var row = el.imageList.querySelector('[data-image-id="' + item.id + '"]');
                    if (row) {
                        row.classList.add("is-broken");
                    }
                }
                resolve();
            };
            probe.src = item.url;
        });
    }

    function rememberCaret() {
        state.caret = { start: el.body.selectionStart, end: el.body.selectionEnd };
    }

    // 본문을 코드로 바꾼 뒤 글자 수·높이를 맞춘다. 본문에 초점이 있으면 커서도 옮긴다.
    function setBody(text, caret) {
        el.body.value = text;
        var position = Math.min(caret, text.length);
        if (document.activeElement === el.body) {
            el.body.setSelectionRange(position, position);
        }
        state.caret = { start: position, end: position };
        updateCount(el.body, el.bodyCount, limits.body_max);
        fitBody();
    }

    function addImages(files) {
        if (state.sending || !files || !files.length) {
            return;
        }
        var current = newImages().map(function (item) {
            return item.file;
        });
        var picked = pickImages(current, files, limits);
        var firstNumber = state.images.length + 1;
        picked.accepted.forEach(function (file) {
            var item = { id: nextImageId++, file: file, url: URL.createObjectURL(file), checked: false, broken: false };
            state.images.push(item);
            checkImage(item);
        });
        if (picked.accepted.length) {
            // 커서를 둔 적이 없으면 본문 끝에 넣는다.
            var length = el.body.value.length;
            var start = state.caret ? Math.min(state.caret.start, length) : length;
            var end = state.caret ? Math.min(state.caret.end, length) : length;
            var numbers = picked.accepted.map(function (file, offset) {
                return firstNumber + offset;
            });
            var result = insertMarkers(el.body.value, start, end, numbers);
            setBody(result.text, result.caret);
        }
        el.imageAdd.removeAttribute("aria-invalid");
        showImageNote(picked.message);
        renderImages(null);
        if (picked.accepted.length) {
            announce("사진 " + picked.accepted.length + "장을 본문에 넣었어요. 모두 " + state.images.length + "장이에요.");
        }
    }

    function removeImage(id) {
        var index = state.images.findIndex(function (item) {
            return item.id === id;
        });
        if (index < 0 || state.sending) {
            return;
        }
        if (state.images[index].file) {
            URL.revokeObjectURL(state.images[index].url);
        }
        var map = {};
        map[index + 1] = 0;
        for (var number = index + 2; number <= state.images.length; number += 1) {
            map[number] = number - 1;
        }
        state.images.splice(index, 1);
        setBody(renumberMarkers(el.body.value, map), state.caret ? state.caret.start : el.body.value.length);
        showImageNote(brokenMessage());
        var neighbor = state.images[Math.min(index, state.images.length - 1)];
        renderImages(neighbor ? { id: neighbor.id, button: "is-remove" } : { id: 0, button: "" });
        announce((index + 1) + "번째 사진을 뺐어요. 남은 사진 " + state.images.length + "장.");
    }

    function shiftImage(id, delta) {
        var index = state.images.findIndex(function (item) {
            return item.id === id;
        });
        if (index < 0 || state.sending) {
            return;
        }
        var moved = moveItem(state.images, index, index + delta);
        if (moved === state.images) {
            return;
        }
        state.images = moved;
        // 본문의 [사진 N] 줄은 그대로 두고 그 번호에 들어갈 사진만 바뀐다. 즉 글 속 두 사진의 자리가 서로 바뀐다.
        showImageNote(brokenMessage());
        renderImages({ id: id, button: delta < 0 ? "is-before" : "is-after" });
        announce("사진을 " + (index + delta + 1) + "번째로 옮겼어요.");
    }

    // 수정할 글에서 고칠 수 없는 원래 블록. 본문의 [원본 N] 줄로 자리를 정하고, 빼면 글에서 사라진다.
    function renderOriginals(focusIndex) {
        if (!el.originals || !el.originalList) {
            return;
        }
        el.originalList.textContent = "";
        state.originals.forEach(function (item, index) {
            var li = document.createElement("li");
            li.className = "write-original";
            var label = document.createElement("span");
            label.className = "write-original-label";
            label.textContent = markerText(index + 1, "원본");
            var preview = document.createElement("span");
            preview.className = "write-original-preview";
            preview.textContent = item.preview || "글자가 없는 블록";
            var remove = document.createElement("button");
            remove.type = "button";
            remove.className = "comment-reply-btn write-original-remove";
            remove.textContent = "빼기";
            remove.setAttribute("aria-label", markerText(index + 1, "원본") + " 빼기");
            remove.disabled = state.sending;
            remove.addEventListener("click", function () {
                removeOriginal(index);
            });
            li.appendChild(label);
            li.appendChild(preview);
            li.appendChild(remove);
            el.originalList.appendChild(li);
        });
        el.originals.hidden = state.originals.length === 0;
        if (focusIndex !== undefined) {
            var buttons = el.originalList.querySelectorAll(".write-original-remove");
            var target = buttons[Math.min(focusIndex, buttons.length - 1)];
            (target || el.body).focus();
        }
    }

    function removeOriginal(index) {
        if (state.sending || index < 0 || index >= state.originals.length) {
            return;
        }
        var map = {};
        map[index + 1] = 0;
        for (var number = index + 2; number <= state.originals.length; number += 1) {
            map[number] = number - 1;
        }
        state.originals.splice(index, 1);
        setBody(renumberMarkers(el.body.value, map, "원본"), state.caret ? state.caret.start : el.body.value.length);
        renderOriginals(index);
        announce("원본 " + (index + 1) + "을 뺐어요.");
    }

    function selectHead(headId) {
        var inputs = headInputs();
        var match = inputs.filter(function (input) {
            return headId !== null && headId !== undefined && input.value === String(headId);
        })[0];
        if (match) {
            match.checked = true;
        } else if (el.head && state.originalHead) {
            // 지금 갤러리에 없는 원래 말머리도 화면에 보이게 두고, 필요하면 다른 것을 고를 수 있게 한다.
            var label = document.createElement("label");
            label.className = "write-head";
            var input = document.createElement("input");
            input.type = "radio";
            input.name = "head_id";
            input.value = state.originalHead.id;
            input.setAttribute("data-head-name", state.originalHead.name);
            input.checked = true;
            var text = document.createElement("span");
            text.textContent = state.originalHead.name;
            label.appendChild(input);
            label.appendChild(text);
            el.head.appendChild(label);
        } else if (inputs.length) {
            inputs[0].checked = true;
        }
    }

    function fillModify(payload) {
        var draft = modifyDraft(payload.blocks);
        state.fingerprint = String(payload.fingerprint || "");
        state.images = draft.photos.map(function (photo) {
            return { id: nextImageId++, keep: photo.keep, url: photo.url, checked: true, broken: false };
        });
        state.originals = draft.originals;
        el.subject.value = typeof payload.subject === "string" ? payload.subject : "";
        state.originalHead = typeof payload.head_id === "number"
            ? { id: String(payload.head_id), name: typeof payload.head_name === "string" ? payload.head_name : "" }
            : null;
        selectHead(payload.head_id);
        state.loaded = true;
        el.password.readOnly = true;
        if (el.modifyLoad) {
            el.modifyLoad.hidden = true;
        }
        el.editor.hidden = false;
        setBody(draft.body, draft.body.length);
        updateCount(el.subject, el.subjectCount, limits.subject_max);
        renderImages(null);
        renderOriginals();
        state.initial = draftSnapshot();
        setStatus("원래 글을 불러왔어요. 고친 뒤 수정을 눌러 주세요.", "success");
        syncSubmit();
        el.subject.focus();
    }

    function loadModify() {
        if (state.sending || state.loaded || state.retryUntil > Date.now()) {
            return;
        }
        clearErrors();
        var body = { board: board, pid: modifyPid, mode: "account" };
        if (state.mode !== "account") {
            var password = el.password.value;
            if (charLength(password) < limits.password_min || charLength(password) > limits.password_max || !password.trim()) {
                showFieldError("password", "글 비밀번호는 " + limits.password_min + "~" + limits.password_max + "자로 입력해 주세요.");
                return;
            }
            body = { board: board, pid: modifyPid, password: password };
        }
        if (kind) {
            body.kind = kind;
        }
        setBusy(true);
        setStatus("원래 글을 불러오고 있어요.");
        post.postJson("/write/modify/load", body, 45000).then(function (result) {
            setBusy(false);
            var outcome = post.interpret(result.status, result.payload);
            var payload = outcome.payload || {};
            var message = typeof payload.message === "string" ? payload.message : "";
            if (outcome.action === "success") {
                fillModify(payload);
            } else if (outcome.action === "visitor_check_required") {
                setStatus(message || "아무것도 전송하지 않았어요. 입력을 보관한 뒤 페이지를 새로고침해 주세요.", "error");
            } else if (outcome.action === "invalid") {
                showFieldError(String(payload.field || ""), message || "입력한 내용을 확인해 주세요.");
            } else if (outcome.action === "retry") {
                startRetryWait(outcome.seconds, message);
            } else if (outcome.action === "locked") {
                setStatus("이 브라우저의 글쓰기가 잠겨 있어요.", "error", { href: post.unlockUrl(), text: "새 탭에서 잠금 해제", newTab: true });
            } else if (outcome.action === "login_needed") {
                setStatus(message || "디시 계정에 다시 로그인해야 해요.", "error", post.accountLink(payload));
            } else {
                // 불러오기는 글을 바꾸지 않으므로 다시 눌러도 된다.
                setStatus(message || "원래 글을 불러오지 못했어요. 잠시 후 다시 시도해 주세요.", "error");
            }
        });
    }

    function imageFiles(list) {
        return Array.prototype.filter.call(list || [], function (file) {
            return file && /^image\//.test(file.type || "");
        });
    }

    function bindImageInputs() {
        if (!el.images || !el.imageInput || !el.imageAdd) {
            return;
        }
        el.imageAdd.addEventListener("click", function () {
            el.imageInput.click();
        });
        el.imageInput.addEventListener("change", function () {
            addImages(Array.prototype.slice.call(el.imageInput.files || []));
            el.imageInput.value = "";
        });
        // 본문에 붙여 넣은 사진은 사진 칸으로 옮긴다. 글자는 평소처럼 붙는다.
        el.body.addEventListener("paste", function (event) {
            var files = imageFiles(event.clipboardData && event.clipboardData.files);
            if (files.length) {
                event.preventDefault();
                rememberCaret();
                addImages(files);
            }
        });
        ["select", "keyup", "click", "input", "blur"].forEach(function (name) {
            el.body.addEventListener(name, rememberCaret);
        });
        [el.images, el.body].forEach(function (zone) {
            zone.addEventListener("dragover", function (event) {
                if (event.dataTransfer && Array.prototype.indexOf.call(event.dataTransfer.types || [], "Files") >= 0) {
                    event.preventDefault();
                    el.images.classList.add("is-dragover");
                }
            });
            zone.addEventListener("dragleave", function () {
                el.images.classList.remove("is-dragover");
            });
            zone.addEventListener("drop", function (event) {
                el.images.classList.remove("is-dragover");
                var files = event.dataTransfer ? Array.prototype.slice.call(event.dataTransfer.files || []) : [];
                if (files.length) {
                    event.preventDefault();
                    addImages(files);
                }
            });
        });
        renderImages(null);
    }

    function submit(event) {
        event.preventDefault();
        if (!state.loaded) {
            // 수정 화면에서 비밀번호 칸의 Enter는 원래 글 불러오기다.
            loadModify();
            return;
        }
        if (state.sending || state.retryUntil > Date.now()) {
            return;
        }
        var pending = state.images.filter(function (item) {
            return !item.checked;
        });
        if (pending.length) {
            setBusy(true);
            setStatus("사진을 확인하고 있어요.");
            Promise.all(pending.map(function (item) {
                return item.check;
            })).then(function () {
                setBusy(false);
                renderImages(null);
                setStatus("");
                submit({ preventDefault: function () {} });
            });
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
            imageCount: state.images.length + state.originals.length,
            captcha: answer,
            account: state.mode === "account"
        }, limits);
        if (problem) {
            showFieldError(problem.field, problem.message);
            return;
        }
        if (brokenMessage()) {
            showFieldError("images", brokenMessage());
            return;
        }
        var markerProblem = (state.images.length ? checkMarkers(el.body.value, state.images.length) : null)
            || (state.originals.length ? checkMarkers(el.body.value, state.originals.length, "원본") : null);
        if (markerProblem) {
            showFieldError("body", markerProblem);
            return;
        }
        var body = {
            board: board,
            subject: el.subject.value.trim(),
            body: el.body.value
        };
        if (state.mode === "account") {
            body.mode = "account";
        } else {
            body.nickname = el.nickname.value.trim();
            body.password = el.password.value;
        }
        if (kind) {
            body.kind = kind;
        }
        Object.assign(body, headPayload());
        if (answer) {
            body.captcha_key = answer.key;
            body.captcha_code = answer.code;
        }
        var uploads = newImages();
        if (modifyPid) {
            // 사진 순서: 원래 사진은 원래 블록 번호, 새 사진은 올리는 순서로 가리킨다.
            var nextUpload = 0;
            body.pid = modifyPid;
            body.fingerprint = state.fingerprint;
            body.photos = state.images.map(function (item) {
                return item.file ? { "new": nextUpload++ } : { keep: item.keep };
            });
            body.originals = state.originals.map(function (item) {
                return item.index;
            });
        }
        setBusy(true);
        var request;
        if (uploads.length) {
            // 본문에서 [사진 N] 줄을 지운 사진은 글 맨 아래에 붙인다.
            body.image_position = "bottom";
            var data = new FormData();
            data.append("payload", JSON.stringify(body));
            uploads.forEach(function (item) {
                data.append("images", item.file, item.file.name || "image");
            });
            setStatus("사진을 올리고 있어요. 0%");
            request = post.postForm("/write", data, {
                onProgress: function (ratio) {
                    setStatus("사진을 올리고 있어요. " + Math.min(99, Math.floor(ratio * 100)) + "%");
                },
                onUploaded: function () {
                    setStatus(modifyPid ? "수정하고 있어요." : "등록하고 있어요.");
                }
            });
        } else {
            setStatus(modifyPid ? "수정하고 있어요." : "등록하고 있어요.");
            request = post.postJson("/write", body, 45000);
        }
        request.then(function (result) {
            setBusy(false);
            renderImages(null);
            if (result.notSent) {
                // 서버가 요청을 다 받지 못했으니 보이는 캡챠 답도 아직 쓰이지 않았다.
                setStatus(modifyPid
                    ? "사진을 다 올리기 전에 연결이 끊겼어요. 글은 바뀌지 않았어요. 다시 수정해 주세요."
                    : "사진을 다 올리기 전에 연결이 끊겼어요. 글은 등록되지 않았어요. 다시 등록해 주세요.", "error");
                return;
            }
            handle(post.interpret(result.status, result.payload));
        });
    }

    function handle(outcome) {
        var payload = outcome.payload || {};
        var message = typeof payload.message === "string" ? payload.message : "";
        if (outcome.action === "visitor_check_required") {
            setStatus(message || "아무것도 전송하지 않았어요. 입력을 보관한 뒤 페이지를 새로고침해 주세요.", "error");
            return;
        }
        // 한 번 보낸 캡챠 답은 다시 쓰지 않는다. 다시 필요하면 서버가 새 문제를 준다.
        if (outcome.action !== "captcha" && captcha) {
            captcha.hide();
        }
        if (outcome.action === "success") {
            if (state.mode !== "account") {
                saveIdentity();
            }
            state.leaving = true;
            setStatus(modifyPid ? "수정했어요. 글로 이동해요." : "등록했어요. 목록으로 이동해요.", "success");
            root.location.assign(post.safePath(payload.read_url) || post.safePath(payload.board_url) || backUrl);
            return;
        }
        if (outcome.action === "captcha") {
            setStatus(message || "보이는 문자를 입력하고 다시 등록해 주세요.", "error");
            // 캡챠 칸이 같은 안내를 보여 주므로 상태 줄은 화면 낭독기에만 남긴다.
            el.status.classList.add("sr-only");
            captcha.show(payload.captcha);
            return;
        }
        if (outcome.action === "invalid") {
            showFieldError(String(payload.field || ""), message || "입력한 내용을 확인해 주세요.");
            return;
        }
        if (outcome.action === "rejected") {
            setStatus(message || (modifyPid ? "디시가 글 수정을 받지 않았어요." : "디시에서 글을 받지 않았어요."), "error");
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
        if (outcome.action === "login_needed") {
            setStatus(message || "디시 계정에 다시 로그인해야 해요. 글은 보내지 않았어요.", "error", post.accountLink(payload));
            return;
        }
        if (outcome.action === "unavailable" && message) {
            setStatus(message, "error");
            return;
        }
        // 응답을 받지 못했으면 글이 올라갔는지 알 수 없으므로 다시 보내라고 하지 않는다.
        if (modifyPid) {
            setStatus(message || "수정 여부를 확인하지 못했어요. 글을 먼저 확인해 주세요.", "error", { href: backUrl, text: "글 보기", newTab: true });
            return;
        }
        setStatus("등록 여부를 확인하지 못했어요. 목록에서 먼저 확인해 주세요.", "error", { href: backUrl, text: "목록 보기", newTab: true });
    }

    restoreIdentity();
    if (modifyPid) {
        // 수정할 때 보내는 닉네임이 글쓴이 이름이 될 수 있어서 원래 이름을 먼저 채운다.
        var author = form.getAttribute("data-modify-author") || "";
        if (author) {
            el.nickname.value = author;
        }
        if (el.modifyLoad) {
            el.modifyLoad.addEventListener("click", loadModify);
        }
    }
    // 고른 말머리가 이 갤러리에 없으면 "말머리 없음"을 고른 상태로 둔다.
    if (el.head && !checkedHead() && headInputs().length) {
        headInputs()[0].checked = true;
    }
    function syncRememberNote() {
        if (el.rememberNote) {
            el.rememberNote.hidden = state.mode === "account" || !el.rememberPassword.checked;
        }
    }
    // 계정으로 쓰면 닉네임·비밀번호 칸과 기억 선택을 숨긴다.
    function applyMode() {
        var account = state.mode === "account";
        el.modeInputs.forEach(function (input) {
            input.checked = input.value === state.mode;
        });
        [el.identity, el.remember, el.passwordHint].forEach(function (node) {
            if (node) {
                node.hidden = account;
            }
        });
        if (el.accountNote) {
            el.accountNote.hidden = !account;
        }
        if (el.modeNote && el.modeInputs.length) {
            el.modeNote.textContent = account ? "디시 계정으로 올라가요." : "유동닉으로 올라가요. 서버 IP로 기록돼요.";
        }
        syncRememberNote();
    }
    el.modeInputs.forEach(function (input) {
        input.addEventListener("change", function () {
            if (!input.checked) {
                return;
            }
            state.mode = input.value === "account" ? "account" : "anonymous";
            post.saveMode(storage, state.mode);
            clearErrors();
            applyMode();
        });
    });
    applyMode();
    state.initial = draftSnapshot();
    updateCount(el.subject, el.subjectCount, limits.subject_max);
    updateCount(el.body, el.bodyCount, limits.body_max);
    fitBody();
    el.subject.addEventListener("input", function () {
        updateCount(el.subject, el.subjectCount, limits.subject_max);
    });
    el.body.addEventListener("input", function () {
        updateCount(el.body, el.bodyCount, limits.body_max);
        fitBody();
    });
    el.rememberNickname.addEventListener("change", saveIdentity);
    el.rememberPassword.addEventListener("change", saveIdentity);
    el.rememberPassword.addEventListener("change", syncRememberNote);
    bindImageInputs();
    form.addEventListener("submit", submit);
    root.addEventListener("beforeunload", function (event) {
        if (!state.leaving && isDirty()) {
            event.preventDefault();
            event.returnValue = "";
        }
    });
    syncSubmit();
})(typeof window !== "undefined" ? window : globalThis);
