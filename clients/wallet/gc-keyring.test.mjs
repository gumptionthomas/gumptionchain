import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Wallet } from './gc-wallet.mjs';
import * as keyring from './gc-keyring.mjs';

// In-memory single-record store mirroring the gc-store-idb contract.
function fakeStore() {
  let rec = null;
  return {
    get: async () => rec,
    put: async (r) => {
      rec = r;
    },
    delete: async () => {
      rec = null;
    },
  };
}

// Fixed-PRF fake passkey: deterministic 32-byte PRF so the HKDF->AES-GCM KEK is
// reproducible across enroll/unlock without touching real WebAuthn.
function fakePasskey(fill = 7, credentialId = 'cred1') {
  const PRF = new Uint8Array(32).fill(fill);
  return {
    isSupported: async () => true,
    enroll: async () => ({ credentialId, prfOutput: PRF }),
    unlock: async () => PRF,
  };
}

// --- Task 2: passphrase enroll/unlock ---

test('enroll(passphrase) then unlock(passphrase) recovers the same wallet', async () => {
  const store = fakeStore();
  const w = await Wallet.generate();
  const addr = await w.address();
  await keyring.enroll(w, { store }, { passphrase: 'correct horse' });
  assert.equal(await keyring.hasWallet(store), true);
  const unlocked = await keyring.unlock({ store }, { passphrase: 'correct horse' });
  assert.equal(await unlocked.address(), addr);
});

test('wrong passphrase fails closed (unlock rejects)', async () => {
  const store = fakeStore();
  await keyring.enroll(await Wallet.generate(), { store }, { passphrase: 'right' });
  await assert.rejects(() => keyring.unlock({ store }, { passphrase: 'wrong' }));
});

test('the stored record is always ciphertext (no plaintext b58/DEK leaks)', async () => {
  const store = fakeStore();
  const w = await Wallet.generate();
  const b58 = await w.exportPrivateKeyB58();
  await keyring.enroll(w, { store }, { passphrase: 'pw' });
  const rec = await store.get();
  const blob = JSON.stringify(rec, (_k, v) =>
    v instanceof Uint8Array ? Buffer.from(v).toString('hex') : v,
  );
  assert.ok(!blob.includes(b58), 'plaintext b58 must not appear in the record');
  assert.equal(rec.version, 1);
  assert.equal(rec.address, await w.address());
  assert.ok(rec.wallet_ct && rec.wallet_ct.iv && rec.wallet_ct.ciphertext);
  assert.ok(rec.wraps.passphrase.salt && rec.wraps.passphrase.iterations);
});

test('unlock with no stored wallet rejects', async () => {
  const store = fakeStore();
  await assert.rejects(() => keyring.unlock({ store }, { passphrase: 'pw' }));
});

test('clear removes the stored wallet', async () => {
  const store = fakeStore();
  await keyring.enroll(await Wallet.generate(), { store }, { passphrase: 'pw' });
  assert.equal(await keyring.hasWallet(store), true);
  await keyring.clear(store);
  assert.equal(await keyring.hasWallet(store), false);
});

// --- Task 3: passkey method + add/remove ---

test('a wallet with both methods unlocks by passkey AND by passphrase', async () => {
  const store = fakeStore();
  const passkey = fakePasskey();
  const w = await Wallet.generate();
  const addr = await w.address();
  await keyring.enroll(w, { store }, { passphrase: 'pw' });
  await keyring.addPasskey({ store, passkey }, { passphrase: 'pw' });
  // passkey path
  assert.equal(await (await keyring.unlock({ store, passkey }, {})).address(), addr);
  // passphrase path
  assert.equal(
    await (await keyring.unlock({ store }, { passphrase: 'pw' })).address(),
    addr,
  );
});

test('addPasskey with a wrong passphrase fails closed (cannot wrap the DEK)', async () => {
  const store = fakeStore();
  const passkey = fakePasskey();
  await keyring.enroll(await Wallet.generate(), { store }, { passphrase: 'pw' });
  await assert.rejects(() =>
    keyring.addPasskey({ store, passkey }, { passphrase: 'nope' }),
  );
  // The record must be unchanged: no passkey wrap was merged in.
  assert.equal((await store.get()).wraps.passkey, undefined);
});

test('addPassphrase adds a second passphrase wrap via the passkey-unwrapped DEK', async () => {
  const store = fakeStore();
  const passkey = fakePasskey();
  const w = await Wallet.generate();
  const addr = await w.address();
  await keyring.enroll(w, { store }, { passphrase: 'first' });
  await keyring.addPasskey({ store, passkey }, { passphrase: 'first' });
  // Re-wrap the DEK under a new passphrase using the passkey to unwrap.
  await keyring.addPassphrase({ store, passkey }, { passphrase: 'second' });
  assert.equal(
    await (await keyring.unlock({ store }, { passphrase: 'second' })).address(),
    addr,
  );
});

test('removeMethod refuses to remove the last remaining method', async () => {
  const store = fakeStore();
  await keyring.enroll(await Wallet.generate(), { store }, { passphrase: 'pw' });
  await assert.rejects(() => keyring.removeMethod(store, 'passphrase'));
  // Still intact.
  assert.ok((await store.get()).wraps.passphrase);
});

test('after removing passphrase, passkey still unlocks the same wallet', async () => {
  const store = fakeStore();
  const passkey = fakePasskey();
  const w = await Wallet.generate();
  const addr = await w.address();
  await keyring.enroll(w, { store }, { passphrase: 'pw' });
  await keyring.addPasskey({ store, passkey }, { passphrase: 'pw' });
  await keyring.removeMethod(store, 'passphrase');
  assert.equal((await store.get()).wraps.passphrase, undefined);
  assert.equal(await (await keyring.unlock({ store, passkey }, {})).address(), addr);
});

test('removeMethod on a wallet with no such wrap rejects', async () => {
  const store = fakeStore();
  await keyring.enroll(await Wallet.generate(), { store }, { passphrase: 'pw' });
  // No passkey wrap exists; refusing (it would also leave only passphrase).
  await assert.rejects(() => keyring.removeMethod(store, 'passkey'));
});

test('unlock prefers an explicitly-supplied passphrase over the passkey', async () => {
  const store = fakeStore();
  const passkey = fakePasskey();
  const w = await Wallet.generate();
  const addr = await w.address();
  await keyring.enroll(w, { store }, { passphrase: 'pw' });
  await keyring.addPasskey({ store, passkey }, { passphrase: 'pw' });
  // Both supplied: passphrase wins. A correct passphrase succeeds.
  assert.equal(
    await (await keyring.unlock({ store, passkey }, { passphrase: 'pw' })).address(),
    addr,
  );
});
