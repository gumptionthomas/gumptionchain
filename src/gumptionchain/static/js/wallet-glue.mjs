// Glue for the /wallet management page. All key work is client-side: the
// passphrase and private key never leave the browser, are never logged, and
// are never written to the DOM/result text. The ONLY things persisted are the
// gc-keyring ciphertext record (IndexedDB) and a small per-origin trust-ack
// flag (localStorage). Backup output is the encrypted blob (safe to download).
//
// The pure helpers (whichControls / backupFilename / readTrustAck /
// writeTrustAck / makePasskey) are exported and DOM-free so they can be
// unit-tested with fakes. The DOM wiring is in init().
import { Wallet } from '../wallet/gc-wallet.mjs';
import * as keyring from '../wallet/gc-keyring.mjs';
import { makeIdbStore } from '../wallet/gc-store-idb.mjs';
import { exportEncrypted, importEncrypted } from '../wallet/gc-backup.mjs';
import { session as defaultSession } from './wallet-session.mjs';
import { makePasskey } from './wallet-passkey.mjs';

// Re-exported so existing importers (and tests) of wallet-glue keep working;
// the implementation now lives in the shared wallet-passkey module so /transact
// can reuse the same secure-context gating.
export { makePasskey };

// --- pure helpers ---------------------------------------------------------

// Given the observable page state, decide which sections/buttons show. This is
// the single source of truth for the state-driven UI, so it's unit-tested for
// the key states and the passkey secure-context gating.
export function whichControls({
  hasWallet,
  unlocked,
  secureContext,
  passkeySupported,
}) {
  const passkeyOk = !!secureContext && !!passkeySupported;
  return {
    // No-wallet section.
    showCreate: !hasWallet,
    showImport: !hasWallet,
    // Has-wallet section.
    showHasWallet: !!hasWallet,
    showUnlock: !!hasWallet && !unlocked,
    showUnlockPasskey: !!hasWallet && !unlocked && passkeyOk,
    showLock: !!hasWallet && !!unlocked,
    // Add-passkey is an unlocked-only action (it re-wraps the live DEK).
    showAddPasskey: !!hasWallet && !!unlocked && passkeyOk,
    showBackup: !!hasWallet,
    showForget: !!hasWallet,
  };
}

// A stable, address-tagged filename for the downloaded encrypted backup.
export function backupFilename(address) {
  const slug = (address || 'wallet').replace(/[^A-Za-z0-9]/g, '').slice(0, 12);
  return `gc-wallet-backup-${slug || 'wallet'}.json`;
}

// Per-origin trust acknowledgment: localStorage is origin-scoped, so this flag
// is naturally per-origin. Stored as '1' once acknowledged. read/write tolerate
// a missing or throwing storage (private mode / blocked storage) by failing to
// "not acknowledged" rather than throwing.
export const TRUST_ACK_KEY = 'gc-wallet-trust-ack-v1';

export function readTrustAck(storage) {
  try {
    return storage?.getItem(TRUST_ACK_KEY) === '1';
  } catch {
    return false;
  }
}

export function writeTrustAck(storage) {
  try {
    storage?.setItem(TRUST_ACK_KEY, '1');
  } catch {
    // best-effort; a blocked storage just means the ack isn't remembered.
  }
}

// --- DOM wiring -----------------------------------------------------------

function setStatus(el, text, kind = 'info') {
  if (!el) return;
  el.textContent = text;
  el.dataset.kind = kind;
}

function msgOf(e) {
  return e instanceof Error ? e.message : String(e);
}

// Trigger a client-side download of a text blob (the encrypted backup).
function downloadText(doc, filename, text) {
  const blob = new Blob([text], { type: 'application/json' });
  const url = URL.createObjectURL(blob);
  const a = doc.createElement('a');
  a.href = url;
  a.download = filename;
  doc.body.appendChild(a);
  a.click();
  doc.body.removeChild(a);
  URL.revokeObjectURL(url);
}

