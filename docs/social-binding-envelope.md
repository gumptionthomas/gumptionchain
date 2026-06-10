# GumptionChain Social Binding Envelope: `gc-msg-v1` / binding claim

A **social binding** is a Keybase-style bidirectional proof that a wallet
address controls a social-platform handle. It works in two directions:

1. **Wallet side (this spec):** the wallet signs a structured claim
   `{platform, handle, proof_url?}` as a `gc-msg-v1` message. The signed
   proof is the authoritative cryptographic record that the wallet's owner
   asserts ownership of the named handle.

2. **Social side (directory / verifier service):** the handle's account posts
   the armored form of that proof at a publicly retrievable location under
   the handle's control (a gist, a Mastodon post, a `rel="me"` HTML page, a
   DNS TXT record, …).

A full verification requires both directions: fetch the social-side post,
find the armored proof inside it, verify the signature, and confirm the claim
inside matches the handle and platform being registered. **This document
covers only direction 1** — the wallet-side claim schema, canonical encoding,
envelope format, and the cryptographic operations a verifier must apply to it.
Fetching `proof_url`, SSRF guards, storage of the binding record, revocation
UX, and handle display are all the directory/verifier service's
responsibility, not specified here.

---

## Versioning

The social binding envelope reuses `gc-msg-v1` without modification. There is
no binding-specific scheme or version. The `gc-msg-v1` scheme version is
always the string `"1"` (see `scheme` and `version` fields below). A future
revision that changes the canonical claim encoding would be introduced as a
new scheme identifier (e.g. `gc-binding-v2`), not by bumping `version`.

---

## Claim schema

A binding claim is a JSON object with the following fields.

| Field | Required | Validation |
|---|---|---|
| `platform` | yes | `^[a-z0-9-]{1,32}$` — lowercase letters, digits, and hyphens only |
| `handle` | yes | non-empty string, ≤ 256 Unicode code points |
| `proof_url` | no | when present: must start with `https://`, ≤ 512 Unicode code points |

**Length limits are Unicode code points, not bytes or UTF-16 code units.** In
Python, `len(s)` counts code points. In JavaScript,
`[...s].length` counts code points (spread unpacks surrogate pairs). A
verifier MUST use code-point counting and MUST NOT use byte length or
`String.length` (which counts UTF-16 units and double-counts characters
outside the BMP).

**`platform`** is an open namespace. Any string matching `^[a-z0-9-]{1,32}$`
is structurally valid here — `github`, `mastodon`, `web`, `dns`, `bluesky`,
and so on. This spec performs shape-only validation. Which platforms a
verifier accepts, and what constitutes a valid `proof_url` for each, is
verifier policy, not the envelope's concern. Extending support to a new
platform requires no change to this spec.

**`handle`** syntax is platform-specific and is the verifier's business
(`gumptionthomas` on GitHub, `@alice@mastodon.social` on Mastodon). This spec
enforces only non-empty and the code-point ceiling so that canonical bytes are
well-defined.

**`proof_url` is optional by design.** On platforms where the posted URL is
unknown until after posting (a new gist, for example), the wallet signs the
claim first and fills in `proof_url` with a subsequent re-sign once the URL is
known. On platforms where there is no retrievable URL at all (DNS TXT records),
`proof_url` is absent from the claim. The directory stores the resolved
location; claims include it only when stable and known at signing time. When
`proof_url` is present, the `https://` requirement is mandatory — an
unencrypted proof location is not acceptable evidence.

**The claim deliberately has no `address` field.** The wallet side of the
binding is the `gc-msg-v1` envelope's own signer (`proof['address']`,
self-certified by public key — see Address derivation below). Duplicating the
address inside the claim would invite mismatch bugs with no cryptographic gain.

---

## Canonical claim encoding

The claim is serialized to a UTF-8 string as compact JSON with keys in a fixed
order and no whitespace. This is the `message` field that gets signed.

**Key order:** `platform` → `handle` → `proof_url` (omitted when absent).

**Python recipe:**

```python
ordered = {'platform': claim['platform'], 'handle': claim['handle']}
if claim.get('proof_url') is not None:
    ordered['proof_url'] = claim['proof_url']
message = json.dumps(ordered, separators=(',', ':'), ensure_ascii=False)
```

**JavaScript recipe:**

```js
const ordered = { platform: claim.platform, handle: claim.handle };
if (claim.proof_url !== undefined && claim.proof_url !== null) {
    ordered.proof_url = claim.proof_url;
}
const message = JSON.stringify(ordered);
```

`JSON.stringify` and `json.dumps(ensure_ascii=False)` produce byte-identical
output for any claim containing only BMP characters. For handles containing
characters outside the BMP, both implementations encode them as raw UTF-8
(Python's `ensure_ascii=False`) or the native Unicode string (JS's default),
producing the same UTF-8 bytes on the wire.

