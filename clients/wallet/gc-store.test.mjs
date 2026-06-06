import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Wallet } from './gc-wallet.mjs';
import {
  enroll, unlock, hasWallet, clear, NoWalletError, UnsupportedError,
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

test('enroll then unlock recovers a wallet that signs identically', async () => {
  const wallet = await Wallet.generate();
  const store = fakeStore();
  const passkey = fakePasskey(PRF);

  const address = await enroll(wallet, { passkey, store }, { userName: 'p' });
  assert.equal(address, await wallet.address());
  assert.ok(await hasWallet(store));

  const recovered = await unlock({ passkey, store });
  assert.equal(await recovered.address(), await wallet.address());
  const msg = new TextEncoder().encode('prove-it');
  assert.equal(await recovered.sign(msg), await wallet.sign(msg));
});

test('stored record holds only ciphertext + non-secrets', async () => {
  const wallet = await Wallet.generate();
  const store = fakeStore();
  await enroll(wallet, { passkey: fakePasskey(PRF), store }, { userName: 'p' });
  const rec = store._peek();
  assert.equal(rec.version, 1);
  assert.equal(rec.address, await wallet.address());
  assert.ok(rec.credentialId && rec.iv && rec.ciphertext);
  const blob = JSON.stringify(rec);
  assert.ok(!blob.includes(await wallet.exportPrivateKeyB58()));
});

test('unlock with no stored wallet throws NoWalletError', async () => {
  await assert.rejects(
    () => unlock({ passkey: fakePasskey(PRF), store: fakeStore() }),
    NoWalletError,
  );
});

test('unlock with a wrong PRF output fails to decrypt', async () => {
  const wallet = await Wallet.generate();
  const store = fakeStore();
  await enroll(wallet, { passkey: fakePasskey(PRF), store }, { userName: 'p' });
  const wrong = fakePasskey(new Uint8Array(32).fill(9));
  await assert.rejects(() => unlock({ passkey: wrong, store }));
});

test('enroll throws UnsupportedError when PRF is unavailable', async () => {
  await assert.rejects(
    async () => enroll(await Wallet.generate(),
      { passkey: fakePasskey(PRF, { supported: false }), store: fakeStore() },
      { userName: 'p' }),
    UnsupportedError,
  );
});

test('clear removes the stored wallet', async () => {
  const store = fakeStore();
  await enroll(await Wallet.generate(), { passkey: fakePasskey(PRF), store }, { userName: 'p' });
  await clear(store);
  assert.equal(await hasWallet(store), false);
});

test('clear on an empty store is an idempotent no-op', async () => {
  const store = fakeStore();
  await clear(store); // must not throw
  assert.equal(await hasWallet(store), false);
});

test('unlock rejects a record with an unknown version (fail fast)', async () => {
  const store = fakeStore();
  await enroll(await Wallet.generate(), { passkey: fakePasskey(PRF), store }, { userName: 'p' });
  store._peek().version = 999;
  await assert.rejects(() => unlock({ passkey: fakePasskey(PRF), store }), /version/);
});
