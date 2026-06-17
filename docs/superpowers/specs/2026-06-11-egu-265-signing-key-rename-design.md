# EGU #265 — the complete wallet → signing-key rename

**Date:** 2026-06-11
**Issue:** #265 (base) + a hub follow-up PR (gumption-hub)
**Status:** design approved

## Goal

Retire the word **wallet** from both repos entirely — internals included.
This is an open-source protocol project; the internals are part of the
product, and "wallet" is the last finance-vocabulary vestige (the
CancelChain lesson). `signing_key` is an exact, honest replacement: the
thing IS an RSA signing keypair.

Decisions locked in brainstorm:

1. **`signing-key` everywhere** — no abbreviated surfaces. Code
   identifiers `SigningKey`/`signing_key`, CLI group `signing-key`
   (`gc signing-key create`), route `/signing-key`, env
   `GC_SIGNING_KEY_DIR`, directories `clients/signing-key/` and
   `static/signing-key/`.
2. **Clean break on persisted browser names** — pre-launch is the only
   time this is possible. The IndexedDB database name, the trust-ack
   localStorage key, and the keyring record/backup field `wallet_ct` all
   rename. Anyone with a saved key re-creates or re-imports once
   (`.pem`/backups unaffected as key material; old backups become
   incompatible with the new field name — acceptable, same clean break).
3. **Timing:** both PRs land before the gcm-01 re-provision and the
   first `v*` tag (runbook §8), so the fleet and the public HOWTO are
   born with the new names.

## The mapping

### Base — Python

| Old | New |
|---|---|
| `src/gumptionchain/wallet.py` | `src/gumptionchain/signing_key.py` |
| `class Wallet` | `class SigningKey` |
| `app.wallets` | `app.signing_keys` |
| `read_wallets` (application.py) | `read_signing_keys` |
| `WALLET_DIR` config attr / `GC_WALLET_DIR` env | `SIGNING_KEY_DIR` / `GC_SIGNING_KEY_DIR` |
| CLI group `wallet` (`wallet_cli`) | `signing-key` (`signing_key_cli`) |
| `milling_wallet`, `wallet=` params, locals | `milling_signing_key`, `signing_key=` (mechanical) |
| conftest `READER_WALLET` etc. | `READER_SIGNING_KEY` etc. |
| any test file with `wallet` in its name | mechanical rename (`test_wallet_page` → `test_signing_key_page`, `test_browser_wallet_vectors` → `test_browser_signing_key_vectors`, `test_wallet_vendored` → `test_signing_key_vendored`, `test_wallet_audit` → `test_signing_key_audit`, …) |

Derived identifiers follow mechanically (any `wallet` substring in an
identifier becomes `signing_key`, preserving case convention). Error
classes, docstrings, comments, and log messages included.

### Base — web

| Old | New |
|---|---|
| route `/wallet`, endpoint `wallet_view` | `/signing-key`, `signing_key_view` |
| `templates/wallet.html` | `templates/signing_key.html` (copy becomes key-language in the same pass — this absorbs the former "browser copy sweep") |
| nav label (base + page titles) | `Signing key` |

### Base — JS/ESM

| Old | New |
|---|---|
| `clients/wallet/` | `clients/signing-key/` |
| `src/gumptionchain/static/wallet/` (vendored) | `src/gumptionchain/static/signing-key/` |
| `scripts/sync_wallet.py` | `scripts/sync_signing_key.py` |
| `gc-wallet.mjs`, JS `class Wallet` | `gc-signing-key.mjs`, `class SigningKey` |
| `wallet-glue.mjs` / `wallet-session.mjs` / `wallet-passkey.mjs` | `signing-key-glue.mjs` / `signing-key-session.mjs` / `signing-key-passkey.mjs` |
| `wallet-glue` exports (`readTrustAck` consumers update imports) | unchanged names, new module path |
| keyring record field `wallet_ct`; gc-backup payload field | `signing_key_ct` (clean break; bump the record `VERSION` and the backup format version so old artifacts fail loudly, not weirdly) |
| IDB `dbName 'gc-wallet'` | `'gc-signing-key'` |
| `TRUST_ACK_KEY 'gc-wallet-trust-ack-v1'` | `'gc-signing-key-trust-ack-v1'` |
| JSDoc/comments/CLI harness names (`*-cli.mjs` doc headers) | mechanical |

