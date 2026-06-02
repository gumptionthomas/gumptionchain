# Wallet & Cryptographic-Primitives Threat-Modeled Audit

**Date:** 2026-06-02
**Surface:** the cryptographic root of trust — `wallet.py` primitives, the `cc-sig-v1` primitives in `signing.py`, and the key-deserialization validators in `schema.py`.
**Design:** [`specs/2026-06-02-wallet-crypto-audit-design.md`](../specs/2026-06-02-wallet-crypto-audit-design.md) — scope razor, adversary categories, severity rubric (incl. the self-harm carve-out exclusions).
**Method:** three-phase multi-agent fan-out — 6 analyst agents (one per adversary category) → de-dup → 3 adversarial refuters per candidate (survives only if a majority fail to refute) → synthesis. **26 raw candidates → 8 canonical → 0 surviving exploitable findings.**

## Executive summary

**0 Critical / 0 High / 0 Medium / 2 Low** (at audit time). **Remediation progress: WC1 closed; 1 Low (WC2) open.**

The fan-out produced **no exploitable cryptographic findings**. The crypto root of trust is well-constructed: identity is bound to the public key by a sound double-hash, that binding is re-certified before every signature check on both the transaction and the request-signing paths, signature verification delegates to pyca's strict full-width PKCS#1 v1.5 verifier (foreclosing small-exponent forgery), and key import is fail-closed (malformed material becomes `InvalidKeyError`, never a silent fresh key). Twelve such controls are recorded as **confirmed strengths** below.

Adversarial verification refuted all eight de-duplicated candidates as exploitable findings. Two of them, however, carry a **real, non-exploitable defense-in-depth / hygiene residual** that the refuters themselves graded "Low," and that the prior audits' bar tracks as remediation items. They are recorded here as **Low findings** with strict-xfail demonstration tests — explicitly *not* vulnerabilities, but cheap, in-scope hardening (the design scoped both "unreachable/bespoke crypto" and "imported-key parameter validation" as targets):

