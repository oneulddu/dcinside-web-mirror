const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

const root = path.resolve(__dirname, "../..");
const context = { URLSearchParams };
vm.runInNewContext(fs.readFileSync(path.join(root, "app/static/javascript/dc_post_client.js"), "utf8"), context);
vm.runInNewContext(fs.readFileSync(path.join(root, "app/static/javascript/write_form.js"), "utf8"), context);
const post = context.MirrorDcPost;
const form = context.MirrorWriteForm;
const limits = { subject_max: 40, body_max: 10000, nickname_max: 20, password_min: 2, password_max: 20 };
const ok = { nickname: "ㅇㅇ", password: "1234", subject: "제목", body: "본문" };

test("이모지는 서버처럼 한 글자로 센다", () => {
    assert.equal(post.charLength("가😀"), 2);
    assert.equal(form.validateFields(Object.assign({}, ok, { subject: "😀".repeat(40) }), limits), null);
    assert.equal(form.validateFields(Object.assign({}, ok, { subject: "😀".repeat(41) }), limits).field, "subject");
});

test("빈 칸과 길이 제한을 칸 이름과 함께 알려 준다", () => {
    assert.equal(form.validateFields(Object.assign({}, ok, { nickname: "  " }), limits).field, "nickname");
    assert.equal(form.validateFields(Object.assign({}, ok, { password: "1" }), limits).field, "password");
    assert.equal(form.validateFields(Object.assign({}, ok, { subject: "   " }), limits).field, "subject");
    assert.equal(form.validateFields(Object.assign({}, ok, { body: "\n\n" }), limits).field, "body");
    assert.equal(form.validateFields(Object.assign({}, ok, { body: "a".repeat(10001) }), limits).field, "body");
    assert.equal(form.validateFields(ok, limits), null);
});

test("비밀번호 앞뒤 공백은 지우지 않고 그대로 센다", () => {
    assert.equal(form.validateFields(Object.assign({}, ok, { password: " 1" }), limits), null);
});

test("사진이 있으면 본문을 비워도 되고, 없으면 본문이 필요하다", () => {
    assert.equal(form.validateFields(Object.assign({}, ok, { body: "  ", imageCount: 1 }), limits), null);
    assert.equal(form.validateFields(Object.assign({}, ok, { body: "", imageCount: 0 }), limits).field, "body");
    assert.equal(form.validateFields(Object.assign({}, ok, { body: "a".repeat(10001), imageCount: 2 }), limits).field, "body");
});

test("사진은 형식·장당 크기·장수·합계를 넘는 것만 빼고 넣는다", () => {
    const imageLimits = { image_max_count: 3, image_max_bytes: 100, image_total_bytes: 250 };
    const file = (name, type, size) => ({ name, type, size });
    const picked = form.pickImages([file("a.jpg", "image/jpeg", 90)], [
        file("b.png", "image/png", 90),
        file("c.heic", "image/heic", 10),
        file("d.gif", "image/gif", 101),
        file("e.webp", "", 80),
        file("f.jpg", "image/jpeg", 10)
    ], imageLimits);
    assert.equal(JSON.stringify(picked.accepted.map((f) => f.name)), JSON.stringify(["b.png", "f.jpg"]));
    assert.match(picked.message, /아닌 파일 1개/);
    assert.match(picked.message, /빈 사진 1장/);
    assert.match(picked.message, /합쳐 0MB를 넘어서 1장/);
    const full = form.pickImages([file("a.jpg", "image/jpeg", 1)], [1, 2, 3].map((n) => file(n + ".jpg", "image/jpeg", 1)), imageLimits);
    assert.equal(full.accepted.length, 2);
    assert.match(full.message, /3장까지라 1장/);
    assert.equal(form.pickImages([], [file("x.png", "image/png", 0)], imageLimits).accepted.length, 0);
    assert.equal(form.pickImages([], [file("x.webp", "", 10)], imageLimits).accepted.length, 1);
});

