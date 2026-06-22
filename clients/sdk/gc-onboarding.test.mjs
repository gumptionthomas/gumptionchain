import { test } from 'node:test';
import assert from 'node:assert/strict';

import {
  makeOnboarding, NoSigningKeyError, BadPassphraseError, BadBackupError,
} from './gc-onboarding.mjs';
import { verifyMessage } from './gc-message.mjs';
import { SigningKey } from './gc-signing-key.mjs';
import { exportEncrypted } from './gc-backup.mjs';
import { deriveSigningKey } from './gc-derive.mjs';
import { txid as txnTxid, signingData } from './gc-transaction.mjs';

function fakeStore() {
  let rec = null;
  return {
    get: async () => rec,
    put: async (r) => { rec = r; },
    delete: async () => { rec = null; },
  };
}

// A fake passkey adapter. enroll + discover return the SAME PRF so a derived
// identity re-derives to the same address. enroll captures its options so a
// test can assert residentKey. credentialId is stable.
function fakePasskey(fill = 7, credentialId = 'cred1') {
  const PRF = new Uint8Array(32).fill(fill);
  const calls = { enroll: [] };
  return {
    calls,
    isSupported: async () => true,
    enroll: async (ids = {}) => { calls.enroll.push(ids); return { credentialId, prfOutput: PRF }; },
    unlock: async () => PRF,
    discover: async () => ({ credentialId, prfOutput: PRF, userHandle: null }),
  };
}

const SECURE = { isSecureContext: true };

// --- status / empty -------------------------------------------------------

test('empty store: status reports no key + null kind, passkey off without an adapter', async () => {
  const onb = makeOnboarding({ store: fakeStore(), window: SECURE });
  const s = await onb.status();
  assert.equal(s.hasKey, false);
  assert.equal(s.unlocked, false);
  assert.equal(s.kind, null);
  assert.equal(s.address, null);
  assert.equal(s.passkeySupported, false);
  assert.equal(s.secureContext, true);
  assert.deepEqual(s.methods, []);
  assert.equal(s.passkeyEnrolled, false);
});

// --- wrap kind ------------------------------------------------------------

test('create({passphrase}) makes a wrap identity; status reads kind+methods without unlocking', async () => {
  const onb = makeOnboarding({ store: fakeStore(), window: SECURE });
  const { kind, address } = await onb.create({ passphrase: 'pw' });
  assert.equal(kind, 'wrap');
  assert.ok(address.startsWith('gc1'));
  await onb.lock();
  const s = await onb.status();
  assert.equal(s.kind, 'wrap');
  assert.deepEqual(s.methods, ['passphrase']);
  assert.equal(s.passkeyEnrolled, false);
});

test('create persists + holds unlocked, and onChange fires with fresh status', async () => {
  const onb = makeOnboarding({ store: fakeStore(), window: SECURE });
  let last = null;
  const off = onb.onChange((s) => { last = s; });
  const { address } = await onb.create({ passphrase: 'pw' });
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
  await assert.rejects(() => onb.unlock({ passphrase: 'WRONG' }), BadPassphraseError);
});

test('wrap + addPasskey: status shows both methods; unlock by passkey works', async () => {
  const passkey = fakePasskey();
  const onb = makeOnboarding({ store: fakeStore(), window: SECURE, passkey });
  const { address } = await onb.create({ passphrase: 'pw' });
  await onb.addPasskey({ passphrase: 'pw' });
  await onb.lock();
  const s = await onb.status();
  assert.equal(s.kind, 'wrap');
  assert.deepEqual(s.methods, ['passphrase', 'passkey']);
  assert.equal(s.passkeyEnrolled, true);
  const r = await onb.unlock({ passkey: true });
  assert.equal(r.address, address);
});

test('addPasskey enrolls a NON-resident convenience passkey (residentKey discouraged)', async () => {
  const passkey = fakePasskey();
  const onb = makeOnboarding({ store: fakeStore(), window: SECURE, passkey });
  await onb.create({ passphrase: 'pw' });
  await onb.addPasskey({ passphrase: 'pw' });
  // the convenience passkey enroll must request a discouraged (non-resident) key
  assert.equal(passkey.calls.enroll.at(-1).residentKey, 'discouraged');
});

test('create with withPasskey but unsupported adapter falls back to wrap (no throw)', async () => {
  const passkey = {
    isSupported: async () => false,
    enroll: async () => { throw new Error('enroll must not run when unsupported'); },
    unlock: async () => { throw new Error('unlock must not run when unsupported'); },
    discover: async () => { throw new Error('discover must not run when unsupported'); },
  };
  const onb = makeOnboarding({ store: fakeStore(), window: SECURE, passkey });
  const { kind, address } = await onb.create({ passphrase: 'pw', withPasskey: true });
  assert.equal(kind, 'wrap');
  assert.ok(address.startsWith('gc1'));
  assert.equal((await onb.status()).unlocked, true);
});

// --- derived kind ---------------------------------------------------------

