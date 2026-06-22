// Cross-document, non-extractable, auto-locking sign-only session. Holds a sign-only
// SigningKey handle in an injected session store (cross-document) + an in-memory cache;
// exposes signLogin/signTransaction; never exposes the seed. Populate methods
// (recognize/unlock/createDerived) and auto-lock are added in later tasks. DOM-free;
// store/passkey/clock/broadcast are injected.
import { SigningKey } from './gc-signing-key.mjs';
import { signMessage } from './gc-message.mjs';
import { signUnsignedTxn } from './gc-transaction.mjs';
import { NoSigningKeyError } from './gc-errors.mjs';

export function makeSessionSigner({ store } = {}) {
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

  function onLock(cb) { lockCbs.push(cb); }

  async function lock() {
    cached = null;
    await store.delete();
    for (const cb of lockCbs) cb();
  }

  return { adopt, status, signLogin, signTransaction, onLock, lock };
}
