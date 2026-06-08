# EGU #217 — persistent browser wallet (Tier 1.5)

**Date:** 2026-06-08
**Issue:** #217 (deferred from base transact Tiers 0+1; on the EGU checklist #190)
**Status:** design approved

## Goal

Let a wallet **persist in the user's browser** (IndexedDB, zero server state) so
they don't re-import a key every visit, and let a node **generate** a new wallet
(only useful once persisted — to receive grit and spend later). Unlockable by
**passphrase (universal) or passkey (secure-origin upgrade)** — the same wallet,
either method. Ephemeral import (Tier 1) stays the default; persistence is an
explicit, origin-scoped opt-in.

Out of scope (Tier 2, stays hub / EGU #5): cross-device *hosted* sync, the
handle→address identity directory, recovery-as-a-service. (Manual encrypted
backup export/import IS in scope — it's client-side.)

## Boundary (restated)

Base ships the full client-side self-custody wallet; the **hub** is the
*recommended* trusted origin for persistence and owns the server-only features.
A persisted wallet is **origin-bound** — see the security model.

## The model: locked wallet in IndexedDB, unlock per page-session

A persisted wallet lives **encrypted in IndexedDB** (`gc-store-idb`,
origin-scoped, single `singleton` record). `/wallet` and `/transact` are
**separate full-page loads**, so an unlocked in-memory key CANNOT be shared
across them. Therefore **unlock is per-page-session**: each page that needs the
key reads the locked record and unlocks its own in-memory copy.

- `/wallet` — management (create / import / enroll methods / unlock / lock /
  backup / forget).
- `/transact` — gains an **"Unlock saved wallet"** option (passphrase/passkey)
  alongside the existing ephemeral import; the unlocked key is held for that
  page's session.

## The crypto: one wallet, either method unlocks it (`gc-keyring`)

"Enroll both" requires a **DEK-wrapping keyring** — a new orchestration module
(`clients/wallet/gc-keyring.mjs`) over the existing primitives. (The current
`gc-store` is single-method/passkey-only and is superseded by this for the
persistent path.)

- Generate a random 32-byte **DEK**; `wallet_ct = sealWithKey(DEK, b58_bytes)`.
- Wrap the DEK separately per enrolled method's KEK:
  - **passphrase** → PBKDF2-SHA256 → AES-GCM key (`gc-backup`'s `deriveKey`):
    `ppWrap = sealWithKey(KEK_pp, DEK_raw)`, store `{salt, iterations, ...ppWrap}`.
  - **passkey** → WebAuthn-PRF → HKDF-SHA256 → AES-GCM key
    (`gc-envelope.deriveAesKey`): `pkWrap = sealWithKey(KEK_pk, DEK_raw)`, store
    `{credentialId, ...pkWrap}`.
- Stored record (IndexedDB singleton):
  `{ version, address, wallet_ct, wraps: { passphrase?: {...}, passkey?: {...} } }`.
- **`unlock(method, secret)`** → derive that method's KEK → `openWithKey` the
  wrap → DEK → `openWithKey(DEK, wallet_ct)` → b58 → `Wallet.fromPrivateKeyB58`.
  A wrong passphrase fails closed via the GCM auth tag.

`gc-keyring` API (DOM-free, injected `store` + `passkey` like `gc-store`):
`enroll(wallet, {store}, {passphrase})` (create the record + passphrase wrap),
`addPasskey(wallet-or-unlocked, {store, passkey})`, `addPassphrase(...)`,
`unlock({store, passkey?}, {passphrase?})` → `Wallet`, `removeMethod(...)`,
`hasWallet(store)`, `clear(store)`. Reuses `gc-envelope.sealWithKey/openWithKey`
+ exposes `gc-envelope.deriveAesKey` and `gc-backup.deriveKey` (currently
internal) so no crypto is duplicated.

**Enrollment rule:** **passphrase is the mandatory floor** (set at create/import,
universal). **Passkey is an optional add**, offered only on a **secure context**
where `passkey.isSupported()` (PRF available). A user cannot go passkey-only —
passphrase is always present as the portable/recovery method.

## `/wallet` page (management)

States:
- **No wallet** → **Create** (`Wallet.generate()`) or **Import** (b58 / PEM).
  Both require setting a passphrase to persist; create then prompts to download
  an encrypted backup.
- **Has wallet (locked)** → show address; **Unlock** (passphrase or, if enrolled
  + supported, passkey); **Backup**; **Forget** (with confirm).
- **Has wallet (unlocked)** → **Lock**; **Add passkey** (secure origin only);
  Backup; Forget.

On the **first persist on an origin**, an **explicit trust acknowledgment**
(one-time, remembered in a small flag) gates it: a plain-language confirm —
"Persist only on a node you trust: its page can use your key while unlocked."

## Generation + backup/recovery

- **Create**: `Wallet.generate()` (RSA) → show the new address → set passphrase →
  persist → prompt "download an encrypted backup now" (self-custody: this is the
  only recovery).
- **Backup**: `gc-backup.exportEncrypted` (passphrase) → downloadable JSON;
  **import-from-backup** (`importEncrypted`) on `/wallet`. This is the recovery +
  cross-device + cross-method path. Stated plainly: lose the passphrase AND the
  backup → the wallet is unrecoverable (no server, no reset).

## Passkey specifics

`makeWebauthnPasskey({ rpId, rpName })` with `rpId` = the serving origin's
hostname (from `location.hostname`), `rpName` from a sensible default/title.
Enroll/unlock via passkey only when `window.isSecureContext` AND
`await passkey.isSupported()` (PRF). On plain-HTTP/LAN nodes, passphrase is the
whole story — the passkey controls are simply absent (graceful degradation).

## Security model / session

- Ephemeral import (Tier 1) stays the **default**; persistence is opt-in behind
  the trust acknowledgment.
- **Session unlock + auto-lock** (a shared `wallet-session.mjs` helper used by
  both `/wallet` and `/transact`): the unlocked `Wallet` is held in a
  module-scoped var for the page; **auto-lock** on tab close/hide
  (`visibilitychange`/`pagehide`), after an **idle timeout** (default ~15 min,
  reset on activity), and via a manual **Lock**. On lock, drop the key reference
  (best-effort; RSA `CryptoKey` can't be zeroed, but the reference is released).
- Only signatures + public key ever leave the browser. The stored record is
  always ciphertext (DEK-wrapped); the plaintext key exists only transiently
  while unlocked.

## `/transact` integration

`/transact`'s key area becomes: **"Unlock saved wallet"** (passphrase / passkey,
shown when `hasWallet()`) **or** "Import a key (this session only)" (the existing
ephemeral path). Either yields an in-memory `Wallet` for the page session under
the same auto-lock policy. No change to the build→confirm→sign→submit flow.

## Data flow

```
/wallet create:  Wallet.generate -> set passphrase -> gc-keyring.enroll(store) -> backup prompt
/wallet unlock:  gc-keyring.unlock({store, passkey?}, {passphrase?}) -> Wallet (session)
/wallet backup:  gc-backup.exportEncrypted(wallet, passphrase) -> download
/transact:       hasWallet? -> Unlock (as above) | ephemeral import -> sign+submit
session:         wallet held in memory; auto-lock on idle/hide/close/manual
```

## Testing

- **`gc-keyring` (node:test) — the heart:** enroll(passphrase)→unlock round-trip;
  add passkey (fake PRF passkey injected) → unlock by passkey AND by passphrase
  (same wallet); wrong passphrase fails closed (no DEK, no wallet); remove a
  method; record shape/versioning. Plus the exposed `deriveAesKey`/`deriveKey`
  reuse (no duplicated crypto).
- **`/wallet` + `/transact`**: page renders, the create/import/unlock/forget +
  trust-acknowledgment markup, secure-context gating of passkey controls
  (testable via a flag), backup export/import round-trip (pure helper), seam
  tests.
- **Session/auto-lock**: pure helper tests (idle timer, lock clears reference,
  hide/close handlers wired).
- **Hard gates:** ruff + format, mypy strict, pytest, `node --test` (both globs);
  vendored-drift guard stays green (new `gc-keyring.mjs` synced).

## PR decomposition (sequential, off fresh main)

0. **docs** — this spec + the plan.
1. **`gc-keyring.mjs`** — DEK-wrap enroll/unlock/add/remove for passphrase +
   passkey over `gc-store-idb`; expose `deriveAesKey`/`deriveKey`; round-trip +
   fail-closed tests with a fake passkey. Crypto core, no UI. Vendor + drift
   guard.
2. **`/wallet` page** — create / import / enroll(passphrase + optional passkey) /
   unlock / lock / forget + trust acknowledgment + backup export/import +
   warnings; the `wallet-session.mjs` auto-lock helper; nav link; tests + seam.
3. **`/transact` integration** — "Unlock saved wallet" alongside ephemeral
   import; reuse `wallet-session.mjs`; tests.

## Out of scope / follow-ups

- Multiple wallets + active selector (gc-store-idb is single-record today).
- Cross-device hosted sync / identity directory / hosted recovery — hub (Tier 2).
- A passkey-managed *list* of credentials (single passkey per wallet for now).
