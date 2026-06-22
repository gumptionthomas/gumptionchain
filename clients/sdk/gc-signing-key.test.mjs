import assert from 'node:assert/strict';
import { test } from 'node:test';
import { SigningKey } from './gc-signing-key.mjs';
import { NoSeedError } from './gc-errors.mjs';
import { signMessage } from './gc-message.mjs';

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

test('fromAddress yields a verify-only key that verifies the address owner', async () => {
  const w = await SigningKey.generate();
  const addr = await w.address();
  const pub = await SigningKey.fromAddress(addr);
  assert.equal(await pub.address(), addr);
  const msg = new TextEncoder().encode('x');
  assert.equal(await pub.verify(msg, await w.sign(msg)), true);
  await assert.rejects(() => pub.sign(msg)); // verify-only, no private key
});

test('fromAddress rejects a corrupted address', async () => {
  const addr = await (await SigningKey.generate()).address();
  const bad = addr.slice(0, -1) + (addr.at(-1) === 'q' ? 'p' : 'q');
  await assert.rejects(() => SigningKey.fromAddress(bad), /address/);
});

test('fromSecretSignOnly: signs + correct address, but the seed is non-extractable', async () => {
  const full = await SigningKey.generate();
  const gcsec = await full.exportSecret();
  const addr = await full.address();
  const signOnly = await SigningKey.fromSecretSignOnly(gcsec);
  assert.equal(await signOnly.address(), addr);
  const sig = await signOnly.sign(new TextEncoder().encode('hi'));
  assert.equal(await full.verify(new TextEncoder().encode('hi'), sig), true);
  await assert.rejects(() => signOnly.exportSecret(), NoSeedError);
  await assert.rejects(() => signOnly.mnemonic(), NoSeedError);
});

test('signMessage works with a sign-only key', async () => {
  const full = await SigningKey.generate();
  const signOnly = await SigningKey.fromSecretSignOnly(await full.exportSecret());
  const proof = await signMessage(signOnly, 'login:abc');
  assert.equal(proof.scheme, 'gc-msg-v1');
  assert.equal(proof.address, await full.address());
});

test('toSignOnlyHandle round-trips and is non-extractable; a normal key still exports', async () => {
  const full = await SigningKey.generate();
  const handle = await full.toSignOnlyHandle();
  assert.equal(handle.privateKey.extractable, false);
  const rehydrated = SigningKey.fromSignOnlyHandle(handle);
  assert.equal(await rehydrated.address(), handle.address);
  await assert.rejects(() => rehydrated.exportSecret(), NoSeedError);
  assert.ok((await full.exportSecret()).startsWith('gcsec1'));
});
