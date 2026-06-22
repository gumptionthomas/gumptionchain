import { test } from 'node:test';
import assert from 'node:assert/strict';
import { makeSessionSigner } from './gc-session-signer.mjs';
import { SigningKey } from './gc-signing-key.mjs';
import { verifyMessage } from './gc-message.mjs';

function fakeStore() {
  let rec = null;
  return { get: async () => rec, put: async (r) => { rec = r; }, delete: async () => { rec = null; } };
}

test('adopt persists a sign-only handle; a FRESH signer on the same store is signed in', async () => {
  const store = fakeStore();
  const key = await SigningKey.generate();
  const addr = await key.address();
  const s1 = makeSessionSigner({ store });
  await s1.adopt(key);
  assert.deepEqual(await s1.status(), { signedIn: true, address: addr });
  const s2 = makeSessionSigner({ store }); // a new document hydrates from the same store
  assert.deepEqual(await s2.status(), { signedIn: true, address: addr });
  const proof = await s2.signLogin('login:abc');
  const v = await verifyMessage(proof, { maxAge: Number.MAX_SAFE_INTEGER });
  assert.equal(v.valid, true);
  assert.equal((await store.get()).privateKey.extractable, false); // persisted handle non-extractable
});

test('lock clears the store + memory; a fresh signer is signed out', async () => {
  const store = fakeStore();
  const s1 = makeSessionSigner({ store });
  await s1.adopt(await SigningKey.generate());
  let locked = false;
  s1.onLock(() => { locked = true; });
  await s1.lock();
  assert.equal(locked, true);
  assert.equal(await store.get(), null);
  assert.deepEqual(await s1.status(), { signedIn: false, address: null });
  assert.deepEqual(await makeSessionSigner({ store }).status(), { signedIn: false, address: null });
});

test('signLogin/signTransaction reject when signed out', async () => {
  const { NoSigningKeyError } = await import('./gc-errors.mjs');
  const s = makeSessionSigner({ store: fakeStore() });
  await assert.rejects(() => s.signLogin('x'), NoSigningKeyError);
  await assert.rejects(() => s.signTransaction({ txid: 'x' }), NoSigningKeyError);
});
