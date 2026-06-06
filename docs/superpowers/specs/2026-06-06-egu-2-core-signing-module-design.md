# EGU #2.1 — headless gc-sig-v1 core signing module — design

**Date:** 2026-06-06
**Status:** Approved design, pre-implementation
**Issue:** #170 (sub-project 1 of EGU #2 / #152)
**Type:** New client module (vanilla JS / Web Crypto) — additive; no change to the Python chain.

## Summary

The first sub-project of the dependency-free browser wallet (#152): a headless
JS module that produces **`gc-sig-v1`** signatures, **`GC`-tagged addresses**, and
**`GC-*` request headers** that the Python node verifies **byte-for-byte**. It is
**vanilla ESM, zero-dependency, zero-build**, using only Web Crypto
(`crypto.subtle`) — browser-target, but runs identically under Node for tests.

This sub-project is **crypto/signing parity only**. Passkey-anchored storage,
backup/recovery, generic-message-signing UX, and packaging/hosting are deferred
to later sub-projects of #152. The `sign(bytes)` primitive built here is what all
of those later layers build on.

Depends on EGU 1b (#167, merged): RSA key size 2048 and signing-scheme stability.

## Why this first

It is small, self-contained, and unblocks everything else in #152, and it carries
the hardest correctness bar — a JS-produced signature must verify in the Python
`signing.verify`. Getting parity nailed and CI-gated first means the
storage/UX layers can be built against a trusted core.

## Verified parity mappings (from `wallet.py` / `signing.py` / `milling.py`)

| Concern | Python | Web Crypto / JS |
|---|---|---|
| Signature | `private_key.sign(data, PKCS1v15(), SHA384())` → base64 | `subtle.sign({name:'RSASSA-PKCS1-v1_5'}, key, data)` → base64 |
| Keygen | RSA 2048, public exponent 65537 | `subtle.generateKey` modulusLength 2048, publicExponent `[0x01,0x00,0x01]`, hash `SHA-384` |
| Public key bytes | DER **SubjectPublicKeyInfo** | `subtle.exportKey('spki', pub)` |
| Public key b64 | base64(SPKI DER) | base64 of the SPKI export |
| Private key bytes | DER **PKCS8** (unencrypted) | `subtle.importKey('pkcs8', …)` / `exportKey('pkcs8')` |
| Private key b58 | base58check(PKCS8 DER) | base58check of the PKCS8 bytes |
| `millHash` | `sha256(sha512(data)).digest()` | `subtle.digest('SHA-256', await subtle.digest('SHA-512', data))` |
| Address | `'GC' + base58check(millHash(spki)) + 'GC'` | same, with pure-JS base58check |
| Canonical | `\n`-join: `gc-sig-v1`, METHOD, path, query, `sha256(body).hexdigest()`, node_host, timestamp, address (UTF-8) | same |
| Headers | `GC-Sig-Version=1`, `GC-Address`, `GC-Public-Key`(b64 SPKI), `GC-Timestamp`, `GC-Signature`(b64 sig over canonical) | same |

**Determinism:** `PKCS1v15` signing has no randomness, so a fixed key + fixed
canonical yields **identical** signature bytes in Python and JS. This makes
golden-vector parity exact, not approximate.

**The one delicate primitive — base58check.** The JS implementation must
byte-match the Python `base58check>=1.0.2` package: base58-encode
`payload + sha256(sha256(payload))[:4]` using the Bitcoin alphabet
(`123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz`), preserving
leading-zero bytes as leading `1`s. The golden vectors and the live cross-verify
are what prove this is exact; if a mismatch appears, base58check is the first
suspect.

## Components (small, single-responsibility files under `clients/wallet/`)

- **`gc-crypto.mjs`** — primitives, no protocol knowledge: `base64encode/decode`,
  `base58checkEncode/Decode`, `millHash(bytes) -> Uint8Array` (sha256∘sha512),
  `sha256Hex(bytes)`.
- **`gc-wallet.mjs`** — the `Wallet`-equivalent: `generate()` (RSA-2048 keypair),
  `fromPrivateKeyB58(b58)` / `exportPrivateKeyB58()`, `publicKeyB64()`,
  `address()`, `sign(bytes) -> base64`. Pure crypto identity; no storage.
- **`gc-sig.mjs`** — protocol layer: `canonical({method, path, query, body,
  nodeHost, timestamp, address}) -> Uint8Array` and `signHeaders(wallet, {…})
  -> {GC-Sig-Version, GC-Address, GC-Public-Key, GC-Timestamp, GC-Signature}`,
  mirroring `signing.py` `_canonical` / `sign_headers`.

Each is independently testable; `gc-sig` depends on `gc-wallet` depends on
`gc-crypto`. All are async where Web Crypto is (keygen/sign/digest return
promises).

## Data flow (signing a request)

`signHeaders` builds the canonical bytes from the request parts → `wallet.sign`
runs `subtle.sign` (PKCS1v15/SHA-384) → base64 → assembled into the `GC-*`
headers alongside the b64 SPKI public key and the address. The Python
`authorize()` / `signing.verify` recomputes the same canonical and verifies.

## Error handling

The module is pure crypto; errors are programmer errors surfaced as thrown
`Error`s (mirroring the Python `NoPrivateKeyError` / import-failure semantics):
`sign` without a private key throws; malformed key import throws. Callers (later
sub-projects) handle UX. No silent fallback.

## Testing — three layers

1. **JS unit tests** — `clients/wallet/*.test.mjs`, run with Node's built-in
   runner (`node --test`), **zero npm dependencies**. Cover: keygen shape (2048,
   e=65537), `millHash` against a known digest, base58check round-trip + against
   golden values, address derivation, `sign` against golden signature, canonical
   string bytes, and `signHeaders` shape/values.
2. **Golden vectors** — `clients/wallet/testdata/gc-sig-vectors.json`, emitted by
   a Python generator from a **fixed test wallet** (a checked-in private key,
   distinct from the conftest canonical wallet): `{private_key_b58,
   public_key_b64, address, cases: [{method, path, query, body, node_host,
   timestamp, canonical, signature}]}`. A pytest (`tests/test_browser_wallet_
   vectors.py`) regenerates from the fixed key and asserts the committed vectors
   match (so they can't silently drift), and that Python verifies each vector
   signature. The JS unit tests assert JS reproduces the same address/canonical/
   signature for the same inputs.
3. **Live cross-verify** — `tests/test_browser_wallet_parity.py`: pytest shells
   out to `node` running a tiny `gc-wallet` driver that signs a canonical with
   the fixed key and prints the b64 signature + derived address; pytest asserts
   the real `Wallet(public_key).validate_signature(canonical, sig)` (and/or
   `signing.verify`) accepts it, and `address == Python address`. This is the
   no-fixture-drift proof that the actual JS code interoperates with the actual
   Python verifier.

## CI

Add **Node 20 LTS** to the CI workflow as the one new tool:
- a step running `node --test clients/wallet/`,
- Node also on the `pytest` job's PATH so `test_browser_wallet_parity.py` runs
  (or it skips with a clear marker if `node` is absent — but the intent is it
  runs).

**No `package.json`, no `node_modules`, no npm install** — Node's built-in test
runner + Web Crypto only. This is the supply-chain-safe path: the only new
dependency surface is the Node runtime itself, explicitly surfaced, with zero npm
packages (per the CLAUDE.md npm-caution).

## Out of scope (later sub-projects of #152)

- **Passkey-anchored storage** (WebAuthn PRF → at-rest key → IndexedDB).
- **Backup / recovery** (encrypted export + passkey-synced restore).
- **Generic message-signing UX** (the `sign(bytes)` primitive is built here; the
  off-chain "address X is me" envelope/format is later).
- **Packaging / hosting** (distribution; gumption.com embedding).
- Any change to the Python chain (this is purely additive client code; the only
  Python added is test-side generators/parity tests).

## Decisions log

- **Core signing parity first**, storage/UX deferred — smallest unblocking unit,
  hardest correctness bar.
- **In this repo** under `clients/wallet/` — co-located with `signing.py` so the
  live cross-verify can feed JS output straight into the Python verifier. (The
  separate-repo item is the gumption.com hub, EGU #5.)
- **Vanilla ESM, zero-dep, zero-build, Web Crypto** — sidesteps npm supply-chain
  risk; the browser is the target and Node runs the same code for tests.
- **Parity strategy = golden vectors + live cross-verify (option A)**;
  deterministic PKCS1v15 makes vectors exact. JS is CI-gated against the Python
  verifier.
- **Node 20 in CI, built-in runner, no npm deps** — the one surfaced new tool.

## Definition of done

- `gc-crypto.mjs` / `gc-wallet.mjs` / `gc-sig.mjs` under `clients/wallet/`,
  vanilla ESM, zero deps, runnable in browser and Node.
- A JS-produced `gc-sig-v1` signature verifies in the Python `signing.verify`,
  and JS-derived addresses equal Python addresses — proven by the live
  cross-verify test and the golden vectors.
- `node --test clients/wallet/` green; the Python vector + parity tests green;
  full `uv run pytest` green; ruff + mypy green (the new Python is test-side).
- CI runs Node 20 (`node --test`) and the cross-verify, with no npm packages.
- No change to the Python chain runtime; no schema/migration.
