import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  whichControls,
  backupFilename,
  TRUST_ACK_KEY,
  readTrustAck,
  writeTrustAck,
  init,
  UNSUPPORTED_MSG,
} from './signing-key-glue.mjs';
import { SigningKey } from '../sdk/gc-signing-key.mjs';
import { makeSession } from './signing-key-session.mjs';

// --- whichControls: state -> which sections/buttons are visible ----------

test('no signing_key: create/import shown, has-signing_key controls hidden', () => {
  const c = whichControls({
    hasSigningKey: false,
    unlocked: false,
    secureContext: true,
    passkeySupported: true,
  });
  assert.equal(c.showCreate, true);
  assert.equal(c.showImport, true);
  assert.equal(c.showHasSigningKey, false);
  assert.equal(c.showUnlock, false);
  assert.equal(c.showLock, false);
  assert.equal(c.showAddPasskey, false);
  assert.equal(c.showBackup, false);
  assert.equal(c.showForget, false);
});

test('has signing_key, locked: unlock/backup/forget shown, lock hidden', () => {
  const c = whichControls({
    hasSigningKey: true,
    unlocked: false,
    secureContext: true,
    passkeySupported: true,
  });
  assert.equal(c.showCreate, false);
  assert.equal(c.showImport, false);
  assert.equal(c.showHasSigningKey, true);
  assert.equal(c.showUnlock, true);
  assert.equal(c.showLock, false);
  assert.equal(c.showBackup, true);
  assert.equal(c.showForget, true);
  // Passkey unlock button shown only when supported + secure.
  assert.equal(c.showUnlockPasskey, true);
  // Add-passkey is an unlocked-only action.
  assert.equal(c.showAddPasskey, false);
});

test('has signing_key, unlocked: lock + add-passkey shown, unlock hidden', () => {
  const c = whichControls({
    hasSigningKey: true,
    unlocked: true,
    secureContext: true,
    passkeySupported: true,
  });
  assert.equal(c.showHasSigningKey, true);
  assert.equal(c.showUnlock, false);
  assert.equal(c.showLock, true);
  assert.equal(c.showAddPasskey, true);
  assert.equal(c.showBackup, true);
  assert.equal(c.showForget, true);
});

test('non-secure origin: every passkey control is hidden', () => {
  const locked = whichControls({
    hasSigningKey: true,
    unlocked: false,
    secureContext: false,
    passkeySupported: true,
  });
  assert.equal(locked.showUnlockPasskey, false);
  const unlocked = whichControls({
    hasSigningKey: true,
    unlocked: true,
    secureContext: false,
    passkeySupported: true,
  });
  assert.equal(unlocked.showAddPasskey, false);
});

test('passkey unsupported (even on secure origin): passkey controls hidden', () => {
  const unlocked = whichControls({
    hasSigningKey: true,
    unlocked: true,
    secureContext: true,
    passkeySupported: false,
  });
  assert.equal(unlocked.showAddPasskey, false);
  const locked = whichControls({
    hasSigningKey: true,
    unlocked: false,
    secureContext: true,
    passkeySupported: false,
  });
  assert.equal(locked.showUnlockPasskey, false);
});

// --- backupFilename ------------------------------------------------------

test('backupFilename embeds a short address slug and the .json ext', () => {
  const name = backupFilename('GCabcdef1234567890GC');
  assert.match(name, /^gc-signing-key-backup-/);
  assert.match(name, /\.json$/);
  // It carries part of the address so multiple backups are distinguishable.
  assert.match(name, /GCabcdef/);
});

test('backupFilename tolerates a null/empty address', () => {
  const name = backupFilename('');
  assert.match(name, /^gc-signing-key-backup/);
  assert.match(name, /\.json$/);
});

// --- trust-ack flag (per-origin, localStorage) ---------------------------

function fakeStorage() {
  const m = new Map();
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, String(v)),
    removeItem: (k) => m.delete(k),
  };
}

