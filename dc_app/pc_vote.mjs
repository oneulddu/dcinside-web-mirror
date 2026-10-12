// PC 웹 추천. 워커(worker.mjs)가 불러 쓴다. DC 앱 API와 달리 PC 쿠키 세션을 쓴다.
const MAX_IMAGE = 200 * 1024;

// 앱 보안 문자는 노이즈가 불투명해 읽기 어렵다. 보안 문자가 필요한 추천은 PC 화면과 같은 방식으로 보낸다.
// PC 보안 문자 답은 PC 접속 쿠키에 묶이므로, 문자 이미지를 받은 쿠키를 key로 잠깐 보관했다가 그대로 쓴다.
const PC_HOST = 'https://gall.dcinside.com';
const PC_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36';
const PC_VIEW = { G: '/board/view/', M: '/mgallery/board/view/', MI: '/mini/board/view/' };
const PC_SESSION_TTL = 5 * 60 * 1000;
const PC_SESSION_MAX = 20;
const pcSessions = new Map();

function pcInput(input) {
  if (!/^[A-Za-z0-9_]{1,80}$/.test(input.gallery) || !Number.isSafeInteger(input.no) || input.no < 1
      || !Object.hasOwn(PC_VIEW, input.galltype) || !/^[0-9a-f]{16}$/.test(input.key)) throw new Error('Invalid PC vote request');
}

function pcViewUrl(input) {
  return PC_HOST + PC_VIEW[input.galltype] + '?' + new URLSearchParams({ id: input.gallery, no: String(input.no) });
}

function storeCookies(jar, response) {
  for (const line of response.headers.getSetCookie?.() ?? []) {
    const pair = line.split(';', 1)[0];
    const index = pair.indexOf('=');
    if (index > 0) jar.set(pair.slice(0, index).trim(), pair.slice(index + 1).trim());
  }
}

async function pcFetch(url, jar, init = {}) {
  const headers = new Headers({ 'User-Agent': PC_UA, 'Accept-Language': 'ko-KR,ko;q=0.9', ...init.headers });
  if (jar.size) headers.set('Cookie', [...jar].map(([name, value]) => name + '=' + value).join('; '));
  const response = await fetch(url, { ...init, headers, redirect: 'manual', signal: AbortSignal.timeout(20000) });
  storeCookies(jar, response);
  return response;
}

