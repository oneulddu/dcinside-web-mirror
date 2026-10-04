const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const code = fs.readFileSync(path.join(__dirname, '../../app/static/javascript/poker_images.js'), 'utf8');
function harness() {
  const handlers = {}, timers = new Map(), writes = []; let now = 0, serial = 0;
  const document = { addEventListener(type, fn, capture) { assert.equal(capture, true); handlers[type] = fn; } };
  vm.runInNewContext(code, { document, WeakMap, Math: { random: () => 0 },
    setTimeout(fn, delay) { timers.set(++serial, {fn, at: now + delay}); return serial; },
    clearTimeout(id) { timers.delete(id); }
  });
  function image(url = '/poker/media?src=approved&sig=signature') {
    const attrs = {'data-body-image-src':url, src:url};
    return {tagName:'IMG',hidden:false,isConnected:true,naturalWidth:0,
      getAttribute(k) { return attrs[k] ?? null; },
      setAttribute(k,v) {attrs[k]=v; writes.push(this);},
      removeAttribute(k) {delete attrs[k];}
    };
  }
  function advance(ms) {
    const end=now+ms;
    while(true) {
      const next=[...timers].filter(([,t])=>t.at<=end).sort((a,b)=>a[1].at-b[1].at)[0];
      if(!next) break;
      timers.delete(next[0]); now=next[1].at; next[1].fn();
    }
    now=end;
  }
  return {image,advance,writes,emit(type,target) {handlers[type]({target});}};
}
// A burst of failed body/comment images eventually recovers, at most two at a time.
{
  const h=harness(), images=Array.from({length:12},()=>h.image());
  images.forEach(n=>h.emit('error',n)); h.advance(1499); assert.equal(h.writes.length,0);
  h.advance(1); assert.equal(h.writes.length,2);
  for(let i=0;i<images.length;i++) {images[i].naturalWidth=120; h.emit('load',images[i]); assert.ok(h.writes.length<=i+3);}
  assert.equal(h.writes.length,12); h.advance(60000); assert.equal(h.writes.length,12);
}
// Permanent failures stop after two retries and duplicate errors do not add jobs.
{
  const h=harness(), n=h.image(); h.emit('error',n);h.emit('error',n);
  h.advance(1500);assert.equal(h.writes.length,1);h.emit('error',n);
  h.advance(3999);assert.equal(h.writes.length,1);h.advance(1);assert.equal(h.writes.length,2);
  h.emit('error',n);h.advance(60000);assert.equal(h.writes.length,2);
}
// Image blocking, removal, successful load, or a changed source cancels pending work.
for(const mutate of [n=>{n.hidden=true;n.removeAttribute('src');},n=>{n.isConnected=false;},n=>{n.naturalWidth=10;},n=>n.setAttribute('src','/different')]) {
  const h=harness(),n=h.image();h.emit('error',n);mutate(n);const before=h.writes.length;
  h.advance(60000);assert.equal(h.writes.length,before);
}
// Other sites and non-image errors are untouched; capture handles later comments.
{
  const h=harness();h.emit('error',h.image('/media?src=dc'));h.emit('error',{tagName:'SCRIPT'});
  h.advance(10000);assert.equal(h.writes.length,0);
  const comment=h.image();h.emit('error',comment);h.advance(1500);assert.equal(h.writes[0],comment);
}
// Stalled requests are canceled before their slots are reused, even while visible.
{
  const h=harness(),images=Array.from({length:6},()=>h.image());images.forEach(n=>h.emit('error',n));
  h.advance(1500);assert.equal(h.writes.length,2);
  h.advance(35000);assert.equal(h.writes.length,4);
  assert.equal(images[0].getAttribute('src'),null);assert.equal(images[1].getAttribute('src'),null);
  h.advance(35000);assert.equal(h.writes.length,6);
  assert.equal(images[2].getAttribute('src'),null);assert.equal(images[3].getAttribute('src'),null);
  assert.equal(images.filter(n=>n.getAttribute('src')).length,2);
}
console.log('poker_images: burst recovery, retry cap, blocking, inserted comments and stalled requests passed');
