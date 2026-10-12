const assert = require('node:assert/strict');
const net = require('node:net');
const path = require('node:path');
const { test } = require('node:test');

const load = () => import(path.resolve(__dirname, '../../scripts/dc_relay.mjs'));

function connect(port, request) {
  return new Promise(resolve => {
    const socket = net.connect(port, '127.0.0.1', () => socket.write(request));
    let data = '';
    socket.on('data', chunk => { data += chunk; });
    socket.on('close', () => resolve(data));
    socket.on('error', () => resolve(data));
  });
}

async function withRelay(options, run) {
  const { createRelay } = await load();
  const server = createRelay(options);
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try { return await run(server.address().port); } finally { server.close(); }
}

const basic = secret => 'Basic ' + Buffer.from('relay:' + secret).toString('base64');

test('relay allows only DC and app registration hosts on 443', async () => {
  const { allowedTarget } = await load();
  for (const target of ['app.dcinside.com:443', 'upload.dcinside.com:443', 'dcimg2.dcinside.co.kr:443', 'android.clients.google.com:443'])
    assert.equal(allowedTarget(target), true, target);
  for (const target of ['app.dcinside.com:80', 'evil.com:443', 'dcinside.com.evil.com:443', 'notdcinside.com:443', '127.0.0.1:443', '', 'app.dcinside.com'])
    assert.equal(allowedTarget(target), false, target);
});

test('relay checks the token in constant time form', async () => {
  const { authorized } = await load();
  assert.equal(authorized(basic('secret'), 'secret'), true);
  assert.equal(authorized(basic('secreT'), 'secret'), false);
  assert.equal(authorized('Bearer secret', 'secret'), false);
  assert.equal(authorized(undefined, 'secret'), false);
});

test('relay rejects bad auth, bad targets, non-CONNECT and unknown sources before connecting upstream', async () => {
  await withRelay({ token: 'secret', allowFrom: new Set(['127.0.0.1']) }, async port => {
    assert.match(await connect(port, 'CONNECT app.dcinside.com:443 HTTP/1.1\r\n\r\n'), /^HTTP\/1.1 407/);
    assert.match(await connect(port, `CONNECT evil.com:443 HTTP/1.1\r\nProxy-Authorization: ${basic('secret')}\r\n\r\n`), /^HTTP\/1.1 403/);
    assert.match(await connect(port, `GET http://app.dcinside.com/ HTTP/1.1\r\nProxy-Authorization: ${basic('secret')}\r\n\r\n`), /^HTTP\/1.1 405/);
  });
  await withRelay({ token: 'secret', allowFrom: new Set(['100.64.0.1']) }, async port => {
    assert.equal(await connect(port, `CONNECT app.dcinside.com:443 HTTP/1.1\r\nProxy-Authorization: ${basic('secret')}\r\n\r\n`), '');
  });
});
