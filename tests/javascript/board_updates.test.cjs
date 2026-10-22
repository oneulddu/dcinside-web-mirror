const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

const source = fs.readFileSync(path.resolve(__dirname, "../../app/static/javascript/board_updates.js"), "utf8");
const context = {};
vm.runInNewContext(source, context);
const { computeNew } = context.MirrorBoardUpdates;

const item = (id, extra) => Object.assign({ id: String(id), title: "t" + id, author: "a", author_code: null }, extra || {});

test("기준보다 새 글만 세고 응답이 기준까지 닿으면 정확한 수를 준다", () => {
    const result = computeNew(100, [100, 99], [item(103), item(102), item(101), item(100), item(99)]);
    assert.equal(result.count, 3);
    assert.equal(result.more, false);
});

test("응답이 모두 기준보다 새로우면 '이상'으로 표시한다", () => {
    const result = computeNew(100, [100], [item(130), item(129), item(128)]);
    assert.equal(result.count, 3);
    assert.equal(result.more, true);
});

test("차단된 새 글과 이미 화면에 있는 글, 중복 글은 세지 않는다", () => {
    const blocked = it => it.author_code === "1.2";
    const result = computeNew(100, [100, 102], [item(103, { author_code: "1.2" }), item(102), item(101), item(101), item(100)], blocked);
    assert.equal(result.count, 1);
    assert.equal(result.newer, 2);
});

test("새 글이 없거나 번호가 이상한 항목은 무시한다", () => {
    assert.equal(computeNew(100, [100], [item(100), item(90)]).count, 0);
    assert.equal(computeNew(100, [100], [{ id: "abc" }, { id: "" }, null]).count, 0);
});

const { checkDelay, titleWithCount } = context.MirrorBoardUpdates;

test("보이는 탭은 60~70초, 가려진 탭은 180~210초마다 확인한다", () => {
    assert.equal(checkDelay({ hidden: false, now: 0, hiddenSince: 0, failures: 0, random: 0 }), 60000);
    assert.equal(checkDelay({ hidden: false, now: 0, hiddenSince: 0, failures: 0, random: 0.99 }), 69900);
    assert.equal(checkDelay({ hidden: true, now: 1000, hiddenSince: 0, failures: 0, random: 0 }), 180000);
    assert.equal(checkDelay({ hidden: true, now: 1000, hiddenSince: 0, failures: 0, random: 0.99 }), 209700);
});

test("가려진 지 30분이 지나면 확인을 멈춘다", () => {
    assert.equal(checkDelay({ hidden: true, now: 30 * 60000, hiddenSince: 0, failures: 0, random: 0 }), -1);
    assert.ok(checkDelay({ hidden: true, now: 30 * 60000 - 1, hiddenSince: 0, failures: 0, random: 0 }) > 0);
});

test("실패하면 늘어난 간격을 쓰되 가려진 탭은 180초보다 짧아지지 않는다", () => {
    assert.equal(checkDelay({ hidden: false, now: 0, hiddenSince: 0, failures: 1 }), 120000);
    assert.equal(checkDelay({ hidden: false, now: 0, hiddenSince: 0, failures: 9 }), 300000);
    assert.equal(checkDelay({ hidden: true, now: 0, hiddenSince: 0, failures: 1 }), 180000);
});

test("탭 제목에 새 글 수를 한 번만 붙이고 0개면 뗀다", () => {
    assert.equal(titleWithCount("야구 갤러리", { count: 3, more: false }), "(3) 야구 갤러리");
    assert.equal(titleWithCount("(3) 야구 갤러리", { count: 5, more: true }), "(5+) 야구 갤러리");
    assert.equal(titleWithCount("(5+) 야구 갤러리", { count: 0 }), "야구 갤러리");
    assert.equal(titleWithCount("(주) 갤러리", { count: 0 }), "(주) 갤러리");
});

function browserHarness() {
    const requests = [], timers = new Map(), events = {};
    let sequence = 0, reloads = 0, bar;
    class Element {
        constructor() { this.attrs = {}; this.children = []; this.events = {}; this.isConnected = true; }
        getAttribute(name) { return this.attrs[name] || null; }
        setAttribute(name, value) { this.attrs[name] = value; }
        appendChild(child) { this.children.push(child); }
        addEventListener(name, fn) { this.events[name] = fn; }
        querySelector(selector) {
            return this.children.find(child => selector === "[role=status]" ? child.attrs.role === "status"
                : child.className === selector.slice(1)) || null;
        }
        querySelectorAll() { return [{ getAttribute: () => "100" }]; }
    }
    const list = new Element();
    list.attrs = { "data-page": "1", "data-board": "test" };
    list.parentNode = { insertBefore(node) { bar = node; } };
    const document = {
        title: "board", readyState: "complete", visibilityState: "visible",
        querySelector: () => null, getElementById: () => list,
        createElement: () => new Element(),
        addEventListener(name, fn) { events[name] = fn; },
    };
    const browser = {
        document, URL, URLSearchParams, AbortController,
        location: { href: "https://mirror.test/board?board=test", reload() { reloads++; } },
        addEventListener(name, fn) { events[name] = fn; },
        setTimeout(fn, delay) { timers.set(++sequence, { fn, delay }); return sequence; },
        clearTimeout(id) { timers.delete(id); },
        fetch(url, init) {
            return new Promise(resolve => requests.push({ url, init, resolve }));
        },
    };
    vm.runInNewContext(source, browser);
    return { requests, timers, events, list,
        nextTimer() {
            const [id, timer] = [...timers][0];
            timers.delete(id); timer.fn();
        },
        button: () => bar.querySelector(".board-updates-btn"),
        reloads: () => reloads,
    };
}
async function settle() { for (let i = 0; i < 8; i++) await new Promise(setImmediate); }
function answer(call, payload, status = 200) {
    call.resolve({ ok: status === 200, status, json: async () => payload });
}

test("visitor check stops polling even on online/visibility/pageshow and offers refresh", async () => {
    const h = browserHarness();
    h.nextTimer();
    answer(h.requests[0], { ok: false, code: "visitor_check_required", sent: false }, 403);
    await settle();
    assert.equal(h.timers.size, 0);
    h.events.online();
    h.events.visibilitychange();
    h.events.pageshow({ persisted: true });
    assert.equal(h.timers.size, 0);
    assert.equal(h.requests.length, 1);
    assert.match(h.button().textContent, /새로고침/);
    h.button().events.click();
    assert.equal(h.reloads(), 1);
    assert.equal(h.requests.length, 1);
});

test("HTML refresh is marked and visitor rejection stops both refresh retry and polling", async () => {
    const h = browserHarness();
    h.nextTimer();
    answer(h.requests[0], { ok: true, items: [item(101), item(100)] });
    await settle();
    h.button().events.click();
    assert.equal(h.requests[1].init.headers.Accept, "text/html");
    assert.equal(h.requests[1].init.headers["X-Mirror-Fetch"], "1");
    answer(h.requests[1], { code: "visitor_check_required", sent: false }, 403);
    await settle();
    assert.equal(h.timers.size, 0);
    assert.equal(h.list.attrs["aria-busy"], "false");
    h.button().events.click();
    assert.equal(h.reloads(), 1);
    assert.equal(h.requests.length, 2);
});
