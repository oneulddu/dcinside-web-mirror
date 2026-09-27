// poker_post_list.js 의 실제 동작을 node:vm 위 최소 DOM 픽스처로 실행한다.
// 화면 근처 지연 요청, 현재 글·출발 페이지 요청, 목록만 바꾸는 페이지 이동, 늦은 응답 무시,
// 실패한 페이지 재시도, 26초 시간 초과, JSON 아닌 429·깨진 JSON, 보조 키 클릭, 포커스 계약을 검증한다.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const root = path.resolve(__dirname, "../..");
const source = fs.readFileSync(path.join(root, "app/static/javascript/poker_post_list.js"), "utf8");
const BASE_HREF = "https://mirror.test/poker/hand/123?page=3";

function kebab(name) {
    return String(name).replace(/[A-Z]/g, (char) => "-" + char.toLowerCase());
}

function parseSelector(selector) {
    return String(selector).split(",").map((part) => part.trim()).filter(Boolean).map((part) => {
        const spec = { tag: null, attrs: [] };
        const tag = part.match(/^[a-zA-Z]+/);
        if (tag) spec.tag = tag[0].toUpperCase();
        for (const match of part.matchAll(/\[([\w-]+)(?:=['"]([^'"]*)['"])?\]/g)) spec.attrs.push([match[1], match[2]]);
        return spec;
    });
}

class Element {
    constructor(tag, attrs) {
        const self = this;
        this.tagName = String(tag).toUpperCase();
        this.children = [];
        this.parentNode = null;
        this.attributes = new Map();
        this.listeners = new Map();
        this.textContent = "";
        this.innerHTML = "";
        this.hidden = false;
        this.focusCount = 0;
        this.dataset = new Proxy({}, {
            get(_target, key) {
                return typeof key === "string" ? self.attributes.get("data-" + kebab(key)) : undefined;
            },
            set(_target, key, value) {
                self.attributes.set("data-" + kebab(key), String(value));
                return true;
            },
        });
        Object.entries(attrs || {}).forEach(([name, value]) => this.setAttribute(name, value));
    }

    setAttribute(name, value) { this.attributes.set(name, String(value)); }
    getAttribute(name) { return this.attributes.has(name) ? this.attributes.get(name) : null; }
    removeAttribute(name) { this.attributes.delete(name); }
    appendChild(node) { node.parentNode = this; this.children.push(node); return node; }
    focus() { this.focusCount += 1; }

    descendants() {
        return this.children.flatMap((child) => [child, ...child.descendants()]);
    }

    matches(selector) {
        return parseSelector(selector).some((spec) => {
            if (spec.tag && this.tagName !== spec.tag) return false;
            return spec.attrs.every(([name, value]) => {
                const actual = this.getAttribute(name);
                return actual !== null && (value === undefined || actual === value);
            });
        });
    }

    querySelector(selector) {
        return this.descendants().find((node) => node.matches(selector)) || null;
    }

    closest(selector) {
        for (let node = this; node; node = node.parentNode) {
            if (node.matches(selector)) return node;
        }
        return null;
    }

    addEventListener(type, handler) {
        if (!this.listeners.has(type)) this.listeners.set(type, []);
        this.listeners.get(type).push(handler);
    }

    // 실제 브라우저처럼 대상에서 위로 올라가며 클릭을 전달한다.
    click(modifiers) {
        const event = Object.assign({
            type: "click", target: this, button: 0, defaultPrevented: false,
            metaKey: false, ctrlKey: false, shiftKey: false, altKey: false,
            preventDefault() { this.defaultPrevented = true; },
        }, modifiers || {});
        for (let node = this; node; node = node.parentNode) {
            (node.listeners.get("click") || []).forEach((handler) => handler(event));
        }
        return event;
    }
}

function response(status, payload, type) {
    return {
        ok: status >= 200 && status < 300,
        status,
        headers: { get: (name) => (name.toLowerCase() === "content-type" ? type : null) },
        json: () => (payload instanceof Error ? Promise.reject(payload) : Promise.resolve(payload)),
    };
}

const json = (payload, status) => response(status || 200, payload, "application/json");

async function flush() {
    for (let i = 0; i < 10; i += 1) await new Promise((resolve) => setImmediate(resolve));
}

