const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { test } = require('node:test');

// A local package double makes accidental network access impossible.
const fakeLibrary = `
export class DCInside {
  constructor() {
    this.credentials = {androidId:'123',securityToken:'456',fid:'private-fid',refreshToken:'private-refresh',clientToken:'private-token',appId:'private-app',appIdIssuedAt:Date.now()-30000};
    this.auth = {
      appId: async () => 'private-app',
      exportCredentials: () => this.credentials,
      importCredentials: value => { this.credentials = value; },
    };
    this.articles = {
      list: async options => {
        if (options.page !== 1) throw new Error('Wrong page');
        if (options.gallery === 'failure') throw Object.assign(new Error('failure'), {name:'ApiError',cause:'권한 오류'});
        if (options.gallery === 'programming') return {gall_info:[{}],gall_list:[]};
        if (options.gallery !== 'baseball_new12') throw new Error('Unexpected gallery');
        return {gall_info:[{head_text:[{no:0,name:'일반',level:0,selected:true},{no:10,name:'기록',level:9},{no:3,name:'unknown'},{no:4.5,name:'bad',level:0}],no_write:true,captcha:true}],gall_list:[]};
      },
      write: async options => {
        const session = this.session;
        await new Promise(resolve => setTimeout(resolve, 5));
        if (this.session !== session) throw new Error('Session race');
        if (session.type !== 'anonymous' || session.password !== 'private-password') throw new Error('Wrong session');
        if (options.content.length !== 1 || options.content[0] !== 'line1\\nline2 <script>') throw new Error('Content altered');
        if (!options.validateOnly) throw new Error('Expected validateOnly');
        if (options.subject === 'captcha') throw Object.assign(new Error('private-password'), {name:'CaptchaRequiredError',cause:'보안 코드 입력'});
        if (options.subject === 'unknown') throw new Error('private-password');
        return {result:'true',subject:options.subject};
      },
      upvote: async () => { if (this.session !== null) throw new Error('Leaked session'); return {result:'true'}; },
    };
    this.http = { fetch: async url => {
      if (url.includes('badtype')) return new Response('html', {headers:{'content-type':'text/html'}});
      if (url.includes('oversize')) return new Response(new Uint8Array(204801), {headers:{'content-type':'image/png'}});
      return new Response(new Uint8Array([1,2,3]), {headers:{'content-type':'image/png'}});
    }};
  }
  useAnonymous(nickname,password) { this.session={type:'anonymous',nickname,password}; }
}
export const captchaUrl = (kind,key,gallery) => 'https://invalid.test/' + gallery;
`;

function run(requests, seed = false) {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'dc-worker-test-'));
  try {
    const library = path.join(directory, 'node_modules/@green-1052/dcinside.js');
    fs.mkdirSync(library, { recursive: true });
    fs.writeFileSync(path.join(library, 'package.json'), JSON.stringify({ type: 'module', exports: './index.mjs' }));
    fs.writeFileSync(path.join(library, 'index.mjs'), fakeLibrary);
    fs.copyFileSync(path.resolve(__dirname, '../../dc_app/worker.mjs'), path.join(directory, 'worker.mjs'));
    const state = path.join(directory, 'state/credentials.json');
    if (seed) {
      fs.mkdirSync(path.dirname(state));
      fs.writeFileSync(state, JSON.stringify({ restored: true, appIdIssuedAt: Date.now() - 30000 }), { mode: 0o644 });
    }
    const child = spawnSync(process.execPath, [path.join(directory, 'worker.mjs')], {
      env: { ...process.env, MIRROR_DC_APP_STATE: state },
      input: requests.map((request, index) => JSON.stringify({ id: index, ...request })).join('\n') + '\n',
      encoding: 'utf8', timeout: 5000,
    });
    assert.equal(child.status, 0, child.stderr);
    assert(!child.stdout.includes('private-') && !child.stderr.includes('private-'));
    const replies = child.stdout.trim().split('\n').map(line => JSON.parse(line));
    assert.deepEqual(replies.map(reply => reply.id), requests.map((_, index) => index));
    return { replies, mode: fs.existsSync(state) ? fs.statSync(state).mode & 0o777 : null,
      state: fs.existsSync(state) ? JSON.parse(fs.readFileSync(state, 'utf8')) : null,
      temporary: fs.existsSync(path.dirname(state)) ? fs.readdirSync(path.dirname(state)).filter(name => name.endsWith('.tmp')) : [] };
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
}

const write = subject => ({ op:'write', session:{type:'anonymous', nickname:'private-nickname', password:'private-password'}, gallery:'programming', subject, body:'line1\nline2 <script>', validateOnly:true });

test('worker serializes sessions, preserves text and validateOnly, and writes credentials atomically', () => {
  const result = run([write('one'), write('two'), { op:'upvote', gallery:'programming', no:1 }]);
  assert(result.replies.every(reply => reply.ok));
  assert.deepEqual(result.replies.slice(0, 2).map(reply => reply.data.subject), ['one', 'two']);
  assert.equal(result.mode, 0o600);
  assert.deepEqual(result.temporary, []);
});

test('credential restore restricts existing file permissions', () => {
  const result = run([{ op:'ping' }], true);
  assert.equal(result.mode, 0o600);
  assert.equal(result.state.restored, true);
});

test('only known API errors expose causes; unsupported login is rejected', () => {
  const result = run([write('captcha'), write('unknown'), { ...write('login'), session:{type:'login'} }]);
  assert.deepEqual(result.replies[0].error, { name:'CaptchaRequiredError', cause:'보안 코드 입력', status:null });
  assert.deepEqual(result.replies[1].error, { name:'WorkerError', cause:'', status:null });
  assert.equal(result.replies[2].ok, false);
});

test('captcha transport checks content type and streamed size', () => {
  const result = run(['good', 'badtype', 'oversize'].map(gallery => ({op:'captcha',kind:'article',key:'a'.repeat(16),gallery})));
  assert.deepEqual(result.replies[0].data, {content_type:'image/png',base64:'AQID'});
  assert.equal(result.replies[1].ok, false);
  assert.equal(result.replies[2].ok, false);
});


test('gallery_info maps heads and flags and saves credentials', () => {
  const result = run([{op:'gallery_info',gallery:'baseball_new12'}, {op:'gallery_info',gallery:'programming'}]);
  assert(result.replies.every(reply => reply.ok));
  assert.deepEqual(result.replies[0].data, {heads:[{no:0,name:'일반',level:0},{no:10,name:'기록',level:9}],no_write:true,captcha:true});
  assert.deepEqual(result.replies[1].data, {heads:[],no_write:false,captcha:false});
  assert.equal(result.mode, 0o600);
  assert.deepEqual(result.temporary, []);
});

test('gallery_info rejects invalid galleries before credential persistence and reports API failures', () => {
  const invalid = run(['../x', '', 'a'.repeat(81)].map(gallery => ({op:'gallery_info',gallery})));
  assert(invalid.replies.every(reply => !reply.ok && reply.error.name === 'WorkerError'));
  assert.equal(invalid.state, null);
  const failure = run([{op:'gallery_info',gallery:'failure'}]);
  assert.deepEqual(failure.replies[0].error, {name:'ApiError',cause:'권한 오류',status:null});
  assert.equal(failure.mode, 0o600);
});
