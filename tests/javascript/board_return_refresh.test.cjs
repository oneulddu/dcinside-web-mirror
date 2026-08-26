const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const root = path.resolve(__dirname, "../..");
const source = fs.readFileSync(
    path.join(root, "app/static/javascript/board_return_refresh.js"),
    "utf8"
);

function createHarness(options) {
    const initialHref = options.href;
    const navigationType = options.navigationType || "back_forward";
    const listeners = { window: {}, document: {} };
    const storage = new Map();
    const pendingFetches = [];
    const requestedUrls = [];
    let replacementCount = 0;
    const location = {};

    function setHref(value) {
        const parsed = new URL(value, location.href || initialHref);
        location.href = parsed.href;
        location.origin = parsed.origin;
        location.pathname = parsed.pathname;
        location.search = parsed.search;
    }

    function boardKey() {
        const url = new URL(location.href);
        url.searchParams.delete("refresh");
        return url.pathname + url.search;
    }

    setHref(initialHref);
    if (options.markedReturn) {
        storage.set("mirror_board_return_refresh_v1", boardKey());
    }

    const boardNode = {
        replaceWith() {
            replacementCount += 1;
        },
    };

    const context = {
        URL,
        Promise,
        setTimeout,
        clearTimeout,
        CustomEvent: function CustomEvent(type, init) {
            this.type = type;
            this.detail = init.detail;
        },
        DOMParser: function DOMParser() {
            this.parseFromString = function parseFromString() {
                return { getElementById: () => boardNode };
            };
        },
        fetch(url) {
            requestedUrls.push(url);
            return new Promise((resolve, reject) => {
                pendingFetches.push({ reject, resolve, url });
            });
        },
        window: {
            location,
            history: {
                state: null,
                replaceState(state, unused, value) {
                    setHref(value);
                },
            },
            performance: {
                getEntriesByType() {
                    return [{ type: navigationType }];
                },
            },
            sessionStorage: {
                getItem(key) {
                    return storage.has(key) ? storage.get(key) : null;
                },
                setItem(key, value) {
                    storage.set(key, value);
                },
                removeItem(key) {
                    storage.delete(key);
                },
            },
            addEventListener(type, callback) {
                listeners.window[type] = callback;
            },
        },
        document: {
            addEventListener(type, callback) {
                listeners.document[type] = callback;
            },
            getElementById() {
                return boardNode;
            },
            dispatchEvent() {},
        },
    };

    vm.runInNewContext(source, context);

    return {
        clickRead(pid) {
            listeners.document.click({
                altKey: false,
                button: 0,
                ctrlKey: false,
                defaultPrevented: false,
                metaKey: false,
                shiftKey: false,
                target: {
                    closest() {
                        return {
                            hasAttribute: () => false,
                            href: `https://mir.rootios.com/read?board=test&pid=${pid}`,
                            target: "",
                        };
                    },
                },
            });
        },
        fetchCount() {
            return requestedUrls.length;
        },
        href() {
            return location.href;
        },
        pageShow(persisted) {
            listeners.window.pageshow({ persisted: Boolean(persisted) });
        },
        pendingCount() {
            return pendingFetches.length;
        },
        rejectNext() {
            assert.ok(pendingFetches.length > 0, "expected a pending fetch to reject");
            pendingFetches.shift().reject(new Error("upstream unavailable"));
        },
        replacementCount() {
            return replacementCount;
        },
        resolveNext() {
            assert.ok(pendingFetches.length > 0, "expected a pending fetch to resolve");
            const pending = pendingFetches.shift();
            pending.resolve({
                ok: true,
                url: pending.url,
                text: async () => '<section id="board-list"></section>',
            });
        },
    };
}

async function settle() {
    for (let index = 0; index < 6; index += 1) {
        await Promise.resolve();
    }
    await new Promise((resolve) => setImmediate(resolve));
}

async function repeatPageShow(harness, count) {
    for (let index = 0; index < count; index += 1) {
        harness.pageShow(true);
        await settle();
    }
}

