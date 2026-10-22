const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

const source = fs.readFileSync(path.resolve(__dirname, "../../app/static/javascript/visitor_check.js"), "utf8");
function harness(options = {}) {
    const storage = options.storage || new Map();
    const message = { textContent: "", dataset: {
        cookie: "mirror_visitor", token: "signed-token", maxAge: "15552000", secure: options.secure ? "1" : "0",
    } };
    let cookie = options.cookie || "", written = "", reloads = 0;
    const document = {
        getElementById: () => message,
        get cookie() {
            if (options.readError) throw new Error("cookie denied");
            return cookie;
        },
        set cookie(value) {
            if (options.writeError) throw new Error("cookie denied");
            written = value;
            if (!options.blockCookie) cookie = "other=1; " + value.split(";")[0];
        },
    };
    const context = {
        document,
        location: { href: "https://mirror.test/read?pid=1#comments", reload() { reloads++; } },
        sessionStorage: {
            getItem(key) {
                if (options.storageReadError) throw new Error("storage denied");
                return storage.get(key) || null;
            },
            setItem(key, value) {
                if (options.storageWriteError) throw new Error("storage denied");
                storage.set(key, value);
            },
        },
    };
    return { run: () => vm.runInNewContext(source, context), storage, message,
        reloads: () => reloads, written: () => written };
}

test("stores the token with required attributes then reloads even with a fragment", () => {
    const h = harness({ secure: true });
    h.run();
    assert.equal(h.written(), "mirror_visitor=signed-token; Path=/; Max-Age=15552000; SameSite=Lax; Secure");
    assert.equal(h.reloads(), 1);
    const http = harness();
    http.run();
    assert.ok(!http.written().includes("Secure"));
});

test("exact cookie name AND value are required", () => {
    for (const cookie of ["other_mirror_visitor=signed-token", "mirror_visitor=stale", "mirror_visitor=signed-token-extra", ""]) {
        const h = harness({ blockCookie: true, cookie });
        h.run();
        assert.equal(h.reloads(), 0);
        assert.match(h.message.textContent, /허용.*새 탭/);
    }
    const h = harness({ blockCookie: true, cookie: "other=abc; mirror_visitor=signed-token; more=xyz" });
    h.run();
    assert.equal(h.reloads(), 1);
});

test("at most two automatic attempts across separate loads sharing session storage", () => {
    const storage = new Map();
    for (let i = 0; i < 4; i++) {
        const h = harness({ storage });
        h.run();
        assert.equal(h.reloads(), i < 2 ? 1 : 0);
        if (i >= 2) {
            assert.equal(h.written(), "");
            assert.match(h.message.textContent, /새 탭/);
        }
    }
});

test("cookie and storage exceptions stop with an actionable message", () => {
    for (const option of ["readError", "writeError", "storageReadError", "storageWriteError"]) {
        const h = harness({ [option]: true });
        assert.doesNotThrow(() => h.run());
        assert.equal(h.reloads(), 0);
        assert.match(h.message.textContent, /JavaScript.*쿠키.*저장소.*새 탭/);
    }
});
