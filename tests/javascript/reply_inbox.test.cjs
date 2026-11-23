const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { load, track, receive, drop, cursor, pushRows, follow } = require("../../app/static/javascript/reply_inbox.js");

// 워커와 화면이 함께 쓰는 Cache Storage를 흉내 낸다.
function fakeCaches() {
    const stores = new Map();
    return {
        has: async name => stores.has(name),
        open: async name => {
            if (!stores.has(name)) stores.set(name, new Map());
            const store = stores.get(name);
            return { put: async (key, response) => { store.set(key, await response.text()); },
                match: async key => store.has(key) ? new Response(store.get(key)) : undefined,
                delete: async key => store.delete(key) };
        },
    };
}

test("a manually watched post starts from a baseline, keeps previews, and can be dropped", () => {
    const post = { board: "test", kind: "", pid: "70", id: "post" };
    const state = load(null, 10);
    track(state, { ...post, id: "20" }, "남의 글", 10);
    track(state, post, "남의 글", 11, true);
    assert.equal(cursor(state, post), "20");
    // 댓글을 다 받지 못한 응답은 기준선이 되지 않고, 기준선 확인에서도 내 댓글에 이미 달린 답글은 알린다.
    assert.deepEqual(receive(state, post, [], 12, ["5", "20"], {}, false), []);
    assert.deepEqual(receive(state, post, [["20", "22"]], 12, ["5", "20", "22", "25"]).map(row => [row.id, row.type]),
        [["22", "reply"]]);
    const added = receive(state, post, [["20", "22"], ["20", "31"]], 13, ["5", "20", "22", "25", "30", "31"],
        { 30: { author: "가".repeat(50), text: "나".repeat(79) + "😀다" }, 31: { author: 7 } });
    assert.deepEqual(added.map(row => [row.id, row.type, row.author.length, row.text]),
        [["31", "reply", 0, ""], ["30", "comment", 40, "나".repeat(79) + "😀"]]);
    assert.equal(cursor(state, post), "31");
    drop(state, post, false);
    assert.deepEqual([state.mine.map(row => row.id), state.inbox.length], [["20"], 3]);
    drop(state, post, true);
    assert.deepEqual([state.mine, state.inbox], [[], []]);
});

test("push sync rows carry each watch row's generation and highest known comment, and push state survives load", () => {
    const post = { board: "test", kind: "minor", pid: "900", id: "post" };
    const state = load(null, 10);
    track(state, post, "내 글", 10);
    track(state, { ...post, id: "10" }, "내 글", 11);
    track(state, { ...post, id: "30" }, "내 글", 12);
    track(state, { board: "other", kind: "", pid: "70", id: "post" }, "남의 글", 13, true);
    receive(state, post, [["10", "12"]], 14, ["9", "10", "12"]);
    assert.deepEqual(pushRows(state), [
        { board: "other", kind: "", pid: "70", id: "post", title: "남의 글", at: 13, base: true, known: "0" },
        { board: "test", kind: "minor", pid: "900", id: "30", title: "내 글", at: 12, base: false, known: "30" },
        { board: "test", kind: "minor", pid: "900", id: "10", title: "내 글", at: 11, base: false, known: "12" },
        { board: "test", kind: "minor", pid: "900", id: "post", title: "내 글", at: 10, base: false, known: "12" },
    ]);
    const push = { endpoint: "https://push.example/send/1", sig: "rows", synced: 15, tried: 16, rev: 17 };
    assert.deepEqual(load(JSON.stringify({ ...state, push }), 20).push, push);
    assert.deepEqual(load(JSON.stringify({ push: { endpoint: "http://push.example/1", synced: 99, rev: -3 } }), 20).push,
        { endpoint: "", sig: "", synced: 20, tried: 0, rev: 0 });
});

test("tracked titles keep an entire emoji at the limit", () => {
    const state = load(null, 1), expected = "가".repeat(99) + "😀";
    track(state, {board:"b", kind:"", pid:"1", id:"post"}, expected + "끝", 1);
    assert.equal(state.mine[0].title, expected);
});

