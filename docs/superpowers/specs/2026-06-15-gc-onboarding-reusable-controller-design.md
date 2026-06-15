# gc-onboarding: a reusable, style-agnostic signing-key onboarding controller

**Date:** 2026-06-15
**Status:** design approved
**Consumers:** gumption-hub (refactor, separate PR), gumptactoe (first external
consumer), future EGU games/tools.

## Goal

Extract the hub's onboarding flow into a **reusable, framework- and
style-agnostic drop-in** that base `gumptionchain` ships, so every EGU app
adopts the proper *create → back up → restore → unlock → sign-login* flow **by
inclusion** rather than hand-rolling the glue. This is the "Recommended
follow-up" in `docs/key-onboarding-for-egu-apps.md`.

The deliverable is **logic, not markup**: a headless ES-module controller that
orchestrates the existing low-level signing modules. The consuming app owns all
DOM and CSS — a Bootstrap app and a CRT/green-phosphor terminal game must both
consume it unchanged.

This spec also ships a small **Python static-mount helper** so non-node
consumers (apps that embed the package but don't register the chain-explorer
blueprint) stop rediscovering how to serve base's static modules.

## Background — what already exists (and stays unchanged)

The low-level browser modules are owned by base at
`src/gumptionchain/static/signing-key/*.mjs` (source of truth in
`clients/signing-key/`, synced to static via `scripts/sync_signing_key.py`):

| Module | Relevant exports |
|---|---|
| `gc-signing-key.mjs` | `SigningKey.generate()`, `.fromPrivateKeyB58()`, `.address()`, `.sign()` |
| `gc-store-idb.mjs` | `makeIdbStore({dbName='gc-signing-key'})` → `{get,put,delete}` |
| `gc-keyring.mjs` | `hasSigningKey(store)`, `enroll(sk,{store},{passphrase})`, `unlock({store,passkey},{passphrase})`, `addPasskey({store,passkey},{passphrase},ids)`, `clear(store)`; record stores `address` cleartext; typed `NoSigningKeyError`/`UnsupportedError` |
| `gc-backup.mjs` | `exportEncrypted(sk,passphrase)` → `gc-signing-key-backup` artifact, `importEncrypted(backup,passphrase)` → `SigningKey`; `BadBackupError`/`BadPassphraseError` |
| `gc-passkey-webauthn.mjs` | `makeWebauthnPasskey({rpId,rpName})` → `{isSupported,enroll,unlock}` |
| `gc-message.mjs` | `signMessage(sk,message,{timestamp})` → `gc-msg-v1` proof |

The current orchestration (`static/js/signing-key-glue.mjs`,
`transact-glue.mjs`) is **DOM/Bootstrap-coupled** (queries `#ids`, toggles
`.hidden`, writes status divs). That coupling is exactly what this controller
extracts away from. The low-level modules above are **not modified** — this is
additive.

## What we build

### 1. `gc-onboarding.mjs` — the headless controller

New module `clients/signing-key/gc-onboarding.mjs` (synced to
`static/signing-key/`, served at
`/static/gumptionchain/signing-key/gc-onboarding.mjs`). No DOM, no CSS, no
framework; pure ESM, no bundler.

**Boundary:** the controller owns **identity state only** (has-key / unlocked /
address). It does **not** own the onboarding *wizard* (which screen is shown,
"create → backup → name → done") — that staging is the app's DOM/flow concern.
This boundary is what keeps it style-agnostic.

**Factory:**

```js
import { makeOnboarding } from '.../gc-onboarding.mjs';

const onb = makeOnboarding({
  store,          // optional; default makeIdbStore() (the gc-signing-key IDB)
  rpId, rpName,   // optional; enables passkey via makeWebauthnPasskey. Omit → passkey disabled
  passkey,        // optional explicit adapter (tests inject a fake); else built from rpId/rpName
  window,         // optional; default globalThis.window (secureContext detection)
});
```

**Status + subscription:**

```js
await onb.status();
// → { hasKey, unlocked, address|null, passkeySupported, secureContext }
const off = onb.onChange(status => render(status)); // fires after every transition; returns unsubscribe
```

