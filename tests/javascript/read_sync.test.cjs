const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../../app/static/javascript/read_state.js'), 'utf8');
const store = 'read_posts_v1', meta = 'read_posts_sync_v1';
const stateId = 'abcdef0123456789abcdef0123456789';
const settled = () => new Promise(resolve => setImmediate(resolve));

// Exercise DOM events, fetch and both storage areas without loading an upstream page.
function page({url = '/recent', enabled = true, storage = new Map(), session = new Map()} = {}) {
  let now = 100000;
  const events = {}, requests = [], classes = new Set();
  const link = {getAttribute: () => '/read?board=b&pid=2', querySelector: () => null,
    classList: {toggle: (k, on) => on ? classes.add(k) : classes.delete(k)}};
  const area = map => ({getItem: k => map.get(k) ?? null, setItem: (k, v) => map.set(k, v)});
  const document = {readyState: 'loading', visibilityState: 'visible',
    body: {dataset: enabled ? {readSync: '/read-state/sync'} : {}, classList: {toggle() {}}},
    documentElement: {dataset: {}, style: {}},
    addEventListener: (event, cb) => (events[event] ||= []).push(cb),
    querySelector: () => null, querySelectorAll: s => s.includes('a.feed-item') ? [link] : [],
    getElementById: id => id === 'article-body' ? {getAttribute: () => '42'} : null};
  let retry;
  const window = {location: new URL(url, 'https://mirror.test'), addEventListener: document.addEventListener,
    setTimeout(cb, delay) { retry = () => { now += delay; cb(); }; return 1; }, clearTimeout() {},
    localStorage: area(storage), sessionStorage: area(session),
    fetch: (dest, options) => new Promise((resolve, reject) => {
      assert.equal(dest, '/read-state/sync');
      requests.push({body: JSON.parse(options.body), keepalive: options.keepalive,
        reply: (rev, items, reset = false) => resolve({ok: true, json: async () => ({ok: true, id: stateId, rev, items, reset})}),
        fail: status => status ? resolve({ok: false, status}) : reject(new Error('offline'))});
    })};
  vm.runInNewContext(source, {window, document, URL, URLSearchParams, Blob, Date: {now: () => now}});
  const emit = (name, detail = {}) => events[name].forEach(cb => cb(detail));
  return {requests, classes, boot: () => emit('DOMContentLoaded'), visible: () => emit('visibilitychange'),
    retry: () => retry(), emit, advance: ms => { now += ms; }, get: k => JSON.parse(storage.get(k)), put: (k,v) => storage.set(k, JSON.stringify(v))};
}

test('first upload includes current article and history; reply preserves concurrent local edits', async () => {
  const h = page({url: '/read?board=b&pid=1'});
  h.put(store, {'poker:42': 5});
  h.put(meta, {rev: 9, pending: 'corrupt'});
  h.boot();
  h.visible();
  assert.equal(h.requests.length, 1);
  assert.deepEqual(h.requests[0].body, {since: 0, items: {'poker:42': 5, 'b|1': 100000}});
  assert.equal(h.requests[0].keepalive, false);
  h.put(store, {'poker:42': 6, 'b|1': 100000, 'a|3': 11});
  h.put(meta, {rev: 12, pending: ['poker:42','b|1','a|3']});
  h.requests[0].reply(10, {'poker:42': 5, 'b|2': 0});
  await settled();
  assert.deepEqual(h.get(store), {'poker:42': 6, 'b|1': 100000, 'a|3': 11, 'b|2': 0});
  assert.deepEqual(h.get(meta), {id: stateId, rev: 12, pending: ['poker:42','a|3']});
  assert.ok(h.classes.has('is-read'));
  h.visible();
  assert.deepEqual(h.requests[1].body, {id: stateId, since: 12, items: {'poker:42': 6, 'a|3': 11}});
});

test('reset replaces cursor and replays local union once only', async () => {
  const h = page();
  h.put(store, {'b|1': 100}); h.put(meta, {rev: 20, pending: []});
  h.boot();
  assert.deepEqual(h.requests[0].body, {since: 20, items: {}});
  h.requests[0].reply(1, {'poker:1': 1}, true);
  await settled();
  assert.deepEqual(h.requests[1].body, {id: stateId, since: 1, items: {'b|1': 100, 'poker:1': 1}});
  h.requests[1].reply(0, {}, true);
  await settled();
  assert.equal(h.requests.length, 2);
  assert.deepEqual(h.get(meta), {id: stateId, rev: 0, pending: ['b|1','poker:1']});
});

