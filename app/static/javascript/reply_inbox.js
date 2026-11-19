(function (root) {
    "use strict";

    const KEY = "mirror_reply_inbox_v1";
    const POST = "post";
    const DAY = 86400000, INTERVAL = 60000;
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
            checked: Math.min(now, Number(data?.checked) || 0) };
    }

    function track(state, ref, title, now) {
        if (!valid(ref, true) || state.mine.some(row => sameRow(row, ref))) return;
        state.inbox = state.inbox.filter(row => !sameRow(row, ref));
        state.mine.unshift({ board: ref.board, kind: ref.kind, pid: ref.pid, id: ref.id,
            title: title.slice(0, 100), at: now, checked: 0, floor: ref.id === POST ? "0" : ref.id, seen: [] });
        state.mine.length = Math.min(50, state.mine.length);
    }

    function receive(state, post, replies, now, comments = []) {
        const parents = new Map(state.mine.filter(row => samePost(row, post)).map(row => [row.id, row]));
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
            const entry = { board: mine.board, kind: mine.kind, pid: mine.pid, id: child,
                title: mine.title, type: parent === POST ? "comment" : "reply", at: now, unread: true };
            added.push(entry);
        }
        for (const [parent, ids] of seen) {
            const mine = parents.get(parent), ordered = [...ids].sort((a, b) => +b - +a);
            mine.seen = ordered.slice(0, 100);
            if (ordered.length > 100) mine.floor = ordered[100];
        }
        added.sort((a, b) => +b.id - +a.id);
        state.inbox = added.concat(state.inbox).slice(0, 100);
    }

    if (typeof module !== "undefined" && module.exports) module.exports = { load, track, receive };
    const doc = root.document;
    if (!doc) return;
    const byId = name => doc.getElementById("reply-inbox" + name);
    const button = byId("-open"), dialog = byId("");
    if (!button || !dialog) return;
    let problem = "", busy = false, stopped = false, timer;
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
        if (!dialog.open) return;
        byId("-status").textContent = problem || (state.inbox.length ? "안 읽은 알림 " + unread + "개"
            : state.mine.length ? "아직 새 댓글이나 답글이 없어요." : "여기에서 글이나 댓글을 쓰면 알림을 받을 수 있어요.");
        byId("-read").hidden = !unread;
        const list = byId("-list");
        list.replaceChildren();
        for (const row of state.inbox) {
            const li = doc.createElement("li"), link = doc.createElement("a");
            const query = new URLSearchParams({ board: row.board, pid: row.pid });
            if (row.kind) query.set("kind", row.kind);
            link.href = "/read?" + query + "#comment-" + row.id;
            link.textContent = (row.unread ? "새 " : "") + (row.type === "comment" ? "댓글 · " : "답글 · ") + (row.title || row.board + " 글 " + row.pid);
            if (row.unread) link.setAttribute("data-unread", "");
            link.addEventListener("click", () => {
                const latest = read(), found = latest.inbox.find(entry => sameRow(entry, row));
                if (found) found.unread = false;
                save(latest);
                dialog.close();
                render();
            });
            li.append(link);
            list.append(li);
        }
    }

    function schedule(delay) {
        clearTimeout(timer);
        if (!doc.hidden && !stopped && root.navigator.onLine !== false && read().mine.length)
            timer = setTimeout(check, delay);
    }
    async function check() {
        if (busy || stopped || doc.hidden || !root.navigator.onLine) return;
        const state = read(), now = Date.now();
        if (!state.mine.length) return;
        if (state.checked + INTERVAL > now) return schedule(state.checked + INTERVAL - now);
        const post = state.mine.reduce((a, b) => a.checked <= b.checked ? a : b);
        state.checked = now;
        state.mine.filter(row => samePost(row, post)).forEach(row => { row.checked = now; });
        save(state);
        busy = true;
        const query = new URLSearchParams({ board: post.board, pid: post.pid, kind: post.kind });
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
                receive(latest, post, data.replies, Date.now(), data.comments);
                save(latest);
                problem = "";
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
    doc.addEventListener("mirror:comment-written", event => remember(event.detail, doc.querySelector(".article-head h1")?.textContent || ""));
    doc.addEventListener("mirror:post-written", event => remember({ ...event.detail, id: POST }, event.detail.title));
    button.addEventListener("click", () => { dialog.showModal(); render(); schedule(0); });
    byId("-read").addEventListener("click", () => {
        const state = read();
        state.inbox.forEach(row => { row.unread = false; });
        save(state);
        render();
    });
    root.addEventListener("storage", event => { if (!event.key || event.key === KEY) { render(); schedule(1500); } });
    doc.addEventListener("visibilitychange", () => schedule(1500));
    root.addEventListener("online", () => schedule(1500));
    root.addEventListener("pageshow", () => { render(); schedule(1500); });
    root.addEventListener("pagehide", () => clearTimeout(timer));
    render();
    schedule(1500);
})(typeof window !== "undefined" ? window : globalThis);
