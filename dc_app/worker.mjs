import { DCInside, captchaUrl, decodeHtml } from '@green-1052/dcinside.js';
import { readFile, mkdir, open, rename, unlink, chmod } from 'node:fs/promises';
import { basename, dirname, isAbsolute, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomBytes, createHash } from 'node:crypto';
import { createInterface } from 'node:readline';
import { pcVote, pcVoteCaptcha } from './pc_vote.mjs';

const statePath = process.env.MIRROR_DC_APP_STATE || resolve(dirname(fileURLToPath(import.meta.url)), '../instance/dc_app_credentials.json');
const dc = new DCInside({ http: { timeout: 45000 } });
// certification_login is an answered rejection, not a transport failure.
// Returning false makes Http.send throw rather than replay the mutation.
dc.http.context.relogin = async () => false;
// 2026-10-23 앱 업데이트 이후 디시는 app_id 발급 요청의 multipart 본문을 거절한다.
// 공식 앱과 같이 urlencoded 폼으로 보내도록 이 요청에만 옵션을 덧붙인다.
const sendPost = dc.http.post;
dc.http.post = function (url, fields, options) {
  return String(url).endsWith('/auth/mobile_app_verification')
    ? sendPost.call(this, url, fields, { ...options, urlencoded: true })
    : sendPost.call(this, url, fields, options);
};
const loginId = process.env.MIRROR_DC_LOGIN_ID || '';
const loginPassword = process.env.MIRROR_DC_LOGIN_PASSWORD || '';
let loginSession = null;
let otpToken;
let loginBlocked = false;
let loginReason = null;
let lastLoginAttempt = null;
const LOGIN_INTERVAL_MS = 10000;
function accountStatus() {
  return { configured: Boolean(loginId && loginPassword), logged_in: loginSession !== null,
    user_id: loginSession?.userId ?? null, name: loginSession?.result?.name ?? null,
    blocked: loginBlocked, reason: loginReason };
}
function loginNeeded(reason) {
  return Object.assign(new Error('Login needed'), { name: 'LoginNeeded', reason, sent: false,
    ...(reason === 'captcha' ? { captcha_key: randomBytes(8).toString('hex') } : {}) });
}
async function accountLogin(input = {}) {
  // Both explicit and lazy login share this clock. Rejection must not change
  // the session/failure state or extend the interval.
  if (loginId && loginPassword) {
    const now = Date.now();
    const remaining = lastLoginAttempt === null ? 0 : LOGIN_INTERVAL_MS - (now - lastLoginAttempt);
    if (remaining > 0) throw Object.assign(new Error('Login cooldown'), {
      name: 'LoginCooldown', retry_after: Math.ceil(remaining / 1000), sent: false,
    });
    lastLoginAttempt = now;
  }
  loginSession = null;
  try {
    if (!loginId || !loginPassword) throw new Error('Not configured');
    await settled();
    loginSession = await dc.auth.login(loginId, loginPassword, {
      otp: input.otp, captcha: input.captcha, otpToken,
    });
    otpToken = loginSession.result?.otp_token ?? otpToken;
    loginBlocked = false;
    loginReason = null;
    return { result: true, ...accountStatus() };
  } catch (error) {
    loginBlocked = true;
    loginReason = error.name === 'OtpRequiredError' ? 'otp'
      : error.name === 'CaptchaRequiredError' ? 'captcha' : 'failed';
    throw loginNeeded(loginReason);
  }
}
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const MAX_IMAGE = 200 * 1024;
const MAX_UPLOAD = 10 * 1024 * 1024;
const IMAGE_TYPES = new Set(['image/jpeg', 'image/png', 'image/gif', 'image/webp']);
const IMAGE_NAME = /^[A-Za-z0-9_-]+\.(jpg|png|gif|webp)$/;
const COMMENT_MAX = 400;
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
  if (!['article', 'comment', 'recommend', 'login'].includes(input.kind) || !/^[0-9a-f]{16}$/.test(input.key)
      || (input.kind !== 'login' && !/^[A-Za-z0-9_]{1,80}$/.test(input.gallery))) throw new Error('Invalid captcha request');
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

