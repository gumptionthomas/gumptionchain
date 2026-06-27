# Client SDK reference

The **browser client SDK** that EGU member apps consume to do identity and chain
operations entirely client-side — zero npm, vanilla ESM. This is the **API
reference** for the primitives; the *patterns* guide ("how to compose these into
good UX," with the hub and Gump-Tac-Toe as reference impls) lives in gump-hub.

Every signature below is verified against the SDK source in `clients/sdk/`
(vendored to the served `static/sdk/`). All crypto methods are **async** — assume
`await` unless noted.

## Importing

The SDK is served by a base node and imported as ESM:

- **SDK barrel:** `/static/gumptionchain/sdk/index.mjs` — **the supported import
  surface.** Import from here, not from individual file URLs (those are internal
  and may move; a direct file import 404s on a rename after a pin bump — see
  issue #352). Read the `version` export (`'0.13.0'`) for the embedder-API semver.
- **Transact glue:** `/static/gumptionchain/js/transact-glue.mjs` — the served
  app-glue for the build→sign→submit transaction flow (a thin layer over the SDK;
  not part of the barrel).

```js
import { SigningKey, makeOnboarding, signMessage, version }
  from '/static/gumptionchain/sdk/index.mjs';
```

**Only symbols re-exported from `index.mjs` are the supported public API.** Other
modules (`gc-crypto`, `gc-envelope`, `gc-transaction`, `gc-derived-identity`, …)
are internal and may change without notice. Where a primitive below lives outside
the barrel, that is called out.

## Identity kinds (read this first)

Every identity is one of two **kinds**, and most UX branches on it:

- **`derived`** — a passkey-PRF identity. The Ed25519 seed is *derived* from the
  authenticator's WebAuthn PRF (`seed = KDF(PRF)`); **nothing secret is stored at
  rest**. Seamless across EGU apps, recoverable from a 24-word phrase. Optional
  passphrase adds 2FA (folded into the KDF).
- **`wrap`** — a passphrase-encrypted keyring. A random seed is wrapped (AES-GCM
  under a passphrase-derived key) and stored in IndexedDB; an optional convenience
  passkey can also unlock it. Home-origin/device-local by construction.

`status().kind` / a `recognize()` verdict tell you which you're holding. The
**phantom guard** (`classifyRecognition`, below) is what keeps a wrap passkey from
being mistaken for a derived one.

---

## 1. Onboarding controller — `makeOnboarding(opts)`

The high-level, headless identity surface (create / unlock / restore / back up /
sign-in). Source: `gc-onboarding.mjs`.

```js
const onb = makeOnboarding({ store, rpId, rpName, passkey, window });
```

- `store` — durable record store; defaults to `makeIdbStore()`.
- `rpId`, `rpName` — WebAuthn Relying Party id/name; **both** required to enable
  passkey features (else passphrase-only). See the rpId federation note in §5.
- `passkey` — optional pre-built adapter; otherwise built from `rpId`/`rpName`.
- `window` — for secure-context checks; defaults to `globalThis.window`.

Returns `{ status, onChange, create, unlock, restore, backup, addPasskey,
discover, recognize, signLogin, signTransaction, lock, forget }`:

| Method | Signature → returns | Purpose |
|---|---|---|
| `status()` | → `{ hasKey, unlocked, kind, address, passkeySupported, passkeyEnrolled, methods, secureContext }` | Snapshot for rendering UI. `kind` is `'derived' \| 'wrap' \| null`; `methods` is the enrolled unlock methods. |
| `onChange(fn)` | → unsubscribe `fn` | Fire `fn(snapshot)` on any state change (lock/unlock/create/…). |
| `create({ passphrase, withPasskey, userName })` | → `{ kind, address, mnemonic? }` | New identity. `withPasskey` (and support) → `derived` + `mnemonic`; else `wrap`. Throws `NoSigningKeyError` if a passkey is needed but absent. |
| `unlock({ passphrase, passkey })` | → `{ address }` | Unlock an existing record (derived re-derives from the passkey; wrap uses passphrase or passkey). |
| `restore({ mnemonic, backup, passphrase })` | → `{ kind: 'wrap', address }` | Import from a recovery phrase **or** encrypted backup; lands a `wrap`. Throws `BadBackupError` / `BadPassphraseError`. |
| `backup({ passphrase })` | → `{ kind:'derived', mnemonic }` or `{ kind:'wrap', artifact, filename }` | Derived → show the recovery phrase; wrap → an encrypted artifact + suggested filename. |
| `addPasskey({ passphrase, userName })` | → `{ address }` | Add a convenience passkey to a **wrap** identity. |
| `discover(opts)` | → adapter result or `null` | Ask the authenticator for resident credentials on the rpId. |
| `recognize()` | → `{ recognized, kind?, address? }` | "Who's here?" — discovers a passkey; **adopts** a `derived` one (writes the record + unlocks), only *reports* a `wrap` one. Call **only** when `status().hasKey` is false. |
| `signLogin(challenge, { timestamp })` | → gc-msg-v1 proof | Sign a login challenge (key must be unlocked). Throws `NoSigningKeyError` if locked. |
| `signTransaction(unsigned)` | → signed txn | Sign a node-built unsigned txn (verifies the txid first). Throws `NoSigningKeyError` if locked. |
| `lock()` | → `undefined` | Drop the in-memory key (record kept). |
| `forget()` | → `undefined` | Erase the stored record **and** the in-memory key. |

---

## 2. Session signer — `makeSessionSigner(opts)`

A cross-document, **non-extractable, auto-locking, sign-only** surface. The seed
is never exposed; a sign-only key handle lives in IndexedDB and is reused across
the origin's documents without re-prompting, with idle/visibility auto-lock and
cross-tab lock sync. Source: `gc-session-signer.mjs`. (Reference consumer:
gump-hub `docs/auth-signing.md`.)

```js
const ss = makeSessionSigner({ store, durableStore, passkey, idleMs, broadcast, clock });
```

- `store` — in-memory/session handle store for *this* document.
- `durableStore` — cross-document durable store (typically IDB) of identities to
  unlock/create.
- `passkey` — adapter for `recognize` / `createDerived`.
- `idleMs` — idle auto-lock timeout (default 15 min, `DEFAULT_IDLE_MS = 900000`).
- `broadcast` — a `BroadcastChannel`-like object for cross-tab lock sync.
- `clock` — injectable `{ now, setTimer, clearTimer }` (tests).

Returns `{ adopt, status, signingKey, signLogin, signTransaction, recognize,
createDerived, unlock, onLock, lock, armIdle, touch, installAutoLock }`:

| Method | Signature → returns | Purpose |
|---|---|---|
| `adopt(key)` | → `{ address }` | Begin a session from a `SigningKey` (stores its non-extractable sign-only handle; arms idle). |
| `status()` | → `{ signedIn, address }` | Session snapshot. |
| `signingKey()` | → `SigningKey \| null` | The sign-only key for composing ops (attestations, custom `signMessage`). **Call fresh per op; never cache** — `lock()` can't revoke a captured handle. `exportSecret()`/`mnemonic()` throw `NoSeedError`. `null` when locked. |
| `signLogin(challenge, { timestamp })` | → gc-msg-v1 proof | Sign a login challenge. Throws `NoSigningKeyError` if locked. |
| `signTransaction(unsigned)` | → signed txn | Sign a transaction. Throws `NoSigningKeyError` if locked. |
| `recognize()` | → `{ verdict, address? }` | `verdict` is `'none' \| 'wrap' \| 'derived'`; a `derived` verdict writes the durable record **and** adopts the session. |
| `createDerived({ userName })` | → `{ address, mnemonic }` | Enroll a new derived identity, persist it, adopt the session. |
| `unlock({ passphrase, passkey })` | → `{ address }` | Unlock a wrap identity from `durableStore` and adopt it. |
| `onLock(cb)` | → `undefined` | Register a callback fired on lock (explicit / idle / cross-tab). |
| `lock()` | → `undefined` | Clear the cache, delete the session record, broadcast lock to other tabs. |
| `installAutoLock({ document, window, idleMs, activityEvents, … })` | → `undefined` | **Required to enable auto-lock.** Wires visibility/pagehide → lock and activity → `touch`, and arms the idle timer. Without it, `touch`/`armIdle` are no-ops. |
| `armIdle(ms)` / `touch()` | → `undefined` | Arm / reset the idle countdown (mostly internal; `adopt` arms automatically). |

---

## 3. Passkeys & recognition

Source: `gc-passkey-webauthn.mjs` (`makeWebauthnPasskey`, `recognize` are in the
barrel) and `gc-derived-identity.mjs` (internal — the kind logic).

**`makeWebauthnPasskey({ rpId, rpName, userVerification = 'preferred' })`** — the
WebAuthn-PRF adapter. Returns `{ isSupported(), enroll(), unlock(), discover(),
isConditionalAvailable() }`:

- `isSupported()` → `boolean`.
- `enroll({ userId, userName, residentKey = 'required' })` → `{ credentialId,
  prfOutput }` (creates a passkey, extracts its PRF).
- `unlock(credentialId)` → `prfOutput` (`Uint8Array`).
- `discover({ mediation = 'optional', signal })` → `{ credentialId, prfOutput,
  userHandle } | null`.
- Throws `UnsupportedError` when the authenticator has no PRF; a user
  cancellation throws (`enroll`/`unlock`) or maps to `null` (`discover`).

**`recognize({ rpId, mediation = 'optional', signal })`** → `{ recognized,
address }` — a fail-open "is a returning user here?" hint (never throws). **Caveat:
for a `derived` identity the `userHandle` address is a *phantom*** (the PRF is the
seed but `userHandle` was fixed at enroll time, before the PRF existed). For
derived recognition use the onboarding/session `recognize()` (which re-derives and
guards), not this raw helper.

**`classifyRecognition({ userHandle, derivedAddress })`** → `'wrap' | 'derived'`
(internal, `gc-derived-identity.mjs`) — the **phantom guard**: a discovered
passkey whose `userHandle` is a valid address *different from* the PRF-derived
address is a `wrap` keyring; otherwise it's `derived`. This is what the
controllers use so a wrap passkey is never adopted as the wrong address.

---

## 4. Keys — `SigningKey`

The Ed25519 identity primitive (barrel). All methods async unless noted. Note the
**camelCase** names.

**Construct (statics):**

| Static | → | Notes |
|---|---|---|
| `SigningKey.generate()` | `SigningKey` | New random key. |
| `SigningKey.fromSecret(gcsec)` | `SigningKey` | From a `gcsec1…` secret. Throws on bad checksum/HRP. |
| `SigningKey.fromSecretSignOnly(gcsec)` | `SigningKey` | Non-extractable; `exportSecret`/`mnemonic` throw `NoSeedError`. |
| `SigningKey.fromMnemonic(mnemonic)` | `SigningKey` | From a 24-word phrase. |
| `SigningKey.fromAddress(address)` | `SigningKey` | Verify-only, from a `gc1…` address. |
| `SigningKey.fromPublicKeyB64(b64)` | `SigningKey` | Verify-only, from base64 SPKI. |
| `SigningKey.fromSignOnlyHandle({ privateKey, publicKey })` | `SigningKey` (sync) | Reconstruct from a structured-cloneable handle (IndexedDB). |
| `SigningKey.isSupported()` | `boolean` | Feature-detect WebCrypto Ed25519. |

**Instance:**

| Method | → | Notes |
|---|---|---|
| `address()` | `string` | `gc1…` bech32m address. |
| `exportSecret()` | `string` | `gcsec1…` seed. Throws `NoSeedError` if sign-only / no private key. |
| `mnemonic()` | `string` | 24-word phrase. Throws `NoSeedError` if sign-only. |
| `publicKeyB64()` | `string` | base64 SPKI public key. |
| `sign(bytes)` | `string` | base64 Ed25519 signature (`bytes: Uint8Array`). Throws if verify-only. |
| `verify(bytes, signatureB64)` | `boolean` | False (not throw) on malformed input. |
| `toSignOnlyHandle()` | `{ address, privateKey, publicKey }` | Non-extractable, structured-cloneable handle for the session signer. |

---

## 5. Messages & attestations

**Messages — `gc-message.mjs` (barrel):** the generic gc-msg-v1 signing scheme.

- `signMessage(signing_key, message, { timestamp })` → a proof `{ scheme:
  'gc-msg-v1', version:'1', address, timestamp, message, signature }`.
- `verifyMessage(proof, { maxAge, now })` → `{ address, timestamp, message, valid,
  reason? }` (`reason` is `'bad-signature' | 'expired'`). Throws `BadProofError`
  on a malformed proof.
- `toArmored(proof)` → PEM-style string; `fromArmored(text)` → proof (throws
  `BadProofError` on a mismatch). Both sync.

**Attestations — `gc-attestation.mjs`:** stake/provenance claims built on gc-msg-v1.
**In the barrel:** `signStakeAttestation`, `parseStakeAttestation`, `verifyStake`.

- `signStakeAttestation(signing_key, claim, { timestamp })` → a gc-msg-v1 proof
  over a canonical stake claim (`txid, kind, subject/address, amount, handle?`).
- `parseStakeAttestation(proof)` → the validated claim (throws `BadAttestationError`).
- `verifyStake(proof, { fetchProvenance, maxAge, minConfirmations })` → `{ valid,
  checks: { signature, onchain, consistent }, signer, claim, provenance,
  confirmations, reasons }`. `fetchProvenance(txid)` is an injected lookup so the
  SDK stays node-agnostic.

> Social-binding helpers (`signSocialBinding`, `parseSocialBinding`,
> `verifyBinding`, `buildBindingMessage`, `validateBindingClaim`) also exist in
> `gc-attestation.mjs` but are **not in the barrel** — they're consumed hub-side
> (the person↔key binding registry). Treat them as internal until promoted.

**API request signing — `gc-sig.mjs` (barrel):** authenticate calls to a node
(gc-sig-v1, node-bound).

- `canonical({ method, path, query, body, nodeHost, timestamp, address })` →
  `Uint8Array` canonical string.
- `signHeaders(signing_key, { method, path, query, body, nodeHost, timestamp })` →
  `{ 'GC-Sig-Version':'1', 'GC-Address', 'GC-Timestamp', 'GC-Signature' }`.

---

## 6. Transactions — `transact-glue.mjs`

The served build→sign→submit glue (`/static/gumptionchain/js/transact-glue.mjs`).
The node builds the unsigned txn (canonical bytes), the client verifies the txid,
signs, and submits — the seed never leaves the browser. Pure helpers are DOM-free;
`init()` is the only DOM-wiring export.

| Export | Signature → returns | Purpose |
|---|---|---|
| `buildUnsigned({ type, fields, signing_key, nodeHost, fetchImpl?, timestamp? })` | → `{ unsigned }` | gc-sig-authed build (GET) of an unsigned txn; **recomputes and verifies the txid** against the node's. `type` ∈ `transfer\|opposition\|support\|rescind`. Throws on `txid mismatch`. |
| `signAndSubmit({ unsigned, signing_key, nodeHost, fetchImpl? })` | → `{ unsigned, signed, status, message }` | Sign a pre-built unsigned txn and POST it (the two-step, confirm-then-sign flow). |
| `submitSigned({ signed, unsigned?, signing_key, nodeHost, … })` | → `{ unsigned, signed, status, message }` | POST an already-signed txn (also the "broadcast" path). HTTP errors surface in `message`, not as throws. |
| `signAttestation({ txid, kind, rawSubject, amount, signing_key, timestamp? })` | → gc-msg-v1 proof | Sign a stake attestation; **encodes `rawSubject`** for you (the `/verify` producer side). |
| `encodeSubject(raw)` | → `string` (sync) | base64url-encode a subject (matches on-chain provenance encoding). |
| `init(root, { nodeHost, rpId, rpName, store, session, win, doc })` | → `undefined` | Wire a page's transaction-builder + key-panel + attestation DOM. The only side-effecting export. |

Lower-level txid primitives live in `gc-transaction.mjs` (internal):
`dataCsv(txn)`, `txid(txn)`, `signingData(txn)`, `signUnsignedTxn(unsigned,
signing_key)` — byte-identical to the Python implementation.

### `relay-glue.mjs` — the relay path

`transact-glue` above is **direct-to-node**: the browser gc-sig-signs each API
request *as the user*, so the user's address must be a `TRANSACTOR`. For a
**closed-transactor** node where end users transact through a **relay** (the
node-proxy `node_proxy_blueprint`; the *relay's* service key is the only
authorized caller), use `relay-glue.mjs`
(`/static/gumptionchain/js/relay-glue.mjs`). The browser→relay hop is a plain
JSON POST (no GC-* headers — the relay signs the node call); the user still signs
only their own tx payload.

| Export | Signature → returns | Purpose |
|---|---|---|
| `buildUnsigned({ relayBase, type, fields, fetchImpl? })` | → `{ unsigned }` | POST `relayBase/txn/<type>` (`fields` use the relay's wire names: `signer`, `subject`, `amount_grit`, `kind`/`to_address`/`denomination_grit`+`count`). `type` ∈ `support\|oppose\|rescind\|transfer\|split`. |
| `signAndSubmit({ relayBase, unsigned, signing_key, fetchImpl? })` | → `{ unsigned, signed, txid }` | Sign a pre-built unsigned txn (`signUnsignedTxn`) and POST `relayBase/txn/submit` (confirm-then-sign). |
| `submit({ relayBase, signed, fetchImpl? })` | → `{ txid }` | POST an already-signed txn to the relay. |
| `buildSignSubmit({ relayBase, type, fields, signing_key, fetchImpl? })` | → `{ unsigned, signed, txid }` | The full relay round-trip in one call. |
| `buildBody` / `normalizeGrit` / `relayMessage` / `relayUrl` | (pure helpers) | Request-body assembly, whole-GRIT validation (sub-grain rejected, mirrors `gumptionchain.units`), and relay status→message mapping. |

Amounts cross as **whole GRIT** (`amount_grit`/`denomination_grit`, ≤ 2 decimals);
the relay converts to grains. `relayBase` is the prefix the consuming app mounts
the relay at (e.g. `/relay`).

---

## 7. Backup, recovery & derivation

**Backup — `gc-backup.mjs` (barrel):**

- `exportEncrypted(signing_key, passphrase, { iterations })` → an encrypted
  artifact (`version:3`, PBKDF2-SHA256 + AES-GCM-256; default 600k iterations).
- `importEncrypted(backup, passphrase)` → `SigningKey`. Throws `BadBackupError`
  (bad shape/version) or `BadPassphraseError` (GCM tag mismatch).
- `exportPlain(signing_key)` → `gcsec1…` string; `importPlain(secret)` →
  `SigningKey`.

**PRF derivation & phrase codec (barrel):**

- `deriveSeed(prfOutput, { passphrase })` → 32-byte `Uint8Array` (HKDF over the
  WebAuthn PRF; optional passphrase = 2FA). `deriveSigningKey(prfOutput, opts)` →
  `SigningKey`. (`gc-derive.mjs`)
- `seedToMnemonic(seed)` → 24-word string; `mnemonicToSeed(mnemonic)` → 32-byte
  `Uint8Array`. (`gc-bip39.mjs`)

---

## 8. Stores

- `makeIdbStore({ dbName = 'gc-signing-key' })` → `{ get(), put(record), delete() }`
  — the durable singleton record store (barrel).
- `makeSessionStore({ dbName = 'gc-session-signer' })` → `{ get(), put(), delete() }`
  — a *separate* IDB for the session signer's non-extractable handle (barrel).
- Low-level passkey-store helpers `enroll`, `unlock`, `hasSigningKey`, `clear`
  (`gc-store.mjs`, barrel) underlie the controllers; most apps use
  `makeOnboarding` / `makeSessionSigner` instead of calling these directly.

---

## 9. Errors — `gc-errors.mjs`

Typed errors so apps can branch on failure. **In the barrel:**

| Error | Thrown when |
|---|---|
| `UnsupportedError` | WebAuthn PRF (or other required capability) unavailable on this device/authenticator. |
| `NoSigningKeyError` | An unlock/sign was attempted with no stored or in-memory key (locked). |
| `BadBackupError` | A backup/restore artifact is structurally invalid or the wrong version. |
| `BadPassphraseError` | Decrypt failed — wrong passphrase or a tampered backup (GCM tag mismatch). |
| `BadProofError` | Input is not a structurally valid gc-msg-v1 proof (distinct from a *valid-but-unverified* proof). |
| `BadAttestationError` | Input is not a structurally valid stake attestation/claim. |

`NoSeedError` (thrown by `exportSecret()`/`mnemonic()` on a sign-only key) exists
in `gc-errors.mjs` but is **not re-exported by the barrel** — catch it by message
or treat a sign-only key as never-extractable by construction.