function pushWorker(windows = [], caches = fakeCaches(), consumers = windows) {
    const listeners = {}, shown = [], opened = [], focused = [], posted = [], waits = [];
    const moved = [], deliver = new Map();
    for (const url of consumers) {
        const serviceWorker = {startMessages() {}, addEventListener(type, cb) { deliver.set(url, cb); }};
        // A Poker/locked page has no inbox controls. Fail if it starts a DC request or poll.
        const ctx = {URL, caches, navigator:{serviceWorker}, addEventListener() {},
            document:{hidden:true, addEventListener(){}, getElementById() { return null; }},
            location:{href:url, origin:new URL(url).origin, assign(to) { moved.push(to); }}};
        vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../../app/static/javascript/reply_inbox.js"), "utf8"), ctx);
        const dispatch = deliver.get(url);
        deliver.set(url, event => { ctx.document.hidden = !focused.includes(url); dispatch(event); });
    }
    const self = {
        location: { origin: "https://mir.example" },
        addEventListener: (type, listener) => { listeners[type] = listener; },
        registration: { showNotification: async (title, options) => { shown.push([title, options.body, options.tag, options.data.url]); } },
        clients: { matchAll: async () => windows.map(url => ({ url, focus: async () => { focused.push(url); },
            postMessage: (message, ports = []) => {
                if (!ports.length) posted.push([url, message]);
                deliver.get(url)?.({data:message, ports});
            } })),
            openWindow: async url => { opened.push(url); } },
        caches,
    };
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../../app/static/javascript/push_sw.js"), "utf8"),
        { self, URL, Response, Date, JSON, MessageChannel, setTimeout, clearTimeout });
    const fire = (type, event) => { listeners[type]({ ...event, waitUntil: promise => waits.push(promise) }); return Promise.all(waits); };
    return { shown, opened, focused, posted, moved,
        push: data => fire("push", { data: data === null ? null : { json: () => JSON.parse(data) } }),
        click: url => fire("notificationclick", { notification: { data: { url }, close() {} } }) };
}

const settle = () => new Promise(resolve => setImmediate(resolve));
function inboxPage({on = true, synced = false} = {}) {
    const endpoint = "https://push.example/1", now = Date.now(), state = load(null, now);
    track(state, {board:"b", kind:"", pid:"1", id:"post"}, "post", now);
    state.push = {...state.push, endpoint:on ? endpoint : "", sig:synced ? `b  1 post ${now}` : "old", synced:now};
    let raw = JSON.stringify(state);
    const nodes = new Map(), requests = [], timers = new Map();
    function node(id) {
        if (!nodes.has(id)) nodes.set(id, {dataset:{worker:"/static/javascript/push_sw.js"}, setAttribute(){},
            addEventListener(type, cb) { this[type] = cb; }});
        return nodes.get(id);
    }
    const document = {getElementById:node, querySelector:() => null, addEventListener(){}};
    const sub = {endpoint, toJSON:() => ({keys:{}}), unsubscribe:async () => true};
    const registration = {active:{}, pushManager:{getSubscription:async () => on ? sub : null, subscribe:async () => sub}};
    const serviceWorker = {register:async () => registration, getRegistration:async () => registration};
    const ctx = {document, URL, URLSearchParams, AbortController, PushManager:{}, isSecureContext:true,
        Notification:{requestPermission:async () => "granted"}, atob, navigator:{serviceWorker, onLine:true},
        localStorage:{getItem:() => raw, setItem:(_, value) => {raw = value;}},
        location:new URL("https://mir.example/"), addEventListener(){},
        setTimeout(fn, ms) { const id = Symbol(); timers.set(id,{fn,ms}); return id; }, clearTimeout:id => timers.delete(id),
        fetch(url, options) {
            if (url === "/push/key") return Promise.resolve({status:200, json:async () => ({ok:true,key:"A".repeat(87)})});
            return new Promise(resolve => requests.push({url,body:options.body && JSON.parse(options.body),
                reply:(status, payload) => resolve({status,ok:status === 200,json:async () => payload})}));
        }};
    vm.runInNewContext(fs.readFileSync(path.join(__dirname, "../../app/static/javascript/reply_inbox.js"), "utf8"), ctx);
    return { requests, node, read:() => JSON.parse(raw), write:state => {raw = JSON.stringify(state);},
        check() { [...timers.values()].find(timer => timer.ms === 1500).fn(); } };
}

