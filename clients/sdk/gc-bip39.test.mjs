import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { test } from 'node:test';
import { WORDLIST } from './gc-bip39-wordlist.mjs';
import { seedToMnemonic, mnemonicToSeed } from './gc-bip39.mjs';

test('wordlist is the official 2048-word BIP-39 English list', async () => {
  assert.equal(WORDLIST.length, 2048);
  assert.equal(WORDLIST[0], 'abandon');
  assert.equal(WORDLIST[2047], 'zoo');
  const joined = new TextEncoder().encode(WORDLIST.join('\n') + '\n');
  const h = new Uint8Array(await crypto.subtle.digest('SHA-256', joined));
  const hex = [...h].map((b) => b.toString(16).padStart(2, '0')).join('');
  assert.equal(
    hex,
    '2f5eed53a4727b4bf8880d8f3f199efc90e58503646d9ff8eff3a2ed3b24dbda',
  );
});

test('round-trips a 32-byte seed through 24 words', async () => {
  const seed = Uint8Array.from({ length: 32 }, (_, i) => i);
  const m = await seedToMnemonic(seed);
  assert.equal(m.split(' ').length, 24);
  assert.deepEqual([...(await mnemonicToSeed(m))], [...seed]);
});

test('official BIP-39 vector: all-zero entropy', async () => {
  const m = await seedToMnemonic(new Uint8Array(32));
  assert.equal(
    m,
    'abandon abandon abandon abandon abandon abandon abandon abandon ' +
      'abandon abandon abandon abandon abandon abandon abandon abandon ' +
      'abandon abandon abandon abandon abandon abandon abandon art',
  );
  assert.deepEqual([...(await mnemonicToSeed(m))], [...new Uint8Array(32)]);
});

test('a corrupted checksum is rejected', async () => {
  const seed = Uint8Array.from({ length: 32 }, (_, i) => i + 1);
  const words = (await seedToMnemonic(seed)).split(' ');
  words[23] = words[23] === 'zoo' ? 'zone' : 'zoo';
  await assert.rejects(() => mnemonicToSeed(words.join(' ')), /checksum/);
});

test('a bad word / wrong length is rejected', async () => {
  await assert.rejects(() => mnemonicToSeed('not a real phrase'), /24-word/);
  const seed = new Uint8Array(32);
  const words = (await seedToMnemonic(seed)).split(' ');
  words[0] = 'notaword';
  await assert.rejects(() => mnemonicToSeed(words.join(' ')), /invalid/);
});

test('seedToMnemonic requires exactly 32 bytes', async () => {
  await assert.rejects(() => seedToMnemonic(new Uint8Array(31)), /32/);
});

test('JS reproduces the committed Python BIP-39 vectors', async () => {
  const vectors = JSON.parse(
    readFileSync(
      new URL('../../tests/fixtures/bip39_vectors.json', import.meta.url),
    ),
  );
  for (const v of vectors) {
    const seed = Uint8Array.from(
      v.seed_hex.match(/../g).map((h) => parseInt(h, 16)),
    );
    assert.equal(await seedToMnemonic(seed), v.mnemonic);
    assert.deepEqual([...(await mnemonicToSeed(v.mnemonic))], [...seed]);
  }
});
