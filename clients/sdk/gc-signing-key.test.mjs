import assert from 'node:assert/strict';
import { test } from 'node:test';
import { SigningKey } from './gc-signing-key.mjs';

test('isSupported() returns true on a runtime with WebCrypto Ed25519', async () => {
  // Node 20+ (and current browsers) support Ed25519, so the probe succeeds.
  assert.equal(await SigningKey.isSupported(), true);
});

test('fresh keygen: address is gc1…, sign/verify round-trips', async () => {
  const w = await SigningKey.generate();
  const addr = await w.address();
  assert.ok(addr.startsWith('gc1'));
  const msg = new TextEncoder().encode('hello');
  const sig = await w.sign(msg);
  assert.equal(await w.verify(msg, sig), true);
});

test('exportSecret round-trips through fromSecret', async () => {
  const w = await SigningKey.generate();
  const secret = await w.exportSecret();
  assert.ok(secret.startsWith('gcsec1'));
  const w2 = await SigningKey.fromSecret(secret);
  assert.equal(await w2.address(), await w.address());
  // Ed25519 is deterministic → identical signatures.
  const msg = new TextEncoder().encode('hi');
  assert.equal(await w2.sign(msg), await w.sign(msg));
});

test('fromPublicKeyB64 yields a verify-only key with the same address', async () => {
  const w = await SigningKey.generate();
  const pub = await SigningKey.fromPublicKeyB64(await w.publicKeyB64());
  assert.equal(await pub.address(), await w.address());
  const msg = new TextEncoder().encode('x');
  assert.equal(await pub.verify(msg, await w.sign(msg)), true);
});

test('verify-only key cannot sign or export the secret', async () => {
  const w = await SigningKey.generate();
  const pub = await SigningKey.fromPublicKeyB64(await w.publicKeyB64());
  await assert.rejects(() => pub.sign(new Uint8Array([1])));
  await assert.rejects(() => pub.exportSecret());
});

test('fromSecret rejects a corrupted secret', async () => {
  const w = await SigningKey.generate();
  const secret = await w.exportSecret();
  const bad = secret.slice(0, -1) + (secret.at(-1) === 'q' ? 'p' : 'q');
  await assert.rejects(() => SigningKey.fromSecret(bad));
});

test('mnemonic() round-trips through fromMnemonic to the same address', async () => {
  const w = await SigningKey.generate();
  const phrase = await w.mnemonic();
  assert.equal(phrase.split(' ').length, 24);
  const w2 = await SigningKey.fromMnemonic(phrase);
  assert.equal(await w2.address(), await w.address());
});

test('fromMnemonic rejects a corrupted phrase', async () => {
  const w = await SigningKey.generate();
  const words = (await w.mnemonic()).split(' ');
  words[23] = words[23] === 'zoo' ? 'zone' : 'zoo';
  await assert.rejects(() => SigningKey.fromMnemonic(words.join(' ')), /checksum/);
});
