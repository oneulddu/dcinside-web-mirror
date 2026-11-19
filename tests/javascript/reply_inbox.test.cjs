const test = require("node:test");
const assert = require("node:assert/strict");
const { load, track, receive } = require("../../app/static/javascript/reply_inbox.js");

test("a new post notifies for others' comments, preferring a single reply notification on overlap", () => {
    const state = load(null, 10), otherBrowser = load(null, 10);
    const post = {board: "test", kind: "", pid: "900", id: "post"};
    track(state, post, "내 글", 10);
    track(state, {...post, id: "10"}, "내 글", 11);
    const ids = ["9", "10", "12", "13"], edges = [["10", "12"]];
    receive(otherBrowser, post, edges, 12, ids);
    receive(state, {...post, pid: "901"}, edges, 12, ids);
    assert.deepEqual([state.inbox, otherBrowser.inbox], [[], []]);
    receive(state, post, edges, 13, ids);
    assert.deepEqual(state.inbox.map(row => [row.id, row.type]), [["13","comment"], ["12","reply"], ["9","comment"]]);
    state.inbox[0].unread = false;
    const next = load(JSON.stringify(state), 14);
    receive(next, post, [...edges, ["10","13"]], 14, ids);
    assert.deepEqual(next.inbox.map(row => [row.id,row.unread]), [["13",false], ["12",true], ["9",true]]);
});

test("only replies to this browser's successful comments in the matching post are notified", () => {
    const a = load(null, 10), b = load(null, 10);
    const post = { board: "test", kind: "minor", pid: "12", id: "100" };
    track(a, post, "제목", 10);
    track(a, { ...post, id: "105" }, "제목", 10);
    const edges = [["100", "105"], ["100", "120"], ["100", "120"], ["103", "115"]];
    receive(b, post, edges, 20);
    receive(a, { ...post, pid: "13" }, edges, 20);
    assert.deepEqual([a.inbox,b.inbox], [[],[]]);
    receive(a, post, edges, 20);
    assert.deepEqual(a.inbox.map(row => [row.id,row.unread]), [["120",true]]);
    a.inbox[0].unread = false;
    const next = load(JSON.stringify(a), 30);
    receive(next, post, [...edges, ["100","111"]], 30);
    assert.deepEqual(next.inbox.map(row => [row.id,row.unread]), [["111",true],["120",false]]);
    assert.deepEqual(load(JSON.stringify(next), 31 * 86400000).mine, []);
});

test("read replies stay read after the older authored-post watch expires", () => {
    const day = 86400000, post = { board: "test", kind: "", pid: "90", id: "post" };
    let state = load(null, day);
    track(state, post, "내 글", day);
    track(state, { ...post, id: "30" }, "내 글", 2 * day);
    receive(state, post, [["30", "40"]], 3 * day, ["30", "40"]);
    state.inbox[0].unread = false;
    state = load(JSON.stringify(state), 31 * day + 1);
    receive(state, post, [["30", "40"], ["30", "50"]], 31 * day + 1, ["30", "40", "50"]);
    assert.deepEqual(state.inbox.map(row => [row.id, row.unread]), [["50", true], ["40", false]]);
});