test('trust-ack defaults to false and flips to true once written', () => {
  const store = fakeStorage();
  assert.equal(readTrustAck(store), false);
  writeTrustAck(store);
  assert.equal(readTrustAck(store), true);
  // The flag is stored under the documented per-origin key.
  assert.equal(store.getItem(TRUST_ACK_KEY), '1');
});

test('readTrustAck tolerates a missing/throwing storage', () => {
  assert.equal(readTrustAck(null), false);
  assert.equal(readTrustAck(undefined), false);
  const boom = {
    getItem() {
      throw new Error('blocked');
    },
  };
  assert.equal(readTrustAck(boom), false);
});

test('writeTrustAck tolerates a missing/throwing storage', () => {
  assert.doesNotThrow(() => writeTrustAck(null));
  const boom = {
    setItem() {
      throw new Error('blocked');
    },
  };
  assert.doesNotThrow(() => writeTrustAck(boom));
});

// --- import (gcsec secret) via init (#B5) --------------------------------
// The served /signing-key glue must drive the REAL SigningKey.fromSecret when
// a gcsec1… secret is pasted into #import-secret. This is the regression guard
// that catches a revert to the removed fromPrivateKeyB58 (which would throw).

// Minimal DOM stand-in: querySelector caches a fake element per selector;
// elements record click handlers so the test can trigger them. querySelectorAll
// returns [] (no password inputs to clear in this stub).
function fakeElement() {
  const handlers = {};
  return {
    value: '',
    hidden: false,
    checked: false,
    textContent: '',
    dataset: {},
    files: null,
    addEventListener(type, fn) {
      (handlers[type] ??= []).push(fn);
    },
    async click() {
      for (const fn of handlers.click ?? []) await fn();
    },
  };
}

function fakeRoot() {
  const nodes = {};
  return {
    querySelector(sel) {
      return (nodes[sel] ??= fakeElement());
    },
    querySelectorAll() {
      return [];
    },
  };
}

function memStorage(seed = {}) {
  const m = new Map(Object.entries(seed));
  return {
    getItem: (k) => (m.has(k) ? m.get(k) : null),
    setItem: (k, v) => m.set(k, String(v)),
    removeItem: (k) => m.delete(k),
  };
}

test('init: pasting a gcsec1… secret enrolls the key at the matching address', async () => {
  const w = await SigningKey.generate();
  const secret = await w.exportSecret();

  const root = fakeRoot();
  const session = makeSession();
  // In-memory single-record store (what keyring.enroll persists into).
  let record = null;
  const store = {
    get: async () => record,
    put: async (rec) => {
      record = rec;
    },
    delete: async () => {
      record = null;
    },
  };
  // Trust-ack already granted on this origin so the first persist proceeds.
  const storage = memStorage({ [TRUST_ACK_KEY]: '1' });

  // win/doc omitted -> no auto-lock, makePasskey -> null.
  init(root, { store, session, storage });

  root.querySelector('#import-secret').value = secret;
  root.querySelector('#import-passphrase').value = 'correct horse battery';
  await root.querySelector('#import-btn').click();

  // The real SigningKey.fromSecret + keyring.enroll ran via the glue: a
  // ciphertext record was persisted at the source key's address.
  assert.notEqual(record, null);
  assert.equal(record.address, await w.address());
  // The status reports success (the address), not an error.
  assert.match(
    root.querySelector('#import-status').textContent,
    /imported and saved/i,
  );
  // The secret textarea was cleared after a successful import.
  assert.equal(root.querySelector('#import-secret').value, '');
});

test('init: a blank gcsec secret reports the gcsec prompt, enrolls nothing', async () => {
  const root = fakeRoot();
  const session = makeSession();
  let record = null;
  const store = {
    get: async () => record,
    put: async (rec) => {
      record = rec;
    },
    delete: async () => {
      record = null;
    },
  };
  const storage = memStorage({ [TRUST_ACK_KEY]: '1' });
  init(root, { store, session, storage });

  root.querySelector('#import-secret').value = '';
  root.querySelector('#import-passphrase').value = 'pw';
  await root.querySelector('#import-btn').click();

  assert.equal(record, null);
  assert.match(root.querySelector('#import-status').textContent, /gcsec1…/);
});

