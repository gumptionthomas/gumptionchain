# EGU #251 — Social Binding Envelope Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the base-side primitives for wallet↔social bindings: a
`gc-msg-v1` claim envelope (`{platform, handle, proof_url?}`), Python
sign/parse/verify, mirrored JS in the wallet ESM, byte-parity + vector
tests, and the public envelope spec doc.

**Architecture:** Per the approved spec
(`docs/superpowers/specs/2026-06-10-egu-251-social-binding-envelope-design.md`):
no new scheme — bindings reuse `gc-msg-v1` exactly as stake attestations do.
New functions live beside the stake family in
`src/gumptionchain/attestation.py` and `clients/wallet/gc-attestation.mjs`.
`verify_binding` is pure (shape + signature; never fetches `proof_url`).

**Tech Stack:** Python (stdlib `json`/`re` + existing `message.py` /
`wallet.py` primitives), browser-grade ESM (WebCrypto via `gc-wallet.mjs`),
pytest with Node-subprocess parity harness. Gates:
`uv run ruff format --check src tests`, `uv run ruff check src tests`,
`uv run mypy`, `uv run pytest`. No schema change; `db check` unaffected.

**Branch:** `feat/egu-251-social-binding-envelope` off `main` (after this
docs PR merges). Single implementation PR.

**Verified-in-code facts the implementer needs:**

- `attestation.py` template: `_validate_claim` (raises `BadAttestationError`
  with single-purpose messages), `build_stake_message` (ordered dict →
  `json.dumps(..., separators=(',', ':'), ensure_ascii=False)`),
  `sign_stake_attestation` (wraps `sign_message`), `parse_stake_attestation`
  (json-load + validate + canonical-reconstruction equality check),
  `verify_stake` (catches `BadProofError` → re-raises `BadAttestationError`;
  reasons `'expired'`/`'bad-signature'` chosen via
  `sig.get('reason') == 'expired'`).
- `message.py`: `sign_message(wallet, message, timestamp=None)`,
  `verify_message(proof, max_age=None, now=None)`, `to_armored`/
  `from_armored`. `BadProofError` importable from `gumptionchain.message`.
- JS mirror: `clients/wallet/gc-attestation.mjs` — `validateClaim` uses
  `!== undefined` for off-side key presence; `buildStakeMessage` relies on
  `JSON.stringify` default separators matching Python's compact form;
  `parseStakeAttestation` enforces canonical equality;
  `verifyStake(proof, {fetchProvenance, maxAge, minConfirmations})`.
- CLI harness: `clients/wallet/attestation-cli.mjs` — `process.argv[2]` is
  the mode, `process.argv[3]` is a JSON arg; existing modes `build` / `sign`
  / `verify`. Add `build-binding` / `sign-binding` / `verify-binding`.
- Parity tests spawn `node` via `subprocess.run([..., mode, json.dumps(p)])`
  and `@pytest.mark.skipif(shutil.which('node') is None, ...)`; the shared
  fixed key is `VECTOR_WALLET_B58` from `tests/test_browser_wallet_vectors.py`.
- Vector fixtures live in `clients/wallet/testdata/` (e.g.
  `gc-attestation-vectors.json`), regenerated when `GC_REGEN_VECTORS=1`;
  vector dicts store `{claim, timestamp, message, signature, address}`.
- `scripts/sync_wallet.py` copies `clients/wallet/*.mjs` →
  `src/gumptionchain/static/wallet/`, excluding `*.test.mjs` and `*-cli.mjs`.
  Run it after editing `gc-attestation.mjs`; the vendored copy is part of
  the diff.
- Platform regex: keep one source of truth per language —
  `_PLATFORM_RE = re.compile(r'[a-z0-9-]{1,32}')` with `.fullmatch`
  (Python) and `/^[a-z0-9-]{1,32}$/` (JS), like `_TXID_RE`/`TXID_RE`.
- Ruff: 80-col, single quotes; mypy strict (annotate `dict[str, Any]`
  returns; `parse_social_binding` needs the same
  `# type: ignore[no-any-return]` as `parse_stake_attestation` if returning
  a `json.loads` product).

---

### Task 1: Python — claim validation + canonical message + sign/parse (TDD)

