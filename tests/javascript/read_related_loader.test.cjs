// read_related_loader.js 의 실제 동작을 node:vm 위 최소 DOM 픽스처로 실행한다.
// 초기 자동 로드 1회, 종료 상태 가드, 미확정(has_more null) 처리, 타임아웃과
// 수동 재시도, 실패 시 기존 행 보존, 커서/필터 유지 계약을 검증한다.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const root = path.resolve(__dirname, "../..");
const source = fs.readFileSync(
    path.join(root, "app/static/javascript/read_related_loader.js"),
    "utf8"
);
const BASE_HREF = "https://mirror.test/read?board=airforce&pid=5000";

function kebab(name) {
    return String(name).replace(/[A-Z]/g, (char) => "-" + char.toLowerCase());
}

function parseSelector(selector) {
    return String(selector)
        .split(",")
        .map((part) => part.trim())
        .filter(Boolean)
        .map((part) => {
            const spec = { tag: null, classes: [], attrs: [] };
            const tag = part.match(/^[a-zA-Z]+/);
            if (tag) spec.tag = tag[0].toUpperCase();
            for (const match of part.matchAll(/\.([\w-]+)/g)) spec.classes.push(match[1]);
            for (const match of part.matchAll(/\[([\w-]+)(?:=['"]([^'"]*)['"])?\]/g)) {
                spec.attrs.push([match[1], match[2]]);
            }
            return spec;
        });
}

function matchesSpec(node, spec) {
    if (spec.tag && node.tagName !== spec.tag) return false;
    for (const name of spec.classes) {
        if (!node.classList.contains(name)) return false;
    }
    for (const [name, value] of spec.attrs) {
        const actual = node.getAttribute(name);
        if (actual === null) return false;
        if (value !== undefined && actual !== value) return false;
    }
    return true;
}

class Element {
    constructor(tag) {
        const self = this;
        this.tagName = String(tag).toUpperCase();
        this.children = [];
        this.parentNode = null;
        this.attributes = new Map();
        this.listeners = new Map();
        this.className = "";
        this.textContent = "";
        this.innerHTML = "";
        this.disabled = false;
        this.classList = {
            toggle(name, force) {
                const names = new Set(self.className.split(/\s+/).filter(Boolean));
                const enabled = force === undefined ? !names.has(name) : !!force;
                if (enabled) names.add(name);
                else names.delete(name);
                self.className = Array.from(names).join(" ");
            },
            add(...values) {
                values.forEach((value) => self.classList.toggle(value, true));
            },
            remove(...values) {
                values.forEach((value) => self.classList.toggle(value, false));
            },
            contains(value) {
                return self.className.split(/\s+/).includes(value);
            },
        };
        this.dataset = new Proxy({}, {
            get(_target, key) {
                if (typeof key !== "string") return undefined;
                return self.attributes.get("data-" + kebab(key));
            },
            set(_target, key, value) {
                self.attributes.set("data-" + kebab(key), String(value));
                return true;
            },
            has(_target, key) {
                return typeof key === "string" && self.attributes.has("data-" + kebab(key));
            },
        });
    }

    setAttribute(name, value) {
        const text = String(value);
        this.attributes.set(name, text);
        if (name === "class") this.className = text;
    }

    getAttribute(name) {
        return this.attributes.has(name) ? this.attributes.get(name) : null;
    }

    removeAttribute(name) {
        this.attributes.delete(name);
    }

    set href(value) {
        this.setAttribute("href", value);
    }

    get href() {
        return this.getAttribute("href");
    }

    appendChild(node) {
        node.remove();
        node.parentNode = this;
        this.children.push(node);
        return node;
    }

    remove() {
        if (!this.parentNode) return;
        const index = this.parentNode.children.indexOf(this);
        if (index >= 0) this.parentNode.children.splice(index, 1);
        this.parentNode = null;
    }

    descendants() {
        const out = [];
        for (const child of this.children) {
            out.push(child);
            out.push(...child.descendants());
        }
        return out;
    }

    matches(selector) {
        return parseSelector(selector).some((spec) => matchesSpec(this, spec));
    }

    querySelectorAll(selector) {
        const specs = parseSelector(selector);
        return this.descendants().filter((node) => specs.some((spec) => matchesSpec(node, spec)));
    }

    querySelector(selector) {
        return this.querySelectorAll(selector)[0] || null;
    }

    closest(selector) {
        let node = this;
        while (node) {
            if (node.matches(selector)) return node;
            node = node.parentNode;
        }
        return null;
    }

    addEventListener(type, handler) {
        if (!this.listeners.has(type)) this.listeners.set(type, []);
        this.listeners.get(type).push(handler);
    }

