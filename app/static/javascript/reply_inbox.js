(function (root) {
    "use strict";

    const KEY = "mirror_reply_inbox_v1";
    const POST = "post";
    const DAY = 86400000, INTERVAL = 60000, HIDDEN_INTERVAL = 180000, HIDDEN_LIMIT = 30 * 60000;
    // 서버와 같은 코드 포인트 단위로 잘라 이모지가 반쪽으로 남지 않게 한다.
    const short = (value, max) => typeof value === "string" ? Array.from(value).slice(0, max).join("") : "";
    const id = value => typeof value === "string" && /^[1-9][0-9]{0,14}$/.test(value);
    const valid = (row, allowPost) => row && typeof row.board === "string" && /^[A-Za-z0-9_]{1,80}$/.test(row.board)
        && ["", "normal", "minor", "mini", "person"].includes(row.kind) && id(row.pid) && (id(row.id) || (allowPost && row.id === POST));
    const samePost = (a, b) => a.board === b.board && a.kind === b.kind && a.pid === b.pid;
    const sameRow = (a, b) => samePost(a, b) && a.id === b.id;

    function load(raw, now) {
        let data;
        try { data = JSON.parse(raw); } catch (_) { /* 손상된 기록은 사용하지 않는다. */ }
        const rows = key => (Array.isArray(data?.[key]) ? data[key] : [])
            .filter(row => valid(row, key === "mine") && row.at >= now - 30 * DAY && row.at <= now);
        return { mine: rows("mine").slice(0, 50), inbox: rows("inbox").slice(0, 100),
            checked: Math.min(now, Number(data?.checked) || 0), desktop: data?.desktop === true };
    }

    // base: 직접 알림을 켠 글. 첫 확인까지 달린 댓글은 기준선으로만 삼고 알리지 않는다.
    function track(state, ref, title, now, base = false) {
        if (!valid(ref, true) || state.mine.some(row => sameRow(row, ref))) return;
        state.inbox = state.inbox.filter(row => !sameRow(row, ref));
        state.mine.unshift({ board: ref.board, kind: ref.kind, pid: ref.pid, id: ref.id,
            title: title.slice(0, 100), at: now, checked: 0, floor: ref.id === POST ? "0" : ref.id, seen: [],
            ...(base ? { base: true } : {}) });
        state.mine.length = Math.min(50, state.mine.length);
    }

    // all이면 이 글의 감시와 쌓인 알림을 모두 지우고, 아니면 글 전체 댓글 감시만 멈춘다.
    function drop(state, post, all) {
        state.mine = state.mine.filter(row => !samePost(row, post) || (!all && row.id !== POST));
        if (all) state.inbox = state.inbox.filter(row => !samePost(row, post));
    }

    // 서버가 미리보기를 붙일 시작 번호: 감시 행마다 이미 확인한 가장 큰 번호 중 가장 작은 값.
    function cursor(state, post) {
        const rows = state.mine.filter(row => samePost(row, post) && !row.base);
        return rows.length ? String(Math.min(...rows.map(row => Math.max(+row.floor, ...row.seen.map(Number))))) : "";
    }

    function receive(state, post, replies, now, comments = [], previews = {}, complete = true) {
        const parents = new Map(state.mine.filter(row => samePost(row, post)).map(row => [row.id, row]));
        const watched = parents.get(POST);
        if (watched?.base) {
            // 댓글을 다 받지 못한 응답으로 기준선을 잡으면 빠진 옛 댓글이 나중에 새 알림으로 쏟아진다.
            if (complete) {
                watched.floor = String(Math.max(+watched.floor, ...comments.filter(id).map(Number)));
                delete watched.base;
            }
            // 기준선을 잡는 확인에서는 내 댓글의 답글을 내 댓글 기록만으로 판단해 묻히지 않게 한다.
            parents.delete(POST);
        }
        const targets = new Map();
        if (parents.has(POST)) for (const child of comments) targets.set(child, POST);
        for (const pair of replies) {
            if (Array.isArray(pair) && id(pair[0]) && parents.has(pair[0])) targets.set(pair[1], pair[0]);
        }
        const added = [], seen = new Map();
        const existing = new Set(state.inbox.filter(row => samePost(row, post)).map(row => row.id));
        for (const [child, parent] of targets) {
            if (!id(child) || parents.has(child)) continue;
            const mine = parents.get(parent);
            // 글 기록이 먼저 만료되어도 내 댓글 기록에는 같은 답글을 확인한 사실이 남아야 한다.
            const owners = parent !== POST && parents.has(POST) ? [mine, parents.get(POST)] : [mine];
            let known = existing.has(child);
            for (const owner of owners) {
                if (!seen.has(owner.id)) seen.set(owner.id, new Set(owner.seen));
                const ids = seen.get(owner.id);
                if (+child <= +owner.floor || ids.has(child)) known = true;
                else ids.add(child);
            }
            if (known) continue;
            const preview = previews?.[child];
            const entry = { board: mine.board, kind: mine.kind, pid: mine.pid, id: child,
                title: mine.title, type: parent === POST ? "comment" : "reply",
                author: short(preview?.author, 40), text: short(preview?.text, 80), at: now, unread: true };
            added.push(entry);
        }
        for (const [parent, ids] of seen) {
            const mine = parents.get(parent), ordered = [...ids].sort((a, b) => +b - +a);
            mine.seen = ordered.slice(0, 100);
            if (ordered.length > 100) mine.floor = ordered[100];
        }
        added.sort((a, b) => +b.id - +a.id);
        state.inbox = added.concat(state.inbox).slice(0, 100);
        return added;
    }

    if (typeof module !== "undefined" && module.exports) module.exports = { load, track, receive, drop, cursor };
    const doc = root.document;
    if (!doc) return;
    const byId = name => doc.getElementById("reply-inbox" + name);
    const button = byId("-open"), dialog = byId("");
    if (!button || !dialog) return;
    const Notify = root.Notification, context = doc.querySelector("[data-board-context]"), watch = byId("-watch");
    const here = { board: context?.dataset.board, kind: context?.dataset.kind || "", pid: context?.dataset.pid, id: POST };
    const watching = Boolean(watch) && valid(here, true);
    const title = () => doc.querySelector(".article-head h1")?.textContent || "";
    let problem = "", notice = "", busy = false, stopped = false, timer, hiddenAt = Date.now();
    const desktop = state => Boolean(Notify) && state.desktop && Notify.permission === "granted";
    // 가려진 탭은 브라우저 알림을 켠 경우에만, 가려진 뒤 30분 동안만 확인한다.
    const background = state => desktop(state) && Date.now() - hiddenAt < HIDDEN_LIMIT;
    function read() {
        try { return load(root.localStorage.getItem(KEY), Date.now()); }
        catch (_) { problem = "브라우저 저장소를 사용할 수 없어 알림을 보관하지 못해요."; return load(null, Date.now()); }
    }
    function save(state) {
        try { root.localStorage.setItem(KEY, JSON.stringify(state)); }
        catch (_) { problem = "브라우저 저장소를 사용할 수 없어 알림을 보관하지 못해요."; }
    }
    function render() {
        const state = read(), unread = state.inbox.filter(row => row.unread).length;
        const badge = byId("-count");
        badge.textContent = String(unread);
        badge.hidden = !unread;
        button.setAttribute("aria-label", unread ? "댓글·답글 알림, 안 읽은 알림 " + unread + "개" : "댓글·답글 알림");
        if (watching) watch.textContent = state.mine.some(row => sameRow(row, here)) ? "댓글 알림 끄기" : "댓글 알림 받기";
        if (!dialog.open) return;
        byId("-status").textContent = problem || notice || (state.inbox.length ? "안 읽은 알림 " + unread + "개"
            : state.mine.length ? "아직 새 댓글이나 답글이 없어요."
            : "여기에서 글이나 댓글을 쓰거나, 글 화면에서 댓글 알림을 켜면 알림을 받을 수 있어요.");
        byId("-read").hidden = !unread;
        byId("-clear").hidden = unread === state.inbox.length;
        byId("-desktop").hidden = !Notify;
        if (Notify) {
            const denied = Notify.permission === "denied";
            byId("-desktop-toggle").textContent = desktop(state) ? "브라우저 알림 끄기" : "브라우저 알림 켜기";
            byId("-desktop-toggle").disabled = denied;
            byId("-desktop-hint").textContent = denied ? "브라우저 설정에서 이 사이트의 알림을 허용하면 켤 수 있어요."
                : "켜 두면 이 사이트를 연 탭이 가려져도 30분 동안 새 댓글과 답글을 브라우저 알림으로 알려요. 탭을 모두 닫으면 오지 않아요.";
        }
        const list = byId("-list");
        list.replaceChildren();
        for (const row of state.inbox) {
            const li = doc.createElement("li"), link = doc.createElement("a");
            const query = new URLSearchParams({ board: row.board, pid: row.pid });
            if (row.kind) query.set("kind", row.kind);
            link.href = "/read?" + query + "#comment-" + row.id;
            const name = row.title || row.board + " 글 " + row.pid;
            link.textContent = (row.unread ? "새 " : "") + (row.type === "comment" ? "댓글 · " : "답글 · ") + name;
            const preview = [short(row.author, 40), short(row.text, 80)].filter(Boolean).join(": ");
            if (preview) {
                const more = doc.createElement("span");
                more.className = "reply-inbox-preview";
                more.textContent = preview;
                link.append(more);
            }
            if (row.unread) link.setAttribute("data-unread", "");
            link.addEventListener("click", () => {
                const latest = read(), found = latest.inbox.find(entry => sameRow(entry, row));
                if (found) found.unread = false;
                save(latest);
                dialog.close();
                render();
            });
            li.append(link);
            if (state.mine.some(mine => samePost(mine, row))) {
                const off = doc.createElement("button");
                off.type = "button";
                off.className = "site-foot-link";
                off.textContent = "알림 끄기";
                off.setAttribute("aria-label", "이 글 알림 끄기: " + name);
                off.addEventListener("click", () => {
                    const latest = read();
                    drop(latest, row, true);
                    save(latest);
                    notice = "이 글 알림을 껐어요. 글 화면의 ‘댓글 알림 받기’로 다시 켤 수 있어요.";
                    render();
                    byId("-status").focus();
                });
                li.append(off);
            }
            list.append(li);
        }
    }

    function announce(added) {
        const first = added[0];
        try {
            const note = new Notify(added.length > 1 ? "새 댓글·답글 " + added.length + "개"
                : (first.type === "comment" ? "새 댓글 · " : "새 답글 · ") + (first.title || "숨터"),
                { body: [first.author, first.text].filter(Boolean).join(": "), tag: KEY, renotify: true });
            // 쓰던 글을 잃지 않도록 댓글로 바로 이동하지 않고 알림함을 연다.
            note.onclick = () => { root.focus(); note.close(); if (!dialog.open) dialog.showModal(); render(); };
        } catch (_) { /* 띄우지 못한 알림도 알림함에는 남아 있다. */ }
    }
    function schedule(delay) {
        clearTimeout(timer);
        const state = read();
        if (stopped || root.navigator.onLine === false || !state.mine.length || (doc.hidden && !background(state))) return;
        timer = setTimeout(check, doc.hidden ? Math.max(delay, HIDDEN_INTERVAL + Math.random() * 30000) : delay);
    }
    async function check() {
        if (busy || stopped || !root.navigator.onLine) return;
        const state = read(), now = Date.now(), gap = doc.hidden ? HIDDEN_INTERVAL : INTERVAL;
        if (!state.mine.length || (doc.hidden && !background(state))) return;
        if (state.checked + gap > now) return schedule(state.checked + gap - now);
        const post = state.mine.reduce((a, b) => a.checked <= b.checked ? a : b);
        state.checked = now;
        state.mine.filter(row => samePost(row, post)).forEach(row => { row.checked = now; });
        save(state);
        busy = true;
        const query = new URLSearchParams({ board: post.board, pid: post.pid, kind: post.kind });
        const after = cursor(state, post);
        if (/^[0-9]{1,15}$/.test(after)) query.set("after", after);
        const controller = new AbortController();
        const deadline = setTimeout(() => controller.abort(), 20000);
        try {
            const response = await root.fetch("/read/reply-activity?" + query, {
                headers: { Accept: "application/json" }, cache: "no-store", signal: controller.signal
            });
            if (response.status === 403) {
                stopped = true;
                problem = "새로고침하고 글쓰기 잠금 상태를 확인해 주세요.";
            } else if (response.status === 404) {
                const latest = read();
                latest.mine = latest.mine.filter(row => !samePost(row, post));
                save(latest);
            } else {
                if (!response.ok) throw Error();
                const data = await response.json();
                if (data.ok !== true || !Array.isArray(data.replies)) throw Error();
                const latest = read();
                const added = receive(latest, post, data.replies, Date.now(), data.comments, data.previews, data.complete !== false);
                save(latest);
                problem = "";
                if (added.length && doc.hidden && desktop(latest)) announce(added);
            }
        } catch (_) { problem = "지금은 알림을 확인할 수 없어요. 잠시 뒤 다시 확인해요."; }
        finally { clearTimeout(deadline); busy = false; render(); schedule(INTERVAL); }
    }

    function remember(ref, title) {
        const state = read();
        track(state, ref, title, Date.now());
        save(state);
        render();
        schedule(1500);
    }
    doc.addEventListener("mirror:comment-written", event => remember(event.detail, title()));
    doc.addEventListener("mirror:post-written", event => remember({ ...event.detail, id: POST }, event.detail.title));
    button.addEventListener("click", () => { notice = ""; dialog.showModal(); render(); schedule(0); });
    if (watching) {
        watch.hidden = false;
        watch.addEventListener("click", () => {
            const state = read();
            if (state.mine.some(row => sameRow(row, here))) drop(state, here, false);
            else track(state, here, title(), Date.now(), true);
            save(state);
            render();
            schedule(1500);
        });
    }
    byId("-clear").addEventListener("click", () => {
        const state = read();
        state.inbox = state.inbox.filter(row => row.unread);
        save(state);
        render();
    });
    byId("-desktop-toggle").addEventListener("click", async () => {
        let state = read();
        if (desktop(state)) state.desktop = false;
        else {
            // 예전 Safari는 Promise 없이 콜백으로만 답한다.
            const answer = await new Promise(resolve => { Notify.requestPermission(resolve)?.then(resolve); });
            state = read();
            state.desktop = answer === "granted";
            if (state.desktop) {
                try { new Notify("브라우저 알림을 켰어요", { body: "새 댓글과 답글이 오면 이렇게 알려요.", tag: KEY }); }
                catch (_) { state.desktop = false; notice = "이 브라우저는 열린 탭에서 보내는 알림을 지원하지 않아요."; }
            }
        }
        save(state);
        render();
        schedule(1500);
    });
    byId("-read").addEventListener("click", () => {
        const state = read();
        state.inbox.forEach(row => { row.unread = false; });
        save(state);
        render();
    });
    root.addEventListener("storage", event => { if (!event.key || event.key === KEY) { render(); schedule(1500); } });
    doc.addEventListener("visibilitychange", () => { hiddenAt = Date.now(); schedule(1500); });
    root.addEventListener("online", () => schedule(1500));
    root.addEventListener("pageshow", () => { render(); schedule(1500); });
    root.addEventListener("pagehide", () => clearTimeout(timer));
    render();
    schedule(1500);
})(typeof window !== "undefined" ? window : globalThis);
