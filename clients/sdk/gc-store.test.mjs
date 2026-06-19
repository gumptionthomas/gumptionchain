import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SigningKey } from './gc-signing-key.mjs';
import {
  enroll, unlock, hasSigningKey, clear, NoSigningKeyError, UnsupportedError,
} from './gc-store.mjs';

const PRF = new Uint8Array(32).fill(7);

function fakeStore() {
  let rec = null;
  return {
    async get() { return rec; },
    async put(r) { rec = r; },
    async delete() { rec = null; },
    _peek() { return rec; },
  };
}

function fakePasskey(prf, { supported = true } = {}) {
  return {
    async isSupported() { return supported; },
    async enroll() { return { credentialId: 'cred-1', prfOutput: prf }; },
    async unlock() { return prf; },
  };
}

test('enroll then unlock recovers a signing_key that signs identically', async () => {
  const signing_key = await SigningKey.generate();
  const store = fakeStore();
  const passkey = fakePasskey(PRF);

  const address = await enroll(signing_key, { passkey, store }, { userName: 'p' });
  assert.equal(address, await signing_key.address());
  assert.ok(await hasSigningKey(store));

  const recovered = await unlock({ passkey, store });
  assert.equal(await recovered.address(), await signing_key.address());
  const msg = new TextEncoder().encode('prove-it');
  assert.equal(await recovered.sign(msg), await signing_key.sign(msg));
});

test('stored record holds only ciphertext + non-secrets', async () => {
  const signing_key = await SigningKey.generate();
  const store = fakeStore();
  await enroll(signing_key, { passkey: fakePasskey(PRF), store }, { userName: 'p' });
  const rec = store._peek();
  assert.equal(rec.version, 1);
  assert.equal(rec.address, await signing_key.address());
  assert.ok(rec.credentialId && rec.iv && rec.ciphertext);
  const blob = JSON.stringify(rec);
  assert.ok(!blob.includes(await signing_key.exportPrivateKeyB58()));
});

test('unlock with no stored signing_key throws NoSigningKeyError', async () => {
  await assert.rejects(
    () => unlock({ passkey: fakePasskey(PRF), store: fakeStore() }),
    NoSigningKeyError,
  );
});

test('unlock with a wrong PRF output fails to decrypt', async () => {
  const signing_key = await SigningKey.generate();
  const store = fakeStore();
  await enroll(signing_key, { passkey: fakePasskey(PRF), store }, { userName: 'p' });
  const wrong = fakePasskey(new Uint8Array(32).fill(9));
  await assert.rejects(() => unlock({ passkey: wrong, store }));
});

test('enroll throws UnsupportedError when PRF is unavailable', async () => {
  await assert.rejects(
    async () => enroll(await SigningKey.generate(),
      { passkey: fakePasskey(PRF, { supported: false }), store: fakeStore() },
      { userName: 'p' }),
    UnsupportedError,
  );
});

test('clear removes the stored signing_key', async () => {
  const store = fakeStore();
  await enroll(await SigningKey.generate(), { passkey: fakePasskey(PRF), store }, { userName: 'p' });
  await clear(store);
  assert.equal(await hasSigningKey(store), false);
});

test('clear on an empty store is an idempotent no-op', async () => {
  const store = fakeStore();
  await clear(store); // must not throw
  assert.equal(await hasSigningKey(store), false);
});

test('unlock rejects a record with an unknown version (fail fast)', async () => {
  const store = fakeStore();
  await enroll(await SigningKey.generate(), { passkey: fakePasskey(PRF), store }, { userName: 'p' });
  store._peek().version = 999;
  await assert.rejects(() => unlock({ passkey: fakePasskey(PRF), store }), /version/);
});
