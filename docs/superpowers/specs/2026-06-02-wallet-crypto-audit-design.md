# Wallet & Cryptographic-Primitives Threat-Modeled Audit — Design

**Status:** Draft for review
**Date:** 2026-06-02
**Kind:** Security audit (design phase — defines scope, adversary model, methodology, and deliverable shape; the audit itself is run during the implementation plan that follows this spec)

This is the fourth threat-modeled audit of cancelchain, after the [verification-pipeline audit](../audits/2026-05-29-verification-pipeline-audit.md) (closed 0/0/0/0), the [API-authentication audit](../audits/2026-05-31-api-authentication-audit.md) (closed 0/0/0/0), and the [P2P/networking audit](../audits/2026-06-01-network-p2p-audit.md) (closed 0/0/0/0). It targets the **cryptographic root of trust** — the wallet primitive layer (`wallet.py`), the `cc-sig-v1` signing primitives (`signing.py`), and the key-deserialization validators (`schema.py`) — the code every signature, address, and authenticated request is ultimately built on.

## Motivation

The three prior audits hardened (a) whether an individual block/transaction is *valid*, (b) whether a request is *authenticated/authorized*, and (c) whether a hostile peer can exhaust resources or corrupt orchestration state. All three **assume the cryptographic primitives underneath are sound**: that a signature can't be forged, that an address uniquely binds a public key, that key material can't be smuggled in degenerate form, and that private keys stay confidential. That assumption has never been audited.

This layer is the highest-leverage surface in the system: a flaw here doesn't admit one bad block — it can undermine identity, signature integrity, or key confidentiality *system-wide*, beneath every control the prior three audits established. It is also the most recently churned code (the 2048→3072 key-size bump, PR #119), which is exactly when latent assumptions are worth re-checking.

Concrete attack *seeds* already visible on inspection (candidate, not pre-judged — the audit confirms or refutes each):

- **`Wallet.encrypt` / `Wallet.decrypt`** (a bespoke RSA-OAEP + AES-GCM hybrid) has **zero production callers** after PR #111 replaced the challenge/response handshake — only `tests/test_wallet.py` exercises it (confirmed by grep). Unreachable bespoke cryptographic code is a standing liability: it can drift from production assumptions, be copy-pasted into a reachable path, or mask a framing bug nobody runs.
- **`import_key`** parses untrusted key material (`wallet.py:65`) behind a broad `except Exception` and tries private-then-public. Beyond pyca's loader and the `key.key_size != KEY_SIZE` check, imported public keys are **not validated for sane parameters** (public exponent, modulus oddness). Does a degenerate-exponent key (`e=1`, `e=3`, even `e`) load and verify? Does an oversized-modulus key cause a load-time DoS before the size check rejects it?
- **`decrypt`** slices `enc_session_key = raw[:key_size_bytes]` (`wallet.py:228`) with no length validation of `raw`; short/garbage input produces wrong-length slices fed to `private_key.decrypt`. (Folds into the dead-code finding if removal is the remediation.)
- **Passphrase-based `BestAvailableEncryption`** (`wallet.py:57,166`) delegates KDF and cipher choice to pyca defaults — no explicit KDF-hardness control. Brute-force resistance of an encrypted exported wallet is unpinned and undocumented.
- **`sign` / `validate_signature`** use **PKCS1v15** (`wallet.py:185,195`), not PSS. Confirm this is sound for the signing use, that the broad `except` in `validate_signature` can't be coaxed into a false `True`, and that base64 / length handling has no malleability.
- **Address derivation** `CC + b58(sha256(sha512(pubkey_DER))) + CC` (`wallet.py:156`) and the split between `validate_address_format` (structural, `schema.py:35`) and `validate_address` (recomputes from the key, `schema.py:27`) — is there a path that treats an address as backed by a key it isn't?
- **`schema.validate_signature`** (`schema.py:69`) wraps a public key and checks a signature but performs **no key→address binding check** (unlike `signing.verify`, which self-certifies `wallet.address == address` at `signing.py:117` *before* validating). Trace whether every caller that validates a transaction/block signature *also* enforces the key↔address binding (`transaction.py:97` calls `validate_address` — confirm it is always on the path), or whether a payload can carry a public key that signs correctly yet does not back the claimed `address`. **This seam is owned by this audit** (the verification and auth audits are closed; neither explicitly nails the crypto-layer key↔address binding of `schema.validate_signature`).
- **`Wallet.from_dict` / `from_json`** (`wallet.py:276-281`) call `cls(b58ks=wallet_dict.get('private_key'))`; a malformed/empty blob with a **missing `private_key` field** yields `b58ks=None`, which falls through to the no-arg branch and **silently generates a fresh wallet** instead of raising. Confirm this can't route a caller into operating on an unintended identity.
- **Wire base64 alignment** — `sign`/`validate_signature` and `validate_base64` use *standard* (not URL-safe) base64 (`wallet.py:6,38-43`; `schema.py:53`). Confirm a URL-safe-encoded signature/key is **rejected fail-closed** (raises `binascii.Error` → `False`) rather than silently mis-decoded, and that the wire format the client emits matches what the validators accept.
- **`encrypt` AES-GCM nonce** is a fresh `os.urandom(12)` per call (`wallet.py:219`); GCM nonce *reuse* under one key is catastrophic for confidentiality+integrity. The risk is **deferred because the path is dead, not because generation is proven sound** — and `encrypt` needs no private key (any public-key `Wallet` can call it), so re-enabling it warrants a fresh look. Confirm distinctly from the framing-length issue.

