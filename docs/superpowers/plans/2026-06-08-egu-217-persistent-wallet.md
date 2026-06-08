# EGU #217 persistent browser wallet (Tier 1.5) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A persisted, browser-resident wallet (IndexedDB, zero server state) unlockable by passphrase OR passkey, managed on a `/wallet` page and usable from `/transact`, with generation + encrypted backup. Ephemeral import stays the default; persistence is opt-in behind a trust acknowledgment.

**Architecture:** A DEK-wrapping keyring (`gc-keyring.mjs`) encrypts the wallet under a random data key (DEK); the DEK is wrapped separately by a passphrase-KEK (PBKDF2) and a passkey-KEK (WebAuthn-PRF→HKDF), so either method unlocks the same wallet. Persistence lives in IndexedDB (origin-scoped, single record); unlock is per-page-session with auto-lock.

**Tech Stack:** Web Crypto (AES-GCM, PBKDF2, HKDF, RSA), IndexedDB, vanilla ESM; Flask/Jinja/Bootstrap; pytest + `node --test`.

**Spec:** `docs/superpowers/specs/2026-06-08-egu-217-persistent-wallet-design.md`

**Primitives to reuse (READ FIRST):**
- `clients/wallet/gc-envelope.mjs`: `sealWithKey(aesKey, bytes) -> {iv, ciphertext}`, `openWithKey(aesKey, {iv, ciphertext}) -> bytes`, `deriveAesKey(prfOutput) -> AES-GCM key` (HKDF; currently NOT exported — export it), `seal/open` (PRF wrappers).
- `clients/wallet/gc-backup.mjs`: `deriveKey(passphrase, salt, iterations) -> AES-GCM key` (PBKDF2; currently a module-internal `async function` — export it), `exportEncrypted(wallet, passphrase)` / `importEncrypted(backup, passphrase)`, `SALT_BYTES`/default `iterations`.
- `clients/wallet/gc-store-idb.mjs`: `makeIdbStore({dbName}) -> { get()->record|null, put(record), delete() }` (single `singleton` record; stores structured-cloneable objects incl. `Uint8Array`).
- `clients/wallet/gc-passkey-webauthn.mjs`: `makeWebauthnPasskey({rpId, rpName}) -> { isSupported()->bool, enroll({userId,userName})->{credentialId, prfOutput}, unlock(credentialId)->prfOutput }`.
- `clients/wallet/gc-wallet.mjs`: `Wallet.generate()`, `Wallet.fromPrivateKeyB58(b58)`, `await wallet.exportPrivateKeyB58()`, `await wallet.address()`.
- `clients/wallet/gc-store.mjs` is the single-method (passkey-only) predecessor — read it for the enroll/unlock shape, but the persistent path uses the new `gc-keyring`.

**Conventions:** ruff/mypy/pytest gates; `node --test clients/wallet/*.test.mjs src/gumptionchain/static/js/*.test.mjs`; vendor wallet modules via `scripts/sync_wallet.py`; drift guard `tests/test_wallet_vendored.py` must stay green. Commit bodies end with `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.

---

## PR 1 — `gc-keyring.mjs` (DEK-wrapping multi-method keyring)

Branch: `feat/wallet-keyring` off fresh `main`.

### Task 1: expose the KEK derivations

**Files:** `clients/wallet/gc-envelope.mjs`, `clients/wallet/gc-backup.mjs`

- [ ] **Step 1:** `export` `deriveAesKey` in `gc-envelope.mjs` (currently internal). Add a node:test asserting `seal/open` still round-trip (no behavior change).
- [ ] **Step 2:** `export` `deriveKey` (PBKDF2) from `gc-backup.mjs`; confirm `exportEncrypted`/`importEncrypted` still pass their tests.
- [ ] **Step 3:** `uv run python scripts/sync_wallet.py`; gates green. Commit: `refactor(wallet): export deriveAesKey + deriveKey for keyring reuse`.

### Task 2: `gc-keyring.mjs` enroll/unlock (passphrase) — TDD

**Files:** Create `clients/wallet/gc-keyring.mjs`, `clients/wallet/gc-keyring.test.mjs`

- [ ] **Step 1: Failing test** (node:test, in-memory fake store):

```js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { Wallet } from './gc-wallet.mjs';
import * as keyring from './gc-keyring.mjs';

function fakeStore() {
  let rec = null;
  return { get: async () => rec, put: async (r) => { rec = r; }, delete: async () => { rec = null; } };
}

test('enroll(passphrase) then unlock(passphrase) recovers the same wallet', async () => {
  const store = fakeStore();
  const w = await Wallet.generate();
  const addr = await w.address();
  await keyring.enroll(w, { store }, { passphrase: 'correct horse' });
  assert.equal(await keyring.hasWallet(store), true);
  const unlocked = await keyring.unlock({ store }, { passphrase: 'correct horse' });
  assert.equal(await unlocked.address(), addr);
});

