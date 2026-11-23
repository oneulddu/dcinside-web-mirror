(function (root) {
    "use strict";

    const KEY = "mirror_reply_inbox_v1";
    const POST = "post";
    const DAY = 86400000, INTERVAL = 60000, SYNC_GAP = 10000, LOCK_WAIT = 3000;
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
        // push: 이 브라우저의 푸시 구독 주소, 마지막으로 서버에 보낸 감시 목록과 시각, 보낼 때마다 커지는 rev.
        const push = data?.push;
        return { mine: rows("mine").slice(0, 50), inbox: rows("inbox").slice(0, 100),
            checked: Math.min(now, Number(data?.checked) || 0),
            push: { endpoint: typeof push?.endpoint === "string" && push.endpoint.startsWith("https://") ? push.endpoint : "",
                sig: typeof push?.sig === "string" ? push.sig : "", synced: Math.min(now, Number(push?.synced) || 0),
                tried: Math.min(now, Number(push?.tried) || 0), rev: Number.isSafeInteger(push?.rev) && push.rev > 0 ? push.rev : 0 } };
    }

    // 감시 행이 이미 확인한 가장 큰 댓글 번호.
    const known = row => Math.max(+row.floor, ...row.seen.map(Number));

    // 서버 푸시가 쓰는 감시 목록. known은 서버가 그 행을 처음 볼 때만 시작점으로 쓴다.
    function pushRows(state) {
        return state.mine.map(row => ({ board: row.board, kind: row.kind, pid: row.pid, id: row.id,
            title: short(row.title, 100), at: row.at, base: row.base === true, known: String(known(row)) }));
    }

    // base: 직접 알림을 켠 글. 첫 확인까지 달린 댓글은 기준선으로만 삼고 알리지 않는다.
    function track(state, ref, title, now, base = false) {
        if (!valid(ref, true) || state.mine.some(row => sameRow(row, ref))) return;
        state.inbox = state.inbox.filter(row => !sameRow(row, ref));
        state.mine.unshift({ board: ref.board, kind: ref.kind, pid: ref.pid, id: ref.id,
            title: short(title, 100), at: now, checked: 0, floor: ref.id === POST ? "0" : ref.id, seen: [],
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
        return rows.length ? String(Math.min(...rows.map(known))) : "";
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

    // 열린 화면에서 누른 알림의 주소를 push_sw.js가 남겨 두면 가져가 이동한다. 탭이 여럿이어도 지우기에 성공한 한 곳만 간다.
    const HANDOFF = "mirror-notification-open", HANDOFF_KEY = "/__mirror-notification-open";
    async function follow(win) {
        try {
            if (!win.caches || !await win.caches.has(HANDOFF)) return false;
            const cache = await win.caches.open(HANDOFF), answer = await cache.match(HANDOFF_KEY);
            if (!answer || !await cache.delete(HANDOFF_KEY)) return false;
            const { url, at } = await answer.json(), target = new URL(url, win.location.href);
            if (target.origin !== win.location.origin || !(target.pathname === "/" || target.pathname === "/read")
                || !(Math.abs(Date.now() - at) < 60000)) return false;
            const current = new URL(win.location.href);
            if (current.pathname === target.pathname && current.search === target.search) {
                win.history.replaceState(win.history.state, "", target.href);
                if (win.MirrorCommentForm?.hasDraft?.()) {
                    const anchor = win.document.getElementById(target.hash.slice(1)) || win.document.getElementById("comment-title");
                    anchor?.scrollIntoView();
                } else win.location.reload();
            } else win.location.assign(target.href);
            return true;
        } catch (_) {
            return false;
        }
    }

    if (typeof module !== "undefined" && module.exports) module.exports = { load, track, receive, drop, cursor, pushRows, follow };
    const doc = root.document;
    if (!doc) return;
    const nav = root.navigator;
    if (nav.serviceWorker && root.caches) {
        const followNow = () => { if (!doc.hidden) follow(root); };
        nav.serviceWorker.addEventListener("message", event => {
            if (event.data === HANDOFF + ":ready?") event.ports[0]?.postMessage(HANDOFF);
            else if (event.data === HANDOFF) followNow();
        });
        nav.serviceWorker.startMessages();
        doc.addEventListener("visibilitychange", followNow);
        root.addEventListener("focus", followNow);
        root.addEventListener("pageshow", followNow);
        followNow();
    }
    const byId = name => doc.getElementById("reply-inbox" + name);
    const button = byId("-open"), dialog = byId("");
    if (!button || !dialog) return;
    const context = doc.querySelector("[data-board-context]"), watch = byId("-watch");
    const here = { board: context?.dataset.board, kind: context?.dataset.kind || "", pid: context?.dataset.pid, id: POST };
    const watching = Boolean(watch) && valid(here, true);
    const title = () => doc.querySelector(".article-head h1")?.textContent || "";
    let problem = "", notice = "", busy = false, stopped = false, timer;
    // 푸시는 서버가 설정되어 있고(#reply-inbox-push), 브라우저가 서비스 워커 푸시를 지원할 때만 쓴다.
    const pushBox = byId("-push");
    const pushable = Boolean(pushBox) && root.isSecureContext === true && "serviceWorker" in nav
        && "PushManager" in root && "Notification" in root;
    // 워커는 /static/javascript/ 범위에 있어 이 화면을 제어하지 않는다. 등록은 그 범위로 찾는다.
    const worker = pushBox ? new URL(pushBox.dataset.worker, root.location.href).href : "";
    const scope = worker && new URL(".", worker).href;
    const PUSH = {
        idle: "켜 두면 사이트를 모두 닫아도 새 댓글과 답글을 이 기기의 알림으로 받아요.",
        on: "푸시 알림이 켜져 있어요. 사이트를 모두 닫아도 새 댓글과 답글을 이 기기의 알림으로 받아요.",
        enabled: "푸시 알림을 켰어요. 사이트를 모두 닫아도 새 댓글과 답글을 이 기기의 알림으로 받아요.",
        welcomed: "푸시 알림을 켰어요. 확인 알림을 보냈으니 이 기기에 알림이 왔는지 봐 주세요.",
        quiet: "푸시 알림을 켰지만 확인 알림을 보내지 못했어요. 새 댓글 알림이 오지 않으면 껐다가 다시 켜 주세요.",
        denied: "알림이 차단되어 있어요. 브라우저나 기기 설정에서 이 사이트의 알림을 허용한 뒤 다시 눌러 주세요.",
        dismissed: "알림을 허용해야 켤 수 있어요. 다시 누르고 ‘허용’을 골라 주세요.",
        unsupported: "이 브라우저에서는 푸시 알림을 쓸 수 없어요. iPhone에서는 Safari 공유 메뉴의 ‘홈 화면에 추가’로 이 사이트를 추가하고 홈 화면에서 열어야 켤 수 있어요. 홈 화면 앱은 글쓰기 잠금 해제와 알림 기록을 Safari와 따로 가져요.",
        retry: "방금 다른 켜기 요청이 있어 받지 않았어요. 5초쯤 뒤에 다시 눌러 주세요.",
        gone: "푸시 서비스가 이 기기의 구독을 받지 않아 켜지 못했어요. 다시 눌러 새로 켜 주세요.",
        locked: "글쓰기 잠금 상태라 켜지 못했어요. 새로고침하고 잠금 상태를 확인해 주세요.",
        network: "연결이 끊겨 켜지 못했어요. 인터넷 연결을 확인하고 다시 눌러 주세요.",
        failed: "지금은 푸시 알림을 켤 수 없어요. 잠시 뒤에 다시 눌러 주세요.",
        off: "푸시 알림을 껐어요.",
        offPartial: "푸시 알림을 껐지만 이 브라우저의 구독을 지우지 못해 알림이 더 올 수 있어요. 다시 켰다가 꺼 주세요.",
        lost: "이 브라우저의 푸시 구독이 사라져 푸시 알림이 꺼졌어요. 다시 켜 주세요.",
    };
    let pushNote = "", pushBusy = "", syncing = false, failed = null, syncTimer, locking = false;
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
        if (pushBox) {
            const on = Boolean(state.push.endpoint), toggle = byId("-push-toggle");
            toggle.hidden = !pushable;
            toggle.disabled = Boolean(pushBusy);
            toggle.textContent = pushBusy === "on" ? "푸시 알림 켜는 중…" : pushBusy === "off" ? "푸시 알림 끄는 중…"
                : on ? "푸시 알림 끄기" : "푸시 알림 켜기";
            byId("-push-hint").textContent = !pushable ? PUSH.unsupported : pushNote
                || (on ? PUSH.on : root.Notification.permission === "denied" ? PUSH.denied : PUSH.idle);
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
                    resync();
                });
                li.append(off);
            }
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
        // 만료된 행이나 24시간 지난 푸시 목록은 보이는 탭의 확인 때 다시 보낸다.
        resync();
        const state = read(), now = Date.now();
        if (!state.mine.length) return;
        if (state.checked + INTERVAL > now) return schedule(state.checked + INTERVAL - now);
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
                resync();
            } else {
                if (!response.ok) throw Error();
                const data = await response.json();
                if (data.ok !== true || !Array.isArray(data.replies)) throw Error();
                const latest = read();
                receive(latest, post, data.replies, Date.now(), data.comments, data.previews, data.complete !== false);
                save(latest);
                problem = "";
            }
        } catch (_) { problem = "지금은 알림을 확인할 수 없어요. 잠시 뒤 다시 확인해요."; }
        finally { clearTimeout(deadline); busy = false; render(); schedule(INTERVAL); }
    }

    async function send(path, body) {
        const controller = new AbortController(), deadline = setTimeout(() => controller.abort(), 20000);
        try {
            const response = await root.fetch(path, { method: body ? "POST" : "GET", cache: "no-store", credentials: "same-origin",
                headers: body ? { Accept: "application/json", "Content-Type": "application/json" } : { Accept: "application/json" },
                // 잠그면서 화면을 떠나도 구독 해제 요청이 끝까지 전달되게 한다.
                body: body && JSON.stringify(body), keepalive: Boolean(body), signal: controller.signal });
            return { status: response.status, data: await response.json().catch(() => null) };
        } catch (_) { return { status: 0, data: null }; }
        finally { clearTimeout(deadline); }
    }
    // 계약의 오류 응답을 안내 문구로 바꾼다. status 0은 통신 실패다.
    const failure = ({ status }) => ({ 0: "network", 403: "locked", 410: "gone", 429: "retry" })[status] || "failed";
    // 서버는 같은 (board, kind, pid, id, at) 행의 진행 상태를 이어 쓰므로 이 값이 바뀔 때만 다시 보낸다.
    const signature = rows => rows.map(row => [row.board, row.kind, row.pid, row.id, row.at].join(" ")).join("\n");
    const key = text => Uint8Array.from(root.atob(text.replace(/-/g, "+").replace(/_/g, "/") + "="), c => c.charCodeAt(0));

    // 구독은 활성 워커가 있어야 만들 수 있다. 화면이 워커 범위 밖이라 serviceWorker.ready는 끝나지 않는다.
    function activated(registration) {
        return new Promise((resolve, reject) => {
            if (registration.active) return resolve(registration);
            const next = registration.installing || registration.waiting;
            next.addEventListener("statechange", () => {
                if (registration.active) resolve(registration);
                else if (next.state === "redundant") reject(Error("push worker failed to install"));
            });
        });
    }
    async function subscription() {
        const registration = await nav.serviceWorker.getRegistration(scope);
        return registration ? registration.pushManager.getSubscription() : null;
    }
    function forget() {
        const state = read();
        state.push = { ...state.push, endpoint: "", sig: "", synced: 0, rev: state.push.rev + 1 };
        save(state);
    }
    async function sync(sub, stored) {
        const state = read(), rows = pushRows(state), keys = sub.toJSON().keys;
        if (state.push.endpoint !== stored) return { current: false };
        const rev = Math.max(Date.now(), state.push.rev + 1);
        state.push = { ...state.push, rev, tried: Date.now() };
        save(state);
        const result = await send("/push/sync", { subscription: { endpoint: sub.endpoint,
            keys: { p256dh: keys.p256dh, auth: keys.auth } }, rev, rows });
        const latest = read(), sig = signature(rows);
        const current = latest.push.endpoint === stored && latest.push.rev === rev;
        const ok = current && result.status === 200 && result.data?.ok === true && !result.data.stale;
        if (ok) {
            latest.push = { ...latest.push, endpoint: sub.endpoint, sig, synced: Date.now() };
            save(latest);
        }
        return { ...result, current, ok, sig };
    }

    async function enable(asked) {
        const permission = await asked;
        if (permission !== "granted") return permission === "denied" ? "denied" : "dismissed";
        const registration = await activated(await nav.serviceWorker.register(worker));
        const answer = await send("/push/key");
        if (answer.status !== 200 || answer.data?.ok !== true || !/^[A-Za-z0-9_-]{87}$/.test(answer.data.key))
            return failure(answer);
        // 서버 키가 바뀌었으면 남은 구독으로는 새로 구독할 수 없어 먼저 지운다.
        await (await registration.pushManager.getSubscription())?.unsubscribe();
        const sub = await registration.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key(answer.data.key) });
        const result = await sync(sub, "");
        if (!result.current || result.data?.stale) return "failed";
        if (!result.ok) {
            // 서버가 받지 않은 구독을 남기지 않는다. 지우지 못해도 다음에 켤 때 위에서 다시 지운다.
            await sub.unsubscribe().catch(() => {});
            return failure(result);
        }
        return !result.data.created ? "enabled" : result.data.welcomed ? "welcomed" : "quiet";
    }
    // 화면에서는 먼저 꺼진 것으로 기록하고 서버에 바로 알린다. 브라우저 구독 조회나 삭제가
    // 실패하거나 늦어도(잠금은 3초만 기다린다) 서버는 이미 보내지 않게 된다.
    async function disable() {
        const stored = read().push.endpoint;
        forget();
        const told = stored ? send("/push/unsubscribe", { endpoint: stored }) : null;
        const sub = await subscription();
        if (sub && sub.endpoint !== stored) await send("/push/unsubscribe", { endpoint: sub.endpoint });
        await told;
        if (sub) await sub.unsubscribe();
    }
    async function run(kind, task) {
        pushBusy = kind;
        pushNote = "";
        render();
        let note;
        try { note = await task; }
        catch (error) { note = kind === "off" ? "offPartial" : error?.name === "NotAllowedError" ? "denied" : "failed"; }
        pushBusy = "";
        pushNote = PUSH[note];
        render();
        resync();
    }

    // 푸시가 켜져 있을 때 감시 목록이 바뀌었거나 24시간이 지났으면 서버에 다시 보낸다(10초에 한 번까지).
    // renewed: 브라우저가 구독을 새로 바꿔 저장한 주소와 다를 때의 새 구독.
    // 실패한 목록은 다음 계기(목록 변화, 새 화면)에 다시 보내고 그사이에는 반복하지 않는다.
    async function resync(renewed) {
        clearTimeout(syncTimer);
        const state = read(), now = Date.now(), stored = state.push.endpoint;
        if (!pushable || pushBusy || syncing || !stored) return;
        const sig = signature(pushRows(state));
        if (!renewed && ((sig === state.push.sig && now - state.push.synced < DAY) || sig === failed)) return;
        if (state.push.tried + SYNC_GAP > now) {
            syncTimer = setTimeout(() => resync(renewed), state.push.tried + SYNC_GAP - now);
            return;
        }
        syncing = true;
        try {
            const sub = renewed || await subscription();
            if (!sub) {
                forget();
                pushNote = PUSH.lost;
                return;
            }
            const result = await sync(sub, stored).catch(error => { failed = sig; throw error; });
            if (!result.current) return;
            failed = result.ok ? null : result.sig;
            if (result.status === 410) {
                forget();
                pushNote = PUSH.lost;
                await sub.unsubscribe();
            }
        } finally {
            syncing = false;
            render();
        }
        resync();
    }
    // 새 화면마다 브라우저의 지금 구독이 저장한 것과 같은지 본다. 등록은 워커 주소가 바뀌었을 때 갱신한다.
    async function verify() {
        if (!pushable) return;
        if (!read().push.endpoint) {
            // 켜는 도중 화면을 떠나면 기록 없이 구독만 남는다. 꺼진 것으로 보이는 동안 알림이 오지 않게 지운다.
            const orphan = await subscription();
            if (orphan && !pushBusy && !read().push.endpoint) {
                send("/push/unsubscribe", { endpoint: orphan.endpoint });
                await orphan.unsubscribe();
            }
            return;
        }
        const registration = await nav.serviceWorker.register(worker);
        const sub = await registration.pushManager.getSubscription();
        if (!sub) {
            forget();
            pushNote = PUSH.lost;
            render();
        } else resync(sub.endpoint === read().push.endpoint ? undefined : sub);
    }

    function remember(ref, title) {
        const state = read();
        track(state, ref, title, Date.now());
        save(state);
        render();
        schedule(1500);
        resync();
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
            resync();
        });
    }
    byId("-clear").addEventListener("click", () => {
        const state = read();
        state.inbox = state.inbox.filter(row => row.unread);
        save(state);
        render();
    });
    if (pushable) {
        byId("-push-toggle").addEventListener("click", () => {
            if (pushBusy) return;
            if (read().push.endpoint) return run("off", disable().then(() => "off"));
            // iOS는 await 뒤에는 사용자 동작으로 보지 않아 권한 창을 띄우지 않으므로 가장 먼저 묻는다.
            run("on", enable(root.Notification.requestPermission()));
        });
        // 잠근 브라우저에 알림이 계속 오지 않게 먼저 끈다. 끄지 못해도 3초 안에 그대로 잠근다.
        const lock = doc.getElementById("write-lock-form");
        lock?.addEventListener("submit", event => {
            event.preventDefault();
            if (locking) return;
            locking = true;
            const submit = () => lock.submit();
            Promise.race([disable(), new Promise(resolve => setTimeout(resolve, LOCK_WAIT))]).then(submit, submit);
        });
    }
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
    root.addEventListener("pagehide", () => { clearTimeout(timer); clearTimeout(syncTimer); });
    render();
    schedule(1500);
    verify().catch(() => { /* 구독 확인에 실패해도 알림함은 그대로 쓴다. */ });
})(typeof window !== "undefined" ? window : globalThis);