- **WC1 (Low)** — dead bespoke `encrypt`/`decrypt` hybrid (zero production callers post-#111).
- **WC2 (Low)** — no public-exponent floor on imported keys (`e=3` loads and is accepted).

## Findings table

| id | adversary | severity | description | status | demonstration test |
|---|---|---|---|---|---|
| WC1 | confused-primitive / dead-code | Low | `Wallet.encrypt`/`Wallet.decrypt` (bespoke RSA-OAEP + AES-GCM hybrid) have **zero `src/` callers** after PR #111 removed the challenge/response handshake — only tests exercise them. Unreachable bespoke crypto is a re-introduction hazard and standing maintenance/attack surface. Not exploitable (unreachable). | ✅ Remediated — methods + `AESGCM`/nonce-size constants removed; `test_wc1_bespoke_encrypt_decrypt_removed` is now a passing regression. | `test_wc1_bespoke_encrypt_decrypt_removed` (regression) |
| WC2 | malicious key supplier | Low | `Wallet.__init__` validates `isinstance(RSA*)` + `key_size == 3072` but **does not validate the public exponent**. A 3072-bit `e=3` key loads and is accepted (pyca does not enforce a minimum exponent). Not exploitable today — pyca's strict PKCS#1 v1.5 verifier forecloses cube-root forgery, and a self-chosen weak key derives its own distinct address (no cross-address/cross-node impact) — but accepting non-standard exponents is unnecessary and inconsistent with the node's own `e=65537` generation. | Open (Low) | `test_wc2_import_rejects_degenerate_exponent` (xfail) |

## Adversary traces

Six adversary lenses (design §"Adversary categories") each traced concrete attacks through the actual code. 26 raw candidates collapsed to 8 canonical, all refuted as exploitable:

1. **Malicious key supplier (A1).** Traced degenerate exponents, wrong algorithm, wrong size, garbage DER, encrypted-without-passphrase, oversized modulus. *Wrong-algorithm / wrong-size / garbage* are all caught fail-closed by `isinstance(RSA*)` + `key_size == 3072` (`wallet.py:130-133`). *Oversized modulus* is rejected by the size check before cryptographic use. The one real gap — **no public-exponent validation** — is **WC2**; its proposed High (third-party cube-root forgery) was empirically refuted against pyca's strict verifier (a 3072-bit `e=3` key loads but yields no forgeability, and a weak self-chosen key gets its own distinct hash-derived address), leaving a Low defense-in-depth residual.
2. **Signature forger / malleability (A2).** Traced PKCS#1 v1.5 forgery (incl. with a degenerate key), base64 non-canonical encodings, length/empty/`None` confusion, unbounded `int(ts)`. All refuted: pyca's strict EMSA-PKCS1-v1_5 verifier (`wallet.py:195`) forecloses small-exponent forgery; `validate_base64`'s round-trip rejects non-canonical encodings; `validate_signature` is fail-closed on every parse/verify error; `int(ts)` is `try/except`-guarded and Python ints don't overflow. `_canonical` binds method/path/query/body-digest/node-host/timestamp/address.
3. **Impersonation / address-collision (A3).** Traced second-preimage on `sha256(sha512(pubkey_DER))`, the `validate_address_format` vs `validate_address` split, and the **`schema.validate_signature` key→address binding seam**. The seam is real (`schema.validate_signature` checks the signature but not the binding) but **fully bounded** by its only caller: `Transaction.validate_signature` (`transaction.py:219`) runs *after* Pydantic `model_validate` (`transaction.py:229`), which enforces `validate_pk_address` (`transaction.py:95-99`). Recorded as an observation, not a finding (see below).
4. **Key-confidentiality (A4).** Traced the unpinned KDF behind `BestAvailableEncryption`, the hybrid `encrypt`/`decrypt` confidentiality/integrity, AES-GCM nonce generation, and plaintext-key exposure via `__repr__`/`to_dict`/`to_json`. All refuted as findings: KDF default is a sound PBKDF2/AES scheme and the exposure is operator-local self-harm over the operator's own at-rest key (an explicit non-goal); `to_dict`/`to_json` have no reachable production sink; `__repr__` emits only the address. The unpinned-KDF point is recorded as an observation.
5. **Deserialization / resource abuse (A5).** Traced oversized encodings (memory/CPU) and the broad `except Exception` in `import_key`. The swallow is fail-closed: a supplied-but-unparseable key becomes `None` → `InvalidKeyError`; fresh generation is reachable *only* when all key args are `None`, so a bad key can never be conflated with "no key." Oversized input is bounded by the post-load size check.
6. **Confused-primitive / dead-code (A6).** Confirmed `encrypt`/`decrypt` are dead (**WC1**) and that no production code uses a `Wallet` as a dict key / set member (`__hash__ = None` is therefore harmless today). The `decrypt` framing length-confusion candidate folded into WC1 — AES-GCM is AEAD and fails closed on any mis-framing, and the path is unreachable, so it participates in no consensus/mempool/milling.

## Cross-cutting observations (no finding)

- **`schema.validate_signature` performs no key→address binding.** It is currently safe because every in-tree signature-validation path runs `validate_pk_address` first (`transaction.py:95-99`, inherited by both `RegularTransactionModel` and `CoinbaseTransactionModel`). This is a **load-bearing invariant held by an upstream caller, not by the function itself** — a future caller that validates a signature without first running `validate_pk_address` would reintroduce an impersonation seam. Consider documenting the invariant at `schema.validate_signature`, or folding a binding assertion into it, when that code is next touched. No finding; defense-in-depth only.
- **Unpinned KDF on encrypted exports.** `BestAvailableEncryption` (`wallet.py:57,166`) delegates KDF/cipher choice to pyca's default (currently a sound PBKDF2/AES scheme). Brute-force resistance of an encrypted exported wallet is therefore unpinned and tied to the pyca version. This is operator-local (the operator's own at-rest key) and below the finding bar, but worth pinning explicitly if encrypted-wallet portability across pyca versions ever matters.

## Confirmed strengths

The audit records what is sound, not only what is open. Twelve controls were confirmed by tracing:

