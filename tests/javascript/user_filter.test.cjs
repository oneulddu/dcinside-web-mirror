const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

const source = fs.readFileSync(path.resolve(__dirname, "../../app/static/javascript/user_filter.js"), "utf8");

function loadApi() {
    const context = {};
    vm.runInNewContext(source, context);
    return context.MirrorUserFilter;
}

const api = loadApi();

function settingsWith(rules, extra) {
    let settings = api.defaultSettings();
    for (const [type, value] of rules) {
        const result = api.addRule(settings, type, value);
        assert.equal(result.error, null, type + ":" + value);
        settings = result.settings;
    }
    return Object.assign(settings, extra || {});
}

test("닉네임과 식별 코드는 정규화 후 완전히 같을 때만 차단한다", () => {
    const compiled = api.compile(settingsWith([["nickname", "  ABC닉 "], ["authorCode", "Noodle4477"]]));
    assert.equal(api.matchItem(compiled, { author: "abc닉" }), "nickname");
    assert.equal(api.matchItem(compiled, { author: "abc닉네임" }), null);
    assert.equal(api.matchItem(compiled, { author: "누군가", code: "noodle4477" }), "authorCode");
    assert.equal(api.matchItem(compiled, { author: "누군가", code: "noodle44" }), null);
    // 조합형과 분해형 한글은 같은 글자로 본다.
    const decomposed = "한글".normalize("NFD");
    const nfdCompiled = api.compile(settingsWith([["nickname", decomposed]]));
    assert.equal(api.matchItem(nfdCompiled, { author: "한글" }), "nickname");
});

test("__proto__ 같은 특수 이름도 일반 닉네임·코드처럼 차단한다", () => {
    const compiled = api.compile(settingsWith([["nickname", "__proto__"], ["authorCode", "constructor"]]));
    assert.equal(api.matchItem(compiled, { author: "__proto__" }), "nickname");
    assert.equal(api.matchItem(compiled, { author: "x", code: "constructor" }), "authorCode");
    assert.equal(api.matchItem(api.compile(settingsWith([["nickname", "a"]])), { author: "toString" }), null);
});

test("제목 단어는 포함 여부로 판정하고 정규식 문자를 그대로 비교한다", () => {
    const compiled = api.compile(settingsWith([["titleKeyword", "스포"], ["titleKeyword", "a.b"]]));
    assert.equal(api.matchItem(compiled, { title: "최종화 스포 있음" }), "titleKeyword");
    assert.equal(api.matchItem(compiled, { title: "axb 테스트" }), null);
    assert.equal(api.matchItem(compiled, { title: "A.B 테스트" }), "titleKeyword");
    // 댓글처럼 제목이 없으면 단어 규칙에 걸리지 않는다.
    assert.equal(api.matchItem(compiled, { author: "스포" }), null);
});

test("유동 전체 차단은 IP 앞자리 코드만 막고 코드가 없는 익명은 남긴다", () => {
    const compiled = api.compile(settingsWith([], { blockGuests: true }));
    assert.equal(api.matchItem(compiled, { author: "익명", code: "175.113" }), "guest");
    assert.equal(api.matchItem(compiled, { author: "익명", code: "" }), null);
    assert.equal(api.matchItem(compiled, { author: "고닉", code: "uid123" }), null);
    assert.equal(api.matchItem(compiled, { author: "운영자" }), null);
});

test("필터를 끄면 규칙은 남지만 아무것도 차단하지 않는다", () => {
    const settings = settingsWith([["nickname", "ㅇㅇ"]], { enabled: false });
    assert.equal(api.isActive(settings), false);
    assert.equal(api.matchItem(api.compile(settings), { author: "ㅇㅇ" }), null);
    assert.equal(settings.rules.length, 1);
});

test("규칙 추가는 중복·빈 값·길이·개수·공백 코드를 거부한다", () => {
    let settings = settingsWith([["nickname", "가나"]]);
    assert.equal(api.addRule(settings, "nickname", " 가나 ").error, "이미 등록된 규칙이에요.");
    assert.equal(api.addRule(settings, "authorCode", "가나").error, null);
    assert.ok(api.addRule(settings, "nickname", "   ").error);
    assert.ok(api.addRule(settings, "authorCode", "a b").error);
    assert.ok(api.addRule(settings, "unknown", "x").error);
    assert.ok(api.addRule(settings, "nickname", "a\u0007").error);
    assert.equal(api.addRule(settings, "nickname", "가".repeat(64)).error, null);
    assert.ok(api.addRule(settings, "nickname", "가".repeat(65)).error);
    // 이모지처럼 두 UTF-16 단위인 글자도 한 글자로 센다.
    assert.equal(api.addRule(settings, "nickname", "😀".repeat(64)).error, null);

    settings = api.defaultSettings();
    for (let i = 0; i < api.MAX_RULES; i += 1) {
        settings = api.addRule(settings, "authorCode", "id" + i).settings;
    }
    assert.equal(settings.rules.length, api.MAX_RULES);
    assert.ok(api.addRule(settings, "authorCode", "extra").error);
});

test("규칙 삭제는 정규화한 값이 같은 규칙만 지우고 원래 설정은 바꾸지 않는다", () => {
    const settings = settingsWith([["nickname", "ABC"], ["authorCode", "abc"]]);
    const next = api.removeRule(settings, "nickname", "abc");
    assert.deepEqual(JSON.parse(JSON.stringify(next.rules)), [{ type: "authorCode", value: "abc" }]);
    assert.equal(settings.rules.length, 2);
});

test("저장값 해석은 없는 값을 기본값으로, 손상·다른 버전은 오류로 돌려준다", () => {
    // vm 컨텍스트의 객체는 프로토타입이 달라 JSON으로 비교한다.
    const plain = value => JSON.parse(JSON.stringify(value));
    assert.deepEqual(plain(api.parseSettings(null)), { settings: { version: 1, enabled: true, blockGuests: false, rules: [] }, error: null });
    assert.equal(api.parseSettings("{oops").error, "invalid");
    assert.equal(api.parseSettings("[]").error, "invalid");
    assert.equal(api.parseSettings(JSON.stringify({ version: 2, enabled: true, blockGuests: false, rules: [] })).error, "version");
    assert.equal(api.parseSettings(JSON.stringify({ version: 1, enabled: "yes", blockGuests: false, rules: [] })).error, "invalid");
    assert.equal(api.parseSettings(JSON.stringify({ version: 1, enabled: true, blockGuests: false, rules: [{ type: "nickname", value: "" }] })).error, "invalid");

    const ok = api.parseSettings(JSON.stringify({
        version: 1, enabled: true, blockGuests: true,
        rules: [{ type: "nickname", value: " 닉 " }, { type: "nickname", value: "닉" }],
    }));
    assert.equal(ok.error, null);
    assert.deepEqual(plain(ok.settings.rules), [{ type: "nickname", value: "닉" }]);
});

test("DOM이 없으면 화면 연결 없이 판정 API만 만든다", () => {
    const context = {};
    vm.runInNewContext(source, context);
    assert.equal(typeof context.MirrorUserFilter.matchItem, "function");
});