    dispatch(type) {
        (this.listeners.get(type) || []).forEach((handler) => handler({ type }));
    }
}

function jsonResponse(payload, status) {
    const code = status || 200;
    return { ok: code < 400, status: code, json: () => Promise.resolve(payload) };
}

function brokenResponse(status) {
    const code = status || 200;
    return { ok: code < 400, status: code, json: () => Promise.reject(new Error("invalid json")) };
}

function item(id, extra) {
    return Object.assign({ id: id, title: "제목 " + id, comment_count: 0, voteup_count: 0 }, extra || {});
}

async function flush(rounds) {
    for (let i = 0; i < (rounds || 8); i += 1) {
        await new Promise((resolve) => setImmediate(resolve));
    }
}

function createHarness(options) {
    const config = options || {};
    const body = new Element("body");
    const section = new Element("section");
    section.setAttribute("id", "related-section");

    const dataset = Object.assign({ board: "airforce", pid: "5000", limit: "12" }, config.dataset || {});
    Object.keys(dataset).forEach((key) => {
        if (dataset[key] !== null && dataset[key] !== undefined) section.dataset[key] = dataset[key];
    });

    const list = new Element("ul");
    list.setAttribute("id", "related-list");
    list.setAttribute("aria-busy", "false");
    (config.rows || []).forEach((row) => {
        const li = new Element("li");
        li.dataset.postId = row.id;
        const link = new Element("a");
        link.className = "feed-item";
        link.dataset.postId = row.id;
        link.href = "/read?board=airforce&pid=" + row.id;
        li.appendChild(link);
        list.appendChild(li);
    });

    const status = new Element("p");
    status.setAttribute("id", "related-status");

    const button = new Element("button");
    button.setAttribute("id", "related-load-button");
    button.dataset.defaultLabel = "더보기";
    const label = new Element("span");
    label.setAttribute("data-related-more-label", "");
    button.appendChild(label);

    section.appendChild(list);
    section.appendChild(status);
    section.appendChild(button);
    body.appendChild(section);

    let clock = 0;
    let timerId = 0;
    const timers = new Map();
    const fetchCalls = [];

    const context = {
        URL,
        URLSearchParams,
        console,
        AbortController,
        document: {
            readyState: "complete",
            body: body,
            createElement: (tag) => new Element(tag),
            getElementById: (id) => body.descendants().find((node) => node.getAttribute("id") === id) || null,
            addEventListener() {},
        },
        window: { location: { href: BASE_HREF } },
        setTimeout(handler, delay) {
            timerId += 1;
            timers.set(timerId, { handler: handler, at: clock + (delay || 0) });
            return timerId;
        },
        clearTimeout(id) {
            timers.delete(id);
        },
        fetch(url, init) {
            const call = { url: url, init: init, signal: init && init.signal };
            call.promise = new Promise((resolve, reject) => {
                call.resolve = resolve;
                call.reject = reject;
            });
            fetchCalls.push(call);
            return call.promise;
        },
    };
    vm.createContext(context);
    vm.runInContext(source, context);

    return {
        button,
        list,
        fetchCalls,
        click: () => button.dispatch("click"),
        advance(ms) {
            clock += ms;
            for (const [id, timer] of Array.from(timers)) {
                if (timer.at <= clock) {
                    timers.delete(id);
                    timer.handler();
                }
            }
        },
        statusRows: () => list.querySelectorAll("[data-related-loader-status='1']"),
        statusText() {
            const row = list.querySelector("[data-related-loader-status='1']");
            return row ? row.textContent : null;
        },
        live: () => status.textContent,
        busy: () => list.getAttribute("aria-busy"),
        buttonState: () => button.dataset.state,
        postIds: () => list.querySelectorAll("a.feed-item").map((link) => link.dataset.postId),
        hrefFor(id) {
            const link = list.querySelectorAll("a.feed-item").find((node) => node.dataset.postId === id);
            return link ? link.getAttribute("href") : null;
        },
        requestUrl(index) {
            return new URL(fetchCalls[index].url, BASE_HREF);
        },
    };
}

const tests = [];
function test(name, fn) {
    tests.push({ name, fn });
}