// init wires the page. root defaults to document. rpName labels the passkey
// (WebAuthn RP name). store/session/win are injectable for completeness but
// default to the real IndexedDB store / shared session / window.
export function init(
  root = document,
  {
    rpName = 'GumptionChain',
    store = makeIdbStore({}),
    session = defaultSession,
    win = typeof window !== 'undefined' ? window : undefined,
    doc = typeof document !== 'undefined' ? document : undefined,
    storage = typeof localStorage !== 'undefined' ? localStorage : undefined,
  } = {},
) {
  const $ = (sel) => root.querySelector(sel);

  // Section/control elements (any may be absent in a partial DOM).
  const els = {
    noWallet: $('#no-wallet'),
    hasWallet: $('#has-wallet'),
    addressOut: $('#wallet-address'),
    // create
    createPassphrase: $('#create-passphrase'),
    createBtn: $('#create-btn'),
    createStatus: $('#create-status'),
    // import
    importB58: $('#import-b58'),
    importPem: $('#import-pem'),
    importPassphrase: $('#import-passphrase'),
    importBtn: $('#import-btn'),
    importStatus: $('#import-status'),
    importBackupFile: $('#import-backup-file'),
    importBackupPassphrase: $('#import-backup-passphrase'),
    importBackupBtn: $('#import-backup-btn'),
    // trust ack (gates the first persist on this origin)
    trustAck: $('#trust-ack'),
    // unlock
    unlockSection: $('#unlock-section'),
    unlockPassphrase: $('#unlock-passphrase'),
    unlockBtn: $('#unlock-btn'),
    unlockPasskeyBtn: $('#unlock-passkey-btn'),
    unlockStatus: $('#unlock-status'),
    // unlocked actions
    lockBtn: $('#lock-btn'),
    addPasskeySection: $('#add-passkey-section'),
    addPasskeyBtn: $('#add-passkey-btn'),
    addPasskeyPassphrase: $('#add-passkey-passphrase'),
    addPasskeyStatus: $('#add-passkey-status'),
    // backup
    backupPassphrase: $('#backup-passphrase'),
    backupBtn: $('#backup-btn'),
    backupStatus: $('#backup-status'),
    // forget
    forgetBtn: $('#forget-btn'),
    forgetStatus: $('#forget-status'),
  };

  // Cached passkey capability (resolved once). Drives control visibility.
  let passkeyState = { secureContext: !!win?.isSecureContext, supported: false };
  let passkey = null;

  function show(el, visible) {
    if (el) el.hidden = !visible;
  }

  // Re-render which controls are visible from the current state.
  async function render() {
    const hasWallet = await keyring.hasWallet(store);
    const unlocked = session.isUnlocked();
    const c = whichControls({
      hasWallet,
      unlocked,
      secureContext: passkeyState.secureContext,
      passkeySupported: passkeyState.supported,
    });
    show(els.noWallet, c.showCreate || c.showImport);
    show(els.hasWallet, c.showHasWallet);
    show(els.unlockSection, c.showUnlock);
    show(els.unlockPasskeyBtn, c.showUnlockPasskey);
    show(els.lockBtn, c.showLock);
    show(els.addPasskeySection, c.showAddPasskey);
    show(els.addPasskeyBtn, c.showAddPasskey);
    show(els.addPasskeyPassphrase, c.showAddPasskey);
    show(els.backupBtn, c.showBackup);
    if (hasWallet && els.addressOut) {
      const rec = await store.get();
      els.addressOut.textContent = rec?.address ?? '';
    }
  }

  // Clear any passphrase inputs so a secret never lingers in the DOM.
  function clearSecrets() {
    for (const el of root.querySelectorAll('input[type="password"]')) {
      el.value = '';
    }
  }

  // The first persist on this origin is gated on the trust acknowledgment.
  // Returns true if persistence may proceed; otherwise surfaces a message.
  function trustGateOk(statusEl) {
    if (readTrustAck(storage)) {
      return true;
    }
    if (els.trustAck && els.trustAck.checked) {
      writeTrustAck(storage);
      return true;
    }
    setStatus(
      statusEl,
      'Acknowledge the trust note first: persist only on a node you trust.',
      'error',
    );
    return false;
  }

  // --- create ---
  if (els.createBtn) {
    els.createBtn.addEventListener('click', async () => {
      try {
        const passphrase = els.createPassphrase
          ? els.createPassphrase.value
          : '';
        if (!passphrase) {
          setStatus(els.createStatus, 'Set a passphrase first.', 'error');
          return;
        }
        if (!trustGateOk(els.createStatus)) return;
        const wallet = await Wallet.generate();
        await keyring.enroll(wallet, { store }, { passphrase });
        const address = await wallet.address();
        clearSecrets();
        setStatus(
          els.createStatus,
          `Wallet created and saved on this node: ${address}. ` +
            'Download an encrypted backup now — it is your only recovery.',
          'ok',
        );
        await render();
      } catch (e) {
        setStatus(els.createStatus, `Could not create: ${msgOf(e)}`, 'error');
      }
    });
  }

  // --- import (b58) ---
  if (els.importBtn) {
    els.importBtn.addEventListener('click', async () => {
      try {
        const b58 = els.importB58 ? els.importB58.value.trim() : '';
        const passphrase = els.importPassphrase
          ? els.importPassphrase.value
          : '';
        if (!b58) {
          setStatus(els.importStatus, 'Paste a base58 private key.', 'error');
          return;
        }
        if (!passphrase) {
          setStatus(
            els.importStatus,
            'Set a passphrase to persist the wallet.',
            'error',
          );
          return;
        }
        if (!trustGateOk(els.importStatus)) return;
        const wallet = await Wallet.fromPrivateKeyB58(b58);
        await keyring.enroll(wallet, { store }, { passphrase });
        const address = await wallet.address();
        if (els.importB58) els.importB58.value = '';
        clearSecrets();
        setStatus(
          els.importStatus,
          `Wallet imported and saved on this node: ${address}.`,
          'ok',
        );
        await render();
      } catch (e) {
        setStatus(els.importStatus, `Could not import: ${msgOf(e)}`, 'error');
      }
    });
  }
  // .pem import is deferred (mirrors /transact): surface clearly.
  if (els.importPem) {
    els.importPem.addEventListener('change', () => {
      setStatus(
        els.importStatus,
        'PEM upload is not supported yet — paste the base58 private key ' +
          'instead (a follow-up will add .pem import).',
        'error',
      );
      els.importPem.value = '';
    });
  }

  // --- import from an encrypted backup ---
  if (els.importBackupBtn) {
    els.importBackupBtn.addEventListener('click', async () => {
      try {
        const file =
          els.importBackupFile && els.importBackupFile.files
            ? els.importBackupFile.files[0]
            : null;
        const passphrase = els.importBackupPassphrase
          ? els.importBackupPassphrase.value
          : '';
        if (!file) {
          setStatus(els.importStatus, 'Choose a backup file.', 'error');
          return;
        }
        if (!passphrase) {
          setStatus(els.importStatus, 'Enter the backup passphrase.', 'error');
          return;
        }
        if (!trustGateOk(els.importStatus)) return;
        const backup = JSON.parse(await file.text());
        const wallet = await importEncrypted(backup, passphrase);
        // Persist under the SAME passphrase the backup used (the user has it).
        await keyring.enroll(wallet, { store }, { passphrase });
        const address = await wallet.address();
        clearSecrets();
        setStatus(
          els.importStatus,
          `Wallet restored from backup and saved: ${address}.`,
          'ok',
        );
        await render();
      } catch (e) {
        setStatus(
          els.importStatus,
          `Could not restore backup: ${msgOf(e)}`,
          'error',
        );
      }
    });
  }

  // --- unlock (passphrase) ---
  if (els.unlockBtn) {
    els.unlockBtn.addEventListener('click', async () => {
      try {
        const passphrase = els.unlockPassphrase
          ? els.unlockPassphrase.value
          : '';
        if (!passphrase) {
          setStatus(els.unlockStatus, 'Enter your passphrase.', 'error');
          return;
        }
        const wallet = await keyring.unlock({ store }, { passphrase });
        session.setWallet(wallet);
        clearSecrets();
        setStatus(
          els.unlockStatus,
          'Unlocked for this page session. It auto-locks on idle or when ' +
            'you leave.',
          'ok',
        );
        await render();
      } catch (e) {
        setStatus(
          els.unlockStatus,
          'Could not unlock (wrong passphrase?).',
          'error',
        );
      }
    });
  }

  // --- unlock (passkey) ---
  if (els.unlockPasskeyBtn) {
    els.unlockPasskeyBtn.addEventListener('click', async () => {
      try {
        if (!passkey) {
          setStatus(
            els.unlockStatus,
            'Passkeys are not available here.',
            'error',
          );
          return;
        }
        const wallet = await keyring.unlock({ store, passkey }, {});
        session.setWallet(wallet);
        setStatus(
          els.unlockStatus,
          'Unlocked with a passkey for this page session.',
          'ok',
        );
        await render();
      } catch (e) {
        setStatus(
          els.unlockStatus,
          `Could not unlock with a passkey: ${msgOf(e)}`,
          'error',
        );
      }
    });
  }

  // --- lock ---
  if (els.lockBtn) {
    els.lockBtn.addEventListener('click', async () => {
      session.lock();
      setStatus(els.unlockStatus, 'Locked.', 'info');
      await render();
    });
  }

  // --- add passkey (unlocked, secure-origin only) ---
  if (els.addPasskeyBtn) {
    els.addPasskeyBtn.addEventListener('click', async () => {
      try {
        if (!passkey) {
          setStatus(
            els.addPasskeyStatus,
            'Passkeys are not available here.',
            'error',
          );
          return;
        }
        const passphrase = els.addPasskeyPassphrase
          ? els.addPasskeyPassphrase.value
          : '';
        if (!passphrase) {
          setStatus(
            els.addPasskeyStatus,
            'Confirm your passphrase to add a passkey.',
            'error',
          );
          return;
        }
        const rec = await store.get();
        await keyring.addPasskey({ store, passkey }, { passphrase }, {
          userId: rec?.address,
          userName: rec?.address,
        });
        clearSecrets();
        setStatus(
          els.addPasskeyStatus,
          'Passkey added — you can now unlock with it on this device.',
          'ok',
        );
        await render();
      } catch (e) {
        setStatus(
          els.addPasskeyStatus,
          `Could not add a passkey: ${msgOf(e)}`,
          'error',
        );
      }
    });
  }

  // --- backup (download encrypted JSON) ---
  if (els.backupBtn) {
    els.backupBtn.addEventListener('click', async () => {
      try {
        const passphrase = els.backupPassphrase
          ? els.backupPassphrase.value
          : '';
        if (!passphrase) {
          setStatus(els.backupStatus, 'Enter your passphrase.', 'error');
          return;
        }
        // Unlock just to re-export — the passphrase decrypts the keyring.
        const wallet = await keyring.unlock({ store }, { passphrase });
        const backup = await exportEncrypted(wallet, passphrase);
        const address = await wallet.address();
        if (doc) {
          downloadText(
            doc,
            backupFilename(address),
            JSON.stringify(backup, null, 2),
          );
        }
        clearSecrets();
        setStatus(
          els.backupStatus,
          'Encrypted backup downloaded. Keep it (and the passphrase) safe.',
          'ok',
        );
      } catch (e) {
        setStatus(
          els.backupStatus,
          'Could not back up (wrong passphrase?).',
          'error',
        );
      }
    });
  }

  // --- forget (with confirm) ---
  if (els.forgetBtn) {
    els.forgetBtn.addEventListener('click', async () => {
      const ok = win
        ? win.confirm(
            'Forget this wallet on this node? This deletes the saved ' +
              'encrypted record. If you have no backup and no passphrase ' +
              'elsewhere, the wallet is unrecoverable.',
          )
        : true;
      if (!ok) return;
      try {
        session.lock();
        await keyring.clear(store);
        setStatus(els.forgetStatus, 'Wallet forgotten on this node.', 'info');
        await render();
      } catch (e) {
        setStatus(
          els.forgetStatus,
          `Could not forget: ${msgOf(e)}`,
          'error',
        );
      }
    });
  }

  // Resolve passkey capability, install auto-lock, then render. Auto-lock
  // re-renders on lock so the controls reflect the locked state.
  (async () => {
    passkey = await makePasskey({ window: win, rpName });
    passkeyState = {
      secureContext: !!win?.isSecureContext,
      supported: passkey != null,
    };
    session.onLock(() => {
      render().catch(() => {});
    });
    if (doc && win) {
      session.installAutoLock({ document: doc, window: win });
    }
    await render();
  })().catch(() => {});
}
