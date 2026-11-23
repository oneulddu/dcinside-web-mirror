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
const tunnelHead = `CONNECT app.dcinside.com:443 HTTP/1.1\r\nProxy-Authorization: ${basic('secret')}\r\n\r\n`;

test('client FIN reaches upstream and the complete buffered reply reaches client', async () => {
  const bytes = 'a'.repeat(4 * 1024 * 1024);
  const target = net.createServer({allowHalfOpen:true}, socket => {
    let body = '';
    socket.on('data', chunk => { body += chunk; });
    socket.on('end', () => socket.end(body === 'request' ? bytes : 'bad request'));
  });
  await new Promise(resolve => target.listen(0, '127.0.0.1', resolve));
  try {
    await withRelay({token:'secret', allowFrom:new Set(['127.0.0.1']),
      connectUpstream: (opts, cb) => net.connect({...opts, host:'127.0.0.1', port:target.address().port}, cb)},
    async port => {
      const socket = net.connect(port, '127.0.0.1');
      socket.end(tunnelHead + 'request');
      const chunks = [];
      for await (const chunk of socket) chunks.push(chunk);
      assert(Buffer.concat(chunks).equals(Buffer.from('HTTP/1.1 200 Connection Established\r\n\r\n' + bytes)));
    });
  } finally {
    await new Promise(resolve => target.close(resolve));
  }
});

test('upstream connection error closes the tunnel with a complete 502 reply', async () => {
  await withRelay({token:'secret', allowFrom:new Set(['127.0.0.1']), connectUpstream: () => {
    const socket = new net.Socket();
    process.nextTick(() => socket.destroy(new Error('connect failed')));
    return socket;
  }}, async port => assert.match(await connect(port, tunnelHead), /^HTTP\/1\.1 502[\s\S]*\r\n\r\n$/));
});

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
  assert.equal(authorized('Basic ' + Buffer.from('secret').toString('base64'), 'secret'), false);
  assert.equal(authorized('Bearer secret', 'secret'), false);
  assert.equal(authorized(undefined, 'secret'), false);
});

test('relay rejects bad auth, bad targets, non-CONNECT and unknown sources before connecting upstream', async () => {
  await withRelay({ token: 'secret', allowFrom: new Set(['127.0.0.1']) }, async port => {
    assert.match(await connect(port, 'CONNECT app.dcinside.com:443 HTTP/1.1\r\n\r\n'), /^HTTP\/1.1 407/);
    assert.equal(await connect(port, `CONNECT app.dcinside.com:443 HTTP/1.1\r\nX-Padding: ${'a'.repeat(8192)}\r\n\r\n`), '');
    assert.match(await connect(port, `CONNECT evil.com:443 HTTP/1.1\r\nProxy-Authorization: ${basic('secret')}\r\n\r\n`), /^HTTP\/1.1 403/);
    assert.match(await connect(port, `GET http://app.dcinside.com/ HTTP/1.1\r\nProxy-Authorization: ${basic('secret')}\r\n\r\n`), /^HTTP\/1.1 405/);
  });
  await withRelay({ token: 'secret', allowFrom: new Set(['100.64.0.1']) }, async port => {
    assert.equal(await connect(port, `CONNECT app.dcinside.com:443 HTTP/1.1\r\nProxy-Authorization: ${basic('secret')}\r\n\r\n`), '');
  });
});
