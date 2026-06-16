# GRIT-spend rails: node-proxy blueprint + keyring signTransaction

**Date:** 2026-06-16
**Issue:** #282 (base). Consumer: gumptactoe (first adopter); design for any EGU app.
**Status:** design approved
**Related:** #283 (separate `fix(api)`: make the node's `/opposition` balance key
symmetric — out of scope here; the proxy normalizes it). Builds on the
`gc-onboarding` arc (#278/#280/#281).

## Goal

Two **reusable EGU drop-ins** so a consumer web app can let a player spend GRIT
by supporting/opposing a subject, with the player's key signed **client-side**
and the node host kept **private** — mirroring how signing-key onboarding was
extracted into base.

- **A — node-proxy blueprint:** a mountable Flask blueprint exposing a narrow,
  browser-facing JSON API that wraps `ApiClient`, keeping `GC_NODE_HOST`
  server-side. It only relays node-built unsigned txns and consumer-signed txns;
  it never holds a key.
- **B — `signTransaction`:** a method on the `makeOnboarding` controller that
  signs a node-built unsigned txn with the unlocked in-memory key (analog of
  `signLogin`).

The browser flow the rails enable: `balance(address)` → build support/oppose
(proxy) → `onb.signTransaction(unsigned)` (in-browser) → submit signed (proxy) →
poll `txn status` until milled.

## Background — verified base primitives (unchanged)

- `ApiClient` (`api_client.py`) methods return raw `httpx.Response`:
  - `get_support_transaction(public_key, amount_grains, raw_subject)` /
    `get_opposition_transaction(...)` → body is an **unsigned** txn JSON
    (`signature: null`, `txid` precomputed, `outflows:[{amount, support|opposition: <encoded_subject>}]`). The node validates the **raw** subject and encodes it server-side.
  - `post_transaction(txn: Transaction)` → POST `/api/transaction/<txid>`,
    body `{received: <iso>}`, 201 new / 200 dup / 202 async / 400 validation.
  - `get_signing_key_balance(address)` → `{balance: <grains>, as_of_block}` —
    **confirmed/longest-chain only** (the honest spend gate).
  - `get_support_balance(encoded_subject)` → `{support: <grains>, as_of_block}`;
    `get_opposition_balance(encoded_subject)` → `{balance: <grains>, as_of_block}`
    (asymmetric key — see #283).
  - No single-txn status method, but `GET /api/transaction/<txid>` (provenance)
    → `{status: "pending"|"canonical"|"orphaned", block_hash, height, confirmations, as_of_block}`. Reach it via `client.get(...)` (gc-sig-signed).
- `payload.py`: `encode_subject`/`decode_subject` (base64url, no padding),
  `validate_raw_subject` (1–79 printable chars), `MAX_SUBJECT_LENGTH=79`.
- `gc-transaction.mjs`: `signUnsignedTxn(unsigned, signing_key)` → returns
  `{...unsigned, signature, public_key, address}`, and **recomputes the txid,
  throwing on mismatch** (a node can't steer the signer with forged fields).
- `chain.GRAIN_PER_GRIT = 100`; `command.grit_to_grains` / `human_grains`.

## A — node-proxy blueprint

### Factory & wiring

```python
from gumptionchain import node_proxy_blueprint
app.register_blueprint(node_proxy_blueprint(make_client))
```

```python
def node_proxy_blueprint(
    make_client,                       # () -> ApiClient (consumer wires host + key)
    *,
    url_path: str = '/api/node',
    rate_limit=None,                   # (flask.Request) -> bool | None; falsy => 429
    max_body_bytes: int = 65536,       # request body cap => 413
) -> Blueprint: ...
```

`make_client` is **injected** (not read from config): the consumer supplies node
host + the app's signing key (which must hold TRANSACTOR for build/submit,
READER for reads on the node), so `GC_NODE_HOST` never reaches the browser, and
the proxy is testable with a fake client. Called per request (may return a
shared `ApiClient`).

### Endpoints (final)

| Method · path | Request | Response |
|---|---|---|
| `GET <url_path>/balance/<address>` | address in path (base58, path-safe) | `{ grit, grains, as_of_block }` — confirmed |
| `GET <url_path>/subject/balances?subject=<raw>` | raw subject in urlencoded query | `{ subject, support: {grit, grains}, opposition: {grit, grains} }` |
| `POST <url_path>/txn/support` | `{ public_key, amount_grit, subject }` | node-built **unsigned** txn JSON |
| `POST <url_path>/txn/oppose` | `{ public_key, amount_grit, subject }` | unsigned txn JSON |
| `POST <url_path>/txn/submit` | `{ signed: <signed txn JSON> }` | `{ txid }` |
| `GET <url_path>/txn/<txid>/status` | txid in path (hex, path-safe) | `{ state: "pending"\|"milled", block?, confirmations? }` |

Two named build endpoints (matches the node + the consumer spec). **Subjects
never appear in a URL path** — they're free-text (can contain `/`, spaces,
unicode), so they ride in the JSON body (build) or an urlencoded query
(subject-balances).

### Conversion, subject, normalization (the boundary the proxy owns)

- **GRIT⇄grains:** `amount_grit` is a GRIT number. Validate `> 0` and at most 2
  decimal places (no sub-grain precision); convert with `Decimal` to integer
  grains (`grains = int((Decimal(amount_grit) * 100).to_integral_value())`),
  rejecting anything else with 400. Balances out: node grains →
  `{grit: grains / 100, grains}` (`grit` a 2-dp number for display, `grains` the
  int source of truth for an exact spend gate).
- **Subject:** the proxy takes **raw** everywhere. It validates with
  `validate_raw_subject` and fast-fails 400 on a bad subject. For build it passes
  raw through (the node encodes). For subject-balances it `encode_subject`s
  before calling the (encoded-taking) balance methods.
- **Opposition-key normalization (#283):** read support grains from the node's
  `"support"` key and opposition grains from its `"balance"` key, returning a
  symmetric `{support, opposition}` so consumers never see the node's asymmetry.

### Confirmation status

`GET <url_path>/txn/<txid>/status` calls `client.get(f'/api/transaction/{txid}')`
and maps: `canonical → {state:"milled", block: block_hash, confirmations}`;
`pending → {state:"pending"}`; `orphaned → {state:"pending"}` (not in the
canonical chain — honestly still awaiting the mill). Node 404 → proxy 404.

### Errors, limits, CSRF

- Node unreachable / timeout (`httpx` transport error) → **502**
  `{error:"node unavailable"}`. Node 4xx (build/validation, bad subject at node)
  → **400** passthrough with the node's error message. Proxy-side bad subject →
  **400**. Unknown txid → **404**. Body over `max_body_bytes` → **413**.
  `rate_limit(request)` falsy → **429**.
- JSON-only and **CSRF-exempt** (a signed-payload relay, like base's `/api`); no
  Flask-WTF coupling (base doesn't use it). The relay is safe to expose: reads
  are public chain data, builds are harmless, submits carry a self-authorizing
  signature. Consumers may wrap it behind their own session via `rate_limit` or
  an outer gate.

## B — keyring `signTransaction`

Add to the `makeOnboarding` controller (`gc-onboarding.mjs`):

```js
async signTransaction(unsigned)
// requires unlocked (else throws NoSigningKeyError, like signLogin);
// → signUnsignedTxn(unsigned, heldKey)
// → { ...unsigned, signature, public_key, address }   (ready to POST to /txn/submit)
```

Mirrors `signLogin`: requires the keyring unlocked, the raw key never surfaces,
and it inherits `signUnsignedTxn`'s **txid-integrity check** (rejects a node-built
txn whose fields don't hash to its `txid`). No support/oppose convenience
wrappers (YAGNI — the issue marks them optional; gumptactoe needs only
`signTransaction`).

## Testing

- **Python** — `tests/test_node_proxy.py`: mount the blueprint with a **fake
  `make_client`** (a stub `ApiClient` returning canned `httpx`-like responses).
  Cover: balance grains→grit; subject-balances encoding + `{support,opposition}`
  normalization (incl. the node's `balance` opposition key); build GRIT→grains +
  raw-subject passthrough + returns the unsigned txn; submit → `{txid}`; status
  mapping (pending / canonical→milled / orphaned→pending / unknown→404);
  GRIT validation (non-positive, >2dp → 400); error mapping (node transport
  error → 502, node 4xx → 400 passthrough); `max_body_bytes` → 413; `rate_limit`
  falsy → 429.
- **JS** — extend `gc-onboarding.test.mjs`: `signTransaction` signs a real
  unsigned txn (build an `unsigned` with a correct `txid` via `gc-transaction`'s
  `txid()`, real `SigningKey`), asserts the returned txn carries a valid
  `signature`/`public_key`/`address`, and throws `NoSigningKeyError` when locked.
- Full `pytest` + `node --test` + `ruff` + `mypy` + vendored-parity stay green.

## Scope

**In:** `node_proxy.py` (`node_proxy_blueprint`) + `__init__` re-export +
`tests/test_node_proxy.py`; `signTransaction` on `gc-onboarding.mjs` (+ vendored
copy + node test); docs (`key-onboarding-for-egu-apps.md` gains `signTransaction`
+ a proxy mount note); the report-back. One base branch → PR → merge.

**Out (separate):** gumptactoe's mount + CRT spend UX + the `SubjectStake` model
(consumer repo); #283 (node `/opposition` key symmetry); rescind/transfer
endpoints (linked to gumption.com/chain, not in v1).

## Invariants — what does NOT change

- `ApiClient`, `payload`, `gc-transaction.mjs`, and the node `/api` endpoints
  (additive only; #283 is tracked separately).
- Protocol/canonical strings; `GRAIN_PER_GRIT`.
- The proxy holds no key and persists nothing.

## Risks

- **Open relay abuse:** unauthenticated by design → mitigated by the
  `rate_limit` hook + `max_body_bytes`; consumers add their own gate. Documented.
- **Node-key role:** the injected `make_client` must hold TRANSACTOR (build +
  submit) and READER on the node, or build/submit 401/403 at the node → surfaced
  as the node's 4xx passthrough. Documented in the report-back.
- **Float GRIT precision:** handled via `Decimal` + a ≤2-dp / >0 guard.
- **Status for orphaned txns** maps to `pending` (a reorg could move a txn);
  acceptable for the consumer's "awaiting the mill" UX.