async function main() {
    const direct = createHarness({
        href: "https://mir.rootios.com/board?board=test&page=1&refresh=1",
        markedReturn: true,
        navigationType: "navigate",
    });
    direct.pageShow(false);
    await repeatPageShow(direct, 20);
    assert.equal(direct.fetchCount(), 0, "refresh=1 entry must not fetch again");
    assert.equal(new URL(direct.href()).searchParams.has("refresh"), false);

    const restored = createHarness({
        href: "https://mir.rootios.com/board?board=test&page=1",
        markedReturn: true,
    });
    restored.pageShow(true);
    await repeatPageShow(restored, 20);
    assert.equal(restored.fetchCount(), 1, "repeated pageshow must share one refresh");
    restored.resolveNext();
    await settle();
    await repeatPageShow(restored, 5);
    assert.equal(restored.fetchCount(), 1);
    assert.equal(restored.replacementCount(), 1);

    restored.clickRead(2);
    restored.pageShow(true);
    assert.equal(restored.fetchCount(), 2, "a later article return must refresh once again");
    restored.resolveNext();
    await settle();
    await repeatPageShow(restored, 5);
    assert.equal(restored.fetchCount(), 2);

    const raced = createHarness({
        href: "https://mir.rootios.com/board?board=test&page=1",
        markedReturn: true,
    });
    raced.pageShow(true);
    raced.clickRead(3);
    raced.pageShow(true);
    await repeatPageShow(raced, 10);
    assert.equal(raced.fetchCount(), 1, "a second refresh must wait for the in-flight request");
    raced.resolveNext();
    await settle();
    assert.equal(raced.fetchCount(), 2, "the latest return must be replayed after completion");
    assert.equal(raced.pendingCount(), 1);
    await repeatPageShow(raced, 10);
    assert.equal(raced.fetchCount(), 2, "pending replay must also be one-shot");
    raced.resolveNext();
    await settle();
    assert.equal(raced.replacementCount(), 2);

    const latestReturn = createHarness({
        href: "https://mir.rootios.com/board?board=test&page=1",
        markedReturn: true,
    });
    latestReturn.pageShow(true);
    latestReturn.clickRead(5);
    latestReturn.pageShow(true);
    latestReturn.clickRead(6);
    latestReturn.resolveNext();
    await settle();
    assert.equal(latestReturn.fetchCount(), 2, "the first pending return must start next");
    latestReturn.pageShow(true);
    assert.equal(
        latestReturn.fetchCount(),
        2,
        "a newer return must wait while the pending replay is in flight"
    );
    latestReturn.resolveNext();
    await settle();
    assert.equal(
        latestReturn.fetchCount(),
        3,
        "starting a pending replay must not erase a still newer article return"
    );
    latestReturn.resolveNext();
    await settle();
    await repeatPageShow(latestReturn, 10);
    assert.equal(latestReturn.fetchCount(), 3);
    assert.equal(latestReturn.replacementCount(), 3);

    const failed = createHarness({
        href: "https://mir.rootios.com/board?board=test&page=1",
        markedReturn: true,
    });
    failed.pageShow(true);
    failed.rejectNext();
    await settle();
    await repeatPageShow(failed, 20);
    assert.equal(failed.fetchCount(), 1, "a failed refresh must not restart the loop");
    assert.equal(failed.replacementCount(), 0);

    const failedWithPending = createHarness({
        href: "https://mir.rootios.com/board?board=test&page=1",
        markedReturn: true,
    });
    failedWithPending.pageShow(true);
    failedWithPending.clickRead(4);
    failedWithPending.pageShow(true);
    failedWithPending.rejectNext();
    await settle();
    assert.equal(
        failedWithPending.fetchCount(),
        2,
        "a failed in-flight request must still replay a newer return"
    );
    failedWithPending.resolveNext();
    await settle();
    assert.equal(failedWithPending.replacementCount(), 1);

    process.stdout.write("board_return_refresh_state_machine=passed\n");
}

main().catch((error) => {
    process.stderr.write(`${error.stack || error}\n`);
    process.exitCode = 1;
});