### Base — docs, deploy, config surface

- Active docs rename: `docs/howto-miller-pi.md` (wallet ceremony →
  signing-key ceremony, `GC_SIGNING_KEY_DIR`, CLI invocations),
  `docs/pi-appliance-runbook.md`, `docs/api-auth-protocol.md` ("wallet
  signature" → "signing-key signature" prose; **scheme strings and
  canonical formats unchanged**), `docs/social-binding-envelope.md`
  prose, `docs/ui-extension-seam.md`, `README`, `CLAUDE.md`.
- `deploy/pi/`: `WALLET_DIR` variable in `provision-appliance.sh`, env
  examples in docs, custom.toml comments.
- `.env`/test env examples: `GC_SIGNING_KEY_DIR`.

### Hub (separate PR, after base merges + pin bump)

| Old | New |
|---|---|
| `WalletBinding`, table `wallet_binding` | `SigningKeyBinding`, table `signing_key_binding` (greenfield: drop/recreate dev DB; no migration) |
| `from gumptionchain.wallet import Wallet` etc. | `signing_key` / `SigningKey` |
| static imports `/static/gumptionchain/wallet/...` | `/static/gumptionchain/signing-key/...` |
| `url_for('browser.wallet_view')` | `url_for('browser.signing_key_view')` |
| nav dropdown item `Wallet` | `Signing key` |
| residual hub copy ("manage a wallet", bind/me pages) | key-language |
| `gc-handle:` localStorage key | unchanged (no wallet in it) |

## Invariants — what does NOT change

- **Protocol strings and canonical formats**: `gc-sig-v1`, `gc-msg-v1`,
  header names (`GC-Address`, …), claim schemas, armored labels,
  signature algorithms. Verified to contain no "wallet".
- **Key material**: `.pem` files, addresses, base58 exports.
- **`docs/superpowers/specs/` and `plans/`**: dated historical records —
  rewriting history is falsification, not cleanup. They keep their
  original wording. (This spec is the record of the rename itself.)
- Git history, issue/PR text, CHANGELOG-like artifacts.
- The `gc` CLI alias, `GRAIN_PER_GRIT`, GRIT/grains vocabulary.

## Risks and their answers

- **Mechanical-rename misses or over-reaches** (e.g. renaming inside a
  vendored lib or a test vector value): the final gate is
  `grep -ri wallet` over each repo returning ONLY `docs/superpowers/`
  history (and `.git/`); test fixtures with embedded signatures are
  re-validated by the vector tests, which never contained the word.
- **Broken intermediate states**: one atomic base PR; the hub pins a
  base rev, so the hub is never broken (it just lags until its PR).
- **Saved browser keys vanish** (clean break): accepted; affected
  population ≈ the author. The new record `VERSION`/backup version make
  stale artifacts fail with a clear error rather than silently.
- **gcm-01's `.env`** uses `GC_WALLET_DIR`: it gets the new name during
  the runbook §8 re-provision, which is intentionally sequenced after
  this rename.

## Testing

- Full pytest + `node --test` suites pass post-rename (they gate the
  mechanical correctness: imports, fixtures, parity vectors, templates).
- The `grep -ri wallet` gate above, enforced as a small pytest test
  (`test_no_wallet_vocabulary`) that walks `src/`, `clients/`, `tests/`,
  `deploy/`, active `docs/*.md` — so the word can never creep back.
- Hub: same gates + its own grep test; manual smoke of bind/share flows
  against the renamed base static paths.

## PR decomposition

1. This docs PR (spec + plan).
2. **Base PR** — the atomic rename (#265).
3. **Hub PR** — pin bump + hub-side rename.
4. Post-merge: update CLAUDE.md-adjacent memory notes (the vocabulary
   contract memory gains "wallet retired from internals too").