test("enable and background sync acknowledge only a current non-stale revision", async () => {
    for (const on of [true, false]) {
        for (const race of ["stale", "new revision", "different endpoint", "accepted"]) {
            const h = inboxPage({on}); await settle();
            if (!on) { h.node("reply-inbox-push-toggle").click(); await settle(); }
            const request = h.requests[0];
            assert.equal(request.url, "/push/sync");
            const state = h.read();
            if (race === "new revision") state.push.rev = request.body.rev + 1;
            if (race === "different endpoint") state.push.endpoint = "https://push.example/other";
            h.write(state);
            request.reply(200, {ok:true, stale:race === "stale"}); await settle();
            if (race === "accepted") {
                assert.equal(h.read().push.endpoint, "https://push.example/1");
                assert.notEqual(h.read().push.sig, "old");
            } else assert.deepEqual(h.read().push, state.push, race);
        }
    }
});

test("dropping the last watch on a 404 sends the empty watch list", async () => {
    const h = inboxPage({synced:true}); await settle();
    h.check(); await settle();
    assert.match(h.requests[0].url, /^\/read\/reply-activity\?/);
    h.requests[0].reply(404, {}); await settle();
    assert.equal(h.requests[1].url, "/push/sync");
    assert.deepEqual(h.requests[1].body.rows, []);
});

test("the push worker shows contract payloads, falls back on broken ones, and opens only home or read pages", async () => {
    const sw = pushWorker(["https://mir.example/read?board=test&pid=1#comment-5"]);
    await sw.push(JSON.stringify({ title: "새 댓글 · 제목", body: "작성자: 내용", url: "/read?board=test&pid=1#comment-5", tag: "mirror:test:1" }));
    await sw.push("{broken");
    await sw.push(null);
    await sw.push(JSON.stringify({ title: "새 답글 · 제목", body: "a: b", url: "https://evil.example/read?x", tag: "mirror:test:2" }));
    assert.deepEqual(sw.shown, [
        ["새 댓글 · 제목", "작성자: 내용", "mirror:test:1", "/read?board=test&pid=1#comment-5"],
        ["숨터", "새 댓글이나 답글이 있어요.", "mirror:fallback", "/"],
        ["숨터", "새 댓글이나 답글이 있어요.", "mirror:fallback", "/"],
        ["새 답글 · 제목", "a: b", "mirror:test:2", "/"],
    ]);
    await sw.click("/read?board=test&pid=1#comment-5");
    assert.deepEqual([sw.focused, sw.opened], [["https://mir.example/read?board=test&pid=1#comment-5"], []]);
    const closed = pushWorker();
    for (const url of ["/read?board=test&pid=2", "/", "//evil.example/read?x", "/write/lock", undefined])
        await closed.click(url);
    assert.deepEqual(closed.opened, ["https://mir.example/read?board=test&pid=2", "https://mir.example/", "https://mir.example/",
        "https://mir.example/", "https://mir.example/"]);
});

test("a notification tapped while the app shows another page moves exactly one open page to the comment", async () => {
    const caches = fakeCaches(), page = "https://mir.example/board?id=test";
    const sw = pushWorker([page, "https://mir.example/"], caches);
    await sw.click("/read?board=test&pid=1#comment-5");
    // iOS 홈 화면 앱은 openWindow로 주소를 바꾸지 않으므로 열린 화면이 직접 이동해야 한다.
    assert.deepEqual([sw.opened, sw.focused, sw.posted], [[], [page], [[page, "mirror-notification-open"]]]);
    await settle();
    assert.deepEqual(sw.moved, ["https://mir.example/read?board=test&pid=1#comment-5"]);
});

test("same-post handoff reloads at the notification anchor", async () => {
    const caches = fakeCaches(), poker = "https://mir.example/poker/free/2";
    const post = "https://mir.example/read?board=test&pid=1";
    for (const hash of ["#comment-2", "#comment-5"]) {
        const sw = pushWorker([poker, post + hash], caches, []);
        await sw.click("/read?board=test&pid=1#comment-5");
        assert.deepEqual(sw.focused, [post + hash]);
        const location = { href: post + hash, origin: "https://mir.example",
            reload() { assert.equal(this.href, post + "#comment-5"); this.reloaded = true; } };
        assert.equal(await follow({ caches, location, history: { replaceState(_, __, url) { location.href = url; } } }), true);
        assert.equal(location.reloaded, true);
    }
});

test("an installed app displaying only Poker consumes the click and reaches the comment", async () => {
    const poker = "https://mir.example/poker/free/2", target = "/read?board=test&pid=1#comment-5";
    const sw = pushWorker([poker]);
    await sw.click(target); await settle();
    assert.deepEqual(sw.opened, []);
    assert.deepEqual(sw.moved, ["https://mir.example" + target]);
});