test("빈 목록이고 has_more 미확정이면 바인딩 직후 한 번만 자동으로 불러온다", async () => {
    const harness = createHarness({
        dataset: {
            hasMore: "unknown",
            kind: "hit",
            recommend: "1",
            sourcePage: "3",
            headId: "10",
            searchType: "subject_m",
            searchKeyword: "테스트",
            galleryName: "공군",
        },
    });

    assert.equal(harness.fetchCalls.length, 1);
    assert.equal(harness.buttonState(), "loading");
    assert.equal(harness.button.disabled, true);
    assert.equal(harness.busy(), "true");
    assert.equal(harness.statusText(), "다른 게시글을 불러오는 중...");

    const url = harness.requestUrl(0);
    assert.equal(url.pathname, "/read/related");
    assert.equal(url.searchParams.get("board"), "airforce");
    assert.equal(url.searchParams.get("pid"), "5000");
    assert.equal(url.searchParams.get("limit"), "12");
    assert.equal(url.searchParams.get("kind"), "hit");
    assert.equal(url.searchParams.get("recommend"), "1");
    assert.equal(url.searchParams.get("source_page"), "3");
    assert.equal(url.searchParams.get("headid"), "10");
    assert.equal(url.searchParams.get("s_type"), "subject_m");
    assert.equal(url.searchParams.get("serval"), "테스트");
    assert.equal(url.searchParams.has("after_pid"), false);

    harness.fetchCalls[0].resolve(jsonResponse({
        ok: true,
        items: [item("101", { source_page: "7" }), item("102")],
        has_more: true,
    }));
    await flush();

    assert.deepEqual(harness.postIds(), ["101", "102"]);
    assert.equal(harness.buttonState(), "idle");
    assert.equal(harness.button.disabled, false);
    assert.equal(harness.busy(), "false");
    assert.equal(harness.statusText(), null);
    assert.match(harness.live(), /2개/);
    assert.equal(harness.fetchCalls.length, 1);

    const first = new URL(harness.hrefFor("101"), BASE_HREF);
    assert.equal(first.searchParams.get("source_page"), "7");
    assert.equal(first.searchParams.get("kind"), "hit");
    assert.equal(first.searchParams.get("recommend"), "1");
    assert.equal(first.searchParams.get("headid"), "10");
    assert.equal(first.searchParams.get("serval"), "테스트");
    assert.equal(first.searchParams.get("gallery_name"), "공군");
    const second = new URL(harness.hrefFor("102"), BASE_HREF);
    assert.equal(second.searchParams.get("source_page"), "3");
});

test("서버가 이미 목록을 그렸으면 자동으로 불러오지 않고 커서만 이어간다", async () => {
    const harness = createHarness({ rows: [{ id: "301" }, { id: "302" }], dataset: { hasMore: "true" } });

    assert.equal(harness.fetchCalls.length, 0);
    assert.equal(harness.buttonState(), "idle");
    assert.equal(harness.busy(), "false");
    assert.equal(harness.statusText(), null);

    harness.click();
    assert.equal(harness.fetchCalls.length, 1);
    assert.equal(harness.requestUrl(0).searchParams.get("after_pid"), "302");

    harness.fetchCalls[0].resolve(jsonResponse({
        ok: true,
        items: [item("302"), item("303")],
        has_more: null,
    }));
    await flush();

    assert.deepEqual(harness.postIds(), ["301", "302", "303"]);
    assert.equal(harness.buttonState(), "idle");
    assert.equal(harness.statusText(), null);

    harness.click();
    assert.equal(harness.fetchCalls.length, 2);
    assert.equal(harness.requestUrl(1).searchParams.get("after_pid"), "303");
});

test("has_more=false 로 렌더된 빈 목록은 자동 로드 없이 종료 문구만 남긴다", async () => {
    const harness = createHarness({ dataset: { hasMore: "false" } });

    assert.equal(harness.fetchCalls.length, 0);
    assert.equal(harness.buttonState(), "no-more");
    assert.equal(harness.button.disabled, true);
    assert.equal(harness.busy(), "false");
    assert.equal(harness.statusText(), "더 불러올 게시글이 없습니다.");
    assert.equal(harness.statusRows().length, 1);

    harness.click();
    assert.equal(harness.fetchCalls.length, 0);
});

test("has_more=false 이고 목록이 있으면 종료 문구를 덧붙이지 않는다", async () => {
    const harness = createHarness({ rows: [{ id: "401" }], dataset: { hasMore: "false" } });

    assert.equal(harness.fetchCalls.length, 0);
    assert.equal(harness.buttonState(), "no-more");
    assert.equal(harness.statusRows().length, 0);
    assert.deepEqual(harness.postIds(), ["401"]);
});

test("응답이 종료를 알리면 상태 가드가 이후 요청을 막는다", async () => {
    const harness = createHarness({ dataset: { hasMore: "true" } });

    harness.fetchCalls[0].resolve(jsonResponse({ ok: true, items: [item("501")], has_more: false }));
    await flush();

    assert.deepEqual(harness.postIds(), ["501"]);
    assert.equal(harness.buttonState(), "no-more");
    assert.equal(harness.statusRows().length, 1);
    assert.equal(harness.statusText(), "더 불러올 게시글이 없습니다.");

    harness.click();
    harness.click();
    assert.equal(harness.fetchCalls.length, 1);
});