async function uploadedImages(input) {
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
  return images;
}

async function writeContent(input) {
  const images = await uploadedImages(input);
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

// 댓글 요청을 디시에 보내기 전에 확인한다. 답글은 부모 댓글 번호와 그 작성자 아이디(유동은 빈 값)를 쓴다.
function commentArgs(input) {
  const invalid = () => Object.assign(new Error('Invalid comment'), { sent: false });
  if (!/^[A-Za-z0-9_]{1,80}$/.test(input.gallery) || !Number.isSafeInteger(input.no) || input.no < 1) throw invalid();
  if (typeof input.content !== 'string' || !input.content.trim() || [...input.content].length > COMMENT_MAX) throw invalid();
  if (input.parent === undefined || input.parent === null) return { content: input.content, parent: undefined };
  const { comment_no: commentNo, user_id: userId } = input.parent;
  if (!Number.isSafeInteger(commentNo) || commentNo < 1 || typeof userId !== 'string' || !/^[A-Za-z0-9_]{0,40}$/.test(userId)) throw invalid();
  return { content: input.content, parent: { comment_no: commentNo, user_id: userId } };
}

const invalidMutation = () => Object.assign(new Error('Invalid request'), { sent: false });
const staleMutation = () => Object.assign(new Error('Stale snapshot'), { name: 'WorkerError', cause: 'stale', sent: false });

function mutationArgs(input) {
  const session = input.session;
  if (typeof input.gallery !== 'string' || !/^[A-Za-z0-9_]{1,80}$/.test(input.gallery)
      || !Number.isSafeInteger(input.no) || input.no < 1
      || (session?.type !== 'login' && (session?.type !== 'anonymous' || typeof session.nickname !== 'string'
      || !session.nickname.trim() || [...session.nickname].length > 20
      || typeof session.password !== 'string' || !session.password.trim()
      || [...session.password].length < 2 || [...session.password].length > 20))) throw invalidMutation();
  if (input.op === 'comment_delete' && (!Number.isSafeInteger(input.comment_no) || input.comment_no < 1)) throw invalidMutation();
  if (input.op !== 'modify') return;
  if (typeof input.subject !== 'string' || !input.subject.trim() || [...input.subject].length > 40
      || typeof input.fingerprint !== 'string' || !/^[a-f0-9]{64}$/i.test(input.fingerprint)
      || !Array.isArray(input.blocks) || !input.blocks.length) throw invalidMutation();
  if (input.head != null && (!Number.isSafeInteger(input.head.no) || input.head.no < 0
      || typeof input.head.name !== 'string' || input.head.name.length > 80)) throw invalidMutation();
  if (input.captcha != null && (typeof input.captcha.key !== 'string' || !/^[a-f0-9]{16}$/.test(input.captcha.key)
      || typeof input.captcha.code !== 'string' || !input.captcha.code.trim() || [...input.captcha.code].length > 10)) throw invalidMutation();
  const keeps = new Set(), images = new Set();
  let textLength = 0;
  for (const block of input.blocks) {
    if (!block || typeof block !== 'object' || Object.keys(block).length !== 1) throw invalidMutation();
    if (typeof block.text === 'string' && block.text.trim()) textLength += [...block.text].length;
    else if (Number.isSafeInteger(block.keep) && block.keep >= 0 && !keeps.has(block.keep)) keeps.add(block.keep);
    else if (Number.isSafeInteger(block.image) && block.image >= 0 && !images.has(block.image)) images.add(block.image);
    else throw invalidMutation();
  }
  const uploads = input.images ?? [];
  if (textLength > 10000 || !Array.isArray(uploads) || images.size !== uploads.length
      || [...images].some(index => index >= uploads.length)) throw invalidMutation();
}

function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical);
  if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(key => [key, canonical(value[key])]));
  return value;
}

function isPhotoBlock(html, files) {
  // Provisional: the live photo memo/file sample decides this format.
  return /<img\b/i.test(html);
}

function deletedFiles(info, removedPhotoBlockIndexes) {
  // Provisional: the live photo memo/file sample decides file IDs. Never guess deletions.
  return [];
}

