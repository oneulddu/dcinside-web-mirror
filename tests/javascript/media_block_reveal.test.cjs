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
function createHarness({
    mode = "none", empty = false, boot = true, loading = false, readError = false,
    imageCount = 2, dcconCount = 2, serverPreview = true,
} = {}) {
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
        set id(value) { this.setAttribute("id", value); }
        get id() { return this.getAttribute("id") || ""; }
        set type(value) { this.setAttribute("type", value); }
        get type() { return this.getAttribute("type") || ""; }
        set src(value) { this.setAttribute("src", value); }
        get src() { return this.getAttribute("src"); }
        set href(value) { this.setAttribute("href", value); }
        get href() { return this.getAttribute("href"); }
        get protocol() { return new URL(this.href).protocol; }
        get nextSibling() { return this.parentNode.children[this.parentNode.children.indexOf(this) + 1] || null; }
        appendChild(node) { node.remove(); node.parentNode = this; this.children.push(node); return node; }
        prepend(node) { node.remove(); node.parentNode = this; this.children.unshift(node); }
        insertBefore(node, reference) {
            if (!reference) return this.appendChild(node);
            assert.ok(this.children.includes(reference), "insertBefore reference must belong to parent");
            if (node === reference) return node;
            node.remove();
            node.parentNode = this;
            this.children.splice(this.children.indexOf(reference), 0, node);
            return node;
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
            event.currentTarget = this;
            event.defaultPrevented ??= false;
            event.cancelBubble ??= false;
            event.preventDefault ||= function () { if (this.cancelable) this.defaultPrevented = true; };
            event.stopPropagation ||= function () { this.cancelBubble = true; };
            for (const callback of this.listeners[event.type] || []) callback.call(this, event);
            if (event.bubbles && !event.cancelBubble && this.parentNode) this.parentNode.dispatchEvent(event);
            return !event.defaultPrevented;
        }
        click() {
            const event = { type: "click", bubbles: true, cancelable: true };
            this.dispatchEvent(event);
            return event;
        }
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
        for (let i = 0; i < Math.max(dcconCount, imageCount); i++) {
            if (i < dcconCount) {
                dccons.push(deferred(article, "dccon", "data-dccon-src", "/media?dccon=repeat"));
            }
            if (i < imageCount) {
                images.push(deferred(article, "body-image", "data-body-image-src", "/media?image=repeat"));
            }
        }
        if (serverPreview) {
            const serverCard = add(article, "a", "link-preview has-media");
            serverCard.href = "https://example.com/server";
            images.push(deferred(add(serverCard, "span", "link-preview-media"), "link-preview-image", "data-preview-image-src", PREVIEW_URL));
        }
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
    const originalParents = new Map([...dccons, ...images].map(image => [image, image.parentNode]));
    const originalOrder = article.querySelectorAll("img");
    if (boot) runRead();
    return {
        document, window, article, buttons, controls, dccons, images, comment, commentItem,
        storage, requests, writes, failures, link, video, iframe, runRead,
        assertImagePositions() {
            for (const [image, parent] of originalParents) {
                assert.equal(image.parentNode, parent, "toggles must preserve each image's original parent");
            }
            assert.deepEqual(article.querySelectorAll("img").filter(image => originalParents.has(image)), originalOrder,
                "inserted buttons must not change original image order");
        },
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
        if (image.classList.contains("link-preview-image")) {
            assert.equal(image.closest(".link-preview").dataset.previewImageBlocked, String(blocked),
                "preview card state must follow its own image");
        }
    }
}

function itemButton(h, image) {
    const id = image.getAttribute("id");
    assert.ok(id, "controlled images need an ID");
    assert.equal(h.document.getElementById(id), image, "aria-controls must resolve to the actual image");
    const buttons = h.article.querySelectorAll("button.body-media-item-toggle")
        .filter(button => button.getAttribute("aria-controls") === id);
    assert.equal(buttons.length, 1, "each image must have exactly one native toggle button");
    const button = buttons[0];
    assert.equal(button.type, "button");
    const kind = image.classList.contains("dccon") ? "dccon" : "image";
    assert.equal(button.getAttribute("data-body-media-kind"), kind);
    const reference = image.closest("a") || image;
    assert.equal(button.parentNode, reference.parentNode);
    assert.equal(button.nextSibling, reference, "toggle must immediately precede the closest anchor or image");
    assert.equal(button.closest("a"), null, "image toggles must stay outside links");
    return button;
}

