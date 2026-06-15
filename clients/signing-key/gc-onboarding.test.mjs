import { test } from 'node:test';
import assert from 'node:assert/strict';

import { makeOnboarding, NoSigningKeyError } from './gc-onboarding.mjs';
import { verifyMessage } from './gc-message.mjs';

function fakeStore() {
  let rec = null;
  return {
    get: async () => rec,
    put: async (r) => { rec = r; },
    delete: async () => { rec = null; },
  };
}

function fakePasskey(fill = 7, credentialId = 'cred1') {
  const PRF = new Uint8Array(32).fill(fill);
  return {
    isSupported: async () => true,
    enroll: async () => ({ credentialId, prfOutput: PRF }),
    unlock: async () => PRF,
  };
}

const SECURE = { isSecureContext: true };

test('empty store: status reports no key, passkey off without an adapter', async () => {
  const onb = makeOnboarding({ store: fakeStore(), window: SECURE });
  const s = await onb.status();
  assert.equal(s.hasKey, false);
  assert.equal(s.unlocked, false);
  assert.equal(s.address, null);
  assert.equal(s.passkeySupported, false);
  assert.equal(s.secureContext, true);
});

test('create persists + holds unlocked, and onChange fires with fresh status', async () => {
  const onb = makeOnboarding({ store: fakeStore(), window: SECURE });
  let last = null;
  const off = onb.onChange((s) => { last = s; });
  const { address } = await onb.create({ passphrase: 'pw' });
  assert.match(address, /^GC.*GC$/);
  const s = await onb.status();
  assert.equal(s.hasKey, true);
  assert.equal(s.unlocked, true);
  assert.equal(s.address, address);
  assert.equal(last.unlocked, true);
  off();
});

test('lock drops the in-memory key, keeps the record; address still readable', async () => {
  const onb = makeOnboarding({ store: fakeStore(), window: SECURE });
  const { address } = await onb.create({ passphrase: 'pw' });
  await onb.lock();
  const s = await onb.status();
  assert.equal(s.unlocked, false);
  assert.equal(s.hasKey, true);
  assert.equal(s.address, address);
});

test('unlock by passphrase re-holds the key; wrong passphrase rejects', async () => {
  const onb = makeOnboarding({ store: fakeStore(), window: SECURE });
  const { address } = await onb.create({ passphrase: 'pw' });
  await onb.lock();
  const r = await onb.unlock({ passphrase: 'pw' });
  assert.equal(r.address, address);
  assert.equal((await onb.status()).unlocked, true);
  await onb.lock();
  await assert.rejects(() => onb.unlock({ passphrase: 'WRONG' }));
});

test('passkey: create with passkey, unlock by passkey', async () => {
  const onb = makeOnboarding({ store: fakeStore(), window: SECURE, passkey: fakePasskey() });
  assert.equal((await onb.status()).passkeySupported, true);
  const { address } = await onb.create({ passphrase: 'pw', withPasskey: true });
  await onb.lock();
  const r = await onb.unlock({ passkey: true });
  assert.equal(r.address, address);
});

test('create with withPasskey silently skips the passkey when unsupported (no throw)', async () => {
  // An adapter that reports unsupported must NOT be enrolled — create gates on
  // the live support state, not merely the adapter's presence.
  const passkey = {
    isSupported: async () => false,
    enroll: async () => { throw new Error('enroll must not run when unsupported'); },
    unlock: async () => { throw new Error('unlock must not run when unsupported'); },
  };
  const onb = makeOnboarding({ store: fakeStore(), window: SECURE, passkey });
  assert.equal((await onb.status()).passkeySupported, false);
  const { address } = await onb.create({ passphrase: 'pw', withPasskey: true });
  assert.match(address, /^GC.*GC$/);
  assert.equal((await onb.status()).unlocked, true);
});

test('backup yields an encrypted artifact (no raw key) + filename; restore into a fresh store recovers the same address', async () => {
  const onb1 = makeOnboarding({ store: fakeStore(), window: SECURE });
  const { address } = await onb1.create({ passphrase: 'pw' });
  const { artifact, filename } = await onb1.backup({ passphrase: 'pw' });
  assert.equal(artifact.kind, 'gc-signing-key-backup');
  assert.match(filename, /^gc-signing-key-backup-.*\.json$/);
  assert.deepEqual(
    Object.keys(artifact).sort(),
    ['address', 'ciphertext', 'iv', 'kdf', 'kind', 'version'],
  );

  const onb2 = makeOnboarding({ store: fakeStore(), window: SECURE });
  const r = await onb2.restore({ backup: JSON.stringify(artifact), passphrase: 'pw' });
  assert.equal(r.address, address);
  assert.equal((await onb2.status()).hasKey, true);
});

test('signLogin requires unlocked and produces a verifiable gc-msg-v1 proof', async () => {
  const onb = makeOnboarding({ store: fakeStore(), window: SECURE });
  await onb.create({ passphrase: 'pw' });
  await onb.lock();
  await assert.rejects(() => onb.signLogin('login:abc'), NoSigningKeyError);
  await onb.unlock({ passphrase: 'pw' });
  const proof = await onb.signLogin('login:abc');
  assert.equal(proof.scheme, 'gc-msg-v1');
  assert.equal(proof.message, 'login:abc');
  const v = await verifyMessage(proof, { maxAge: Number.MAX_SAFE_INTEGER });
  assert.equal(v.valid, true);
});

test('forget deletes the device record', async () => {
  const onb = makeOnboarding({ store: fakeStore(), window: SECURE });
  await onb.create({ passphrase: 'pw' });
  await onb.forget();
  const s = await onb.status();
  assert.equal(s.hasKey, false);
  assert.equal(s.unlocked, false);
});
