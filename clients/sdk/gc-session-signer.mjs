// Cross-document, non-extractable, auto-locking sign-only session. Holds a sign-only
// SigningKey handle in an injected session store (cross-document) + an in-memory cache;
// exposes signLogin/signTransaction; never exposes the seed. DOM-free;
// store/passkey/clock/broadcast are injected.
//
// Auto-lock mirrors base's signing-key-session.mjs: an idle timeout (default
// ~15 min, reset on user activity via touch()), plus the page being hidden
// (visibilitychange -> hidden) or unloaded (pagehide) when installAutoLock wires
// the DOM lifecycle. A lock in one tab/document is broadcast over an injected
// BroadcastChannel-like object so the other tabs drop their in-memory cache too.
import { SigningKey } from './gc-signing-key.mjs';
import { signMessage } from './gc-message.mjs';
import { signUnsignedTxn } from './gc-transaction.mjs';
import { NoSigningKeyError } from './gc-errors.mjs';
import * as keyring from './gc-keyring.mjs';
import { makeDerivedIdentity, classifyRecognition } from './gc-derived-identity.mjs';
import { deriveSigningKey } from './gc-derive.mjs';

export const DEFAULT_IDLE_MS = 15 * 60 * 1000; // 15 minutes
const DEFAULT_ACTIVITY_EVENTS = ['pointerdown', 'keydown', 'scroll', 'touchstart'];

export function makeSessionSigner({ store, durableStore, passkey, idleMs = DEFAULT_IDLE_MS, broadcast, clock } = {}) {
  let cached = null; // in-memory sign-only SigningKey for this document
  const lockCbs = [];

  // Idle-timer deps (injectable clock/timer). Defaults reference the real
  // globals only here, inside the function, guarded — never at module top level.
  const timerDeps = {
    now: clock?.now ?? (() => Date.now()),
    setTimer: clock?.setTimer ?? ((cb, t) => setTimeout(cb, t)),
    clearTimer: clock?.clearTimer ?? ((id) => clearTimeout(id)),
  };
  let timerHandle = null;
  // Auto-lock is OFF until installAutoLock() turns it on. Until then touch()
  // (called by adopt() when a session begins) is a no-op — so a signer with no
  // auto-lock installed never arms a timer (mirrors base's signing-key-session,
  // where touch() is a no-op until armIdle has run). Without this, every adopt()
  // would leak a real 15-min setTimeout that keeps the process/event loop alive.
  let autoLockArmed = false;

  function clearTimerHandle() {
    if (timerHandle != null) timerDeps.clearTimer(timerHandle);
    timerHandle = null;
  }

  // Arm (or re-arm) the idle timer toward an auto-lock. now() is threaded for
  // parity with base (deadline math may use it); the lock on expiry is the point.
  function armIdle(ms = idleMs) {
    autoLockArmed = true;
    clearTimerHandle();
    timerHandle = timerDeps.setTimer(() => {
      timerHandle = null;
      lock();
    }, ms);
  }

  // Reset the idle countdown on activity / on a session beginning. A no-op
  // until auto-lock has been armed (installAutoLock / an explicit armIdle).
  function touch() {
    if (!autoLockArmed) return;
    armIdle(idleMs);
  }

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
    // A session is beginning: (re)start the idle countdown — mirrors base's
    // setSigningKey -> touch(). A no-op effect on behavior until the timer is
    // wired, but with an injected clock it arms immediately.
    touch();
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

  // Drop the in-memory key + idle timer and fire onLock callbacks. Shared by
  // lock() and the cross-tab broadcast handler. Does NOT touch the store or
  // re-broadcast — those are lock()'s responsibility (the originating tab).
  function clearLocal() {
    cached = null;
    clearTimerHandle();
    for (const cb of lockCbs) cb();
  }

  async function lock() {
    clearLocal();
    await store.delete();
    broadcast?.postMessage({ type: 'lock' });
  }

  // Wire the page lifecycle + activity handlers. Thin: registers listeners that
  // delegate to lock()/touch(), keeping the timer logic unit-testable without a
  // DOM. The clock/timer fall back to the injected `clock` when absent.
  function installAutoLock({
    document,
    window,
    idleMs: ms = idleMs,
    activityEvents = DEFAULT_ACTIVITY_EVENTS,
    now,
    setTimer,
    clearTimer,
  } = {}) {
    if (now) timerDeps.now = now;
    if (setTimer) timerDeps.setTimer = setTimer;
    if (clearTimer) timerDeps.clearTimer = clearTimer;

    if (document) {
      document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'hidden') lock();
      });
    }
    if (window) {
      window.addEventListener('pagehide', () => lock());
      for (const type of activityEvents) {
        window.addEventListener(type, () => touch());
      }
    }
    armIdle(ms);
  }

  // Cross-tab lock-sync: a lock in another document drops our local cache too.
  // Only clear local state — the originating tab already cleared the store and
  // broadcast, so we must NOT re-delete or re-broadcast (avoid a loop).
  if (broadcast) {
    broadcast.onmessage = (ev) => {
      if (ev?.data?.type === 'lock') clearLocal();
    };
  }

  return {
    adopt, status, signLogin, signTransaction, recognize, createDerived,
    unlock, onLock, lock, armIdle, touch, installAutoLock,
  };
}