**Files:** modify `src/gumptionchain/attestation.py`, `tests/test_attestation.py`

- [ ] **Step 1 (RED):** failing tests in `tests/test_attestation.py`:
  canonical bytes for minimal claim
  (`{"platform":"github","handle":"gumptionthomas"}`), field order with
  `proof_url`, UTF-8 handle unescaped; validation rejections (uppercase /
  33-char / empty platform; empty / 257-char handle; `http://` and
  non-string `proof_url`; non-dict claim); sign→parse round-trip;
  parse rejections (extra key e.g. `txid`, reordered keys, whitespace,
  `\u`-escaped Unicode, missing message). Also: stake parser rejects a
  binding proof and vice versa (domain-separation pin).
- [ ] **Step 2:** watch them fail (ImportError / missing functions).
- [ ] **Step 3 (GREEN):** implement `_validate_binding_claim`,
  `build_binding_message`, `sign_social_binding`, `parse_social_binding`
  mirroring the stake functions.
- [ ] **Step 4:** full attestation test file green.

### Task 2: Python — `verify_binding` (TDD)

**Files:** modify `src/gumptionchain/attestation.py`, `tests/test_attestation.py`

- [ ] **Step 1 (RED):** verdict tests — valid proof →
  `{valid: True, checks: {'signature': True}, signer, claim, reasons: []}`;
  tampered message → `bad-signature`; `max_age` exceeded → `expired`;
  malformed envelope (no `public_key`) → `BadAttestationError`; signer
  reported from `proof['address']`.
- [ ] **Step 2 (GREEN):** implement, mirroring `verify_stake`'s
  signature-check block (incl. `BadProofError` → `BadAttestationError`).

### Task 3: JS mirrors + CLI modes

**Files:** modify `clients/wallet/gc-attestation.mjs`,
`clients/wallet/attestation-cli.mjs`

- [ ] **Step 1:** `validateBindingClaim`, `buildBindingMessage`,
  `signSocialBinding`, `parseSocialBinding`, `verifyBinding` — exact
  mirrors (off-side/extra handling via canonical equality; `!== undefined`
  presence checks; `PLATFORM_RE` anchored).
- [ ] **Step 2:** CLI modes `build-binding` (stdout canonical string),
  `sign-binding` (`{private_key_b58, claim, timestamp}` → proof JSON),
  `verify-binding` (`{proof, maxAge?}` → verdict JSON).
- [ ] **Step 3:** validated by Task 4's parity tests (no separate JS test
  runner in this repo's suite).

### Task 4: Parity + vectors (TDD against the JS just written)

**Files:** modify `tests/test_attestation_parity.py`,
`tests/test_attestation_vectors.py`; create
`clients/wallet/testdata/gc-binding-vectors.json`

- [ ] **Step 1:** parity — `_node('build-binding', CLAIM) ==
  build_binding_message(CLAIM)` with UTF-8 handle `'tøm'` and a
  `proof_url` claim; JS `sign-binding` → Python
  `verify_binding(...)['valid']`; Python `sign_social_binding` → JS
  `verify-binding` verdict valid.
- [ ] **Step 2:** vectors — three cases (minimal, `proof_url`, UTF-8
  handle) with fixed timestamps; generate via `GC_REGEN_VECTORS=1
  uv run pytest tests/test_attestation_vectors.py`; commit the fixture;
  verify a clean run validates signatures against the committed bytes.

### Task 5: Vendor sync + public spec doc

**Files:** run `uv run python scripts/sync_wallet.py`; create
`docs/social-binding-envelope.md`

- [ ] **Step 1:** sync; confirm `git status` shows only the expected
  vendored `static/wallet/gc-attestation.mjs` change.
- [ ] **Step 2:** write `docs/social-binding-envelope.md` per the spec's
  Docs section (claim table, canonical rules, armored statement, worked
  example with the vector wallet + fixed timestamp, bidirectional model:
  base checks vs directory-service checks).

### Task 6: Gates + PR

- [ ] `uv run ruff format --check src tests && uv run ruff check src tests`
- [ ] `uv run mypy`
- [ ] `uv run pytest` (full suite, SAWarning gate active)
- [ ] PR `feat(identity): wallet↔social binding envelope — sign, parse,
  verify (#251)`; subagent review; hold for author review.
