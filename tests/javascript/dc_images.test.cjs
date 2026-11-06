const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');

const code = fs.readFileSync(path.join(__dirname, '../../app/static/javascript/dc_images.js'), 'utf8');
const FAILED = '이미지를 불러오지 못했어요.';
const RETRYING = '이미지를 다시 불러오는 중이에요.';
const BUTTON = '다시 불러오기';

const readStateCode = fs.readFileSync(path.join(__dirname, '../../app/static/javascript/read_state.js'), 'utf8');
const MEDIA_KEY = 'mirror_media_block_mode_v1';
const SUSPENDED = 'data-body-image-retry-suspended';

// Small DOM with fake timers. Every img src write is logged in order (set/remove);
// the browser's answer is simulated with fail()/succeed(). With readState the real
// read_state.js runs after dc_images.js, exactly as on the read page.
function harness({ random = 0, readState = false, mode = 'none' } = {}) {
  const timers = new Map(), requests = [], log = [], outstanding = new Set(), docHandlers = {}, winHandlers = {};
  const storage = new Map([[MEDIA_KEY, mode]]);
  let now = 0, serial = 0, observer = null, pending = false, started = false;
  let document;
  const kebab = key => key.replace(/[A-Z]/g, c => '-' + c.toLowerCase());
  function parse(simple) {
    const bare = simple.replace(/\[[^\]]*\]/g, '');
    const tag = bare.match(/^[a-z]+/i);
    return {
      tag: tag && tag[0].toUpperCase(),
      id: (bare.match(/#([\w-]+)/) || [])[1],
      classes: [...bare.matchAll(/\.([\w-]+)/g)].map(m => m[1]),
      attrs: [...simple.matchAll(/\[([\w-]+)(?:([*^]?=)"([^"]*)")?\]/g)].map(m => m.slice(1)),
    };
  }
  function matchesSimple(node, s) {
    if (!node || node === document) return false;
    if (s.tag && node.tagName !== s.tag) return false;
    if (s.id && node.attrs.id !== s.id) return false;
    if (!s.classes.every(c => node.classList.contains(c))) return false;
    return s.attrs.every(([name, op, value]) => {
      const actual = node.getAttribute(name);
      if (actual === null) return false;
      return !op || (op === '=' ? actual === value : op === '*=' ? actual.includes(value) : actual.startsWith(value));
    });
  }
  function matches(node, selector) {
    return selector.split(',').some(part => {
      const parts = part.trim().split(/\s+/);
      if (!matchesSimple(node, parse(parts.pop()))) return false;
      let ancestor = node.parentNode;
      while (parts.length) {
        const s = parse(parts.pop());
        while (ancestor && ancestor !== document && !matchesSimple(ancestor, s)) ancestor = ancestor.parentNode;
        if (!ancestor || ancestor === document) return false;
        ancestor = ancestor.parentNode;
      }
      return true;
    });
  }
  function mutate(node, attribute) {
    if (!observer || !observer.target) return;
    if (attribute !== null && !observer.options.attributeFilter.includes(attribute)) return;
    for (let n = node; n; n = n.parentNode) if (n === observer.target) pending = true;
  }
  class Element {
    constructor(tag, attrs = {}) {
      const self = this;
      this.tagName = tag.toUpperCase(); this.nodeType = 1; this.attrs = {}; this.children = []; this.parentNode = null;
      this.listeners = {}; this.text = ''; this.complete = false; this.naturalWidth = 0; this.style = {};
      this.classList = {
        contains: name => self.className.split(/\s+/).includes(name),
        toggle(name, on) {
          const names = new Set(self.className.split(/\s+/).filter(Boolean));
          if (on === undefined) on = !names.has(name);
          if (on) names.add(name); else names.delete(name);
          self.className = [...names].join(' ');
          return on;
        },
        add: (...names) => names.forEach(n => self.classList.toggle(n, true)),
        remove: (...names) => names.forEach(n => self.classList.toggle(n, false)),
      };
      this.dataset = new Proxy({}, {
        get: (_, key) => typeof key === 'string' ? (self.getAttribute('data-' + kebab(key)) ?? undefined) : undefined,
        set: (_, key, value) => { self.setAttribute('data-' + kebab(key), value); return true; },
      });
      Object.entries(attrs).forEach(([k, v]) => { this.attrs[k] = String(v); });
    }
    get hidden() { return 'hidden' in this.attrs; }
    set hidden(value) { if (value) this.setAttribute('hidden', ''); else this.removeAttribute('hidden'); }
    get id() { return this.attrs.id || ''; }
    set id(value) { this.setAttribute('id', value); }
    get className() { return this.attrs.class || ''; }
    set className(value) { this.setAttribute('class', value); }
    set type(value) { this.setAttribute('type', value); }
    get textContent() { return this.text; }
    set textContent(value) { this.text = String(value); mutate(this, null); }
    getAttribute(name) { return name in this.attrs ? this.attrs[name] : null; }
    setAttribute(name, value) {
      this.attrs[name] = String(value);
      if (name === 'src' && this.tagName === 'IMG') {
        requests.push({ image: this, url: String(value) }); log.push({ op: 'set', image: this });
        outstanding.add(this); this.complete = false; this.naturalWidth = 0;
      }
      mutate(this, name);
    }
    removeAttribute(name) {
      if (!(name in this.attrs)) return;
      delete this.attrs[name];
      if (name === 'src' && this.tagName === 'IMG') {
        log.push({ op: 'remove', image: this }); outstanding.delete(this); this.complete = true; this.naturalWidth = 0;
      }
      mutate(this, name);
    }
    get isConnected() { let n = this; while (n.parentNode) n = n.parentNode; return n === document; }
    get nextSibling() { const s = this.parentNode.children; return s[s.indexOf(this) + 1] || null; }
    appendChild(child) { child.remove(); child.parentNode = this; this.children.push(child); mutate(this, null); return child; }
    insertBefore(child, ref) {
      if (!ref) return this.appendChild(child);
      child.remove(); child.parentNode = this; this.children.splice(this.children.indexOf(ref), 0, child); mutate(this, null);
      return child;
    }
    remove() {
      const parent = this.parentNode;
      if (!parent) return;
      parent.children.splice(parent.children.indexOf(this), 1); this.parentNode = null; mutate(parent, null);
    }
    contains(node) { for (let n = node; n; n = n.parentNode) if (n === this) return true; return false; }
    closest(selector) {
      for (let n = this; n && n !== document; n = n.parentNode) if (matches(n, selector)) return n;
      return null;
    }
    all() { return this.children.flatMap(child => [child, ...child.all()]); }
    querySelectorAll(selector) { return this.all().filter(n => matches(n, selector)); }
    querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
    addEventListener(type, fn) { (this.listeners[type] ||= []).push(fn); }
    focus() { document.activeElement = this; }
    click() {
      flush();
      const event = { type: 'click', target: this, preventDefault() {}, stopPropagation() {} };
      (this.listeners.click || []).forEach(fn => fn.call(this, event));
      flush();
    }
  }
  document = new Element('#document');
  document.documentElement = document.appendChild(new Element('html'));
  const body = document.documentElement.appendChild(new Element('body'));
  const article = body.appendChild(new Element('article', { id: 'article-body', class: 'article-body' }));
  const comments = body.appendChild(new Element('section', { class: 'comment-shell' }));
  document.body = body;
  document.readyState = 'complete';
  document.visibilityState = 'visible';
  document.createElement = tag => new Element(tag);
  document.getElementById = id => document.all().find(n => n.attrs.id === id) || null;
  document.addEventListener = (type, fn) => { (docHandlers[type] ||= []).push(fn); };
  const window = {
    location: new URL('https://mirror.example/read?board=test&pid=1'),
    localStorage: {
      getItem: key => storage.has(key) ? storage.get(key) : null,
      setItem: (key, value) => { storage.set(key, String(value)); },
      removeItem: key => { storage.delete(key); },
    },
    addEventListener(type, fn) { (winHandlers[type] ||= []).push(fn); },
  };
  class MutationObserver {
    constructor(callback) { assert.equal(observer, null, 'only dc_images.js observes the article'); observer = { callback }; }
    observe(target, options) {
      assert.equal(target, article); assert.ok(options.subtree && options.childList && options.attributes);
      observer.target = target; observer.options = options;
    }
  }
  // MutationObserver records are delivered as a microtask before the next task.
  function flush() {
    for (let rounds = 0; pending; rounds++) {
      assert.ok(rounds < 5, 'mutation observer must not feed itself');
      pending = false; observer.callback([]);
    }
  }
  const context = vm.createContext({
    document, window, WeakMap, Set, MutationObserver, URL, URLSearchParams,
    Math: Object.assign(Object.create(Math), { random: () => random }),
    setTimeout(fn, delay) { timers.set(++serial, { fn, at: now + delay }); return serial; },
    clearTimeout(id) { timers.delete(id); },
  });
  function start() {
    assert.equal(started, false); started = true;
    vm.runInContext(code, context);
    if (readState) vm.runInContext(readStateCode, context);
    flush();
  }
  function advance(ms) {
    const end = now + ms;
    for (;;) {
      flush();
      const next = [...timers].filter(([, t]) => t.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
      if (!next) break;
      timers.delete(next[0]); now = next[1].at; next[1].fn();
    }
    flush(); now = end;
  }
  function emit(type, target) { flush(); (docHandlers[type] || []).forEach(fn => fn({ type, target })); flush(); }
  // Server-rendered markup: hidden, no src; read_state.js later hydrates it.
  function dcImage(n, { parent = article, linked = false } = {}) {
    const host = linked ? parent.appendChild(new Element('a', { href: 'https://example.com/original' })) : parent;
    return host.appendChild(new Element('img', {
      class: 'body-image', 'data-body-image-src': `/media?src=https%3A%2F%2Fdcimg.example%2F${n}&board=test&pid=1`,
      alt: `본문 사진 ${n}`, loading: 'lazy', decoding: 'async', hidden: '',
    }));
  }
  // Without read_state.js: the same attribute writes it uses to show and to block an image.
  function show(image) { image.setAttribute('src', image.getAttribute('data-body-image-src')); image.hidden = false; flush(); }
  function block(image) { image.removeAttribute('src'); image.hidden = true; flush(); }
  function fail(image) { outstanding.delete(image); image.complete = true; image.naturalWidth = 0; emit('error', image); }
  function succeed(image) { outstanding.delete(image); image.complete = true; image.naturalWidth = 640; emit('load', image); }
  function rows() { return document.all().filter(n => n.classList.contains('body-image-retry')); }
  function rowOf(image) {
    const host = image.closest('a') || image;
    const row = host.parentNode && host.nextSibling;
    return row && row.classList.contains('body-image-retry') ? row : null;
  }
  function ui(image) {
    const row = rowOf(image);
    if (!row || row.hidden) return 'none';
    const [text, button] = row.children;
    return `${row.getAttribute('data-state')}|${text.textContent}|${button.hidden ? 'no-button' : button.textContent + ':' + button.getAttribute('aria-disabled')}`;
  }
  function button(image) { return rowOf(image).children[1]; }
  const h = { requests, log, outstanding, article, comments, Element, start, advance, emit, dcImage, show, block, fail, succeed,
    rows, rowOf, ui, button,
    page(type) { flush(); (winHandlers[type] || []).forEach(fn => fn({ type, persisted: true })); flush(); },
    visible() { flush(); (docHandlers.visibilitychange || []).forEach(fn => fn({ type: 'visibilitychange' })); flush(); },
    // A media-block change from another tab: read_state.js re-applies the policy.
    policy(next) { storage.set(MEDIA_KEY, next); flush(); (winHandlers.storage || []).forEach(fn => fn({ key: MEDIA_KEY })); flush(); },
    setsSince(index) { return log.slice(index).filter(e => e.op === 'set').map(e => e.image); },
    indexOf(op, image, from = 0) { return log.findIndex((e, i) => i >= from && e.op === op && e.image === image); } };
  if (!readState) start();
  return h;
}
const count = (h, image) => h.requests.filter(r => r.image === image).length;

// Failure -> two delayed retries -> accessible manual retry -> manual failure -> success.
{
  const h = harness(), image = h.dcImage(1), url = image.getAttribute('data-body-image-src');
  h.show(image); assert.equal(count(h, image), 1);
  h.fail(image); assert.equal(h.ui(image), `retrying|${RETRYING}|no-button`);
  h.advance(1499); assert.equal(count(h, image), 1);
  h.advance(1); assert.equal(count(h, image), 2);
  h.fail(image); h.advance(3999); assert.equal(count(h, image), 2);
  h.advance(1); assert.equal(count(h, image), 3);
  h.fail(image); assert.equal(h.ui(image), `failed|${FAILED}|${BUTTON}:false`);
  h.advance(600000); assert.equal(count(h, image), 3);
  const row = h.rowOf(image), button = h.button(image);
  assert.equal(button.tagName, 'BUTTON'); assert.equal(button.getAttribute('type'), 'button');
  assert.equal(button.getAttribute('aria-describedby'), row.children[0].id);
  assert.equal(row.children[0].getAttribute('role'), 'status');
  button.click(); assert.equal(count(h, image), 4);
  assert.equal(h.ui(image), `loading|${RETRYING}|${BUTTON}:true`);
  button.click(); button.click(); assert.equal(count(h, image), 4);
  h.fail(image); h.fail(image); assert.equal(h.ui(image), `failed|${FAILED}|${BUTTON}:false`);
  h.advance(600000); assert.equal(count(h, image), 4, 'manual failure must not restart automatic retries');
  button.click(); assert.equal(count(h, image), 5);
  h.succeed(image); assert.equal(h.ui(image), 'none'); assert.equal(image.hidden, false);
  h.advance(600000); assert.equal(count(h, image), 5);
  assert.ok(h.requests.every(r => r.url === url), 'retries reuse the original source URL');
  assert.equal(image.getAttribute('alt'), '본문 사진 1');
}
// The row follows the source link instead of nesting a button inside it; jitter stays small.
{
  const h = harness({ random: 0.999 }), image = h.dcImage(2, { linked: true });
  h.show(image); h.fail(image);
  const row = h.rowOf(image);
  assert.equal(row.parentNode, h.article); assert.equal(row.closest('a'), null);
  h.advance(1499); assert.equal(count(h, image), 1);
  h.advance(501); assert.equal(count(h, image), 2);
}
// A burst with duplicate and stale events keeps at most two retry requests in flight.
{
  const h = harness(), images = Array.from({ length: 10 }, (_, i) => h.dcImage(i));
  images.forEach(n => h.show(n)); const initial = h.requests.length;
  images.forEach(n => { h.fail(n); h.fail(n); });
  h.advance(1499); assert.equal(h.requests.length, initial);
  h.advance(1); assert.equal(h.requests.length, initial + 2);
  h.advance(40000); assert.equal(h.requests.length, initial + 2, 'queued retries wait for a free slot');
  for (let i = 0; i < images.length; i++) {
    const inflight = h.requests.slice(initial).map(r => r.image).filter(n => !n.naturalWidth);
    assert.ok(inflight.length <= 2);
    h.succeed(inflight[0]); h.emit('load', inflight[0]);
  }
  assert.equal(h.requests.length, initial + 10);
  assert.ok(images.every(n => h.ui(n) === 'none'));
  h.advance(600000); assert.equal(h.requests.length, initial + 10);
}
// Blocking cancels waiting, queued and in-flight work, hides the row and never loads the image.
{
  const h = harness(), [a, b, c] = [h.dcImage(1), h.dcImage(2), h.dcImage(3)];
  [a, b, c].forEach(n => { h.show(n); h.fail(n); });
  h.advance(1500); assert.equal(count(h, a) + count(h, b) + count(h, c), 5);
  h.block(a); assert.equal(h.ui(a), 'none'); assert.equal(a.getAttribute('src'), null);
  assert.equal(count(h, c), 2, 'a blocked in-flight retry frees its slot');
  h.block(b); h.block(c); h.advance(600000);
  assert.deepEqual([a, b, c].map(n => [count(h, n), n.getAttribute('src'), n.hidden]), [[2, null, true], [2, null, true], [2, null, true]]);
  h.button(a).click(); assert.equal(count(h, a), 2, 'a hidden row cannot load a blocked image');
  // Showing again is the user's own load; a failure resumes the remaining retry budget only.
  h.show(a); assert.equal(count(h, a), 3); h.fail(a);
  h.advance(4000); assert.equal(count(h, a), 4); h.fail(a);
  assert.equal(h.ui(a), `failed|${FAILED}|${BUTTON}:false`);
  h.block(a); assert.equal(h.ui(a), 'none'); h.show(a); h.fail(a);
  assert.equal(h.ui(a), `failed|${FAILED}|${BUTTON}:false`); h.advance(600000); assert.equal(count(h, a), 5);
}
// Removed images and changed sources are forgotten together with their rows.
for (const change of [
  n => n.remove(),
  n => n.setAttribute('data-body-image-src', '/media?src=other&board=test&pid=1'),
  n => n.setAttribute('src', '/media?src=other&board=test&pid=1'),
]) {
  for (const stage of ['waiting', 'inflight']) {
    const h = harness(), image = h.dcImage(1), other = h.dcImage(2), third = h.dcImage(3);
    [image, other, third].forEach(n => { h.show(n); h.fail(n); });
    if (stage === 'inflight') h.advance(1500);
    change(image); h.advance(0);
    assert.equal(h.rowOf(image), null); assert.equal(h.rows().length, 2);
    const before = count(h, image);
    h.fail(image); h.advance(600000);
    assert.equal(count(h, image), before, 'no retry for a removed image or a changed source');
    if (stage === 'inflight') assert.equal(count(h, third), 2, 'removal frees the slot for the queued image');
  }
}
// A stalled retry is canceled by the watchdog, frees its slot and offers manual recovery.
{
  const h = harness(), images = [1, 2, 3].map(n => h.dcImage(n));
  images.forEach(n => { h.show(n); h.fail(n); });
  h.advance(1500); assert.deepEqual(images.map(n => count(h, n)), [2, 2, 1]);
  h.advance(44999); assert.deepEqual(images.map(n => count(h, n)), [2, 2, 1]);
  h.advance(1);
  assert.deepEqual(images.map(n => count(h, n)), [2, 2, 2]);
  assert.equal(images[0].getAttribute('src'), null); assert.equal(h.ui(images[0]), `failed|${FAILED}|${BUTTON}:false`);
  h.emit('error', images[0]); h.advance(600000);
  assert.deepEqual(images.map(n => count(h, n)), [2, 2, 2], 'stale errors and stalls never add automatic retries');
  assert.ok(images.every(n => n.getAttribute('src') === null));
  h.button(images[0]).click(); assert.equal(count(h, images[0]), 3); h.fail(images[0]);
  h.advance(600000); assert.equal(count(h, images[0]), 3, 'a failed manual retry is a single attempt');
  h.button(images[0]).click(); assert.equal(count(h, images[0]), 4); h.succeed(images[0]); assert.equal(h.ui(images[0]), 'none');
}
// After a stalled first retry or a page exit, a duplicate error for the failed manual retry
// does not restart the remaining automatic budget; unblocking and a new source still do.
for (const interrupt of [h => h.advance(45000), h => { h.page('pagehide'); h.page('pageshow'); }]) {
  const h = harness(), image = h.dcImage(1);
  h.show(image); h.fail(image); h.advance(1500); assert.equal(count(h, image), 2);
  interrupt(h); assert.equal(h.ui(image), `failed|${FAILED}|${BUTTON}:false`);
  h.button(image).click(); assert.equal(count(h, image), 3);
  h.fail(image); h.fail(image); h.advance(600000);
  assert.equal(count(h, image), 3, 'a duplicate failed-state error must not schedule a retry');
  assert.equal(h.ui(image), `failed|${FAILED}|${BUTTON}:false`);
  // The user's own reload after unblocking resumes the one remaining automatic retry.
  h.block(image); h.show(image); assert.equal(count(h, image), 4); h.fail(image);
  h.advance(4500); assert.equal(count(h, image), 5); h.fail(image); h.advance(600000);
  assert.equal(count(h, image), 5);
  // A new source is a new image with its own retry budget.
  const fresh = '/media?src=fresh&board=test&pid=1';
  image.setAttribute('data-body-image-src', fresh); image.setAttribute('src', fresh); h.advance(0);
  h.fail(image); h.advance(2000);
  assert.deepEqual(h.requests.slice(-2).map(r => r.url), [fresh, fresh]);
}
// Leaving the page cancels timers, queue and requests; a restored page recovers by hand.
{
  const h = harness(), images = [1, 2, 3].map(n => h.dcImage(n));
  images.forEach(n => { h.show(n); h.fail(n); }); h.advance(1500);
  h.page('pagehide'); h.advance(600000);
  assert.deepEqual(images.map(n => count(h, n)), [2, 2, 1]);
  assert.ok(images.every(n => h.ui(n) === `failed|${FAILED}|${BUTTON}:false`));
  assert.equal(images[0].getAttribute('src'), null);
  h.button(images[2]).click(); assert.equal(count(h, images[2]), 1);
  h.page('pageshow'); h.button(images[2]).click(); assert.equal(count(h, images[2]), 2);
}
// Poker, DCCon, link preview, comment and non-image failures are untouched.
{
  const h = harness();
  const add = (parent, attrs) => { const n = parent.appendChild(new h.Element('img', attrs)); n.setAttribute('src', attrs.s); return n; };
  const others = [
    add(h.article, { class: 'body-image', 'data-body-image-src': '/poker/media?src=x&sig=y', s: '/poker/media?src=x&sig=y' }),
    add(h.article, { class: 'dccon body-dccon', 'data-dccon-src': '/media?src=dccon', s: '/media?src=dccon' }),
    add(h.article, { class: 'link-preview-image', 'data-preview-image-src': '/embed/link-preview-image?url=x', s: '/embed/link-preview-image?url=x' }),
    add(h.comments, { class: 'body-image', 'data-body-image-src': '/media?src=comment', s: '/media?src=comment' }),
  ];
  const before = h.requests.length;
  others.forEach(n => h.fail(n)); h.emit('error', new h.Element('script'));
  h.advance(600000);
  assert.equal(h.requests.length, before); assert.equal(h.rows().length, 0);
}
// Both scripts: a stalled retry stays manual-only across tab-return and bfcache resyncs,
// and manual recovery still keeps at most two retry requests active.
{
  const h = harness({ readState: true }), images = [1, 2, 3].map(n => h.dcImage(n));
  h.start();
  assert.deepEqual(images.map(n => [count(h, n), n.hidden]), [[1, false], [1, false], [1, false]], 'read_state.js hydrates after the listener');
  images.forEach(n => h.fail(n));
  h.advance(1500); assert.deepEqual(images.map(n => count(h, n)), [2, 2, 1]);
  const beforeStall = h.log.length;
  h.advance(45000);
  assert.ok(h.indexOf('remove', images[0], beforeStall) < h.indexOf('set', images[2], beforeStall), 'cancel before the queue advances');
  assert.deepEqual(images.map(n => [n.getAttribute('src') === null, n.getAttribute(SUSPENDED) !== null]), [[true, true], [true, true], [false, false]]);
  const beforeResync = h.log.length;
  h.visible(); h.page('pageshow'); h.advance(0);
  assert.deepEqual(h.setsSince(beforeResync), [], 'automatic resync must not reload a suspended image');
  assert.ok([images[0], images[1]].every(n => h.ui(n) === `failed|${FAILED}|${BUTTON}:false`));
  h.button(images[0]).click(); h.button(images[1]).click();
  assert.deepEqual(h.setsSince(beforeResync), [images[0]]); assert.equal(h.outstanding.size, 2);
  h.fail(images[2]); assert.deepEqual(h.setsSince(beforeResync), [images[0], images[1]]); assert.equal(h.outstanding.size, 2);
  h.advance(4500); assert.equal(h.outstanding.size, 2, 'the third image waits for a free slot');
  h.succeed(images[0]); assert.equal(count(h, images[2]), 3); assert.equal(h.outstanding.size, 2);
  h.visible(); h.page('pageshow'); assert.ok(h.outstanding.size <= 2);
}
// Both scripts: a page exit stays manual-only after restore; the block policy stays
// authoritative and an explicit show or global unblock is the user's own reload.
{
  const h = harness({ readState: true }), [a, b] = [h.dcImage(1), h.dcImage(2)];
  h.start(); h.fail(a); h.fail(b); h.advance(1500);
  h.page('pagehide');
  assert.deepEqual([a, b].map(n => [n.getAttribute('src'), n.getAttribute(SUSPENDED)]), [[null, ''], [null, '']]);
  const beforeRestore = h.log.length;
  h.page('pageshow'); h.visible(); h.advance(600000);
  assert.deepEqual(h.setsSince(beforeRestore), []);
  h.policy('body');
  assert.deepEqual([a, b].map(n => [n.hidden, n.getAttribute('src'), n.getAttribute(SUSPENDED), h.ui(n)]),
    [[true, null, null, 'none'], [true, null, null, 'none']]);
  h.button(a).click(); assert.equal(h.ui(a), 'none', 'a hidden row cannot load a blocked image');
  const toggleA = h.article.querySelector(`.body-media-item-toggle[aria-controls="${a.id}"]`);
  toggleA.click(); assert.deepEqual(h.setsSince(beforeRestore), [a]); assert.equal(b.getAttribute('src'), null);
  h.fail(a); h.advance(4500); assert.deepEqual(h.setsSince(beforeRestore), [a, a], 'the unused retry resumes after an explicit show');
  h.policy('none'); assert.deepEqual(h.setsSince(beforeRestore), [a, a, b]);
  h.fail(b); h.advance(4500); assert.deepEqual(h.setsSince(beforeRestore), [a, a, b, b]);
}
// Both scripts: a suspended image whose manual retry is queued stays recoverable when an
// ancestor is hidden and shown again, even after resyncs and freed slots.
{
  const h = harness({ readState: true });
  const [a, b, c] = [1, 2, 3].map(n => h.dcImage(n, { parent: h.article.appendChild(new h.Element('p')) }));
  h.start(); [a, b, c].forEach(n => h.fail(n));
  h.advance(1500); h.advance(45000);
  assert.deepEqual([a, b].map(n => [n.getAttribute('src'), n.getAttribute(SUSPENDED)]), [[null, ''], [null, '']]);
  h.button(b).click(); h.button(a).click();
  assert.equal(h.outstanding.size, 2); assert.equal(h.ui(a), `loading|${RETRYING}|${BUTTON}:true`);
  a.parentNode.hidden = true; h.advance(0); assert.equal(h.ui(a), 'none');
  a.parentNode.hidden = false; h.advance(0);
  h.fail(c); h.succeed(b); h.visible(); h.page('pageshow'); h.advance(600000);
  assert.deepEqual([a.getAttribute('src'), count(h, a)], [null, 2], 'neither the freed slot nor a resync loads it');
  assert.equal(h.ui(a), `failed|${FAILED}|${BUTTON}:false`);
  h.button(a).click(); assert.equal(count(h, a), 3); h.succeed(a); assert.equal(h.ui(a), 'none');
}
// Relinquished in-flight retries are canceled before the slot goes to the next image;
// a replacement source or an already received image is left alone.
for (const [name, change] of [
  ['removal', n => n.remove()],
  ['hidden ancestor', n => { n.parentNode.hidden = true; }],
  ['source only', n => n.setAttribute('data-body-image-src', '/media?src=fresh&board=test&pid=1')],
  ['replacement src', n => n.setAttribute('src', '/media?src=replacement&board=test&pid=1')],
  ['loaded, event pending', (n, h) => { h.outstanding.delete(n); n.complete = true; n.naturalWidth = 640; n.parentNode.hidden = true; }],
]) {
  const h = harness(), wrappers = [1, 2, 3].map(() => h.article.appendChild(new h.Element('p')));
  const [a, b, c] = wrappers.map((p, i) => h.dcImage(i + 1, { parent: p }));
  [a, b, c].forEach(n => { h.show(n); h.fail(n); });
  h.advance(1500); assert.deepEqual([a, b, c].map(n => count(h, n)), [2, 2, 1]);
  const before = h.log.length, srcBefore = name === 'replacement src' ? null : a.getAttribute('src');
  change(a, h); h.advance(0);
  const canceled = h.indexOf('remove', a, before), next = h.indexOf('set', c, before);
  assert.ok(next !== -1, `${name}: the queued image gets the freed slot`);
  if (name === 'replacement src' || name.startsWith('loaded')) {
    assert.equal(canceled, -1, `${name}: not this module's request to cancel`);
    assert.equal(a.getAttribute('src'), name === 'replacement src' ? '/media?src=replacement&board=test&pid=1' : srcBefore);
  } else {
    assert.ok(canceled !== -1 && canceled < next, `${name}: cancel before the queue advances`);
    assert.ok(!h.outstanding.has(a));
  }
  assert.deepEqual([...h.outstanding].sort((x, y) => x.attrs.alt.localeCompare(y.attrs.alt)), name === 'replacement src' ? [a, b, c] : [b, c]);
  if (name === 'hidden ancestor') {
    a.parentNode.hidden = false; h.advance(0);
    assert.equal(h.ui(a), `failed|${FAILED}|${BUTTON}:false`); assert.equal(a.getAttribute(SUSPENDED), '');
  }
  if (name === 'source only') assert.equal(a.getAttribute(SUSPENDED), null, 'a new source may be hydrated again');
  if (name.startsWith('loaded')) { a.parentNode.hidden = false; h.advance(0); assert.equal(h.ui(a), 'none'); }
}
console.log('dc_images: retries, manual recovery, burst limit, blocking, source change, removal, watchdog, page exit, resync suspension with read_state.js, cancellation order and isolation passed');
