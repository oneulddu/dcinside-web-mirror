#!/usr/bin/env node
// 디시 쓰기·추천용 집 회선 중계기. 운영 서버의 DC 워커만 이 맥을 거쳐 DC에 접속한다.
// HTTPS CONNECT만 받고, 허용한 출발지·목적지·토큰이 모두 맞을 때만 터널을 연다.
//
// 환경 변수
//   DC_RELAY_HOST        바인딩 주소 (테일스케일 IP 권장)
//   DC_RELAY_PORT        포트 (기본 8899)
//   DC_RELAY_TOKEN_FILE  토큰 파일 (한 줄). 프록시 주소의 비밀번호 자리에 같은 값을 넣는다.
//   DC_RELAY_ALLOW_FROM  접속을 허용할 IP 목록 (쉼표 구분)
import net from 'node:net';
import { readFileSync } from 'node:fs';
import { timingSafeEqual } from 'node:crypto';
import { pathToFileURL } from 'node:url';

const ALLOWED_HOSTS = [
  /^([a-z0-9-]+\.)*dcinside\.com$/,
  /^([a-z0-9-]+\.)*dcinside\.co\.kr$/,
  // 앱 기기 등록(app_id 발급)에 쓰는 구글 주소
  /^android\.clients\.google\.com$/,
  /^firebaseinstallations\.googleapis\.com$/,
  /^android\.apis\.google\.com$/,
];

export function allowedTarget(target) {
  const match = /^([a-z0-9.-]{1,253}):443$/i.exec(String(target || ''));
  return Boolean(match) && ALLOWED_HOSTS.some(pattern => pattern.test(match[1].toLowerCase()));
}

export function authorized(header, token) {
  const match = /^Basic\s+([A-Za-z0-9+/=]+)$/.exec(String(header || '').trim());
  if (!match || !token) return false;
  const decoded = Buffer.from(match[1], 'base64').toString('utf8');
  const secret = Buffer.from(decoded.slice(decoded.indexOf(':') + 1));
  const expected = Buffer.from(token);
  return secret.length === expected.length && timingSafeEqual(secret, expected);
}

export function normalizeAddress(address) {
  return String(address || '').replace(/^::ffff:/, '');
}

export function createRelay({ token, allowFrom, log = () => {} }) {
  const server = net.createServer(client => {
    const source = normalizeAddress(client.remoteAddress);
    client.setTimeout(30000);
    client.on('timeout', () => client.destroy());
    client.on('error', () => {});
    if (allowFrom.size && !allowFrom.has(source)) {
      log(`deny source ${source}`);
      client.destroy();
      return;
    }
    let head = Buffer.alloc(0);
    const onData = chunk => {
      head = Buffer.concat([head, chunk]);
      const end = head.indexOf('\r\n\r\n');
      if (end < 0) {
        if (head.length > 8192) client.destroy();
        return;
      }
      client.off('data', onData);
      client.pause();
      const lines = head.subarray(0, end).toString('latin1').split('\r\n');
      const rest = head.subarray(end + 4);
      const [method, target] = lines[0].split(' ');
      const auth = lines.slice(1).find(line => /^proxy-authorization:/i.test(line))?.replace(/^[^:]+:/, '');
      const reply = (status, text) => {
        client.end(`HTTP/1.1 ${status} ${text}\r\nConnection: close\r\nContent-Length: 0\r\n${status === 407 ? 'Proxy-Authenticate: Basic realm="dc-relay"\r\n' : ''}\r\n`);
      };
      if (method !== 'CONNECT') return reply(405, 'Method Not Allowed');
      if (!authorized(auth, token)) { log('deny auth'); return reply(407, 'Proxy Authentication Required'); }
      if (!allowedTarget(target)) { log(`deny target ${String(target).slice(0, 80)}`); return reply(403, 'Forbidden'); }
      const [host] = target.split(':');
      let established = false;
      const upstream = net.connect({ host, port: 443 }, () => {
        established = true;
        client.write('HTTP/1.1 200 Connection Established\r\n\r\n');
        if (rest.length) upstream.write(rest);
        client.setTimeout(120000);
        upstream.pipe(client);
        client.pipe(upstream);
        client.resume();
      });
      upstream.setTimeout(120000, () => upstream.destroy());
      upstream.on('error', () => {
        if (!established && !client.destroyed) reply(502, 'Bad Gateway');
        else client.destroy();
      });
      upstream.on('close', () => client.destroy());
      client.on('close', () => upstream.destroy());
      log(`connect ${host}`);
    };
    client.on('data', onData);
  });
  return server;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const host = process.env.DC_RELAY_HOST;
  const port = Number(process.env.DC_RELAY_PORT || 8899);
  const token = readFileSync(process.env.DC_RELAY_TOKEN_FILE, 'utf8').trim();
  const allowFrom = new Set(String(process.env.DC_RELAY_ALLOW_FROM || '').split(',').map(value => value.trim()).filter(Boolean));
  if (!host || !token || !allowFrom.size) {
    process.stderr.write('DC_RELAY_HOST, DC_RELAY_TOKEN_FILE, DC_RELAY_ALLOW_FROM are required\n');
    process.exit(2);
  }
  const log = message => process.stdout.write(`${new Date().toISOString()} ${message}\n`);
  createRelay({ token, allowFrom, log }).listen(port, host, () => log(`listening ${host}:${port}`));
}
