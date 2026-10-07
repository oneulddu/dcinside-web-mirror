const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

const source = fs.readFileSync(path.resolve(__dirname, "../../app/static/javascript/board_keyboard.js"), "utf8");
const context = {};
vm.runInNewContext(source, context);
const { keyAction, stepIndex, isTypingTarget } = context.MirrorBoardKeyboard;

const key = (extra) => Object.assign({ key: "", code: "", altKey: false, ctrlKey: false, metaKey: false, shiftKey: false, isComposing: false, defaultPrevented: false }, extra);

test("한글 입력 상태에서도 키 위치로 J/K/O를 알아본다", () => {
    assert.equal(keyAction(key({ key: "ㅓ", code: "KeyJ" })), "next");
    assert.equal(keyAction(key({ key: "ㅏ", code: "KeyK" })), "prev");
    assert.equal(keyAction(key({ key: "ㅐ", code: "KeyO" })), "open");
    assert.equal(keyAction(key({ key: "J" })), "next");
    assert.equal(keyAction(key({ key: "Escape", code: "Escape" })), "clear");
});

test("보조키, 조합 중 입력, 이미 처리된 키는 무시한다", () => {
    assert.equal(keyAction(key({ key: "j", code: "KeyJ", ctrlKey: true })), null);
    assert.equal(keyAction(key({ key: "j", code: "KeyJ", metaKey: true })), null);
    assert.equal(keyAction(key({ key: "J", code: "KeyJ", shiftKey: true })), null);
    assert.equal(keyAction(key({ key: "ㅓ", code: "KeyJ", isComposing: true })), null);
    assert.equal(keyAction(key({ key: "j", code: "KeyJ", defaultPrevented: true })), null);
    assert.equal(keyAction(key({ key: "x", code: "KeyX" })), null);
});

test("선택이 없으면 J는 첫 글, K는 마지막 글을 고르고 끝에서는 멈춘다", () => {
    assert.equal(stepIndex(-1, 5, "next"), 0);
    assert.equal(stepIndex(-1, 5, "prev"), 4);
    assert.equal(stepIndex(4, 5, "next"), 4);
    assert.equal(stepIndex(0, 5, "prev"), 0);
    assert.equal(stepIndex(2, 5, "next"), 3);
    assert.equal(stepIndex(-1, 0, "next"), -1);
});

test("입력칸과 편집 영역에서는 단축키를 쓰지 않는다", () => {
    assert.equal(isTypingTarget({ tagName: "INPUT" }), true);
    assert.equal(isTypingTarget({ tagName: "textarea" }), true);
    assert.equal(isTypingTarget({ tagName: "DIV", isContentEditable: true }), true);
    assert.equal(isTypingTarget({ tagName: "A" }), false);
    assert.equal(isTypingTarget(null), false);
});
