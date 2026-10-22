const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

const root = path.resolve(__dirname, "../..");
const context = {};
vm.runInNewContext(fs.readFileSync(path.join(root, "app/static/javascript/comment_search.js"), "utf8"), context);
vm.runInNewContext(fs.readFileSync(path.join(root, "app/static/javascript/post_manage.js"), "utf8"), context);
const search = context.MirrorCommentSearch;
const manage = context.MirrorPostManage;

test("댓글 찾기는 대소문자와 연속 공백을 무시한다", () => {
    const text = search.normalize("Hello   World\n두 줄");
    assert.equal(text, "hello world 두 줄");
    assert.ok(search.matches(text, search.normalize("WORLD  두")));
    assert.ok(!search.matches(text, search.normalize("없는 말")));
    assert.ok(search.matches(text, ""), "빈 검색어는 모두 보인다");
});

test("찾은 개수와 일부만 불러온 안내", () => {
    assert.equal(search.statusText("", 3, false), "");
    assert.equal(search.statusText("a", 0, false), "찾는 댓글이 없어요.");
    assert.equal(search.statusText("a", 1200, false), "1,200개 찾았어요.");
    assert.equal(search.statusText("a", 2, true), "2개 찾았어요. 일부만 불러온 댓글 안에서 찾았어요.");
});

test("삭제 비밀번호는 서버와 같은 길이 규칙을 쓴다", () => {
    const limits = { password_min: 2, password_max: 20 };
    assert.match(manage.validatePassword("", limits), /2~20자/);
    assert.match(manage.validatePassword("  ", limits), /2~20자/);
    assert.match(manage.validatePassword("a", limits), /2~20자/);
    assert.equal(manage.validatePassword("ab", limits), null);
    assert.equal(manage.validatePassword("😀".repeat(20), limits), null);
    assert.match(manage.validatePassword("a".repeat(21), limits), /2~20자/);
});
