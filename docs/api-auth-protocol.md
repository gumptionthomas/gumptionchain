# GumptionChain API Authentication Protocol: `gc-sig-v1`

Every GumptionChain API request (excluding the unauthenticated browser views) must
be authenticated with a per-request signing_key signature. The server verifies the
signature on each request, so there are no tokens, no sessions, and no server-side
credential state. Role authorization (READER / TRANSACTOR / MILLER / ADMIN) is
enforced live per-request against the server's configured address allowlists after
the signature is verified.

---

## Versioning

Authentication scheme selection is driven by the `GC-Sig-Version` request header.
This document specifies **version `1`** — the `gc-sig-v1` scheme. The header value
is the decimal string `"1"`.

GumptionChain is Ed25519-only: every key is an Ed25519 key, and the GC address
**is** the public key (a bech32m encoding of the raw 32-byte Ed25519 public key).
The verifier therefore reconstructs the public key directly from `GC-Address`, so
`gc-sig-v1` carries **no** `GC-Public-Key` header — the address is the credential,
not a fingerprint of one.

Pre-1.0, the scheme is **v1, redefined in place**: as the wire format evolves
during development the single `gc-sig-v1` definition is edited rather than a new
version stacked beside it (there are no deployed clients pinned to an older
definition to stay compatible with). The `GC-Sig-Version` field nonetheless exists
as a forward-looking dispatch hook: once the protocol is stable (post-1.0), a
future incompatible change — for example, an RFC 9421 HTTP Message Signatures-based
scheme for broader third-party library interoperability — can bump the version and
be accepted side-by-side with `gc-sig-v1`. RFC 9421 support is deferred pending
real third-party-client demand and a fuller Python library ecosystem; it is a
planned additive scheme, not a current one.

---

## Required headers

Every signed request must include all four of the following headers.

| Header | Value |
|---|---|
| `GC-Sig-Version` | `1` |
| `GC-Address` | Caller's GC address — a `gc1…` bech32m string that **is** the raw 32-byte Ed25519 public key |
| `GC-Timestamp` | Unix time of the request, decimal seconds (e.g. `1748736000`) |
| `GC-Signature` | Base64 Ed25519 signature (RFC 8032) over the canonical string |

