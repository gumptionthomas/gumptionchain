// Cross-document, non-extractable, auto-locking sign-only session. Holds a sign-only
// SigningKey handle in an injected session store (cross-document) + an in-memory cache;
// exposes signLogin/signTransaction; never exposes the seed. Populate methods
// (recognize/unlock/createDerived) and auto-lock are added in later tasks. DOM-free;
// store/passkey/clock/broadcast are injected.
import { SigningKey } from './gc-signing-key.mjs';
import { signMessage } from './gc-message.mjs';
import { signUnsignedTxn } from './gc-transaction.mjs';
import { NoSigningKeyError } from './gc-errors.mjs';
import * as keyring from './gc-keyring.mjs';
import { makeDerivedIdentity, classifyRecognition } from './gc-derived-identity.mjs';
import { deriveSigningKey } from './gc-derive.mjs';

export function makeSessionSigner({ store, durableStore, passkey } = {}) {
  let cached = null; // in-memory sign-only SigningKey for this document
  const lockCbs = [];

  async function held() {
    if (cached) return cached;
    const rec = await store.get();
    if (!rec) return null;
    cached = SigningKey.fromSignOnlyHandle(rec);
    return cached;
  }

  async function adopt(key) {
    const handle = await key.toSignOnlyHandle(); // always non-extractable
    await store.put(handle);
    cached = SigningKey.fromSignOnlyHandle(handle);
    return { address: handle.address };
  }

  async function status() {
    const k = await held();
    return { signedIn: Boolean(k), address: k ? await k.address() : null };
  }

  async function signLogin(challenge, { timestamp } = {}) {
    const k = await held();
    if (!k) throw new NoSigningKeyError('locked: no session signer');
    return signMessage(k, challenge, { timestamp });
  }

  async function signTransaction(unsigned) {
    const k = await held();
    if (!k) throw new NoSigningKeyError('locked: no session signer');
    return signUnsignedTxn(unsigned, k);
  }

  async function recognize() {
    if (!passkey || typeof passkey.discover !== 'function') return { verdict: 'none' };
    let found;
    try { found = await passkey.discover(); } catch { return { verdict: 'none' }; }
    if (!found) return { verdict: 'none' };
    const sk = await deriveSigningKey(found.prfOutput);
    const derivedAddress = await sk.address();
    if (classifyRecognition({ userHandle: found.userHandle, derivedAddress }) === 'wrap') {
      return { verdict: 'wrap', address: found.userHandle };
    }
    await durableStore.put({
      version: keyring.VERSION, kind: 'derived', address: derivedAddress,
      credentialId: found.credentialId,
    });
    await adopt(sk);
    return { verdict: 'derived', address: derivedAddress };
  }

  async function createDerived({ userName } = {}) {
    const derived = makeDerivedIdentity({ passkey });
    const { signing_key, address, mnemonic, credentialId } = await derived.enroll({ userName });
    await durableStore.put({
      version: keyring.VERSION, kind: 'derived', address, credentialId,
    });
    await adopt(signing_key);
    return { address, mnemonic };
  }

  async function unlock({ passphrase, passkey: usePasskey } = {}) {
    const key = await keyring.unlock(
      { store: durableStore, passkey: usePasskey ? passkey : undefined }, { passphrase },
    );
    return adopt(key);
  }

  function onLock(cb) { lockCbs.push(cb); }

  async function lock() {
    cached = null;
    await store.delete();
    for (const cb of lockCbs) cb();
  }

  return { adopt, status, signLogin, signTransaction, recognize, createDerived, unlock, onLock, lock };
}
