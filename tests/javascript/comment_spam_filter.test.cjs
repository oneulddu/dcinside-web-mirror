const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");
const source = fs.readFileSync(path.resolve(__dirname, "../../app/static/javascript/comment_spam_filter.js"), "utf8");

function hiddenCount(texts) {
    const comments = texts.map(text => ({
        hidden: false,
        querySelector() { return { querySelector: selector => selector === "p" ? { textContent: text } : null }; },
        classList: { add() { comments.find(comment => comment.classList === this).hidden = true; } },
    }));
    const list = { querySelectorAll: () => comments };
    const shell = { querySelector: () => null, prepend() {} };
    vm.runInNewContext(source, { document: {
        readyState: "complete",
        querySelector: selector => selector === ".comment-list" ? list : shell,
        createElement: () => ({ setAttribute() {}, addEventListener() {} }),
        addEventListener() {},
    } });
    return comments.filter(comment => comment.hidden).length;
}

// 클래스·속성·이벤트만 흉내 내는 작은 DOM. 포커고수 댓글 재계산을 확인한다.
function pokerHarness() {
    const listeners = {};
    const items = [];
    const shellChildren = [];
    function classList() {
        const set = new Set();
        return {
            add: (...names) => names.forEach(name => set.add(name)),
            remove: (...names) => names.forEach(name => set.delete(name)),
            toggle: (name, force) => (force ? set.add(name) : set.delete(name)),
            contains: name => set.has(name),
        };
    }
    function comment(text, withImage) {
        // 이미지 댓글은 차단 버튼 글자("이미지 보기")가 본문 안에 섞여 있다.
        const buttonText = withImage ? "이미지 보기" : "";
        const body = {
            textContent: text + buttonText,
            querySelector: () => null,
            cloneNode: () => {
                const copy = { textContent: text + buttonText };
                copy.querySelectorAll = selector => (selector === "button" && withImage
                    ? [{ remove: () => { copy.textContent = text; } }] : []);
                return copy;
            },
        };
        const main = {
            querySelector: selector => {
                if (selector === ".poker-comment-body") return body;
                return null;
            },
        };
        return { classList: classList(), querySelector: selector => (selector === ".comment-main" ? main : null) };
    }
    const list = { querySelectorAll: () => items.slice() };
    const title = {
        insertAdjacentElement(position, element) {
            shellChildren.push(element);
            element.remove = () => shellChildren.splice(shellChildren.indexOf(element), 1);
        },
    };
    const shell = { querySelector: selector => (selector === "h2" ? title : null), prepend() {} };
    const document = {
        readyState: "complete",
        querySelector: selector => (selector === ".comment-list" ? list : selector === ".comment-shell" ? shell : null),
        createElement: () => {
            const attrs = {};
            const handlers = [];
            return {
                attrs, textContent: "",
                setAttribute: (name, value) => { attrs[name] = value; },
                addEventListener: (type, handler) => handlers.push(handler),
                click: () => handlers.forEach(handler => handler()),
            };
        },
        addEventListener: (type, handler) => { (listeners[type] = listeners[type] || []).push(handler); },
    };
    return {
        items, shellChildren,
        add(texts, withImage = false) { texts.forEach(text => items.unshift(comment(text, withImage))); },
        run() { vm.runInNewContext(source, { document }); },
        fire() { (listeners["poker:comments-added"] || []).forEach(handler => handler()); },
        hidden() { return items.filter(item => item.classList.contains("comment-spam-hidden")).length; },
    };
}

test("Poker comments are re-filtered after older pages are prepended with one toggle", () => {
    const page = pokerHarness();
    page.add(["긴 댓글 반복 내용입니다", "긴 댓글 반복 내용입니다", "정상 댓글"]);
    page.run();
    assert.equal(page.hidden(), 0);
    assert.equal(page.shellChildren.length, 0);

    page.add(["긴 댓글 반복 내용입니다"]);
    page.fire();
    assert.equal(page.hidden(), 3);
    assert.equal(page.shellChildren.length, 1);
    const button = page.shellChildren[0];
    assert.equal(button.textContent, "접힌 댓글 보기 (3)");

    button.click();
    assert.equal(button.attrs["aria-expanded"], "true");
    page.add(["긴 댓글 반복 내용입니다"]);
    page.fire();
    assert.equal(page.shellChildren.length, 1);
    assert.equal(page.hidden(), 0, "expanded state survives a re-run");
    assert.equal(page.items.filter(item => item.classList.contains("comment-spam-highlight")).length, 4);
    assert.equal(button.textContent, "접힌 댓글 숨기기");
});

test("Poker image-only comments stay visible but repeated text with images still folds", () => {
    const imageOnly = pokerHarness();
    imageOnly.add(Array(12).fill(""), true);
    imageOnly.run();
    assert.equal(imageOnly.hidden(), 0);

    const repeated = pokerHarness();
    repeated.add(Array(3).fill("긴 댓글 반복 내용입니다"), true);
    repeated.run();
    assert.equal(repeated.hidden(), 3, "image toggle text is ignored when comparing");
});

for (const suffixes of [
    ["こんにちは", "さようなら", "ありがとう"],
    ["中文", "你好", "再见"],
    ["Привет", "Спасибо", "Пока"],
    ["١", "٢", "٣"],
]) {
    test(`distinct Unicode comments stay visible: ${suffixes[0]}`, () => {
        assert.equal(hiddenCount(suffixes.map(suffix => `이 댓글의 공통 한국어 머리말 ${suffix}`)), 0);
    });
}

test("Arabic comments differing only by combining marks stay visible", () => {
    assert.equal(hiddenCount(["هذا الرجل عَلِمَ", "هذا الرجل عُلِمَ", "هذا الرجل عَلَّمَ"]), 0);
});

test("existing Korean and ASCII repeat thresholds are preserved", () => {
    for (const [text, threshold] of [["ㅋㅋ", 12], ["굿", 12], ["abc_1", 7], ["긴 댓글 반복 내용입니다", 3]]) {
        assert.equal(hiddenCount(Array(threshold - 1).fill(text)), 0);
        assert.equal(hiddenCount(Array(threshold).fill(text)), threshold);
    }
    assert.equal(hiddenCount(["반복되는 댓글입니다!", "반복되는  댓글입니다!", "반복되는 댓글입니다!@"]), 3);
    assert.equal(hiddenCount(Array(3).fill("本当に同じ文章の繰り返しです")), 3);
});