const ENTITIES = { amp: '&', lt: '<', gt: '>', quot: '"', '#39': "'", apos: "'" };
const unescapeHtml = text => text.replace(/&(amp|lt|gt|quot|#39|apos);/g, (_, name) => ENTITIES[name]);
const attribute = (tag, name) => {
  const match = new RegExp('\\s' + name + '\\s*=\\s*("([^"]*)"|\'([^\']*)\')', 'i').exec(tag);
  return match ? unescapeHtml(match[2] ?? match[3]) : null;
};

// PC 화면의 _view_form_ 입력값(일회용 값 포함)을 화면 스크립트처럼 그대로 모은다.
export function parseViewForm(html, no) {
  const form = /<form[^>]*id=["']_view_form_["'][^>]*>([\s\S]*?)<\/form>/i.exec(html);
  if (!form) throw new Error('PC view form missing');
  const fields = [];
  for (const tag of form[1].match(/<input\b[^>]*>/gi) ?? []) {
    const name = attribute(tag, 'name');
    const type = (attribute(tag, 'type') || 'text').toLowerCase();
    if (!name || ['checkbox', 'radio', 'button', 'submit', 'file'].includes(type)) continue;
    fields.push([name, attribute(tag, 'value') ?? '']);
  }
  const use = new RegExp('<input\\b[^>]*id=["\']recommend_code_use_yn_' + no + '["\'][^>]*>', 'i').exec(html);
  const captcha = use && attribute(use[0], 'value') === 'Y' ? (attribute(use[0], 'data-use-type') || 'all') : 'none';
  return { fields, captcha };
}

// 팔레트 PNG의 반투명 색(노이즈 곡선)을 바탕과 같은 불투명 흰색으로 바꾼다. 글자는 불투명이라 그대로 남는다.
// 투명하게만 만들면 어두운 화면에서 배경색이 비쳐 노이즈 자리가 다시 보인다.
export function clearTranslucentNoise(buffer) {
  const SIGNATURE = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
  if (buffer.length < 8 || !buffer.subarray(0, 8).equals(SIGNATURE)) return buffer;
  const chunks = [];
  let offset = 8;
  while (offset + 12 <= buffer.length) {
    const length = buffer.readUInt32BE(offset);
    const end = offset + 12 + length;
    if (end > buffer.length) return buffer;
    chunks.push({ type: buffer.toString('latin1', offset + 4, offset + 8), data: Buffer.from(buffer.subarray(offset + 8, offset + 8 + length)) });
    offset = end;
    if (chunks.at(-1).type === 'IEND') break;
  }
  const palette = chunks.find(chunk => chunk.type === 'PLTE');
  const alpha = chunks.find(chunk => chunk.type === 'tRNS');
  if (!palette || !alpha) return buffer;
  let changed = false;
  for (let index = 0; index < alpha.data.length && index * 3 + 2 < palette.data.length; index += 1) {
    if (alpha.data[index] < 250) {
      palette.data.fill(255, index * 3, index * 3 + 3);
      alpha.data[index] = 255;
      changed = true;
    }
  }
  if (!changed) return buffer;
  return Buffer.concat([SIGNATURE, ...chunks.map(({ type, data }) => {
    const head = Buffer.alloc(8);
    head.writeUInt32BE(data.length, 0);
    head.write(type, 4, 'latin1');
    const crc = Buffer.alloc(4);
    crc.writeUInt32BE(crc32(Buffer.concat([head.subarray(4), data])), 0);
    return Buffer.concat([head, data, crc]);
  })]);
}

const CRC_TABLE = Array.from({ length: 256 }, (_, n) => {
  let c = n;
  for (let k = 0; k < 8; k += 1) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});
function crc32(buffer) {
  let crc = 0xffffffff;
  for (const byte of buffer) crc = CRC_TABLE[(crc ^ byte) & 0xff] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

function prunePcSessions() {
  const now = Date.now();
  for (const [key, session] of pcSessions) if (session.expires <= now) pcSessions.delete(key);
  while (pcSessions.size >= PC_SESSION_MAX) pcSessions.delete(pcSessions.keys().next().value);
}

export async function pcVoteCaptcha(input) {
  pcInput(input);
  const jar = new Map();
  const view = await pcFetch(pcViewUrl(input), jar);
  if (view.status !== 200) throw Object.assign(new Error('PC view unavailable'), { name: 'HTTPError', status: view.status });
  const { fields, captcha } = parseViewForm(await view.text(), input.no);
  const query = new URLSearchParams({ gall_id: input.gallery, kcaptcha_type: 'recommend', time: String(Date.now()), _GALLTYPE_: input.galltype });
  const response = await pcFetch(PC_HOST + '/kcaptcha/image_v3/?' + query, jar, { headers: { Referer: pcViewUrl(input) } });
  const contentType = (response.headers.get('content-type') || '').split(';')[0].trim().toLowerCase();
  const body = Buffer.from(await response.arrayBuffer());
  if (response.status !== 200 || !/^image\/[a-z0-9.+-]+$/.test(contentType) || !body.length || body.length > MAX_IMAGE) throw new Error('Invalid captcha image');
  prunePcSessions();
  pcSessions.set(input.key, { jar, fields, gallery: input.gallery, no: input.no, galltype: input.galltype, expires: Date.now() + PC_SESSION_TTL });
  return { content_type: contentType, base64: clearTranslucentNoise(body).toString('base64'), captcha };
}

export async function pcVote(input, mode) {
  const pc = input.pc || {};
  pcInput({ gallery: input.gallery, no: input.no, galltype: pc.galltype, key: pc.key });
  if (typeof pc.code !== 'string' || !pc.code.trim() || pc.code.length > 10) throw new Error('Invalid PC vote request');
  const session = pcSessions.get(pc.key);
  pcSessions.delete(pc.key);
  if (!session || session.expires <= Date.now() || session.gallery !== input.gallery || session.no !== input.no || session.galltype !== pc.galltype) {
    // 아무것도 보내지 않았다. 새 문자를 받아 다시 입력하게 한다.
    throw Object.assign(new Error('expired'), { name: 'CaptchaRequiredError', cause: '보안 문자가 만료됐어요. 새 문자를 입력해 주세요.', sent: false });
  }
  const token = session.jar.get('ci_c');
  if (!token) throw Object.assign(new Error('PC token missing'), { sent: false });
  session.jar.set(input.gallery + input.no + '_Firstcheck' + (mode === 'D' ? '_down' : ''), 'Y');
  const body = new URLSearchParams(session.fields);
  for (const [name, value] of Object.entries({ ci_t: token, id: input.gallery, no: String(input.no), mode, code_recommend: pc.code.trim(), _GALLTYPE_: session.galltype, link_id: input.gallery })) body.append(name, value);
  const response = await pcFetch(PC_HOST + '/board/recommend/vote', session.jar, {
    method: 'POST',
    body,
    headers: { 'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8', 'X-Requested-With': 'XMLHttpRequest', Origin: PC_HOST, Referer: pcViewUrl(session) },
  });
  const text = (await response.text()).trim();
  const parts = text.split('||');
  if (response.status === 200 && parts[0] === 'true') return { result: true, count: /^[0-9,]+$/.test(parts[1] || '') ? Number(parts[1].replace(/,/g, '')) : null };
  if (response.status === 200 && parts[0] === 'false') {
    const cause = (parts[1] === 'nomember' ? parts[2] : parts[1]) || '';
    throw Object.assign(new Error('rejected'), { name: /코드|문자/.test(cause) ? 'CaptchaRequiredError' : 'ApiError', cause: cause.slice(0, 200) });
  }
  throw Object.assign(new Error('Unexpected PC vote response'), { name: 'HTTPError', status: response.status });
}
