import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { SigningKey } from './gc-signing-key.mjs';
import { base64encode } from './gc-crypto.mjs';
import {
  dataCsv, txid, signingData, signUnsignedTxn,
} from './gc-transaction.mjs';

// Python-generated parity vectors (tests/fixtures/gen_txn_fixtures.py).
// JS must reconstruct data_csv / txid / signing_data byte-for-byte.
const VECTORS = JSON.parse(
  readFileSync(
    fileURLToPath(
      new URL('../../tests/fixtures/txn_signing_vectors.json', import.meta.url),
    ),
    'utf8',
  ),
);

assert.ok(VECTORS.length > 0, 'expected at least one parity vector');

for (const v of VECTORS) {
  test(`data_csv parity: ${v.name}`, () => {
    assert.equal(dataCsv(v.txn), v.data_csv);
  });

  test(`txid parity: ${v.name}`, async () => {
    assert.equal(await txid(v.txn), v.txid);
  });

  test(`signing_data parity: ${v.name}`, () => {
    assert.equal(base64encode(signingData(v.txn)), v.signing_data_b64);
  });

  test(`sign-then-fields: ${v.name}`, async () => {
    const signing_key = await SigningKey.fromSecret(v.secret);
    // The fixture txn is sealed but unsigned; strip any signature first.
    const unsigned = { ...v.txn, signature: undefined };
    const signed = await signUnsignedTxn(unsigned, signing_key);
    // Ed25519 (RFC 8032) is deterministic, but we don't assert signature
    // equality across implementations — just that the expected fields are
    // populated and the txid still re-derives.
    assert.ok(signed.signature, 'signature populated');
    assert.equal(signed.address, await signing_key.address());
    assert.equal(await txid(signed), v.txid);
    assert.ok(
      await signing_key.verify(signingData(signed), signed.signature),
      'signature verifies against the signing data',
    );
  });
}

test('signUnsignedTxn rejects a txid that does not match its fields', async () => {
  const v = VECTORS[0];
  const signing_key = await SigningKey.fromSecret(v.secret);
  const tampered = { ...v.txn, txid: 'f'.repeat(64) };
  await assert.rejects(
    () => signUnsignedTxn(tampered, signing_key),
    /txid mismatch/,
  );
});