**A verifier MUST reject any proof whose `message` bytes differ from the
result of reconstructing the canonical message from the parsed claim.** This
reconstruction check catches:

- reordered keys (e.g. `handle` before `platform`)
- whitespace or indentation in the JSON
- extra or unknown fields in the message
- escaped Unicode (e.g. `\u00f8` instead of the raw UTF-8 bytes for `ø`)

---

## Envelope: a `gc-msg-v1` proof

The canonical claim string is signed as a standard `gc-msg-v1` message using
`sign_message` / `signMessage`. No new signing scheme is introduced. The
envelope format is identical to stake attestations; only the `message`
contents differ.

### gc-msg-v1 canonical string

The signer constructs — and the verifier reconstructs — a canonical string
formed by joining exactly these five fields with newline (`\n`) characters,
**in this order**, with no trailing newline:

```
gc-msg-v1
<version>
<address>
<timestamp>
<sha256hex(message)>
```

Field-by-field rules:

| Field | Value |
|---|---|
| `gc-msg-v1` | Literal scheme identifier, always this exact string |
| `<version>` | Always the string `"1"` |
| `<address>` | Signer's GC address (see Address derivation below) |
| `<timestamp>` | Decimal integer, Unix seconds (same value as `proof['timestamp']`) |
| `<sha256hex(message)>` | Lowercase hex SHA-256 of the UTF-8 bytes of `proof['message']` |

The canonical string is UTF-8 encoded to bytes before signing. The signature
algorithm is **RSASSA-PKCS1-v1_5 with SHA-384** over these bytes, using the
wallet's RSA-2048 private key. The signature is encoded with standard base64
(RFC 4648, `+` and `/`).

For the address derivation algorithm (public key → GC address), see
`docs/api-auth-protocol.md` — the derivation is identical: DER
SubjectPublicKeyInfo → `sha256(sha512(der_bytes))` → Base58Check → `GC…GC`
wrapper.

### Proof fields

A signed social binding proof is a JSON object with the seven fields below.
Verifiers validate these fields and ignore any additional keys. Note that
extra keys are **not covered by the signature** — the canonical string
binds only the fields below (`message` via its digest, `public_key` via
address self-certification) — so consumers MUST NOT read or trust any
other key found on a proof object.

| Field | Type | Value |
|---|---|---|
| `scheme` | string | `"gc-msg-v1"` |
| `version` | string | `"1"` |
| `address` | string | Signer's GC address (`GC…GC`) |
| `public_key` | string | Standard base64-encoded DER SubjectPublicKeyInfo of signer's RSA public key |
| `timestamp` | string | Decimal integer Unix seconds (as a string, not a number) |
| `message` | string | Canonical claim JSON (see Canonical claim encoding above) |
| `signature` | string | Standard base64 RSASSA-PKCS1-v1_5/SHA-384 signature over the canonical string bytes |

---

## Social-side statement: the armored form

What the handle's account posts publicly is the **armored proof** — a
copy-paste-friendly text block:

```
-----BEGIN GUMPTION SIGNED MESSAGE-----
<message>
-----BEGIN GUMPTION SIGNATURE-----
<base64 of JSON-serialized proof object>
-----END GUMPTION SIGNED MESSAGE-----
```

The signature block is a standard base64-encoded JSON serialization of the
full proof object (all seven fields). The exact whitespace and key order of
that JSON are **implementation-defined and NOT canonical**: Python's
`to_armored` uses `json.dumps` with default separators (`, ` / `: `), while
JS's `toArmored` uses `JSON.stringify` (compact, no spaces) — the resulting
blob bytes differ between implementations, and that is intentional. Verifiers
MUST NOT byte-compare armor blobs; they must decode the base64, parse the
JSON, and verify the contained proof. The cleartext between the two `BEGIN`
markers is `proof['message']` — the canonical claim string, reproduced
verbatim for human inspection. `from_armored` / `fromArmored` rejects any
armored block where the cleartext does not match `proof['message']` from the
decoded signature block.

**What a directory service must verify:**

1. Fetch the post at `proof_url` (or at the platform-canonical location for
   the handle, for platforms with no `proof_url` in the claim).
2. Parse the armored block from the fetched document using `from_armored` /
   `fromArmored`.
3. Verify the `gc-msg-v1` proof signature (steps 1–3 of the Verification
   procedure below).
4. Confirm the claim inside the proof matches the handle and platform being
   registered (i.e. `claim['handle'] == registered_handle` and
   `claim['platform'] == registered_platform`).
5. Confirm the signer (`proof['address']`) is the wallet being bound.

All five checks must pass for a binding to be considered verified. Step 3 is
this spec's domain; steps 1–2 and 4–5 are the directory's domain.

---