## Scope & trust boundaries

### In scope

- **Wallet primitives** (`src/cancelchain/wallet.py`) — the entire module:
  - Key generation (`Wallet.__init__` no-arg path), key-size enforcement.
  - Key import/deserialization: `import_key`, `import_b58_key`, `import_b64_key`, `from_file`, `from_dict`, `from_json` (PEM/DER, private + public, encrypted + plain).
  - Key export/serialization: `export_binary_key`, `export_private_key_pem`, `export_private_key_b58`, `public_key_b64`, `to_dict`/`to_json`, `to_file`.
  - Signing & verification: `sign`, `validate_signature` (PKCS1v15/SHA-384).
  - Hybrid encryption: `encrypt`, `decrypt` (RSA-OAEP + AES-GCM) — including its reachability.
  - Address derivation: `address`, and `mill_hash_bin` as used there.
  - Object identity: `__eq__`, `__hash__ = None`, `__repr__` (key-material exposure).
- **`cc-sig-v1` signing primitives** (`src/cancelchain/signing.py`) — `_canonical`, `sign_headers`, `verify` — examined for **cryptographic binding completeness and canonicalization ambiguity** (not the auth/role angle the 2026-05-31 audit already covered).
- **Key-deserialization validators** (`src/cancelchain/schema.py`) — `validate_address`, `validate_public_key`, `validate_signature`, `validate_address_format`, `validate_base64` — the entry points that wrap untrusted public keys into a `Wallet`.
- **Encoding helpers** — `b58decode`/`b58encode`, `b64decode`/`b64encode`, and the address hash construction `sha256(sha512(x))` as it bears on collision / second-preimage resistance.

### Trusted boundaries (reference, do not re-audit)

- The `authorize()` role gate and the auth *protocol-level* properties (node-binding, freshness as an **authorization** control, allowlist matching) — closed by the 2026-05-31 API-authentication audit.
- Whether a block/transaction is *valid* — verification audit's domain.
- The networking/orchestration/resource layer — networking audit's domain.

**Framing consequence (the scope razor):**

- A finding that reduces to *"an under-authenticated or under-authorized request is honored"* belongs to the **auth audit**. Cross-reference; do not claim it here.
- A finding that reduces to *"an invalid block/transaction is accepted"* belongs to the **verification audit**. Same treatment.
- This audit owns: *"a flaw in a cryptographic primitive or key-handling routine lets an adversary forge a signature, impersonate an address, recover or weaken key material, smuggle a degenerate key parameter, or relies on unreachable/bespoke crypto — independent of the higher protocol layers."*

**Seam clarifications (to prevent orphaning between now-closed audits).** Because the auth and verification audits are *closed*, a finding must not be silently deferred to them. Two specific seams are claimed **here**:

- A `signing.verify` finding about **canonical-string construction or input parsing** — e.g., the unbounded `int(ts)` timestamp parse (`signing.py:104`) feeding the canonical — is a crypto-canonicalization concern owned by this audit, even though the auth audit owned `verify`'s *authorization* properties (node-binding, freshness-as-an-authz-control). Trace it here; do not defer to the closed audit.
- The **key↔address binding** of `schema.validate_signature` (does a signature-valid public key actually back the claimed address?) is owned here, even though "an invalid transaction is accepted" superficially reads as the verification audit's domain.

