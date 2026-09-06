const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const root = path.resolve(__dirname, "../..");
const readSource = fs.readFileSync(path.join(root, "app/static/javascript/read_state.js"), "utf8");
const previewSource = fs.readFileSync(path.join(root, "app/static/javascript/link_preview.js"), "utf8");
const MEDIA_KEY = "mirror_media_block_mode_v1";
const LEGACY_KEY = "mirror_dccon_block_v1";
const PREVIEW_URL = "/embed/link-preview-image?url=https%3A%2F%2Fexample.com%2Fimage.jpg&token=signed";

// Small DOM/event fixture, like the board-return harness: execute the actual scripts
// and record every image src assignment, including assignments before insertion.
function createHarness({ mode = "none", empty = false, boot = true, loading = false, readError = false } = {}) {
    const requests = [];
    const pending = [];
    const writes = [];
    const storage = new Map([[MEDIA_KEY, mode]]);
    const failures = { read: readError, write: false };
    let document;
    function dataKey(name) {
        return name.slice(5).replace(/-([a-z])/g, (_, letter) => letter.toUpperCase());
    }
    function matchesSimple(node, selector) {
        const tag = selector.match(/^[a-z]+/i);
        if (tag && node.tagName !== tag[0].toUpperCase()) return false;
        for (const match of selector.matchAll(/\.([\w-]+)/g)) {
            // Attribute values in this fixture have no dots.
            if (!node.className.split(/\s+/).includes(match[1])) return false;
        }
        for (const match of selector.matchAll(/\[([\w-]+)(?:(\*?=)"([^"]*)")?\]/g)) {
            const value = node.getAttribute(match[1]);
            if (value === null) return false;
            if (match[2] === "=" && value !== match[3]) return false;
            if (match[2] === "*=" && !value.includes(match[3])) return false;
        }
        return true;
    }
    function matches(node, selector) {
        const parts = selector.trim().split(/\s+/);
        if (!matchesSimple(node, parts.pop())) return false;
        let ancestor = node.parentNode;
        while (parts.length) {
            const part = parts.pop();
            while (ancestor && !matchesSimple(ancestor, part)) ancestor = ancestor.parentNode;
            if (!ancestor) return false;
            ancestor = ancestor.parentNode;
        }
        return true;
    }
    class Element {
        constructor(tag) {
            this.tagName = tag.toUpperCase();
            this.nodeType = 1;
            this.children = [];
            this.attributes = new Map();
            this.dataset = {};
            this.style = {};
            this.listeners = {};
            this.className = "";
            this.hidden = false;
            this.textContent = "";
            this.classList = {
                toggle: (value, enabled) => {
                    const names = new Set(this.className.split(/\s+/).filter(Boolean));
                    if (enabled === undefined) enabled = !names.has(value);
                    if (enabled) names.add(value); else names.delete(value);
                    this.className = [...names].join(" ");
                },
                add: (...values) => values.forEach(value => this.classList.toggle(value, true)),
                remove: (...values) => values.forEach(value => this.classList.toggle(value, false)),
                contains: value => this.className.split(/\s+/).includes(value),
            };
        }
        setAttribute(name, value) {
            value = String(value);
            this.attributes.set(name, value);
            if (name.startsWith("data-")) this.dataset[dataKey(name)] = value;
            if (name === "class") this.className = value;
            if (name === "src" && this.tagName === "IMG") requests.push({ image: this, value, attached: document.contains(this) });
        }
        getAttribute(name) {
            if (name.startsWith("data-")) return this.dataset[dataKey(name)] ?? null;
            return this.attributes.get(name) ?? null;
        }
        removeAttribute(name) { this.attributes.delete(name); }
        set src(value) { this.setAttribute("src", value); }
        get src() { return this.getAttribute("src"); }
        set href(value) { this.setAttribute("href", value); }
        get href() { return this.getAttribute("href"); }
        get protocol() { return new URL(this.href).protocol; }
        get nextSibling() { return this.parentNode.children[this.parentNode.children.indexOf(this) + 1] || null; }
        appendChild(node) { node.parentNode = this; this.children.push(node); return node; }
        prepend(node) { node.parentNode = this; this.children.unshift(node); }
        insertBefore(node, reference) {
            if (!reference) return this.appendChild(node);
            node.parentNode = this;
            this.children.splice(this.children.indexOf(reference), 0, node);
        }
        insertAdjacentElement(position, node) {
            assert.equal(position, "afterend");
            this.parentNode.insertBefore(node, this.nextSibling);
        }
        replaceChild(node, previous) {
            const index = this.children.indexOf(previous);
            assert.ok(index >= 0);
            node.parentNode = this;
            this.children[index] = node;
            previous.parentNode = null;
        }
        removeChild(node) { this.children.splice(this.children.indexOf(node), 1); node.parentNode = null; }
        remove() { this.parentNode?.removeChild(this); }
        contains(node) { return this === node || this.children.some(child => child.contains(node)); }
        querySelectorAll(selector) {
            const found = [];
            for (const child of this.children) {
                if (selector.split(",").some(part => matches(child, part))) found.push(child);
                found.push(...child.querySelectorAll(selector));
            }
            return found;
        }
        querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
        closest(selector) { return matches(this, selector) ? this : this.parentNode?.closest(selector) || null; }
        addEventListener(type, callback) { (this.listeners[type] ||= []).push(callback); }
        dispatchEvent(event) {
            event.target ||= this;
            for (const callback of this.listeners[event.type] || []) callback.call(this, event);
        }
        click() { this.dispatchEvent({ type: "click" }); }
        focus() { document.activeElement = this; }
    }
    document = new Element("document");
    document.documentElement = document.appendChild(new Element("html"));
    document.body = document.documentElement.appendChild(new Element("body"));
    document.readyState = loading ? "loading" : "complete";
    document.visibilityState = "visible";
    document.createElement = tag => new Element(tag);
    document.getElementById = id => {
        const search = node => node.getAttribute("id") === id ? node : node.children.map(search).find(Boolean);
        return search(document) || null;
    };
    function add(parent, tag, className = "") {
        const element = new Element(tag);
        element.className = className;
        return parent.appendChild(element);
    }
    const controls = add(document.body, "div", "body-media-controls");
    controls.hidden = true;
    const buttons = {};
    for (const group of ["dccon", "image"]) {
        const button = add(controls, "button", "body-media-toggle");
        button.type = "button";
        button.hidden = true;
        button.setAttribute("data-body-media-group", group);
        button.setAttribute("aria-controls", "article-body");
        buttons[group] = button;
    }
    const article = add(document.body, "article", "article-body");
    article.setAttribute("id", "article-body");
    const dccons = [];
    const images = [];
    function deferred(parent, className, attribute, url) {
        const image = add(parent, "img", className);
        image.hidden = true;
        image.setAttribute(attribute, url);
        return image;
    }
    if (!empty) {
        for (let i = 0; i < 2; i++) {
            dccons.push(deferred(article, "dccon", "data-dccon-src", "/media?dccon=repeat"));
            images.push(deferred(article, "body-image", "data-body-image-src", "/media?image=repeat"));
        }
        const serverCard = add(article, "a", "link-preview has-media");
        serverCard.href = "https://example.com/server";
        images.push(deferred(add(serverCard, "span", "link-preview-media"), "link-preview-image", "data-preview-image-src", PREVIEW_URL));
    }
    const link = add(article, "a", "link-preview-target");
    link.href = "https://example.com/delayed";
    const video = add(article, "video");
    video.src = "/movie?no=1";
    const iframe = add(article, "iframe");
    iframe.src = "https://www.youtube.com/embed/test";
    const commentShell = add(document.body, "section", "comment-shell");
    add(commentShell, "h2");
    const commentItem = add(commentShell, "li", "comment-item comment-spam-hidden");
    const comment = deferred(add(commentItem, "div", "comment-main"), "dccon", "data-dccon-src", "/media?dccon=repeat");
    const control = add(document.body, "div", "media-block-control");
    add(control, "button", "dccon-toggle");
    const menu = add(control, "div", "media-block-menu");
    for (const value of ["none", "dccon", "body", "all"]) {
        add(menu, "button", "media-block-option").setAttribute("data-media-block-mode", value);
    }
    const window = new Element("window");
    window.location = new URL("https://mirror.example/read?board=test&pid=1");
    window.localStorage = {
        getItem(key) { if (failures.read) throw Error("read denied"); return storage.get(key) ?? null; },
        setItem(key, value) { if (failures.write) throw Error("write denied"); writes.push(key); storage.set(key, value); },
        removeItem(key) { if (failures.write) throw Error("write denied"); storage.delete(key); },
    };
    window.fetch = url => new Promise(resolve => pending.push({ url, resolve }));
    const context = vm.createContext({
        document, window, URL, URLSearchParams, fetch: window.fetch,
        CustomEvent: function (type, init) { this.type = type; this.detail = init.detail; },
    });
    function runRead() { vm.runInContext(readSource, context); }
    if (boot) runRead();
    return {
        document, window, article, buttons, controls, dccons, images, comment, commentItem,
        storage, requests, writes, failures, link, video, iframe, runRead,
        previews() { return article.querySelectorAll("img.link-preview-image"); },
        runPreview() { vm.runInContext(previewSource, context); },
        resolvePreview(imageUrl = PREVIEW_URL) {
            assert.equal(pending.length, 1);
            assert.ok(pending[0].url.startsWith("/embed/link-preview?url="));
            pending.shift().resolve({ ok: true, json: async () => ({ ok: true, title: "Preview", image_url: imageUrl }) });
        },
        select(mode) { menu.dispatchEvent({ type: "click", target: menu.querySelector('[data-media-block-mode="' + mode + '"]') }); },
        restore() { window.dispatchEvent({ type: "pageshow", persisted: true }); },
        visible() { document.dispatchEvent({ type: "visibilitychange" }); },
        external(key, value) {
            if (key === null) storage.clear();
            else if (value === null) storage.delete(key);
            else storage.set(key, value);
            window.dispatchEvent({ type: "storage", key });
        },
        finishBoot() { document.readyState = "complete"; document.dispatchEvent({ type: "DOMContentLoaded" }); },
    };
}

function assertBlocked(images, blocked) {
    for (const image of images) {
        assert.equal(image.hidden, blocked);
        assert.equal(image.getAttribute("src") === null, blocked);
    }
}
const settle = () => new Promise(resolve => setImmediate(resolve));

async function main() {
    for (const mode of ["none", "dccon", "body", "all"]) {
        const h = createHarness({ mode });
        const dcconBlocked = mode !== "none";
        const imageBlocked = mode === "body" || mode === "all";
        const commentBlocked = mode === "dccon" || mode === "all";
        assertBlocked(h.dccons, dcconBlocked);
        assertBlocked(h.images, imageBlocked);
        assertBlocked([h.comment], commentBlocked);
        const blockedImages = [
            ...(dcconBlocked ? h.dccons : []),
            ...(imageBlocked ? h.images : []),
            ...(commentBlocked ? [h.comment] : []),
        ];
        assert.ok(h.requests.every(request => !blockedImages.includes(request.image)), "blocked images must never have received src");
        const positions = [...h.dccons, ...h.images].map(image => [image, image.parentNode, image.parentNode.children.indexOf(image)]);
        assert.equal(h.buttons.dccon.hidden, !dcconBlocked);
        assert.equal(h.buttons.image.hidden, !imageBlocked);
        assert.equal(h.controls.hidden, mode === "none");
        assert.equal(h.buttons.dccon.textContent, "차단된 이모티콘 보기 (2)");
        assert.equal(h.buttons.image.textContent, "차단된 이미지 보기 (3)");
        assert.equal(h.article.dataset.bodyImagesBlocked, String(imageBlocked));
        assert.equal(h.buttons.image.getAttribute("aria-controls"), h.article.getAttribute("id"));
        assert.ok(h.commentItem.classList.contains("comment-spam-hidden"), "spam fold must survive media changes");
        const writes = h.writes.length;
        if (dcconBlocked) {
            h.buttons.dccon.click();
            assertBlocked(h.dccons, false);
            assertBlocked(h.images, imageBlocked);
            assertBlocked([h.comment], commentBlocked);
            assert.equal(h.buttons.dccon.textContent, "차단된 이모티콘 숨기기");
            assert.equal(h.buttons.dccon.getAttribute("aria-expanded"), "true");
        }
        if (imageBlocked) {
            h.buttons.image.click();
            assertBlocked(h.images, false);
            assert.equal(h.article.dataset.bodyImagesBlocked, "false");
            assert.equal(h.buttons.image.textContent, "차단된 이미지 숨기기");
            h.buttons.image.click();
            assertBlocked(h.images, true);
            assertBlocked(h.dccons, false);
        }
        if (commentBlocked) {
            const button = h.document.querySelector(".comment-dccon-block-toggle");
            assert.equal(button.textContent, "차단된 이모티콘 보기 (1)");
            button.click();
            assertBlocked([h.comment], false);
            assertBlocked(h.images, imageBlocked);
            assertBlocked(h.dccons, false);
            assert.ok(h.commentItem.classList.contains("comment-spam-hidden"));
        }
        assert.equal(h.writes.length, writes, "temporary reveals must not write storage");
        for (const [image, parent, index] of positions) {
            assert.equal(parent.children[index], image, "reveals must keep each original image in place");
        }
        assert.equal(h.video.src, "/movie?no=1");
        assert.equal(h.iframe.src, "https://www.youtube.com/embed/test");
        assert.equal(h.link.href, "https://example.com/delayed");
    }

    const empty = createHarness({ mode: "all", empty: true });
    assert.equal(empty.controls.hidden, true);
    assert.equal(empty.buttons.dccon.hidden, true);
    assert.equal(empty.buttons.image.hidden, true);
    empty.runPreview();
    empty.resolvePreview();
    await settle();
    assert.equal(empty.buttons.image.hidden, false);
    assert.equal(empty.buttons.image.textContent, "차단된 이미지 보기 (1)");
    assert.equal(empty.requests.length, 0, "blocked delayed cards must never receive src");
    empty.buttons.image.click();
    assertBlocked(empty.previews(), false);
    assert.equal(empty.requests.length, 1);
    assert.ok(empty.requests[0].attached, "read_state must hydrate only after card insertion");
    empty.previews()[0].dispatchEvent({ type: "error" });
    empty.buttons.image.click();
    assertBlocked(empty.previews(), true);
    empty.buttons.image.click();
    assertBlocked(empty.previews(), false);

    const pendingChange = createHarness({ mode: "none", empty: true });
    pendingChange.runPreview();
    pendingChange.select("all");
    const before = pendingChange.requests.length;
    pendingChange.resolvePreview();
    await settle();
    assert.equal(pendingChange.requests.length, before, "response must use mode at completion");
    assertBlocked(pendingChange.previews(), true);

    const revealed = createHarness({ mode: "all" });
    revealed.buttons.image.click();
    revealed.runPreview();
    revealed.resolvePreview();
    await settle();
    assertBlocked(revealed.previews(), false);
    assert.ok(revealed.requests.every(request => request.attached), "link_preview must never eagerly assign src");
    revealed.buttons.image.click();
    assert.equal(revealed.buttons.image.textContent, "차단된 이미지 보기 (4)");

    const changedReveal = createHarness({ mode: "all" });
    changedReveal.buttons.image.click();
    changedReveal.runPreview();
    changedReveal.select("body");
    changedReveal.resolvePreview();
    await settle();
    assertBlocked(changedReveal.previews(), true);

    for (const beforeReadScript of [true, false]) {
        const early = createHarness({ mode: "all", empty: true, boot: !beforeReadScript, loading: true });
        early.runPreview();
        early.resolvePreview();
        await settle();
        assert.equal(early.requests.length, 0, "cards arriving before boot must stay deferred");
        if (beforeReadScript) early.runRead();
        early.finishBoot();
        assert.equal(early.buttons.image.textContent, "차단된 이미지 보기 (1)");
        assertBlocked(early.previews(), true);
        early.select("none");
        assertBlocked(early.previews(), false);
    }

    const allowed = createHarness({ empty: true });
    allowed.runPreview();
    allowed.resolvePreview();
    await settle();
    assertBlocked(allowed.previews(), false);
    assert.ok(allowed.requests.every(request => request.attached));
    const unsafe = createHarness({ empty: true });
    unsafe.runPreview();
    unsafe.resolvePreview("https://unsafe.example/image.jpg");
    await settle();
    assert.equal(unsafe.previews().length, 0);

    const restored = createHarness({ mode: "all" });
    restored.buttons.dccon.click();
    restored.buttons.image.click();
    restored.select("all");
    restored.restore();
    restored.visible();
    restored.external(MEDIA_KEY, "all");
    restored.external(LEGACY_KEY, "1");
    assertBlocked([...restored.dccons, ...restored.images], false);
    assert.equal(restored.buttons.image.getAttribute("aria-expanded"), "true");
    restored.storage.set(MEDIA_KEY, "body");
    restored.restore();
    assertBlocked([...restored.dccons, ...restored.images], true);
    restored.buttons.image.click();
    restored.storage.set(MEDIA_KEY, "all");
    restored.visible();
    assertBlocked(restored.images, true);
    restored.external(MEDIA_KEY, null); // legacy dccon mode
    assertBlocked(restored.dccons, true);
    assertBlocked(restored.images, false);
    restored.external(LEGACY_KEY, null);
    assertBlocked([...restored.dccons, ...restored.images, restored.comment], false);
    restored.external(LEGACY_KEY, "1");
    assertBlocked(restored.dccons, true);
    restored.external(null, null);
    assertBlocked([...restored.dccons, restored.comment], false);
    assert.equal(restored.controls.hidden, true);
    assertBlocked(createHarness({ mode: "all" }).images, true, "fresh documents have no temporary override");

    const errors = createHarness({ mode: "all" });
    errors.buttons.image.click();
    errors.failures.read = true;
    errors.restore();
    errors.visible();
    errors.external(MEDIA_KEY, "body");
    assert.equal(errors.document.documentElement.dataset.mediaBlockMode, "all");
    assertBlocked(errors.images, false);
    errors.failures.read = false;
    errors.failures.write = true;
    errors.select("dccon");
    errors.buttons.dccon.click();
    errors.restore();
    errors.visible();
    assert.equal(errors.document.documentElement.dataset.mediaBlockMode, "dccon", "failed writes must not restore stale stored mode");
    assertBlocked(errors.dccons, false);
    errors.external(MEDIA_KEY, "all");
    assertBlocked([...errors.dccons, ...errors.images], true);
    const inaccessible = createHarness({ readError: true });
    inaccessible.failures.write = true;
    inaccessible.select("all");
    inaccessible.restore();
    assertBlocked([...inaccessible.dccons, ...inaccessible.images], true);

    process.stdout.write("media_block_reveal_state_machine=passed\n");
}

main().catch(error => {
    process.stderr.write(`${error.stack || error}\n`);
    process.exitCode = 1;
});
