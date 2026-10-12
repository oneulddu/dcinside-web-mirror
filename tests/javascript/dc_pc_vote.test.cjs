const assert = require('node:assert/strict');
const path = require('node:path');
const zlib = require('node:zlib');
const { test } = require('node:test');

const load = () => import(path.resolve(__dirname, '../../dc_app/pc_vote.mjs'));

function crc32(buffer) {
  let crc = 0xffffffff;
  for (const byte of buffer) {
    crc ^= byte;
    for (let k = 0; k < 8; k += 1) crc = crc & 1 ? 0xedb88320 ^ (crc >>> 1) : crc >>> 1;
  }
  return (crc ^ 0xffffffff) >>> 0;
}
function chunk(type, data) {
  const head = Buffer.alloc(8);
  head.writeUInt32BE(data.length, 0);
  head.write(type, 4, 'latin1');
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(Buffer.concat([head.subarray(4), data])), 0);
  return Buffer.concat([head, data, crc]);
}
// 2x1 팔레트 PNG: 0번 흰 바탕, 1번 파란 글자(불투명), 2번 노이즈(반투명)
function palettePng() {
  const header = Buffer.alloc(13);
  header.writeUInt32BE(3, 0); header.writeUInt32BE(1, 4); header[8] = 8; header[9] = 3;
  return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', header),
    chunk('PLTE', Buffer.from([255, 255, 255, 20, 60, 160, 40, 90, 170])), chunk('tRNS', Buffer.from([255, 255, 135])),
    chunk('IDAT', zlib.deflateSync(Buffer.from([0, 0, 1, 2]))), chunk('IEND', Buffer.alloc(0))]);
}
function chunksOf(buffer) {
  const found = {};
  for (let offset = 8; offset < buffer.length;) {
    const length = buffer.readUInt32BE(offset);
    const type = buffer.toString('latin1', offset + 4, offset + 8);
    const data = buffer.subarray(offset + 8, offset + 8 + length);
    assert.equal(buffer.readUInt32BE(offset + 8 + length), crc32(buffer.subarray(offset + 4, offset + 8 + length)), type + ' CRC');
    found[type] = data;
    offset += 12 + length;
  }
  return found;
}

test('noise palette entries become opaque background white; text stays', async () => {
  const { clearTranslucentNoise } = await load();
  const cleaned = chunksOf(clearTranslucentNoise(palettePng()));
  assert.deepEqual([...cleaned.PLTE], [255, 255, 255, 20, 60, 160, 255, 255, 255]);
  assert.deepEqual([...cleaned.tRNS], [255, 255, 255]);
  const other = Buffer.from('GIF89a');
  assert.equal(clearTranslucentNoise(other), other);
});

test('view form keeps every hidden value and reads the captcha direction', async () => {
  const { parseViewForm } = await load();
  const html = '<form id="_view_form_"><input type="hidden" name="gallery_id" value="ai_utilize">'
    + '<input name="e98eab" type="hidden" value="a&amp;b"><input type="checkbox" name="skip" value="1"><input type="hidden" id="noname"></form>'
    + '<input type="hidden" name="recommend_code_use_yn" id="recommend_code_use_yn_7" value="Y" data-use-type="non_recommend">';
  assert.deepEqual(parseViewForm(html, 7), { fields: [['gallery_id', 'ai_utilize'], ['e98eab', 'a&b']], captcha: 'non_recommend' });
  assert.equal(parseViewForm('<form id="_view_form_"></form>', 7).captcha, 'none');
  assert.throws(() => parseViewForm('<html></html>', 7));
});

