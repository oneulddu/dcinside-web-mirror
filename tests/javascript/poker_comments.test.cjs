const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const root = path.resolve(__dirname, '../..');
const code = fs.readFileSync(path.join(root, 'app/static/javascript/poker_comments.js'), 'utf8');
const mediaCode = fs.readFileSync(path.join(root, 'app/templates/poker/read.html'), 'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];

class Element {
  constructor(tag, attrs = {}) {
    this.tagName = tag.toUpperCase(); this.attrs = { ...attrs }; this.children = [];
    this.listeners = {}; this.hidden = false; this.style = {}; this.textContent = '';
    this.dataset = new Proxy({}, {
      get: (_, k) => this.attrs['data-' + k.replace(/[A-Z]/g, c => '-' + c.toLowerCase())],
      set: (_, k, v) => { this.attrs['data-' + k.replace(/[A-Z]/g, c => '-' + c.toLowerCase())] = String(v); return true; }
    });
  }
  get id() { return this.attrs.id || ''; }
  set id(v) { this.attrs.id = v; }
  get className() { return this.attrs.class || ''; }
  set className(v) { this.attrs.class = v; }
  get firstChild() { return this.children[0] || null; }
  getAttribute(k) { return this.attrs[k] === undefined ? null : this.attrs[k]; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  removeAttribute(k) { delete this.attrs[k]; }
  appendChild(n) { return this.insertBefore(n, null); }
  insertBefore(n, ref) { if (n.parentNode) n.parentNode.removeChild(n); const i = ref ? this.children.indexOf(ref) : this.children.length; this.children.splice(i, 0, n); n.parentNode = this; n.doc = this.doc; return n; }
  removeChild(n) { this.children.splice(this.children.indexOf(n), 1); n.parentNode = null; }
  contains(n) { return n === this || this.children.some(c => c.contains(n)); }
  matches(s) {
    return s.split(',').some(sel => {
      sel = sel.trim(); const tag = sel.match(/^[a-z]+/i); if (tag && this.tagName !== tag[0].toUpperCase()) return false;
      const id = sel.match(/#([\w-]+)/); if (id && this.id !== id[1]) return false;
      for (const m of sel.matchAll(/\.([\w-]+)/g)) if (!this.className.split(' ').includes(m[1])) return false;
      for (const m of sel.matchAll(/\[([\w-]+)(?:=['"]([^'"]*)['"])?\]/g)) if (this.getAttribute(m[1]) === null || (m[2] !== undefined && this.getAttribute(m[1]) !== m[2])) return false;
      return true;
    });
  }
  querySelectorAll(s) { return this.children.flatMap(c => [...(c.matches(s) ? [c] : []), ...c.querySelectorAll(s)]); }
  querySelector(s) { return this.querySelectorAll(s)[0] || null; }
  closest(s) { for (let n = this; n; n = n.parentNode) if (n.matches(s)) return n; return null; }
  addEventListener(k, fn) { (this.listeners[k] ||= []).push(fn); }
  dispatchEvent(e) { for (const fn of this.listeners[e.type] || []) fn(e); }
  focus() { this.doc.activeElement = this; this.doc.focuses++; }
  getBoundingClientRect() {
    const list = this.doc.list, top = this.doc.listTop - this.doc.scroll;
    if (this === list) return { top, bottom: top + list.children.length * 20 };
    if (this.parentNode === list) { const y = top + list.children.indexOf(this) * 20; return { top: y, bottom: y + 20 }; }
    return { top: 0, bottom: 20 };
  }
}
function harness({ next = 1, total = 49, ids = Array.from({length:21}, (_,i) => 'C'+(i+29)), hidden = false, listTop = 1000 } = {}) {
  const document = new Element('document'); document.doc = document; document.hidden = hidden;
  document.readyState = 'complete'; document.activeElement = null; document.focuses = 0; document.scroll = 0; document.listTop = listTop;
  document.documentElement = new Element('html'); document.documentElement.doc = document;
  document.documentElement.dataset.mediaBlockMode = 'all';
  document.getElementById = id => document.querySelector('#'+id);
  const section = document.appendChild(new Element('section', { id:'comment', 'data-comments-url':'/poker/best/123/comments', 'data-next-page':next === null ? '' : String(next), 'data-total':total === null ? '' : String(total) }));
  const heading = section.appendChild(new Element('h2', {id:'comment-title'}));
  const count = heading.appendChild(new Element('span', {class:'comment-count'})); count.textContent=String(total);
  const notice = section.appendChild(new Element('div', {'data-poker-comments-notice':''}));
  const noticeText = notice.appendChild(new Element('p', {'data-poker-comments-notice-text':''}));
  const list = section.appendChild(new Element('ul', {id:'poker-comment-list'})); document.list=list;
  ids.forEach(id => list.appendChild(new Element('li', {'data-comment-id':id})));
  const status = section.appendChild(new Element('div', {'data-poker-comments-status':''}));
  const progress = status.appendChild(new Element('p', {'data-poker-comments-progress':''}));
  const retry = status.appendChild(new Element('button', {'data-poker-comments-retry':''})); retry.hidden=true;
  const live = section.appendChild(new Element('p', {'data-poker-comments-live':''}));
  const postList = document.appendChild(new Element('section', {id:'poker-post-list'}));
  const requests = [], timers = new Map(); let timerId=0, now=0;
  const window = new Element('window'); window.innerHeight=600; window.location={href:'https://mirror.test/poker/best/123'};
  window.scrollBy=(_, delta) => { document.scroll += delta; };
  document.createElement = tag => {
    const el = new Element(tag); el.doc=document;
    if (tag === 'template') {
      el.content = new Element('fragment'); el.content.doc=document;
      Object.defineProperty(el, 'innerHTML', {set(value) {
        el.content.children=[];
        for (const m of value.matchAll(/<li\b([^>]*)>[\s\S]*?<\/li>/g)) {
          const attrs={}; for (const a of m[1].matchAll(/([\w-]+)="([^"]*)"/g)) attrs[a[1]]=a[2];
          const row = el.content.appendChild(new Element('li', attrs));
          if (/<script/.test(m[0])) row.appendChild(new Element('script'));
        }
      }});
    }
    return el;
  };
  const context = {
    document, window, URL, AbortController, console,
    CustomEvent: class { constructor(type, options) { this.type=type; this.detail=options.detail; } },
    MutationObserver: class { constructor(fn) { document.mutate=fn; } observe() {} },
    requestAnimationFrame: fn => fn(),
    setTimeout(fn, delay) { timers.set(++timerId, {fn, at:now+delay}); return timerId; },
    clearTimeout(id) { timers.delete(id); },
    fetch(url, init) { const r={url, init}; r.promise=new Promise((resolve,reject)=> {r.resolve=resolve;r.reject=reject;}); requests.push(r); return r.promise; }
  };
  vm.createContext(context); vm.runInContext(code, context);
  async function flush() { for(let i=0;i<8;i++) await new Promise(setImmediate); }
  async function advance(ms) {
    const end=now+ms;
    while(true) { const due=[...timers].filter(([,t])=>t.at<=end).sort((a,b)=>a[1].at-b[1].at)[0]; if(!due) break; timers.delete(due[0]); now=due[1].at; due[1].fn(); await flush(); }
    now=end; await flush();
  }
  async function resolve(index,payload,{status=200,type='application/json'}={}) {
    requests[index].resolve({ok:status>=200&&status<300,status,headers:{get:()=>type},json:()=>payload instanceof Error ? Promise.reject(payload) : Promise.resolve(payload)}); await flush();
  }
  return {document,window,section,heading,count,notice,noticeText,list,status,progress,retry,live,postList,requests,context,flush,advance,resolve,
    ids:()=>list.children.map(n=>n.getAttribute('data-comment-id')), click:()=>retry.dispatchEvent({type:'click'})};
}
const row = id => ({id,html:'<li class="comment-item" data-comment-id="'+id+'"><p>'+id+'</p></li>'});
const payload=(page,ids,total=49)=>({page,next_page:page>1?page-1:null,total,comments:ids.map(row)});
const cases=[]; const test=(name,fn)=>cases.push({name,fn});

test('automatic merge deduplicates IDs, keeps oldest page first and completes49 without focus changes',async()=>{
  const h=harness(); assert.equal(h.requests.length,1); assert.match(h.requests[0].url,/cpage=1/);
  const ids=Array.from({length:28},(_,i)=>'C'+(i+1));
  await h.resolve(0,payload(1,[...ids,'C1','C29']));
  assert.deepEqual(h.ids(),Array.from({length:49},(_,i)=>'C'+(i+1)));
  assert.equal(h.section.dataset.commentsComplete,'1'); assert.equal(h.notice.hidden,true); assert.equal(h.document.focuses,0);
});
test('three-page sequence waits one second and never fetches concurrently',async()=>{
  const h=harness({next:2,total:3,ids:['C3']});
  await h.resolve(0,payload(2,['C2'],3)); assert.equal(h.requests.length,1);
  await h.advance(999); assert.equal(h.requests.length,1); await h.advance(1); assert.equal(h.requests.length,2);
  await h.resolve(1,payload(1,['C1'],3)); assert.deepEqual(h.ids(),['C1','C2','C3']); assert.equal(h.section.dataset.commentsComplete,'1');
});
test('failure preserves rows and retry starts at failed cursor',async()=>{
  const h=harness({next:2,total:3,ids:['C3']}); await h.resolve(0,payload(2,['C2'],3)); await h.advance(1000);
  await h.resolve(1,{error:'잠시 후 다시'}, {status:503}); assert.deepEqual(h.ids(),['C2','C3']);
  await h.advance(60000); assert.equal(h.requests.length,2); h.click(); assert.match(h.requests[2].url,/cpage=1/);
  await h.resolve(2,payload(1,['C1'],3)); assert.equal(h.section.dataset.commentsComplete,'1');
});
test('upstream comment block ends collection as partial without retry or more requests',async()=>{
  const h=harness({next:2,total:3,ids:['C3']}); await h.resolve(0,payload(2,['C2'],3)); await h.advance(1000);
  await h.resolve(1,{error:'이전 댓글은 지금 원본에서 가져올 수 없어요.',code:'comments_blocked'},{status:503});
  assert.deepEqual(h.ids(),['C2','C3']); assert.equal(h.retry.hidden,true);
  assert.equal(h.section.dataset.commentsState,'done'); assert.equal(h.section.dataset.commentsComplete,'0');
  await h.advance(60000); assert.equal(h.requests.length,2);
});
test('timeout and stale response never mutate rows or overwrite successful retry',async()=>{
  const h=harness({total:2,ids:['C2']}); await h.advance(26000); assert.equal(h.requests[0].init.signal.aborted,true); h.click();
  await h.resolve(1,payload(1,['C1'],2)); await h.resolve(0,payload(1,['C9'],2)); assert.deepEqual(h.ids(),['C1','C2']);
});
test('invalid content, JSON, cursor and row all fail without insertion',async()=>{
  for(const response of [
    [{error:'<script>bad</script>'},{status:429,type:'text/html'}], [new Error('bad JSON'),{}],
    [{...payload(1,['C1'],2),page:2},{}], [{...payload(1,['C1'],2),next_page:1},{}],
    [{...payload(1,['C1'],2),comments:[row('C1'),{id:'C2',html:row('C9').html}]},{}],
    [{...payload(2,['C1'],2),next_page:null},{}]
  ]) {
    const h=harness({next:response[0]?.page===2?2:1,total:2,ids:['C3']}); await h.resolve(0,...response);
    assert.equal(h.section.dataset.commentsState,'error'); assert.deepEqual(h.ids(),['C3']);
  }
});
test('hidden tabs and list loading pause requests, pagehide aborts, restored pages resume only interruption',async()=>{
  const h=harness({hidden:true,total:2,ids:['C2']}); assert.equal(h.requests.length,0);
  h.postList.dataset.state='loading'; h.document.hidden=false; h.document.dispatchEvent({type:'visibilitychange'}); assert.equal(h.requests.length,0);
  h.postList.dataset.state='ready'; await h.advance(300); assert.equal(h.requests.length,1);
  h.window.dispatchEvent({type:'pagehide'}); assert.equal(h.requests[0].init.signal.aborted,true);
  await h.resolve(0,payload(1,['C9'],2)); assert.deepEqual(h.ids(),['C2']);
  h.window.dispatchEvent({type:'pageshow',persisted:true}); assert.equal(h.requests.length,2);
  await h.resolve(1,{error:'failure'},{status:503}); h.document.hidden=true; h.document.hidden=false;
  h.document.dispatchEvent({type:'visibilitychange'}); h.window.dispatchEvent({type:'pageshow',persisted:true}); assert.equal(h.requests.length,2);
});
test('known complete single page and unknown cursor never request',async()=>{
  for(const total of [1,null]) assert.equal(harness({next:null,total,ids:['C1']}).requests.length,0);
});
test('changed total stays honestly partial and updates visible count',async()=>{
  const h=harness({total:2,ids:['C2']}); await h.resolve(0,payload(1,['C1'],3));
  assert.equal(h.section.dataset.commentsComplete,'0'); assert.equal(h.notice.hidden,false); assert.equal(h.count.textContent,'3');
});
test('missing total in a later response cannot reuse an old count to claim completion',async()=>{
  const h=harness({total:2,ids:['C2']}); await h.resolve(0,payload(1,['C1'],null));
  assert.equal(h.section.dataset.commentsComplete,'0'); assert.equal(h.notice.hidden,false);
  assert.equal(h.count.textContent,'2'); assert.doesNotMatch(h.live.textContent,/모두/);
});
test('prepend preserves an existing row at top but does not scroll away from a visible article',async()=>{
  const h=harness({total:3,ids:['C2','C3'],listTop:-10}); const old=h.list.children[0]; const before=old.getBoundingClientRect().top;
  await h.resolve(0,payload(1,['C1'],3)); assert.equal(old.getBoundingClientRect().top,before);
  const top=harness({total:2,ids:['C2'],listTop:300}); await top.resolve(0,payload(1,['C1'],2)); assert.equal(top.document.scroll,0);
});
test('initial zero images can receive blocked image, reveal once, keep override and unique controls',async()=>{
  const h=harness({next:null,total:1,ids:['C1']});
  // The media controller uses a descendant selector; this fixture exposes only matching images.
  const ordinaryQuery=h.document.querySelectorAll.bind(h.document); const images=[];
  h.document.querySelectorAll=s=>s.startsWith('.poker-comment-body img')?images:ordinaryQuery(s);
  vm.runInContext(mediaCode,h.context);
  const container=h.list.children[0].appendChild(new Element('div'));
  const img=container.appendChild(new Element('img',{'data-body-image-src':'/poker/media?src=x&sig=y',class:'body-image'})); images.push(img);
  h.document.dispatchEvent({type:'poker:comments-added'}); assert.equal(img.getAttribute('src'),null); assert.equal(img.hidden,true);
  const button=container.children[0]; button.dispatchEvent({type:'click',preventDefault(){}}); assert.equal(img.getAttribute('src'),'/poker/media?src=x&sig=y');
  h.document.dispatchEvent({type:'poker:comments-added'}); assert.equal(container.children.length,2); assert.equal(img.hidden,false);
  h.document.documentElement.dataset.mediaBlockMode='none'; h.document.mutate(); assert.equal(button.hidden,true);
});
(async()=>{for(const {name,fn} of cases){try{await fn();}catch(e){console.error(name);throw e;}}console.log('poker_comments_contract=passed ('+cases.length+')');})().catch(e=>{console.error(e);process.exitCode=1;});
