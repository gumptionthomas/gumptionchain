# Key onboarding for EGU apps

> How an Extended Gumption Universe app should bring a user's **signing key**
> into being — and what it must never do. This is the contract every EGU app
> (the hub, Too Big To Fail, gumptactoe, future games and tools) follows so
> identity feels the same — safe, persistent, recoverable — across the universe.

GumptionChain identity is a **signing key**: an RSA keypair that lives on the
user's device and signs their stakes. There are no accounts and no server-side
secrets. How an app introduces that key is the user's first and most
security-critical moment — get it wrong and you either leak the private key or
make people paste a secret every session.

The reference implementation is the hub's `/onboarding` flow (`gumption-hub`,
`templates/onboarding.html`). This guide describes the pattern it embodies so
other apps reproduce it rather than reinventing — or, worse, falling back to raw
key handling.

---

## The anti-pattern this replaces

Do **not** do this (gumptactoe's first cut, `static/js/console.js`, is the
worked example of what to avoid):

1. `const b58 = await key.exportPrivateKeyB58()` — export the raw private key,
2. show it to the user: *"here's your key, don't lose it,"*
3. ask them to paste it back next session: *"paste your key here."*

Why it's wrong:

- **The raw private key is shown to and handled by the user.** Anything on the
  page, in the clipboard, in a screenshot, or in browser history can capture it.
  A private key must never be rendered.
- **No persistence.** The user re-pastes a long secret every session, so they
  stash it in a notes app, a chat-to-self, or a sticky note — all worse than a
  proper backup.
- **No encryption at rest.** Even if persisted, a bare key in `localStorage` is
  readable by any script on the origin.
- **No real recovery.** "Don't lose it" is not a backup strategy.

The base58 export (`exportPrivateKeyB58` / `fromPrivateKeyB58`) exists for power
users and tooling. It is **not** an onboarding UX.

---

## The five principles

A proper onboarding never exposes the raw key, and gives the user a durable,
recoverable identity:

1. **Generated on-device, never sent.** `SigningKey.generate()` runs in the
   browser; the private key never touches a server.
2. **Persisted, so there's no re-paste.** Store it on-device in IndexedDB
   (`gc-store-idb`, the `gc-signing-key` database). On the next visit the app
   recalls the user from the saved record — they paste nothing.
3. **Encrypted at rest behind a passphrase.** Enroll the key under a
   user-chosen passphrase (`gc-keyring.enroll`); unlock it only to sign
   (`gc-keyring.unlock`). The stored record is a passphrase-wrapped blob, not a
   usable key.
4. **Backed up as an _encrypted artifact_, never the raw key.** Offer "download
   encrypted backup" (and/or copy) producing the `gc-signing-key-backup` JSON
   (`gc-backup.exportEncrypted` — PBKDF2 → AES-GCM). The user saves *that*,
   protected by their passphrase — not the private key.
5. **Restored from the artifact, never pasted.** Recovery is "upload your backup
   + passphrase" (`gc-backup.importEncrypted` → `gc-keyring.enroll`), not "paste
   your key."

---

## The module contract

The browser signing modules are owned by base gumptionchain and shipped in its
package at `gumptionchain/static/signing-key/*.mjs`. Apps that embed the
`gumptionchain` package (the hub, gumptactoe, …) **should serve base's full set
from the embedded package** — the hub mounts them at
`/static/gumptionchain/signing-key/…` via `browser.static` — rather than
vendoring a partial copy that drifts. Base owns these; let it be the single
source of truth.

Adopt the **full** set, not just the signing subset:

| Module | Role |
|---|---|
| `gc-signing-key.mjs` | the `SigningKey` (keygen, sign, address) — the low-level primitive |
| `gc-store.mjs` / `gc-store-idb.mjs` | persistent on-device storage (the `gc-signing-key` IndexedDB) |
| `gc-keyring.mjs` | `enroll` / `unlock` — passphrase (and passkey) encryption at rest |
| `gc-backup.mjs` | `exportEncrypted` / `importEncrypted` — the encrypted backup artifact |
| `gc-passkey-webauthn.mjs` | optional passkey wrap, so unlock can use a passkey |
| `gc-message.mjs` / `gc-attestation.mjs` | sign challenges/claims (login + stakes) |

