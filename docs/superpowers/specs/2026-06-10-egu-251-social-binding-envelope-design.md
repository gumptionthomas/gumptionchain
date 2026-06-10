# EGU #251 — wallet↔social binding envelope (base side of hub#17)

**Date:** 2026-06-10
**Issue:** #251 (base half of gumptionthomas/gumption-hub#17, under the EGU #3
federated-identity umbrella #153)
**Status:** design proposed

## Goal

Define the **claim format and crypto** for binding a wallet address to a
social handle — the stateless truth primitives every node and EGU app must
agree on byte-for-byte. The hub keeps the stateful half (fetching the
social-side post, SSRF guard, binding storage in the proof store, revocation
UX, handle display): same boundary as verify (base) vs proof store (hub).

A binding is a **Keybase-style bidirectional proof**:

1. **Wallet side (this spec):** the wallet signs a claim
   `{platform, handle, proof_url?}` as a `gc-msg-v1` proof.
2. **Social side (hub#17):** the handle's account posts the armored proof at
   a publicly fetchable URL under its control (gist, Mastodon post,
   `rel="me"` page).
3. Verifiers check both directions; base ships direction 1's primitives only.

## Non-goals

- Fetching/validating `proof_url` (hub: outbound HTTP, SSRF allowlist).
- Binding storage, registry, revocation UX, handle display (hub).
- On-chain anchoring (possible later via EGU #3 attestation txns;
  off-chain-first keeps bindings revocable and chain-spam-free).

## Envelope: a `gc-msg-v1` message, like stake attestations

No new scheme. Exactly as `sign_stake_attestation` wraps `sign_message`, a
binding is a `gc-msg-v1` proof whose `message` is a canonical claim JSON.
Everything inherited from `gc-msg-v1` stays inherited: RSASSA-PKCS1-v1_5 /
SHA-384, address self-certification, the armored copy-paste form, and the
canonical string `gc-msg-v1\n1\n<address>\n<timestamp>\n<sha256(message)>`.

### Claim schema

| Field       | Required | Validation                                          |
|-------------|----------|-----------------------------------------------------|
| `platform`  | yes      | `fullmatch([a-z0-9-]{1,32})` — lowercase identifier |
| `handle`    | yes      | non-empty `str`, ≤ 256 chars                        |
| `proof_url` | no       | when present: `str` starting `https://`, ≤ 512 chars |

- **`platform`** is an open namespace (`github`, `mastodon`, `web`, `dns`,
  …) validated by *shape* only. Which platforms a verifier accepts is policy
  (hub MVP: gist first, Mastodon second) — adding one requires no envelope
  change.
- **`handle`** syntax is platform-specific and is the hub's business
  (`gumptionthomas` vs `@tom@mastodon.social`); base enforces only
  non-empty/length so canonical bytes are well-defined.
- **`proof_url` is optional by design** (chicken-and-egg): a gist's URL
  exists only *after* the signed statement is posted, and DNS TXT proofs
  have no URL at all. The hub's binding record stores the resolved location;
  claims include it only for platforms with stable, predictable URLs.
  `https://` is required when present — an unencrypted proof location is not
  acceptable evidence.
- **No `address` field in the claim**: the wallet side of the binding is the
  envelope's own signer (`proof['address']`, self-certified by `gc-msg-v1`).
  Duplicating it invites mismatch bugs.
- **No expiry semantics**: bindings are durable, unlike API signatures.
  `verify_binding` defaults `max_age=None`; re-verification cadence (and
  treating a deleted social post as revocation) is hub policy.

### Canonical message

Mirrors `build_stake_message` exactly — ordered dict, compact JSON,
`ensure_ascii=False` (UTF-8 unescaped, byte-identical to JS
`JSON.stringify`):

```python
ordered = {'platform': claim['platform'], 'handle': claim['handle']}
if claim.get('proof_url') is not None:
    ordered['proof_url'] = claim['proof_url']
json.dumps(ordered, separators=(',', ':'), ensure_ascii=False)
```

Example: `{"platform":"github","handle":"gumptionthomas"}`

Parsing enforces **canonical reconstruction** (same as
`parse_stake_attestation`): `build_binding_message(claim) != proof['message']`
→ reject. Extra keys, reordered keys, whitespace, or escaped Unicode are all
non-canonical; JS and Python agree on accept/reject for any input.

### Domain separation from stake attestations

Structural and total: a binding claim requires `platform` + `handle` and the
canonical check rejects any extra key; a stake claim requires a 64-hex
`txid` + `kind` ∈ KINDS. Each parser rejects the other family's messages, so
no signature can be replayed across claim types. (The optional `handle` field
on *stake* claims remains what it always was — self-asserted display sugar;
this envelope is the verifiable link the hub can cross-check it against.)

## Social-side statement

What gets posted publicly is the **armored proof** (`to_armored`/`toArmored`
— already designed for copy-paste):

```
-----BEGIN GUMPTION SIGNED MESSAGE-----
{"platform":"github","handle":"gumptionthomas"}
-----BEGIN GUMPTION SIGNATURE-----
<base64 proof JSON>
-----END GUMPTION SIGNED MESSAGE-----
```

Byte-reproducible by third parties: armor the proof dict per the existing
`gc-msg-v1` armored form, no new rules. The hub's fetch step confirms the
fetched document contains an armored proof whose signature verifies and
whose claim matches the binding being registered.

## API surface

### Python — `src/gumptionchain/attestation.py` (same module: it is the EGU #3 attestation family)

| Function | Mirrors | Behavior |
|---|---|---|
| `build_binding_message(claim) -> str` | `build_stake_message` | validate + canonical JSON |
| `sign_social_binding(wallet, claim, timestamp=None) -> dict` | `sign_stake_attestation` | `sign_message(wallet, build_binding_message(claim), ...)` |
| `parse_social_binding(proof) -> dict` | `parse_stake_attestation` | shape + canonical enforcement, returns claim |
| `verify_binding(proof, max_age=None) -> dict` | `verify_stake` minus on-chain | pure: shape + signature only |

`verify_binding` returns `{valid, checks: {'signature': bool}, signer,
claim, reasons}` — the same verdict shape as `verify_stake` so the hub can
merge its own `proofside` check into `checks` and compose `valid`. Malformed
envelopes raise `BadAttestationError` (reusing the existing error types);
signature failures report `reasons` `['bad-signature']` or `['expired']`
exactly as `verify_stake` does. **It never fetches `proof_url`.**

### JS — `clients/wallet/gc-attestation.mjs` (vendored to `static/wallet/` via `scripts/sync_wallet.py`)

`buildBindingMessage(claim)`, `signSocialBinding(wallet, claim,
{timestamp})`, `parseSocialBinding(proof)`, `verifyBinding(proof,
{maxAge})` — exact mirrors, same file as the stake functions so consumers
keep a single import. `attestation-cli.mjs` gains `build-binding` /
`sign-binding` / `verify-binding` modes for the parity harness.

## Testing

Replicates the stake-attestation pattern wholesale:

- **Unit** (`tests/test_attestation.py`): claim validation table (bad
  platform charset/length, empty handle, http `proof_url`, extra keys,
  non-canonical encodings), sign→parse round-trip, `verify_binding` verdict
  paths (valid / bad-signature / expired / malformed envelope).
- **Parity** (`tests/test_attestation_parity.py`): byte-identical canonical
  (`_node('build-binding', CLAIM) == build_binding_message(CLAIM)`) with a
  UTF-8 handle (e.g. `'tøm'`); JS-signed → Python-verified and vice versa.
- **Vectors**: new `clients/wallet/testdata/gc-binding-vectors.json`
  (regen via `GC_REGEN_VECTORS=1`), exercised from
  `tests/test_attestation_vectors.py` — minimal claim, claim with
  `proof_url`, UTF-8 handle.

## Docs

`docs/social-binding-envelope.md` — public spec mirroring
`docs/api-auth-protocol.md`'s level of detail: claim table,
canonicalization rules, armored statement format, a worked example with
fixed key/timestamp, and the bidirectional-verification model (what base
checks vs what a directory service must additionally check).

## PR decomposition

1. **This docs PR** — spec + implementation plan.
2. **One implementation PR** — Python + JS + CLI modes + tests + vectors +
   vendored sync + public spec doc (the pieces are byte-coupled by the
   parity tests; splitting them would create un-mergeable intermediate
   states).
