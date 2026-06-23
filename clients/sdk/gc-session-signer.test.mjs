import { test } from 'node:test';
import assert from 'node:assert/strict';
import { makeSessionSigner, DEFAULT_IDLE_MS } from './gc-session-signer.mjs';
import { SigningKey } from './gc-signing-key.mjs';
import { verifyMessage } from './gc-message.mjs';
import { deriveSigningKey } from './gc-derive.mjs';

function fakeStore() {
  let rec = null;
  return { get: async () => rec, put: async (r) => { rec = r; }, delete: async () => { rec = null; } };
}

// A connected fake BroadcastChannel pair: postMessage on A delivers to B.onmessage
// (as a MessageEvent-shaped { data }) and vice versa.
function fakeChannelPair() {
  const a = { onmessage: null, postMessage: (m) => b.onmessage && b.onmessage({ data: m }) };
  const b = { onmessage: null, postMessage: (m) => a.onmessage && a.onmessage({ data: m }) };
  return [a, b];
}

// A fake injectable clock that captures the most recent armed timer callback so
// tests can fire it deterministically and count set/clear invocations.
function fakeClock() {
  let cb = null;
  const calls = { set: 0, clear: 0 };
  return {
    now: () => 0,
    setTimer: (fn) => { cb = fn; calls.set++; return 1; },
    clearTimer: () => { calls.clear++; },
    fire: () => cb && cb(),
    calls,
  };
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

test('idle timeout fires lock: signed out + store cleared', async () => {
  const store = fakeStore();
  const clock = fakeClock();
  const s = makeSessionSigner({ store, clock });
  let locked = false;
  s.onLock(() => { locked = true; });
  const key = await SigningKey.generate();
  await s.adopt(key);
  // Auto-lock is off until installAutoLock; it arms with the injected fake clock
  // (so no real setTimeout leaks and keeps the process alive). adopt() before
  // this is a no-op for the timer.
  s.installAutoLock({});
  assert.equal(clock.calls.set > 0, true);
  assert.deepEqual(await s.status(), { signedIn: true, address: await key.address() });
  clock.fire(); // simulate the idle deadline elapsing
  assert.equal(locked, true);
  assert.equal(await store.get(), null);
  assert.deepEqual(await s.status(), { signedIn: false, address: null });
});

test('installAutoLock: visibilitychange -> hidden locks', async () => {
  const store = fakeStore();
  const clock = fakeClock();
  const s = makeSessionSigner({ store, clock });
  await s.adopt(await SigningKey.generate());
  let locked = false;
  s.onLock(() => { locked = true; });

  const docListeners = {};
  const winListeners = {};
  const document = {
    visibilityState: 'visible',
    addEventListener: (type, fn) => { docListeners[type] = fn; },
  };
  const window = {
    addEventListener: (type, fn) => { winListeners[type] = fn; },
  };
  s.installAutoLock({ document, window });
  document.visibilityState = 'hidden';
  docListeners.visibilitychange();
  assert.equal(locked, true);
  assert.equal(await store.get(), null);
});

test('installAutoLock: pagehide locks', async () => {
  const store = fakeStore();
  const clock = fakeClock();
  const s = makeSessionSigner({ store, clock });
  await s.adopt(await SigningKey.generate());
  let locked = false;
  s.onLock(() => { locked = true; });

  const docListeners = {};
  const winListeners = {};
  const document = { visibilityState: 'visible', addEventListener: (type, fn) => { docListeners[type] = fn; } };
  const window = { addEventListener: (type, fn) => { winListeners[type] = fn; } };
  s.installAutoLock({ document, window });
  winListeners.pagehide();
  assert.equal(locked, true);
  assert.equal(await store.get(), null);
});

test('cross-tab: lock in tab A clears tab B in-memory cache + fires B onLock', async () => {
  const store = fakeStore();
  const [chA, chB] = fakeChannelPair();
  const A = makeSessionSigner({ store, broadcast: chA });
  const B = makeSessionSigner({ store, broadcast: chB });
  await A.adopt(await SigningKey.generate());
  // adopt on B first to give B a live in-memory cache (so we can assert it clears)
  await B.status(); // hydrate B's cache from the shared store
  assert.equal((await B.status()).signedIn, true);
  let bLocked = false;
  B.onLock(() => { bLocked = true; });
  await A.lock();
  assert.equal(bLocked, true); // B fired onLock from the broadcast
  assert.deepEqual(await B.status(), { signedIn: false, address: null });
});

test('touch re-arms the idle timer (clear + set)', async () => {
  const store = fakeStore();
  const clock = fakeClock();
  const s = makeSessionSigner({ store, clock });
  await s.adopt(await SigningKey.generate());
  s.installAutoLock({}); // arm auto-lock (touch is a no-op until then)
  const setAfterArm = clock.calls.set;
  const clearAfterArm = clock.calls.clear;
  s.touch();
  assert.equal(clock.calls.clear > clearAfterArm, true);
  assert.equal(clock.calls.set > setAfterArm, true);
});

test('DEFAULT_IDLE_MS is 15 minutes', () => {
  assert.equal(DEFAULT_IDLE_MS, 15 * 60 * 1000);
});
