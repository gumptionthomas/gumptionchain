// Headless, style-agnostic signing-key onboarding controller. Orchestrates the
// low-level gc-* modules into create / back up / restore / unlock / sign-login
// over a single in-memory unlocked-key holder. NO DOM, NO CSS, NO framework:
// the consuming app owns all markup and renders from status() + onChange().
import { SigningKey } from './gc-signing-key.mjs';
import * as keyring from './gc-keyring.mjs';
import { makeIdbStore } from './gc-store-idb.mjs';
import { exportEncrypted, importEncrypted } from './gc-backup.mjs';
import { makeWebauthnPasskey } from './gc-passkey-webauthn.mjs';
import { signMessage } from './gc-message.mjs';
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

  let key = null; // the in-memory unlocked SigningKey, or null when locked
  const listeners = new Set();

  const secureContext = () => Boolean(win && win.isSecureContext);

  async function status() {
    const rec = await store.get();
    const address = key ? await key.address() : (rec ? rec.address : null);
    let passkeySupported = false;
    if (pk && secureContext()) {
      try {
        passkeySupported = await pk.isSupported();
      } catch {
        passkeySupported = false;
      }
    }
    return {
      hasKey: Boolean(rec),
      unlocked: Boolean(key),
      address,
      passkeySupported,
      secureContext: secureContext(),
    };
  }

  async function notify() {
    const snapshot = await status();
    for (const fn of listeners) fn(snapshot);
  }

  function onChange(fn) {
    listeners.add(fn);
    return () => listeners.delete(fn);
  }

  function passkeyIds(userName, address) {
    return { userId: address, userName: userName || address };
  }

  async function create({ passphrase, withPasskey = false, userName } = {}) {
    const sk = await SigningKey.generate();
    await keyring.enroll(sk, { store }, { passphrase });
    const address = await sk.address();
    if (withPasskey && pk) {
      await keyring.addPasskey(
        { store, passkey: pk }, { passphrase }, passkeyIds(userName, address),
      );
    }
    key = sk;
    await notify();
    return { address };
  }

  async function unlock({ passphrase, passkey: usePasskey } = {}) {
    key = await keyring.unlock(
      { store, passkey: usePasskey ? pk : undefined },
      { passphrase },
    );
    await notify();
    return { address: await key.address() };
  }

  async function restore({ backup, passphrase } = {}) {
    const artifact = typeof backup === 'string' ? JSON.parse(backup) : backup;
    const sk = await importEncrypted(artifact, passphrase);
    await keyring.enroll(sk, { store }, { passphrase });
    key = sk;
    await notify();
    return { address: await sk.address() };
  }

  async function backup({ passphrase } = {}) {
    if (!key) {
      key = await keyring.unlock({ store }, { passphrase });
    }
    const artifact = await exportEncrypted(key, passphrase);
    await notify();
    return { artifact, filename: backupFilename(await key.address()) };
  }

  async function addPasskey({ passphrase, userName } = {}) {
    const rec = await store.get();
    const address = await keyring.addPasskey(
      { store, passkey: pk },
      { passphrase },
      passkeyIds(userName, rec ? rec.address : undefined),
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
    status, onChange, create, unlock, restore, backup, addPasskey,
    signLogin, lock, forget,
  };
}