test('wrong passphrase fails closed', async () => {
  const store = fakeStore();
  await keyring.enroll(await Wallet.generate(), { store }, { passphrase: 'right' });
  await assert.rejects(() => keyring.unlock({ store }, { passphrase: 'wrong' }));
});
```
Run `node --test clients/wallet/gc-keyring.test.mjs` → FAIL.

- [ ] **Step 2: Implement** `gc-keyring.mjs`:

```js
import { sealWithKey, openWithKey, deriveAesKey } from './gc-envelope.mjs';
import { deriveKey } from './gc-backup.mjs';
import { Wallet } from './gc-wallet.mjs';

const VERSION = 1;
const SALT_BYTES = 16;
const PBKDF2_ITERATIONS = 600000;
const te = new TextEncoder();
const td = new TextDecoder();

async function newDek() {
  const raw = crypto.getRandomValues(new Uint8Array(32));
  const key = await crypto.subtle.importKey('raw', raw, 'AES-GCM', false, ['encrypt', 'decrypt']);
  return { raw, key };
}
async function importDek(raw) {
  return crypto.subtle.importKey('raw', raw, 'AES-GCM', false, ['encrypt', 'decrypt']);
}

async function passphraseWrap(passphrase, dekRaw) {
  const salt = crypto.getRandomValues(new Uint8Array(SALT_BYTES));
  const kek = await deriveKey(passphrase, salt, PBKDF2_ITERATIONS);
  const { iv, ciphertext } = await sealWithKey(kek, dekRaw);
  return { salt, iterations: PBKDF2_ITERATIONS, iv, ciphertext };
}
async function passkeyWrap(passkey, dekRaw, ids) {
  const { credentialId, prfOutput } = await passkey.enroll(ids);
  const kek = await deriveAesKey(prfOutput);
  const { iv, ciphertext } = await sealWithKey(kek, dekRaw);
  return { credentialId, iv, ciphertext };
}

export async function hasWallet(store) {
  return (await store.get()) !== null;
}

export async function enroll(wallet, { store }, { passphrase }) {
  const b58 = await wallet.exportPrivateKeyB58();
  const { raw: dekRaw, key: dekKey } = await newDek();
  const wallet_ct = await sealWithKey(dekKey, te.encode(b58));
  const wraps = { passphrase: await passphraseWrap(passphrase, dekRaw) };
  await store.put({ version: VERSION, address: await wallet.address(), wallet_ct, wraps });
  return wallet;
}

// Unwrap the DEK with whichever method's secret was supplied, then decrypt the
// wallet. Wrong secret -> GCM auth-tag failure -> reject (fails closed).
async function unwrapDek(rec, { passkey } = {}, { passphrase } = {}) {
  const { wraps } = rec;
  if (passphrase != null && wraps.passphrase) {
    const w = wraps.passphrase;
    const kek = await deriveKey(passphrase, w.salt, w.iterations);
    return new Uint8Array(await openWithKey(kek, w));
  }
  if (passkey && wraps.passkey) {
    const prfOutput = await passkey.unlock(wraps.passkey.credentialId);
    const kek = await deriveAesKey(prfOutput);
    return new Uint8Array(await openWithKey(kek, wraps.passkey));
  }
  throw new Error('no usable unlock method/secret');
}

export async function unlock({ store, passkey } = {}, { passphrase } = {}) {
  const rec = await store.get();
  if (!rec) throw new Error('no stored wallet');
  const dekRaw = await unwrapDek(rec, { passkey }, { passphrase });
  const dekKey = await importDek(dekRaw);
  const b58 = td.decode(new Uint8Array(await openWithKey(dekKey, rec.wallet_ct)));
  return Wallet.fromPrivateKeyB58(b58);
}

export async function clear(store) { await store.delete(); }
```

> Match `sealWithKey`/`openWithKey`'s exact arg/return shapes from `gc-envelope.mjs` (iv/ciphertext types). Store iv/ciphertext as the types IndexedDB structured-clones (Uint8Array is fine). `deriveKey(passphrase, salt, iterations)` takes the salt as bytes — store/read it consistently.

- [ ] **Step 3:** Run → PASS. `node --test`.
- [ ] **Step 4: Commit** — `feat(wallet): gc-keyring — DEK-wrapped passphrase enroll/unlock`.

### Task 3: passkey method (add/unlock by either) — TDD

**Files:** `gc-keyring.mjs`, `gc-keyring.test.mjs`

- [ ] **Step 1: Failing test** with a fake PRF passkey:

```js
function fakePasskey() {
  const PRF = new Uint8Array(32).fill(7);
  return { isSupported: async () => true, enroll: async () => ({ credentialId: 'cred1', prfOutput: PRF }), unlock: async () => PRF };
}

