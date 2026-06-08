import { test } from 'node:test';
import assert from 'node:assert/strict';
import {
  whichControls,
  backupFilename,
  TRUST_ACK_KEY,
  readTrustAck,
  writeTrustAck,
} from './wallet-glue.mjs';

// --- whichControls: state -> which sections/buttons are visible ----------

test('no wallet: create/import shown, has-wallet controls hidden', () => {
  const c = whichControls({
    hasWallet: false,
    unlocked: false,
    secureContext: true,
    passkeySupported: true,
  });
  assert.equal(c.showCreate, true);
  assert.equal(c.showImport, true);
  assert.equal(c.showHasWallet, false);
  assert.equal(c.showUnlock, false);
  assert.equal(c.showLock, false);
  assert.equal(c.showAddPasskey, false);
  assert.equal(c.showBackup, false);
  assert.equal(c.showForget, false);
});

test('has wallet, locked: unlock/backup/forget shown, lock hidden', () => {
  const c = whichControls({
    hasWallet: true,
    unlocked: false,
    secureContext: true,
    passkeySupported: true,
  });
  assert.equal(c.showCreate, false);
  assert.equal(c.showImport, false);
  assert.equal(c.showHasWallet, true);
  assert.equal(c.showUnlock, true);
  assert.equal(c.showLock, false);
  assert.equal(c.showBackup, true);
  assert.equal(c.showForget, true);
  // Passkey unlock button shown only when supported + secure.
  assert.equal(c.showUnlockPasskey, true);
  // Add-passkey is an unlocked-only action.
  assert.equal(c.showAddPasskey, false);
});

test('has wallet, unlocked: lock + add-passkey shown, unlock hidden', () => {
  const c = whichControls({
    hasWallet: true,
    unlocked: true,
    secureContext: true,
    passkeySupported: true,
  });
  assert.equal(c.showHasWallet, true);
  assert.equal(c.showUnlock, false);
  assert.equal(c.showLock, true);
  assert.equal(c.showAddPasskey, true);
  assert.equal(c.showBackup, true);
  assert.equal(c.showForget, true);
});

test('non-secure origin: every passkey control is hidden', () => {
  const locked = whichControls({
    hasWallet: true,
    unlocked: false,
    secureContext: false,
    passkeySupported: true,
  });
  assert.equal(locked.showUnlockPasskey, false);
  const unlocked = whichControls({
    hasWallet: true,
    unlocked: true,
    secureContext: false,
    passkeySupported: true,
  });
  assert.equal(unlocked.showAddPasskey, false);
});

test('passkey unsupported (even on secure origin): passkey controls hidden', () => {
  const unlocked = whichControls({
    hasWallet: true,
    unlocked: true,
    secureContext: true,
    passkeySupported: false,
  });
  assert.equal(unlocked.showAddPasskey, false);
  const locked = whichControls({
    hasWallet: true,
    unlocked: false,
    secureContext: true,
    passkeySupported: false,
  });
  assert.equal(locked.showUnlockPasskey, false);
});

// --- backupFilename ------------------------------------------------------

test('backupFilename embeds a short address slug and the .json ext', () => {
  const name = backupFilename('GCabcdef1234567890GC');
  assert.match(name, /^gc-wallet-backup-/);
  assert.match(name, /\.json$/);
  // It carries part of the address so multiple backups are distinguishable.
  assert.match(name, /GCabcdef/);
});

test('backupFilename tolerates a null/empty address', () => {
  const name = backupFilename('');
  assert.match(name, /^gc-wallet-backup/);
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
