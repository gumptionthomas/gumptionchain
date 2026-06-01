# API Auth: Per-Request Wallet Signatures (replace the token handshake) — Design

**Status:** Draft for review
**Date:** 2026-06-01
**Supersedes the auth handshake.** Replaces the roll-your-own challenge/response + HS256 bearer-JWT model with stateless per-request signatures made by the caller's wallet key and verified against the address's public key. Chosen via the [API authentication audit](../audits/2026-05-31-api-authentication-audit.md) Recommendations (the "protocol replacement" design cycle), candidate (a)/per-request-signature variant.

## Motivation

The audit identified two structural roots. **R1** (the JWT's authorization model — `rol` not re-validated, no `iss`/`aud`) is already remediated (PRs #105/#107/#109). **R2** remains: the handshake hand-rolls "RSA-OAEP-encrypt a random UUID + argon2-hash it," while the chain's own signing primitive (`Wallet.sign`, RSA-PKCS1v15-SHA384 — used for every transaction and block) sits unused for auth. The bearer model also keeps a stateful `ApiToken` table, runs argon2 on an unauthenticated endpoint (the A2.c/A7.a surface), and relies on a shared symmetric `SECRET_KEY` (the A1.a blast radius).

cancelchain's identity model already binds an address to a public key (`address = CC + b58(mill_hash(der(pubkey))) + CC`) and signs everything with wallet keys. Authenticating API requests the same way — sign the request, verify with the public key — is the architecturally honest fit. It is stateless and dissolves four open findings outright.

## Goal

Authenticate every API request with a wallet signature over a canonical request string, verified against the caller's public key (self-certified by the address). No tokens, no `ApiToken` table, no argon2, no symmetric auth secret. Preserve the role-authorization model (live `Role.address_role` per request) and node-binding. Take the audit to **0 Critical / 0 High / 0 Medium / 0 Low**.

## Decisions taken during brainstorming

- **Per-request signatures, not a bearer token** (over the signed-nonce interim and the client-assertion→bearer variants). Stateless; closes bearer-replay structurally; best fit for the chain's signed-everything model. Pre-1.0 with no deployed nodes — no migration/compat constraint.
- **Thin signer over `Wallet.sign`, not RFC 9421 + a library.** The chain already signs canonical byte strings with `Wallet.sign` (txns, blocks); a request signer is the same proven pattern, not a new roll-your-own. Avoids a new dependency and the RFC 9421 algorithm mismatch (`Wallet.sign` is PKCS1v15-SHA384, not an RFC 9421 registered RSA alg). The interop benefit of RFC 9421 is moot — two controlled in-house clients (peer gossip + CLI).
- **Replay guard: timestamp freshness only (stateless), ±300s window.** No nonce, no cache, no shared store; correct across multiple workers. Already a ~48× smaller replay window than today's 4-hour bearer, for zero state. Under the audit's TLS precondition, on-wire capture is out of scope.
- **Atomic single-PR swap, not staged dual-auth.** No deployed nodes → no compatibility window → dual-support would be pure overhead.

## Architecture

### The canonical request string

Newline-joined, deterministic; the client signs it and the server reconstructs it byte-for-byte:

```
method            # request method, uppercased, e.g. "POST"
path              # request path only (no scheme/host), e.g. "/api/block/<hash>"
query_string      # the raw URL query string ("" when absent)
body_sha256_hex   # hex sha256 of the raw request body (sha256 of b"" for GET/empty)
node_host         # the target node identity: host_address(<target url>)[0], e.g. "localhost:8080"
timestamp         # request time, unix seconds, as a decimal string
address           # the caller's CC address
```

Rationale per field: `method`/`path`/`query`/`body_sha256` bind the signature to the exact request (tamper-evident); `node_host` binds it to the receiving node (preserves A3.b — a signature minted for node A fails on node B); `timestamp` bounds replay; `address` ties the signature to the claimed identity (also self-certified against the public key).

**Canonicalization matching — the critical correctness detail.** Because this is a thin signer (no RFC 9421 library standardizing the wire form), the client and server MUST derive `path` and `query_string` to **identical bytes**, or every signature fails verification. The rule: both sides use the **server-side, Werkzeug-decoded** forms — server reads `request.path` and `request.query_string.decode()`; the `ApiClient` signs the exact same path and query string it is about to send to `httpx` (it controls both, so it canonicalizes to match what Werkzeug will present). No reordering, re-quoting, or normalization on either side. The load-bearing guard is the round-trip test: a real `ApiClient` request routed through `requests_proxy` into the app must verify — if canonicalization drifts, that test fails immediately. (Paths already in use, e.g. `/api/block/<64-char mill_hash>`, route correctly today; the signer must reproduce whatever Werkzeug yields for them, not re-derive it.)

### Wire format (headers)

The client sends:
- `CC-Address`: the caller's CC address.
- `CC-Public-Key`: the caller's public key, base64 (`Wallet.public_key_b64`).
- `CC-Timestamp`: unix seconds (decimal string), matching the signed value.
- `CC-Signature`: base64 RSA signature (`Wallet.sign(canonical.encode())`).

(Distinct `CC-*` headers, not a packed `Authorization` value — simpler to parse and to assert in tests. The `Peer-Hosts` gossip header is unchanged and is **not** part of the signed canonical — it is a hop-by-hop loop guard, not request content.)

### Server: the rewritten `authorize()` (`src/cancelchain/api.py`)

The decorator interface is unchanged (`authorize(required_role)` → `authorize_reader/transactor/miller/admin`, injecting `_address`/`_role`). The wrapper body becomes:

1. Read `CC-Address`, `CC-Public-Key`, `CC-Timestamp`, `CC-Signature`; any missing/blank → `abort(401)`.
2. Parse `CC-Timestamp` to a number; non-numeric → `abort(401)`. Freshness: `abs(now_seconds − ts) > 300` → `abort(401)`.
3. `validate_address(public_key_b64, address)` (pubkey must hash to the claimed address) → else `abort(401)`.
4. Reconstruct the canonical string from the live request: `request.method`, `request.path`, `request.query_string` (decoded), `sha256(request.get_data()).hexdigest()`, `host_address(current_app.config['NODE_HOST'])[0]`, the `CC-Timestamp` value, `address`.
5. `Wallet(b64ks=public_key_b64).validate_signature(canonical.encode(), signature_b64)` → else `abort(401)`. (A signature made for a different node fails here because `node_host` differs.)
6. `role = Role.address_role(address)`; `role is None or role.value < required_role.value` → `abort(403)`. Inject `kwargs['_address'] = address`, `kwargs['_role'] = role`; call the view.

All exceptions in 1–5 funnel to `abort(401)` (a malformed/forged credential is unauthenticated); insufficient role is `abort(403)`. A small module helper — e.g. `verify_signed_request(required_role) -> tuple[address, role]` or an inline block — keeps `authorize()` readable. No DB access, no token state.

A note on `request.get_data()`: reading the body for the digest is safe for these endpoints (Flask caches it; the views re-read `request.data`/`request.json` afterward). Confirm during implementation that body-consuming views still see the body after the digest read (they do — `get_data()` caches).

### Client: `ApiClient` (`src/cancelchain/api_client.py`)

- Remove `request_token`, `get_token`, `auth_header`, and the `self.token` field.
- Add `_signed_headers(method, path, query, body) -> dict[str, str]` that builds the canonical string (using `self.host` for `node_host` — already the normalized netloc, and `self.wallet` for signing) and returns the four `CC-*` headers.
- `get()`/`post()` compute the path/query/body, call `_signed_headers`, and merge the result into the request headers. **Remove the `for _i in range(2)` 401-retry loop** — a 401 is now a genuine auth failure, not an expired token; there is nothing to refresh.
- All `get_*`/`post_*`/`post_block`/`post_transaction` convenience methods are unchanged; they sign transparently underneath. The `Peer-Hosts` header continues to be attached unsigned.

### CLI (`src/cancelchain/command.py`)

Uses `ApiClient` with a wallet — inherits signing with no change.

### Removals

- `TokenView` and the two `/api/token/<address>` url rules (`api.py`).
- `ApiToken` model (`models.py`) and the `api_token` table; `_PASSWORD_HASHER`, the `argon2` imports.
- JWT encode/decode and the `import jwt` in `api.py`.
- `PyJWT` and `argon2-cffi` from `pyproject.toml` `[project.dependencies]`; re-resolve `uv.lock` via `uv lock` (tracked, per supply-chain policy).
- `SECRET_KEY` is retired as an auth secret. The config field stays (Flask convention; tests set `FLASK_SECRET_KEY`), but no cancelchain code reads it after this change. (A1.a is thereby moot.)

### Schema migration (pre-1.0: regenerate the base migration)

Dropping `api_token` is a schema change. Per the project's pre-1.0 convention (no legacy installs, append-only Alembic starts at 1.0.0), **regenerate the single initial migration** so it no longer creates `api_token`, rather than adding a drop-migration. Verify with the `cancelchain db upgrade` + `cancelchain db check` CI gate (the model metadata, sans `ApiToken`, must match the regenerated migration). Tests use `db.create_all()` and are unaffected by the migration mechanics beyond the table's absence.

## Findings closed by this replacement

This change is the remediation for the remaining open findings — closed by removal, not by separate fixes:

- **A2.c** (unauthenticated `GET /api/token` creates `ApiToken` rows) — the endpoint and table are gone.
- **A7.a** (repeated wrong-challenge runs argon2, amplification) — argon2 is gone.
- **A1.a** (weak `SECRET_KEY` → JWT forgery) — no symmetric auth secret exists.
- **A2.e** (415/401 content-type oracle on `POST /api/token`) — the endpoint is gone.

Preserved (not regressed): **A4.a** (`Role.validate_config` + exact-match allowlists — role config is unchanged), **A3.a/A5.b** (live `Role.address_role` re-check — now inherent, step 6 of every request), **A3.b** (node-binding — now the `node_host` field of the signed canonical). Audit severity → **0 / 0 / 0 / 0**.

## Testing

### Removed (their gap ceases to exist)
- `tests/test_auth_audit.py`: `test_a1_a_*`, `test_a2_c_*`, `test_a2_e_*`, `test_a7_a_*` (dissolved findings) — deleted.
- `tests/test_api.py`: `test_post_token_none`, `test_post_token_invalid`, and any handshake-specific assertions — deleted.
- `tests/test_api_client.py`: token-handshake tests — deleted/replaced.

### Re-expressed for the signature model
- `test_roles`, `test_no_role`, and the live-role + audience tests: keep their intent; the `ApiClient`/`requests_proxy` path now signs, so happy-path access still works; the role-gating assertions are unchanged (401 token-acquisition cases become "valid signature, but [pre-conditions]" as appropriate).
- `test_auth_audit.py` survivors, re-expressed against signed requests:
  - **A3.a** → a validly-signed request from a READER-only address to a MILLER endpoint → 403 (live-role check). (No `rol` to forge anymore; the live role governs.)
  - **A3.b** → a request signed for node A (its `node_host`) presented to node B → 401 (signature fails: `node_host` mismatch). Uses the two-node `app`/`remote_app` fixtures.
  - **A5.b** → a signed request from an address removed from config → 403.
  - **A4.a** → unchanged (`Role.validate_config` rejects bad `*_ADDRESSES`).

### New negative coverage (`tests/test_api.py`)
Each asserts 401:
- tampered request (signature computed over a different path/body than sent),
- stale timestamp (`now − 301s`) and future timestamp (`now + 301s`),
- public key that does not hash to the claimed `CC-Address`,
- missing any one of the four `CC-*` headers,
- a request signed for a different `node_host`.
Plus a positive: a correctly signed request from a configured address reaches the view.

### Regression / gates
Existing block/transaction/chain tests are unaffected (transaction/block signing is untouched). Full suite green; `ruff` + `mypy` clean; `cancelchain db upgrade` + `db check` green against the regenerated migration. The xfail count drops to **0** (all auth-audit demonstrations are either dissolved-and-removed or re-expressed as passing regressions). Final suite count is re-derived during implementation (the net is: minus the handshake/dissolved tests, plus the new signature tests).

## Documentation updates

- **Audit report**: close A1.a/A2.c/A2.e/A7.a as "remediated by protocol replacement (PR #N)"; headline → `0 Critical / 0 High / 0 Medium / 0 Low`; a short "Resolution" note that the handshake was replaced with per-request wallet signatures, dissolving the token-endpoint and symmetric-key findings; update the Recommendations/Targeted-vs-replacement section to record the chosen path as implemented.
- **CLAUDE.md**: rewrite the API-auth paragraph — requests are authenticated by a per-request wallet signature over a canonical string (method/path/query/body-digest/node-host/timestamp/address), sent in `CC-*` headers and verified against the address's public key; no token, no `SECRET_KEY` for auth, stateless with a ±300s freshness window.
- **ROADMAP**: close the "API auth protocol replacement (design cycle)" entry and the A2.c/A7.a + A1.a/A2.e remediation entries; note the audit is fully closed 0/0/0/0.

## Out of scope

- Transaction/block signing, the `Wallet` signing primitive, and the address scheme — untouched.
- Rate limiting / DoS at the infra layer (an unsigned request now fails fast at signature verify with no argon2; there is no expensive unauthenticated path left to amplify).
- RFC 9421 wire compatibility / third-party clients (none exist).
- A nonce-based replay cache (timestamp-freshness chosen; revisit only if the TLS precondition is ever dropped).
- Any change to the browser layer (still has no auth).

## Acceptance criteria

- `authorize()` verifies a per-request wallet signature (canonical string as specified), self-certifies pubkey→address, enforces a ±300s timestamp window and node-binding, then the live `Role.address_role` gate; no token, no `ApiToken`, no argon2, no `SECRET_KEY` read by auth.
- `ApiClient` signs every request via `_signed_headers`; `request_token`/`get_token`/`auth_header`/`self.token` and the 401-retry loop are removed; the CLI works unchanged.
- `TokenView`, the `/api/token` routes, the `ApiToken` model/table, and the `PyJWT` + `argon2-cffi` deps are removed; the base migration is regenerated without `api_token`; `uv.lock` re-resolved and committed.
- Audit findings A1.a/A2.c/A2.e/A7.a are closed-by-replacement; A4.a/A3.a/A5.b/A3.b properties preserved; audit headline `0/0/0/0`.
- Tests: dissolved-finding tests removed; survivors re-expressed; new negative signature coverage added; full suite green with **0 xfailed**; `ruff`/`mypy`/`db check` green.
- CLAUDE.md, audit report, and roadmap updated.