### Explicitly out of scope

- `browser.py` / the human-facing web UI (XSS/CSRF/session — its own offered audit).
- CLI ergonomics generally (the wallet-file *crypto* handling — passphrase encryption, PEM round-trip — is in scope; argument parsing, output formatting, and import/export file orchestration are not).
- PoW/consensus soundness, and the `mill_hash` construction as a *PoW* primitive (the networking/verification audits' domain) — examined here only as the address-derivation hash.
- Brute-forcing 3072-bit RSA or AES-GCM themselves (assumed sound); the audit targets *how the primitives are used*, not the primitives' own cryptanalysis.

## Adversary categories

Six categories tailored to this layer. Each is a lens for the fan-out; a single concrete attack may touch more than one.

1. **Malicious key supplier** — crafts key material fed to `import_key` / `import_b58_key` / `import_b64_key` / `from_file`: wrong algorithm (EC/Ed25519/DSA), degenerate public exponent (`e=1`, `e=3`, even `e`), wrong size, truncated/garbage DER, encrypted-without-passphrase, or an oversized modulus aimed at a load-time DoS *before* the size check. Goal: get a degenerate/weak key accepted, force a mis-parse, or wedge the loader.
2. **Signature forger / malleability attacker** — attempts to make `validate_signature` return `True` on data the key didn't sign: PKCS1v15 forgery surface, base64 non-canonical encodings, length/empty/`None` confusion in the broad-`except` path, or signature reuse across differing canonical inputs.
3. **Impersonation / address-collision attacker** — targets `address = CC + b58(sha256(sha512(pubkey_DER))) + CC`: second-preimage/collision to bind two public keys to one address, or a gap between `validate_address_format` (structure only) and `validate_address` (recomputes from the key) that accepts an address not actually backed by the presented key.
4. **Key-confidentiality attacker** — targets exported/at-rest key material: the unpinned KDF behind `BestAvailableEncryption`, the bespoke `encrypt`/`decrypt` hybrid's confidentiality and integrity (OAEP parameters, AES-GCM nonce generation, framing length-confusion), and any plaintext-key exposure via `__repr__`, logging, or `to_dict`/`to_json` (which emits the b58 *private* key).
5. **Deserialization / resource abuser** — oversized or adversarial encodings to `b58decode` / `b64decode` / `load_*_key` (memory/CPU), and the broad `except Exception` swallow that turns a meaningful failure into a silent `None` (mis-signaling that could route a caller into an unsafe default).
6. **Confused-primitive / dead-code abuser** — the unreachable `encrypt`/`decrypt` path and any other primitive whose mere presence is the risk: re-introduction hazard, test-only crypto drifting from production assumptions, or a primitive one refactor away from a reachable path.

## Methodology — multi-agent Workflow fan-out

The audit is executed (during the subsequent implementation plan) as a Workflow with three phases, mirroring the prior three audits. **Running the Workflow requires the user's explicit opt-in at execution time; this design phase produces only documents.**

1. **Discover (fan-out):** one analyst agent per adversary category, each given the in-scope file set, the trust-boundary razor, and its category lens. Each traces concrete attack attempts through the code and returns structured candidate findings (attack, code path with `file:line`, precondition, impact, proposed severity).
2. **Verify (adversarial):** for each candidate finding, independent agents attempt to **refute** it — is the impact real, or is it already bounded by pyca's loader guarantees, the `key_size` check, the address self-certification in `verify`, the base64-canonicalization check, or a fail-closed validator? A finding survives only if refutation fails. This kills the common false positives (e.g., "a degenerate key is accepted" when pyca's loader already rejects it, or "a weak self-owned wallet" with no cross-address impact in a permissioned, address=hash(pubkey) model).
3. **Synthesize:** dedupe survivors across categories, assign final severities, and compose the report.

## Severity rubric

Same Critical/High/Medium/Low scale as the prior audits. For **cryptographic findings**, severity is graded on:

