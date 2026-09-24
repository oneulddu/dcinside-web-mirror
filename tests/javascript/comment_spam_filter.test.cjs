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
    } });
    return comments.filter(comment => comment.hidden).length;
}

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
