import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { Wallet } from './gc-wallet.mjs';
import { canonical, signHeaders } from './gc-sig.mjs';
import { base58encode } from './gc-crypto.mjs';

const V = JSON.parse(readFileSync(new URL('./testdata/gc-sig-vectors.json', import.meta.url)));

test('fromPrivateKeyB58 rejects a non-2048 RSA key (node would reject it)', async () => {
  // 3072 is freely generatable in Web Crypto; the node only accepts 2048.
  const pair = await crypto.subtle.generateKey(
    {
      name: 'RSASSA-PKCS1-v1_5', modulusLength: 3072,
      publicExponent: new Uint8Array([0x01, 0x00, 0x01]), hash: 'SHA-384',
    },
    true, ['sign', 'verify'],
  );
  const pkcs8 = new Uint8Array(await crypto.subtle.exportKey('pkcs8', pair.privateKey));
  await assert.rejects(
    () => Wallet.fromPrivateKeyB58(base58encode(pkcs8)),
    /modulus length/,
  );
});

test('imported fixed key derives the same address + public key as Python', async () => {
  const w = await Wallet.fromPrivateKeyB58(V.private_key_b58);
  assert.equal(await w.address(), V.address);
  assert.equal(await w.publicKeyB64(), V.public_key_b64);
});

test('canonical bytes match Python for every case', async () => {
  const w = await Wallet.fromPrivateKeyB58(V.private_key_b58);
  const addr = await w.address();
  for (const c of V.cases) {
    const bytes = await canonical({
      method: c.method, path: c.path, query: c.query,
      body: new TextEncoder().encode(c.body),
      nodeHost: c.node_host, timestamp: c.timestamp, address: addr,
    });
    assert.equal(new TextDecoder().decode(bytes), c.canonical);
  }
});

test('signatures match Python byte-for-byte (deterministic PKCS1v15)', async () => {
  const w = await Wallet.fromPrivateKeyB58(V.private_key_b58);
  for (const c of V.cases) {
    const sig = await w.sign(new TextEncoder().encode(c.canonical));
    assert.equal(sig, c.signature);
  }
});

test('fresh keygen round-trips and signHeaders has the GC-* shape', async () => {
  const w = await Wallet.generate();
  const headers = await signHeaders(w, {
    method: 'GET', path: '/api/blocks', query: '',
    body: new Uint8Array(), nodeHost: 'node.example', timestamp: '1700000000',
  });
  assert.equal(headers['GC-Sig-Version'], '1');
  assert.equal(headers['GC-Address'], await w.address());
  assert.equal(headers['GC-Public-Key'], await w.publicKeyB64());
  assert.equal(headers['GC-Timestamp'], '1700000000');
  assert.ok(headers['GC-Signature']);
});

test('exportPrivateKeyB58 round-trips through fromPrivateKeyB58', async () => {
  const w = await Wallet.fromPrivateKeyB58(V.private_key_b58);
  assert.equal(await w.exportPrivateKeyB58(), V.private_key_b58);
});