There is **no** `GC-Public-Key` header in `gc-sig-v1`. The address itself encodes
the public key, so the server **reconstructs** the Ed25519 verifying key by
decoding `GC-Address` (validating its HRP and bech32m checksum) — see
[Address derivation](#address-derivation). No prior key registration is needed;
any signing_key whose address appears in the server's role configuration can
authenticate.

---

## Canonical string (`gc-sig-v1`)

The client signs — and the server reconstructs — a canonical string formed by
joining exactly these eight fields with newline (`\n`) characters, **in this
order**, with no trailing newline:

```
gc-sig-v1
<METHOD>
<path>
<query>
<body-digest>
<node-host>
<timestamp>
<address>
```

Field-by-field rules:

| Field | Value |
|---|---|
| `gc-sig-v1` | Literal scheme identifier, always this exact string |
| `<METHOD>` | HTTP method, **uppercased** (e.g. `GET`, `POST`) |
| `<path>` | The URL path, exactly as the server sees it (e.g. `/api/block`) |
| `<query>` | The raw URL query string; empty string `""` when no query is present |
| `<body-digest>` | Lowercase hex SHA-256 of the raw request body bytes; use SHA-256 of `b""` (empty bytes) for requests with no body |
| `<node-host>` | The full URL of the target node's identity (scheme + host + port, no path, e.g. `http://localhost:8080`) |
| `<timestamp>` | The same decimal integer sent in `GC-Timestamp` |
| `<address>` | The same GC address sent in `GC-Address` |

The canonical string is UTF-8 encoded to bytes before signing.

### Canonicalization matching — the critical correctness rule

The `<path>` and `<query>` fields must be **byte-for-byte identical** on both the
client and the server. The server reads `request.path` and
`request.query_string.decode()` (Werkzeug's URL-decoded forms) directly. The
client must sign the exact path and query string it is about to send, using the
same encoding. Do not re-order, re-quote, or normalize the query string.

The request target (path and query) must be ASCII — any non-ASCII characters
must be consistently percent-encoded on the wire, and the signed `<path>`/`<query>`
must use that same percent-encoded form. Sign what goes on the wire, not a decoded
intermediate. (GumptionChain's own endpoints keep path segments ASCII — subjects
embedded in a path are urlsafe-base64 — so this only concerns clients constructing
arbitrary targets.)

### Body digest

The body digest is computed as:

```
sha256(raw_body_bytes).hexdigest()
```

For requests with no body (such as GET requests), use the SHA-256 of empty bytes:

```
sha256(b'').hexdigest()
# = e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
```

### Node binding

The `<node-host>` field binds the signature to a specific node. Both the client and
server derive this value by parsing the node's configured `NODE_HOST` URL with
`host_address(NODE_HOST)[0]`, which returns the scheme and host:port only (e.g.
`http://localhost:8080`, not a bare `localhost:8080`). A signature produced for
node A will fail verification on node B even if both nodes share the same
cryptographic configuration, because the canonical strings differ.

---

## Signing algorithm

The signature is produced over the canonical string bytes with **Ed25519**
(RFC 8032; the scheme hashes with SHA-512 internally), then encoded with
**standard base64** (not URL-safe, using `+` and `/`).

The verifier reconstructs the Ed25519 public key from `GC-Address` (the address
is the key — see [Address derivation](#address-derivation)). Nodes **verify
Ed25519 signatures with GumptionChain's vendored, version-independent cofactored
verifier** (`src/gumptionchain/ed25519.py`), not OpenSSL, for the same
node-uniform determinism the chain's consensus path requires (see #316).

In Python using the `cryptography` library:

```python
from base64 import standard_b64encode

# Ed25519
signature_bytes = ed25519_private_key.sign(canonical_bytes)  # RFC 8032
gc_signature = standard_b64encode(signature_bytes).decode()
```

---

## Address derivation

A GC address **is** an Ed25519 public key. It is derived as:

```
address = bech32m('gc', ed25519_public_key)
```

That is: the data part of the address is the raw 32-byte Ed25519 public key, the
human-readable part (HRP) is `gc`, and a bech32m (BIP-350) BCH checksum is
appended. The result is a `gc1…` string. The bech32m checksum provides
typo/corruption integrity (a single mistyped character is detected). This
replaces the older `GC…GC`-tagged, hash-based (Base58Check over a `mill_hash`)
address: the address is no longer a fingerprint of the key — it **is** the key.

The verifier **decodes** `GC-Address` back to the 32-byte Ed25519 public key,
validating the `gc` HRP and the bech32m checksum in the process; a malformed or
bad-checksum address is rejected (`401`). Because the address carries the full
public key, there is no separate `GC-Public-Key` header and no public-key→address
cross-check to perform.

---

## Freshness window

The server rejects any request where:

```
abs(server_unix_time - GC-Timestamp) > 300
```

Both stale requests (more than 300 seconds old) and far-future requests (timestamp
more than 300 seconds ahead of server time) are rejected with `401 Unauthorized`.
The client must maintain accurate system time. Requests are not otherwise
deduplicated; the 300-second window is the sole replay guard (under the TLS
transport precondition assumed for all API communication).

---

## Verification steps and error responses

The server performs these checks in order. Any failure in steps 1–6 results in
`401 Unauthorized`. Insufficient role in step 7 results in `403 Forbidden`.

1. `GC-Sig-Version` must be present and equal to `"1"`. Unknown or missing version
   → `401`.
2. `GC-Address`, `GC-Timestamp`, and `GC-Signature` must all be present and
   non-empty → `401`.
3. `GC-Timestamp` must parse as a decimal integer → `401`.
4. Freshness: `abs(now − ts) <= 300` → else `401`.
5. `GC-Address` must decode (valid `gc` HRP + bech32m checksum) to a 32-byte
   Ed25519 public key → else `401`. This decoded key is the verifying key.
6. Reconstruct the canonical string from the live request (see above); verify the
   `GC-Signature` using the public key from step 5 → else `401`.
7. Map `GC-Address` to a role via the server's live address allowlists. If no role
   matches, or the role is insufficient for the endpoint, → `403`.

---

## Worked example

### GET `/api/block`

**Inputs (illustrative — not real crypto values):**

```
method:    GET
path:      /api/block
query:     (empty)
body:      (none)
node_host: http://localhost:8080
timestamp: 1748736000
address:   gc1abc…xyz   ← placeholder
```

**Canonical string** (fields separated by `\n`, shown here on separate lines):

```
gc-sig-v1
GET
/api/block

e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
http://localhost:8080
1748736000
gc1abc…xyz
```

(Line 4 is the empty query string — present as an empty line, not absent.)

**Resulting request headers:**

```
GC-Sig-Version: 1
GC-Address:     gc1abc…xyz
GC-Timestamp:   1748736000
GC-Signature:   <base64 Ed25519 signature over canonical bytes — placeholder>
```

### POST `/api/block/<hash>`

**Inputs (illustrative):**

```
method:    POST
path:      /api/block/0000ab12…ef34
query:     (empty)
body:      {"block": "…"}   ← JSON bytes
node_host: http://localhost:8080
timestamp: 1748736001
address:   gc1abc…xyz
```

**Canonical string:**

```
gc-sig-v1
POST
/api/block/0000ab12…ef34

<sha256 hex of the JSON body bytes>
http://localhost:8080
1748736001
gc1abc…xyz
```

**Resulting request headers:**

```
GC-Sig-Version: 1
GC-Address:     gc1abc…xyz
GC-Timestamp:   1748736001
GC-Signature:   <base64 Ed25519 signature over canonical bytes — placeholder>
```

All placeholder values (`gc1abc…xyz` and the base64 signatures) are illustrative
only and are not real cryptographic values.

---

## Algorithm reference

| Property | Value |
|---|---|
| Key type | Ed25519 (256-bit, RFC 8032) |
| Signature algorithm | Ed25519 (RFC 8032) |
| Signature encoding | Standard base64 (RFC 4648, uses `+` and `/`) |
| Address encoding | `bech32m('gc', raw_32_byte_ed25519_pubkey)` (BIP-350) — the address **is** the public key |
| Body digest algorithm | SHA-256 (hex digest, lowercase) |
| Timestamp format | Decimal integer, Unix seconds |
| Freshness window | ±300 seconds |
| Scheme identifier | `gc-sig-v1` (first field of canonical string) |
| Version header value | `1` |
