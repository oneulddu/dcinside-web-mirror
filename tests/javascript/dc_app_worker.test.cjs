const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { spawnSync } = require('node:child_process');
const { test } = require('node:test');
const { createHash } = require('node:crypto');

const snapshot = {
  subject: '원래 제목',
  memo: [
    {memo_block_0:'&lt;div&gt;첫 &amp;amp; 줄&lt;br&gt;둘째&lt;/div&gt;'},
    {memo_block_1:'&lt;b&gt;굵은&lt;/b&gt; 블록'},
    {memo_block_2:'&lt;div&gt;&lt;img src=&quot;https://dcimg1.dcinside.com/viewimage.php?id=1&amp;amp;no=2&quot;&gt;&lt;/div&gt;'},
  ],
  file: [],
};
// Independent fixture: explicit sorted keys, rather than calling the worker's canonicalizer.
const fingerprint = createHash('sha256').update(JSON.stringify({file:[],memo:snapshot.memo,subject:snapshot.subject})).digest('hex');

// A local package double makes accidental network access impossible.
const fakeLibrary = `
import { appendFileSync } from 'node:fs';
export const decodeHtml = value => value.replace(/&(lt|gt|amp|quot|apos|nbsp);/g, (_, key) => ({lt:'<',gt:'>',amp:'&',quot:'"',apos:"'",nbsp:' '})[key]);
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
      delete: async (gallery, no) => {
        trace('delete');
        if (this.session?.type !== 'anonymous') throw new Error('Wrong session');
        if (this.session.password !== 'private-password') throw Object.assign(new Error('x'), {name:'ApiError',cause:'비밀번호 오류'});
        if (gallery === 'throttle') {
          this.credentials.appIdIssuedAt = Date.now();
          throw Object.assign(new Error('x'), {name:'ApiError',cause:'잠시후 다시 이용해주세요.'});
        }
        return {result:'true',gallery,no};
      },
      modifyInfo: async (gallery, no) => {
        trace('modifyInfo');
        if (this.session?.type !== 'anonymous') throw new Error('Wrong session');
        if (this.session.password !== 'private-password') throw Object.assign(new Error('x'), {name:'ApiError',cause:'비밀번호 오류'});
        if (gallery === 'failure') throw new Error('snapshot transport');
        const info = ${JSON.stringify(snapshot)};
        if (['secret','poll','movie','aiImg'].includes(gallery)) info[gallery] = gallery === 'secret' ? true : [{}];
        if (gallery === 'pinned') info.fix = 'true';
        return {...info, headid:3, head_text:[{no:0,name:'일반',level:0},{no:3,name:'기록',level:9}], auto_del_time:'24'};
      },
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
        if (options.articleNo) {
          if (options.subject === 'captcha') throw Object.assign(new Error('x'), {name:'CaptchaRequiredError',cause:'보안 코드 입력'});
          if (options.subject === 'unknown') throw new Error('private-password');
          const content = await Promise.all(options.content.map(async block => block?.type === 'image'
            ? {type:'image', name:block.file.name, bytes:Array.from(new Uint8Array(await block.file.arrayBuffer()))} : block));
          return {result:'true', articleNo:options.articleNo, content, deleteFiles:options.deleteFiles,
            autoDeleteTime:options.autoDeleteTime, head:options.headText, captcha:options.captcha, fix:options.fix};
        }
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
    this.comments = {
      delete: async (gallery, no, commentNo) => {
        trace('comment_delete');
        if (this.session?.type !== 'anonymous') throw new Error('Wrong session');
        if (this.session.password !== 'private-password') throw Object.assign(new Error('x'), {name:'ApiError',cause:'비밀번호가 틀립니다.'});
        return {result:'true', gallery, no, commentNo};
      },
      write: async (gallery, no, content, options) => {
        trace('comment');
        const session = this.session;
        if (!session || session.type !== 'anonymous' || session.password !== 'private-password') throw new Error('Wrong session');
        if (gallery === 'captcha') throw Object.assign(new Error('x'), {name:'CaptchaRequiredError',cause:'보안 코드 입력'});
        return {result:'true', gallery, no, content, parent: options.parent ?? null, captcha: options.captcha ?? null};
      },
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
    fs.copyFileSync(path.resolve(__dirname, '../../dc_app/pc_vote.mjs'), path.join(directory, 'pc_vote.mjs'));
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
  // nonrecomm_use가 true면 비추천을 끈 갤러리다.
  assert.deepEqual(result.replies[0].data, {down:true,captcha:false});
  assert.deepEqual(result.replies[1].data, {down:false,captcha:true});
  assert.deepEqual(result.replies[2].error, {name:'ApiError',cause:'삭제된 글',status:null});
  assert.equal(result.replies[3].error.name, 'WorkerError');
  assert.deepEqual(result.trace, ['read', 'read', 'read']);
});

test('vote errors report a carrier or VPN ban flag', () => {
  const result = run([write('one'), {op:'upvote',gallery:'vpn',no:1}]);
  assert.deepEqual(result.replies[1].error, {name:'ApiError',cause:'추천할 수 없습니다.',status:null,vpn_ban:true});
});

const comment = extra => ({ op:'comment', session:{type:'anonymous', nickname:'private-nickname', password:'private-password'}, gallery:'programming', no:7, content:'첫 줄\n<b>둘째</b>', ...extra });

test('comment op sends text and reply parent through an anonymous session, then clears it', () => {
  const result = run([
    comment({}),
    comment({ parent:{comment_no:45, user_id:''}, captcha:{key:'a'.repeat(16), code:'ab'} }),
    { op:'upvote', gallery:'programming', no:1 },
    comment({ gallery:'captcha' }),
  ]);
  assert.deepEqual(result.replies[0].data, {result:'true', gallery:'programming', no:7, content:'첫 줄\n<b>둘째</b>', parent:null, captcha:null});
  assert.deepEqual(result.replies[1].data.parent, {comment_no:45, user_id:''});
  assert.deepEqual(result.replies[1].data.captcha, {key:'a'.repeat(16), code:'ab'});
  assert.equal(result.replies[2].ok, true);
  assert.deepEqual(result.replies[3].error, { name:'CaptchaRequiredError', cause:'보안 코드 입력', status:null });
});

test('invalid comments are rejected before any DC call', () => {
  const result = run([
    comment({ content:'x'.repeat(401) }),
    comment({ content:'  ' }),
    comment({ no:0 }),
    comment({ parent:{comment_no:'1', user_id:''} }),
    comment({ parent:{comment_no:1, user_id:'1.2'} }),
    { ...comment({}), session:{type:'login'} },
  ]);
  result.replies.slice(0, 5).forEach(reply => assert.deepEqual(reply.error, { name:'WorkerError', cause:'', status:null, sent:false }));
  assert.equal(result.replies[5].ok, false);
  assert(!result.trace.includes('comment') && !result.trace.includes('anonymous'));
});

const mutation = (op, extra = {}) => ({op, gallery:'programming', no:7, session:write('').session, ...extra});
const modify = (extra = {}) => mutation('modify', {subject:'수정 제목', fingerprint, blocks:[{keep:1},{text:'새 본문'}], ...extra});

test('delete and comment_delete use anonymous passwords and clear sessions on success and rejection', () => {
  const bad = {...write('').session, password:'wrong'};
  const result = run([
    mutation('delete'), mutation('comment_delete',{comment_no:45}),
    mutation('delete',{session:bad}), mutation('comment_delete',{comment_no:45,session:bad}),
    {op:'upvote',gallery:'programming',no:7},
  ]);
  assert.deepEqual(result.replies[0].data, {result:'true',gallery:'programming',no:7});
  assert.deepEqual(result.replies[1].data, {result:'true',gallery:'programming',no:7,commentNo:45});
  assert.equal(result.replies[2].error.cause, '비밀번호 오류');
  assert.equal(result.replies[3].error.cause, '비밀번호가 틀립니다.');
  assert.equal(result.replies[4].ok, true);
});

test('modify_info decodes ordered memo blocks, computes fingerprint, and refuses unsupported content', () => {
  const result = run([
    mutation('modify_info'),
    ...['secret','poll','movie','aiImg'].map(gallery => mutation('modify_info',{gallery})),
    mutation('modify_info',{session:{...write('').session,password:'wrong'}}),
    {op:'upvote',gallery:'programming',no:7},
  ]);
  assert.deepEqual(result.replies[0].data, {
    result:true, subject:snapshot.subject, head_id:3, head_name:'기록', fingerprint, editable:true, reason:null,
    blocks:[
      {kind:'text',text:'첫 & 줄\n둘째'},
      {kind:'html',index:1,preview:'굵은 블록'},
      {kind:'photo',index:2,url:'https://dcimg1.dcinside.com/viewimage.php?id=1&no=2'},
    ],
  });
  assert.deepEqual(result.replies.slice(1,5).map(reply => [reply.data.editable,reply.data.reason]),
    [[false,'secret'],[false,'poll'],[false,'movie'],[false,'ai_image']]);
  assert.equal(result.replies[5].error.cause, '비밀번호 오류');
  assert.equal(result.replies[6].ok, true);
});

test('modify re-fetches original HTML, preserves options, and never accepts HTML from a client', () => {
  const result = run([
    modify({head:{no:3,name:'일반'},captcha:{key:'a'.repeat(16),code:'ab'}}),
    modify({blocks:[{keep:1,html:'<script>bad</script>'}]}),
    {op:'upvote',gallery:'programming',no:7},
  ]);
  assert.deepEqual(result.replies[0].data, {result:'true',articleNo:7,
    content:[{type:'html',html:'<b>굵은</b> 블록'},'새 본문'],deleteFiles:[],autoDeleteTime:'24',
    head:{no:3,name:'일반'},captcha:{key:'a'.repeat(16),code:'ab'}});
  assert.equal(result.replies[1].error.sent, false);
  assert.deepEqual(result.trace, ['anonymous','appId','modifyInfo','write','appId']);
  assert.equal(result.replies[2].ok, true);
});

test('modify rejects stale fingerprints, forbidden content and nonexistent keeps without a write', () => {  const result = run([
    modify({fingerprint:'0'.repeat(64)}), modify({gallery:'poll'}),
    modify({blocks:[{keep:99}]}), modify({gallery:'failure'}),
    {op:'upvote',gallery:'programming',no:7},
  ]);
  result.replies.slice(0,2).forEach(reply => assert.deepEqual(reply.error,
    {name:'WorkerError',cause:'stale',status:null,sent:false}));
  result.replies.slice(2,4).forEach(reply => assert.equal(reply.error.sent,false));
  assert(!result.trace.includes('write'));
  assert.equal(result.replies[4].ok, true);
});

test('modify preserves the pinned setting from the fresh snapshot', () => {
  const result = run([modify({gallery:'pinned'}), modify()]);
  assert.equal(result.replies[0].data.fix, true);
  // A non-pinned snapshot omits fix entirely rather than sending false.
  assert.equal(result.replies[1].data.fix, undefined);
});

test('all mutation inputs are validated before any DC call', () => {
  const requests = ['delete','comment_delete','modify_info','modify'].flatMap(op => [
    mutation(op,{gallery:'../x'}), mutation(op,{no:0}),
    mutation(op,{session:{type:'login'}}),
    mutation(op,{session:{...write('').session,password:'x'}}),
  ]);
  requests.push(mutation('comment_delete',{comment_no:0}),
    modify({fingerprint:'x'}), modify({subject:''}), modify({blocks:[]}),
    modify({blocks:[{keep:1},{keep:1}]}), modify({blocks:[{keep:-1}]}),
    modify({blocks:[{image:0}]}), modify({blocks:[{text:'x',keep:0}]}),
    modify({captcha:{key:'bad',code:'x'}}), modify({head:{no:'3',name:'x'}}));
  const result = run(requests);
  result.replies.forEach(reply => assert.equal(reply.error.sent,false));
  assert.deepEqual(result.trace, []);
});

test('modify uses each upload once in block order and cleans its session after send failure', () => {
  const directory = fs.mkdtempSync(path.join(os.tmpdir(),'dc-modify-test-'));
  try {
    const image = {path:path.join(directory,'image.png'),name:'image.png',type:'image/png'};
    const bytes = [137,80,78,71,13,10,26,10];
    fs.writeFileSync(image.path,Buffer.from(bytes));
    const result = run([
      modify({images:[image],blocks:[{image:0},{keep:1},{text:'끝'}]}),
      modify({images:[image],blocks:[{image:0},{image:0}]}),
      modify({images:[image],blocks:[{keep:1}]}),
      modify({subject:'captcha'}), modify({subject:'unknown'}),
      {op:'upvote',gallery:'programming',no:7},
    ]);
    assert.deepEqual(result.replies[0].data.content,
      [{type:'image',name:'image.png',bytes},{type:'html',html:'<b>굵은</b> 블록'},'끝']);
    result.replies.slice(1,3).forEach(reply => assert.equal(reply.error.sent,false));
    assert.equal(result.replies[3].error.name,'CaptchaRequiredError');
    assert.equal(result.replies[4].error.name,'WorkerError');
    assert.equal(result.replies[4].error.sent,undefined);
    assert.equal(result.replies[5].ok,true);
  } finally {
    fs.rmSync(directory,{recursive:true,force:true});
  }
});

// 새 app_id가 자리 잡기 전의 이 거절은 디시가 받지 않은 것이라 기존 글쓰기처럼 한 번만 다시 보낸다.
test('a fresh app-ID throttle rejection is retried exactly once', () => {
  const result = run([mutation('delete',{gallery:'throttle'})]);
  assert.equal(result.replies[0].error.cause,'잠시후 다시 이용해주세요.');
  assert.equal(result.trace.filter(item => item === 'delete').length,2);
});