test("요청 중에는 추가 클릭이 중복 요청을 만들지 않는다", async () => {
    const harness = createHarness({});

    assert.equal(harness.fetchCalls.length, 1);
    harness.click();
    harness.click();
    await flush(2);
    assert.equal(harness.fetchCalls.length, 1);
});

test("has_more 가 null 이면 종료로 단정하지 않고 다시 확인할 수 있다", async () => {
    const harness = createHarness({});

    harness.fetchCalls[0].resolve(jsonResponse({ ok: true, items: [], has_more: null }));
    await flush();

    assert.equal(harness.buttonState(), "refresh");
    assert.equal(harness.button.disabled, false);
    assert.match(harness.statusText(), /다시 확인/);

    harness.click();
    assert.equal(harness.fetchCalls.length, 2);
});

test("타임아웃은 요청을 끊고 수동 재시도만 허용하며 늦은 응답을 무시한다", async () => {
    const harness = createHarness({});
    const first = harness.fetchCalls[0];

    harness.advance(15000);
    await flush();

    assert.equal(harness.buttonState(), "retry");
    assert.equal(harness.button.disabled, false);
    assert.equal(harness.busy(), "false");
    assert.match(harness.statusText(), /시간이 너무 오래/);
    assert.equal(first.signal.aborted, true);
    assert.equal(harness.fetchCalls.length, 1);

    harness.click();
    assert.equal(harness.fetchCalls.length, 2);

    first.resolve(jsonResponse({ ok: true, items: [item("601")], has_more: true }));
    await flush();
    assert.deepEqual(harness.postIds(), []);
    assert.equal(harness.buttonState(), "loading");

    harness.fetchCalls[1].resolve(jsonResponse({ ok: true, items: [item("602")], has_more: true }));
    await flush();

    assert.deepEqual(harness.postIds(), ["602"]);
    assert.equal(harness.buttonState(), "idle");
    assert.equal(harness.busy(), "false");
});

test("JSON 파싱 실패에도 기존 목록을 유지하고 재시도 상태로 둔다", async () => {
    const harness = createHarness({ rows: [{ id: "701" }], dataset: { hasMore: "true" } });

    harness.click();
    harness.fetchCalls[0].resolve(brokenResponse(200));
    await flush();

    assert.deepEqual(harness.postIds(), ["701"]);
    assert.equal(harness.buttonState(), "retry");
    assert.equal(harness.busy(), "false");
    assert.equal(harness.statusText(), "다른 게시글을 불러오지 못했습니다. 다시 시도할 수 있어요.");
});

test("502 실패 응답은 원인별 안내를 남기고 기존 목록을 지우지 않는다", async () => {
    const harness = createHarness({ rows: [{ id: "801" }], dataset: { hasMore: "true" } });

    harness.click();
    harness.fetchCalls[0].resolve(jsonResponse({
        ok: false,
        items: [],
        error: "related_position_unavailable",
    }, 502));
    await flush();

    assert.deepEqual(harness.postIds(), ["801"]);
    assert.equal(harness.buttonState(), "retry");
    assert.match(harness.statusText(), /이 위치에서/);
    assert.match(harness.live(), /이 위치에서/);

    harness.click();
    assert.equal(harness.fetchCalls.length, 2);
    harness.fetchCalls[1].resolve(jsonResponse({ ok: true, items: [item("802")], has_more: true }));
    await flush();
    assert.deepEqual(harness.postIds(), ["801", "802"]);
});

test("items 가 배열이 아니면 종료로 보지 않고 재시도 가능한 실패로 다룬다", async () => {
    const harness = createHarness({ rows: [{ id: "901" }], dataset: { hasMore: "true" } });

    harness.click();
    harness.fetchCalls[0].resolve(jsonResponse({ ok: true, items: null, has_more: false }));
    await flush();

    assert.deepEqual(harness.postIds(), ["901"]);
    assert.equal(harness.buttonState(), "retry");
    assert.equal(harness.button.disabled, false);
    assert.equal(harness.statusRows().length, 1);
    assert.equal(harness.statusText(), "다른 게시글을 불러오지 못했습니다. 다시 시도할 수 있어요.");

    harness.click();
    assert.equal(harness.fetchCalls.length, 2);
    harness.fetchCalls[1].resolve(jsonResponse({ ok: true, items: [item("902")], has_more: true }));
    await flush();
    assert.deepEqual(harness.postIds(), ["901", "902"]);
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
    console.log("read_related_loader_contract=passed");
})();
