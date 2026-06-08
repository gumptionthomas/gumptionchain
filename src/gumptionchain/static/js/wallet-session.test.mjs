import { test } from 'node:test';
import assert from 'node:assert/strict';
import { makeSession } from './wallet-session.mjs';

// A fake wallet is just an opaque token here — the session never inspects it.
const fakeWallet = (id = 'w') => ({ id });

// --- the holder: set/get/lock/isUnlocked + onLock callbacks --------------

test('setWallet/getWallet/isUnlocked track the held reference', () => {
  const s = makeSession();
  assert.equal(s.isUnlocked(), false);
  assert.equal(s.getWallet(), null);
  const w = fakeWallet();
  s.setWallet(w);
  assert.equal(s.isUnlocked(), true);
  assert.equal(s.getWallet(), w);
});

test('lock() drops the wallet reference and fires onLock callbacks', () => {
  const s = makeSession();
  let fired = 0;
  s.onLock(() => {
    fired += 1;
  });
  s.setWallet(fakeWallet());
  s.lock();
  assert.equal(s.getWallet(), null);
  assert.equal(s.isUnlocked(), false);
  assert.equal(fired, 1);
});

test('lock() while already locked still fires onLock (idempotent drop)', () => {
  const s = makeSession();
  let fired = 0;
  s.onLock(() => {
    fired += 1;
  });
  s.lock();
  assert.equal(fired, 1);
  assert.equal(s.getWallet(), null);
});

test('multiple onLock callbacks all fire', () => {
  const s = makeSession();
  const seen = [];
  s.onLock(() => seen.push('a'));
  s.onLock(() => seen.push('b'));
  s.setWallet(fakeWallet());
  s.lock();
  assert.deepEqual(seen, ['a', 'b']);
});

// --- the idle timer: armIdle + touch with an injected clock/timer --------

function fakeTimer() {
  // A minimal setTimeout/clearTimeout that records the scheduled callback +
  // delay and lets a test fire it deterministically.
  let next = 1;
  const timers = new Map();
  return {
    setTimer(cb, ms) {
      const id = next;
      next += 1;
      timers.set(id, { cb, ms });
      return id;
    },
    clearTimer(id) {
      timers.delete(id);
    },
    // test helpers
    pending() {
      return [...timers.values()];
    },
    fireAll() {
      const cbs = [...timers.values()].map((t) => t.cb);
      timers.clear();
      for (const cb of cbs) cb();
    },
  };
}

test('armIdle schedules a lock after idleMs', () => {
  const s = makeSession();
  let locked = 0;
  s.onLock(() => {
    locked += 1;
  });
  s.setWallet(fakeWallet());
  const timer = fakeTimer();
  s.armIdle(1000, {
    now: () => 0,
    setTimer: timer.setTimer,
    clearTimer: timer.clearTimer,
  });
  assert.equal(timer.pending().length, 1);
  assert.equal(timer.pending()[0].ms, 1000);
  timer.fireAll();
  assert.equal(locked, 1);
  assert.equal(s.isUnlocked(), false);
});

test('touch() resets the idle timer (clears the old, arms a new one)', () => {
  const s = makeSession();
  let locked = 0;
  s.onLock(() => {
    locked += 1;
  });
  s.setWallet(fakeWallet());
  const timer = fakeTimer();
  s.armIdle(1000, {
    now: () => 0,
    setTimer: timer.setTimer,
    clearTimer: timer.clearTimer,
  });
  const firstId = timer.pending();
  assert.equal(firstId.length, 1);
  // touch clears the prior timer and arms a fresh one — still exactly one.
  s.touch();
  assert.equal(timer.pending().length, 1);
  // The fresh timer fires the lock.
  timer.fireAll();
  assert.equal(locked, 1);
});

test('touch() before arming is a harmless no-op', () => {
  const s = makeSession();
  assert.doesNotThrow(() => s.touch());
});

