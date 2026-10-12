import { DCInside, captchaUrl } from '@green-1052/dcinside.js';
import { readFile, mkdir, open, rename, unlink, chmod } from 'node:fs/promises';
import { basename, dirname, isAbsolute, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomBytes } from 'node:crypto';
import { createInterface } from 'node:readline';
import { pcVote, pcVoteCaptcha } from './pc_vote.mjs';

const statePath = process.env.MIRROR_DC_APP_STATE || resolve(dirname(fileURLToPath(import.meta.url)), '../instance/dc_app_credentials.json');
const dc = new DCInside({ http: { timeout: 45000 } });
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const MAX_IMAGE = 200 * 1024;
const MAX_UPLOAD = 10 * 1024 * 1024;
const IMAGE_TYPES = new Set(['image/jpeg', 'image/png', 'image/gif', 'image/webp']);
const IMAGE_NAME = /^[A-Za-z0-9_-]+\.(jpg|png|gif|webp)$/;
try {
  const credentials = JSON.parse(await readFile(statePath, 'utf8'));
  dc.auth.importCredentials(credentials);
  await chmod(statePath, 0o600);
} catch (error) {
  if (error.code !== 'ENOENT') process.stderr.write('DC credential restore failed\n');
}

async function saveCredentials() {
  const credentials = dc.auth.exportCredentials();
  if (!credentials) return;
  await mkdir(dirname(statePath), { recursive: true, mode: 0o700 });
  const temporary = `${statePath}.${process.pid}.${randomBytes(8).toString('hex')}.tmp`;
  let file;
  try {
    file = await open(temporary, 'wx', 0o600);
    await file.writeFile(JSON.stringify(credentials));
    await file.sync();
    await file.close();
    file = null;
    await rename(temporary, statePath);
  } finally {
    if (file) await file.close();
    await unlink(temporary).catch(() => {});
  }
}

async function settled() {
  await dc.auth.appId();
  await saveCredentials();
  const issued = dc.auth.exportCredentials()?.appIdIssuedAt;
  if (issued) await sleep(Math.max(0, 8000 - (Date.now() - issued)));
}

async function captchaImage(input) {
  if (!['article', 'recommend'].includes(input.kind) || !/^[0-9a-f]{16}$/.test(input.key) || !/^[A-Za-z0-9_]{1,80}$/.test(input.gallery)) throw new Error('Invalid captcha request');
  const response = await dc.http.fetch(captchaUrl(input.kind, input.key, input.gallery), { redirect: 'error' });
  const contentType = (response.headers.get('content-type') || '').split(';')[0].trim().toLowerCase();
  if (!response.ok || !/^image\/[a-z0-9.+-]+$/.test(contentType) || Number(response.headers.get('content-length')) > MAX_IMAGE) {
    await response.body?.cancel();
    throw new Error('Invalid captcha image');
  }
  const chunks = [];
  let size = 0;
  for await (const chunk of response.body) {
    size += chunk.length;
    if (size > MAX_IMAGE) throw new Error('Captcha image too large');
    chunks.push(chunk);
  }
  if (!size) throw new Error('Empty captcha image');
  return { content_type: contentType, base64: Buffer.concat(chunks).toString('base64') };
}

async function writeContent(input) {
  const images = [];
  try {
    const uploads = input.images ?? [];
    if (!Array.isArray(uploads) || uploads.length > 10) throw new Error('Invalid image');
    for (const image of uploads) {
      if (!image || typeof image.path !== 'string' || !isAbsolute(image.path)
          || !IMAGE_NAME.test(basename(image.path)) || !IMAGE_TYPES.has(image.type)
          || typeof image.name !== 'string' || !IMAGE_NAME.test(image.name)) throw new Error('Invalid image');
      const buffer = await readFile(image.path);
      if (!buffer.length || buffer.length > MAX_UPLOAD) throw new Error('Invalid image');
      images.push({ type: 'image', file: new File([buffer], image.name, { type: image.type }) });
    }
  } catch {
    // No DC calls have happened. Do not expose filesystem paths or original errors.
    throw Object.assign(new Error('Invalid image'), { sent: false });
  }
  // 서버가 본문의 [사진 N] 줄로 나눈 순서. 사진마다 정확히 한 번씩 써야 한다.
  if (input.blocks !== undefined) {
    const used = new Set();
    if (!Array.isArray(input.blocks) || input.blocks.length > images.length * 2 + 1) throw Object.assign(new Error('Invalid blocks'), { sent: false });
    const content = input.blocks.map(block => {
      if (block && typeof block.text === 'string' && block.text.trim() && block.image === undefined) return block.text;
      if (block && Number.isInteger(block.image) && block.image >= 0 && block.image < images.length && !used.has(block.image) && block.text === undefined) {
        used.add(block.image);
        return images[block.image];
      }
      throw Object.assign(new Error('Invalid blocks'), { sent: false });
    });
    if (used.size !== images.length) throw Object.assign(new Error('Invalid blocks'), { sent: false });
    return content;
  }
  const text = typeof input.body === 'string' && input.body.trim() ? [input.body] : [];
  return input.image_position === 'bottom' ? [...text, ...images] : [...images, ...text];
}

