const test = require("node:test");
const assert = require("node:assert/strict");
const { load, track, receive } = require("../../app/static/javascript/reply_inbox.js");

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
