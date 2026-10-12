const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { test } = require('node:test');

// A local package double makes accidental network access impossible.
const fakeLibrary = `
import { appendFileSync } from 'node:fs';
const trace = operation => appendFileSync(process.env.WORKER_TEST_TRACE, operation + '\\n');
export class DCInside {
  constructor(options) {
    if (options.http.timeout !== 45000) throw new Error('Wrong HTTP timeout');
    this.credentials = {androidId:'123',securityToken:'456',fid:'private-fid',refreshToken:'private-refresh',clientToken:'private-token',appId:'private-app',appIdIssuedAt:Date.now()-30000};
    this.auth = {
      appId: async () => { trace('appId'); return 'private-app'; },
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
        trace('write');
        const session = this.session;
        await new Promise(resolve => setTimeout(resolve, 5));
        if (this.session !== session) throw new Error('Session race');
        if (session.type !== 'anonymous' || session.password !== 'private-password') throw new Error('Wrong session');
        if (!options.validateOnly) throw new Error('Expected validateOnly');
        if (options.subject === 'images') {
          const blocks = [];
          for (const block of options.content) {
            if (typeof block === 'string') blocks.push(block);
            else {
              if (block.type !== 'image' || !(block.file instanceof File)) throw new Error('Expected image File');
              blocks.push({type:block.file.type,name:block.file.name,bytes:Array.from(new Uint8Array(await block.file.arrayBuffer()))});
            }
          }
          return {result:'true',blocks};
        }
        if (options.content.length !== 1 || options.content[0] !== 'line1\\nline2 <script>') throw new Error('Content altered');
        if (options.subject === 'captcha') throw Object.assign(new Error('private-password'), {name:'CaptchaRequiredError',cause:'보안 코드 입력'});
        if (options.subject === 'unknown') throw new Error('private-password');
        return {result:'true',subject:options.subject};
      },
      read: async (gallery, no) => {
        trace('read');
        if (gallery === 'deleted') throw Object.assign(new Error('x'), {name:'ApiError',cause:'삭제된 글'});
        return gallery === 'vanced'
          ? {view_info:{no},view_main:{nonrecomm_use:false}}
          : {view_info:{recommend_captcha:true},view_main:{nonrecomm_use:true}};
      },
      upvote: async gallery => {
        if (this.session !== null) throw new Error('Leaked session');
        if (gallery === 'vpn') throw Object.assign(new Error('x'), {name:'ApiError',cause:'추천할 수 없습니다.',response:[{result:false,carrier_vpn_ban:true}]});
        return {result:'true'};
      },
      downvote: async () => { if (this.session !== null) throw new Error('Leaked session'); return {result:'true',direction:'down'}; },
    };
    this.http = { fetch: async url => {
      if (url.includes('badtype')) return new Response('html', {headers:{'content-type':'text/html'}});
      if (url.includes('oversize')) return new Response(new Uint8Array(204801), {headers:{'content-type':'image/png'}});
      return new Response(new Uint8Array([1,2,3]), {headers:{'content-type':'image/png'}});
    }};
  }
  useAnonymous(nickname,password) { trace('anonymous'); this.session={type:'anonymous',nickname,password}; }
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
    const tracePath = path.join(directory, 'trace.txt');
    if (seed) {
      fs.mkdirSync(path.dirname(state));
      fs.writeFileSync(state, JSON.stringify({ restored: true, appIdIssuedAt: Date.now() - 30000 }), { mode: 0o644 });
    }
    const child = spawnSync(process.execPath, [path.join(directory, 'worker.mjs')], {
      env: { ...process.env, MIRROR_DC_APP_STATE: state, WORKER_TEST_TRACE: tracePath },
      input: requests.map((request, index) => JSON.stringify({ id: index, ...request })).join('\n') + '\n',
      encoding: 'utf8', timeout: 5000,
    });
    assert.equal(child.status, 0, child.stderr);
    assert(!child.stdout.includes('private-') && !child.stderr.includes('private-'));
    const replies = child.stdout.trim().split('\n').map(line => JSON.parse(line));
    assert.deepEqual(replies.map(reply => reply.id), requests.map((_, index) => index));
    return { replies, mode: fs.existsSync(state) ? fs.statSync(state).mode & 0o777 : null,
      trace: fs.existsSync(tracePath) ? fs.readFileSync(tracePath, 'utf8').trim().split('\n') : [],
      state: fs.existsSync(state) ? JSON.parse(fs.readFileSync(state, 'utf8')) : null,
      temporary: fs.existsSync(path.dirname(state)) ? fs.readdirSync(path.dirname(state)).filter(name => name.endsWith('.tmp')) : [] };
  } finally {
    fs.rmSync(directory, { recursive: true, force: true });
  }
}

const write = subject => ({ op:'write', session:{type:'anonymous', nickname:'private-nickname', password:'private-password'}, gallery:'programming', subject, body:'line1\nline2 <script>', validateOnly:true });

test('worker serializes sessions, preserves text and validateOnly, and writes credentials atomically', () => {
  const result = run([write('one'), write('two'), { op:'upvote', gallery:'programming', no:1 }, { op:'downvote', gallery:'programming', no:1 }]);
  assert(result.replies.every(reply => reply.ok));
  assert.equal(result.replies[3].data.direction, 'down');
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

test('write preserves image bytes, File names and order at the top, bottom and without text', () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'dc-upload-test-'));
  try {
    const images = [
      {path:path.join(directory, 'random_1.png'),type:'image/png',name:'image1.png'},
      {path:path.join(directory, 'random-2.jpg'),type:'image/jpeg',name:'image2.jpg'},
    ];
    const buffers = [Buffer.from([137,80,78,71,13,10,26,10]), Buffer.from([255,216,255])];
    images.forEach((image, index) => fs.writeFileSync(image.path, buffers[index]));
    const requests = [
      {...write('images'),images},
      {...write('images'),images,image_position:'bottom'},
      {...write('images'),images,body:''},
      {...write('images'),images,body:' \n\t ',image_position:'bottom'},
      {...write('images'),images,blocks:[{text:'첫 문단'},{image:1},{text:'둘째'},{image:0}]},
    ];
    const result = run(requests);
    assert(result.replies.every(reply => reply.ok));
    const blocks = images.map((image, index) => ({name:image.name,type:image.type,bytes:Array.from(buffers[index])}));
    assert.deepEqual(result.replies.map(reply => reply.data.blocks), [
      [...blocks, write('').body], [write('').body, ...blocks], blocks, blocks,
      ['첫 문단', blocks[1], '둘째', blocks[0]],
    ]);
    assert.deepEqual(result.trace, Array(5).fill(['anonymous', 'appId', 'write']).flat());
    // 사진을 빠뜨리거나 두 번 쓰는 순서는 디시에 보내기 전에 거절한다.
    const bad = run([[{image:0}], [{image:0},{image:0},{image:1}], [{image:0},{image:2},{image:1}], [{text:' '},{image:0},{image:1}]]
      .map(order => ({...write('images'),images,blocks:order})));
    assert(bad.replies.every(reply => !reply.ok && reply.error.sent === false));
    assert.deepEqual(bad.trace, []);
  } finally {
    fs.rmSync(directory, {recursive:true,force:true});
  }
});

test('invalid image metadata and file failures precede anonymous session and DC calls', () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(), 'dc-upload-test-'));
  try {
    const valid = {path:path.join(directory, 'valid.png'),type:'image/png',name:'image1.png'};
    fs.writeFileSync(valid.path, Buffer.from([137,80,78,71,13,10,26,10]));
    const empty = path.join(directory, 'empty.png');
    const large = path.join(directory, 'large.png');
    fs.writeFileSync(empty, Buffer.alloc(0));
    fs.writeFileSync(large, Buffer.alloc(10 * 1024 * 1024 + 1));
    const invalid = [
      {...valid,path:'relative.png'}, {...valid,path:path.join(directory,'bad name.png')},
      {...valid,path:path.join(directory,'bad.svg')}, {...valid,type:'image/svg+xml'},
      {...valid,path:3}, {...valid,name:'../original.png'},
      {...valid,path:path.join(directory,'missing.png')},
      {...valid,path:empty}, {...valid,path:large}, null,
    ];
    const result = run([
      ...invalid.map(image => ({...write('images'),images:[valid,image]})),
      {...write('images'),images:[...Array(11)].map(() => valid)},
      {...write('images'),images:{}},
    ]);
    assert(result.replies.every(reply => !reply.ok));
    for (const reply of result.replies) {
      assert.deepEqual(reply.error, {name:'WorkerError',cause:'',status:null,sent:false});
    }
    assert.deepEqual(result.trace, []);
    assert.equal(result.state, null);
    assert(!JSON.stringify(result.replies).includes(directory));
  } finally {
    fs.rmSync(directory, {recursive:true,force:true});
  }
});

test('vote_info reads gallery vote settings without voting', () => {
  const result = run([{op:'vote_info',gallery:'vanced',no:34645}, {op:'vote_info',gallery:'thesingularity',no:1}, {op:'vote_info',gallery:'deleted',no:1}, {op:'vote_info',gallery:'x',no:0}]);
  assert.deepEqual(result.replies[0].data, {down:false,captcha:false});
  assert.deepEqual(result.replies[1].data, {down:true,captcha:true});
  assert.deepEqual(result.replies[2].error, {name:'ApiError',cause:'삭제된 글',status:null});
  assert.equal(result.replies[3].error.name, 'WorkerError');
  assert.deepEqual(result.trace, ['read', 'read', 'read']);
});

test('vote errors report a carrier or VPN ban flag', () => {
  const result = run([write('one'), {op:'upvote',gallery:'vpn',no:1}]);
  assert.deepEqual(result.replies[1].error, {name:'ApiError',cause:'추천할 수 없습니다.',status:null,vpn_ban:true});
});