async function handle(input) {
  if (input.op === 'ping') return { ready: true };
  if (input.op === 'captcha') return captchaImage(input);
  if (input.op === 'pc_vote_captcha') return pcVoteCaptcha(input);
  // 글 하나를 앱 API로 열어 이 갤러리의 추천 설정만 읽는다. 투표는 보내지 않는다.
  if (input.op === 'vote_info') {
    if (!/^[A-Za-z0-9_]{1,80}$/.test(input.gallery) || !Number.isSafeInteger(input.no) || input.no < 1) throw new Error('Invalid vote info request');
    try {
      const response = await dc.articles.read(input.gallery, input.no);
      const info = response.view_info || {};
      const main = response.view_main || {};
      return {
        // 이름과 달리 nonrecomm_use가 true면 비추천을 끈 갤러리다(PC 화면의 비추천 버튼과 대조함).
        down: main.nonrecomm_use !== true,
        captcha: info.recommend_captcha === true,
      };
    } finally {
      await saveCredentials().catch(() => process.stderr.write('DC credential save failed\n'));
    }
  }
  if (input.op === 'gallery_info') {
    if (!/^[A-Za-z0-9_]{1,80}$/.test(input.gallery)) throw new Error('Invalid gallery request');
    try {
      const response = await dc.articles.list({ gallery: input.gallery, page: 1 });
      const info = response.gall_info?.[0] || {};
      const heads = (Array.isArray(info.head_text) ? info.head_text : [])
        .filter(head => head && Number.isInteger(head.no) && typeof head.name === 'string' && Number.isInteger(head.level))
        .map(({ no, name, level }) => ({ no, name, level }));
      return { heads, no_write: Boolean(info.no_write), captcha: Boolean(info.captcha) };
    } finally {
      await saveCredentials().catch(() => process.stderr.write('DC credential save failed\n'));
    }
  }
  if (!['write', 'upvote', 'downvote'].includes(input.op)) throw new Error('Unsupported operation');
  // 보안 문자 답이 있는 추천·비추천은 그 문자를 받은 PC 쿠키 세션으로 보낸다.
  if (input.pc !== undefined && input.op !== 'write') return pcVote(input, input.op === 'upvote' ? 'U' : 'D');
  let content;
  if (input.op === 'write') {
    // Future login support belongs here; never reuse the preceding writer's session.
    if (input.session?.type !== 'anonymous') throw new Error('Unsupported session');
    content = await writeContent(input);
    dc.useAnonymous(input.session.nickname, input.session.password);
  }
  try {
    await settled();
    const send = () => input.op === 'write'
      ? dc.articles.write({ gallery: input.gallery, subject: input.subject, content, headText: input.head, captcha: input.captcha, validateOnly: input.validateOnly === true })
      : input.op === 'upvote'
        ? dc.articles.upvote(input.gallery, input.no, input.captcha)
        : dc.articles.downvote(input.gallery, input.no, input.captcha);
    try {
      return await send();
    } catch (error) {
      const issued = dc.auth.exportCredentials()?.appIdIssuedAt;
      if (error.cause !== '잠시후 다시 이용해주세요.' || !issued || Date.now() - issued >= 20000) throw error;
      await sleep(3000);
      return await send();
    }
  } finally {
    dc.session = null;
    // A persistence failure after a successful write must not invite a duplicate write.
    await saveCredentials().catch(() => process.stderr.write('DC credential save failed\n'));
  }
}

// Await each line: the package holds a mutable session shared by all operations.
for await (const line of createInterface({ input: process.stdin, crlfDelay: Infinity })) {
  let input;
  let result;
  try {
    input = JSON.parse(line);
    result = { id: input.id, ok: true, data: await handle(input) };
  } catch (error) {
    const apiError = ['ApiError', 'CaptchaRequiredError', 'AuthExpiredError'].includes(error.name);
    const raw = Array.isArray(error.response) ? error.response[0] : error.response;
    const vpnBan = apiError && raw && typeof raw === 'object' && [true, 'true', 1, '1'].includes(raw.carrier_vpn_ban);
    result = { id: input?.id ?? null, ok: false, error: {
      name: apiError || error.name === 'HTTPError' ? error.name : 'WorkerError',
      cause: apiError && typeof error.cause === 'string' ? error.cause : '',
      status: Number.isInteger(error.status) ? error.status : null,
      ...(vpnBan ? { vpn_ban: true } : {}),
      ...(error.sent === false ? { sent: false } : {}),
    } };
  }
  process.stdout.write(`${JSON.stringify(result)}\n`);
}
