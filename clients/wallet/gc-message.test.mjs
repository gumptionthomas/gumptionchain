import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Wallet } from './gc-wallet.mjs';
import {
  signMessage, verifyMessage, BadProofError,
} from './gc-message.mjs';

const TS = '1700001000';

test('signMessage -> verifyMessage round-trips valid', async () => {
  const w = await Wallet.generate();
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
  const w = await Wallet.generate();
  const proof = await signMessage(w, 'original', { timestamp: TS });
  proof.message = 'forged';
  const r = await verifyMessage(proof);
  assert.equal(r.valid, false);
  assert.equal(r.reason, 'bad-signature');
});

test('an address not matching the public key yields address-mismatch', async () => {
  const w = await Wallet.generate();
  const proof = await signMessage(w, 'hi', { timestamp: TS });
  proof.address = `${proof.address}X`;
  const r = await verifyMessage(proof);
  assert.equal(r.valid, false);
  assert.equal(r.reason, 'address-mismatch');
});

test('wrong scheme/version/missing fields throw BadProofError', async () => {
  const w = await Wallet.generate();
  const good = await signMessage(w, 'hi', { timestamp: TS });
  await assert.rejects(() => verifyMessage({ ...good, scheme: 'gc-sig-v1' }), BadProofError);
  await assert.rejects(() => verifyMessage({ ...good, version: '2' }), BadProofError);
  await assert.rejects(() => verifyMessage({ scheme: 'gc-msg-v1', version: '1' }), BadProofError);
  await assert.rejects(() => verifyMessage(null), BadProofError);
});

test('maxAge enforces freshness when supplied', async () => {
  const w = await Wallet.generate();
  const proof = await signMessage(w, 'hi', { timestamp: TS });
  const now = Number(TS) + 1000;
  const stale = await verifyMessage(proof, { maxAge: 300, now });
  assert.equal(stale.valid, false);
  assert.equal(stale.reason, 'expired');
  const fresh = await verifyMessage(proof, { maxAge: 5000, now });
  assert.equal(fresh.valid, true);
});

import { toArmored, fromArmored } from './gc-message.mjs';

test('toArmored/fromArmored round-trip preserves the proof', async () => {
  const w = await Wallet.generate();
  const proof = await signMessage(w, 'multi\nline\nmessage', { timestamp: TS });
  const armored = toArmored(proof);
  assert.ok(armored.startsWith('-----BEGIN GUMPTION SIGNED MESSAGE-----'));
  const back = fromArmored(armored);
  assert.deepEqual(back, proof);
  assert.equal((await verifyMessage(back)).valid, true);
});

test('fromArmored rejects malformed armor and cleartext mismatch', async () => {
  const w = await Wallet.generate();
  const proof = await signMessage(w, 'hello', { timestamp: TS });
  assert.throws(() => fromArmored('not armored at all'), BadProofError);
  const tampered = toArmored(proof).replace('hello', 'goodbye');
  assert.throws(() => fromArmored(tampered), BadProofError);
});