test('create({withPasskey}) derives a passkey identity: returns kind+mnemonic, no key material stored', async () => {
  const store = fakeStore();
  const onb = makeOnboarding({ store, window: SECURE, passkey: fakePasskey() });
  const { kind, address, mnemonic } = await onb.create({ withPasskey: true });
  assert.equal(kind, 'derived');
  assert.ok(address.startsWith('gc1'));
  assert.equal(mnemonic.split(' ').length, 24);
  // record carries version + kind + credentialId, NO signing_key_ct / wraps
  const rec = await store.get();
  assert.equal(rec.version, 2);
  assert.equal(rec.kind, 'derived');
  assert.equal(rec.credentialId, 'cred1');
  assert.equal(rec.signing_key_ct, undefined);
  assert.equal(rec.wraps, undefined);
  // unlocked in memory
  assert.equal((await onb.status()).unlocked, true);
});

test('derived status reports kind+passkeyEnrolled without unlocking', async () => {
  const onb = makeOnboarding({ store: fakeStore(), window: SECURE, passkey: fakePasskey() });
  await onb.create({ withPasskey: true });
  await onb.lock();
  const s = await onb.status();
  assert.equal(s.kind, 'derived');
  assert.equal(s.passkeyEnrolled, true);
  assert.deepEqual(s.methods, []);
});

test('derived unlock re-derives via the passkey (No-2FA, empty creds) and matches the stored address', async () => {
  const onb = makeOnboarding({ store: fakeStore(), window: SECURE, passkey: fakePasskey() });
  const { address } = await onb.create({ withPasskey: true });
  await onb.lock();
  const r = await onb.unlock();
  assert.equal(r.address, address);
  assert.equal((await onb.status()).unlocked, true);
});

test('derived unlock throws when the passkey re-derives a different address (wrong passkey)', async () => {
  const store = fakeStore();
  // enroll with one PRF...
  const onb1 = makeOnboarding({ store, window: SECURE, passkey: fakePasskey(7) });
  await onb1.create({ withPasskey: true });
  await onb1.lock();
  // ...then a different PRF on unlock derives a different address -> not 'ok'
  const onb2 = makeOnboarding({ store, window: SECURE, passkey: fakePasskey(3) });
  await assert.rejects(() => onb2.unlock(), NoSigningKeyError);
});

test('derived backup returns the recovery phrase; restoring it lands a WRAP identity at the same address', async () => {
  const onb = makeOnboarding({ store: fakeStore(), window: SECURE, passkey: fakePasskey() });
  const { address } = await onb.create({ withPasskey: true });
  const b = await onb.backup({});
  assert.equal(b.kind, 'derived');
  assert.equal(b.mnemonic.split(' ').length, 24);

  const onb2 = makeOnboarding({ store: fakeStore(), window: SECURE });
  const r = await onb2.restore({ mnemonic: b.mnemonic, passphrase: 'pw' });
  assert.equal(r.kind, 'wrap');
  assert.equal(r.address, address);
  assert.equal((await onb2.status()).kind, 'wrap');
});

test('derived backup while locked taps the passkey and does not leave it unlocked', async () => {
  const onb = makeOnboarding({ store: fakeStore(), window: SECURE, passkey: fakePasskey() });
  await onb.create({ withPasskey: true });
  await onb.lock();
  const b = await onb.backup({});
  assert.equal(b.kind, 'derived');
  assert.equal((await onb.status()).unlocked, false);
});

// --- backup / restore (wrap) ---------------------------------------------

test('wrap backup yields an encrypted artifact (no raw key) + filename; restore recovers the same address', async () => {
  const onb1 = makeOnboarding({ store: fakeStore(), window: SECURE });
  const { address } = await onb1.create({ passphrase: 'pw' });
  const { kind, artifact, filename } = await onb1.backup({ passphrase: 'pw' });
  assert.equal(kind, 'wrap');
  assert.equal(artifact.kind, 'gc-signing-key-backup');
  assert.match(filename, /^gc-signing-key-backup-.*\.json$/);
  // the artifact is exactly the encrypted envelope — no plaintext / extra fields
  assert.deepEqual(
    Object.keys(artifact).sort(),
    ['address', 'ciphertext', 'iv', 'kdf', 'kind', 'version'],
  );

  const onb2 = makeOnboarding({ store: fakeStore(), window: SECURE });
  const r = await onb2.restore({ backup: JSON.stringify(artifact), passphrase: 'pw' });
  assert.equal(r.kind, 'wrap');
  assert.equal(r.address, address);
  assert.equal((await onb2.status()).hasKey, true);
});

test('restore requires a mnemonic or a backup, and a passphrase', async () => {
  const onb = makeOnboarding({ store: fakeStore(), window: SECURE });
  await assert.rejects(() => onb.restore({ passphrase: 'pw' }), BadBackupError);
  const k = await SigningKey.generate();
  await assert.rejects(async () => onb.restore({ mnemonic: await k.mnemonic() }), BadPassphraseError);
});

// --- signing / lifecycle (unchanged behavior) ----------------------------

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

