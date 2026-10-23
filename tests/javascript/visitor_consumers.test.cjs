const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

const root = path.resolve(__dirname, "../../app/static/javascript");
const rejection = { status: 403, payload: {
    ok: false, code: "visitor_check_required", sent: false,
    message: "아무것도 전송하지 않았어요. 입력을 보관한 뒤 페이지를 새로고침해 주세요.",
} };

// Run the real scripts and submit listeners with a small DOM, as in the other VM harnesses.
function harness(script, modify = false) {
    const nodes = new Map(), requests = [], timers = [], navigations = [];
    let response = rejection;
    let captchaHides = 0;
    class Element {
        constructor() {
            this.attrs = {}; this.events = {}; this.value = ""; this.textContent = "";
            this.style = {}; this.checked = false; this.children = [];
            this.classList = { add() {}, remove() {}, toggle() {} };
        }
        getAttribute(name) { return this.attrs[name] || null; }
        setAttribute(name, value) { this.attrs[name] = String(value); }
        removeAttribute(name) { delete this.attrs[name]; }
        addEventListener(name, fn) { (this.events[name] ||= []).push(fn); }
        fire(name, extra = {}) { for (const fn of this.events[name] || []) fn({ preventDefault() {}, target: this, ...extra }); }
        querySelector(selector) { return nodes.get(selector) || null; }
        querySelectorAll(selector) { return selector === "[data-vote]" ? [node("vote-up")] : []; }
        appendChild(child) { this.children.push(child); child.parentNode = this; return child; }
        focus() {}
        select() {}
        showModal() { this.open = true; }
        close() { this.open = false; }
    }
    function node(id) {
        if (!nodes.has(id)) nodes.set(id, new Element());
        return nodes.get(id);
    }
    for (const prefix of ["write", "comment"]) {
        for (const name of ["form", "nickname", "password", "submit", "status", "remember-nickname",
            "remember-password", "subject", "body", "subject-count", "body-count", "content",
            "identity-error", "content-error", "password-hint", "length", "form-cancel",
            "form-target", "form-target-text", "editor", "modify-load"]) node(prefix + "-" + name);
        node(prefix + "-form").attrs = { "data-board": "test", "data-pid": "123" };
        node(prefix + "-nickname").value = "guest";
        node(prefix + "-password").value = "secret";
    }
    node("write-subject").value = "my title";
    node("write-body").value = "my draft";
    node("comment-content").value = "my comment";
    if (modify) node("write-form").attrs["data-modify-pid"] = "123";
    node("vote-panel").attrs = { "data-board": "test", "data-pid": "123" };
    node("vote-up").attrs = { "data-vote": "up", "data-count": "7" };
    node("vote-status");
    node(".read-shell[data-board-context]").attrs = { "data-board": "test", "data-pid": "123" };
    node("[data-post-delete]");
    node("comment-list");
    for (const selector of ["#owner-status", "#owner-password", "#owner-error", "#owner-dialog-title",
        "#owner-hint", ".owner-submit", ".owner-form", ".ufd-close", ".owner-cancel"]) node(selector);
    const document = {
        getElementById: id => nodes.get(id) || null,
        querySelector: selector => nodes.get(selector) || null,
        addEventListener() {},
        createElement: () => new Element(),
        body: new Element(),
    };
    const context = {
        document, URLSearchParams, console,
        addEventListener() {},
        location: { assign: url => navigations.push(url), reload: () => navigations.push("reload") },
        setTimeout: fn => { timers.push(fn); return timers.length; }, clearTimeout() {},
    };
    vm.createContext(context);
    vm.runInContext(fs.readFileSync(path.join(root, "dc_post_client.js"), "utf8"), context);
    context.MirrorDcPost.createCaptcha = () => ({
        answer: () => null, hide: () => captchaHides++, setDisabled() {}, setSource() {},
    });
    context.MirrorDcPost.postJson = (url, body) => {
        requests.push({ url, body });
        return Promise.resolve(response);
    };
    vm.runInContext(fs.readFileSync(path.join(root, script), "utf8"), context);
    return { node, document, requests, timers, navigations, captchaHides: () => captchaHides,
        respondWith: value => { response = value; } };
}
async function flush() { for (let i = 0; i < 5; i++) await new Promise(setImmediate); }