function assertItem(h, image, revealed, visible = true) {
    const button = itemButton(h, image);
    const label = image.classList.contains("dccon") ? "이모티콘" : "이미지";
    assert.equal(button.hidden, !visible);
    if (visible) {
        assert.equal(button.getAttribute("aria-expanded"), String(revealed));
        assert.equal(button.textContent, label + (revealed ? " 숨기기" : " 보기"));
    }
    assertBlocked([image], !revealed);
    return button;
}
const settle = () => new Promise(resolve => setImmediate(resolve));

async function main() {
    const theme = createHarness();
    const themeKey = "mirror_theme_v1";
    const toggle = theme.document.createElement("button");
    toggle.className = "theme-toggle";
    theme.document.body.appendChild(toggle);
    function assertTheme(value) {
        assert.equal(theme.document.documentElement.dataset.theme, value);
        assert.equal(theme.document.documentElement.style.colorScheme, value);
        assert.equal(theme.document.body.dataset.theme, value);
        assert.equal(theme.document.body.classList.contains("theme-light"), value === "light");
        assert.equal(theme.document.body.classList.contains("theme-dark"), value === "dark");
        assert.equal(toggle.getAttribute("aria-label"), value === "light" ? "어두운 테마로 전환" : "밝은 테마로 전환");
    }
    theme.storage.set(themeKey, "light");
    theme.restore();
    assertTheme("light");
    theme.external(themeKey, "dark");
    assertTheme("dark");
    theme.external(themeKey, "light");
    assertTheme("light");
    theme.external(null, null);
    assertTheme("dark");
    theme.storage.set(themeKey, "light");
    theme.external("unrelated", "value");
    assertTheme("dark");
    assert.equal(theme.writes.includes(themeKey), false, "theme sync must not write back to storage");

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
        h.assertImagePositions();
        assert.equal(h.buttons.dccon.hidden, !dcconBlocked);
        assert.equal(h.buttons.image.hidden, !imageBlocked);
        assert.equal(h.controls.hidden, mode === "none");
        if (dcconBlocked) assert.equal(h.buttons.dccon.textContent, "이모티콘 전체 보기 (2)");
        if (imageBlocked) assert.equal(h.buttons.image.textContent, "이미지 전체 보기 (3)");
        assert.equal(h.article.dataset.bodyImagesBlocked, String(imageBlocked));
        assert.equal(h.buttons.image.getAttribute("aria-controls"), h.article.getAttribute("id"));
        assert.ok(h.commentItem.classList.contains("comment-spam-hidden"), "spam fold must survive media changes");
        const writes = h.writes.length;
        if (dcconBlocked) {
            h.buttons.dccon.click();
            assertBlocked(h.dccons, false);
            assertBlocked(h.images, imageBlocked);
            assertBlocked([h.comment], commentBlocked);
            assert.equal(h.buttons.dccon.textContent, "이모티콘 전체 숨기기");
            assert.equal(h.buttons.dccon.getAttribute("aria-expanded"), "true");
        }
        if (imageBlocked) {
            h.buttons.image.click();
            assertBlocked(h.images, false);
            assert.equal(h.article.dataset.bodyImagesBlocked, "false");
            assert.equal(h.buttons.image.textContent, "이미지 전체 숨기기");
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
        h.assertImagePositions();
        assert.equal(h.video.src, "/movie?no=1");
        assert.equal(h.iframe.src, "https://www.youtube.com/embed/test");
        assert.equal(h.link.href, "https://example.com/delayed");
    }

    for (const kind of ["image", "dccon"]) {
        const label = kind === "image" ? "이미지" : "이모티콘";
        for (const count of [1, 2]) {
            const h = createHarness({
                mode: "all", serverPreview: false,
                imageCount: kind === "image" ? count : 0,
                dcconCount: kind === "dccon" ? count : 0,
            });
            const images = kind === "image" ? h.images : h.dccons;
            assert.equal(h.buttons[kind].hidden, count < 2);
            assert.equal(h.controls.hidden, count < 2);
            images.forEach(image => assertItem(h, image, false));
            assert.equal(new Set(images.map(image => image.id)).size, count, "image IDs must be unique");
            if (count === 2) assert.equal(h.buttons[kind].textContent, label + " 전체 보기 (2)");
            itemButton(h, images[0]).click();
            assertItem(h, images[0], true);
            assertBlocked(images.slice(1), true);
            assert.equal(h.requests.length, 1, "one click must assign src only to the selected image");
            assert.equal(h.requests[0].image, images[0]);
            assert.equal(h.requests[0].attached, true);
            h.assertImagePositions();
        }

        const h = createHarness({ mode: "all" });
        const images = kind === "image" ? h.images : h.dccons;
        const other = kind === "image" ? h.dccons : h.images;
        const group = h.buttons[kind];
        const first = assertItem(h, images[0], false);
        const second = assertItem(h, images[1], false);
        const sourceAttribute = kind === "image" ? "data-body-image-src" : "data-dccon-src";
        assert.equal(images[0].getAttribute(sourceAttribute), images[1].getAttribute(sourceAttribute));
        assert.notEqual(first.getAttribute("aria-controls"), second.getAttribute("aria-controls"));
        const writes = h.writes.length;
        first.click();
        assertItem(h, images[0], true);
        assertItem(h, images[1], false);
        assertBlocked(other, true);
        assertBlocked([h.comment], true);
        assert.equal(h.requests.length, 1, "same URL must not share individual reveal state");
        assert.equal(group.getAttribute("aria-expanded"), "false");
        assert.equal(group.textContent, label + " 전체 보기 (" + images.length + ")");

        group.click(); // A partially revealed group must show every image.
        images.forEach(image => assertItem(h, image, true));
        assert.equal(group.getAttribute("aria-expanded"), "true");
        assert.equal(group.textContent, label + " 전체 숨기기");
        first.click(); // An individual hide overrides the group's revealed default.
        assertItem(h, images[0], false);
        images.slice(1).forEach(image => assertItem(h, image, true));
        assert.equal(group.getAttribute("aria-expanded"), "false");
        assert.equal(group.textContent, label + " 전체 보기 (" + images.length + ")");
        group.click(); // Clears the explicit hide even though group default was already show.
        images.forEach(image => assertItem(h, image, true));
        group.click(); // Clears the earlier explicit reveal as well.
        images.forEach(image => assertItem(h, image, false));
        assert.equal(group.getAttribute("aria-expanded"), "false");
        assertBlocked(other, true);
        assertBlocked([h.comment], true);
        assert.equal(h.writes.length, writes, "individual and group actions are temporary");
        h.assertImagePositions();
    }

    const linked = createHarness({ mode: "all" });
    const preview = linked.previews()[0];
    const card = preview.closest("a");
    const linkedButton = assertItem(linked, preview, false);
    const ids = [...linked.dccons, ...linked.images].map(image => itemButton(linked, image).getAttribute("aria-controls"));
    assert.equal(new Set(ids).size, ids.length, "IDs must be unique across both media kinds");
    let anchorClicks = 0;
    let articleClicks = 0;
    card.addEventListener("click", () => { anchorClicks += 1; });
    linked.article.addEventListener("click", () => { articleClicks += 1; });
    for (const revealed of [true, false]) {
        const event = linkedButton.click();
        assert.equal(event.defaultPrevented, true, "toggle click must cancel default link navigation");
        assert.equal(event.cancelBubble, true, "toggle click must stop propagation");
        assert.equal(anchorClicks, 0);
        assert.equal(articleClicks, 0);
        assertItem(linked, preview, revealed);
        assertBlocked(linked.images.filter(image => image !== preview), true);
    }
    card.click();
    assert.equal(anchorClicks, 1, "the original preview link must remain clickable");
    assert.equal(articleClicks, 1, "the fixture must bubble ordinary link clicks");
    linked.assertImagePositions();

    // All individually revealed images count as expanded, but do not change
    // the default that a later preview inherits.
    const individuallyRevealed = createHarness({ mode: "all", imageCount: 1, dcconCount: 0, serverPreview: false });
    assertItem(individuallyRevealed, individuallyRevealed.images[0], false).click();
    individuallyRevealed.runPreview();
    const singleRequests = individuallyRevealed.requests.length;
    individuallyRevealed.resolvePreview();
    await settle();
    assert.equal(individuallyRevealed.requests.length, singleRequests, "an individual reveal must not hydrate a delayed image");
    assertItem(individuallyRevealed, individuallyRevealed.images[0], true);
    assertItem(individuallyRevealed, individuallyRevealed.previews()[0], false);
    assert.equal(individuallyRevealed.buttons.image.hidden, false, "the second image enables the group action");
    assert.equal(individuallyRevealed.buttons.image.textContent, "이미지 전체 보기 (2)");
    assert.equal(individuallyRevealed.buttons.image.getAttribute("aria-expanded"), "false");
    itemButton(individuallyRevealed, individuallyRevealed.previews()[0]).click();
    assert.equal(individuallyRevealed.buttons.image.getAttribute("aria-expanded"), "true");
    assert.equal(individuallyRevealed.buttons.image.textContent, "이미지 전체 숨기기");
    individuallyRevealed.buttons.image.click();
    assertBlocked([...individuallyRevealed.images, ...individuallyRevealed.previews()], true);

    const defaultShown = createHarness({ mode: "all" });
    defaultShown.buttons.image.click();
    itemButton(defaultShown, defaultShown.images[0]).click();
    defaultShown.runPreview();
    const globalRequests = defaultShown.requests.length;
    defaultShown.resolvePreview();
    await settle();
    assert.equal(defaultShown.requests.length, globalRequests + 1);
    assertItem(defaultShown, defaultShown.images[0], false);
    assertItem(defaultShown, defaultShown.previews().at(-1), true);
    assert.equal(defaultShown.buttons.image.getAttribute("aria-expanded"), "false");
    assert.equal(defaultShown.buttons.image.textContent, "이미지 전체 보기 (4)");
    defaultShown.buttons.image.click();
    assertBlocked([...defaultShown.images, ...defaultShown.previews()], false);

    const individualModes = createHarness({ mode: "all" });
    individualModes.buttons.image.click();
    itemButton(individualModes, individualModes.images[0]).click();
    itemButton(individualModes, individualModes.dccons[0]).click();
    individualModes.select("all");
    individualModes.restore();
    individualModes.visible();
    individualModes.external(MEDIA_KEY, "all");
    individualModes.external(LEGACY_KEY, "1");
    assertItem(individualModes, individualModes.images[0], false);
    assertBlocked(individualModes.images.slice(1), false);
    assertItem(individualModes, individualModes.dccons[0], true);
    assertItem(individualModes, individualModes.dccons[1], false);
    individualModes.select("body");
    [...individualModes.images, ...individualModes.dccons].forEach(image => assertItem(individualModes, image, false));
    itemButton(individualModes, individualModes.images[0]).click();
    individualModes.select("none");
    [...individualModes.images, ...individualModes.dccons].forEach(image => assertItem(individualModes, image, true, false));
    individualModes.select("dccon");
    individualModes.images.forEach(image => assertItem(individualModes, image, true, false));
    individualModes.dccons.forEach(image => assertItem(individualModes, image, false));
    individualModes.select("all");
    [...individualModes.images, ...individualModes.dccons].forEach(image => assertItem(individualModes, image, false));
    individualModes.assertImagePositions();

    const empty = createHarness({ mode: "all", empty: true });
    assert.equal(empty.controls.hidden, true);
    assert.equal(empty.buttons.dccon.hidden, true);
    assert.equal(empty.buttons.image.hidden, true);
    empty.runPreview();
    empty.resolvePreview();
    await settle();
    assert.equal(empty.controls.hidden, true);
    assert.equal(empty.buttons.image.hidden, true);
    assert.equal(empty.requests.length, 0, "blocked delayed cards must never receive src");
    const delayedButton = assertItem(empty, empty.previews()[0], false);
    delayedButton.click();
    assertBlocked(empty.previews(), false);
    assert.equal(empty.requests.length, 1);
    assert.ok(empty.requests[0].attached, "read_state must hydrate only after card insertion");
    empty.previews()[0].dispatchEvent({ type: "error" });
    delayedButton.click();
    assertBlocked(empty.previews(), true);
    delayedButton.click();
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
    assert.equal(revealed.buttons.image.textContent, "이미지 전체 보기 (4)");

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
        assert.equal(early.buttons.image.hidden, true);
        assertItem(early, early.previews()[0], false);
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
