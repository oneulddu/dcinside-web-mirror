const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

const source = fs.readFileSync(path.resolve(__dirname, "../../app/static/javascript/board_updates.js"), "utf8");
const context = {};
vm.runInNewContext(source, context);
const { computeNew } = context.MirrorBoardUpdates;

const item = (id, extra) => Object.assign({ id: String(id), title: "t" + id, author: "a", author_code: null }, extra || {});

test("기준보다 새 글만 세고 응답이 기준까지 닿으면 정확한 수를 준다", () => {
    const result = computeNew(100, [100, 99], [item(103), item(102), item(101), item(100), item(99)]);
    assert.equal(result.count, 3);
    assert.equal(result.more, false);
});

test("응답이 모두 기준보다 새로우면 '이상'으로 표시한다", () => {
    const result = computeNew(100, [100], [item(130), item(129), item(128)]);
    assert.equal(result.count, 3);
    assert.equal(result.more, true);
});

test("차단된 새 글과 이미 화면에 있는 글, 중복 글은 세지 않는다", () => {
    const blocked = it => it.author_code === "1.2";
    const result = computeNew(100, [100, 102], [item(103, { author_code: "1.2" }), item(102), item(101), item(101), item(100)], blocked);
    assert.equal(result.count, 1);
    assert.equal(result.newer, 2);
});

test("새 글이 없거나 번호가 이상한 항목은 무시한다", () => {
    assert.equal(computeNew(100, [100], [item(100), item(90)]).count, 0);
    assert.equal(computeNew(100, [100], [{ id: "abc" }, { id: "" }, null]).count, 0);
});
