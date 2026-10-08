import { DCInside, captchaUrl } from '@green-1052/dcinside.js';
import { readFile, mkdir, open, rename, unlink, chmod } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { randomBytes } from 'node:crypto';
import { createInterface } from 'node:readline';

const statePath = process.env.MIRROR_DC_APP_STATE || resolve(dirname(fileURLToPath(import.meta.url)), '../instance/dc_app_credentials.json');
const dc = new DCInside({ http: { timeout: 20000 } });
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const MAX_IMAGE = 200 * 1024;
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

async function handle(input) {
  if (input.op === 'ping') return { ready: true };
  if (input.op === 'captcha') return captchaImage(input);
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
  if (!['write', 'upvote'].includes(input.op)) throw new Error('Unsupported operation');
  if (input.op === 'write') {
    // Future login support belongs here; never reuse the preceding writer's session.
    if (input.session?.type !== 'anonymous') throw new Error('Unsupported session');
    dc.useAnonymous(input.session.nickname, input.session.password);
  }
  try {
    await settled();
    const send = () => input.op === 'write'
      ? dc.articles.write({ gallery: input.gallery, subject: input.subject, content: [input.body], headText: input.head, captcha: input.captcha, validateOnly: input.validateOnly === true })
      : dc.articles.upvote(input.gallery, input.no, input.captcha);
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
    result = { id: input?.id ?? null, ok: false, error: {
      name: apiError || error.name === 'HTTPError' ? error.name : 'WorkerError',
      cause: apiError && typeof error.cause === 'string' ? error.cause : '',
      status: Number.isInteger(error.status) ? error.status : null,
    } };
  }
  process.stdout.write(`${JSON.stringify(result)}\n`);
}