gumptactoe's first cut vendored only `gc-signing-key`, `gc-crypto`, `gc-errors`,
`gc-message` — the signing subset — which is exactly why it fell back to raw
base58. Add the persistence + backup modules and the proper UX becomes
available.

---

## The reference flow

The hub's `/onboarding` is the working reference. Copy its shape:

1. **Create your signing key** — a passphrase field; `keyring.enroll` persists
   the encrypted key. Reuse base's three-state key panel
   (`templates/_key_import.html`), driven by the signing-key glue, so you don't
   rebuild create/unlock/passkey.
2. **Back it up** *(encouraged, skippable)* — "download encrypted backup" /
   "copy backup" via `exportEncrypted`; honest "lose it and it's gone" framing.
3. **Put a name on it** *(optional)* — bind to a GitHub handle so shared proofs
   carry `@you` (see [`social-binding-envelope.md`](social-binding-envelope.md)).
4. **You're in** — confirm, show the address, hand off into the app.
5. **Restore** — a peer entry ("already have a key? restore a backup") that
   `importEncrypted`s a backup and lands the user in the same "you're in" state.

The key is **portable**: the *same* backup restores a user in any EGU app.
Storage is per-origin (IndexedDB), so each app restores once — the encrypted
backup is the bridge between them.

Reusable building blocks (don't reinvent them):

- `templates/_key_import.html` — the create / unlock / passkey panel.
- `templates/signing_key.html` — the full page: create, import, **restore from
  backup**, download backup.
- The hub's `templates/onboarding.html` — the staged create flow + the
  restore-a-backup path. The canonical copy-from until the follow-up below ships.

---

## The reusable drop-in (shipped)

Base ships **`gc-onboarding.mjs`** — a headless, style-agnostic controller that
orchestrates the modules above into the full flow. The consuming app owns all
DOM/CSS and drives the controller; logic and improvements land once for everyone.

Served at `/static/gumptionchain/signing-key/gc-onboarding.mjs`. Non-node
consumers that don't register the chain-explorer blueprint can mount the assets
with `static_assets_blueprint()`:

    from gumptionchain import static_assets_blueprint
    app.register_blueprint(static_assets_blueprint())  # serves /static/gumptionchain/...

Minimal use:

    import { makeOnboarding } from '/static/gumptionchain/signing-key/gc-onboarding.mjs';
    const onb = makeOnboarding({ rpId: location.hostname, rpName: 'My App' });
    onb.onChange(render);                       // app re-renders its own DOM
    await onb.status();                         // { hasKey, unlocked, address, passkeySupported, secureContext }
    await onb.create({ passphrase, withPasskey: true });
    await onb.unlock({ passphrase });           // or { passkey: true }
    const { artifact, filename } = await onb.backup({ passphrase });
    await onb.restore({ backup, passphrase });
    const proof = await onb.signLogin(challenge); // gc-msg-v1, POST to your server
    onb.lock();

Auto-lock is the app's policy — wire it to your own lifecycle, e.g.:

    document.addEventListener('visibilitychange', () => {
      if (document.hidden) onb.lock();
    });

---

## Checklist for an EGU app's key onboarding

- [ ] The raw private key is **never** rendered, logged, or pasted.
- [ ] The key is generated on-device and persisted in IndexedDB (`gc-store-idb`).
- [ ] It is encrypted at rest under a user passphrase (`gc-keyring`).
- [ ] Backup is an **encrypted artifact** (`gc-backup`), offered as download/copy.
- [ ] Recovery is **restore-from-backup**, not paste-a-key.
- [ ] The full gc module set is served from the embedded `gumptionchain` package,
      not a vendored partial copy.
- [ ] Returning users are recalled from the saved key with no re-entry.
