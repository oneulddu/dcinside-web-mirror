const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");
const { test } = require("node:test");

const source = fs.readFileSync(path.resolve(__dirname, "../../app/static/javascript/image_viewer.js"), "utf8");
const context = {};
vm.runInNewContext(source, context);
const { isViewable, stepIndex, isSwipe } = context.MirrorImageViewer;

function image({ src = "/media?u=1", hidden = false, inBlocked = false, rendered = true } = {}) {
    return {
        hidden,
        getAttribute: (name) => (name === "src" ? src : null),
        closest: () => (inBlocked ? {} : null),
        getClientRects: () => (rendered ? [{}] : []),
    };
}

test("이미지 차단으로 주소가 없거나 숨겨진 이미지는 크게 보기 대상이 아니다", () => {
    assert.equal(isViewable(image()), true);
    assert.equal(isViewable(image({ src: null })), false);
    assert.equal(isViewable(image({ hidden: true })), false);
    assert.equal(isViewable(image({ rendered: false })), false);
    assert.equal(isViewable(null), false);
});

test("링크 안 이미지나 숨겨진 영역 안 이미지는 제외한다", () => {
    assert.equal(isViewable(image({ inBlocked: true })), false);
});

test("처음과 끝에서는 더 넘어가지 않는다", () => {
    assert.equal(stepIndex(0, 3, -1), 0);
    assert.equal(stepIndex(2, 3, 1), 2);
    assert.equal(stepIndex(1, 3, 1), 2);
});

test("세로 스크롤은 넘기기로 보지 않는다", () => {
    assert.equal(isSwipe(-80, 10), true);
    assert.equal(isSwipe(80, 70), false);
    assert.equal(isSwipe(30, 0), false);
});
