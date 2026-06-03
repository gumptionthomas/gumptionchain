# GumptionChain API Authentication Protocol: `gc-sig-v1`

Every GumptionChain API request (excluding the unauthenticated browser views) must
be authenticated with a per-request wallet signature. The server verifies the
signature on each request, so there are no tokens, no sessions, and no server-side
credential state. Role authorization (READER / TRANSACTOR / MILLER / ADMIN) is
enforced live per-request against the server's configured address allowlists after
the signature is verified.

---

## Versioning

Authentication scheme selection is driven by the `GC-Sig-Version` request header.
This document specifies **version `1`** — the `gc-sig-v1` scheme. The header value
is the decimal string `"1"`.

Future schemes (for example, an RFC 9421 HTTP Message Signatures-based `v2` for
broader third-party library interoperability) will be assigned new version numbers
and accepted by the server side-by-side with existing versions — existing `v1`
clients do not need to change when a new version is introduced. RFC 9421 support is
deferred pending real third-party-client demand and a fuller Python library
ecosystem; it is a planned additive scheme, not a current one.

---

## Required headers

Every signed request must include all five of the following headers.

| Header | Value |
|---|---|
| `GC-Sig-Version` | `1` |
| `GC-Address` | Caller's GC address (e.g. `GC…GC`) |
| `GC-Public-Key` | Caller's RSA public key, base64-encoded DER SubjectPublicKeyInfo |
| `GC-Timestamp` | Unix time of the request, decimal seconds (e.g. `1748736000`) |
| `GC-Signature` | Base64 RSASSA-PKCS1-v1_5 / SHA-384 signature over the canonical string |

The `GC-Public-Key` is **self-certifying**: the server derives the GC address from
the supplied public key and requires it to equal `GC-Address`. No prior key
registration is needed; any wallet whose address appears in the server's role
configuration can authenticate.

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

The signature is produced using **RSASSA-PKCS1-v1_5 with SHA-384** over the
canonical string bytes. The key is the wallet's RSA-3072 private key. The resulting
signature bytes are encoded with **standard base64** (not URL-safe base64, using
`+` and `/`).

In Python using the `cryptography` library:

```python
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding
from base64 import standard_b64encode

signature_bytes = private_key.sign(canonical_bytes, padding.PKCS1v15(), hashes.SHA384())
gc_signature = standard_b64encode(signature_bytes).decode()
```

---

## Address derivation (public key self-certification)

A GC address is derived from a public key as follows:

1. Serialize the RSA public key to DER-encoded SubjectPublicKeyInfo bytes.
2. Compute `mill_hash` of those bytes: `sha256(sha512(der_bytes).digest()).digest()` — 32 bytes.
3. Base58Check-encode the 32-byte hash.
4. Wrap with the `GC` tag: `"GC" + base58check_str + "GC"`.

The server performs this derivation on the `GC-Public-Key` value it receives and
requires the result to equal `GC-Address`. This means the public key is the
authoritative credential; the address is a fingerprint of it.

The `GC-Public-Key` header value is the standard base64 encoding of the DER
SubjectPublicKeyInfo bytes (step 1 above).

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
2. `GC-Address`, `GC-Public-Key`, `GC-Timestamp`, and `GC-Signature` must all be
   present and non-empty → `401`.
3. `GC-Timestamp` must parse as a decimal integer → `401`.
4. Freshness: `abs(now − ts) <= 300` → else `401`.
5. `GC-Public-Key` must be a valid RSA-3072 public key in base64 DER format, and
   it must derive to an address equal to `GC-Address` → else `401`.
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
address:   GCAbcDef…XyzGC   ← placeholder
```

**Canonical string** (fields separated by `\n`, shown here on separate lines):

```
gc-sig-v1
GET
/api/block

e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
http://localhost:8080
1748736000
GCAbcDef…XyzGC
```

(Line 4 is the empty query string — present as an empty line, not absent.)

**Resulting request headers:**

```
GC-Sig-Version: 1
GC-Address:     GCAbcDef…XyzGC
GC-Public-Key:  <base64 DER SubjectPublicKeyInfo — placeholder>
GC-Timestamp:   1748736000
GC-Signature:   <base64 RSASSA-PKCS1-v1_5/SHA-384 over canonical bytes — placeholder>
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
address:   GCAbcDef…XyzGC
```

**Canonical string:**

```
gc-sig-v1
POST
/api/block/0000ab12…ef34

<sha256 hex of the JSON body bytes>
http://localhost:8080
1748736001
GCAbcDef…XyzGC
```

**Resulting request headers:**

```
GC-Sig-Version: 1
GC-Address:     GCAbcDef…XyzGC
GC-Public-Key:  <base64 DER SubjectPublicKeyInfo — placeholder>
GC-Timestamp:   1748736001
GC-Signature:   <base64 RSASSA-PKCS1-v1_5/SHA-384 over canonical bytes — placeholder>
```

All placeholder values (`GCAbcDef…XyzGC`, the base64 keys, and the base64
signatures) are illustrative only and are not real cryptographic values.

---

## Algorithm reference

| Property | Value |
|---|---|
| RSA key size | 3072 bits |
| Signature algorithm | RSASSA-PKCS1-v1_5 |
| Signature hash | SHA-384 |
| Signature encoding | Standard base64 (RFC 4648, uses `+` and `/`) |
| Public key encoding | Standard base64 of DER SubjectPublicKeyInfo |
| Body digest algorithm | SHA-256 (hex digest, lowercase) |
| Address derivation hash | `sha256(sha512(der_pubkey))` then Base58Check |
| Timestamp format | Decimal integer, Unix seconds |
| Freshness window | ±300 seconds |
| Scheme identifier | `gc-sig-v1` (first field of canonical string) |
| Version header value | `1` |
