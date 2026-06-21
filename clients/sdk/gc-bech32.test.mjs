import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  decodeAddress, decodeSecret, encodeAddress, encodeSecret,
} from './gc-bech32.mjs';

const seed = Uint8Array.from({ length: 32 }, (_, i) => i);

test('address round-trips and starts with gc1', () => {
  const a = encodeAddress(seed);
  assert.ok(a.startsWith('gc1'));
  assert.deepEqual([...decodeAddress(a)], [...seed]);
});

test('secret round-trips and starts with gcsec1', () => {
  const s = encodeSecret(seed);
  assert.ok(s.startsWith('gcsec1'));
  assert.deepEqual([...decodeSecret(s)], [...seed]);
});

test('HRPs do not cross-decode', () => {
  assert.equal(decodeSecret(encodeAddress(seed)), null);
  assert.equal(decodeAddress(encodeSecret(seed)), null);
});

test('a single-char mutation is rejected', () => {
  const a = encodeAddress(seed);
  const i = a.length - 3;
  const bad = a.slice(0, i) + (a[i] === 'q' ? 'p' : 'q') + a.slice(i + 1);
  const out = decodeAddress(bad);
  assert.ok(out === null || [...out].join() !== [...seed].join());
});

test('non-32-byte input throws', () => {
  assert.throws(() => encodeAddress(seed.slice(0, 31)), /32/);
});
