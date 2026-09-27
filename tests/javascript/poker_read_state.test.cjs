const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const assert = require('node:assert/strict');
// Run the real shared implementation; expose existing functions only in this isolated test copy.
const source = fs.readFileSync(path.resolve(__dirname, '../../app/static/javascript/read_state.js'), 'utf8')
  .replace(/\}\)\(\);\s*$/, 'window.testRead = {parseReadHref,markRead,markCurrentRead,applyReadState,wireClickMarking};})();');
function link(href) {
  const classes = new Set(); const label = {hidden:true};
  return {href,label,classes,getAttribute:k=>k==='href'?href:null,
    querySelector:()=>label, matches:()=>true,
    classList:{toggle(k,v){v?classes.add(k):classes.delete(k)},add:k=>classes.add(k)}};
}
function setup(url='https://mirror.test/poker/free/123', marker='123', saved={'free|123':1}, blocked=false) {
  const events={}, winEvents={}; let storage=JSON.stringify(saved); let links=[];
  const article=marker===null?null:{dataset:{pokerPostId:marker},getAttribute:k=>k==='data-poker-post-id'?marker:null};
  const document={readyState:'loading',
    addEventListener:(type,fn)=>(events[type]||=[]).push(fn),
    querySelector:s=>s.includes('article-body')||s.includes('data-poker-post-id')?article:null,
    getElementById:id=>id==='article-body'?article:null,
    querySelectorAll:()=>links};
  const window={location:new URL(url),addEventListener:(type,fn)=>(winEvents[type]||=[]).push(fn),
    localStorage:{getItem(k){if(blocked)throw Error('blocked');return k==='read_posts_v1'?storage:null},setItem(k,v){if(blocked)throw Error('blocked');if(k==='read_posts_v1')storage=v}}};
  vm.runInNewContext(source,{window,document,URL,URLSearchParams,console});
  return {api:window.testRead,window,document,events,winEvents,setLinks:v=>links=v,
    store:()=>JSON.parse(storage),setStore:v=>storage=JSON.stringify(v),
    emit:(type,e)=>{for(const fn of events[type]||[])fn(e)},emitWindow:(type,e)=>{for(const fn of winEvents[type]||[])fn(e)}};
}
let h=setup();
assert.equal(h.api.parseReadHref('/read?board=free&pid=123'),'free|123');
assert.equal(h.api.parseReadHref('/poker/free/123?page=2'),'poker:123');
assert.equal(h.api.parseReadHref('/poker/best/123'),'poker:123');
assert.equal(h.api.parseReadHref('/poker/free/0123'),'poker:123');
for(const board of JSON.parse(process.argv[2]||'[]')) assert.equal(h.api.parseReadHref('/poker/'+board+'/123'),'poker:123',board);
for(const href of ['/poker/free','/poker/free/list','/poker/free/0','/poker/free/123/comments','/poker/unknown/123','/poker/free/9999999999999','https://evil.test/poker/free/123']) assert.equal(h.api.parseReadHref(href),null,href);
h.api.markCurrentRead(); assert.ok(h.store()['poker:123']); assert.equal(h.store()['free|123'],1);
const free=link('/poker/free/123'),best=link('/poker/best/123'),dc=link('/read?board=poker&pid=123'),unread=link('/poker/free/456');
h.setLinks([free,best,dc,unread]);h.api.applyReadState(h.document,h.store());
assert.ok(free.classes.has('is-read')&&best.classes.has('is-read'));assert.equal(free.label.hidden,false);
assert.ok(!dc.classes.has('is-read')&&!unread.classes.has('is-read'));
for(const marker of [null,'999']) {const failed=setup(undefined,marker,{});failed.api.markCurrentRead();assert.deepEqual(failed.store(),{});}
const listing=setup('https://mirror.test/poker/free',null,{});listing.api.markCurrentRead();assert.deepEqual(listing.store(),{});
const leading=setup('https://mirror.test/poker/free/0123','123',{});leading.api.markCurrentRead();assert.ok(leading.store()['poker:123']);
const legacy=setup('https://mirror.test/read?board=old&pid=7',null,{});legacy.api.markCurrentRead();assert.ok(legacy.store()['old|7']);
const dynamic=link('/poker/hand/123');h.emit('poker:list-rendered',{detail:{root:{querySelectorAll:()=>[dynamic]}}});assert.ok(dynamic.classes.has('is-read'));
h.setStore({'poker:456':10});h.emitWindow('storage',{key:'read_posts_v1'});assert.ok(unread.classes.has('is-read'));assert.ok(!free.classes.has('is-read'));assert.equal(free.label.hidden,true);
assert.doesNotThrow(()=>{const locked=setup(undefined,'123',{},true);locked.api.markCurrentRead();locked.api.applyReadState();});
const clickOnly=setup(undefined,null,{});clickOnly.api.wireClickMarking();
const queryLink=link('/poker/free/123?next=/read?pid=4');
clickOnly.emit('click',{target:{closest:s=>s.includes('feed-item')?queryLink:null}});
assert.deepEqual(clickOnly.store(),{});assert.ok(!queryLink.classes.has('is-read'));
console.log('poker_read_state_contract=passed');
