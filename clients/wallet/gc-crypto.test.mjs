import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  base58encode, base58decode, base64encode, base64decode, millHash, sha256Hex,
} from './gc-crypto.mjs';

const hex = (u8) => Buffer.from(u8).toString('hex');

test('base58 matches the Python base58check lib (plain, no checksum)', () => {
  const bytes0to31 = Uint8Array.from({ length: 32 }, (_, i) => i);
  assert.equal(base58encode(bytes0to31), '1thX6LZfHDZZKUs92febYZhYRcXddmzfzF2NvTkPNE');
  assert.equal(base58encode(new TextEncoder().encode('hello')), 'Cn8eVZg');
});

test('base58 preserves leading zero bytes as leading 1s', () => {
  assert.equal(base58encode(Uint8Array.from([0])), '1');
  assert.equal(base58encode(Uint8Array.from([0, 0, 1])), '112');
});

test('base58 round-trips arbitrary bytes', () => {
  for (const sample of [[0], [255], [0, 0, 7, 200], [1, 2, 3, 4, 5]]) {
    const u8 = Uint8Array.from(sample);
    assert.equal(hex(base58decode(base58encode(u8))), hex(u8));
  }
});

test('base58decode rejects characters outside the alphabet', () => {
  for (const bad of ['0', 'O', 'I', 'l']) {
    assert.throws(() => base58decode(`abc${bad}`), /invalid base58 char/);
  }
});

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
