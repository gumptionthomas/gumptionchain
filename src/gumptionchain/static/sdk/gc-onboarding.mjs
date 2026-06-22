// Headless, style-agnostic signing-key onboarding controller. Orchestrates the
// low-level gc-* modules into create / back up / restore / unlock / sign-login
// over a single in-memory unlocked-key holder. NO DOM, NO CSS, NO framework:
// the consuming app owns all markup and renders from status() + onChange().
//
// Kind-aware (two identity kinds behind one surface):
//   - 'derived' — a passkey identity whose seed = KDF(passkey-PRF) (No-2FA). The
//     record holds { kind, address, credentialId } and NO key material; the key
//     is re-derived from the passkey on demand (makeDerivedIdentity.resolve).
//   - 'wrap'    — a random key encrypted in the keyring, unlockable by passphrase
//     (and optionally a non-resident convenience passkey).
import { SigningKey } from './gc-signing-key.mjs';
import * as keyring from './gc-keyring.mjs';
import { makeIdbStore } from './gc-store-idb.mjs';
import { exportEncrypted, importEncrypted } from './gc-backup.mjs';
import { makeWebauthnPasskey } from './gc-passkey-webauthn.mjs';
import {
  classifyRecognition, makeDerivedIdentity,
} from './gc-derived-identity.mjs';
import { deriveSigningKey } from './gc-derive.mjs';
import { signMessage } from './gc-message.mjs';
import { signUnsignedTxn } from './gc-transaction.mjs';
import {
  NoSigningKeyError,
  UnsupportedError,
  BadBackupError,
  BadPassphraseError,
} from './gc-errors.mjs';

// Re-exported so consuming apps can catch by type and render their own copy.
export {
  NoSigningKeyError, UnsupportedError, BadBackupError, BadPassphraseError,
};

function backupFilename(address) {
  const slug = (address || 'signing-key')
    .replace(/[^A-Za-z0-9]/g, '')
    .slice(0, 12);
  return `gc-signing-key-backup-${slug || 'signing-key'}.json`;
}

