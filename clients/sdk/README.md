# GumptionChain Browser SDK

A dependency-free, vanilla-JS ([Web Crypto](https://developer.mozilla.org/en-US/docs/Web/API/Web_Crypto_API))
SDK for [GumptionChain](../../README.md): Ed25519 key management,
`gc-sig-v1` authenticated API requests, passkey-anchored at-rest storage,
passkey-PRF key **derivation** (self-custodial federated login),
24-word BIP-39 recovery phrases, self-custody backup/recovery, and generic
`gc-msg-v1` message signing.

The Python node verifies every signature this produces byte-for-byte.

## Requirements

- **A secure context** (`https://` or `http://localhost`) — WebAuthn and
  IndexedDB require it. `file://` will not work.
- **A PRF-capable authenticator** for passkey storage: a platform passkey
  (Touch ID / iCloud Keychain / Google Password Manager), a phone passkey via
  hybrid (QR), or a hardware key with `hmac-secret`. **The Bitwarden browser
  extension does _not_ export PRF to external relying parties** (it uses PRF
  only to unlock its own vault), so it cannot anchor signing_key storage. Backup via
  the raw `gcsec1…` secret string (below) _can_ be stored in any password
  manager, Bitwarden included.
- A modern browser, or Node 20+ to run the test suite.

## Importing

This is a barrel-style ESM package; import from `index.mjs`.

```js
// Relative path (vendored / monorepo)
import { SigningKey, signMessage } from './clients/sdk/index.mjs';

// Or pin a tag/commit via a CDN that serves the repo (no install). Include an
// @<tag-or-sha> segment so you track a fixed version, not the default branch:
// import { SigningKey } from
//   'https://cdn.jsdelivr.net/gh/gumptionthomas/gumptionchain@<tag-or-sha>/clients/sdk/index.mjs';

// Or, when a base gumptionchain node serves the package (member apps embedding
// the node's static assets), import the SERVED barrel:
// import { SigningKey } from '/static/gumptionchain/sdk/index.mjs';
```

Only symbols re-exported from `index.mjs` are the supported public API. Other
files (`gc-crypto.mjs`, `gc-envelope.mjs`, …) are internal and may change.

**Importing the served package (member apps).** When you consume the SDK from a
base node's static assets, import **only the served barrel** —
`/static/gumptionchain/sdk/index.mjs` — never an individual file URL
(`…/sdk/gc-keyring.mjs`). The barrel path is the stable contract; individual
served filenames are internal and may be renamed or moved, which would 404 a
direct import only **after** you bump the node pin (invisible until runtime —
see issue #352). Read the `version` export to gate on the embedder-API semver.
A base node serves `index.mjs` with a `text/javascript` MIME type (required for
`import`); `tests/test_static_assets.py` guards that path so a base-side break
fails CI rather than your deploy.

## Quickstart

### 1. Sign an authenticated API request (`gc-sig-v1`)

```js
import { SigningKey, signHeaders } from './index.mjs';

const signing_key = await SigningKey.generate();
const headers = await signHeaders(signing_key, {
  method: 'POST',
  path: '/api/transactions',
  query: '',
  body: new TextEncoder().encode(JSON.stringify(txn)),
  nodeHost: 'node.example',
  timestamp: Math.floor(Date.now() / 1000),
});
await fetch('https://node.example/api/transactions', {
  method: 'POST', headers, body: JSON.stringify(txn),
});
```

### 2. Store under a passkey, then unlock after reload

```js
import { SigningKey, enroll, unlock, hasSigningKey,
         makeWebauthnPasskey, makeIdbStore } from './index.mjs';

const passkey = makeWebauthnPasskey({ rpId: location.hostname, rpName: 'My App' });
const store = makeIdbStore({ dbName: 'gc-signing-key' });

if (!(await hasSigningKey(store))) {
  const signing_key = await SigningKey.generate();
  await enroll(signing_key, { passkey, store }, { userName: 'player' });
}
// ...later, after a reload:
const signing_key = await unlock({ passkey, store }); // one passkey ceremony
```

### 3. Back up & restore (self-custody)

```js
import { exportEncrypted, importEncrypted, exportPlain } from './index.mjs';

// Passphrase-encrypted file (download backup.kind === 'gc-signing-key-backup'):
const backup = await exportEncrypted(signing_key, passphrase);
const restored = await importEncrypted(backup, passphrase);

// Or the raw `gcsec1…` secret string for a password manager:
const secret = await exportPlain(signing_key);
```

### 4. Sign & verify a message (`gc-msg-v1`)

```js
import { signMessage, verifyMessage, toArmored, fromArmored } from './index.mjs';

const proof = await signMessage(signing_key, 'address X is me');
const armored = toArmored(proof); // shareable PGP-style block

const result = await verifyMessage(fromArmored(armored));
// { valid: true, address, timestamp, message }
```

### 5. Derive a key from a passkey's PRF (no stored key)

```js
import { deriveSigningKey, deriveSeed } from './index.mjs';

// `prfOutput` is the WebAuthn PRF result (a Uint8Array) from an
// enroll/discover ceremony. The same passkey reproduces the same key anywhere.
const signing_key = await deriveSigningKey(prfOutput);
// Optional passphrase = a second factor (a different passphrase → a different
// key, hence a different address):
const sk2 = await deriveSigningKey(prfOutput, { passphrase: 'correct horse' });
```

The seed is `HKDF-SHA-256(PRF)` — domain-separated from the wrap-keyring's
PRF→AES-KEK use by a distinct HKDF info label. With a passphrase it is
`HKDF(PRF ‖ PBKDF2-SHA-256-stretched(passphrase))`. It is fully deterministic
(no stored salt), so **the same passkey re-derives the same GC identity on any
origin without storing key material** — this is the "self-custodial federated
login" primitive. `deriveSeed(prfOutput, { passphrase? })` returns the raw
32-byte seed if you want to encode it yourself.

### 6. Import / export a 24-word recovery phrase (BIP-39)

```js
import { SigningKey } from './index.mjs';

const phrase = await signing_key.mnemonic();          // 24 words
const restored = await SigningKey.fromMnemonic(phrase);
// Standalone codec over the raw seed:
import { seedToMnemonic, mnemonicToSeed } from './index.mjs';
```

A recovery phrase is a standard 24-word BIP-39 encoding (8-bit SHA-256
checksum) of the **same 32-byte seed** that `gcsec1…` encodes — just a more
transcribable alternate form. The `/signing-key` page accepts either a phrase
or a `gcsec1…` string to import an identity. (This is the entropy↔mnemonic
mapping, *not* BIP-39's PBKDF2 seed-stretching — the words **are** the seed.)

## Public API

| Symbol | Purpose |
| --- | --- |
| `SigningKey` | Ed25519 keygen, `exportSecret`/`fromSecret` (`gcsec1…`), `mnemonic()`/`fromMnemonic` (24-word phrase), `gc1…` address, sign, verify-only via `fromPublicKeyB64` |
| `canonical`, `signHeaders` | `gc-sig-v1` request signing |
| `enroll`, `unlock`, `hasSigningKey`, `clear` | passkey-anchored storage orchestration |
| `makeWebauthnPasskey`, `makeIdbStore` | real WebAuthn + IndexedDB adapters; the passkey adapter also exposes `discover({ mediation, signal })` / `isConditionalAvailable()` |
| `recognize` | `recognize({ rpId })` → `{ recognized, address }` — a "who's here?" hint over `discover()` (enrolled identities only; see caveat) |
| `deriveSeed`, `deriveSigningKey` | passkey-PRF → 32-byte seed / `SigningKey` (no stored key; optional `passphrase` 2FA) |
| `seedToMnemonic`, `mnemonicToSeed` | BIP-39 24-word recovery-phrase codec over the raw seed |
| `exportEncrypted`, `importEncrypted`, `exportPlain`, `importPlain` | backup/recovery |
| `signMessage`, `verifyMessage`, `toArmored`, `fromArmored` | `gc-msg-v1` message signing |
| `UnsupportedError`, `NoSigningKeyError`, `BadBackupError`, `BadPassphraseError`, `BadProofError` | typed errors |
| `version` | package semver |

Anything not listed is internal/unstable.

### Cross-origin passkey discovery

`makeWebauthnPasskey({ rpId }).discover({ mediation })` finds an **existing**
discoverable passkey for `rpId` on a fresh origin (no prior local state) and
returns `{ credentialId, prfOutput, userHandle }`, or `null` if none is found or
the user dismisses. `userHandle` is the credential's enrolled `user.id` decoded
as a string (in EGU apps that's the signer's address), or `null` if the
assertion carries none — so a member can learn *which* identity returned with no
key material. `mediation: 'optional'` (default) is a modal prompt; `'conditional'`
is passkey autofill — for which the **consumer** supplies the
`<input autocomplete="webauthn">` (the SDK stays DOM-free). Feature-detect with
`isConditionalAvailable()`. For conditional mediation in a single-page app, pass a `signal` from an `AbortController` and abort it on route changes to cancel the autofill session. `makeOnboarding(...)` exposes the same `discover()`
when it is configured with a passkey adapter (`rpId` + `rpName`, or an injected
`passkey`); otherwise it returns `null`. Note `discover()` itself only needs
`rpId` — `rpName` is required for enrollment, not discovery.

This is the base primitive for hub-brokered "Sign in with Gumption" recognition.
**It yields recognition + unlock authority (the PRF), not the key bytes** — in
the *wrap* model the encrypted key blob is origin-scoped and not re-derivable
from the PRF, so the material itself still comes from the hub-served store or a
user backup. (In the *derive* model below, the PRF **is** the key — see the
federation boundary.)

### `recognize()` — the "who's here?" hint

`recognize({ rpId, mediation = 'optional', signal })` is a thin convenience over
`discover()` for the member-kit: it answers "is a returning EGU user here, and who
are they?" in one call, resolving `{ recognized: true, address }` when a passkey is
picked, or `{ recognized: false, address: null }` on every absent / cancelled /
unsupported / no-`userHandle` path. It **never throws** for those cases, so a caller
always falls through to "create".

> **Caveat — enrolled identities only.** The returned `address` is the credential's
> decoded `userHandle`, which equals the GC address **only for enrolled (wrap-keyring)
> identities**, where `user.id === address`. A **derived** identity (`create({ withPasskey })`
> / `makeDerivedIdentity`) has a **random** `userHandle` — its address isn't known at
> `create()` time, before the PRF exists — so `recognize()` would report a confidently
> **wrong** address for it. Recognize/rehydrate derived identities with
> `makeDerivedIdentity.resolve({ expectedAddress })` (which re-derives the real address
> from the PRF), **not** `recognize()`. Route the two identity kinds accordingly.

### Derived-identity flow (`gc-derived-identity.mjs`)

`makeDerivedIdentity({ passkey })` composes PRF derivation into a federated
login flow. It is a runtime module (imported from `./gc-derived-identity.mjs`,
not the `index.mjs` barrel); the hub and the `/signing-key` page build on it.
The injected `passkey` has the same shape as `makeWebauthnPasskey(...)`.

- **`enroll({ userName, passphrase? })`** creates a passkey, derives the key
  from its PRF, and returns `{ signing_key, address, mnemonic, credentialId }`
  — the derived key, its 24-word recovery phrase, and the credential id. The
  caller records `credentialId → address` (and whether a passphrase was used)
  in its own recognition directory; the derived address is **not** the
  passkey's `userHandle` (that is fixed at `create()` time, before the PRF
  exists).
- **`resolve({ expectedAddress, passphrase? })`** is federated login: it
  discovers a passkey, re-derives the key, and matches `address` against the
  caller-supplied `expectedAddress` (falling back to the discovered
  `userHandle` only when the caller omitted it and the credential carries one).
  It returns a status: `ok` (matched), `needs-passphrase` (mismatch with no
  passphrase tried), `mismatch` (wrong passphrase), `derived` (no expected
  address to check against), or `no-passkey`. A wrong passphrase simply derives
  a different address, so the match alone confirms the 2FA.

### Derive vs. wrap — the federation boundary

Two key-custody models ship side by side, and **only DERIVE identities federate
cross-origin**:

- **DERIVE** (`gc-derive.mjs` / `gc-derived-identity.mjs`): the seed is computed
  from the passkey PRF on the fly and **nothing is stored**. Because the
  derivation is deterministic and salt-free, the same passkey reproduces the
  same GC identity on **any** origin — so this is the model that supports
  "Sign in with Gumption" across sites.
- **WRAP** (`gc-store.mjs` keyring + `gc-envelope.mjs`): a generated key is
  AES-GCM-encrypted under a PRF-derived KEK and persisted in **origin-local**
  IndexedDB. The PRF unlocks the blob but cannot reconstruct the key elsewhere,
  so a wrap identity is **origin-bound** — it does not federate; cross-origin
  recovery needs the hub-served store or a user backup.

Consumers choosing a model should pick DERIVE when they need the same identity
to appear across origins, and WRAP when an origin-local, individually-generated
key is preferred.

## Versioning

`version` is the **package** semver (pre-1.0, pre-launch — the
embedder API is not yet frozen). It is **independent** of the wire scheme ids
`gc-sig-v1` and `gc-msg-v1`, which are protocol identifiers bound into
signatures and change only on a protocol revision.

## Testing

```bash
node --test clients/sdk/*.test.mjs   # JS unit + contract tests, zero npm
```

JS↔Python signature parity is enforced from the Python side
(`tests/test_browser_signing_key_parity.py`, `tests/test_message_parity.py`) via the
`sign-cli.mjs` / `message-cli.mjs` harnesses. Browser-only flows (real passkey +
IndexedDB) are covered by `MANUAL-VERIFICATION.md`.

## Extracting to its own repo / hosting

The SDK is packaged in place today. To move it to a dedicated repo or
embed it in a host web application:

1. Copy `clients/sdk/` to the new location. It is self-contained — the
   public surface has no imports outside this directory.
2. The only cross-repo coupling is the **JS↔Python parity tests** and the
   `sign-cli.mjs` / `message-cli.mjs` harnesses they invoke. Keep those here
   (pointing at a vendored/submoduled copy) or re-home them with the node.
3. Serve over `https://`, or pin a tag via jsDelivr. The path segment after
   `@<tag>` is wherever `index.mjs` lives in the new repo — at the repo root if
   you extracted `clients/sdk/`'s contents, e.g.
   `https://cdn.jsdelivr.net/gh/<owner>/<repo>@<tag>/index.mjs` (or
   `…@<tag>/clients/sdk/index.mjs` if you kept the subdirectory).

No build step is required — the barrel is plain ESM.
