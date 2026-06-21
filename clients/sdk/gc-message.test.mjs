import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SigningKey } from './gc-signing-key.mjs';
import {
  signMessage, verifyMessage, BadProofError,
} from './gc-message.mjs';

const TS = '1700001000';

test('signMessage -> verifyMessage round-trips valid', async () => {
  const w = await SigningKey.generate();
  const proof = await signMessage(w, 'I made stake T1', { timestamp: TS });
  assert.equal(proof.scheme, 'gc-msg-v1');
  assert.equal(proof.version, '1');
  assert.equal(proof.address, await w.address());
  assert.equal(proof.timestamp, TS);
  const r = await verifyMessage(proof);
  assert.deepEqual(r, {
    valid: true, address: await w.address(), timestamp: TS, message: 'I made stake T1',
  });
});

test('a tampered message yields bad-signature', async () => {
  const w = await SigningKey.generate();
  const proof = await signMessage(w, 'original', { timestamp: TS });
  proof.message = 'forged';
  const r = await verifyMessage(proof);
  assert.equal(r.valid, false);
  assert.equal(r.reason, 'bad-signature');
});

test('an address not matching the public key yields address-mismatch', async () => {
  const w = await SigningKey.generate();
  const proof = await signMessage(w, 'hi', { timestamp: TS });
  proof.address = `${proof.address}X`;
  const r = await verifyMessage(proof);
  assert.equal(r.valid, false);
  assert.equal(r.reason, 'address-mismatch');
});

test('wrong scheme/version/missing fields throw BadProofError', async () => {
  const w = await SigningKey.generate();
  const good = await signMessage(w, 'hi', { timestamp: TS });
  await assert.rejects(() => verifyMessage({ ...good, scheme: 'gc-sig-v1' }), BadProofError);
  await assert.rejects(() => verifyMessage({ ...good, version: '2' }), BadProofError);
  await assert.rejects(() => verifyMessage({ scheme: 'gc-msg-v1', version: '1' }), BadProofError);
  await assert.rejects(() => verifyMessage(null), BadProofError);
});

test('maxAge enforces freshness when supplied', async () => {
  const w = await SigningKey.generate();
  const proof = await signMessage(w, 'hi', { timestamp: TS });
  const now = Number(TS) + 1000;
  const stale = await verifyMessage(proof, { maxAge: 300, now });
  assert.equal(stale.valid, false);
  assert.equal(stale.reason, 'expired');
  const fresh = await verifyMessage(proof, { maxAge: 5000, now });
  assert.equal(fresh.valid, true);
});

test('maxAge rejects a far-future timestamp (symmetric window)', async () => {
  const w = await SigningKey.generate();
  const future = String(Number(TS) + 10000);
  const proof = await signMessage(w, 'hi', { timestamp: future });
  const r = await verifyMessage(proof, { maxAge: 300, now: Number(TS) });
  assert.equal(r.valid, false);
  assert.equal(r.reason, 'expired');
});

test('a non-base64 signature yields bad-signature, not an exception', async () => {
  const w = await SigningKey.generate();
  const proof = await signMessage(w, 'hi', { timestamp: TS });
  proof.signature = '!!! not base64 !!!';
  const r = await verifyMessage(proof);
  assert.equal(r.valid, false);
  assert.equal(r.reason, 'bad-signature');
});

test('a non-numeric timestamp is rejected as a malformed proof', async () => {
  const w = await SigningKey.generate();
  const proof = await signMessage(w, 'hi', { timestamp: TS });
  proof.timestamp = 'notanumber';
  await assert.rejects(() => verifyMessage(proof), BadProofError);
  await assert.rejects(
    () => verifyMessage(proof, { maxAge: 300, now: 9999999999 }),
    BadProofError,
  );
});

import { toArmored, fromArmored } from './gc-message.mjs';

test('toArmored/fromArmored round-trip preserves the proof', async () => {
  const w = await SigningKey.generate();
  const proof = await signMessage(w, 'multi\nline\nmessage', { timestamp: TS });
  const armored = toArmored(proof);
  assert.ok(armored.startsWith('-----BEGIN GUMPTION SIGNED MESSAGE-----'));
  const back = fromArmored(armored);
  assert.deepEqual(back, proof);
  assert.equal((await verifyMessage(back)).valid, true);
});

test('fromArmored rejects malformed armor and cleartext mismatch', async () => {
  const w = await SigningKey.generate();
  const proof = await signMessage(w, 'hello', { timestamp: TS });
  assert.throws(() => fromArmored('not armored at all'), BadProofError);
  const tampered = toArmored(proof).replace('hello', 'goodbye');
  assert.throws(() => fromArmored(tampered), BadProofError);
});

import { readFileSync } from 'node:fs';
import { SigningKey as W2 } from './gc-signing-key.mjs';

test('JS signatures match the committed golden vectors', async () => {
  const VEC = JSON.parse(readFileSync(
    new URL('./testdata/gc-msg-vectors.json', import.meta.url),
  ));
  // The fixed Ed25519 key the Python generator uses (seed 1..32), as a
  // gcsec1… secret. Importing it in JS must reproduce byte-identical
  // signatures and the same gc1… address.
  const SECRET =
    'gcsec1qypqxpq9qcrsszg2pvxq6rs0zqg3yyc5z5tpwxqergd3c8g7rusquhpjl5';
  const w = await W2.fromSecret(SECRET);
  for (const c of VEC) {
    const proof = await signMessage(w, c.message, { timestamp: c.timestamp });
    assert.equal(proof.signature, c.signature);
    assert.equal(proof.address, c.address);
  }
});