## Verification procedure

The following checks are performed in order. Any structural failure raises
`BadAttestationError`. Signature and freshness failures are returned as
`reasons` entries in the verdict dict rather than raised exceptions, so the
caller can inspect the partial result.

1. Parse `proof['message']` as JSON and validate the claim schema (platform
   pattern, non-empty handle, code-point limits, `https://` on `proof_url`).
2. Reconstruct the canonical message from the parsed claim and compare
   byte-for-byte to `proof['message']`. Any difference → `BadAttestationError`
   (`'non-canonical binding claim encoding'`).
3. Verify the `gc-msg-v1` envelope:
   a. `scheme` must be `"gc-msg-v1"` and `version` must be `"1"`.
   b. `public_key` must be a valid RSA-2048 DER SubjectPublicKeyInfo in
      standard base64; its derived address must equal `proof['address']`.
   c. Reconstruct the canonical string from `address`, `version`, `timestamp`,
      and `sha256hex(message)`.
   d. Verify the `signature` against the canonical string using the public key.
4. If `max_age` is supplied: `abs(now − int(proof['timestamp'])) <= max_age`.
   Both stale and far-future timestamps are rejected (symmetric window).

`verify_binding` returns a verdict dict:

```python
{
    'valid': bool,                 # True iff all checks pass
    'checks': {'signature': bool}, # per-check flags for merging
    'signer': str,                 # proof['address']
    'claim':  dict,                # parsed and validated claim
    'reasons': list[str],          # [] or ['bad-signature'] or ['expired']
}
```

`valid` is `True` iff all values in `checks` are `True`. A directory service
may add a `'proofside'` key to `checks` after performing its own fetch and
match, then recompute `valid`.

**`verify_binding` never fetches `proof_url`** — the function is pure
(no I/O). Bindings have no built-in expiry: `verify_binding` defaults to
`max_age=None`, which disables the freshness window entirely. Revocation is
the directory's responsibility: remove the social-side post, re-verify, and
update the stored record. Re-verification cadence is verifier policy.

---

## Worked example

**Claim (vector 0 from `gc-binding-vectors.json`):**

```json
{"platform": "github", "handle": "gumptionthomas"}
```

**Step 1 — canonical message:**

```python
json.dumps(
    {'platform': 'github', 'handle': 'gumptionthomas'},
    separators=(',', ':'),
    ensure_ascii=False,
)
# → '{"platform":"github","handle":"gumptionthomas"}'
```

```
message = {"platform":"github","handle":"gumptionthomas"}
```

**Step 2 — SHA-256 of message:**

```python
hashlib.sha256(message.encode()).hexdigest()
# → 91b38993e0cd08e12ca9de336cb4261e2fd0e3624417a82dee7fa87fe0e5006e
```

**Step 3 — gc-msg-v1 canonical string** (fields separated by `\n`, shown
here on separate lines):

```
gc-msg-v1
1
GCFZ3Ce1Nt9nTppJBqUFqeqqKHepfJnbRY5qeCN9RNV6sCGC
1700003000
91b38993e0cd08e12ca9de336cb4261e2fd0e3624417a82dee7fa87fe0e5006e
```

**Step 4 — proof object** (with abbreviated `public_key` and `signature`):

```json
{
  "scheme":     "gc-msg-v1",
  "version":    "1",
  "address":    "GCFZ3Ce1Nt9nTppJBqUFqeqqKHepfJnbRY5qeCN9RNV6sCGC",
  "public_key": "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAt239…AQAB",
  "timestamp":  "1700003000",
  "message":    "{\"platform\":\"github\",\"handle\":\"gumptionthomas\"}",
  "signature":  "dchY/oRfQjuCEB3rbq0k7zoOvV/8cPmxv4Jb7JI0naSc5lf…sNg=="
}
```

Full `public_key`:

```
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAt239j0ePdOUAyRhh
PoMgKDHE5ZoC67VnnmDVW1YbJ/iEeyneil7HVnHtO1nnS2hjbI4pDC1Z3620
lxVSaXttrxV2eIMl9JzfiO6tr9aeIOxNZV81XKOsu5cwwYtepYV2SY8lH4+p
SFngySFbmUf3GNdTmeBqzRbG+y5lEdmMHaTqLtCEvfWhY7+5jajPiYWABfKU
jtcmTwAdsriEmEfG4MWg8ZKF1WIKzFk6saBbob+koXnoQ73axWryngO+Q/Iqj
cqd0EPxoS3p1dsGMOLRcJ6E02cGzixq68jcDm+Ggs9aICL6MH5gCG0ntbcYC
jn4NH9mh84P9ozpR5TkNnkwqwIDAQAB
```

Full `signature`:

