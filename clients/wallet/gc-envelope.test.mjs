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
