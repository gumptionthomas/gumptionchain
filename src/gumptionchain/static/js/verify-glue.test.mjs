import { test } from 'node:test';
import assert from 'node:assert/strict';
import { nodeFetchProvenance } from './verify-glue.mjs';

const TX = 'a'.repeat(64);

test('nodeFetchProvenance returns null on 404', async () => {
  globalThis.fetch = async () => ({ status: 404, ok: false });
  assert.equal(await nodeFetchProvenance('')(TX), null);
});

test('nodeFetchProvenance throws on non-ok, non-404', async () => {
  globalThis.fetch = async () => ({ status: 500, ok: false });
  await assert.rejects(
    () => nodeFetchProvenance('')(TX),
    /provenance fetch failed: 500/,
  );
});

test('nodeFetchProvenance encodes the txid path segment', async () => {
  let captured;
  globalThis.fetch = async (url) => {
    captured = url;
    return { status: 200, ok: true, json: async () => ({}) };
  };
  await nodeFetchProvenance('http://x')('a/b?c#d');
  assert.equal(
    captured,
    'http://x/transaction/a%2Fb%3Fc%23d/provenance.json',
  );
});
