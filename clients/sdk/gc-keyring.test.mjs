import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SigningKey } from './gc-signing-key.mjs';
import * as keyring from './gc-keyring.mjs';
import { BadPassphraseError } from './gc-errors.mjs';

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

test('enroll(passphrase) then unlock(passphrase) recovers the same signing_key', async () => {
  const store = fakeStore();
  const w = await SigningKey.generate();
  const addr = await w.address();
  await keyring.enroll(w, { store }, { passphrase: 'correct horse' });
  assert.equal(await keyring.hasSigningKey(store), true);
  const unlocked = await keyring.unlock({ store }, { passphrase: 'correct horse' });
  assert.equal(await unlocked.address(), addr);
});

test('wrong passphrase fails closed with a typed BadPassphraseError', async () => {
  const store = fakeStore();
  await keyring.enroll(await SigningKey.generate(), { store }, { passphrase: 'right' });
  // Parity with gc-backup.importEncrypted: a wrong passphrase must surface as
  // BadPassphraseError, not a raw WebCrypto OperationError (#279).
  await assert.rejects(
    () => keyring.unlock({ store }, { passphrase: 'wrong' }),
    BadPassphraseError,
  );
});

test('the stored record is always ciphertext (no plaintext secret/DEK leaks)', async () => {
  const store = fakeStore();
  const w = await SigningKey.generate();
  const secret = await w.exportSecret();
  await keyring.enroll(w, { store }, { passphrase: 'pw' });
  const rec = await store.get();
  const blob = JSON.stringify(rec, (_k, v) =>
    v instanceof Uint8Array ? Buffer.from(v).toString('hex') : v,
  );
  assert.ok(!blob.includes(secret), 'plaintext secret must not appear in the record');
  assert.equal(rec.version, 2);
  assert.equal(rec.address, await w.address());
  assert.ok(rec.signing_key_ct && rec.signing_key_ct.iv && rec.signing_key_ct.ciphertext);
  assert.ok(rec.wraps.passphrase.salt && rec.wraps.passphrase.iterations);
});

test('unlock with no stored signing_key rejects', async () => {
  const store = fakeStore();
  await assert.rejects(() => keyring.unlock({ store }, { passphrase: 'pw' }));
});

test('clear removes the stored signing_key', async () => {
  const store = fakeStore();
  await keyring.enroll(await SigningKey.generate(), { store }, { passphrase: 'pw' });
  assert.equal(await keyring.hasSigningKey(store), true);
  await keyring.clear(store);
  assert.equal(await keyring.hasSigningKey(store), false);
});

// --- Task 3: passkey method + add/remove ---

test('a signing_key with both methods unlocks by passkey AND by passphrase', async () => {
  const store = fakeStore();
  const passkey = fakePasskey();
  const w = await SigningKey.generate();
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
  await keyring.enroll(await SigningKey.generate(), { store }, { passphrase: 'pw' });
  await assert.rejects(() =>
    keyring.addPasskey({ store, passkey }, { passphrase: 'nope' }),
  );
  // The record must be unchanged: no passkey wrap was merged in.
  assert.equal((await store.get()).wraps.passkey, undefined);
});

test('addPassphrase adds a second passphrase wrap via the passkey-unwrapped DEK', async () => {
  const store = fakeStore();
  const passkey = fakePasskey();
  const w = await SigningKey.generate();
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
  await keyring.enroll(await SigningKey.generate(), { store }, { passphrase: 'pw' });
  await assert.rejects(() => keyring.removeMethod(store, 'passphrase'));
  // Still intact.
  assert.ok((await store.get()).wraps.passphrase);
});

test('after removing passphrase, passkey still unlocks the same signing_key', async () => {
  const store = fakeStore();
  const passkey = fakePasskey();
  const w = await SigningKey.generate();
  const addr = await w.address();
  await keyring.enroll(w, { store }, { passphrase: 'pw' });
  await keyring.addPasskey({ store, passkey }, { passphrase: 'pw' });
  await keyring.removeMethod(store, 'passphrase');
  assert.equal((await store.get()).wraps.passphrase, undefined);
  assert.equal(await (await keyring.unlock({ store, passkey }, {})).address(), addr);
});

test('removeMethod on a signing_key with no such wrap rejects', async () => {
  const store = fakeStore();
  await keyring.enroll(await SigningKey.generate(), { store }, { passphrase: 'pw' });
  // No passkey wrap exists; refusing (it would also leave only passphrase).
  await assert.rejects(() => keyring.removeMethod(store, 'passkey'));
});

test('unlock prefers an explicitly-supplied passphrase over the passkey', async () => {
  const store = fakeStore();
  const passkey = fakePasskey();
  const w = await SigningKey.generate();
  const addr = await w.address();
  await keyring.enroll(w, { store }, { passphrase: 'pw' });
  await keyring.addPasskey({ store, passkey }, { passphrase: 'pw' });
  // Both supplied: passphrase wins. A correct passphrase succeeds.
  assert.equal(
    await (await keyring.unlock({ store, passkey }, { passphrase: 'pw' })).address(),
    addr,
  );
});