test("사진 순서는 범위 안에서만 옮긴다", () => {
    assert.equal(JSON.stringify(form.moveItem([1, 2, 3], 0, 1)), JSON.stringify([2, 1, 3]));
    assert.equal(JSON.stringify(form.moveItem([1, 2, 3], 2, 1)), JSON.stringify([1, 3, 2]));
    const list = [1, 2];
    assert.equal(form.moveItem(list, 0, -1), list);
    assert.equal(form.moveItem(list, 1, 2), list);
});

test("캡챠가 보이면 답이 있어야 하고 이미지를 못 받았으면 보내지 않는다", () => {
    assert.equal(form.validateFields(Object.assign({}, ok, { captcha: { key: "a".repeat(16), code: "" } }), limits).field, "captcha");
    assert.equal(form.validateFields(Object.assign({}, ok, { captcha: { key: "a".repeat(16), code: "ab12", imageFailed: true } }), limits).field, "captcha");
    assert.equal(form.validateFields(Object.assign({}, ok, { captcha: { key: "a".repeat(16), code: "ab12" } }), limits), null);
});

test("동의한 값만 저장하고 둘 다 끄면 지운다. 저장 실패는 false", () => {
    const store = new Map();
    const storage = { getItem: (k) => store.get(k) ?? null, setItem: (k, v) => store.set(k, v), removeItem: (k) => store.delete(k) };
    assert.equal(form.writeIdentity(storage, { nickname: "ㅇㅇ", password: "" }), true);
    assert.equal(JSON.stringify(form.readIdentity(storage)), JSON.stringify({ nickname: "ㅇㅇ" }));
    assert.equal(form.writeIdentity(storage, { nickname: "", password: "" }), true);
    assert.equal(store.has(form.IDENTITY_KEY), false);
    const broken = { setItem: () => { throw new Error("quota"); }, removeItem: () => {} };
    assert.equal(form.writeIdentity(broken, { nickname: "a" }), false);
    assert.equal(JSON.stringify(form.readIdentity({ getItem: () => "{not json" })), "{}");
});

test("응답 코드를 화면 동작으로 바꾸고, 결과를 모르면 unknown이다", () => {
    assert.equal(post.interpret(200, { ok: true }).action, "success");
    assert.equal(post.interpret(200, { ok: false, code: "captcha_required", captcha: { key: "0123456789abcdef", image_url: "/write/captcha?x" } }).action, "captcha");
    assert.equal(post.interpret(200, { ok: false, code: "captcha_required", captcha: { key: "bad" } }).action, "unknown");
    assert.equal(post.interpret(429, { ok: false, code: "retry", retry_after: 12.2 }).seconds, 13);
    assert.equal(post.interpret(429, { ok: false, code: "retry" }).seconds, 10);
    assert.equal(post.interpret(403, { ok: false, code: "locked" }).action, "locked");
    assert.equal(post.interpret(0, null).action, "unknown");
    assert.equal(post.interpret(502, { ok: false }).action, "unavailable");
});

test("이동 주소는 같은 사이트 경로만 받는다", () => {
    assert.equal(post.safePath("/board?board=a&refresh=1"), "/board?board=a&refresh=1");
    assert.equal(post.safePath("//evil.example/x"), "");
    assert.equal(post.safePath("/\\evil.example"), "");
    assert.equal(post.safePath("https://evil.example"), "");
    assert.equal(post.safePath(null), "");
});

test("캡챠 키는 16자리 소문자 16진수다", () => {
    const key = post.newCaptchaKey({ getRandomValues: (a) => { a.fill(10); return a; } });
    assert.equal(key, "0a".repeat(8));
    assert.match(post.captchaImageUrl("article", "programming", key), /^\/write\/captcha\?kind=article&board=programming&key=0a0a/);
});

test("결과를 모르는 uncertain은 다시 보내라고 하지 않는 unknown으로 다룬다", () => {
    assert.equal(post.interpret(503, { ok: false, code: "uncertain", message: "x" }).action, "unknown");
    assert.equal(post.interpret(503, { ok: false, code: "unavailable", message: "x" }).action, "unavailable");
});
