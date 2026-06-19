# GumptionChain Browser SigningKey

A dependency-free, vanilla-JS ([Web Crypto](https://developer.mozilla.org/en-US/docs/Web/API/Web_Crypto_API))
signing_key for [GumptionChain](../../README.md): RSA-2048 key management,
`gc-sig-v1` authenticated API requests, passkey-anchored at-rest storage,
self-custody backup/recovery, and generic `gc-msg-v1` message signing.

The Python node verifies every signature this produces byte-for-byte.

## Requirements

- **A secure context** (`https://` or `http://localhost`) — WebAuthn and
  IndexedDB require it. `file://` will not work.
- **A PRF-capable authenticator** for passkey storage: a platform passkey
  (Touch ID / iCloud Keychain / Google Password Manager), a phone passkey via
  hybrid (QR), or a hardware key with `hmac-secret`. **The Bitwarden browser
  extension does _not_ export PRF to external relying parties** (it uses PRF
  only to unlock its own vault), so it cannot anchor signing_key storage. Backup via
  the raw b58 string (below) _can_ be stored in any password manager, Bitwarden
  included.
- A modern browser, or Node 20+ to run the test suite.

## Importing

This is a barrel-style ESM package; import from `index.mjs`.

```js
// Relative path (vendored / monorepo)
import { SigningKey, signMessage } from './clients/signing-key/index.mjs';

// Or pin a tag/commit via a CDN that serves the repo (no install). Include an
// @<tag-or-sha> segment so you track a fixed version, not the default branch:
// import { SigningKey } from
//   'https://cdn.jsdelivr.net/gh/gumptionthomas/gumptionchain@<tag-or-sha>/clients/signing-key/index.mjs';
```

Only symbols re-exported from `index.mjs` are the supported public API. Other
files (`gc-crypto.mjs`, `gc-envelope.mjs`, …) are internal and may change.

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

// Or the raw b58 string for a password manager:
const b58 = await exportPlain(signing_key);
```

### 4. Sign & verify a message (`gc-msg-v1`)

```js
import { signMessage, verifyMessage, toArmored, fromArmored } from './index.mjs';

const proof = await signMessage(signing_key, 'address X is me');
const armored = toArmored(proof); // shareable PGP-style block

const result = await verifyMessage(fromArmored(armored));
// { valid: true, address, timestamp, message }
```

## Public API

| Symbol | Purpose |
| --- | --- |
| `SigningKey` | keygen, key import/export, address, sign, verify-only via `fromPublicKeyB64` |
| `canonical`, `signHeaders` | `gc-sig-v1` request signing |
| `enroll`, `unlock`, `hasSigningKey`, `clear` | passkey-anchored storage orchestration |
| `makeWebauthnPasskey`, `makeIdbStore` | real WebAuthn + IndexedDB adapters |
| `exportEncrypted`, `importEncrypted`, `exportPlain`, `importPlain` | backup/recovery |
| `signMessage`, `verifyMessage`, `toArmored`, `fromArmored` | `gc-msg-v1` message signing |
| `UnsupportedError`, `NoSigningKeyError`, `BadBackupError`, `BadPassphraseError`, `BadProofError` | typed errors |
| `version` | package semver |

Anything not listed is internal/unstable.

## Versioning

`version` is the **package** semver (currently `0.1.0`, pre-launch — the
embedder API is not yet frozen). It is **independent** of the wire scheme ids
`gc-sig-v1` and `gc-msg-v1`, which are protocol identifiers bound into
signatures and change only on a protocol revision.

## Testing

```bash
node --test clients/signing-key/*.test.mjs   # JS unit + contract tests, zero npm
```

JS↔Python signature parity is enforced from the Python side
(`tests/test_browser_signing_key_parity.py`, `tests/test_message_parity.py`) via the
`sign-cli.mjs` / `message-cli.mjs` harnesses. Browser-only flows (real passkey +
IndexedDB) are covered by `MANUAL-VERIFICATION.md`.

## Extracting to its own repo / hosting

The signing_key is packaged in place today. To move it to a dedicated repo or
embed it in a host web application:

1. Copy `clients/signing-key/` to the new location. It is self-contained — the
   public surface has no imports outside this directory.
2. The only cross-repo coupling is the **JS↔Python parity tests** and the
   `sign-cli.mjs` / `message-cli.mjs` harnesses they invoke. Keep those here
   (pointing at a vendored/submoduled copy) or re-home them with the node.
3. Serve over `https://`, or pin a tag via jsDelivr. The path segment after
   `@<tag>` is wherever `index.mjs` lives in the new repo — at the repo root if
   you extracted `clients/signing-key/`'s contents, e.g.
   `https://cdn.jsdelivr.net/gh/<owner>/<repo>@<tag>/index.mjs` (or
   `…@<tag>/clients/signing-key/index.mjs` if you kept the subdirectory).

No build step is required — the barrel is plain ESM.
