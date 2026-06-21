import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { deriveSeed, deriveSigningKey } from './gc-derive.mjs';

const PRF = Uint8Array.from({ length: 32 }, (_, i) => i + 1);

test('PRF-only derivation is deterministic and 32 bytes', async () => {
  const a = await deriveSeed(PRF);
  const b = await deriveSeed(PRF);
  assert.equal(a.length, 32);
  assert.deepEqual([...a], [...b]);
});

test('passphrase changes the seed; same passphrase reproduces it', async () => {
  const plain = await deriveSeed(PRF);
  const p1 = await deriveSeed(PRF, { passphrase: 'hunter2' });
  const p2 = await deriveSeed(PRF, { passphrase: 'hunter2' });
  const p3 = await deriveSeed(PRF, { passphrase: 'different' });
  assert.deepEqual([...p1], [...p2]);
  assert.notDeepEqual([...plain], [...p1]);
  assert.notDeepEqual([...p1], [...p3]);
});

test('a different PRF yields a different seed', async () => {
  const other = Uint8Array.from({ length: 32 }, (_, i) => i + 2);
  assert.notDeepEqual([...(await deriveSeed(PRF))], [...(await deriveSeed(other))]);
});

test('deriveSigningKey returns a usable SigningKey at the derived address', async () => {
  const sk = await deriveSigningKey(PRF);
  const addr = await sk.address();
  assert.ok(addr.startsWith('gc1'));
  const sig = await sk.sign(new TextEncoder().encode('x'));
  assert.equal(await sk.verify(new TextEncoder().encode('x'), sig), true);
});

test('an empty PRF is rejected', async () => {
  await assert.rejects(() => deriveSeed(new Uint8Array(0)), /PRF/);
});

test('JS reproduces the committed Python derive vectors', async () => {
  const vectors = JSON.parse(
    readFileSync(
      new URL('../../tests/fixtures/derive_vectors.json', import.meta.url),
    ),
  );
  for (const v of vectors) {
    const prf = Uint8Array.from(
      v.prf_hex.match(/../g).map((h) => parseInt(h, 16)),
    );
    const seed = await deriveSeed(
      prf,
      v.passphrase ? { passphrase: v.passphrase } : {},
    );
    assert.equal(
      [...seed].map((b) => b.toString(16).padStart(2, '0')).join(''),
      v.seed_hex,
    );
  }
});
