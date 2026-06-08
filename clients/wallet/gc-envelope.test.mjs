import { test } from 'node:test';
import assert from 'node:assert/strict';
import { seal, open } from './gc-envelope.mjs';

const PRF = new Uint8Array(32).fill(7);
const OTHER = new Uint8Array(32).fill(9);
const hex = (u8) => Buffer.from(u8).toString('hex');

test('seal -> open round-trips the plaintext', async () => {
  const msg = new TextEncoder().encode('the quick brown fox');
  const sealed = await seal(PRF, msg);
  const out = await open(PRF, sealed);
  assert.equal(hex(out), hex(msg));
});

test('open fails closed on a tampered ciphertext (GCM auth)', async () => {
  const sealed = await seal(PRF, new TextEncoder().encode('secret'));
  sealed.ciphertext[0] ^= 0xff;
  await assert.rejects(() => open(PRF, sealed));
});

test('open fails with a different PRF output (wrong authenticator)', async () => {
  const sealed = await seal(PRF, new TextEncoder().encode('secret'));
  await assert.rejects(() => open(OTHER, sealed));
});

test('each seal uses a fresh IV', async () => {
  const msg = new TextEncoder().encode('x');
  const a = await seal(PRF, msg);
  const b = await seal(PRF, msg);
  assert.notEqual(hex(a.iv), hex(b.iv));
  assert.notEqual(hex(a.ciphertext), hex(b.ciphertext));
});

import { sealWithKey, openWithKey, deriveAesKey } from './gc-envelope.mjs';

test('sealWithKey/openWithKey round-trip with a fixed CryptoKey', async () => {
  const raw = new Uint8Array(32).fill(5);
  const key = await crypto.subtle.importKey(
    'raw', raw, { name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt'],
  );
  const msg = new TextEncoder().encode('hello-primitive');
  const env = await sealWithKey(key, msg);
  assert.equal(env.iv.length, 12);
  const out = await openWithKey(key, env);
  assert.deepEqual(out, msg);
});

test('openWithKey fails closed on a tampered ciphertext', async () => {
  const raw = new Uint8Array(32).fill(6);
  const key = await crypto.subtle.importKey(
    'raw', raw, { name: 'AES-GCM', length: 256 }, false, ['encrypt', 'decrypt'],
  );
  const env = await sealWithKey(key, new TextEncoder().encode('x'));
  env.ciphertext[0] ^= 0xff;
  await assert.rejects(() => openWithKey(key, env));
});

test('exported deriveAesKey backs seal/open (HKDF -> AES-GCM key reuse)', async () => {
  // The keyring reuses deriveAesKey directly; assert the exported derivation
  // produces a key whose sealWithKey/openWithKey round-trips and matches the
  // seal/open PRF wrappers (same HKDF derivation under the hood).
  const key = await deriveAesKey(PRF);
  const msg = new TextEncoder().encode('keyring reuse');
  const env = await sealWithKey(key, msg);
  assert.deepEqual(await openWithKey(key, env), msg);
  // Cross-check: open() (PRF wrapper) decrypts a sealWithKey(deriveAesKey)
  // envelope, proving the exported path is the same derivation.
  assert.deepEqual(await open(PRF, env), msg);
});
