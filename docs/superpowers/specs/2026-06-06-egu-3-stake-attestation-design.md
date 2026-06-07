# EGU #3 / #176b — composed stake verifier + attestation convention — design

**Date:** 2026-06-06
**Status:** Approved — ready for implementation
**Issue:** #184 (second slice of the verifiable stake card #176, EGU #3)
**Type:** New client module (vanilla JS / ESM) + new Python library module —
additive; no node endpoint, no schema/consensus change.

## Summary

Produce and verify a portable **stake attestation** — a "Verified on
GumptionChain" claim that composes three independent guarantees:

1. **Signature** (gc-msg-v1, #2.4) — the attestation is signed by address X.
2. **On-chain** (#176a provenance, #183) — the referenced txn is canonical.
3. **Consistency** — the signer is the on-chain staker, and an on-chain outflow
   matches the claimed kind/subject/amount.

Built in **both JS and Python** (parity, like #2.4). The verifier is
**client-side** and does **no I/O**: the node-read transport is injected
(`fetchProvenance`), so the verifier is pure and fully testable with a fake — the
node stays a dumb data source and the module, not a central server, is the
federation glue.

Depends on #2.4 (#178, message signing) and #176a (#183, provenance lookup). The
verify web page + Bluesky OG unfurl + handle-ownership proof are #176c (EGU #5),
out of scope here.

## The stake attestation

A stake attestation **is** a gc-msg-v1 proof whose `message` is the canonical
JSON of a **claim**:

```json
{ "txid": "…", "kind": "opposition", "subject": "goblins", "amount": 300, "handle": "me.bsky.social" }
```

- **`txid`** — the staking transaction's id.
- **`kind`** — one of the provenance kinds: `opposition` | `support` | `rescind`
  | `transfer`. (The verifiable-share use case centers on `opposition`/`support`,
  but the convention accepts any.)
- **`subject`** — for `opposition`/`support`/`rescind`. For `transfer`, use
  **`address`** instead (exactly one of `subject`/`address`, matching the kind).
- **`amount`** — in **grains** (integer; matches the on-chain/provenance unit
  exactly — no float; GRIT formatting is the display layer's job).
- **`handle`** — *optional* off-chain identity (e.g. a Bluesky handle). It is
  inside the signed message, so it is tamper-evident; **ownership of the handle
  is not verified here** (that bidirectional AT-Protocol check is #176c).

Because the whole claim is the signed gc-msg-v1 message, every field is
tamper-evident.

### Canonical message — byte-exact cross-language (critical)

`buildStakeMessage(claim)` MUST produce byte-identical output in JS and Python,
or signatures will not cross-verify. Rules:

- **Fixed key order:** `txid`, `kind`, then exactly one of `subject` / `address`
  (per kind), then `amount`, then `handle` **only if present**. Absent optionals
  are omitted entirely (not emitted as `null`).
- **Compact separators, no whitespace.** JS: `JSON.stringify` over an object
  literal built in that order. Python: `json.dumps(ordered_dict,
  separators=(',', ':'), ensure_ascii=False)`.
- **`ensure_ascii=False`** in Python is mandatory — JS `JSON.stringify` leaves
  non-ASCII (UTF-8 subjects/handles) unescaped; Python's default would escape
  them and break parity.
- Golden vectors (a fixed key + fixed claims) are checked by both languages to
  lock the bytes.

## API (identical shape in both languages)

- **`signStakeAttestation(wallet, claim, { timestamp? })` → proof** — validates
  the claim shape, builds the canonical message, returns
  `signMessage(wallet, buildStakeMessage(claim), { timestamp })`.
- **`parseStakeAttestation(proof) → claim`** — `verifyMessage`-independent
  structural parse: `JSON.parse(proof.message)`, validate required fields and the
  kind/subject-or-address pairing; throw **`BadAttestationError`** on malformed
  input.
- **`verifyStake(proof, { fetchProvenance, maxAge?, minConfirmations? }) →
  verdict`** — the composed check. `fetchProvenance(txid)` is an injected async
  function returning the #176a provenance object (or `null`/throwing on 404).
  The verifier performs no network/db I/O itself.

`fetchProvenance` contract: `async (txid: string) => provenance | null`, where
`provenance` is the #176a response `{ address, outflows:[{kind,
subject|address, amount, …}], status, confirmations, … }`. Returning `null`
(or a thrown not-found) means unknown txid.

## Verdict

```json
{
  "valid": true,
  "checks": { "signature": true, "onchain": true, "consistent": true },
  "signer": "GC…GC",
  "claim": { "txid": "…", "kind": "opposition", "subject": "goblins", "amount": 300 },
  "provenance": { "...#176a object..." },
  "confirmations": 5,
  "reasons": []
}
```

Check semantics (evaluated in order; later checks still run so `reasons`
accumulates a full picture):

1. **signature** — `verifyMessage(proof)` returns `valid: true` **and** its
   `address` equals `proof.address`. If `maxAge` is supplied it is passed through
   to `verifyMessage`. Failure → `reasons += 'bad-signature'` (or `'expired'`).
2. **onchain** — `prov = await fetchProvenance(claim.txid)`. `prov` exists and
   `prov.status === 'canonical'`. If `minConfirmations` is supplied,
   `prov.confirmations >= minConfirmations`. Failures →
   `'txn-not-found'` / `'not-canonical'` / `'insufficient-confirmations'`.
3. **consistent** — only meaningful when signature+onchain hold: `prov.address
   === signer` (staker is the signer) **and** at least one outflow in
   `prov.outflows` matches the claim — same `kind`, same `subject` (stake kinds)
   or `address` (transfer), same `amount`. Failures → `'signer-not-staker'` /
   `'claim-mismatch'`.

`valid = signature && onchain && consistent`. `confirmations` mirrors
`prov.confirmations` (or `0`/absent). The **verdict is data** — verification
failures return `valid:false` with `reasons`; only structurally malformed input
throws (`BadAttestationError`), mirroring `verifyMessage`'s split. The claim a
caller sees in the verdict comes from the *signed* `proof.message` (not a
caller-supplied claim), so it cannot be spoofed.

## Components

### JavaScript (`clients/wallet/`)

- **`gc-attestation.mjs`** (new) — `buildStakeMessage`, `signStakeAttestation`,
  `parseStakeAttestation`, `verifyStake`. Pure; reuses `gc-message`
  (`signMessage`/`verifyMessage`).
- **`gc-errors.mjs`** (modified) — add `BadAttestationError` (re-exported from
  `gc-attestation`).
- **`index.mjs`** (modified) — re-export the new public surface
  (`signStakeAttestation`, `parseStakeAttestation`, `verifyStake`,
  `BadAttestationError`); bump package `version`.

### Python (`src/gumptionchain/`)

- **`attestation.py`** (new) — `build_stake_message`, `sign_stake_attestation`,
  `parse_stake_attestation`, `verify_stake`, `BadAttestationError` (subclass of
  a module error). Reuses `message.py` (`sign_message`/`verify_message`).
  `verify_stake(proof, fetch_provenance, max_age=None, min_confirmations=None)`
  where `fetch_provenance` is a `Callable[[str], dict | None]`.

## Testing

- **JS (`node --test`, zero npm):**
  - `buildStakeMessage` byte-exactness (fixed order, omitted optionals, UTF-8
    subject/handle), and golden vectors.
  - `signStakeAttestation` → `parseStakeAttestation` round-trip; malformed proof
    / claim → `BadAttestationError`.
  - `verifyStake` with a **fake `fetchProvenance`**: happy path (`valid:true`,
    all checks); tampered claim → `bad-signature`; unknown txn → `txn-not-found`;
    orphaned/pending provenance → `not-canonical`; `minConfirmations` not met →
    `insufficient-confirmations`; provenance address ≠ signer →
    `signer-not-staker`; amount/subject/kind mismatch → `claim-mismatch`;
    matching against a txn with a **change-transfer** outflow present (the real
    #176a shape).
- **Python (`uv run pytest`):** the same matrix in `tests/test_attestation.py`
  with a fake `fetch_provenance`.
- **Cross-language parity (`tests/test_attestation_parity.py`, skipif no node):**
  - byte-identical canonical messages (JS `message-cli`-style harness vs Python
    `build_stake_message`) and golden vectors at
    `clients/wallet/testdata/gc-attestation-vectors.json`.
  - a JS-signed attestation verifies in Python and vice-versa, using a fake
    provenance both sides agree on (no live node).
- **Demo (manual):** extend `passkey-wallet-demo.html` — build+sign a stake
  attestation, then verify it with a real `fetchProvenance` that GETs
  `/api/transaction/<txid>`; documented in `MANUAL-VERIFICATION.md`.

## Out of scope

- **Handle ownership verification** (Bluesky/AT-Protocol bidirectional proof) —
  #176c.
- The **verify web page + OG unfurl / Bluesky delivery** — #176c (EGU #5 hub).
- A node `/verify` endpoint (rejected: client-side composition by design).
- Multi-chain / chain-id binding; GRIT formatting; encryption.

## Decisions log

- **Client-side verifier, injected `fetchProvenance`** (Q1) — pure, parity-
  testable, decentralized; the node is a data source, the module is the glue.
- **JS + Python parity** (Q2) — both can produce and verify attestations now;
  symmetry with #2.4.
- **Grains in the claim** — exact integer match with on-chain/provenance; GRIT is
  display. Avoids a float units-mismatch bug class.
- **Canonical JSON message, fixed key order + `ensure_ascii=False`** — guarantees
  byte-identical cross-language signing; golden vectors enforce it.
- **Claim is the signed message; verdict's claim comes from `proof.message`** —
  no caller-supplied claim to spoof; everything is bound by the signature.
- **`valid:false`+reasons vs. throw** — verification outcomes are data; only
  malformed input is exceptional (mirrors `verifyMessage`).
- **handle signed but ownership deferred** — tamper-evident now; bidirectional
  proof is #176c.

## Definition of done

- `gc-attestation.mjs` (4-function API) + `BadAttestationError` + `index.mjs`
  re-export/version bump; Python `attestation.py` mirror.
- JS tests + Python tests + cross-language parity (canonical bytes, golden
  vectors, JS↔Python verify with a fake provenance) pass; full `uv run pytest`
  green; `ruff`/`mypy` clean; zero npm.
- Demo + `MANUAL-VERIFICATION.md` updated for sign/verify of a stake attestation.
- No node endpoint, no schema/consensus change. Part of #176.