function modifySnapshot(info) {
  const memo = info.memo ?? [], files = info.file ?? [];
  const htmlBlocks = memo.flatMap(block => Object.entries(block))
    .filter(([key]) => /^memo_block_\d+$/.test(key))
    .sort(([a], [b]) => Number(a.slice(11)) - Number(b.slice(11)))
    .map(([, value]) => decodeHtml(value));
  const blocks = htmlBlocks.map((html, index) => {
    if (isPhotoBlock(html, files)) {
      const src = /<img\b[^>]*?\s+src\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s>]+))/i.exec(html);
      return { kind: 'photo', index, url: src ? decodeHtml(src[1] ?? src[2] ?? src[3]) : null };
    }
    const plain = /^<div>((?:[^<]|<br\s*\/?>)*)<\/div>$/i.exec(html);
    if (plain) return { kind: 'text', text: decodeHtml(plain[1].replace(/<br\s*\/?>/gi, '\n')) };
    return { kind: 'html', index, preview: [...decodeHtml(html.replace(/<[^>]*>/g, ''))].slice(0, 80).join('') };
  });
  const reason = [true, 'true', 1, '1'].includes(info.secret) ? 'secret'
    : info.poll?.length ? 'poll' : info.movie?.length ? 'movie' : info.aiImg?.length ? 'ai_image' : null;
  const fingerprint = createHash('sha256').update(JSON.stringify(canonical({ subject: info.subject ?? '', memo, file: files }))).digest('hex');
  const head = Array.isArray(info.head_text) ? info.head_text.find(entry => entry?.no === info.headid) : null;
  return {
    htmlBlocks,
    normalized: { result: true, subject: info.subject ?? '', head_id: Number.isInteger(info.headid) ? info.headid : null,
      head_name: typeof head?.name === 'string' ? head.name : null,
      blocks, fingerprint, editable: reason === null, reason },
  };
}

async function modifyContent(input, images) {
  let info;
  try {
    info = await dc.articles.modifyInfo(input.gallery, input.no);
  } catch (error) {
    // Loading the snapshot is not a mutation, even if its transport fails.
    error.sent = false;
    throw error;
  }
  if ([false, 'false'].includes(info.result)) return { rejection: info };
  const snapshot = modifySnapshot(info);
  if (!snapshot.normalized.editable || snapshot.normalized.fingerprint !== input.fingerprint.toLowerCase()) throw staleMutation();
  const kept = new Set();
  const content = input.blocks.map(block => {
    if (block.text !== undefined) return block.text;
    if (block.image !== undefined) return images[block.image];
    if (block.keep >= snapshot.htmlBlocks.length) throw invalidMutation();
    kept.add(block.keep);
    return { type: 'html', html: snapshot.htmlBlocks[block.keep] };
  });
  const removed = snapshot.normalized.blocks.filter(block => block.kind === 'photo' && !kept.has(block.index)).map(block => block.index);
  return { content, deleteFiles: deletedFiles(info, removed), autoDeleteTime: info.auto_del_time,
    ...([true, 'true', 1, '1'].includes(info.fix) ? { fix: true } : {}) };
}

async function handle(input) {
  try {
    return await dispatch(input);
  } catch (error) {
    if (input.session?.type === 'login' && error.name === 'AuthExpiredError' && error.kind === 'login') {
      loginSession = null;
      loginReason = 'expired';
      throw loginNeeded('expired');
    }
    throw error;
  } finally {
    dc.session = null;
  }
}