function assertNotSent(h, status) {
    assert.match(status.textContent, /전송하지 않았.*새로고침/);
    assert.equal(h.requests.length, 1, "no automatic resubmission");
    assert.equal(h.timers.length, 0, "no retry scheduled");
    assert.equal(h.navigations.length, 0, "no automatic refresh/navigation");
    assert.equal(h.captchaHides(), 0, "keep captcha input too");
}

test("write rejection preserves the draft and password without uncertain UI", async () => {
    const h = harness("write_form.js");
    h.node("write-form").fire("submit");
    await flush();
    assertNotSent(h, h.node("write-status"));
    assert.equal(h.node("write-subject").value, "my title");
    assert.equal(h.node("write-body").value, "my draft");
    assert.equal(h.node("write-password").value, "secret");
    assert.equal(h.node("write-submit").disabled, false);
});

test("modify load rejection preserves input and never resends", async () => {
    const h = harness("write_form.js", true);
    h.node("write-modify-load").fire("click");
    await flush();
    assertNotSent(h, h.node("write-status"));
    assert.equal(h.requests[0].url, "/write/modify/load");
    assert.equal(h.node("write-password").value, "secret");
    assert.equal(h.node("write-body").value, "my draft");
});

test("modify submission rejection keeps the edited draft without resending", async () => {
    const h = harness("write_form.js", true);
    h.respondWith({ status: 200, payload: {
        ok: true, subject: "original title", fingerprint: "fingerprint",
        blocks: [{ kind: "text", text: "original body" }],
    } });
    h.node("write-modify-load").fire("click");
    await flush();
    assert.equal(h.node("write-body").value, "original body");
    h.respondWith(rejection);
    h.node("write-body").value = "edited draft";
    h.node("write-form").fire("submit");
    await flush();
    assert.equal(h.requests.length, 2);
    assert.equal(h.requests[1].url, "/write");
    assert.equal(h.requests[1].body.pid, "123");
    assert.equal(h.node("write-body").value, "edited draft");
    assert.equal(h.node("write-password").value, "secret");
    assert.match(h.node("write-status").textContent, /전송하지 않았.*새로고침/);
    assert.equal(h.timers.length, 0);
    assert.equal(h.navigations.length, 0);
    assert.equal(h.captchaHides(), 0);
});

test("comment rejection preserves input without inserting a comment", async () => {
    const h = harness("comment_form.js");
    h.node("comment-form").fire("submit");
    await flush();
    assertNotSent(h, h.node("comment-status"));
    assert.equal(h.node("comment-content").value, "my comment");
    assert.equal(h.node("comment-password").value, "secret");
});

test("vote rejection leaves the count unchanged and never marks it done", async () => {
    const h = harness("vote.js");
    h.node("vote-up").fire("click");
    await flush();
    assertNotSent(h, h.node("vote-status"));
    assert.equal(h.node("vote-up").getAttribute("data-count"), "7");
    assert.equal(h.node("vote-up").getAttribute("aria-pressed"), "false");
});

for (const type of ["post", "comment"]) {
    test(`${type} delete rejection keeps the dialog/password, without deleting or navigating`, async () => {
        const h = harness("post_manage.js");
        if (type === "post") {
            h.node("[data-post-delete]").fire("click");
        } else {
            const item = { getAttribute: () => "99" };
            const button = { closest: () => item, getAttribute: () => null };
            h.node("comment-list").fire("click", { target: { closest: () => button } });
        }
        h.node("#owner-password").value = "secret";
        h.node(".owner-form").fire("submit");
        await flush();
        assertNotSent(h, h.node("#owner-status"));
        assert.equal(h.node("#owner-password").value, "secret");
        assert.equal(h.document.body.children[0].open, true);
        assert.equal(h.requests[0].url, type === "post" ? "/read/delete" : "/read/comment/delete");
    });
}
