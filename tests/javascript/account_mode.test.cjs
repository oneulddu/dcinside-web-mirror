const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

const root = path.resolve(__dirname, "../..");
const context = { URLSearchParams };
for (const name of ["dc_post_client.js", "write_form.js", "comment_form.js", "account_login.js"]) {
    vm.runInNewContext(fs.readFileSync(path.join(root, "app/static/javascript", name), "utf8"), context);
}
const post = context.MirrorDcPost;
const limits = { subject_max: 40, body_max: 10000, nickname_max: 20, password_min: 2, password_max: 20, comment_max: 400 };

function memoryStorage() {
    const data = new Map();
    return {
        getItem: (key) => (data.has(key) ? data.get(key) : null),
        setItem: (key, value) => data.set(key, String(value)),
    };
}

test("로그인이 필요하다는 응답은 결과를 모르는 경우와 따로 다룬다", () => {
    const payload = { ok: false, code: "login_needed", reason: "expired", message: "다시 로그인", account_url: "/write/account" };
    assert.equal(post.interpret(200, payload).action, "login_needed");
    assert.equal(post.interpret(200, { ok: false, code: "uncertain" }).action, "unknown");
});

test("계정 화면 링크는 같은 사이트 경로만 따른다", () => {
    assert.equal(post.accountLink({ account_url: "/write/account" }).href, "/write/account");
    assert.equal(post.accountLink({ account_url: "//evil.test/x" }).href, "/write/account");
    assert.equal(post.accountLink(null).newTab, true);
});

test("작성자 선택은 계정 번호까지 기억하고, 예전 값은 계정 1로 읽는다", () => {
    const storage = memoryStorage();
    assert.equal(post.readMode(storage), "anonymous");
    post.saveMode(storage, "account:2");
    assert.equal(post.readMode(storage), "account:2");
    storage.setItem(post.MODE_KEY, "account");
    assert.equal(post.readMode(storage), "account:1");
    for (const bad of ["something", "account:3", "account:", "account:1x"]) {
        storage.setItem(post.MODE_KEY, bad);
        assert.equal(post.readMode(storage), "anonymous");
    }
    assert.equal(post.readMode(null), "anonymous");
    const broken = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("blocked"); } };
    assert.equal(post.readMode(broken), "anonymous");
    post.saveMode(broken, "account:1");
    assert.equal(post.modeAccount("account:2"), "2");
    assert.equal(post.modeAccount("anonymous"), "");
});

test("기억한 계정이 선택지에 없으면 다른 계정으로 바꾸지 않고 다시 고르게 한다", () => {
    const inputs = [{ value: "anonymous" }, { value: "account:1" }];
    assert.equal(post.pickMode("account:1", inputs), "account:1");
    assert.equal(post.pickMode("account:2", inputs), "");
    assert.equal(post.pickMode("anonymous", inputs), "anonymous");
    // 계정을 모두 뺐으면 유동으로 바로 보내지 않고 한 번 알린다.
    assert.equal(post.pickMode("account:2", []), "");
    assert.equal(post.pickMode("anonymous", []), "anonymous");
});

test("상태 응답에서 계정 번호별 상태만 꺼내고, 예전 응답은 계정 1로 본다", () => {
    const one = { logged_in: true, user_id: "a1", name: "닉1" };
    const two = { logged_in: false, user_id: null, name: null };
    assert.deepEqual(Object.keys(post.accountList({ ok: true, accounts: { "1": one, "2": two, "3": one } })), ["1", "2"]);
    assert.equal(post.accountList({ ok: true, logged_in: true, user_id: "a1" })["1"].user_id, "a1");
    assert.deepEqual(Object.keys(post.accountList(null)), []);
});

test("계정으로 쓰면 닉네임과 비밀번호를 확인하지 않는다", () => {
    const write = context.MirrorWriteForm;
    const values = { nickname: "", password: "", subject: "제목", body: "본문" };
    assert.equal(write.validateFields(values, limits).field, "nickname");
    assert.equal(write.validateFields(Object.assign({}, values, { account: true }), limits), null);
    assert.equal(write.validateFields(Object.assign({}, values, { account: true, subject: " " }), limits).field, "subject");
    const comment = context.MirrorCommentForm;
    assert.equal(comment.validateComment({ content: "댓글", nickname: "", password: "" }, limits).field, "nickname");
    assert.equal(comment.validateComment({ content: "댓글", account: true }, limits), null);
    assert.equal(comment.validateComment({ content: " ", account: true }, limits).field, "content");
});

test("로그인 상태 문구는 닉네임과 아이디를 함께 보여 준다", () => {
    const login = context.MirrorAccountLogin;
    assert.equal(login.describe({ name: "고정닉", user_id: "abc12" }), "고정닉(abc12) 계정으로 로그인했어요.");
    assert.equal(login.describe({ name: "", user_id: "abc12" }), "abc12(abc12) 계정으로 로그인했어요.");
    assert.ok(login.OTP_PATTERN.test("012345"));
    assert.ok(!login.OTP_PATTERN.test("12345a"));
});
