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

// --- passphrase enroll/unlock ---

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