- `hasKey` = `await keyring.hasSigningKey(store)`.
- `unlocked` = controller holds an in-memory key.
- `address` = the unlocked key's address, else the stored record's cleartext
  `address` if a record exists, else `null` (lets the app show "welcome back
  GC…" *before* unlock — no raw key, no re-paste).
- `passkeySupported` = `secureContext && await passkey.isSupported()` (false
  when no `rpId`/`rpName`/`passkey` provided).
- `secureContext` = `window.isSecureContext`.

**Actions (all async unless noted):**

| Method | Behavior | Returns |
|---|---|---|
| `create({ passphrase, withPasskey=false, userName })` | `SigningKey.generate()` → `keyring.enroll(sk,{store},{passphrase})`; if `withPasskey && passkeySupported`, `keyring.addPasskey(...)`. Holds unlocked. | `{ address }` |
| `unlock({ passphrase })` or `unlock({ passkey: true })` | `keyring.unlock({store,passkey?},{passphrase?})`. Holds unlocked. | `{ address }` |
| `restore({ backup, passphrase })` | `importEncrypted(backup,passphrase)` → `keyring.enroll(...)`. `backup` is a parsed object or JSON string (the app reads the File). Holds unlocked. | `{ address }` |
| `backup({ passphrase })` | If locked, unlock with `passphrase` first; then `exportEncrypted(heldKey, passphrase)`. The app downloads/copies the artifact — the raw key is never returned. | `{ artifact, filename }` |
| `addPasskey({ passphrase, userName })` | `keyring.addPasskey({store,passkey},{passphrase},ids)` on an existing key. | `{ address }` |
| `signLogin(challenge, { timestamp } = {})` | Requires unlocked (throws `NoSigningKeyError` if locked); `signMessage(heldKey, challenge, {timestamp})`. The app POSTs the proof to its server, which verifies `gc-msg-v1`. | `gc-msg-v1` proof |

**Passkey identity (`ids`):** WebAuthn enrollment needs a `userId`/`userName`.
The controller defaults both to the key's `address` (stable, no PII); an app may
override the display `userName` via the `create`/`addPasskey` option above.
| `lock()` (sync) | Drop the in-memory key; the stored record stays. Fires `onChange`. | — |
| `forget()` | `keyring.clear(store)` + `lock()`. Deletes the device record. | — |

**Errors:** the low-level typed errors (`NoSigningKeyError`, `BadBackupError`,
`BadPassphraseError`, `UnsupportedError`) propagate unchanged and are
**re-exported** from `gc-onboarding.mjs` so apps can `catch` by type and render
their own copy.

**`onChange`** fires after `create`/`unlock`/`restore`/`addPasskey`/`lock`/
`forget`, invoking each listener with the fresh `status()` snapshot.

**Lock policy:** holds the unlocked `SigningKey` in memory until `lock()`. The
controller ships **no timers** — idle/`visibilitychange` auto-lock is the app's
policy. The docs/PR include the recommended snippet apps can wire themselves.

### 2. `static_assets_blueprint()` — the Python static-mount helper

In base (e.g. `src/gumptionchain/__init__.py`):

```python
from gumptionchain import static_assets_blueprint
app.register_blueprint(static_assets_blueprint())  # serves /static/gumptionchain/...
```

A static-only `Blueprint(static_folder=files('gumptionchain')/'static',
static_url_path='/static/gumptionchain')` — the **same URL space** as the full
`browser` blueprint, so module paths are identical whether a consumer mounts the
explorer (hub) or only the assets (gumptactoe). For consumers that embed the
package but do **not** want the chain explorer + DB. `url_for(
'gumptionchain_static.static', filename='signing-key/gc-onboarding.mjs')`.

## Testing

- **JS** — `clients/signing-key/gc-onboarding.test.mjs` (`node --test`, the
  existing harness): exercises create → backup → restore → unlock (passphrase
  and passkey) → signLogin → lock → forget end-to-end with a **real**
  `SigningKey` (Node webcrypto), `fakeStore`, and `fakePasskey`. Asserts: the
  raw private key is never returned by any method; `restore` of a `backup`
  artifact yields the same address; `signLogin` proof verifies via
  `verifyMessage`; `status()` transitions are correct; passkey paths are skipped
  when unsupported. Not vendored (tests excluded by `sync_signing_key.py`);
  parity test continues to cover the synced `.mjs`.
- **Python** — a pytest mounts `static_assets_blueprint()` on a throwaway Flask
  app and asserts `GET /static/gumptionchain/signing-key/gc-onboarding.mjs`
  returns 200 with a JavaScript content-type.
- Full `node --test` + `pytest` + `ruff` + `mypy` gates stay green.

## Docs / deliverable

- Update `docs/key-onboarding-for-egu-apps.md`: flip "Recommended follow-up" to
  **Shipped**, documenting `gc-onboarding.mjs` (the API above), the
  `static_assets_blueprint()` mount, and the auto-lock snippet.
- **Report back to the gumptactoe session** (in the PR + a summary): the served
  module path, the `makeOnboarding` API + methods/options, the minimal mount
  snippet, and the **merge commit SHA** to pin.

## Scope

**In:** `gc-onboarding.mjs` + node tests; `static_assets_blueprint()` + pytest;
docs update; the report-back. One base branch → PR → merge.

**Out (separate efforts):**
- The **hub refactor** to consume the controller (separate gumption-hub PR;
  proves reusability but is a different repo).
- **gumptactoe** itself (the consumer; pins the merge SHA and builds its CRT DOM
  against the API).
- Transaction build/sign/submit (that's `transact-glue`; this controller is
  identity + login only).
- A shipped HTML template partial — markup stays the app's job (a
  "style-agnostic partial" is a contradiction; YAGNI).

## Invariants — what does NOT change

- The low-level `gc-*.mjs` module APIs (additive only).
- Protocol strings: `gc-msg-v1`, `gc-signing-key-backup`, the `gc-signing-key`
  IndexedDB name, keyring record/backup `VERSION`s.
- The `browser` blueprint and its `/static/gumptionchain` URL space (the helper
  reuses the same path).

## Risks

- **API doesn't fit a real consumer.** Mitigation: comprehensive end-to-end node
  test now; the hub refactor + gumptactoe are the ergonomics proof. Keep the
  controller small so a follow-up additive tweak is cheap.
- **Endpoint-name overlap** between `browser` (`'browser.static'`) and the
  helper (`'gumptionchain_static.static'`): only an issue if a single app
  registers both, which no consumer does (the hub uses `browser`; gumptactoe
  uses the helper). Same URL path is intentional.
- **Holding the unlocked key in memory** is inherent to "unlock to sign without
  re-prompting"; auto-lock is delegated to the app with a documented snippet.