test('re-unlock re-arms the idle timer after a prior auto-lock', () => {
  // Regression: idle auto-lock must hold across lock -> re-unlock. Arm, let it
  // fire (auto-lock), then setWallet again (a re-unlock) must re-arm exactly
  // one fresh timer that auto-locks again.
  const s = makeSession();
  let locked = 0;
  s.onLock(() => {
    locked += 1;
  });
  s.setWallet(fakeWallet());
  const timer = fakeTimer();
  s.armIdle(1000, {
    now: () => 0,
    setTimer: timer.setTimer,
    clearTimer: timer.clearTimer,
  });
  timer.fireAll(); // idle elapses -> auto-lock
  assert.equal(locked, 1);
  assert.equal(s.isUnlocked(), false);
  assert.equal(timer.pending().length, 0);

  // Re-unlock: setWallet must re-arm the (still-configured) idle timer.
  s.setWallet(fakeWallet());
  assert.equal(s.isUnlocked(), true);
  assert.equal(timer.pending().length, 1);
  timer.fireAll();
  assert.equal(locked, 2); // idle auto-lock fired again
  assert.equal(s.isUnlocked(), false);
});

// --- installAutoLock: wire fake document/window events -------------------

function fakeDom() {
  const docHandlers = {};
  const winHandlers = {};
  return {
    document: {
      visibilityState: 'visible',
      addEventListener(type, cb) {
        docHandlers[type] = cb;
      },
    },
    window: {
      addEventListener(type, cb) {
        winHandlers[type] = cb;
      },
    },
    docHandlers,
    winHandlers,
  };
}

test('installAutoLock locks when the page becomes hidden', () => {
  const s = makeSession();
  let locked = 0;
  s.onLock(() => {
    locked += 1;
  });
  s.setWallet(fakeWallet());
  const dom = fakeDom();
  const timer = fakeTimer();
  s.installAutoLock({
    document: dom.document,
    window: dom.window,
    idleMs: 1000,
    now: () => 0,
    setTimer: timer.setTimer,
    clearTimer: timer.clearTimer,
  });
  // Visible visibilitychange does not lock.
  dom.document.visibilityState = 'visible';
  dom.docHandlers.visibilitychange();
  assert.equal(locked, 0);
  // Hidden visibilitychange locks.
  dom.document.visibilityState = 'hidden';
  dom.docHandlers.visibilitychange();
  assert.equal(locked, 1);
  assert.equal(s.isUnlocked(), false);
});

test('installAutoLock locks on pagehide', () => {
  const s = makeSession();
  let locked = 0;
  s.onLock(() => {
    locked += 1;
  });
  s.setWallet(fakeWallet());
  const dom = fakeDom();
  const timer = fakeTimer();
  s.installAutoLock({
    document: dom.document,
    window: dom.window,
    idleMs: 1000,
    now: () => 0,
    setTimer: timer.setTimer,
    clearTimer: timer.clearTimer,
  });
  dom.winHandlers.pagehide();
  assert.equal(locked, 1);
});

test('installAutoLock arms the idle timer and touch resets it on activity', () => {
  const s = makeSession();
  let locked = 0;
  s.onLock(() => {
    locked += 1;
  });
  s.setWallet(fakeWallet());
  const dom = fakeDom();
  const timer = fakeTimer();
  s.installAutoLock({
    document: dom.document,
    window: dom.window,
    idleMs: 1000,
    now: () => 0,
    setTimer: timer.setTimer,
    clearTimer: timer.clearTimer,
    activityEvents: ['pointerdown', 'keydown'],
  });
  // An idle timer is armed up front.
  assert.equal(timer.pending().length, 1);
  // An activity event touches (resets) the timer — still exactly one pending.
  dom.winHandlers.pointerdown();
  assert.equal(timer.pending().length, 1);
  // Eventually the idle timer fires → lock.
  timer.fireAll();
  assert.equal(locked, 1);
});