```
dchY/oRfQjuCEB3rbq0k7zoOvV/8cPmxv4Jb7JI0naSc5lfUDyNMi44OQIbf
R18KUKbVuQiAbIX40nyWySdCyYdM5/XxPFBg9Wa1phDoHnjXbrsJtZ/mQAaDj
cgS8ZDOyrwxU/mPwM++7w20HJPHZpSR8pNM7+wimDfW+9l3HaL93WSuM3GW+S
zibaL5hci7pVTJftltMvr0wVe+rEXZuXuRyo/obmpDimBB+QsJDrPkFl2agPn
/dC+VVVvEAaYVqxfUrXU0GxSV8oICVta9f5M3p5oO3iV+uD5ABZsW+tGJVv19
mFmVqUtHydEsQHvdjp5dSv6XBPrICdfeQFzsNg==
```

**Step 5 — armored statement** (post this to the social-platform account):

```
-----BEGIN GUMPTION SIGNED MESSAGE-----
{"platform":"github","handle":"gumptionthomas"}
-----BEGIN GUMPTION SIGNATURE-----
eyJzY2hlbWUiOiAiZ2MtbXNnLXYxIiwgInZlcnNpb24iOiAiMSIsICJhZGRy
ZXNzIjogIkdDRlozQ2UxTnQ5blRwcEpCcVVGcWVxcUtIZXBmSm5iUlk1cWVD
TjlSTlY2c0NHQyIsICJwdWJsaWNfa2V5IjogIk1JSUJJakFOQmdrcWhraUc5
dzBCQVFFRkFBT0NBUThBTUlJQkNnS0NBUUVBdDIzOWowZVBkT1VBeVJoaFBv
...
-----END GUMPTION SIGNED MESSAGE-----
```

(The full base64 signature block is the standard base64 encoding of the
complete proof object above, serialized with Python's `json.dumps` defaults —
this example shows the Python serialization.)

---

## Function reference

### Python — `gumptionchain.attestation`

| Function | Description |
|---|---|
| `build_binding_message(claim) -> str` | Validate claim schema and return the canonical message string |
| `sign_social_binding(wallet, claim, timestamp=None) -> dict` | Sign the canonical message; returns the full proof dict |
| `parse_social_binding(proof) -> dict` | Validate proof structure and canonical encoding; returns the claim dict |
| `verify_binding(proof, max_age=None) -> dict` | Pure verification — shape + signature only; returns verdict dict |

### JavaScript — `gc-attestation.mjs`

| Function | Description |
|---|---|
| `buildBindingMessage(claim)` | Validate claim schema and return the canonical message string |
| `signSocialBinding(wallet, claim, {timestamp})` | Async; sign the canonical message; returns the full proof dict |
| `parseSocialBinding(proof)` | Validate proof structure and canonical encoding; returns the claim dict |
| `verifyBinding(proof, {maxAge})` | Async, pure; shape + signature only; returns verdict dict |

The JS functions are exported from the same module as the stake-attestation
functions (`gc-attestation.mjs`), vendored to `static/wallet/gc-attestation.mjs`
via `scripts/sync_wallet.py`. Consumers import from one file for both claim
types.

`verify_binding` / `verifyBinding` is **pure** — it never fetches `proof_url`
and performs no network I/O. Bindings have **no expiry by default**; pass
`max_age` / `{maxAge}` (seconds) to opt into a freshness window. Revocation is
effected by removing the social-side post; re-checking cadence is verifier
policy.

---

## Domain separation

Binding claims and stake-attestation claims are structurally disjoint. A
binding claim requires `platform` + `handle` (and optionally `proof_url`); the
canonical check rejects any extra key. A stake claim requires `txid` + `kind`
+ `amount` + (`subject` or `address`); its canonical check likewise rejects
extras. Each parser rejects the other family's messages, so no signature can
be replayed across claim types. The optional `handle` field on stake claims
remains self-asserted display sugar; this envelope is the verifiable link a
directory service can cross-check it against.

---

## Algorithm reference

| Property | Value |
|---|---|
| Envelope scheme | `gc-msg-v1` |
| Scheme version | `"1"` |
| Signing algorithm | RSASSA-PKCS1-v1_5 |
| Signing hash | SHA-384 |
| Signature encoding | Standard base64 (RFC 4648, uses `+` and `/`) |
| Public key encoding | Standard base64 of DER SubjectPublicKeyInfo |
| Message digest algorithm | SHA-256 (hex digest, lowercase) |
| Address derivation hash | `sha256(sha512(der_pubkey))` then Base58Check, wrapped `GC…GC` |
| Timestamp format | Decimal integer, Unix seconds (stored as a string in the proof) |
| Key order in canonical JSON | `platform` → `handle` → `proof_url` (when present) |
| Unicode normalization | None applied; bytes are used as-is |
| Expiry | None by default; `max_age` opt-in, symmetric window |
