const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

const source = fs.readFileSync(path.resolve(__dirname, "../../app/static/javascript/author_actions.js"), "utf8");

// DOM의 텍스트·속성·노드 교체만 구현한다. 매칭 함수를 복제하거나 추출하지 않고
// 전체 스크립트의 boot()와 작성자 span → button 변환까지 실행한다.
class MemoryNode {
    constructor(tagName = "", text = "") {
        this.tagName = tagName.toUpperCase();
        this.nodeType = tagName === "#fragment" ? 11 : tagName ? 1 : 3;
        this.childNodes = [];
        this.attributes = new Map();
        this.parentNode = null;
        this.className = "";
        this.text = text;
    }
    get textContent() {
        return this.childNodes.length ? this.childNodes.map(node => node.textContent).join("") : this.text;
    }
    set textContent(value) {
        this.childNodes.forEach(node => { node.parentNode = null; });
        this.childNodes = [];
        this.text = String(value);
    }
    setAttribute(name, value) { this.attributes.set(name, String(value)); }
    getAttribute(name) { return this.attributes.get(name) ?? null; }
    hasAttribute(name) { return this.attributes.has(name); }
    appendChild(node) {
        node.parentNode = this;
        this.childNodes.push(node);
        return node;
    }
    replaceWith(node) {
        const parent = this.parentNode;
        assert.ok(parent, "교체할 노드는 부모가 있어야 한다");
        const replacements = node.nodeType === 11 ? node.childNodes.slice() : [node];
        replacements.forEach(child => { child.parentNode = parent; });
        parent.childNodes.splice(parent.childNodes.indexOf(this), 1, ...replacements);
        this.parentNode = null;
        if (node.nodeType === 11) node.childNodes = [];
    }
    closest(selector) {
        assert.equal(selector, "a");
        for (let node = this; node; node = node.parentNode) {
            if (node.tagName === "A") return node;
        }
        return null;
    }
}

function run(people, parts) {
    const meta = new MemoryNode("div");
    people.forEach(([name, code, display = name]) => {
        const author = meta.appendChild(new MemoryNode("span", display));
        author.className = "author-text";
        author.setAttribute("data-author", display);
        author.setAttribute("data-author-search-name", name);
        if (code) author.setAttribute("data-author-code", code);
    });
    const paragraph = new MemoryNode("p");
    parts.forEach(part => paragraph.appendChild(typeof part === "string" ? new MemoryNode("", part) : part));
    const originalText = paragraph.textContent;
    const listeners = {};
    const document = {
        readyState: "complete",
        querySelectorAll(selector) {
            if (selector === ".article-meta span.author-text[data-author], .comment-meta span.author-text[data-author]") {
                return meta.childNodes.filter(node => node.tagName === "SPAN" && node.hasAttribute("data-author"));
            }
            if (selector === ".article-meta [data-author-search-name], .comment-meta [data-author-search-name]") {
                return meta.childNodes.filter(node => node.hasAttribute("data-author-search-name"));
            }
            if (selector === ".comment-main > p") return [paragraph];
            throw new Error("지원하지 않는 선택자: " + selector);
        },
        createElement: tag => new MemoryNode(tag),
        createTextNode: text => new MemoryNode("", text),
        createDocumentFragment: () => new MemoryNode("#fragment"),
        addEventListener: (type, listener) => { listeners[type] = listener; },
    };
    vm.runInNewContext(source, { document, window: { addEventListener() {} }, URLSearchParams });
    assert.equal(typeof listeners.click, "function", "전체 boot()가 클릭 핸들러 등록까지 끝나야 한다");
    assert.ok(meta.childNodes.every(node => node.tagName === "BUTTON"));
    assert.equal(paragraph.textContent, originalText, "본문의 공백·구두점·줄바꿈을 그대로 보존해야 한다");
    const buttons = paragraph.childNodes.filter(node => node.tagName === "BUTTON");
    return {
        paragraph,
        mentions: buttons.map(button => ({
            text: button.textContent,
            name: button.getAttribute("data-author-search-name"),
            code: button.getAttribute("data-author-code"),
        })),
    };
}

test("다른 IP나 계정 코드가 붙은 멘션에 화면의 동명 계정 코드를 붙이지 않는다", () => {
    const result = run([["가나다", "111.22"]], ["@가나다(222.33) 답변 / @가나다(otherid) 확인"]);
    assert.deepEqual(result.mentions, [
        { text: "@가나다(222.33)", name: "가나다(222.33)", code: null },
        { text: "@가나다(otherid)", name: "가나다(otherid)", code: null },
    ]);
});

test("점 뒤 문자나 숫자가 이어지는 미지의 닉네임을 기존 계정에 연결하지 않는다", () => {
    const result = run([["abc", "knownid"]], ["@abc.def @abc.123 @abc.한글"]);
    assert.deepEqual(result.mentions, [
        { text: "@abc.def", name: "abc.def", code: null },
        { text: "@abc.123", name: "abc.123", code: null },
        { text: "@abc.한글", name: "abc.한글", code: null },
    ]);
});

test("닉네임과 코드가 정확히 일치하면 해당 계정으로 연결한다", () => {
    const result = run([["가나다", "111.22"]], ["@가나다(111.22) 답변"]);
    assert.deepEqual(result.mentions, [{ text: "@가나다(111.22)", name: "가나다", code: "111.22" }]);
});

test("문장을 끝내는 마침표는 닉네임에 포함하지 않는다", () => {
    const result = run([["abc", "knownid"]], ["@abc. 다음 문장 @abc."]);
    assert.deepEqual(result.mentions, [
        { text: "@abc", name: "abc", code: "knownid" },
        { text: "@abc", name: "abc", code: "knownid" },
    ]);
});

test("동명이인은 순서와 중복 등장에 무관하게 코드 없이 열고 명시한 코드는 구분한다", () => {
    for (const codes of [["one", "two", "one"], ["two", "one", "two"], ["one", "", "one"]]) {
        const result = run(codes.map(code => ["가나다", code]), ["@가나다 답변 @가나다(one)"]);
        assert.deepEqual(result.mentions, [
            { text: "@가나다", name: "가나다", code: null },
            { text: "@가나다(one)", name: "가나다", code: "one" },
        ]);
    }
});

test("이메일과 URL 링크는 유지하면서 주변 멘션·공백·줄바꿈을 보존한다", () => {
    const link = new MemoryNode("a");
    const url = "https://example.com/@abc?q=@가나다#@새닉";
    link.setAttribute("href", url);
    link.appendChild(new MemoryNode("", url));
    const result = run([["abc", "knownid"]], [
        "foo@abc.com a.b+tag@example.com\n  ", link, "\t(@abc), @새닉!\n끝",
    ]);
    assert.deepEqual(result.mentions, [
        { text: "@abc", name: "abc", code: "knownid" },
        { text: "@새닉", name: "새닉", code: null },
    ]);
    assert.ok(result.paragraph.childNodes.includes(link), "기존 링크 노드를 교체하지 않아야 한다");
    assert.equal(link.getAttribute("href"), url);
    assert.equal(link.textContent, url);
    assert.equal(link.childNodes.length, 1);
    assert.equal(link.childNodes[0].nodeType, 3);
});
