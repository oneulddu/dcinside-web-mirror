(function (root) {
    "use strict";

    // 디시 글쓰기 폼. 확인은 서버가 다시 하지만 같은 기준으로 먼저 알려 준다.
    var IDENTITY_KEY = "mirror_write_identity_v1";
    var FIELDS = ["nickname", "password", "subject", "body", "images", "captcha", "videos", "dccons", "poll", "options"];
    var IMAGE_TYPES = { "image/jpeg": true, "image/png": true, "image/gif": true, "image/webp": true };
    var IMAGE_EXTENSION = /\.(jpe?g|png|gif|webp)$/i;
    // 본문 한 줄이 "[사진 N]"뿐이면 그 자리에 N번째 사진이 들어간다. 서버(dc_write.PHOTO_MARKER)와 같은 규칙.
    var MARKER_LINE = /^[ \t]*\[사진 ?([0-9]{1,2})\][ \t]*$/;
    var VIDEO_EXTENSION = /\.mp4$/i;
    var FORMAT_PAIRS = { b: ["<b>", "</b>"], i: ["<i>", "</i>"], u: ["<u>", "</u>"], s: ["<s>", "</s>"], link: ['<a href="https://">', "</a>"] };
    var FORMAT_INSERTS = {
        br: "<br>\n",
        table: '<table border="1">\n<tr><td>칸</td><td>칸</td></tr>\n<tr><td>칸</td><td>칸</td></tr>\n</table>\n'
    };

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
    function insertMarkers(text, start, end, numbers, label) {
        var before = text.slice(0, start);
        var after = text.slice(end);
        var lead = before && before.charAt(before.length - 1) !== "\n" ? "\n" : "";
        var tail = after.charAt(0) === "\n" ? "" : "\n";
        var inserted = lead + numbers.map(function (number) {
            return markerText(number, label);
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

    // 고른 항목(디시콘)마다 본문에 표시 줄이 한 번씩 있어야 한다. 서버(dc_write.photo_blocks)와 같은 규칙.
    function missingMarkers(text, count, label) {
        var pattern = markerPattern(label);
        var seen = {};
        String(text).split("\n").forEach(function (line) {
            var match = pattern.exec(line);
            if (match) {
                seen[Number(match[1])] = true;
            }
        });
        for (var number = 1; number <= count; number += 1) {
            if (!seen[number]) {
                return "본문에 " + markerText(number, label) + " 줄이 없어요. 넣을 자리에 그 줄을 다시 넣거나 " + label + "을 빼 주세요.";
            }
        }
        return null;
    }

    // 서버(dc_write.new_post_fields)와 같은 기준. 엔티티로 감춘 것은 서버가 다시 거른다.
    function htmlProblem(body) {
        var text = String(body || "");
        if (/<\s*(?:script|iframe|object|embed|form)\b|\bon\w+\s*=/i.test(text)
                || /javascript\s*:/i.test(text.replace(/[\x00-\x20]/g, ""))) {
            return "HTML에 스크립트, 외부 삽입 요소나 실행 가능한 속성을 넣을 수 없어요.";
        }
        return null;
    }

    // <input type="datetime-local"> 값(YYYY-MM-DDTHH:mm)을 서버 형식(YYYY-MM-DD HH:mm)으로 바꾼다.
    function localDate(value) {
        var match = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})/.exec(String(value || ""));
        return match ? match[1] + " " + match[2] : "";
    }

    // 날짜 칸은 서버와 같이 서울 시간(UTC+9, 서머타임 없음)으로 읽고 쓴다. 브라우저 시간대와 무관하다.
    var SEOUL_OFFSET = 9 * 3600000;

    function inputDate(time) {
        function pad(number) {
            return (number < 10 ? "0" : "") + number;
        }
        var date = new Date(time + SEOUL_OFFSET);
        return date.getUTCFullYear() + "-" + pad(date.getUTCMonth() + 1) + "-" + pad(date.getUTCDate())
            + "T" + pad(date.getUTCHours()) + ":" + pad(date.getUTCMinutes());
    }

    function parseDate(text) {
        var match = /^(\d{4})-(\d{2})-(\d{2}) (\d{2}):(\d{2})$/.exec(String(text || ""));
        return match ? new Date(Date.UTC(Number(match[1]), Number(match[2]) - 1, Number(match[3]), Number(match[4]), Number(match[5])) - SEOUL_OFFSET) : null;
    }

    // values: {secret, secretPassword, autoDelete, autoDeleteAt("YYYY-MM-DD HH:mm")}
    function validateOptions(values, limits, now) {
        var password = String(values.secretPassword || "");
        if (values.secret && password && (charLength(password) < limits.password_min || charLength(password) > limits.password_max)) {
            return "비밀글 비밀번호는 " + limits.password_min + "~" + limits.password_max + "자로 입력하거나 비워 주세요.";
        }
        if (values.autoDelete) {
            var date = parseDate(values.autoDeleteAt);
            if (!date) {
                return "자동 삭제 시각을 골라 주세요.";
            }
            if (date.getUTCMinutes() % 5) {
                return "자동 삭제 시각은 5분 단위로 골라 주세요.";
            }
            if (date.getTime() <= now) {
                return "자동 삭제 시각은 지금보다 뒤로 골라 주세요.";
            }
        }
        return null;
    }

    // poll: {title, items(빈 항목은 뺀 목록), multiple(0 또는 최대 선택 수), end_date?}
    function validatePoll(poll, limits, now) {
        var title = String(poll.title || "").trim();
        if (!title) {
            return "투표 제목을 입력해 주세요.";
        }
        if (charLength(title) > limits.poll_title_max) {
            return "투표 제목은 " + limits.poll_title_max + "자까지 쓸 수 있어요.";
        }
        var items = poll.items || [];
        if (items.length < 2) {
            return "투표 항목을 2개 이상 입력해 주세요.";
        }
        if (items.length > limits.poll_items_max) {
            return "투표 항목은 " + limits.poll_items_max + "개까지 넣을 수 있어요.";
        }
        for (var i = 0; i < items.length; i += 1) {
            if (charLength(items[i]) > limits.poll_item_max) {
                return (i + 1) + "번째 항목은 " + limits.poll_item_max + "자까지 쓸 수 있어요.";
            }
        }
        if (poll.multiple !== 0 && !(poll.multiple >= 2 && poll.multiple <= items.length)) {
            return "고를 수 있는 수를 입력한 항목 수 안에서 골라 주세요.";
        }
        if (poll.end_date !== undefined) {
            var date = parseDate(poll.end_date);
            if (!date || date.getTime() <= now) {
                return "투표 마감은 지금보다 뒤로 골라 주세요.";
            }
        }
        return null;
    }

    // 새로 고른 동영상 중 넣을 수 있는 것만 고른다. otherBytes는 함께 올리는 새 사진의 크기 합이다.
    function pickVideos(existing, incoming, limits, otherBytes) {
        var accepted = [];
        var skipped = { type: 0, size: 0, count: 0, total: 0 };
        var total = (otherBytes || 0) + existing.reduce(function (sum, file) {
            return sum + file.size;
        }, 0);
        Array.prototype.forEach.call(incoming || [], function (file) {
            var typeOk = file.type ? file.type === "video/mp4" : VIDEO_EXTENSION.test(file.name || "");
            if (!typeOk) {
                skipped.type += 1;
            } else if (!file.size || file.size > limits.video_max_bytes) {
                skipped.size += 1;
            } else if (existing.length + accepted.length >= limits.video_max_count) {
                skipped.count += 1;
            } else if (total + file.size > limits.upload_total_bytes) {
                skipped.total += 1;
            } else {
                accepted.push(file);
                total += file.size;
            }
        });
        var messages = [];
        if (skipped.type) {
            messages.push("MP4가 아닌 파일 " + skipped.type + "개는 넣지 않았어요.");
        }
        if (skipped.size) {
            messages.push(megabytes(limits.video_max_bytes) + "를 넘거나 빈 동영상 " + skipped.size + "개는 넣지 않았어요.");
        }
        if (skipped.count) {
            messages.push("동영상은 " + limits.video_max_count + "개까지라 " + skipped.count + "개는 넣지 않았어요.");
        }
        if (skipped.total) {
            messages.push("사진과 합쳐 " + megabytes(limits.upload_total_bytes) + "를 넘어서 " + skipped.total + "개는 넣지 않았어요.");
        }
        return { accepted: accepted, message: messages.join(" ") };
    }

    // HTML 서식 단추. 고른 글자를 태그로 감싸거나 커서 자리에 넣고, 새로 고를 범위를 돌려준다.
    function wrapSelection(text, start, end, format, color) {
        var before = text.slice(0, start);
        var selected = text.slice(start, end);
        var after = text.slice(end);
        var insert;
        if (format === "br" || format === "table") {
            insert = FORMAT_INSERTS[format];
            return { text: before + insert + after, start: start + insert.length, end: start + insert.length };
        }
        var pair = FORMAT_PAIRS[format];
        if (format === "color") {
            if (!/^#[0-9a-f]{6}$/i.test(String(color || ""))) {
                return null;
            }
            pair = ['<span style="color:' + color.toLowerCase() + '">', "</span>"];
        }
        if (!pair) {
            return null;
        }
        insert = pair[0] + selected + pair[1];
        return { text: before + insert + after, start: start + pair[0].length, end: start + pair[0].length + selected.length };
    }

    root.MirrorWriteForm = {
        missingMarkers: missingMarkers,
        htmlProblem: htmlProblem,
        localDate: localDate,
        validateOptions: validateOptions,
        validatePoll: validatePoll,
        pickVideos: pickVideos,
        wrapSelection: wrapSelection,
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
        identity: form.querySelector(".write-identity"),
        author: form.querySelector(".write-author"),
        remember: form.querySelector(".write-remember"),
        passwordHint: document.getElementById("write-password-hint"),
        accountNote: document.getElementById("write-account-note"),
        modeNote: document.getElementById("write-mode-note"),
        videoAdd: document.getElementById("write-videos-add"),
        videoInput: document.getElementById("write-videos-input"),
        videoList: document.getElementById("write-video-list"),
        videoCount: document.getElementById("write-videos-count"),
        dcconAdd: document.getElementById("write-dccon-add"),
        dcconPanel: document.getElementById("write-dccon-panel"),
        dcconClose: document.getElementById("write-dccon-close"),
        dcconPicker: document.getElementById("write-dccon-picker"),
        dcconPackage: document.getElementById("write-dccon-package"),
        dcconStatus: document.getElementById("write-dccon-status"),
        dcconGrid: document.getElementById("write-dccon-grid"),
        dcconList: document.getElementById("write-dccon-list"),
        pollAdd: document.getElementById("write-poll-add"),
        poll: document.getElementById("write-poll"),
        pollRemove: document.getElementById("write-poll-remove"),
        pollTitle: document.getElementById("write-poll-title"),
        pollItems: document.getElementById("write-poll-items"),
        pollAddItem: document.getElementById("write-poll-add-item"),
        pollMultiple: document.getElementById("write-poll-multiple"),
        pollEnd: document.getElementById("write-poll-end"),
        pollPreview: document.getElementById("write-poll-preview"),
        html: document.getElementById("write-html"),
        htmlHint: document.getElementById("write-html-hint"),
        format: document.getElementById("write-format"),
        formatColor: document.getElementById("write-format-color"),
        noRepost: document.getElementById("write-no-repost"),
        secret: document.getElementById("write-secret"),
        secretField: document.getElementById("write-secret-field"),
        secretPassword: document.getElementById("write-secret-password"),
        autoDelete: document.getElementById("write-auto-delete"),
        autoDeleteField: document.getElementById("write-auto-delete-field"),
        autoDeleteAt: document.getElementById("write-auto-delete-at")
    };
    // 한 글에 넣는 디시콘 수. 서버는 따로 막지 않지만 화면과 표시 번호(두 자리)를 위해 둔다.
    var DCCON_MAX = 20;
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
        image_total_bytes: Number(form.getAttribute("data-image-total-bytes")) || 31457280,
        video_max_count: Number(form.getAttribute("data-video-max-count")) || 3,
        video_max_bytes: Number(form.getAttribute("data-video-max-bytes")) || 20971520,
        upload_total_bytes: Number(form.getAttribute("data-upload-total-bytes")) || 31457280,
        poll_title_max: Number(form.getAttribute("data-poll-title-max")) || 40,
        poll_item_max: Number(form.getAttribute("data-poll-item-max")) || 30,
        poll_items_max: Number(form.getAttribute("data-poll-items-max")) || 20
    };
    var board = form.getAttribute("data-board") || "";
    var kind = form.getAttribute("data-kind") || "";
    var backUrl = post.safePath(form.getAttribute("data-back-url")) || "/";
    var captcha = post.createCaptcha("write-captcha", "article", board);
    // images: 새 사진({file, url})과 수정할 글의 원래 사진({keep, url})이 본문 순서대로 섞인다.
    // originals: 수정할 글의 사진이 아닌 원래 블록({index, preview}).
    var state = { sending: false, retryUntil: 0, retryTimer: null, leaving: false, initial: "", images: [], caret: null,
        originals: [], fingerprint: "", originalHead: null, loaded: !modifyPid,
        // 새 글 전용: 동영상 파일, 고른 디시콘({package_idx, detail_idx, title, image_url}), 계정별 디시콘 목록.
        videos: [], dccons: [], dcconCache: {}, dcconLoading: "" };
    var nextImageId = 1;
    var storage = null;
    try {
        storage = root.localStorage;
    } catch (err) {
        storage = null;
    }
    // 수정 화면은 연 방식(주소의 mode)을 끝까지 쓰고 상단 작성자 메뉴를 따르지 않는다. 새 글은 상단 메뉴의 값을 쓴다.
    // 기억한 계정이 서버에 없으면 빈 값으로 두고 상단 메뉴에서 다시 고르게 한다.
    // 수정 주소에 account가 없을 때만 계정 1로 본다. 빈 값·잘못된 값은 그대로 보내 서버가 거절한다.
    var pinnedAccount = form.getAttribute("data-account");
    state.mode = form.getAttribute("data-mode") === "account" ? "account:" + (pinnedAccount === null ? "1" : pinnedAccount)
        : (modifyPid ? "anonymous" : post.authorMode());
    // 로그인한 계정의 닉네임. 작성자 안내에 붙인다.
    var authorNames = {};

    function isAccount() {
        return state.mode.indexOf("account:") === 0;
    }

    // 계정으로 쓰면 계정 번호. 수정 주소의 값은 그대로 보내 서버가 확인한다.
    function accountSlot() {
        return isAccount() ? state.mode.slice(8) : "";
    }

    function draftSnapshot() {
        return [el.subject.value, el.body.value, headValue(), state.images.length, state.originals.length,
            state.videos.length, state.dccons.length, pollOpen()].join("\u0000");
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
        if (field === "videos") {
            return el.videoAdd;
        }
        if (field === "dccons") {
            return el.dcconAdd && !el.dcconAdd.hidden ? el.dcconAdd : (el.dcconList ? el.body : null);
        }
        if (field === "poll") {
            return el.pollTitle;
        }
        if (field === "options") {
            return el.noRepost;
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
        [el.nickname, el.password, el.subject, el.body, el.rememberNickname, el.rememberPassword, el.imageAdd, el.modifyLoad].forEach(function (input) {
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
        [el.dcconAdd, el.dcconPackage, el.dcconClose, el.pollAdd, el.pollRemove, el.pollTitle, el.pollAddItem, el.pollMultiple,
            el.pollEnd, el.pollPreview, el.html, el.formatColor, el.noRepost, el.secret, el.secretPassword, el.autoDelete,
            el.autoDeleteAt].forEach(function (input) {
            if (input) {
                input.disabled = busy;
            }
        });
        [el.videoList, el.dcconList, el.dcconGrid, el.pollItems, el.format].forEach(function (zone) {
            if (zone) {
                Array.prototype.forEach.call(zone.querySelectorAll("button, input"), function (control) {
                    control.disabled = busy;
                });
            }
        });
        if (el.videoAdd) {
            el.videoAdd.disabled = busy || state.videos.length >= limits.video_max_count;
        }
        if (!busy && el.pollItems) {
            syncPollItems();
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
            el.imageCount.classList.toggle("is-zero", uploads === 0);
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
        var body = { board: board, pid: modifyPid, mode: "account", account: accountSlot() };
        if (!isAccount()) {
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
                    addFiles(files);
                }
            });
        });
        renderImages(null);
    }

    // ---- 새 글 전용: 동영상, 디시콘, 투표, HTML 서식, 글 설정 ----

    function uploadBytes(files) {
        return files.reduce(function (sum, file) {
            return sum + file.size;
        }, 0);
    }

    function newImageFiles() {
        return newImages().map(function (item) {
            return item.file;
        });
    }

    function showListNote(field, message) {
        var error = errorElement(field);
        if (error) {
            error.textContent = message || "";
            error.hidden = !message;
        }
    }

    // 커서 자리(없으면 본문 끝)에 표시 줄을 넣는다.
    function insertMarked(label, numbers) {
        var length = el.body.value.length;
        var start = state.caret ? Math.min(state.caret.start, length) : length;
        var end = state.caret ? Math.min(state.caret.end, length) : length;
        var result = insertMarkers(el.body.value, start, end, numbers, label);
        setBody(result.text, result.caret);
    }

    // 목록에서 하나를 빼고 본문의 표시 번호를 맞춘다.
    function removeMarked(list, index, label, render) {
        if (state.sending || index < 0 || index >= list.length) {
            return;
        }
        var map = {};
        map[index + 1] = 0;
        for (var number = index + 2; number <= list.length; number += 1) {
            map[number] = number - 1;
        }
        list.splice(index, 1);
        setBody(renumberMarkers(el.body.value, map, label), state.caret ? state.caret.start : el.body.value.length);
        render(index);
        announce(label + " " + (index + 1) + "을 뺐어요.");
    }

    function attachmentRow(index, label, previewText, thumb, onRemove) {
        var li = document.createElement("li");
        li.className = "write-original";
        if (thumb) {
            var img = document.createElement("img");
            img.className = "write-attachment-thumb";
            img.alt = "";
            img.src = thumb;
            li.appendChild(img);
        }
        var name = document.createElement("span");
        name.className = "write-original-label";
        name.textContent = markerText(index + 1, label);
        var preview = document.createElement("span");
        preview.className = "write-original-preview";
        preview.textContent = previewText;
        var remove = document.createElement("button");
        remove.type = "button";
        remove.className = "comment-reply-btn write-original-remove";
        remove.textContent = "빼기";
        remove.setAttribute("aria-label", markerText(index + 1, label) + " 빼기");
        remove.disabled = state.sending;
        remove.addEventListener("click", onRemove);
        li.appendChild(name);
        li.appendChild(preview);
        li.appendChild(remove);
        return li;
    }

    function focusRow(list, focusIndex, fallback) {
        if (focusIndex === undefined || !list) {
            return;
        }
        var buttons = list.querySelectorAll(".write-original-remove");
        var target = buttons[Math.min(focusIndex, buttons.length - 1)];
        (target || fallback || el.body).focus();
    }

    function renderVideos(focusIndex) {
        if (!el.videoList) {
            return;
        }
        el.videoList.textContent = "";
        state.videos.forEach(function (file, index) {
            el.videoList.appendChild(attachmentRow(index, "동영상", (file.name || "동영상") + " · " + formatSize(file.size), "", function () {
                removeMarked(state.videos, index, "동영상", renderVideos);
            }));
        });
        el.videoList.hidden = state.videos.length === 0;
        if (el.videoCount) {
            el.videoCount.textContent = state.videos.length + "/" + limits.video_max_count;
            el.videoCount.classList.toggle("is-zero", state.videos.length === 0);
        }
        if (el.videoAdd) {
            el.videoAdd.disabled = state.sending || state.videos.length >= limits.video_max_count;
        }
        focusRow(el.videoList, focusIndex, el.videoAdd && !el.videoAdd.disabled ? el.videoAdd : el.body);
    }

    function addVideos(files) {
        if (state.sending || !files || !files.length || !el.videoAdd) {
            return;
        }
        var picked = pickVideos(state.videos, files, limits, uploadBytes(newImageFiles()));
        var first = state.videos.length + 1;
        picked.accepted.forEach(function (file) {
            state.videos.push(file);
        });
        if (picked.accepted.length) {
            insertMarked("동영상", picked.accepted.map(function (file, offset) {
                return first + offset;
            }));
        }
        el.videoAdd.removeAttribute("aria-invalid");
        showListNote("videos", picked.message);
        renderVideos();
        if (picked.accepted.length) {
            announce("동영상 " + picked.accepted.length + "개를 본문에 넣었어요.");
        }
    }

    function isVideoFile(file) {
        return /^video\//.test(file.type || "") || (!file.type && VIDEO_EXTENSION.test(file.name || ""));
    }

    // 끌어다 놓은 파일을 사진과 동영상으로 나눈다. 수정 화면에는 동영상 칸이 없다.
    function addFiles(files) {
        if (!el.videoAdd) {
            addImages(files);
            return;
        }
        var videos = files.filter(isVideoFile);
        var rest = files.filter(function (file) {
            return !isVideoFile(file);
        });
        if (videos.length) {
            addVideos(videos);
        }
        if (rest.length) {
            addImages(rest);
        }
    }

    function setDcconStatus(text, link) {
        if (!el.dcconStatus) {
            return;
        }
        el.dcconStatus.textContent = text || "";
        if (link) {
            el.dcconStatus.appendChild(document.createTextNode(" "));
            var anchor = document.createElement("a");
            anchor.href = link.href;
            anchor.textContent = link.text;
            anchor.target = "_blank";
            anchor.rel = "noopener";
            el.dcconStatus.appendChild(anchor);
        }
    }

    function renderDccons(focusIndex) {
        if (!el.dcconList) {
            return;
        }
        el.dcconList.textContent = "";
        state.dccons.forEach(function (item, index) {
            el.dcconList.appendChild(attachmentRow(index, "디시콘", item.title || "디시콘", item.image_url, function () {
                removeMarked(state.dccons, index, "디시콘", renderDccons);
            }));
        });
        el.dcconList.hidden = state.dccons.length === 0;
        focusRow(el.dcconList, focusIndex, el.dcconAdd && !el.dcconAdd.hidden ? el.dcconAdd : el.body);
    }

    function addDccon(pack, icon) {
        if (state.sending || !isAccount()) {
            return;
        }
        if (state.dccons.length >= DCCON_MAX) {
            showListNote("dccons", "디시콘은 한 글에 " + DCCON_MAX + "개까지 넣을 수 있어요.");
            return;
        }
        state.dccons.push({ package_idx: pack.package_idx, detail_idx: icon.detail_idx, title: icon.title || "",
            image_url: post.safePath(icon.image_url), slot: accountSlot() });
        insertMarked("디시콘", [state.dccons.length]);
        showListNote("dccons", "");
        renderDccons();
        announce("디시콘을 본문에 넣었어요. 모두 " + state.dccons.length + "개예요.");
    }

    function renderDcconIcons() {
        if (!el.dcconGrid) {
            return;
        }
        var packages = state.dcconCache[accountSlot()] || [];
        var pack = packages[Number(el.dcconPackage.value) || 0];
        el.dcconGrid.textContent = "";
        if (!pack) {
            return;
        }
        (pack.icons || []).forEach(function (icon) {
            var src = post.safePath(icon.image_url);
            if (!src) {
                return;
            }
            var button = document.createElement("button");
            button.type = "button";
            button.className = "write-dccon-icon";
            button.setAttribute("aria-label", icon.title || "디시콘");
            button.title = icon.title || "";
            button.disabled = state.sending;
            var img = document.createElement("img");
            img.alt = "";
            img.loading = "lazy";
            img.src = src;
            button.appendChild(img);
            button.addEventListener("click", function () {
                addDccon(pack, icon);
            });
            el.dcconGrid.appendChild(button);
        });
        setDcconStatus(el.dcconGrid.children.length ? "" : "이 묶음에는 넣을 수 있는 디시콘이 없어요.");
    }

    function renderDcconPackages() {
        var packages = state.dcconCache[accountSlot()] || [];
        el.dcconPackage.textContent = "";
        if (!packages.length) {
            el.dcconPicker.hidden = true;
            el.dcconGrid.textContent = "";
            setDcconStatus("이 계정에 가진 디시콘이 없어요.");
            return;
        }
        packages.forEach(function (pack, index) {
            var option = document.createElement("option");
            option.value = String(index);
            option.textContent = pack.title || "묶음 " + (index + 1);
            el.dcconPackage.appendChild(option);
        });
        el.dcconPicker.hidden = false;
        renderDcconIcons();
    }

    // 목록은 서버가 계정 로그인으로 디시에서 읽는다. 계정마다 한 번만 받아 둔다.
    function loadDccons() {
        var slot = accountSlot();
        if (!slot) {
            return;
        }
        if (state.dcconCache[slot]) {
            renderDcconPackages();
            return;
        }
        if (state.dcconLoading === slot) {
            return;
        }
        state.dcconLoading = slot;
        el.dcconPicker.hidden = true;
        el.dcconGrid.textContent = "";
        setDcconStatus("디시콘을 불러오고 있어요.");
        root.fetch("/write/dccons?account=" + encodeURIComponent(slot), {
            credentials: "same-origin",
            headers: { "Accept": "application/json" }
        }).then(function (response) {
            return response.json().catch(function () {
                return null;
            });
        }, function () {
            return null;
        }).then(function (payload) {
            if (state.dcconLoading === slot) {
                state.dcconLoading = "";
            }
            // 기다리는 사이 상단 메뉴에서 다른 계정을 골랐으면 그 계정 목록을 따로 받는다.
            if (accountSlot() !== slot || el.dcconPanel.hidden) {
                return;
            }
            if (payload && payload.ok === true && Array.isArray(payload.packages)) {
                state.dcconCache[slot] = payload.packages;
                renderDcconPackages();
                return;
            }
            var message = payload && typeof payload.message === "string" ? payload.message : "디시콘을 불러오지 못했어요. 잠시 뒤 다시 열어 주세요.";
            setDcconStatus(message, payload && payload.code === "login_needed" ? post.accountLink(payload) : null);
        });
    }

    function openDccon() {
        el.dcconPanel.hidden = false;
        el.dcconAdd.setAttribute("aria-expanded", "true");
        loadDccons();
        (el.dcconPicker.hidden ? el.dcconClose : el.dcconPackage).focus();
    }

    function closeDccon(focus) {
        if (!el.dcconPanel) {
            return;
        }
        el.dcconPanel.hidden = true;
        el.dcconAdd.setAttribute("aria-expanded", "false");
        if (focus && !el.dcconAdd.hidden) {
            el.dcconAdd.focus();
        }
    }

    // 디시콘은 디시 계정으로 쓸 때만 넣을 수 있다.
    function syncDcconTool() {
        if (!el.dcconAdd) {
            return;
        }
        el.dcconAdd.hidden = !isAccount();
        if (!isAccount()) {
            closeDccon(false);
        } else if (!el.dcconPanel.hidden) {
            loadDccons();
        }
    }

    function pollOpen() {
        return Boolean(el.poll && !el.poll.hidden);
    }

    function pollInputs() {
        return el.pollItems ? Array.prototype.slice.call(el.pollItems.querySelectorAll("input")) : [];
    }

    function syncPollMultiple() {
        var count = Math.max(2, pollInputs().length);
        var current = Number(el.pollMultiple.value) || 0;
        el.pollMultiple.textContent = "";
        var single = document.createElement("option");
        single.value = "0";
        single.textContent = "하나만";
        el.pollMultiple.appendChild(single);
        for (var number = 2; number <= count; number += 1) {
            var option = document.createElement("option");
            option.value = String(number);
            option.textContent = number + "개까지";
            el.pollMultiple.appendChild(option);
        }
        el.pollMultiple.value = String(Math.min(current, count));
    }

    function syncPollItems() {
        var inputs = pollInputs();
        inputs.forEach(function (input, index) {
            input.setAttribute("aria-label", (index + 1) + "번째 항목");
            input.placeholder = "항목 " + (index + 1);
            var remove = input.parentNode.querySelector("button");
            remove.setAttribute("aria-label", (index + 1) + "번째 항목 빼기");
            remove.disabled = state.sending || inputs.length <= 2;
        });
        el.pollAddItem.disabled = state.sending || inputs.length >= limits.poll_items_max;
        syncPollMultiple();
    }

    function addPollItem(focus) {
        if (pollInputs().length >= limits.poll_items_max) {
            return;
        }
        var li = document.createElement("li");
        li.className = "write-poll-item";
        var input = document.createElement("input");
        input.className = "write-input";
        input.type = "text";
        input.autocomplete = "off";
        input.maxLength = limits.poll_item_max * 2;
        input.setAttribute("aria-describedby", "write-poll-error");
        var remove = document.createElement("button");
        remove.type = "button";
        remove.className = "comment-reply-btn";
        remove.textContent = "빼기";
        remove.addEventListener("click", function () {
            var index = pollInputs().indexOf(input);
            li.parentNode.removeChild(li);
            syncPollItems();
            var inputs = pollInputs();
            (inputs[Math.min(index, inputs.length - 1)] || el.pollTitle).focus();
        });
        li.appendChild(input);
        li.appendChild(remove);
        el.pollItems.appendChild(li);
        syncPollItems();
        if (focus) {
            input.focus();
        }
    }

    function openPoll() {
        if (!pollOpen()) {
            el.poll.hidden = false;
            el.pollAdd.setAttribute("aria-expanded", "true");
            while (pollInputs().length < 2) {
                addPollItem(false);
            }
        }
        el.pollTitle.focus();
    }

    function removePoll() {
        el.poll.hidden = true;
        el.pollAdd.setAttribute("aria-expanded", "false");
        el.pollTitle.value = "";
        el.pollItems.textContent = "";
        el.pollEnd.value = "";
        el.pollPreview.checked = false;
        el.pollMultiple.value = "0";
        showListNote("poll", "");
        el.pollAdd.focus();
    }

    function pollPayload() {
        var poll = {
            title: el.pollTitle.value.trim(),
            items: pollInputs().map(function (input) {
                return input.value.trim();
            }).filter(Boolean),
            multiple: Number(el.pollMultiple.value) || 0,
            preview: el.pollPreview.checked
        };
        var end = localDate(el.pollEnd.value);
        if (end) {
            poll.end_date = end;
        }
        return poll;
    }

    function applyHtml() {
        var on = Boolean(el.html && el.html.checked);
        if (el.format) {
            el.format.hidden = !on;
        }
        if (el.htmlHint) {
            el.htmlHint.hidden = !on;
        }
        el.body.classList.toggle("is-html", on);
    }

    function applyFormat(format, color) {
        var start = state.caret ? state.caret.start : el.body.selectionStart;
        var end = state.caret ? state.caret.end : el.body.selectionEnd;
        var result = wrapSelection(el.body.value, start, end, format, color);
        if (!result) {
            return;
        }
        el.body.value = result.text;
        el.body.focus();
        el.body.setSelectionRange(result.start, result.end);
        state.caret = { start: result.start, end: result.end };
        updateCount(el.body, el.bodyCount, limits.body_max);
        fitBody();
    }

    function applyOptions() {
        if (el.secretField) {
            el.secretField.hidden = !el.secret.checked;
        }
        if (el.autoDeleteField) {
            el.autoDeleteField.hidden = !el.autoDelete.checked;
            if (el.autoDelete.checked && !el.autoDeleteAt.value) {
                // 처음 켜면 하루 뒤(서울 시간)를 5분 단위로 올려 채운다.
                el.autoDeleteAt.value = inputDate(Math.ceil((Date.now() + 86400000) / 300000) * 300000);
            }
        }
    }

    function optionsPayload() {
        if (!el.noRepost) {
            return null;
        }
        var options = { no_repost: el.noRepost.checked, secret: el.secret.checked };
        if (el.secret.checked && el.secretPassword.value) {
            options.secret_password = el.secretPassword.value;
        }
        var at = localDate(el.autoDeleteAt.value);
        if (el.autoDelete.checked && at) {
            options.auto_delete = at;
        }
        return options.no_repost || options.secret || options.auto_delete ? options : null;
    }

    // 새 글 도구의 입력을 서버와 같은 기준으로 먼저 확인한다.
    function extrasProblem() {
        var body = el.body.value;
        var problem = state.videos.length ? checkMarkers(body, state.videos.length, "동영상") : null;
        if (problem) {
            return { field: "body", message: problem };
        }
        if (state.dccons.length) {
            if (!isAccount()) {
                return { field: "dccons", message: "디시콘은 디시 계정으로 쓸 때만 넣을 수 있어요. 디시콘을 빼거나 상단 작성자 메뉴에서 계정을 골라 주세요." };
            }
            if (state.dccons.some(function (item) {
                return item.slot !== accountSlot();
            })) {
                return { field: "dccons", message: "다른 계정에서 고른 디시콘이 있어요. 빼고 지금 계정의 디시콘으로 다시 넣어 주세요." };
            }
            problem = checkMarkers(body, state.dccons.length, "디시콘") || missingMarkers(body, state.dccons.length, "디시콘");
            if (problem) {
                return { field: "body", message: problem };
            }
        }
        if (el.html && el.html.checked) {
            problem = htmlProblem(body);
            if (problem) {
                return { field: "body", message: problem };
            }
        }
        if (pollOpen()) {
            problem = validatePoll(pollPayload(), limits, Date.now());
            if (problem) {
                return { field: "poll", message: problem };
            }
        }
        if (el.noRepost) {
            problem = validateOptions({ secret: el.secret.checked, secretPassword: el.secretPassword.value,
                autoDelete: el.autoDelete.checked, autoDeleteAt: localDate(el.autoDeleteAt.value) }, limits, Date.now());
            if (problem) {
                return { field: "options", message: problem };
            }
        }
        if (state.videos.length && uploadBytes(newImageFiles()) + uploadBytes(state.videos) > limits.upload_total_bytes) {
            return { field: "videos", message: "사진과 동영상은 모두 합쳐 " + megabytes(limits.upload_total_bytes) + "까지 올릴 수 있어요." };
        }
        return null;
    }

    function bindExtras() {
        if (el.videoAdd && el.videoInput) {
            el.videoAdd.addEventListener("click", function () {
                el.videoInput.click();
            });
            el.videoInput.addEventListener("change", function () {
                addVideos(Array.prototype.slice.call(el.videoInput.files || []));
                el.videoInput.value = "";
            });
            renderVideos();
        }
        if (el.dcconAdd && el.dcconPanel) {
            el.dcconAdd.addEventListener("click", function () {
                if (el.dcconPanel.hidden) {
                    openDccon();
                } else {
                    closeDccon(true);
                }
            });
            el.dcconClose.addEventListener("click", function () {
                closeDccon(true);
            });
            el.dcconPackage.addEventListener("change", renderDcconIcons);
            el.dcconPanel.addEventListener("keydown", function (event) {
                if (event.key === "Escape") {
                    event.preventDefault();
                    closeDccon(true);
                }
            });
        }
        if (el.pollAdd && el.poll) {
            el.pollAdd.addEventListener("click", openPoll);
            el.pollRemove.addEventListener("click", removePoll);
            el.pollAddItem.addEventListener("click", function () {
                addPollItem(true);
            });
        }
        if (el.html) {
            el.html.addEventListener("change", applyHtml);
            applyHtml();
        }
        if (el.format) {
            el.format.addEventListener("click", function (event) {
                var button = event.target.closest ? event.target.closest("[data-format]") : null;
                if (button && !state.sending) {
                    applyFormat(button.getAttribute("data-format"));
                }
            });
            if (el.formatColor) {
                el.formatColor.addEventListener("change", function () {
                    applyFormat("color", el.formatColor.value);
                });
            }
        }
        [el.secret, el.autoDelete].forEach(function (input) {
            if (input) {
                input.addEventListener("change", applyOptions);
            }
        });
        if (el.noRepost) {
            applyOptions();
        }
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
            // 사진을 확인하는 사이 상단 메뉴에서 작성자를 바꾸면 바뀐 작성자로 보내지 않고 멈춘다.
            var modeAtSubmit = state.mode;
            setBusy(true);
            setStatus("사진을 확인하고 있어요.");
            Promise.all(pending.map(function (item) {
                return item.check;
            })).then(function () {
                setBusy(false);
                renderImages(null);
                if (state.mode !== modeAtSubmit) {
                    setStatus("사진을 확인하는 동안 작성자를 바꿔서 글을 보내지 않았어요. 다시 등록해 주세요.", "error");
                    return;
                }
                setStatus("");
                submit({ preventDefault: function () {} });
            });
            return;
        }
        clearErrors();
        setStatus("");
        if (!state.mode) {
            if (!post.authorModes().length) {
                // 상단 메뉴가 없어 고를 수 있는 것이 유동뿐이다. 알린 뒤 다시 누르면 유동으로 보낸다.
                post.setAuthorMode("anonymous");
                state.mode = "anonymous";
                applyMode();
                setStatus("전에 고른 디시 계정이 서버에 없어요. 유동으로 올리려면 다시 등록을 눌러 주세요.", "error");
                return;
            }
            setStatus("작성자를 다시 골라 주세요. 전에 고른 계정이 서버에 없어요. 상단 작성자 메뉴에서 골라 주세요.", "error");
            return;
        }
        var answer = captcha ? captcha.answer() : null;
        var problem = validateFields({
            nickname: el.nickname.value,
            password: el.password.value,
            subject: el.subject.value,
            body: el.body.value,
            // 동영상·디시콘·투표만 있어도 본문을 비울 수 있다.
            imageCount: state.images.length + state.originals.length + state.videos.length + state.dccons.length + (pollOpen() ? 1 : 0),
            captcha: answer,
            account: isAccount()
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
        var extras = modifyPid ? null : extrasProblem();
        if (extras) {
            showFieldError(extras.field, extras.message);
            return;
        }
        var body = {
            board: board,
            subject: el.subject.value.trim(),
            body: el.body.value
        };
        // 보낼 때의 작성자를 붙잡아 둔다. 결과를 받기 전에 상단 메뉴를 바꿔도 바뀌지 않는다.
        state.sentAccount = isAccount();
        if (state.sentAccount) {
            body.mode = "account";
            body.account = accountSlot();
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
        var videos = modifyPid ? [] : state.videos.slice();
        if (!modifyPid) {
            if (el.html && el.html.checked) {
                body.html = true;
            }
            var options = optionsPayload();
            if (options) {
                body.options = options;
            }
            if (pollOpen()) {
                body.poll = pollPayload();
            }
            if (state.dccons.length) {
                body.dccons = state.dccons.map(function (item) {
                    return { package_idx: item.package_idx, detail_idx: item.detail_idx };
                });
            }
        }
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
        if (uploads.length || videos.length) {
            // 본문에서 [사진 N]·[동영상 N] 줄을 지운 파일은 글 맨 아래에 붙인다.
            body.image_position = "bottom";
            var data = new FormData();
            data.append("payload", JSON.stringify(body));
            uploads.forEach(function (item) {
                data.append("images", item.file, item.file.name || "image");
            });
            videos.forEach(function (file) {
                data.append("videos", file, file.name || "video.mp4");
            });
            var what = videos.length ? (uploads.length ? "사진과 동영상을" : "동영상을") : "사진을";
            setStatus(what + " 올리고 있어요. 0%");
            request = post.postForm("/write", data, {
                onProgress: function (ratio) {
                    setStatus(what + " 올리고 있어요. " + Math.min(99, Math.floor(ratio * 100)) + "%");
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
                    : "파일을 다 올리기 전에 연결이 끊겼어요. 글은 등록되지 않았어요. 다시 등록해 주세요.", "error");
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
            if (!state.sentAccount) {
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
            el.rememberNote.hidden = isAccount() || !el.rememberPassword.checked;
        }
    }
    // 계정으로 쓰면 닉네임·비밀번호 칸과 기억 선택을 숨긴다.
    function applyMode() {
        var account = isAccount();
        [el.identity, el.remember, el.passwordHint].forEach(function (node) {
            if (node) {
                node.hidden = account;
            }
        });
        // 새 글은 위쪽 작성자 안내 한 줄로 충분하다. 계정이면 비어 버린 작성자 칸을 통째로 숨긴다.
        if (el.accountNote) {
            el.accountNote.hidden = !account || !modifyPid;
        }
        if (el.author && !modifyPid) {
            el.author.hidden = account;
        }
        if (el.modeNote && !modifyPid) {
            var modes = post.authorModes();
            el.modeNote.textContent = !state.mode ? "작성자를 상단 작성자 메뉴에서 다시 골라 주세요. 전에 고른 계정이 서버에 없어요."
                : account ? "작성자: " + post.authorLabel(state.mode, authorNames, modes.length === 2) + ". 상단 작성자 메뉴에서 바꿀 수 있고, 로그인은 계정 화면에서 해요."
                : "유동닉으로 올라가요. 서버 IP로 기록돼요." + (modes.length ? " 작성자는 상단 메뉴에서 바꿀 수 있어요." : "");
        }
        syncRememberNote();
        syncDcconTool();
    }
    // 새 글은 상단 작성자 메뉴(다른 탭 포함)를 따른다. 써 둔 내용과 닉네임·비밀번호 칸 값은 그대로 둔다.
    if (!modifyPid) {
        post.onAuthorChange(function (mode) {
            state.mode = mode;
            clearErrors();
            applyMode();
        });
        if (post.authorModes().length && typeof post.authorNames === "function") {
            post.authorNames().then(function (names) {
                authorNames = names || {};
                applyMode();
            });
        }
    }
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
    bindExtras();
    form.addEventListener("submit", submit);
    root.addEventListener("beforeunload", function (event) {
        if (!state.leaving && isDirty()) {
            event.preventDefault();
            event.returnValue = "";
        }
    });
    syncSubmit();
})(typeof window !== "undefined" ? window : globalThis);
