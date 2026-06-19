# HTTP API Reference

Every GumptionChain node exposes a JSON API under the `/api` prefix. This
document lists the endpoints, the role each requires, and their request and
response shapes.

- **Authentication** for every endpoint is a per-request signing-key signature
  (`gc-sig-v1`). The full protocol — canonical string, headers, algorithm, and
  a worked example — is specified in
  [api-auth-protocol.md](api-auth-protocol.md). In practice you sign requests
  with [`ApiClient`](../src/gumptionchain/api_client.py), which sets the
  `GC-*` headers for you.
- **Roles** form a hierarchy: `READER` < `TRANSACTOR` < `MILLER` < `ADMIN`. An
  endpoint's "Role" column is the *minimum*; a higher role also passes. Roles
  are resolved from the `*_ADDRESSES` allowlists (see
  [configuration.md](configuration.md)) and re-checked on every request, so
  revocations take effect immediately.
- **Errors** are returned as `{"error": "<message>"}` with an appropriate HTTP
  status: `401` (signature invalid/expired), `403` (role insufficient), `404`
  (not found), `429` (per-transactor quota exceeded), `503` (mempool full).
- **Units:** all amounts in API request/response bodies are in **grains**
  (1 GRIT = 100 grains). The CLI and the browser-facing proxy relay accept
  GRIT and convert; the raw node API does not.

## Endpoint summary

| Method | Path | Role | Purpose |
| --- | --- | --- | --- |
| GET | `/api/block` | READER | Fetch the longest-chain tip block |
| GET | `/api/block/<block_hash>` | READER | Fetch a block by hash |
| POST | `/api/block/<block_hash>` | MILLER | Submit a block (peer gossip) |
| POST | `/api/block/<block_hash>/<process>` | MILLER | Submit a block, forcing sync processing |
| GET | `/api/blocks?from_idx=&limit=` | READER | Range of longest-chain blocks |
| POST | `/api/transaction/<txid>` | TRANSACTOR | Submit a signed transaction |
| POST | `/api/transaction/<txid>/<process>` | TRANSACTOR | Submit, forcing sync processing |
| GET | `/api/transaction/<txid>` | READER | Transaction provenance / status |
| GET | `/api/transaction/transfer` | TRANSACTOR | Build an unsigned transfer txn |
| GET | `/api/transaction/split` | TRANSACTOR | Build an unsigned self-split txn |
| GET | `/api/transaction/opposition` | TRANSACTOR | Build an unsigned opposition txn |
| GET | `/api/transaction/support` | TRANSACTOR | Build an unsigned support txn |
| GET | `/api/transaction/rescind` | TRANSACTOR | Build an unsigned rescind txn |
| GET | `/api/transaction/pending` | READER | List pending (mempool) transactions |
| GET | `/api/signing-key/<address>/balance` | READER | Address balance |
| GET | `/api/subject/<subject>/opposition` | READER | Subject opposition total |
| GET | `/api/subject/<subject>/support` | READER | Subject support total |
| GET | `/api/subjects/search?q=&limit=` | READER | Prefix-search subjects |
| GET | `/api/stats/transactors` | READER | Per-relay submission leaderboard |

## Blocks

### `GET /api/block` · `GET /api/block/<block_hash>`

Returns a block as JSON. Without a hash, returns the current longest-chain
tip. `<block_hash>` is a 64-hex-character mill hash.

### `POST /api/block/<block_hash>[/<process>]` — MILLER

Submits a block to the node, used for peer-to-peer gossip. The body is the
block JSON. Response status: `201` if the block was newly applied, `202` if
accepted for asynchronous processing (`GC_API_ASYNC_PROCESSING=true`), `200`
if already known. Appending `/process` forces synchronous processing of a
block that was previously queued.

### `GET /api/blocks?from_idx=<n>&limit=<n>`

Returns an array of longest-chain blocks starting at height `from_idx`
(`>= 0`), up to `limit` blocks (`>= 1`, server-clamped to `SYNC_BATCH_SIZE`).
Used by `gumptionchain sync`.

## Transactions

### `POST /api/transaction/<txid>[/<process>]` — TRANSACTOR

Submits a signed transaction. The body is the signed transaction JSON; `txid`
is its mill hash. Response status mirrors block submission (`201`/`202`/`200`).
A `429` is returned if the submitting TRANSACTOR is over its
`MAX_PENDING_PER_TRANSACTOR` in-flight quota; a `503` if the global mempool
(`MAX_PENDING_TXNS`) is full.

### `GET /api/transaction/<txid>` — READER

Returns the provenance and confirmation status of a transaction, whether it is
canonical (mined into the longest chain) or still pending.

### Transaction builders — TRANSACTOR

These endpoints **build and return an unsigned transaction** for the supplied
public key. The caller signs it with the corresponding private key and submits
it via `POST /api/transaction/<txid>`. They never move funds themselves. All
take the signer's `public_key` (base64 SubjectPublicKeyInfo) as a query
parameter; amounts are in grains.

| Endpoint | Extra query params | Builds |
| --- | --- | --- |
| `GET /api/transaction/transfer` | `amount`, `address` | Transfer `amount` to another address |
| `GET /api/transaction/split` | `denomination`, `count` (1–49) | Mint `count` same-address chips of `denomination` each |
| `GET /api/transaction/opposition` | `amount`, `subject` (raw) | Stake `amount` of opposition on `subject` |
| `GET /api/transaction/support` | `amount`, `subject` (raw) | Stake `amount` of support on `subject` |
| `GET /api/transaction/rescind` | `amount`, `subject` (raw), `kind` (`opposition`\|`support`) | Rescind a prior stake |

The `subject` parameter is the raw (unencoded) UTF-8 string (1–79 chars); the
node encodes it.

### `GET /api/transaction/pending?earliest=<iso8601>` — READER

Returns the current mempool as an array of pending transactions, excluding any
already confirmed or expired. The optional `earliest` filter returns only
transactions submitted at or after the given ISO-8601 timestamp.

## Balances and subjects — READER

### `GET /api/signing-key/<address>/balance`

Returns `{"balance": <grains>, "as_of_block": <hash>}` for an address.

### `GET /api/subject/<subject>/opposition` · `.../support`

Returns `{"opposition": <grains>, "as_of_block": <hash>}` (or `"support"`) for
a subject. `<subject>` is URL-path-encoded.

### `GET /api/subjects/search?q=<prefix>&limit=<n>`

Case-insensitive prefix search over subjects, ranked by total GRIT at stake.
`limit` is clamped by the node to 1–50 (default 8). Returns
`{"subjects": [{"subject", "opposition", "support"}, ...], "as_of_block": <hash>}`
with amounts in grains.

## Stats — READER

### `GET /api/stats/transactors`

Returns `{"transactors": [{"address", "count", "last_submit_at"}, ...]}` — a
per-relay leaderboard of how many transactions each submitter has been
attributed (node-local accounting, first-submitter-wins; **not** consensus
data).

## Browser-facing proxy relay

For consumer web apps that want to let a browser build, sign, and submit
transactions without exposing the node host or holding a relay key
client-side, GumptionChain ships an **embeddable** Flask blueprint,
`node_proxy_blueprint` (see [node-proxy.md](node-proxy.md)). It is a library
helper an app mounts itself — it is **not** part of a node's default surface.