// --- graceful degradation: an Ed25519-unsupported browser must show the
// friendly "update your browser" message INSTEAD of the SDK's opaque
// NotSupportedError, and must NOT reach SDK keygen/import (nothing enrolled).
// We simulate the unsupported browser by stubbing SigningKey.isSupported ->
// false for the test's duration. A regression that drops the guard would let
// real keygen/import run (enrolling a record or surfacing the opaque throw),
// failing these assertions.

test('init: keygen on an Ed25519-unsupported browser shows the update message', async () => {
  const orig = SigningKey.isSupported;
  const origGenerate = SigningKey.generate;
  let generateCalled = false;
  SigningKey.isSupported = async () => false;
  SigningKey.generate = async () => {
    generateCalled = true;
    return origGenerate.call(SigningKey);
  };
  try {
    const root = fakeRoot();
    const session = makeSession();
    let record = null;
    const store = {
      get: async () => record,
      put: async (rec) => {
        record = rec;
      },
      delete: async () => {
        record = null;
      },
    };
    // Trust-ack already granted so the create handler reaches the guard.
    const storage = memStorage({ [TRUST_ACK_KEY]: '1' });
    init(root, { store, session, storage });

    root.querySelector('#create-passphrase').value = 'correct horse battery';
    await root.querySelector('#create-btn').click();

    assert.equal(root.querySelector('#create-status').textContent, UNSUPPORTED_MSG);
    assert.equal(generateCalled, false);
    assert.equal(record, null);
  } finally {
    SigningKey.isSupported = orig;
    SigningKey.generate = origGenerate;
  }
});

test('init: gcsec import on an Ed25519-unsupported browser shows the update message', async () => {
  const orig = SigningKey.isSupported;
  const origFromSecret = SigningKey.fromSecret;
  let fromSecretCalled = false;
  SigningKey.isSupported = async () => false;
  SigningKey.fromSecret = async (s) => {
    fromSecretCalled = true;
    return origFromSecret.call(SigningKey, s);
  };
  try {
    const root = fakeRoot();
    const session = makeSession();
    let record = null;
    const store = {
      get: async () => record,
      put: async (rec) => {
        record = rec;
      },
      delete: async () => {
        record = null;
      },
    };
    const storage = memStorage({ [TRUST_ACK_KEY]: '1' });
    init(root, { store, session, storage });

    root.querySelector('#import-secret').value = 'gcsec1anything';
    root.querySelector('#import-passphrase').value = 'correct horse battery';
    await root.querySelector('#import-btn').click();

    assert.equal(root.querySelector('#import-status').textContent, UNSUPPORTED_MSG);
    assert.equal(fromSecretCalled, false);
    assert.equal(record, null);
  } finally {
    SigningKey.isSupported = orig;
    SigningKey.fromSecret = origFromSecret;
  }
});

test('init: backup restore on an Ed25519-unsupported browser shows the update message', async () => {
  const orig = SigningKey.isSupported;
  SigningKey.isSupported = async () => false;
  let fileRead = false;
  try {
    const root = fakeRoot();
    const session = makeSession();
    let record = null;
    const store = {
      get: async () => record,
      put: async (rec) => {
        record = rec;
      },
      delete: async () => {
        record = null;
      },
    };
    const storage = memStorage({ [TRUST_ACK_KEY]: '1' });
    init(root, { store, session, storage });

    // The guard runs before the backup file is read, so a tripwire on
    // file.text() proves it short-circuited (no opaque Ed25519 throw).
    root.querySelector('#import-backup-file').files = [
      {
        text: async () => {
          fileRead = true;
          return '{}';
        },
      },
    ];
    root.querySelector('#import-backup-passphrase').value = 'correct horse battery';
    await root.querySelector('#import-backup-btn').click();

    assert.equal(
      root.querySelector('#import-backup-status').textContent,
      UNSUPPORTED_MSG,
    );
    assert.equal(fileRead, false);
    assert.equal(record, null);
  } finally {
    SigningKey.isSupported = orig;
  }
});
