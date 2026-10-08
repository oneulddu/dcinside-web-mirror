(function (root) {
    "use strict";

    // 디시 글쓰기 폼. 확인은 서버가 다시 하지만 같은 기준으로 먼저 알려 준다.
    var IDENTITY_KEY = "mirror_write_identity_v1";
    var FIELDS = ["nickname", "password", "subject", "body", "images", "captcha"];
    var IMAGE_TYPES = { "image/jpeg": true, "image/png": true, "image/gif": true, "image/webp": true };
    var IMAGE_EXTENSION = /\.(jpe?g|png|gif|webp)$/i;

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
        images: document.getElementById("write-images"),
        imageAdd: document.getElementById("write-images-add"),
        imageInput: document.getElementById("write-images-input"),
        imageList: document.getElementById("write-image-list"),
        imageCount: document.getElementById("write-images-count"),
        imagePosition: document.getElementById("write-image-position"),
        imageLive: document.getElementById("write-images-live")
    };
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
    var state = { sending: false, retryUntil: 0, retryTimer: null, leaving: false, initial: "", images: [] };
    var nextImageId = 1;
    var storage = null;
    try {
        storage = root.localStorage;
    } catch (err) {
        storage = null;
    }

    function draftSnapshot() {
        return [el.subject.value, el.body.value, el.head ? el.head.value : "", state.images.length].join("\u0000");
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
        [el.nickname, el.password, el.subject, el.body, el.head, el.rememberNickname, el.rememberPassword, el.imageAdd].forEach(function (input) {
            if (input) {
                input.disabled = busy;
            }
        });
        if (el.imageList) {
            Array.prototype.forEach.call(el.imageList.querySelectorAll("button, input"), function (control) {
                control.disabled = busy;
            });
        }
        if (el.imagePosition) {
            Array.prototype.forEach.call(el.imagePosition.querySelectorAll("input"), function (control) {
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

    function formatSize(bytes) {
        if (bytes < 1048576) {
            return Math.max(1, Math.round(bytes / 1024)) + "KB";
        }
        return (bytes / 1048576).toFixed(1).replace(/\.0$/, "") + "MB";
    }

    function imagePosition() {
        var checked = el.imagePosition ? el.imagePosition.querySelector("input:checked") : null;
        return checked && checked.value === "bottom" ? "bottom" : "top";
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
            img.alt = (index + 1) + "번째 사진";
            img.decoding = "async";
            // 브라우저가 열지 못하는 파일은 디시에서도 사진이 아니므로 미리 알려 준다.
            img.addEventListener("error", function () {
                if (!item.broken) {
                    item.broken = true;
                    showImageNote(brokenMessage());
                }
                li.classList.add("is-broken");
            });
            img.src = item.url;
            if (item.broken) {
                li.classList.add("is-broken");
            }
            var badge = document.createElement("span");
            badge.className = "write-image-order";
            badge.setAttribute("aria-hidden", "true");
            badge.textContent = String(index + 1);
            frame.appendChild(img);
            var broken = document.createElement("span");
            broken.className = "write-image-broken";
            broken.textContent = "열 수 없는 파일";
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
            var size = document.createElement("span");
            size.className = "write-image-size";
            size.textContent = formatSize(item.file.size);
            var after = imageButton("is-after", (index + 1) + "번째 사진 뒤로", "M9.5 6l6 6-6 6", function () {
                shiftImage(item.id, 1);
            });
            before.disabled = index === 0 || state.sending;
            after.disabled = index === count - 1 || state.sending;
            remove.disabled = state.sending;
            bar.appendChild(before);
            bar.appendChild(size);
            bar.appendChild(after);
            li.appendChild(frame);
            li.appendChild(bar);
            el.imageList.appendChild(li);
        });
        el.imageList.hidden = count === 0;
        if (el.imagePosition) {
            el.imagePosition.hidden = count === 0;
        }
        if (el.imageCount) {
            el.imageCount.textContent = count + "/" + limits.image_max_count;
        }
        el.imageAdd.disabled = state.sending || count >= limits.image_max_count;
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

    function addImages(files) {
        if (state.sending || !files || !files.length) {
            return;
        }
        var current = state.images.map(function (item) {
            return item.file;
        });
        var picked = pickImages(current, files, limits);
        picked.accepted.forEach(function (file) {
            var item = { id: nextImageId++, file: file, url: URL.createObjectURL(file), checked: false, broken: false };
            state.images.push(item);
            checkImage(item);
        });
        el.imageAdd.removeAttribute("aria-invalid");
        showImageNote(picked.message);
        renderImages(null);
        if (picked.accepted.length) {
            announce("사진 " + picked.accepted.length + "장을 넣었어요. 모두 " + state.images.length + "장이에요.");
        }
    }

    function removeImage(id) {
        var index = state.images.findIndex(function (item) {
            return item.id === id;
        });
        if (index < 0 || state.sending) {
            return;
        }
        URL.revokeObjectURL(state.images[index].url);
        state.images.splice(index, 1);
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
        showImageNote(brokenMessage());
        renderImages({ id: id, button: delta < 0 ? "is-before" : "is-after" });
        announce("사진을 " + (index + delta + 1) + "번째로 옮겼어요.");
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
                addImages(files);
            }
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
            imageCount: state.images.length,
            captcha: answer
        }, limits);
        if (problem) {
            showFieldError(problem.field, problem.message);
            return;
        }
        if (brokenMessage()) {
            showFieldError("images", brokenMessage());
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
        var request;
        if (state.images.length) {
            body.image_position = imagePosition();
            var data = new FormData();
            data.append("payload", JSON.stringify(body));
            state.images.forEach(function (item) {
                data.append("images", item.file, item.file.name || "image");
            });
            setStatus("사진을 올리고 있어요. 0%");
            request = post.postForm("/write", data, {
                onProgress: function (ratio) {
                    setStatus("사진을 올리고 있어요. " + Math.min(99, Math.floor(ratio * 100)) + "%");
                },
                onUploaded: function () {
                    setStatus("등록하고 있어요.");
                }
            });
        } else {
            setStatus("등록하고 있어요.");
            request = post.postJson("/write", body, 45000);
        }
        request.then(function (result) {
            setBusy(false);
            renderImages(null);
            if (result.notSent) {
                // 서버가 요청을 다 받지 못했으니 보이는 캡챠 답도 아직 쓰이지 않았다.
                setStatus("사진을 다 올리기 전에 연결이 끊겼어요. 글은 등록되지 않았어요. 다시 등록해 주세요.", "error");
                return;
            }
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