test('empty pulls are throttled across visibility changes and same-tab navigations', async () => {
  const shared = {storage: new Map(), session: new Map()};
  const h = page(shared); h.boot(); h.requests[0].reply(8, {});
  await settled();
  const next = page(shared); next.boot();
  next.advance(29999); next.visible();
  assert.equal(next.requests.length, 0);
  next.advance(1); next.visible();
  assert.deepEqual(next.requests[0].body, {id: stateId, since: 8, items: {}});
});

test('locked DC and Poker keep marks queued for the next unlocked DC page', () => {
  const storage = new Map();
  for (const url of ['/read?board=b&pid=1','/poker/best/42']) {
    const local = page({url, enabled: false, storage}); local.boot(); local.visible();
    assert.equal(local.requests.length, 0);
  }
  const h = page({storage}); h.boot();
  assert.deepEqual(h.requests[0].body.items, {'b|1': 100000, 'poker:42': 100000});
});

test('full history fits the queue and uploads without keepalive', () => {
  const h = page({url: '/read?board=new&pid=1'});
  const items = {}, board = 'b'.repeat(80);
  for (let i = 1; i <= 1500; ++i) items[board + '|' + i] = i;
  h.put(store, items); h.boot();
  assert.equal(h.requests[0].keepalive, false);
  assert.equal(Object.keys(h.requests[0].body.items).length, 1500);
  assert.equal(h.requests[0].body.items[board + '|1'], undefined);
  assert.equal(h.get(meta).pending.length, 1500);
});

for (const status of [403, 404]) {
  test(`failure (${status || 'offline'}) keeps pending and stops this page`, async () => {
    const h = page({url: '/read?board=b&pid=1'}); h.boot();
    h.requests[0].fail(status); await settled();
    h.advance(31000); h.visible();
    assert.deepEqual(h.get(meta), {rev: 0, pending: ['b|1']});
    assert.equal(h.requests.length, 1);
  });
}

test('temporary errors retry without a visibility event; bad marks never poison the next batch', async () => {
  const h = page();
  h.put(store, {'b|1': 5, 'b|1234567890123456': 5});
  h.boot();
  for (const status of [503, 0]) {
    const last = h.requests.at(-1);
    last.fail(status); await settled();
    h.visible(); assert.equal(h.requests.at(-1), last);
    h.retry(); assert.notEqual(h.requests.at(-1), last);
    assert.deepEqual(h.requests.at(-1).body.items, {'b|1': 5});
    assert.deepEqual(h.get(meta).pending, ['b|1']);
  }
});

test('pagehide flushes new marks with keepalive and bfcache restoration pulls again', async () => {
  const h = page(); h.boot(); h.requests[0].reply(1, {}); await settled();
  h.put(store, {'b|1': 5}); h.put(meta, {rev: 1, pending: ['b|1']});
  h.emit('pagehide'); assert.equal(h.requests[1].keepalive, true);
  h.requests[1].reply(2, {}); await settled();
  h.advance(31000); h.emit('pageshow', {persisted:true});
  assert.equal(h.requests[2].keepalive, false);
});

test('a large pagehide flush stays pending and uploads normally after restore', async () => {
  const h = page(); h.boot(); h.requests[0].reply(1, {}); await settled();
  const items = {}, board = 'a'.repeat(80);
  for (let i = 1; i <= 600; ++i) items[board + '|' + i] = 5;
  h.put(store, items); h.put(meta, {rev: 1, pending: Object.keys(items)});
  h.emit('pagehide');
  assert.equal(h.requests.length, 1);
  h.emit('pageshow', {persisted:true});
  assert.deepEqual(h.requests[1].body.items, items);
  assert.equal(h.requests[1].keepalive, false);
});

test('a pagehide flush failing after restore retries without another visibility event', async () => {
  const h = page(); h.boot(); h.requests[0].reply(1, {}); await settled();
  h.put(store, {'b|1': 5}); h.put(meta, {rev: 1, pending: ['b|1']});
  h.emit('pagehide'); h.emit('pageshow', {persisted:true});
  assert.equal(h.requests.length, 2);
  h.requests[1].fail(503); await settled(); h.retry();
  assert.equal(h.requests[2].keepalive, false);
  assert.deepEqual(h.requests[2].body.items, {'b|1': 5});
});
