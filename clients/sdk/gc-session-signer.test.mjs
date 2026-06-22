import { test } from 'node:test';
import assert from 'node:assert/strict';
import { makeSessionSigner } from './gc-session-signer.mjs';
import { SigningKey } from './gc-signing-key.mjs';
import { verifyMessage } from './gc-message.mjs';
import { deriveSigningKey } from './gc-derive.mjs';

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

function fakeDerivePasskey({ prfFill = 9, credentialId = 'credS', userHandle = null } = {}) {
  const PRF = new Uint8Array(32).fill(prfFill);
  return {
    PRF,
    isSupported: async () => true,
    enroll: async () => ({ credentialId, prfOutput: PRF }),
    discover: async () => ({ credentialId, prfOutput: PRF, userHandle }),
  };
}

test('recognize(): derived passkey -> signed in + derived record persisted', async () => {
  const store = fakeStore(); const durableStore = fakeStore();
  const passkey = fakeDerivePasskey({ prfFill: 11, credentialId: 'credD', userHandle: 'not-an-address' });
  const D = await (await deriveSigningKey(passkey.PRF)).address();
  const s = makeSessionSigner({ store, durableStore, passkey });
  const r = await s.recognize();
  assert.deepEqual(r, { verdict: 'derived', address: D });
  assert.deepEqual(await s.status(), { signedIn: true, address: D });
  const rec = await durableStore.get();
  assert.equal(rec.version, 2);
  assert.equal(rec.kind, 'derived');
  assert.equal(rec.address, D);
  assert.equal(rec.credentialId, 'credD');
});

test('recognize(): wrap passkey (phantom guard) -> verdict wrap, NOT signed in, nothing persisted', async () => {
  const store = fakeStore(); const durableStore = fakeStore();
  const wrapAddr = await (await SigningKey.generate()).address();
  const passkey = fakeDerivePasskey({ prfFill: 17, userHandle: wrapAddr });
  const D = await (await deriveSigningKey(passkey.PRF)).address();
  assert.notEqual(wrapAddr, D);
  const s = makeSessionSigner({ store, durableStore, passkey });
  assert.deepEqual(await s.recognize(), { verdict: 'wrap', address: wrapAddr });
  assert.deepEqual(await s.status(), { signedIn: false, address: null });
  assert.equal(await durableStore.get(), null);
});

test('recognize(): no passkey -> verdict none, no throw', async () => {
  const s = makeSessionSigner({ store: fakeStore(), durableStore: fakeStore() });
  assert.deepEqual(await s.recognize(), { verdict: 'none' });
});

test('createDerived(): returns address + 24-word mnemonic, signs in, persists derived record', async () => {
  const store = fakeStore(); const durableStore = fakeStore();
  const passkey = fakeDerivePasskey({ prfFill: 5, credentialId: 'credC' });
  const s = makeSessionSigner({ store, durableStore, passkey });
  const { address, mnemonic } = await s.createDerived({ userName: 'x' });
  assert.ok(address.startsWith('gc1'));
  assert.equal(mnemonic.split(' ').length, 24);
  assert.deepEqual(await s.status(), { signedIn: true, address });
  assert.equal((await durableStore.get()).kind, 'derived');
});

test('unlock(): a wrap identity in the keyring signs in', async () => {
  const keyring = await import('./gc-keyring.mjs');
  const store = fakeStore(); const durableStore = fakeStore();
  const w = await SigningKey.generate();
  await keyring.enroll(w, { store: durableStore }, { passphrase: 'pw' });
  const s = makeSessionSigner({ store, durableStore });
  const r = await s.unlock({ passphrase: 'pw' });
  assert.equal(r.address, await w.address());
  assert.deepEqual(await s.status(), { signedIn: true, address: await w.address() });
});
