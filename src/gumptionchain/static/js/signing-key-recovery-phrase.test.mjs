import { test } from 'node:test';
import assert from 'node:assert/strict';
import { chunkPhrase } from './signing-key-recovery-phrase.mjs';

test('chunkPhrase splits 24 words into rows of 4 with 1-based numbers', () => {
  const words = Array.from({ length: 24 }, (_, i) => `w${i + 1}`);
  const rows = chunkPhrase(words.join(' '));
  assert.equal(rows.length, 6);
  assert.equal(rows[0].length, 4);
  assert.deepEqual(rows[0][0], { n: 1, word: 'w1' });
  assert.deepEqual(rows[5][3], { n: 24, word: 'w24' });
});

test('chunkPhrase tolerates extra whitespace and empty input', () => {
  assert.deepEqual(chunkPhrase('  a   b '), [[{ n: 1, word: 'a' }, { n: 2, word: 'b' }]]);
  assert.deepEqual(chunkPhrase(''), []);
  assert.deepEqual(chunkPhrase(null), []);
});
