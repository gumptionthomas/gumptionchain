# Manual verification — passkey signing_key adapters

The browser adapters (`gc-passkey-webauthn.mjs`, `gc-store-idb.mjs`) are
browser-only glue around WebAuthn-PRF and IndexedDB. They are **not** exercised
by `node --test` (no `window` / `navigator` / `indexedDB` in Node); the Node
suite only confirms they import cleanly and expose the right interface shape. The
pure pieces they sit on top of — the AES-GCM envelope (`gc-envelope.mjs`) and the
enroll/unlock orchestration (`gc-store.mjs`) — are CI-tested separately with
fakes.

Real behavior is verified by hand using `passkey-signing-key-demo.html`:

1. **Serve over a secure context.** WebAuthn requires `https://` or
   `http://localhost`. From the repo root:

   ```bash
   python -m http.server --directory clients/sdk 8000
   ```

   then open <http://localhost:8000/passkey-signing-key-demo.html>. (Opening the file
   via `file://` will not work — no secure context.)

2. **Use a PRF-capable browser/authenticator.** Recent Chrome or Safari with a
   platform passkey (Touch ID / iCloud Keychain), a phone passkey via hybrid
   (QR), or a hardware key with `hmac-secret`. The `prf` WebAuthn extension must
   be returned to the relying party.

   > **Not Bitwarden.** The Bitwarden browser extension uses PRF only to unlock
   > its *own* vault; it does not act as a PRF-capable authenticator for an
   > external RP, so it returns no `prf` result and enroll fails. Verified
   > 2026-06-06: `create()` returns `prf: undefined` under the Bitwarden
   > extension.

3. **Enroll.** Click **Enroll** and complete one passkey ceremony. Note the
   `address` shown in the output box.

4. **Reload, then unlock.** Reload the page (proving the key is recovered from
   storage, not in-memory state). Click **Unlock & sign** and complete one
   ceremony — with `userVerification: 'preferred'` and an already-unlocked
   provider this is a single confirm. Confirm the displayed `address` matches the
   one from step 3 and that a base64 signature is produced.

5. **Inspect the stored record.** In devtools → Application → IndexedDB →
   `gc-signing-key` → `signing_key` → key `singleton`, confirm the record holds only
   `{version, address, credentialId, iv, ciphertext}`. There must be **no
   plaintext private key** anywhere in the record.

6. **Clear.** Click **Clear** and confirm the record is removed from IndexedDB.

## Backup / recovery

The backup crypto (`gc-backup.mjs` — PBKDF2-SHA256 → AES-GCM-256, plus the
raw-string path) is fully covered by `node --test`. Only the browser glue in the
demo page (file download/upload, passphrase prompt, clipboard) is manual:

1. **Enroll or unlock** a signing_key; note its address.
2. Click **Download backup**, enter a passphrase, and save
   `gc-signing-key-backup.json`.
3. Open the file in a text editor — confirm it contains
   `"kind": "gc-signing-key-backup"`, base64 `salt`/`iv`/`ciphertext`, and **no**
   recognizable private key.
4. Reload the page (or use a fresh profile), click **Restore from file**, pick
   the file, and enter the passphrase — confirm the recovered address matches
   step 1.
5. Enter a **wrong** passphrase — confirm a clear `BadPassphraseError` message
   is shown and that any currently loaded signing_key is left unchanged (a failed
   restore does not replace it).
6. Click **Show raw key**, copy it; reload; paste into the import textarea and
   click **Import raw key** — confirm the address matches.

## Message signing

The `gc-msg-v1` crypto (`gc-message.mjs` — canonical, sign/verify, armored) is
fully covered by `node --test` and by JS↔Python parity + golden-vector tests.
Only the demo-page glue (textareas, copy buttons, JSON-vs-armored parsing) is
manual:

1. **Unlock a signing_key.** In **Sign message**, enter some text and click **Sign
   message**; confirm a proof JSON and an armored block appear, and that the
   proof `address` matches the loaded signing_key.
2. Copy the **armored** block into **Verify message** and click **Verify
   message**; confirm `valid: true` with the right `address`/`timestamp`.
3. Edit one character of the armored cleartext (the human-readable line between
   the headers) and verify again; confirm a `BadProofError` (cleartext
   mismatch) is surfaced.
4. Paste the **JSON** form instead and verify; confirm `valid: true`.
5. Tamper a character inside the JSON `message` field and verify; confirm
   `valid: false, reason: bad-signature`.

## Stake attestation

Composes gc-msg-v1 signing with on-chain provenance. Verification fetches
`GET /api/transaction/<txid>` from the node, so point the demo at a node where
the relevant transaction is mined and reader access is granted (a public node
sets `READER_ADDRESSES=["*"]`).

1. With a signing_key loaded, in **Stake attestation** enter a real mined `txid`,
   pick the `kind`, fill `subject` (or, for `transfer`, the destination
   `address`) and `amount` (grains) to match an outflow of that transaction,
   then click **Build & sign attestation**. Confirm the proof JSON appears and
   its `address` matches the loaded signing_key.
2. Copy the proof JSON into the verify box and click **Verify attestation**.
   Confirm `valid: true` with all three checks green (`signature: true`,
   `onchain: true`, `consistent: true`) and no `reasons`.
3. Tamper one character inside the proof's `message` field and verify again;
   confirm `valid: false` with `reasons: bad-signature`.
4. Restore the proof but change the `txid` to one that is not mined (or that is
   on an orphaned/pending branch) and verify; confirm `valid: false` with the
   matching reason (`txn-not-found` for an unknown txn, `not-canonical` for a
   non-canonical one).

## Notes for the tester

- The passkey credential is a **separate** WebAuthn credential (ES256/RS256,
  `pubKeyCredParams` `-7`/`-257`) used only for its PRF output. It is **not** the
  signing_key's RSA-2048 signing key — that key is generated by `SigningKey.generate()`
  and stored AES-GCM-encrypted under a key derived from the PRF output.
- `enroll` prefers the PRF result returned at credential-creation time; if the
  authenticator does not return PRF on `create()`, it falls back to one
  additional `get()` assertion to obtain it. On such authenticators enrollment
  may prompt twice.
- If **Enroll** fails immediately with a PRF-related error, the
  browser/authenticator did not return the `prf` extension — try a different
  authenticator (a platform passkey in recent Chrome/Safari, a phone passkey
  via hybrid, or a hardware key with `hmac-secret`). Note the Bitwarden
  extension does **not** export PRF to external RPs.

## Cross-origin discovery

`discover()` is unit-tested with a faked `navigator`; verify the real WebAuthn
path by hand:

1. Enroll a passkey on the demo page (creates a discoverable credential).
2. Reload (or open a second tab on the same `rpId`) and call
   `makeWebauthnPasskey({ rpId }).discover()` from the console.
3. Confirm it resolves to `{ credentialId, prfOutput }` after one ceremony, with
   the same `credentialId` as enrollment.
4. Call `discover()` and **cancel** the prompt — confirm it resolves to `null`
   (not a throw).
5. On a browser exposing `PublicKeyCredential.isConditionalMediationAvailable`,
   confirm `isConditionalAvailable()` resolves `true`; on one without it, `false`.
