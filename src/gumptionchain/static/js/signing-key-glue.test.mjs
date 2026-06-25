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
import {
  createPathFor,
  recognitionOutcome,
  classifyRecognition,
  resolveRpId,
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

test('init: pasting a 24-word recovery phrase enrolls the key at the phrase address', async () => {
  const w = await SigningKey.generate();
  const phrase = await w.mnemonic();

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

  root.querySelector('#import-secret').value = phrase;
  root.querySelector('#import-passphrase').value = 'correct horse battery';
  await root.querySelector('#import-btn').click();

  // The phrase routed through SigningKey.fromMnemonic + keyring.enroll: a
  // ciphertext record was persisted at the phrase's key address (wrap-bound
  // under the device passphrase — the cross-ecosystem migration path).
  assert.notEqual(record, null);
  assert.equal(record.address, await w.address());
  assert.match(
    root.querySelector('#import-status').textContent,
    /imported and saved/i,
  );
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

// --- derive create + recognize phantom guard (#330) ----------------------

test('createPathFor picks derive when passkeys are supported, else wrap', () => {
  assert.equal(createPathFor({ passkeySupported: true }), 'derive');
  assert.equal(createPathFor({ passkeySupported: false }), 'wrap');
});

test('recognitionOutcome maps the recognize verdict to a hub-style action', () => {
  assert.equal(recognitionOutcome({ recognized: true, kind: 'derived' }), 'rehydrated');
  assert.equal(recognitionOutcome({ recognized: true, kind: 'wrap' }), 'restore');
  assert.equal(recognitionOutcome({ recognized: false }), 'none');
  assert.equal(recognitionOutcome(null), 'none');
});

test('classifyRecognition is the phantom guard: wrap iff userHandle is a real address != D', async () => {
  const { SigningKey } = await import('../sdk/gc-signing-key.mjs');
  const D = await (await SigningKey.generate()).address();
  const other = await (await SigningKey.generate()).address();
  assert.equal(classifyRecognition({ userHandle: 'not-an-address', derivedAddress: D }), 'derived');
  assert.equal(classifyRecognition({ userHandle: null, derivedAddress: D }), 'derived');
  assert.equal(classifyRecognition({ userHandle: D, derivedAddress: D }), 'derived');
  assert.equal(classifyRecognition({ userHandle: other, derivedAddress: D }), 'wrap');
});

test('whichControls shows derive + recognize only when a passkey is usable and no key', () => {
  const keyless = whichControls({ hasSigningKey: false, unlocked: false, secureContext: true, passkeySupported: true });
  assert.equal(keyless.showCreateDerive, true);
  assert.equal(keyless.showRecognize, true);
  const noPasskey = whichControls({ hasSigningKey: false, unlocked: false, secureContext: false, passkeySupported: false });
  assert.equal(noPasskey.showCreateDerive, false);
  assert.equal(noPasskey.showRecognize, false);
  assert.equal(noPasskey.showCreate, true);
  const has = whichControls({ hasSigningKey: true, unlocked: false, secureContext: true, passkeySupported: true });
  assert.equal(has.showCreateDerive, false);
  assert.equal(has.showRecognize, false);
});

// --- derive-create / recognize / kind-aware backup via init (#330) --------
// These exercise the DOM wiring added to init(). They reuse the fakeElement /
// memStorage helpers above, but extend the fake DOM with a getElementById()
// (the recovery-phrase partial addresses its nodes by bare id) and a
// createElement() so recoveryPhrase.init can render the words into the fake
// #rp-words container. The same fake is passed as both `root` and `doc`.

// A richer fake DOM: querySelector('#x') and getElementById('x') resolve to the
// SAME cached node, so the glue ($('#rp-words')) and the recovery-phrase partial
// ($('rp-words')) see one element. createElement returns append-able stubs.
function fakeDom() {
  const nodes = {};
  const node = (key) => (nodes[key] ??= fakeElement());
  const makeNode = () => {
    const el = fakeElement();
    el.className = '';
    el.append = () => {};
    el.replaceChildren = () => {};
    return el;
  };
  return {
    querySelector(sel) {
      return node(sel.startsWith('#') ? sel.slice(1) : sel);
    },
    getElementById(id) {
      return node(id);
    },
    querySelectorAll() {
      return [];
    },
    createElement() {
      return makeNode();
    },
    get body() {
      return node('body');
    },
  };
}

function memStore(seed = null) {
  let record = seed;
  return {
    get: async () => record,
    put: async (rec) => {
      record = rec;
    },
    delete: async () => {
      record = null;
    },
  };
}

// A fake passkey matching the gc-passkey-webauthn surface the new flows need:
// enroll()->{credentialId, prfOutput}, discover()->{credentialId, prfOutput,
// userHandle}, isSupported()->true. prfOutput is a deterministic seed so the
// derived address is reproducible across enroll/discover.
function fakePasskey({ prfOutput, userHandle = null, credentialId = 'cred-1' } = {}) {
  const prf = prfOutput ?? new Uint8Array(32).fill(7);
  return {
    isSupported: () => true,
    enroll: async () => ({ credentialId, prfOutput: prf }),
    discover: async () => ({ credentialId, prfOutput: prf, userHandle }),
  };
}

test('init: derive-create persists a derived record (no ciphertext) + shows the recovery phrase', async () => {
  const dom = fakeDom();
  const session = makeSession();
  const store = memStore();
  const storage = memStorage({ [TRUST_ACK_KEY]: '1' });
  const prfOutput = new Uint8Array(32).fill(3);
  const passkey = fakePasskey({ prfOutput });

  init(dom, { store, session, storage, doc: dom, passkey });

  dom.querySelector('#create-derive-btn');
  await dom.querySelector('#create-derive-btn').click();

  const rec = await store.get();
  assert.notEqual(rec, null);
  assert.equal(rec.kind, 'derived');
  assert.equal(rec.version, 2);
  // The derived address matches the seed-derived key's address.
  const { deriveSigningKey } = await import('../sdk/gc-derive.mjs');
  const sk = await deriveSigningKey(prfOutput);
  assert.equal(rec.address, await sk.address());
  assert.equal(rec.credentialId, 'cred-1');
  // A derived record holds NO wrap ciphertext.
  assert.equal(rec.signing_key_ct, undefined);
  assert.equal(rec.wraps, undefined);
  // The live key was handed to the session and the recovery section revealed.
  assert.equal(session.isUnlocked(), true);
  assert.equal(dom.querySelector('#recovery-phrase-section').hidden, false);
});

test('init: derive-create is blocked by the trust gate (no record, error status)', async () => {
  const dom = fakeDom();
  const session = makeSession();
  const store = memStore();
  // No trust-ack written and the checkbox is unchecked -> gate fails.
  const storage = memStorage();
  const passkey = fakePasskey();

  init(dom, { store, session, storage, doc: dom, passkey });
  // Ensure the trust-ack checkbox is unchecked.
  dom.querySelector('#trust-ack').checked = false;
  await dom.querySelector('#create-derive-btn').click();

  assert.equal(await store.get(), null);
  assert.equal(session.isUnlocked(), false);
  assert.equal(dom.querySelector('#create-derive-status').dataset.kind, 'error');
});

test('init: recognize adopts a DERIVED passkey (random userHandle) into the session', async () => {
  const dom = fakeDom();
  const session = makeSession();
  const store = memStore();
  const storage = memStorage({ [TRUST_ACK_KEY]: '1' });
  const prfOutput = new Uint8Array(32).fill(9);
  // A non-address userHandle -> classifyRecognition returns 'derived' -> adopt.
  const passkey = fakePasskey({ prfOutput, userHandle: 'random-handle-xyz' });

  init(dom, { store, session, storage, doc: dom, passkey });
  await dom.querySelector('#recognize-btn').click();

  const { deriveSigningKey } = await import('../sdk/gc-derive.mjs');
  const expected = await (await deriveSigningKey(prfOutput)).address();
  const rec = await store.get();
  assert.notEqual(rec, null);
  assert.equal(rec.kind, 'derived');
  assert.equal(rec.address, expected);
  assert.equal(session.isUnlocked(), true);
  assert.match(dom.querySelector('#recognize-status').textContent, /Signed in as/);
});

test('init: recognize on a WRAP passkey (phantom guard) routes to restore, persists nothing', async () => {
  const dom = fakeDom();
  const session = makeSession();
  const store = memStore();
  const storage = memStorage({ [TRUST_ACK_KEY]: '1' });
  const prfOutput = new Uint8Array(32).fill(11);
  // A real gc address as userHandle that DIFFERS from the PRF-derived address:
  // this is a wrap identity's passkey discovered on a foreign device.
  const otherAddress = await (await SigningKey.generate()).address();
  const passkey = fakePasskey({ prfOutput, userHandle: otherAddress });

  init(dom, { store, session, storage, doc: dom, passkey });
  await dom.querySelector('#recognize-btn').click();

  assert.equal(await store.get(), null);
  assert.equal(session.isUnlocked(), false);
  assert.match(dom.querySelector('#recognize-status').textContent, /restore/i);
});

test('init: backup on a DERIVED record shows the recovery phrase (no download)', async () => {
  const dom = fakeDom();
  const session = makeSession();
  const prfOutput = new Uint8Array(32).fill(5);
  const { deriveSigningKey } = await import('../sdk/gc-derive.mjs');
  const derivedAddress = await (await deriveSigningKey(prfOutput)).address();
  const store = memStore({
    version: 2,
    kind: 'derived',
    address: derivedAddress,
    credentialId: 'cred-1',
  });
  const storage = memStorage({ [TRUST_ACK_KEY]: '1' });
  const passkey = fakePasskey({ prfOutput });

  // A doc whose createElement counts download attempts (there must be none).
  let anchorsCreated = 0;
  const baseCreate = dom.createElement.bind(dom);
  dom.createElement = (tag) => {
    if (tag === 'a') anchorsCreated += 1;
    return baseCreate(tag);
  };

  init(dom, { store, session, storage, doc: dom, passkey });
  await dom.querySelector('#backup-btn').click();

  // The derived branch reveals the recovery section and does NOT download.
  assert.equal(dom.querySelector('#recovery-phrase-section').hidden, false);
  assert.equal(anchorsCreated, 0);
});

test('whichControls is kind-aware: derived hides passphrase-unlock + add-passkey', () => {
  const locked = whichControls({ hasSigningKey: true, unlocked: false, secureContext: true, passkeySupported: true, kind: 'derived' });
  assert.equal(locked.showUnlock, true);
  assert.equal(locked.showUnlockPassphrase, false);
  assert.equal(locked.showUnlockPasskey, true);
  const unlocked = whichControls({ hasSigningKey: true, unlocked: true, secureContext: true, passkeySupported: true, kind: 'derived' });
  assert.equal(unlocked.showAddPasskey, false);
  assert.equal(unlocked.showBackup, true);
  const wrapLocked = whichControls({ hasSigningKey: true, unlocked: false, secureContext: true, passkeySupported: true, kind: 'wrap' });
  assert.equal(wrapLocked.showUnlockPassphrase, true);
  const wrapUnlocked = whichControls({ hasSigningKey: true, unlocked: true, secureContext: true, passkeySupported: true, kind: 'wrap' });
  assert.equal(wrapUnlocked.showAddPasskey, true);
});

// --- kind-aware management render + unlock via init (#338) ----------------
// A saved DERIVED identity gets passkey-unlock (no passphrase field), a
// recovery-phrase backup (relabeled), and NO add-passkey. A saved WRAP record
// keeps the passphrase-unlock + add-passkey affordances. These exercise the
// kind-threaded render() and the derived branch of #unlock-passkey-btn,
// reusing the fakeDom / memStore / memStorage / fakePasskey helpers above.

test('init: a saved DERIVED record renders passkey-unlock, no passphrase, no add-passkey', async () => {
  const dom = fakeDom();
  const session = makeSession();
  const prfOutput = new Uint8Array(32).fill(13);
  const { deriveSigningKey } = await import('../sdk/gc-derive.mjs');
  const D = await (await deriveSigningKey(prfOutput)).address();
  const store = memStore({ version: 2, kind: 'derived', address: D, credentialId: 'c' });
  const storage = memStorage({ [TRUST_ACK_KEY]: '1' });
  const passkey = fakePasskey({ prfOutput });

  init(dom, { store, session, storage, doc: dom, passkey });
  // Let the async init IIFE (passkey resolve + first render) settle.
  await new Promise((r) => setTimeout(r, 0));

  // The whole passphrase row (label + input) is hidden, not just the input —
  // no dangling "Passphrase" label; passkey-unlock is shown.
  assert.equal(dom.querySelector('#unlock-passphrase-row').hidden, true);
  assert.equal(dom.querySelector('#unlock-btn').hidden, true);
  assert.equal(dom.querySelector('#unlock-passkey-btn').hidden, false);
  // Add-passkey is hidden for a derived identity (wrap-only operation).
  assert.equal(dom.querySelector('#add-passkey-section').hidden, true);
  assert.equal(dom.querySelector('#add-passkey-btn').hidden, true);
  // The backup heading + button + description are relabeled for the phrase path.
  assert.equal(dom.querySelector('#backup-btn').textContent, 'Show recovery phrase');
  assert.equal(dom.querySelector('#backup-heading').textContent, 'Your recovery phrase');
  assert.match(dom.querySelector('#backup-desc').textContent, /recovery phrase/i);
  // No passphrase row (label + input) for a derived backup.
  assert.equal(dom.querySelector('#backup-passphrase-row').hidden, true);
});

test('init: derived passkey-unlock with the matching passkey unlocks the session', async () => {
  const dom = fakeDom();
  const session = makeSession();
  const prfOutput = new Uint8Array(32).fill(17);
  const { deriveSigningKey } = await import('../sdk/gc-derive.mjs');
  const D = await (await deriveSigningKey(prfOutput)).address();
  const store = memStore({ version: 2, kind: 'derived', address: D, credentialId: 'c' });
  const storage = memStorage({ [TRUST_ACK_KEY]: '1' });
  const passkey = fakePasskey({ prfOutput });

  init(dom, { store, session, storage, doc: dom, passkey });
  await new Promise((r) => setTimeout(r, 0));

  await dom.querySelector('#unlock-passkey-btn').click();

  assert.equal(session.isUnlocked(), true);
  assert.equal((await session.getSigningKey().address()), D);
  assert.equal(dom.querySelector('#unlock-status').dataset.kind, 'ok');
});

test('init: derived passkey-unlock with a MISMATCHED passkey does not unlock, errors', async () => {
  const dom = fakeDom();
  const session = makeSession();
  // Saved record is for the address derived from one PRF...
  const savedPrf = new Uint8Array(32).fill(19);
  const { deriveSigningKey } = await import('../sdk/gc-derive.mjs');
  const D = await (await deriveSigningKey(savedPrf)).address();
  const store = memStore({ version: 2, kind: 'derived', address: D, credentialId: 'c' });
  const storage = memStorage({ [TRUST_ACK_KEY]: '1' });
  // ...but the passkey on this device derives a DIFFERENT address.
  const otherPrf = new Uint8Array(32).fill(23);
  const passkey = fakePasskey({ prfOutput: otherPrf });

  init(dom, { store, session, storage, doc: dom, passkey });
  await new Promise((r) => setTimeout(r, 0));

  await dom.querySelector('#unlock-passkey-btn').click();

  assert.equal(session.isUnlocked(), false);
  assert.equal(dom.querySelector('#unlock-status').dataset.kind, 'error');
});

test('init: a saved WRAP record keeps passphrase-unlock + add-passkey', async () => {
  const dom = fakeDom();
  const session = makeSession();
  const store = memStore();
  const storage = memStorage({ [TRUST_ACK_KEY]: '1' });
  const passkey = fakePasskey();

  // Enroll a real wrap record (version-tagged ciphertext under a passphrase).
  const sk = await SigningKey.generate();
  const { enroll } = await import('../sdk/gc-keyring.mjs');
  await enroll(sk, { store }, { passphrase: 'correct horse battery' });

  init(dom, { store, session, storage, doc: dom, passkey });
  await new Promise((r) => setTimeout(r, 0));

  // Locked wrap: passphrase-unlock row + unlock button shown.
  assert.equal(dom.querySelector('#unlock-passphrase-row').hidden, false);
  assert.equal(dom.querySelector('#unlock-btn').hidden, false);
  // The backup keeps the encrypted-download labels + passphrase row.
  assert.equal(dom.querySelector('#backup-btn').textContent, 'Download backup');
  assert.equal(dom.querySelector('#backup-heading').textContent, 'Download an encrypted backup');
  assert.equal(dom.querySelector('#backup-passphrase-row').hidden, false);
});

// --- resolveRpId: explicit (server-configured) rpId wins, else hostname ----
test('resolveRpId returns an explicit rpId when provided', () => {
  const win = { location: { hostname: 'chain.example' } };
  assert.equal(resolveRpId({ window: win, rpId: 'gumption.com' }), 'gumption.com');
});

test('resolveRpId falls back to the origin hostname when rpId is empty/absent', () => {
  const win = { location: { hostname: 'chain.example' } };
  assert.equal(resolveRpId({ window: win }), 'chain.example');
  assert.equal(resolveRpId({ window: win, rpId: '' }), 'chain.example');
});