export function makeOnboarding({
  store = makeIdbStore(),
  rpId,
  rpName,
  passkey = null,
  window: win = globalThis.window,
} = {}) {
  // A passkey adapter is built from rpId/rpName unless one is injected; absent
  // both, passkey features stay unavailable (status reports passkeySupported:false).
  const pk = passkey ?? ((rpId && rpName) ? makeWebauthnPasskey({ rpId, rpName }) : null);
  // The derive flow (enroll/resolve) composes the same passkey adapter.
  const derived = pk ? makeDerivedIdentity({ passkey: pk }) : null;

  let key = null; // the in-memory unlocked SigningKey, or null when locked
  const listeners = new Set();

  const secureContext = () => Boolean(win && win.isSecureContext);

  async function passkeySupported() {
    if (!(pk && secureContext())) return false;
    try {
      return await pk.isSupported();
    } catch {
      return false;
    }
  }

  // The stored record's identity kind. New records always carry `kind`; a stale
  // record with keyring `wraps` and no `kind` reads as 'wrap' (a non-crashing
  // default, NOT a migration path — greenfield recreates the dev DB).
  function recordKind(rec) {
    if (!rec) return null;
    return rec.kind ?? (rec.wraps ? 'wrap' : null);
  }

  // Which unlock methods a WRAP record is enrolled under, read from its wraps
  // without unlocking. Derived records have no wraps -> []. Stable order.
  function enrolledMethods(rec) {
    const wraps = (rec && rec.wraps) || {};
    return ['passphrase', 'passkey'].filter((m) => Boolean(wraps[m]));
  }

  async function status() {
    const rec = await store.get();
    const kind = recordKind(rec);
    const address = key ? await key.address() : (rec ? rec.address : null);
    const methods = enrolledMethods(rec);
    return {
      hasKey: Boolean(rec),
      unlocked: Boolean(key),
      kind,
      address,
      // passkeySupported = device capability; passkeyEnrolled = stored state.
      // A derived identity IS a passkey; a wrap identity is passkey-enrolled
      // when it carries a passkey wrap.
      passkeySupported: await passkeySupported(),
      passkeyEnrolled: kind === 'derived' || methods.includes('passkey'),
      methods,
      secureContext: secureContext(),
    };
  }

  async function notify() {
    const snapshot = await status();
    for (const fn of listeners) {
      try {
        fn(snapshot);
      } catch {
        // A consumer's onChange handler must not break the action or starve
        // other listeners. Render errors are the app's problem, not ours.
      }
    }
  }

  function onChange(fn) {
    listeners.add(fn);
    return () => listeners.delete(fn);
  }

  // userId/userName + an optional residentKey preference for passkey enrollment.
  function passkeyIds(userName, address, residentKey) {
    return {
      userId: address,
      userName: userName || address,
      ...(residentKey ? { residentKey } : {}),
    };
  }

  // A passkey at create time means DERIVE (seed = KDF(PRF), No-2FA). Without a
  // (supported) passkey it's a passphrase-WRAP identity. An unsupported passkey
  // falls back to wrap rather than throwing.
  async function create({ passphrase, withPasskey = false, userName } = {}) {
    if (withPasskey && (await passkeySupported())) {
      const { signing_key, address, mnemonic, credentialId } =
        await derived.enroll({ userName });
      await store.put({
        version: keyring.VERSION, kind: 'derived', address, credentialId,
      });
      key = signing_key;
      await notify();
      return { kind: 'derived', address, mnemonic };
    }
    const sk = await SigningKey.generate();
    await keyring.enroll(sk, { store }, { passphrase });
    key = sk;
    await notify();
    return { kind: 'wrap', address: await sk.address() };
  }

  async function unlock({ passphrase, passkey: usePasskey } = {}) {
    const rec = await store.get();
    if (recordKind(rec) === 'derived') {
      if (!derived) {
        throw new NoSigningKeyError(
          'no passkey adapter to rehydrate a derived identity',
        );
      }
      // No-2FA: re-derive from the passkey PRF and match the stored address.
      const r = await derived.resolve({ expectedAddress: rec.address });
      if (r.status !== 'ok') {
        // no-passkey / needs-passphrase / mismatch — can't rehydrate here. The
        // hub treats this as "offer import", never a passphrase prompt.
        throw new NoSigningKeyError('could not rehydrate this passkey identity');
      }
      key = r.signing_key;
      await notify();
      return { address: await key.address() };
    }
    key = await keyring.unlock(
      { store, passkey: usePasskey ? pk : undefined },
      { passphrase },
    );
    await notify();
    return { address: await key.address() };
  }

  // A recovery phrase OR an encrypted backup both land as a WRAP identity —
  // seamlessness can't follow a phrase (no new passkey reproduces an old
  // passkey's PRF). Derived identities don't "restore"; a synced passkey just
  // unlock()s them.
  async function restore({ mnemonic, backup, passphrase } = {}) {
    let sk;
    if (mnemonic != null) {
      sk = await SigningKey.fromMnemonic(mnemonic);
    } else if (backup != null) {
      const artifact = typeof backup === 'string' ? JSON.parse(backup) : backup;
      sk = await importEncrypted(artifact, passphrase);
    } else {
      throw new BadBackupError('restore requires a mnemonic or a backup');
    }
    if (passphrase == null || passphrase === '') {
      throw new BadPassphraseError(
        'a passphrase is required to store a restored identity',
      );
    }
    await keyring.enroll(sk, { store }, { passphrase });
    key = sk;
    await notify();
    return { kind: 'wrap', address: await sk.address() };
  }

  async function backup({ passphrase } = {}) {
    const rec = await store.get();
    if (recordKind(rec) === 'derived') {
      // Return the recovery phrase. Use the held key if unlocked, else tap the
      // passkey to re-derive — without leaving a previously-locked id unlocked.
      const wasLocked = !key;
      let sk = key;
      if (wasLocked) {
        if (!derived) {
          throw new NoSigningKeyError(
            'no passkey adapter to back up a derived identity',
          );
        }
        const r = await derived.resolve({ expectedAddress: rec.address });
        if (r.status !== 'ok') {
          throw new NoSigningKeyError(
            'could not rehydrate this passkey identity',
          );
        }
        sk = r.signing_key;
      }
      const mnemonic = await sk.mnemonic();
      await notify();
      return { kind: 'derived', mnemonic };
    }
    const wasLocked = !key;
    if (wasLocked) {
      key = await keyring.unlock({ store }, { passphrase });
    }
    const artifact = await exportEncrypted(key, passphrase);
    const filename = backupFilename(await key.address());
    if (wasLocked) {
      key = null; // a backup is a read; don't leave the key unlocked
    }
    await notify();
    return { kind: 'wrap', artifact, filename };
  }

  // Add a NON-RESIDENT convenience passkey to a wrap identity. Non-resident
  // (residentKey:'discouraged') keeps it out of other origins' discover(), so it
  // can't produce hollow cross-app recognition. (A preference, not a guarantee —
  // some authenticators make it discoverable anyway; a strong mitigation.)
  async function addPasskey({ passphrase, userName } = {}) {
    const rec = await store.get();
    const address = await keyring.addPasskey(
      { store, passkey: pk },
      { passphrase },
      passkeyIds(userName, rec ? rec.address : undefined, 'discouraged'),
    );
    await notify();
    return { address };
  }

  async function signLogin(challenge, { timestamp } = {}) {
    if (!key) {
      throw new NoSigningKeyError('locked: unlock before signing a login challenge');
    }
    return signMessage(key, challenge, { timestamp });
  }

  async function signTransaction(unsigned) {
    if (!key) {
      throw new NoSigningKeyError('locked: unlock before signing a transaction');
    }
    // signUnsignedTxn recomputes the txid and throws on mismatch, so a
    // dishonest node can't get a signature over fields the user didn't authorize.
    return signUnsignedTxn(unsigned, key);
  }

  async function discover(opts) {
    if (!pk || typeof pk.discover !== 'function') {
      return null;
    }
    return pk.discover(opts);
  }

  // Recognition on entry: discover an existing Gumption passkey on the rpId,
  // derive its address, and ADOPT a derived identity (persist the record +
  // hold the key) — or report a recognized wrap identity without adopting.
  // For a device with NO local identity: adopting OVERWRITES the singleton
  // store record, so callers must invoke this only when status().hasKey is
  // false (the hub gates on exactly that). Never throws for the absent /
  // cancel / unsupported / PRF-absent paths — they resolve to recognized:false.
  async function recognize() {
    let found;
    try {
      found = await discover();
    } catch {
      return { recognized: false };
    }
    if (!found) {
      return { recognized: false };
    }
    const sk = await deriveSigningKey(found.prfOutput);
    const derivedAddress = await sk.address();
    const uh = found.userHandle;
    if (classifyRecognition({ userHandle: uh, derivedAddress }) === 'wrap') {
      // WRAP passkey: its PRF unlocks a keyring — it is NOT the signing seed,
      // so deriving it yields a phantom address. Recognize WHO (the userHandle
      // address), but adopt nothing (the real key isn't derivable here).
      return { recognized: true, kind: 'wrap', address: uh };
    }
    // DERIVED passkey: userHandle is random (not an address), or it backs the
    // derived address — adopt it (the same record create({withPasskey}) writes).
    await store.put({
      version: keyring.VERSION,
      kind: 'derived',
      address: derivedAddress,
      credentialId: found.credentialId,
    });
    key = sk;
    await notify();
    return { recognized: true, kind: 'derived', address: derivedAddress };
  }

  async function lock() {
    key = null;
    await notify();
  }

  async function forget() {
    await keyring.clear(store);
    key = null;
    await notify();
  }

  return {
    status, onChange, create, unlock, restore, backup, addPasskey, discover,
    recognize, signLogin, signTransaction, lock, forget,
  };
}