1. **Key→address binding before signature check (transactions)** — `transaction.py:95-99` + `:229/:232`. `model_validate` runs `validate_pk_address` (recomputes `address = hash(pubkey_DER)`, rejects mismatch) *before* `validate_signature`, on every path including `block.py:294/308`.
2. **Address self-certification before verification (`cc-sig-v1`)** — `signing.py:117-119`. `verify()` asserts `wallet.address == address` before any RSA verify; a signature-valid key for a *different* claimed address is rejected.
3. **Base64 round-trip canonicalization** — `schema.py:53-58`. `b64encode(b64decode(s)) == s` rejects URL-safe/unpadded/non-canonical encodings fail-closed, removing a wire-format malleability surface.
4. **Base58check 4-byte checksum on address decode** — `wallet.py:30-31` via `schema.py:35-50`. ~2⁻³² corruption/truncation detection, defense-in-depth beyond the 32-byte length check.
5. **Key-size enforcement on every key** — `wallet.py:130-133`. Fail-closed reject of any non-RSA or non-3072-bit key; blocks undersized keys and OOM-inducing oversized moduli from cryptographic use.
6. **Fail-closed key import** — `wallet.py:65-94` + `:120-133`. Malformed material → `InvalidKeyError`, never a silent fresh key; fresh generation reachable only when all key args are `None`.
7. **Strict PKCS#1 v1.5 verifier** — `wallet.py:185,195` (via `signing.py:129`, `schema.py:79`). pyca's strict full-width EMSA-PKCS1-v1_5 forecloses the small-exponent cube-root / Bleichenbacher forgery that only works against lenient verifiers.
8. **`validate_signature` fail-closed on all error modes** — `wallet.py:188-207`. `InvalidSignature`/`binascii.Error`/`ValueError`/`TypeError` → `False`; no parse anomaly yields a false-positive accept.
9. **Sound address derivation** — `wallet.py:156` (`mill_hash_bin`, `milling.py:36-37`). `sha256(sha512(pubkey_DER))`; any key-parameter change changes the DER, the hash, and the address — a weak key cannot be bound onto a victim's address without a hash second-preimage.
10. **Strict timestamp parsing + freshness window** — `signing.py:103-111`. Non-integer timestamps rejected; arbitrary-precision ints (no overflow); ±300 s replay bound.
11. **Safe public exponent on generation** — `wallet.py:127-129`. `e=65537`, 3072-bit, attacker-uninfluenced; any fallback fresh key is sound.
12. **RSA-OAEP (MGF1-SHA256) in the hybrid helpers** — `wallet.py:211-217,231-239`. The (currently dead — see WC1) hybrid uses OAEP not PKCS1v15 encryption, with a fresh `os.urandom(12)` nonce. Strength contingent on the path staying dead or being re-reviewed at re-enablement.

## Recommendations

- **WC1 — remove the dead `encrypt`/`decrypt` hybrid** (and its tests). It has no production caller and its presence is the only risk (re-introduction / drift). If a future hybrid-encryption need is anticipated, the alternative is *document-and-accept* with an explicit "not for production use until re-reviewed" marker — but removal is preferred (smallest surface). Demonstration `test_wc1_bespoke_encrypt_decrypt_removed` flips to passing once the methods are gone.
- **WC2 — enforce a public-exponent check on import.** Reject any imported key whose public exponent is not `65537` (matching the node's own generation), in `Wallet.__init__` alongside the existing `key_size` check. Cheap, standards-aligned, and makes imported keys match the node's key profile. Demonstration `test_wc2_import_rejects_degenerate_exponent` flips to passing once the check rejects `e=3`.
- **Observations** (schema-binding invariant, unpinned KDF) are below the finding bar; address opportunistically when that code is next touched.

Each finding is remediated in its own cycle (brainstorm → spec → plan → execution, internal cross-model review to convergence, one Copilot backstop), flipping its strict-xfail demonstration to a passing regression and driving the audit toward **0 / 0 / 0 / 0**. Tracked under "Audit remediation — wallet/crypto findings" in the roadmap.