- **Forgeability / impact** — signature forgery, address impersonation, or private-key recovery (Critical/High) vs. an unpinned default, dead-code, or error-signal-hygiene issue (Low).
- **Reachability** — reachable from remote, any-authenticated input (the `signing.verify` / `schema.validate_*` public-key path, the transaction-signature path) ⇒ higher; operator-local only (wallet-file export, CLI) ⇒ lower.
- **Blast radius** — system-wide identity/signature trust (any address, any node) vs. a single self-owned wallet whose compromise harms only its owner. In a permissioned, `address = hash(pubkey)` model, an attacker degrading *their own* key is usually self-harm, not a finding; binding to or forging for a *different* address is the high-severity shape.

  **Self-harm carve-out — two exclusions (do not let the verify phase kill these).**
  1. **Third-party forgeability is NOT self-harm.** A degenerate key whose *signature scheme becomes forgeable by anyone* (the classic small-public-exponent + PKCS1v15 cube-root forgery, e.g. `e=3`) lets a third party sign as that address **without the private key** — i.e. spend/act as the address's owner. If the system admits such a key onto the chain, that is High (theft-enabling), even though "only" one address is affected. The owner chose the key, but the forgeability harms the owner (and any counterparty trusting that address), not just an abstract self.
  2. **Cross-node wedge is NOT self-harm.** A self-signed-but-degenerate transaction that validates under one `validate_signature` path but raises/diverges on another node — desyncing milling or mempool acceptance — reaches into *other* nodes' state. Grade on the cross-node impact, not the originating address.

## Deliverable / output format

- **Audit report:** `docs/superpowers/audits/2026-06-02-wallet-crypto-audit.md`, structured like the prior three — executive summary with the `N Critical / N High / N Medium / N Low` headline, per-adversary attack traces, a findings table (id, adversary, severity, description, status, demonstration test), cross-cutting observations (including confirmed *strengths* worth recording — address self-certification in `verify`, base64 canonicalization in `validate_base64`, the `base58check` 4-byte checksum on address decode (`wallet.py:31`) hardening format-only validation, OAEP for encryption, fail-closed validators, key-size enforcement), and a Recommendations section.
- **Demonstration tests:** a new `tests/test_wallet_audit.py`, one `@pytest.mark.xfail(strict=True)` test per finding (strict mode forces the marker's removal as part of each remediation PR).
  - **Correctness findings** follow the established pattern: the test asserts the buggy behavior under `xfail` (e.g., a degenerate-exponent key loads and verifies; `validate_signature` returns `True` on a malleated input), flipping to a passing regression when the fix lands.
  - **Hygiene / dead-code / unpinned-default findings** use a **bounded-state-assertion** convention: assert the present, observable state deterministically — e.g., the `encrypt`/`decrypt` symbols exist but have no `src/` reference (import-graph assertion), or an exported encrypted PEM carries the unpinned default KDF marker. **No test ever attempts real key cracking, brute force, or RSA/AES cryptanalysis** — bounds are chosen for fast, deterministic runs.
- **Test fixtures:** reuse `tests/conftest.py` wallets (now 3072-bit) and helpers; construct adversarial keys with `cryptography` directly inside the test where a malformed/degenerate key is needed.

## Close-out flow

Each finding is remediated individually after the audit lands, one per cycle: brainstorm → spec → implementation plan → subagent-driven execution, through the internal cross-model review loop (different-model reviewers to convergence) followed by exactly one Copilot backstop on the PR. Each remediation flips its strict-xfail demonstration into a passing regression and updates the report's headline, driving the audit to **0 Critical / 0 High / 0 Medium / 0 Low**. A roadmap entry under "Audit remediation" tracks open findings.

## Non-goals

- Remediation itself (this spec covers producing the audit; fixes are separate cycles).
- Re-auditing the verification, auth, or networking layers.
- Cryptanalysis of RSA-3072, SHA-2, or AES-GCM (assumed sound).
- Hardening against a malicious *operator* of this node holding their own private keys (the threat actor is a remote supplier of key material / signatures / addresses, plus an attacker against exported at-rest key material).

## Acceptance criteria for this design

- Scope, trust boundaries, and the scope razor are unambiguous: every candidate finding can be classified as in-scope, cross-reference-only (trusted boundary), or out-of-scope.
- The six adversary categories cover the in-scope surface with no obvious gap.
- The methodology is the approved three-phase multi-agent fan-out (run under explicit opt-in during the impl plan).
- The deliverable shape (report + `tests/test_wallet_audit.py` with the strict-xfail + bounded-state-assertion conventions) matches the prior audits' proven format.
- The audit performs no real key cracking or cryptanalysis when its demonstration tests run.