test('captcha session cookies, form values and answer are sent once to the PC vote endpoint', async () => {
  const { pcVoteCaptcha, pcVote } = await load();
  const calls = [];
  const original = globalThis.fetch;
  globalThis.fetch = async (url, init) => {
    calls.push({ url: String(url), init });
    const headers = new Headers();
    if (String(url).includes('/board/view/')) {
      headers.append('set-cookie', 'ci_c=token123; path=/');
      headers.append('set-cookie', 'PHPSESSID=sess; path=/');
      return new Response('<form id="_view_form_"><input type="hidden" name="e_s_n_o" value="xyz"></form>', { status: 200, headers });
    }
    if (String(url).includes('/kcaptcha/')) return new Response(palettePng(), { status: 200, headers: { 'content-type': 'image/png' } });
    return new Response('true||12||3||true', { status: 200 });
  };
  try {
    const base = { gallery: 'ai_utilize', no: 126474, galltype: 'M', key: 'a'.repeat(16) };
    const image = await pcVoteCaptcha(base);
    assert.equal(image.content_type, 'image/png');
    assert.equal(image.captcha, 'none');
    assert.equal(calls[0].url, 'https://gall.dcinside.com/mgallery/board/view/?id=ai_utilize&no=126474');
    assert.match(calls[1].init.headers.get('cookie'), /ci_c=token123/);
    const result = await pcVote({ gallery: 'ai_utilize', no: 126474, pc: { key: 'a'.repeat(16), code: ' ab12 ', galltype: 'M' } }, 'D');
    assert.deepEqual(result, { result: true, count: 12 });
    const vote = calls[2];
    assert.equal(vote.url, 'https://gall.dcinside.com/board/recommend/vote');
    const body = new URLSearchParams(String(vote.init.body));
    assert.deepEqual(Object.fromEntries(body), { e_s_n_o: 'xyz', ci_t: 'token123', id: 'ai_utilize', no: '126474', mode: 'D',
      code_recommend: 'ab12', _GALLTYPE_: 'M', link_id: 'ai_utilize' });
    assert.match(vote.init.headers.get('cookie'), /ai_utilize126474_Firstcheck_down=Y/);
    // 한 번 쓴 문자는 다시 쓰지 않는다. DC에 보내기 전에 새 문자를 요구한다.
    await assert.rejects(pcVote({ gallery: 'ai_utilize', no: 126474, pc: { key: 'a'.repeat(16), code: 'ab12', galltype: 'M' } }, 'U'),
      error => error.name === 'CaptchaRequiredError' && error.sent === false);
    assert.equal(calls.length, 3);
  } finally {
    globalThis.fetch = original;
  }
});

test('PC vote rejections keep DC wording and wrong answers ask for a new captcha', async () => {
  const { pcVoteCaptcha, pcVote } = await load();
  const original = globalThis.fetch;
  let answer = '';
  globalThis.fetch = async url => {
    if (String(url).includes('/board/view/')) return new Response('<form id="_view_form_"></form>', { status: 200, headers: { 'set-cookie': 'ci_c=t' } });
    if (String(url).includes('/kcaptcha/')) return new Response(palettePng(), { status: 200, headers: { 'content-type': 'image/png' } });
    return new Response(answer, { status: 200 });
  };
  try {
    const run = async text => {
      answer = text;
      await pcVoteCaptcha({ gallery: 'g', no: 1, galltype: 'G', key: 'b'.repeat(16) });
      return pcVote({ gallery: 'g', no: 1, pc: { key: 'b'.repeat(16), code: 'x', galltype: 'G' } }, 'U').catch(error => error);
    };
    const wrong = await run('false||자동입력 방지코드가 일치하지 않습니다.');
    assert.equal(wrong.name, 'CaptchaRequiredError');
    const daily = await run('false||추천은 1일 1회만 가능합니다.');
    assert.deepEqual([daily.name, daily.cause], ['ApiError', '추천은 1일 1회만 가능합니다.']);
    const member = await run('false||nomember||멤버만 이용할 수 있습니다.||0||x');
    assert.equal(member.cause, '멤버만 이용할 수 있습니다.');
    const odd = await run('<html>error</html>');
    assert.equal(odd.name, 'HTTPError');
  } finally {
    globalThis.fetch = original;
  }
});