test("a locked account JSON document and a non-listening read document do not take the click", async () => {
    const ready = "https://mir.example/poker/free";
    const sw = pushWorker(["https://mir.example/write/account", "https://mir.example/read?bad", ready], fakeCaches(), [ready]);
    await sw.click("/read?board=b&pid=7"); await settle();
    assert.deepEqual(sw.focused, [ready]);
    assert.deepEqual(sw.moved, ["https://mir.example/read?board=b&pid=7"]);
});

test("a same-post notification keeps a reply target and unsent content without reloading", async () => {
    const href = "https://mir.example/read?board=b&pid=1", target = href + "#comment-5";
    const caches = fakeCaches(), sw = pushWorker([href], caches, []);
    await sw.click("/read?board=b&pid=1#comment-5");
    const location = {href, origin:"https://mir.example", reload:assert.fail};
    let scrolled = false;
    const input = {value:"unfinished"};
    const document = {getElementById: id => id === "comment-title" ? {scrollIntoView(){ scrolled = true; }} : null};
    const windows = Array.from({length:2}, () => ({caches, location, document, MirrorCommentForm:{hasDraft:() => !!input.value},
        history:{replaceState(_, __, url) {location.href = url;}}}));
    assert.deepEqual((await Promise.all(windows.map(follow))).sort(), [false,true]);
    assert.equal(location.href, target);
    assert.equal(scrolled, true);
    assert.equal(input.value, "unfinished");
});

test("a new post notifies for others' comments, preferring a single reply notification on overlap", () => {
    const state = load(null, 10), otherBrowser = load(null, 10);
    const post = {board: "test", kind: "", pid: "900", id: "post"};
    track(state, post, "내 글", 10);
    track(state, {...post, id: "10"}, "내 글", 11);
    const ids = ["9", "10", "12", "13"], edges = [["10", "12"]];
    receive(otherBrowser, post, edges, 12, ids);
    receive(state, {...post, pid: "901"}, edges, 12, ids);
    assert.deepEqual([state.inbox, otherBrowser.inbox], [[], []]);
    receive(state, post, edges, 13, ids);
    assert.deepEqual(state.inbox.map(row => [row.id, row.type]), [["13","comment"], ["12","reply"], ["9","comment"]]);
    state.inbox[0].unread = false;
    const next = load(JSON.stringify(state), 14);
    receive(next, post, [...edges, ["10","13"]], 14, ids);
    assert.deepEqual(next.inbox.map(row => [row.id,row.unread]), [["13",false], ["12",true], ["9",true]]);
});

test("only replies to this browser's successful comments in the matching post are notified", () => {
    const a = load(null, 10), b = load(null, 10);
    const post = { board: "test", kind: "minor", pid: "12", id: "100" };
    track(a, post, "제목", 10);
    track(a, { ...post, id: "105" }, "제목", 10);
    const edges = [["100", "105"], ["100", "120"], ["100", "120"], ["103", "115"]];
    receive(b, post, edges, 20);
    receive(a, { ...post, pid: "13" }, edges, 20);
    assert.deepEqual([a.inbox,b.inbox], [[],[]]);
    receive(a, post, edges, 20);
    assert.deepEqual(a.inbox.map(row => [row.id,row.unread]), [["120",true]]);
    a.inbox[0].unread = false;
    const next = load(JSON.stringify(a), 30);
    receive(next, post, [...edges, ["100","111"]], 30);
    assert.deepEqual(next.inbox.map(row => [row.id,row.unread]), [["111",true],["120",false]]);
    assert.deepEqual(load(JSON.stringify(next), 31 * 86400000).mine, []);
});

test("read replies stay read after the older authored-post watch expires", () => {
    const day = 86400000, post = { board: "test", kind: "", pid: "90", id: "post" };
    let state = load(null, day);
    track(state, post, "내 글", day);
    track(state, { ...post, id: "30" }, "내 글", 2 * day);
    receive(state, post, [["30", "40"]], 3 * day, ["30", "40"]);
    state.inbox[0].unread = false;
    state = load(JSON.stringify(state), 31 * day + 1);
    receive(state, post, [["30", "40"], ["30", "50"]], 31 * day + 1, ["30", "40", "50"]);
    assert.deepEqual(state.inbox.map(row => [row.id, row.unread]), [["50", true], ["40", false]]);
});