test('signTransaction: throws when locked; signs a node-built unsigned txn when unlocked', async () => {
  const onb = makeOnboarding({ store: fakeStore(), window: SECURE });
  await assert.rejects(() => onb.signTransaction({ txid: 'x' }), NoSigningKeyError);

  const k = await SigningKey.generate();
  const backup = await exportEncrypted(k, 'pw');
  await onb.restore({ backup, passphrase: 'pw' });

  const base = {
    timestamp: '1700000000',
    address: await k.address(),
    signature: null,
    inflows: [],
    outflows: [{ amount: 100, support: 'Z29ibGlucw' }],
    version: '1',
    prev_hash: null,
  };
  const unsigned = { ...base, txid: await txnTxid(base) };
  const signed = await onb.signTransaction(unsigned);
  assert.equal(signed.address, await k.address());
  assert.equal(await k.verify(signingData(signed), signed.signature), true);
});

test('discover() delegates to the passkey adapter; null without one', async () => {
  const onb = makeOnboarding({ store: fakeStore(), window: SECURE, passkey: fakePasskey() });
  const r = await onb.discover();
  assert.equal(r.credentialId, 'cred1');
  const onb2 = makeOnboarding({ store: fakeStore(), window: SECURE });
  assert.equal(await onb2.discover(), null);
});

// --- onb.recognize(): discover -> derive -> adopt on entry (#328) ----------

// A passkey fake whose discover() returns a chosen userHandle. enroll/unlock
// return the same PRF so the derived address is deterministic.
function recognizePasskey({ prfFill = 7, credentialId = 'credR', userHandle = null } = {}) {
  const PRF = new Uint8Array(32).fill(prfFill);
  return {
    PRF,
    isSupported: async () => true,
    enroll: async () => ({ credentialId, prfOutput: PRF }),
    unlock: async () => PRF,
    discover: async () => ({ credentialId, prfOutput: PRF, userHandle }),
  };
}

test('recognize() adopts a derived passkey (random non-address userHandle)', async () => {
  const pk = recognizePasskey({ prfFill: 11, credentialId: 'credD', userHandle: 'not-an-address' });
  const store = fakeStore();
  const onb = makeOnboarding({ store, window: SECURE, passkey: pk });
  const D = await (await deriveSigningKey(pk.PRF)).address();
  const r = await onb.recognize();
  assert.deepEqual(r, { recognized: true, kind: 'derived', address: D });
  const rec = await store.get();
  assert.equal(rec.version, 2);
  assert.equal(rec.kind, 'derived');
  assert.equal(rec.address, D);
  assert.equal(rec.credentialId, 'credD');
  assert.equal(rec.signing_key_ct, undefined);
  assert.equal(rec.wraps, undefined);
  assert.equal((await onb.status()).unlocked, true);
});

test('recognize() adopts when the userHandle equals the derived address', async () => {
  const PRF_FILL = 13;
  const D = await (await deriveSigningKey(new Uint8Array(32).fill(PRF_FILL))).address();
  const pk = recognizePasskey({ prfFill: PRF_FILL, credentialId: 'credE', userHandle: D });
  const store = fakeStore();
  const onb = makeOnboarding({ store, window: SECURE, passkey: pk });
  const r = await onb.recognize();
  assert.deepEqual(r, { recognized: true, kind: 'derived', address: D });
  assert.equal((await store.get()).kind, 'derived');
});

test('recognize() does NOT adopt a wrap passkey — phantom guard', async () => {
  const wrapAddr = await (await SigningKey.generate()).address(); // a real address
  const pk = recognizePasskey({ prfFill: 17, credentialId: 'credW', userHandle: wrapAddr });
  const D = await (await deriveSigningKey(pk.PRF)).address();
  assert.notEqual(wrapAddr, D); // sanity: the address claim differs from the derived one
  const store = fakeStore();
  const onb = makeOnboarding({ store, window: SECURE, passkey: pk });
  const r = await onb.recognize();
  assert.deepEqual(r, { recognized: true, kind: 'wrap', address: wrapAddr });
  assert.equal(await store.get(), null);              // nothing persisted
  assert.equal((await onb.status()).unlocked, false); // key not held
});

test('recognize() is recognized:false when discover finds nothing', async () => {
  const pk = { isSupported: async () => true, discover: async () => null };
  const onb = makeOnboarding({ store: fakeStore(), window: SECURE, passkey: pk });
  assert.deepEqual(await onb.recognize(), { recognized: false });
});

test('recognize() is recognized:false with no passkey adapter', async () => {
  const onb = makeOnboarding({ store: fakeStore(), window: SECURE });
  assert.deepEqual(await onb.recognize(), { recognized: false });
});

test('recognize() is recognized:false when discover throws (PRF absent / unsupported)', async () => {
  const pk = {
    isSupported: async () => true,
    discover: async () => { throw new Error('passkey PRF not available'); },
  };
  const onb = makeOnboarding({ store: fakeStore(), window: SECURE, passkey: pk });
  assert.deepEqual(await onb.recognize(), { recognized: false });
});
