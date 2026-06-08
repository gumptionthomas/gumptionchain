// Shared per-page wallet session + auto-lock policy. An unlocked Wallet lives
// ONLY in this module-scoped holder for the life of the page; it is never
// persisted (the persisted record is always the gc-keyring ciphertext) and
// never sent (only the signature + public key leave the browser).
//
// The wallet is dropped — best-effort lock — on:
//   - a manual lock(),
//   - an idle timeout (default ~15 min, reset on user activity via touch()),
//   - the page being hidden (visibilitychange -> hidden) or unloaded (pagehide).
//
// On lock the reference is released. An RSA CryptoKey can't be zeroed in JS, so
// this is best-effort: dropping the reference is the strongest available
// guarantee. The page reload that follows a navigation away clears it fully.
//
// makeSession() returns a fresh, self-contained session so the logic is
// unit-testable with an injected clock/timer and fake document/window (no real
// DOM). A module-level default `session` is also exported for page glue.

export const DEFAULT_IDLE_MS = 15 * 60 * 1000; // 15 minutes
const DEFAULT_ACTIVITY_EVENTS = [
  'pointerdown',
  'keydown',
  'pointermove',
  'scroll',
  'touchstart',
];

export function makeSession() {
  let wallet = null;
  const lockCbs = [];

  // Idle-timer state: the injected timer + the current timer handle. Kept on
  // the session so touch() can clear/re-arm without re-passing the deps.
  let idleMs = null;
  let timerDeps = null; // { now, setTimer, clearTimer }
  let timerHandle = null;

  function getWallet() {
    return wallet;
  }

  function isUnlocked() {
    return wallet !== null;
  }

  function setWallet(w) {
    wallet = w ?? null;
    // Becoming unlocked (re)starts the idle countdown, so the documented idle
    // auto-lock holds across lock -> re-unlock (the timer is cleared on lock
    // and otherwise wouldn't re-arm until the next activity event). touch() is
    // a no-op until the idle timer has been configured (installAutoLock).
    if (wallet !== null) {
      touch();
    }
  }

  function onLock(cb) {
    lockCbs.push(cb);
  }

  function clearTimerHandle() {
    if (timerHandle != null && timerDeps) {
      timerDeps.clearTimer(timerHandle);
    }
    timerHandle = null;
  }

  function lock() {
    // Drop the reference first, then notify — a callback observing the session
    // must already see it locked.
    wallet = null;
    clearTimerHandle();
    for (const cb of lockCbs) {
      cb();
    }
  }

  // Arm (or re-arm) the idle timer. The deps make the clock/timer injectable;
  // defaults use the real ones. now() is reserved for future deadline math but
  // accepted now so callers wire a single clock.
  function armIdle(
    ms,
    {
      now = () => Date.now(),
      setTimer = (cb, t) => setTimeout(cb, t),
      clearTimer = (id) => clearTimeout(id),
    } = {},
  ) {
    idleMs = ms;
    timerDeps = { now, setTimer, clearTimer };
    clearTimerHandle();
    timerHandle = setTimer(() => {
      timerHandle = null;
      lock();
    }, ms);
  }

  // Reset the idle countdown on user activity. A no-op until armIdle has run.
  function touch() {
    if (idleMs == null || !timerDeps) {
      return;
    }
    armIdle(idleMs, timerDeps);
  }

  // Wire the page lifecycle + activity handlers. Thin: it only registers
  // listeners that delegate to lock()/touch(), so the timer logic above stays
  // unit-testable without a DOM. The clock/timer are injectable for tests.
  function installAutoLock({
    document,
    window,
    idleMs: ms = DEFAULT_IDLE_MS,
    activityEvents = DEFAULT_ACTIVITY_EVENTS,
    now,
    setTimer,
    clearTimer,
  } = {}) {
    const deps = {};
    if (now) deps.now = now;
    if (setTimer) deps.setTimer = setTimer;
    if (clearTimer) deps.clearTimer = clearTimer;

    if (document) {
      document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'hidden') {
          lock();
        }
      });
    }
    if (window) {
      window.addEventListener('pagehide', () => lock());
      for (const type of activityEvents) {
        window.addEventListener(type, () => touch());
      }
    }
    armIdle(ms, deps);
  }

  return {
    getWallet,
    setWallet,
    isUnlocked,
    lock,
    onLock,
    armIdle,
    touch,
    installAutoLock,
  };
}

// A module-level default session for page glue that wants a single shared
// holder per page load.
export const session = makeSession();
