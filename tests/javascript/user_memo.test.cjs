const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

const source = fs.readFileSync(path.resolve(__dirname, "../../app/static/javascript/user_memo.js"), "utf8");
const context = {};
vm.runInNewContext(source, context);
const api = context.MirrorUserMemo;

test("식별 코드가 있으면 코드, 없으면 원본 닉네임으로 구분한다", () => {
    assert.equal(api.identityFor({ code: " Noodle4477 ", searchName: "닉" }), "code:noodle4477");
    assert.equal(api.identityFor({ code: "175.113", searchName: "ㅇㅇ" }), "code:175.113");
    assert.equal(api.identityFor({ code: "", searchName: " ㅇㅇ " }), "name:ㅇㅇ");
    assert.equal(api.identityFor({ code: "", searchName: "한글".normalize("NFD") }), "name:한글");
    assert.equal(api.identityFor({ code: "", searchName: "" }), null);
    assert.equal(api.identityFor({ code: "a b", searchName: "" }), null);
});

test("메모는 한 줄로 정리하고 빈 값·120자 초과를 거부한다", () => {
    assert.equal(api.cleanMemo("  좋은\n답변  ").value, "좋은 답변");
    assert.equal(api.cleanMemo("   ").ok, false);
    assert.equal(api.cleanMemo("가".repeat(120)).ok, true);
    assert.equal(api.cleanMemo("가".repeat(121)).ok, false);
    assert.equal(api.cleanMemo("😀".repeat(120)).ok, true);
    assert.equal(api.cleanMemo("a\u0007b").ok, false);
});

test("저장값은 키와 식별자가 맞고 형식이 올바를 때만 읽는다", () => {
    const identity = "code:abc";
    const key = api.storageKey(identity);
    const entry = api.buildEntry(identity, "닉", "메모", 1);
    assert.equal(api.parseEntry(key, JSON.stringify(entry)).memo, "메모");
    assert.equal(api.parseEntry(api.storageKey("code:other"), JSON.stringify(entry)), null);
    assert.equal(api.parseEntry(key, "{broken"), null);
    assert.equal(api.parseEntry(key, JSON.stringify(Object.assign({}, entry, { version: 2 }))), null);
    assert.equal(api.parseEntry(key, JSON.stringify(Object.assign({}, entry, { memo: "" }))), null);
    assert.equal(api.parseEntry(key, JSON.stringify(Object.assign({}, entry, { memo: "a\nb" }))), null);
    assert.equal(api.parseEntry(key, "x".repeat(5000)), null);
});

test("저장 키는 식별자를 그대로 인코딩해 서로 겹치지 않는다", () => {
    assert.notEqual(api.storageKey("name:a:b"), api.storageKey("name:a"));
    assert.ok(api.storageKey("name:ㅇㅇ").startsWith(api.PREFIX));
});