function createHarness(options) {
    const config = Object.assign({ observer: true }, options || {});
    const docBody = new Element("body");
    const section = new Element("section", {
        id: "poker-post-list",
        "data-list-url": "/poker/hand/list",
        "data-page": "3",
        "data-current-pid": "123",
    });
    const heading = section.appendChild(new Element("h2", { tabindex: "-1", "data-poker-list-heading": "" }));
    const message = section.appendChild(new Element("p", { role: "status", "data-poker-list-message": "" }));
    const actions = section.appendChild(new Element("div", { "data-poker-list-actions": "" }));
    actions.hidden = true;
    const retry = actions.appendChild(new Element("button", { "data-poker-list-retry": "" }));
    const fallback = actions.appendChild(new Element("a", { href: "/poker/hand?page=3" }));
    const listBody = section.appendChild(new Element("div", { "data-poker-list-body": "", "aria-busy": "false" }));
    docBody.appendChild(section);

    // 본문 자리. 목록 로딩이 이 요소를 건드리지 않는지 확인한다.
    const article = docBody.appendChild(new Element("article", { id: "article-body" }));
    article.innerHTML = "<p>본문</p>";

    // innerHTML 은 파싱하지 않으므로 서버 조각 안의 페이지 링크를 따로 붙여 흉내 낸다.
    function pagerLink(page) {
        const link = new Element("a", { href: "/poker/hand?page=" + page, "data-list-page": String(page) });
        listBody.appendChild(link);
        return link;
    }

    let clock = 0;
    let timerId = 0;
    const timers = new Map();
    const fetchCalls = [];
    const observers = [];
    const events = [];

    const context = {
        URL,
        console,
        AbortController,
        CustomEvent: class { constructor(type, options) { this.type = type; this.detail = options.detail; } },
        document: {
            readyState: "complete",
            getElementById: (id) => docBody.descendants().find((node) => node.getAttribute("id") === id) || null,
            addEventListener() {},
            dispatchEvent(event) { events.push(event); },
        },
        window: { location: { href: BASE_HREF } },
        setTimeout(handler, delay) {
            timerId += 1;
            timers.set(timerId, { handler, at: clock + (delay || 0) });
            return timerId;
        },
        clearTimeout(id) { timers.delete(id); },
        fetch(url, init) {
            const call = { url, init, signal: init && init.signal };
            call.promise = new Promise((resolve, reject) => {
                call.resolve = resolve;
                call.reject = reject;
            });
            if (call.signal && config.abortRejects !== false) {
                call.signal.addEventListener("abort", () => call.reject(new Error("AbortError")));
            }
            fetchCalls.push(call);
            return call.promise;
        },
    };
    if (config.observer) {
        context.IntersectionObserver = class {
            constructor(callback, init) {
                this.callback = callback;
                this.init = init;
                this.targets = [];
                this.disconnected = false;
                observers.push(this);
            }
            observe(target) { this.targets.push(target); }
            disconnect() { this.disconnected = true; }
            fire(isIntersecting) {
                if (!this.disconnected) this.callback(this.targets.map((target) => ({ target, isIntersecting })), this);
            }
        };
    }
    vm.createContext(context);
    vm.runInContext(source, context);

    return {
        section, heading, message, actions, retry, fallback, listBody, article, fetchCalls, observers, pagerLink, events,
        advance(ms) {
            clock += ms;
            for (const [id, timer] of Array.from(timers)) {
                if (timer.at <= clock) {
                    timers.delete(id);
                    timer.handler();
                }
            }
        },
        pendingTimers: () => timers.size,
        requestUrl: (index) => new URL(fetchCalls[index].url, BASE_HREF),
        state: () => section.dataset.state,
        busy: () => listBody.getAttribute("aria-busy"),
    };
}

// 화면 근처에 오면 첫 요청을 보내고 성공까지 마친 상태를 만든다.
async function loadedHarness(options) {
    const harness = createHarness(options);
    harness.observers[0].fire(true);
    harness.fetchCalls[0].resolve(json({ html: "<ul>3페이지</ul>", page: 3 }));
    await flush();
    assert.equal(harness.events[0].type, "poker:list-rendered");
    assert.equal(harness.events[0].detail.root, harness.listBody);
    return harness;
}

const tests = [];
function test(name, fn) {
    tests.push({ name, fn });
}

test("화면에서 멀면 요청하지 않고, 800px 안으로 들어오면 출발 페이지와 현재 글로 한 번만 요청한다", async () => {
    const harness = createHarness();
    assert.equal(harness.fetchCalls.length, 0);
    assert.equal(harness.state(), "idle");
    assert.equal(harness.observers.length, 1);
    assert.match(harness.observers[0].init.rootMargin, /^800px/);

    harness.observers[0].fire(false);
    assert.equal(harness.fetchCalls.length, 0);

    harness.observers[0].fire(true);
    assert.equal(harness.fetchCalls.length, 1);
    assert.equal(harness.observers[0].disconnected, true);
    const url = harness.requestUrl(0);
    assert.equal(url.pathname, "/poker/hand/list");
    assert.equal(url.searchParams.get("page"), "3");
    assert.equal(url.searchParams.get("current_pid"), "123");
    assert.equal(harness.state(), "loading");
    assert.equal(harness.busy(), "true");
    assert.match(harness.message.textContent, /불러오는 중/);

    harness.observers[0].fire(true);
    assert.equal(harness.fetchCalls.length, 1);
});