async function dispatch(input) {
  if (input.op === 'ping') return { ready: true };
  if (input.op === 'account_status') return accountStatus();
  if (input.op === 'account_login') return accountLogin(input);
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
  const mutation = ['delete', 'comment_delete', 'modify_info', 'modify'].includes(input.op);
  if (!['write', 'comment', 'upvote', 'downvote'].includes(input.op) && !mutation) throw new Error('Unsupported operation');
  // 보안 문자 답이 있는 추천·비추천은 그 문자를 받은 PC 쿠키 세션으로 보낸다.
  if (input.pc !== undefined && ['upvote', 'downvote'].includes(input.op)) return pcVote(input, input.op === 'upvote' ? 'U' : 'D');
  let content;
  let comment;
  let images;
  if (mutation) {
    mutationArgs(input);
    if (input.op === 'modify') images = await uploadedImages(input);
  }
  if (input.op === 'write' || input.op === 'comment' || mutation) {
    if (!['anonymous', 'login'].includes(input.session?.type)) throw new Error('Unsupported session');
    if (input.op === 'write') content = await writeContent(input);
    else if (input.op === 'comment') comment = commentArgs(input);
    if (input.session.type === 'login') {
      if (loginBlocked) throw loginNeeded(loginReason);
      if (!loginSession) await accountLogin();
      dc.session = loginSession;
    } else dc.useAnonymous(input.session.nickname, input.session.password);
  }
  try {
    try {
      await settled();
    } catch (error) {
      error.sent = false;
      throw error;
    }
    if (input.op === 'modify_info') {
      const info = await dc.articles.modifyInfo(input.gallery, input.no);
      return [false, 'false'].includes(info.result) ? info : modifySnapshot(info).normalized;
    }
    if (input.op === 'modify') {
      let prepared;
      try {
        prepared = await modifyContent(input, images);
      } catch (error) {
        error.sent = false;
        throw error;
      }
      if (prepared.rejection) return prepared.rejection;
      return await dc.articles.write({ gallery: input.gallery, articleNo: input.no, subject: input.subject,
        headText: input.head, captcha: input.captcha, useGalleryNickname: false, ...prepared });
    }
    const send = () => {
      if (input.op === 'delete') return dc.articles.delete(input.gallery, input.no);
      if (input.op === 'comment_delete') return dc.comments.delete(input.gallery, input.no, input.comment_no);
      if (input.op === 'write') return dc.articles.write({ gallery: input.gallery, subject: input.subject, content, headText: input.head, captcha: input.captcha, validateOnly: input.validateOnly === true, useGalleryNickname: false });
      if (input.op === 'comment') return dc.comments.write(input.gallery, input.no, comment.content, { parent: comment.parent, captcha: input.captcha ?? undefined, useGalleryNickname: false })
        .then(result => input.session?.type === 'login'
          ? { ...result, author: { name: loginSession.result?.name ?? null, user_id: loginSession.userId } } : result);
      return input.op === 'upvote'
        ? dc.articles.upvote(input.gallery, input.no, input.captcha)
        : dc.articles.downvote(input.gallery, input.no, input.captcha);
    };
    try {
      return await send();
    } catch (error) {
      // 새로 받은 app_id가 자리 잡기 전이면 디시가 이 문구로 거절한다. 받지 않은 요청이라 한 번만 다시 보낸다.
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
    const account = input?.session?.type === 'login';
    result = { id: input?.id ?? null, ok: false, error: {
      name: apiError || error.name === 'HTTPError' ? error.name : 'WorkerError',
      cause: apiError && typeof error.cause === 'string' ? error.cause
        : error.name === 'WorkerError' && error.sent === false && error.cause === 'stale' ? 'stale' : '',
      status: Number.isInteger(error.status) ? error.status : null,
      ...(vpnBan ? { vpn_ban: true } : {}),
      ...(error.sent === false ? { sent: false } : {}),
    } };
    if (account && apiError && !['잠시후 다시 이용해주세요.', '잠시 후 다시 시도해 주세요.'].includes(result.error.cause)) {
      result.error.cause = error.name === 'CaptchaRequiredError' ? '보안 코드를 입력해 주세요.' : '디시에서 요청을 받지 않았어요.';
    }
    if (error.name === 'LoginNeeded') result.error = {
      name: 'LoginNeeded', reason: error.reason, sent: false,
      ...(error.captcha_key ? { captcha_key: error.captcha_key } : {}),
    };
    if (error.name === 'LoginCooldown') result.error = {
      name: 'LoginCooldown', retry_after: error.retry_after, sent: false,
    };
  }
  process.stdout.write(`${JSON.stringify(result)}\n`);
}