test('a wallet with both methods unlocks by passkey AND by passphrase', async () => {
  const store = fakeStore(); const passkey = fakePasskey();
  const w = await Wallet.generate(); const addr = await w.address();
  await keyring.enroll(w, { store }, { passphrase: 'pw' });
  await keyring.addPasskey({ store, passkey }, { passphrase: 'pw' });
  assert.equal(await (await keyring.unlock({ store, passkey }, {})).address(), addr); // passkey
  assert.equal(await (await keyring.unlock({ store }, { passphrase: 'pw' })).address(), addr); // passphrase
});
```

- [ ] **Step 2: Implement** `addPasskey({store, passkey}, {passphrase})`: unlock the DEK via the supplied passphrase (reuse `unwrapDek`), then `passkeyWrap` the same `dekRaw` and merge `wraps.passkey` into the stored record. Add `removeMethod(store, name)` (delete a wrap; refuse to remove the last one). `unlock` already prefers passphrase when supplied, else passkey — confirm both branches.

- [ ] **Step 3:** Run → PASS. Add tests: removing passphrase when it's the only method is refused; `removeMethod` leaves the other method working.

- [ ] **Step 4: Sync + drift guard + gates.** `uv run python scripts/sync_wallet.py`; `uv run pytest tests/test_wallet_vendored.py`; full gates incl. both node globs. Commit: `feat(wallet): gc-keyring passkey method + add/remove`. Open PR.

---

## PR 2 — `/wallet` page (management) + session helper

Branch: `feat/wallet-page` off fresh `main` (after PR 1).

### Task 4: `wallet-session.mjs` auto-lock helper (TDD)

**Files:** Create `src/gumptionchain/static/js/wallet-session.mjs` (+ `.test.mjs`)

- [ ] Pure, testable session holder: `setWallet(w)` / `getWallet()` / `lock()` (drops the reference), `onLock(cb)`, an idle-timer (`armIdle(ms, now=Date.now)` + `touch()`), and `installAutoLock({document, window, idleMs})` wiring `visibilitychange`/`pagehide` → `lock()`. Tests: lock clears the wallet + fires `onLock`; idle timer locks after the interval (inject a fake clock); touch resets it. Commit: `feat(wallet): wallet-session auto-lock helper`.

### Task 5: `/wallet` view + template + glue (TDD)

**Files:** `browser.py` (`wallet_view` → `/wallet`, passes `node_host` + a `rp_name`), `base.html` (nav), `templates/wallet.html`, `src/gumptionchain/static/js/wallet-glue.mjs` (+ test), `tests/test_wallet_page.py`, `tests/test_ui_seam.py`

- [ ] **View:** `wallet_view` renders `wallet.html` (static shell; all key work is client-side). No DB/chain.
- [ ] **Template** (`wallet.html`, extends base, content only): the state-driven UI from the spec — **No wallet** (Create / Import, each with a passphrase field) and **Has wallet** (address, Unlock [passphrase + passkey-if-supported], Lock, Add passkey [secure-origin], Backup [download], Forget [confirm]). A prominent **security banner**. The **first-persist trust acknowledgment** (a checkbox/confirm gating the first enroll on this origin; remember via a small `localStorage` flag). `{% include "wallet/extra.html" ignore missing %}`. Inline `<script type="module">` importing `wallet-glue.mjs` (mirror `/transact`).
- [ ] **`wallet-glue.mjs`**: wire the flows using `gc-keyring` + `gc-store-idb` + `gc-backup` + `gc-wallet` + `wallet-session`; passkey via `makeWebauthnPasskey({rpId: location.hostname, rpName})` gated on `window.isSecureContext && await passkey.isSupported()`. Pure helpers (state→which-controls-shown, backup filename, the trust-ack flag read/write) unit-tested with fakes; DOM `init()`.
- [ ] **Tests:** `/wallet` renders the states/markup + security banner + trust-ack; passkey controls hidden when not secure (drive via a flag/stub); backup helper round-trip; seam test. Commit: `feat(browser): /wallet persistent wallet management page`. Open PR.

---

## PR 3 — `/transact` integration

Branch: `feat/transact-saved-wallet` off fresh `main` (after PR 2).

### Task 6: "Unlock saved wallet" on `/transact` (TDD)

**Files:** `templates/transact.html`, `src/gumptionchain/static/js/transact-glue.mjs` (+ test), `tests/test_transact_page.py`

- [ ] Add an **"Unlock saved wallet"** affordance to the key area, shown when `await keyring.hasWallet(store)`: passphrase input (+ passkey button if supported) → `keyring.unlock(...)` → set `importedWallet` via the shared `wallet-session` (reuse PR 2's helper). Keep the existing ephemeral import path as the alternative. Apply the same auto-lock policy to `/transact`'s session.
- [ ] Tests: `/transact` shows the "Unlock saved wallet" controls when a wallet exists (stub `hasWallet`); unlocking yields a usable wallet for the build/confirm/sign flow; ephemeral import still works. Commit: `feat(browser): unlock a saved wallet on /transact`. Open PR.

---

## Final

After all three merge: final reviewer over the combined diff (focus the keyring crypto + the session/lock handling); update the EGU checklist (#190) to mark #217 (Tier 1.5) shipped; note multiple-wallets as the remaining wallet follow-up. Security note: get a focused security read on `gc-keyring` (the DEK-wrap scheme) before/at merge — it's the key-custody core.