test("IntersectionObserver 가 없으면 바로 한 번 요청한다", async () => {
    const harness = createHarness({ observer: false });
    assert.equal(harness.fetchCalls.length, 1);
    assert.equal(harness.requestUrl(0).searchParams.get("page"), "3");
});

test("첫 성공은 서버 조각을 넣고 포커스를 옮기지 않으며 본문은 그대로 둔다", async () => {
    const harness = await loadedHarness();
    assert.equal(harness.listBody.innerHTML, "<ul>3페이지</ul>");
    assert.equal(harness.state(), "ready");
    assert.equal(harness.busy(), "false");
    assert.equal(harness.heading.focusCount, 0);
    assert.equal(harness.message.textContent, "");
    assert.equal(harness.actions.hidden, true);
    assert.equal(harness.article.innerHTML, "<p>본문</p>");
    assert.equal(harness.pendingTimers(), 0);
});

test("페이지 링크는 목록만 바꾸고 성공하면 목록 제목으로 포커스를 옮긴다", async () => {
    const harness = await loadedHarness();
    const next = harness.pagerLink(4);
    const event = next.click();
    assert.equal(event.defaultPrevented, true);
    assert.equal(harness.fetchCalls.length, 2);
    assert.equal(harness.requestUrl(1).searchParams.get("page"), "4");
    assert.equal(harness.requestUrl(1).searchParams.get("current_pid"), "123");
    // 기다리는 동안 이전 행은 남는다.
    assert.equal(harness.listBody.innerHTML, "<ul>3페이지</ul>");
    assert.equal(harness.busy(), "true");

    harness.fetchCalls[1].resolve(json({ html: "<ul>4페이지</ul>", page: 4 }));
    await flush();
    assert.equal(harness.listBody.innerHTML, "<ul>4페이지</ul>");
    assert.equal(harness.heading.focusCount, 1);
    assert.match(harness.message.textContent, /4페이지/);
    assert.equal(harness.article.innerHTML, "<p>본문</p>");
    assert.equal(harness.section.dataset.shownPage, "4");
});

test("보조 키·가운데 클릭과 일반 글 링크는 브라우저 기본 동작에 맡긴다", async () => {
    const harness = await loadedHarness();
    const next = harness.pagerLink(4);
    for (const modifiers of [{ metaKey: true }, { ctrlKey: true }, { shiftKey: true }, { altKey: true }, { button: 1 }]) {
        const event = next.click(modifiers);
        assert.equal(event.defaultPrevented, false, JSON.stringify(modifiers));
    }
    const post = harness.listBody.appendChild(new Element("a", { href: "/poker/hand/122?page=3" }));
    assert.equal(post.click().defaultPrevented, false);
    assert.equal(harness.fetchCalls.length, 1);
});

test("늦게 온 이전 응답은 무시하고 마지막 요청만 표시하며 이전 요청은 끊는다", async () => {
    // 끊긴 요청이 그래도 응답하는 경우까지 보려고 abort 가 reject 하지 않게 둔다.
    const harness = await loadedHarness({ abortRejects: false });
    harness.pagerLink(4).click();
    harness.pagerLink(5).click();
    assert.equal(harness.fetchCalls.length, 3);
    assert.equal(harness.fetchCalls[1].signal.aborted, true);
    assert.equal(harness.fetchCalls[2].signal.aborted, false);

    harness.fetchCalls[2].resolve(json({ html: "<ul>5페이지</ul>", page: 5 }));
    await flush();
    harness.fetchCalls[1].resolve(json({ html: "<ul>4페이지</ul>", page: 4 }));
    await flush();
    assert.equal(harness.listBody.innerHTML, "<ul>5페이지</ul>");
    assert.equal(harness.state(), "ready");
    assert.equal(harness.heading.focusCount, 1);
});

