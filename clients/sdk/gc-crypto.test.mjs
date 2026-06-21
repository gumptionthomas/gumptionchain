import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  base64encode, base64decode, millHash, sha256Hex,
} from './gc-crypto.mjs';

const hex = (u8) => Buffer.from(u8).toString('hex');

test('base64 round-trips', () => {
  const u8 = Uint8Array.from([0, 1, 250, 99, 7]);
  assert.equal(hex(base64decode(base64encode(u8))), hex(u8));
});

test('millHash is sha256(sha512(x)) — matches Python', async () => {
  const out = await millHash(new TextEncoder().encode('abc'));
  assert.equal(hex(out), '2b8e2baefea41ddf88d7ccd66550cb9493970ea7854d2e74eb33e57cd3c73d9c');
});

test('sha256Hex matches the known SHA-256 of "abc"', async () => {
  const out = await sha256Hex(new TextEncoder().encode('abc'));
  assert.equal(out, 'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad');
});