test("실패하면 기존 행을 남기고 안내·재시도·게시판 링크를 보이며, 재시도는 실패한 페이지를 다시 요청한다", async () => {
    const harness = await loadedHarness();
    harness.pagerLink(4).click();
    harness.fetchCalls[1].resolve(json({ error: "포커고수 응답이 늦어요" }, 503));
    await flush();
    assert.equal(harness.state(), "error");
    assert.equal(harness.message.textContent, "포커고수 응답이 늦어요");
    assert.equal(harness.actions.hidden, false);
    assert.equal(harness.fallback.getAttribute("href"), "/poker/hand?page=4");
    assert.equal(harness.listBody.innerHTML, "<ul>3페이지</ul>");
    assert.equal(harness.heading.focusCount, 0);
    assert.equal(harness.article.innerHTML, "<p>본문</p>");

    // 자동 재시도는 없다.
    harness.advance(120000);
    await flush();
    assert.equal(harness.fetchCalls.length, 2);

    harness.retry.click();
    assert.equal(harness.fetchCalls.length, 3);
    assert.equal(harness.requestUrl(2).searchParams.get("page"), "4");
    assert.equal(harness.retry.getAttribute("aria-disabled"), "true");
    harness.retry.click();
    assert.equal(harness.fetchCalls.length, 3);

    harness.fetchCalls[2].resolve(json({ html: "<ul>4페이지</ul>", page: 4 }));
    await flush();
    assert.equal(harness.listBody.innerHTML, "<ul>4페이지</ul>");
    assert.equal(harness.actions.hidden, true);
    assert.equal(harness.heading.focusCount, 1);
});

test("첫 요청 실패 후 재시도도 출발 페이지를 다시 요청한다", async () => {
    const harness = createHarness({ observer: false });
    harness.fetchCalls[0].reject(new TypeError("network"));
    await flush();
    assert.equal(harness.state(), "error");
    assert.equal(harness.fallback.getAttribute("href"), "/poker/hand?page=3");
    harness.retry.click();
    assert.equal(harness.requestUrl(1).searchParams.get("page"), "3");
});

test("26초가 지나면 요청을 끊고 시간 초과 안내를 두며 늦은 응답은 무시한다", async () => {
    const harness = createHarness({ observer: false });
    const first = harness.fetchCalls[0];
    harness.advance(25999);
    assert.equal(harness.state(), "loading");
    harness.advance(1);
    await flush();
    assert.equal(first.signal.aborted, true);
    assert.equal(harness.state(), "error");
    assert.match(harness.message.textContent, /늦어요/);
    assert.equal(harness.fetchCalls.length, 1);

    harness.retry.click();
    assert.equal(harness.fetchCalls.length, 2);
    assert.equal(harness.requestUrl(1).searchParams.get("page"), "3");
});

test("시간 초과 뒤 끊긴 요청이 늦게 성공해도 목록을 바꾸지 않는다", async () => {
    const harness = createHarness({ observer: false, abortRejects: false });
    harness.advance(26000);
    await flush();
    harness.fetchCalls[0].resolve(json({ html: "<ul>늦은 응답</ul>", page: 3 }));
    await flush();
    assert.equal(harness.state(), "error");
    assert.equal(harness.listBody.innerHTML, "");
    assert.equal(harness.actions.hidden, false);
});

test("JSON 이 아닌 HTML 429 와 깨진 JSON 은 고정 안내로 처리하고 서버 본문을 넣지 않는다", async () => {
    const harness = await loadedHarness();
    harness.pagerLink(4).click();
    harness.fetchCalls[1].resolve(response(429, { html: "<b>차단</b>", error: "원본 문구" }, "text/html; charset=utf-8"));
    await flush();
    assert.equal(harness.state(), "error");
    assert.equal(harness.message.textContent, "목록을 불러오지 못했어요. 잠시 후 다시 시도해 주세요.");
    assert.equal(harness.listBody.innerHTML, "<ul>3페이지</ul>");

    harness.retry.click();
    harness.fetchCalls[2].resolve(response(200, new SyntaxError("bad json"), "application/json"));
    await flush();
    assert.equal(harness.state(), "error");
    assert.equal(harness.message.textContent, "목록을 불러오지 못했어요. 잠시 후 다시 시도해 주세요.");

    harness.retry.click();
    harness.fetchCalls[3].resolve(json({ page: 4 }));
    await flush();
    assert.equal(harness.state(), "error");
    assert.equal(harness.listBody.innerHTML, "<ul>3페이지</ul>");
    assert.equal(harness.requestUrl(3).searchParams.get("page"), "4");
});

(async function main() {
    for (const entry of tests) {
        try {
            await entry.fn();
        } catch (error) {
            console.error("실패: " + entry.name);
            throw error;
        }
    }
    console.log("poker_post_list_contract=passed (" + tests.length + ")");
})().catch((error) => {
    console.error(error);
    process.exitCode = 1;
});
