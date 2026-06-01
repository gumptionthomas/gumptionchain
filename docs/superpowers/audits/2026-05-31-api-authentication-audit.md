# Cancelchain API authentication threat-modeled audit

**Date:** 2026-05-31
**Methodology spec:** `docs/superpowers/specs/2026-05-31-api-auth-audit-design.md`
**Demonstration tests:** `tests/test_auth_audit.py`

## Preconditions

- **TLS assumed.** HTTPS is an explicit deployment precondition. On-wire interception/replay of the bearer JWT or the decrypted challenge is out of scope as a transport concern.
- **Verification pipeline assumed sound** (audited separately, #84). This audit examines only the gate in front of it.
- **No browser auth exists** (`browser.py` has no sessions/login); nothing to audit there.

## Executive summary

**8 findings: 0 Critical / 0 High (1 remediated) / 5 Medium / 2 Low.** No finding is an existential auth bypass with zero preconditions, and the JWT decode path is sound — `HS256` is pinned, and `alg=none`, RS256-confusion, expired, and malformed tokens all fail closed to `401`. The authorization *model*, not the token verification, is where the gaps are.

The lone High, **A4.a** (remediated, PR #105), was an unvalidated `*_ADDRESSES` regex — an overbroad pattern such as `CC.*CC` silently granted every authenticated address that role, with no key compromise required and no warning from the code. It is fixed: role matching is now exact-address membership, and the allowlists are validated at startup (`Role.validate_config` → `InvalidRoleConfigError`).

The Medium findings cluster on a single root cause: **`authorize()` trusts the signed `rol` claim and never re-validates it against live config.** That one omission is demonstrated three ways — a forged role is honored (A3.a), a config-revoked role keeps working for up to 4 hours (A5.b), and a token is accepted by any node sharing `SECRET_KEY` (A3.b) — and is closed primarily by a one-line per-request `Role.address_role()` re-check (plus `iss`/`aud` binding for A3.b). The remaining Mediums (A2.c, A7.a) are resource-amplification and address-enumeration surface on the *unauthenticated* token endpoint, which both creates DB rows and runs argon2 with no rate limiting.

Two cross-cutting observations refine the picture: `authorize_admin` is bound to no endpoint (so ADMIN ≡ MILLER today, which *tempers* A3.a/A4.a's real blast radius), and the handshake is a roll-your-own challenge — it hand-rolls RSA-OAEP encryption + argon2 over a 122-bit random secret while ordinary RSA signatures (`Wallet.sign`) sit unused. The most-capable adversaries (authorized insider; the role-forger) surfaced the cluster above but no privilege-escalation-without-precondition; Adversary 6 was fully clean.

**Recommended next action:** land the remaining targeted fixes — the live-role re-check and a `SECRET_KEY` length check — then open a separate design cycle to evaluate replacing the handshake (signed-nonce reusing `Wallet.sign`, or RFC 9421 / RS256 client-assertion) and adding JWT claim hygiene. See Recommendations for the full targeted-fixes-vs-replacement analysis.

## Threat model

The audit considers 7 adversary categories. Each is defined by capabilities (what the adversary can do, including key-holding and role state) and goals (what they would attempt). The 7 descriptions are restated below alongside their traces.

## Methodology

For each attack attempt:

1. **Pre-state:** what's true (config, wallets, chain, token rows) when the attack begins.
2. **Attack:** the exact request / token / input the attacker sends.
3. **Trace:** which functions get called, in what order, what they check (cite `file.py:line`).
4. **Outcome:** REJECTED at step N (no finding) or ACCEPTED (gap — finding produced).
5. **Finding (if gap):** severity (Critical/High/Medium/Low) + one-line remediation sketch.
6. **Demonstration test (if gap):** a `@pytest.mark.xfail(strict=True)` test in `tests/test_auth_audit.py`.

Findings are ID'd as `A<N>.<letter>` where `N` is the adversary number (1-7) and `letter` is the attack within that adversary's enumeration. E.g., `A3.b` = adversary 3 (token forger), attack b.

## Findings table

**8 findings: 0 Critical / 0 High (A4.a remediated) / 5 Medium / 2 Low.** Sorted by severity, then ID. Three of the Mediums (A3.a, A5.b, A3.b) collapse onto one shared root remediation, and two more (A2.c, A7.a) onto another — see Recommendations.

| ID | Category | Severity | Description | Remediation sketch | Test |
|---|---|---|---|---|---|
| A4.a | 4 | High | ✅ (remediated, PR #105) Operator `*_ADDRESSES` regex was unvalidated; an overbroad pattern (e.g. `CC.*CC`) silently escalated every authenticated address to that role | Replaced with exact-address membership + a READER-only `"*"` sentinel, validated at startup (`Role.validate_config`) | `test_a4_a_overbroad_admin_regex_does_not_escalate` |
| A2.c | 2 | Medium | Unauthenticated `GET /api/token/<address>` persists an `ApiToken` row (and runs argon2) for any on-chain address, with no eviction | Require proof-of-key before persisting / cap unredeemed rows / rate-limit the endpoint | `test_a2_c_unauthenticated_row_creation` |
| A3.a | 3 | Medium | `authorize()` trusts the signed `rol` claim and never re-validates it against live `Role.address_role()`; a token claiming a role the address lacks is honored | Re-check `Role.address_role(address)` in `authorize()` per request; reject if the live role is below the claimed role | `test_a3_a_forged_role_claim_accepted` |
| A3.b | 3 | Medium | JWT carries no `iss`/`aud`; a token is accepted by any node sharing `SECRET_KEY` (cross-node replay) | Add and verify `iss` (node) + `aud` (`cancelchain`) claims | `test_a3_b_cross_node_token_replay` |
| A5.b | 5 | Medium | A role removed from `*_ADDRESSES` keeps working until the 4h JWT expires (same missing live re-check as A3.a) | Same per-request live-role re-check as A3.a | `test_a5_b_stale_role_rejected_after_config_revocation` |
| A7.a | 7 | Medium | Unlimited wrong-challenge `POST`s each run a full argon2id verify with no attempt counter or challenge invalidation | Invalidate the challenge after N failures; add an attempt counter / rate limit | `test_a7_a_repeated_wrong_challenge_invalidates_token` |
| A1.a | 1 | Low | No startup check that `SECRET_KEY` meets a minimum length; a weak key allows offline JWT forgery with no app-level signal | Assert `len(SECRET_KEY) >= 32` (raise/warn) in `create_app()` after config load | `test_a1_a_weak_secret_key_startup_check` |
| A2.e | 2 | Low | `POST` with a wrong `Content-Type` returns 415 when a token row exists but 401 when not, leaking whether an address is known to the node | Normalize the rejection (consistent 400/401) regardless of row existence | `test_a2_e_content_type_oracle` |

## Per-adversary traces

### Adversary 1: Anonymous outsider

**Capabilities:** No wallet, no key, no role. Can send arbitrary HTTP to any endpoint. Can read the public chain (so can recover the public key of any address that has transacted).

---

#### Attack a: Reach a protected endpoint with no token / a malformed Authorization header

**Pre-state:** A fresh Flask app with no prior authentication. The adversary has no token.

**Attack:** Send a GET request to `/api/block` (protected by `authorize_reader`) with no `Authorization` header, with a bare string like `Authorization: notabearer`, or with `Authorization: Bearer ` (empty token after strip).

**Trace:**
1. `api.py:248` — `token = request.headers.get('Authorization')` returns `None` (or the raw string).
2. `api.py:249-252` — `if token and token.startswith('Bearer '):` strips the prefix; if the header is absent or does not start with `'Bearer '`, `token` is set to `None`.
3. `api.py:253` — `if token:` — empty string or `None` is falsy; the entire `jwt.decode` block is skipped.
4. `api.py:244` — `authorized` was initialised to `False` and was never set to `True`.
5. `api.py:268-272` — `if authorized:` is `False`; `abort(401)` is called.

**Outcome:** REJECTED at step 5 via `abort(401)`. No finding.

**Result:** Correctly rejected. No finding.

---

#### Attack b: Forge a JWT the server accepts — alg=none, RS256-as-HS256 confusion, SECRET_KEY weakness

**Pre-state:** Adversary knows the public key of any address from the chain history. Server uses `jwt.decode(..., algorithms=['HS256'])` at `api.py:254-258`.

**Sub-attack b1 — alg=none:**
1. Adversary crafts a token with header `{"alg":"none","typ":"JWT"}` and payload `{"sub":"<victim>","rol":"ADMIN","exp":9999999999}`, appended with an empty signature.
2. `api.py:254-258` — `jwt.decode(token, SECRET_KEY, algorithms=['HS256'])` is called.
3. PyJWT 2.13 sees `alg=none` which is not in the allowed list `['HS256']`; raises `jwt.exceptions.InvalidAlgorithmError`.
4. `api.py:265-267` — caught by `except Exception`; `abort(401)`.

**Outcome:** REJECTED at step 4 via `InvalidAlgorithmError` → `abort(401)`. No finding.

**Sub-attack b2 — RS256-signed token presented to HS256 verifier (algorithm confusion):**
1. Adversary generates an RSA keypair, signs a crafted payload as RS256, and sends the resulting token.
2. `api.py:254-258` — `jwt.decode(token, SECRET_KEY, algorithms=['HS256'])` is called. The token's `alg` header is `RS256`, which is not in `['HS256']`; `InvalidAlgorithmError` is raised.
3. `api.py:265-267` — caught by `except Exception`; `abort(401)`.

**Outcome:** REJECTED at step 3 via algorithm pin. No finding.

**Sub-attack b3 — SECRET_KEY weakness (no startup entropy check):**

**Pre-state:** The code calls `jwt.encode(..., current_app.config['SECRET_KEY'], algorithm='HS256')` at `api.py:223` and `jwt.decode(..., current_app.config['SECRET_KEY'], algorithms=['HS256'])` at `api.py:254`. There is no validation of `SECRET_KEY` length or entropy at application startup.

**Attack:** An operator misconfigures `FLASK_SECRET_KEY` to a guessable value (e.g., `'secret'`, `'dev'`, a hostname). No warning is surfaced to the operator. An adversary who correctly guesses `SECRET_KEY` can issue a fully valid HS256 token for any address and role.

**Trace:**
1. `__init__.py:64` — `app.config.from_prefixed_env()` reads `FLASK_SECRET_KEY` into `app.config['SECRET_KEY']`. No length/entropy check.
2. `api.py:223` — token is issued with the weak key; `api.py:256` — tokens signed with that same weak key are accepted.
3. PyJWT 2.13 emits `InsecureKeyLengthWarning` for keys shorter than 32 bytes, but only as a runtime warning that is typically suppressed in production; no error is raised.
4. An adversary offline-brute-forces or guesses `SECRET_KEY`, mints a token `{"sub":"<any_valid_address>","rol":"ADMIN","exp":...}`, and it is accepted.

**Outcome:** ACCEPTED — there is no guard preventing a dangerously short or predictable `SECRET_KEY` from being used. The code itself does not raise an error or even log a startup warning.

**Finding A1.a — Severity Low:**

**Impact:** An operator who sets a weak `FLASK_SECRET_KEY` (< 32 bytes, or a dictionary word) has no application-level signal that tokens are forgeable. If the key is guessed or brute-forced offline, the adversary obtains the ability to forge valid JWTs for any address and role without possessing that address's private key. This is an existential auth bypass — but only if the operator first makes the deployment mistake of choosing a weak key. Under a strong random key the risk is zero.

**Remediation sketch:** Add a startup check in `create_app()` (or in `init_app()`) that asserts `len(app.config.get('SECRET_KEY','').encode()) >= 32` and raises `RuntimeError` (or at minimum logs `app.logger.warning`) if the key is too short. This is analogous to Flask's own check for `TESTING` mode and costs one line. The check should run after `app.config.from_prefixed_env()` so it sees the live value.

**Demonstration test:** `test_a1_a_weak_secret_key_startup_check`

---

#### Attack c: Exploit the authorize() exception funnel — fall-through to authorized=True, or 500 leak

**Pre-state:** Adversary sends crafted tokens designed to trigger exceptions inside the `authorize()` wrapper.

**Attack:** Send tokens that cause `KeyError` (missing `sub`/`rol`), `TypeError` (wrong type), `jwt.DecodeError` (garbage bytes), `jwt.ExpiredSignatureError` (expired), or `jwt.InvalidSubjectError` (sub=null). Observe whether any path leaks a 500, or whether `authorized` is silently left `True`.

**Trace:**
1. `api.py:244` — `authorized = False` initialised unconditionally.
2. `api.py:247-261` — inside the `try` block: `jwt.decode` raises → exception propagates; `authorized = True` at `api.py:261` is **only reachable** if `jwt.decode` succeeds AND `address` is truthy AND `role.value >= required_role.value`.
3. `api.py:263-264` — `jwt.ExpiredSignatureError` → `abort(401)` (raises `werkzeug.exceptions.Unauthorized`, propagates out of `except`).
4. `api.py:265-267` — all other exceptions (including `KeyError`, `DecodeError`, `InvalidAlgorithmError`, `TypeError`, `InvalidSubjectError`) → `current_app.logger.exception(e)` then `abort(401)`.
5. `api.py:272` — if neither exception branch was taken and `authorized` is still `False`: `abort(401)`.

**Outcome:** REJECTED at steps 3–5. No path from any exception leads to `authorized = True`. No 500 is surfaced; `exception_response` (which calls `abort(500)`) is never invoked from `authorize()`. No finding.

**Result:** Correctly rejected. No finding.

---

#### Attack d: JWT with invalid rol value or empty/None sub — failure mode observation

**Pre-state:** Adversary can craft a syntactically valid HS256-signed JWT (e.g., by replaying a stolen-but-expired token and modifying the payload — which invalidates the signature — or by any server that re-uses the same `SECRET_KEY`). Crafted payloads tested: `rol='SUPERADMIN'`, `rol` absent, `sub=''`, `sub` absent, `sub=null`.

**Trace (rol='SUPERADMIN'):**
1. `api.py:260` — `role = Role[data['rol']]` → `Role['SUPERADMIN']` → `KeyError('SUPERADMIN')`.
2. `api.py:265-267` — caught by `except Exception` → `abort(401)`.

**Trace (rol absent — no 'rol' key):**
1. `api.py:260` — `data['rol']` → `KeyError('rol')`.
2. `api.py:265-267` — caught → `abort(401)`.

**Trace (sub=''):**
1. `api.py:259` — `address = data['sub']` → `address = ''`.
2. `api.py:261` — `if address and ...` → `''` is falsy → `authorized` stays `False`.
3. `api.py:272` — `abort(401)`.

**Trace (sub absent):**
1. `api.py:259` — `data['sub']` → `KeyError('sub')`.
2. `api.py:265-267` — caught → `abort(401)`.

**Trace (sub=None in JWT payload):**
1. `api.py:254-258` — PyJWT 2.13 validates `sub` type; raises `jwt.exceptions.InvalidSubjectError("Subject must be a string")`.
2. `api.py:265-267` — caught by `except Exception` → `abort(401)`.

**Outcome:** All malformed-claim shapes REJECTED at various steps via `abort(401)`. No finding.

**Result:** Correctly rejected. No finding.

---

#### Finding A1.a — Severity Low: No startup guard against a weak or absent SECRET_KEY

**Impact:** If `FLASK_SECRET_KEY` is set to a short or guessable value, an adversary can forge HS256 JWTs bearing any address and role claim; `authorize()` will accept them. The blast radius is full authentication bypass for as long as the weak key is in use. This requires the operator to first make the deployment mistake; under a strong random key the risk is zero. Marked Low (not Medium) because it requires an operator error in addition to the adversary action, and there is no code-level mechanism (not even a test) that prevents the weak-key deployment.

**Remediation sketch:** In `create_app()` (after `app.config.from_prefixed_env()`), add:

```python
sk = app.config.get('SECRET_KEY') or ''
if len(sk.encode('utf-8')) < 32:
    raise RuntimeError(
        'SECRET_KEY must be at least 32 bytes for HS256 token signing. '
        'Set FLASK_SECRET_KEY to a cryptographically random value.'
    )
```

Alternatively, emit `app.logger.warning(...)` instead of raising if a non-blocking notice is preferred. The test suite's `TEST_SECRET_KEY` is already 35 bytes and would pass the check.

**Demonstration test:** `test_a1_a_weak_secret_key_startup_check`

### Adversary 2: Challenge attacker

**Capabilities:** Can call GET/POST /api/token/<address> for any address, including ones whose private key it does NOT hold. Can read responses.

---

#### Attack a: Decrypt-bypass — complete handshake without the private key

**Pre-state:** Any address whose wallet the attacker does not hold.

**Attack:** Attempt to satisfy `ApiToken.verify()` without correctly decrypting the cipher — by passing `None`, an empty string, an integer, a list, or any non-string type as the `challenge` value.

**Trace:**
1. `TokenView.post` — `api.py:210`: `ApiToken.get(address)` returns the existing row.
2. `api.py:212`: `api_token.verify(request.json.get('challenge'))` is called; `request.json.get('challenge')` returns `None` for `{}`, or the literal value for other payloads.
3. `models.py:1014`: `verify(secret: object)` signature accepts `object`.
4. `models.py:1015`: `if self.expired or not self.hashed or not isinstance(secret, str): return False` — the `isinstance(secret, str)` guard fires for any non-string value (None, int, list, dict) and returns `False` unconditionally before argon2 is invoked.
5. `api.py:212`: `verify()` returns `False` → `abort(401)`.

**Outcome:** REJECTED at step 4 via `isinstance` type guard. No bypass path exists.

**Result:** Correctly rejected. No finding.

---

#### Attack b: Cipher-reuse replay — POST the same decrypted secret a second time

**Pre-state:** Attacker has legitimately completed one handshake (possesses the private key), obtains a token. Attacker now replays the same `challenge` string in a second POST within the 60-second window before a new GET re-seeds the cipher.

**Attack:** After a successful POST (step 1), attempt to POST the same decrypted challenge string again to obtain a second JWT.

**Trace:**
1. `TokenView.post` — `api.py:209-226`: First POST with correct challenge. `verify()` passes at `models.py:1017–1018`.
2. `api.py:214`: `api_token.reset()` is called → `models.py:1009-1012`: sets `self.cipher = None`, `self.hashed = None`, calls `commit()` — row is flushed to DB.
3. Replay POST arrives. `api.py:210`: `ApiToken.get(address)` returns the same row (still exists).
4. `api.py:212`: `api_token.verify(same_secret)` is called.
5. `models.py:1015`: `not self.hashed` is `True` (reset cleared it) → returns `False`.
6. `api.py:212`: `abort(401)`.

**Outcome:** REJECTED at step 5. `reset()` fires synchronously before the response is returned, and the unique argon2 hash is unconditionally cleared. The secret is single-use. No replay path exists.

**Result:** Correctly rejected. No finding.

---

#### Attack c: Unauthenticated api_token row creation for on-chain addresses

**Pre-state:** The node has a live chain with previously transacted addresses whose public keys are visible in `TransactionDAO.public_key`. The attacker observes those addresses (chain data is public).

**Attack:** For each on-chain address, call `GET /api/token/<address>`. No authentication is required. The endpoint creates one `api_token` row per previously-unseen address — rows that are never deleted.

**Trace:**
1. `TokenView.get` — `api.py:191`: `ApiToken.get(address)` returns `None` (first GET for this address).
2. `api.py:193`: `app.wallets.get(address)` — not in node wallets. Falls through to chain query path.
3. `api.py:194-200`: `node_lc_dao()` is called; if a chain exists, `lc_dao.address_transactions(address)` queries `TransactionDAO` for `address`. If the address appears in any mined transaction, a `TransactionDAO` row is returned.
4. `api.py:203`: `wallet = Wallet(b64ks=txn.public_key)` — a public-key-only Wallet is constructed from the on-chain public key.
5. `api.py:205`: `wallet` is non-None → does not abort.
6. `api.py:206`: `ApiToken.create(wallet)` — persists a new row to `api_token`. The row has `unique=True` on `address` (`models.py:975`), so only one row per address is ever created; subsequent GETs reuse it.
7. `models.py:1000-1007` (`refreshed_cipher`): called immediately; runs `_PASSWORD_HASHER.hash(secret)` (argon2, intentionally slow), an RSA-OAEP encrypt, and a DB write under two conditions: (a) every time the 60-second `expired` window elapses, AND (b) on the very first call after row creation, because `cipher` and `hashed` are both `NULL` then (the `not (self.cipher and self.hashed)` arm fires). The first-call path is the common one during address enumeration — every new address triggers an immediate argon2 hash compute, strengthening the resource-cost argument.
8. No cleanup: `ApiToken` has no `delete` method and no scheduled or trigger-based cleanup path anywhere in the codebase.

**Outcome:** ACCEPTED as a finding. An unauthenticated caller can enumerate all on-chain addresses (all public) and trigger `api_token` row creation for each, plus argon2 hash computation every 60 seconds per address per GET. Row accumulation is bounded by the set of distinct on-chain addresses (each address creates exactly one row). No authentication is bypassed — the attacker cannot decrypt the cipher without the private key. But the unauthenticated write path and the unbounded persistent row accumulation with no eviction constitute a state-amplification / resource-exhaustion vector that grows with chain size.

**Finding A2.c — Severity Medium:**

**Impact:** An unauthenticated attacker who can enumerate on-chain addresses (chain data is public) can: (a) cause `api_token` row creation for every on-chain address, growing the table in proportion to the chain's address universe with no upper bound and no eviction; (b) repeatedly GET any already-registered address to force argon2 hash computation + RSA-OAEP encrypt + DB write every 60 seconds with no rate limiting. The `api_token` table is never pruned. On a long-running public node the table accumulates stale rows for addresses that will never authenticate again. No auth bypass results — the cipher is encrypted to the target's public key; only the private-key holder can decrypt it.

**Remediation sketch:** Add a periodic cleanup job (or `ON DELETE CASCADE` from `TransactionDAO`, or a TTL via cron/background task) to evict `api_token` rows idle for longer than a configurable threshold (e.g., 24 h). Additionally, consider rate-limiting `GET /api/token/<address>` per source IP to throttle argon2 amplification. A simpler partial fix is to not call `refreshed_cipher()` eagerly inside `TokenView.get` — instead, only generate the cipher on-demand and skip the DB write if the existing cipher is still fresh.

**Demonstration test:** `test_a2_c_unauthenticated_row_creation`

---

#### Attack d: Race between two concurrent GETs for the same new address

**Pre-state:** Address is not yet in `api_token`. Two requests arrive near-simultaneously.

**Attack:** Two concurrent `GET /api/token/<address>` requests for a new address. Both find `ApiToken.get(address)` returning `None` and both attempt to call `ApiToken.create(wallet)`.

**Trace:**
1. Request A and Request B both enter `TokenView.get`. Both call `ApiToken.get(address)` at `api.py:191` — both receive `None`.
2. Both proceed past the `not wallet` guard to `api.py:206`: `ApiToken.create(wallet)`.
3. `models.py:1029-1033`: Both call `db.session.add(api_token)` then `db.session.commit()`.
4. The second commit hits the `UNIQUE` constraint on `api_token.address` (`models.py:975`) and raises `sqlalchemy.exc.IntegrityError`.
5. `TokenView.get` has no `except` for `IntegrityError` — the exception propagates and Flask returns 500.

**Outcome:** The race is theoretically possible but could NOT be deterministically reproduced as a strict-xfail in the test harness (concurrent first-INSERTs did not collide under the in-process SQLite test client), so it is recorded as an OBSERVATION, not a confirmed finding.

**Observation (no demonstration test):** Concurrent first-GETs for the same address could produce an unhandled `IntegrityError` on the second INSERT, surfacing as a 500; flagged for the Cross-cutting observations section.

---

#### Attack e: Content-Type oracle — 415 vs 401 reveals token-row existence

**Pre-state:** Attacker probes whether a given address has an active `api_token` row (i.e., has been previously GETted and is a known-good address on this node).

**Attack:** POST `GET /api/token/<address>` without `Content-Type: application/json`. Observe whether the response is 401 or 415.

**Trace:**
1. `TokenView.post` — `api.py:210`: `if (api_token := ApiToken.get(address)) is None: abort(401)` executes **before** `request.json` is accessed.
2. **If no token row exists** (address never GETted, or not known to this node): `abort(401)` fires at `api.py:211`. Response: 401.
3. **If a token row exists** (address has previously been GETted): execution continues to `api.py:212`: `api_token.verify(request.json.get('challenge'))`. Flask's `request.json` property raises `werkzeug.exceptions.UnsupportedMediaType` (HTTP 415) when `Content-Type` is not `application/json`. Response: 415.
4. The status-code difference (401 vs 415) reveals whether the address has a live `api_token` row, which in turn reveals whether the address is known to this node (either in `app.wallets` or previously observed on-chain).

**Outcome:** ACCEPTED as a Low finding. No authentication bypass; under TLS the information leakage (known-address oracle) is minor. But the discrepancy invites address enumeration without any auth.

**Finding A2.e — Severity Low:**

**Impact:** An unauthenticated attacker can distinguish "address is known to this node" (415) from "address is unknown to this node" (401) by POSTing with wrong Content-Type. This reveals the node's wallet set and the set of previously-authenticated or previously-probed on-chain addresses without any authentication. Under TLS, exploitation requires an active attacker; the leaked information (which addresses are node-local or on-chain) is largely already public via chain exploration.

**Remediation sketch:** Move the `Content-Type` check before the `ApiToken.get` lookup — either by requiring `application/json` at the routing / middleware layer for all POST `/api/token/<address>` requests, or by swapping the guard order in `TokenView.post` so that a missing JSON body always returns 400/415 regardless of token-row existence. The cleanest fix: validate `request.content_type` at the top of `TokenView.post` and return 415 uniformly, then proceed to the `ApiToken.get` lookup.

**Demonstration test:** `test_a2_e_content_type_oracle`

### Adversary 3: Token forger / cryptanalyst

**Capabilities:** Targets the JWT and its signing key directly. Knows the algorithm (HS256) and claim set. May know/guess properties of SECRET_KEY.

---

#### Attack a: SECRET_KEY reuse blast radius

**Pre-state:** A token is issued to `reader_wallet.address` (READER role) via the challenge handshake. The adversary obtains the `SECRET_KEY` value.

**Attack:** The adversary constructs a JWT directly — bypassing the handshake entirely — using the known `SECRET_KEY`, setting `sub` to any address and `rol` to `'MILLER'` or `'ADMIN'`. They present this forged token in the `Authorization: Bearer` header to a `MILLER`-protected endpoint.

**Trace:**
1. `api.py:248-252` — Bearer token extracted from header.
2. `api.py:254-258` — `jwt.decode(token, current_app.config['SECRET_KEY'], algorithms=['HS256'])` succeeds because the forged token was signed with the real key.
3. `api.py:259` — `address = data['sub']` is set to the adversary-chosen address.
4. `api.py:260` — `role = Role[data['rol']]` is set to `Role.MILLER` (or `Role.ADMIN`) directly from the forged claim.
5. `api.py:261` — `role.value >= required_role.value` → `3 >= 3` → `authorized = True`.
6. **No call to `Role.address_role(address)` is made at verification time.** The role embedded in the token is trusted unconditionally. The re-check only happens at issuance time (`api.py:215`).
7. The protected view function is invoked with `_role=Role.MILLER`, granting the adversary MILLER (or ADMIN) access.

**Secondary coupling note:** Flask uses `SECRET_KEY` for itsdangerous-backed session cookies. `cancelchain` does not currently use `flask.session` (confirmed: `grep -rn 'flask.session'` returns no hits in `src/`). If browser sessions are added in a future phase, the key leak would simultaneously compromise JWT forgery and session cookie forgery. This coupling is a design smell but carries no immediate blast radius today.

**Outcome:** ACCEPTED. A bearer of the `SECRET_KEY` can assume any role for any address with no further challenge.

**Finding A3.a — Severity Medium:**
`authorize()` trusts the `rol` JWT claim directly without re-validating it against the live `Role.address_role()` config. A `SECRET_KEY`-bearing adversary can forge any role for any address. Note: forging the `rol` claim requires possessing `SECRET_KEY`, which is already full compromise; the standalone severity of this `rol`-trust defect is Medium (same root cause as A5.b).

**Impact:** Full RBAC bypass for all API endpoints (`/api/block` POST, `/api/transaction/*`, etc.). An attacker who extracts or guesses the `SECRET_KEY` environment variable becomes an unchallenged ADMIN with no RSA key required.

**Remediation sketch:** In `authorize()` (api.py:243–272), after decoding the JWT, re-check the live role: `live_role = Role.address_role(address)`. Reject (abort 403) if `live_role is None` or `live_role.value < required_role.value`. The JWT `rol` claim should only be treated as a hint for the token-issuing node's intent; the actual gate must be the live config lookup. This also prevents stale-role replay after a role demotion.

**Demonstration test:** `test_a3_a_forged_role_claim_accepted`

---

#### Attack b: Missing claims — aud/iss absent, cross-node token replay

**Pre-state:** Two nodes (`app` and `remote_app`) are configured with identical `SECRET_KEY` values (a plausible production mistake, and present in the test suite itself — `conftest.py:381,413` both use `TEST_SECRET_KEY`). The adversary has legitimate ADMIN credentials on `app` (where `wallet.address` is in `ADMIN_ADDRESSES`) but no credentials on `remote_app` (where `wallet.address` is absent from all `*_ADDRESSES` lists — `conftest.py:409-423`).

**Attack:** The adversary authenticates normally on `app`, obtains a JWT bearing `sub=wallet.address, rol='ADMIN'`. They present this token verbatim to `remote_app`'s protected endpoints, where they have no configured role.

**Trace:**
1. Token issued by `app` at `api.py:217-226` — no `iss` or `aud` claim is included in the payload.
2. Adversary sends `Authorization: Bearer <token>` to `remote_app`.
3. `api.py:254-258` — `jwt.decode(token, remote_app.config['SECRET_KEY'], algorithms=['HS256'])` succeeds because both nodes share the key; signature validates.
4. `api.py:259-260` — `address = wallet.address`, `role = Role['ADMIN']`.
5. `api.py:261` — `Role.ADMIN.value(4) >= Role.READER.value(1)` → `authorized = True`.
6. No `aud`/`iss` claim exists to distinguish "this token was issued for node A, not node B." PyJWT would enforce `aud` if passed as `audience=` to `decode()`, but no such argument is present (`api.py:254-258`).
7. **The view function on `remote_app` is invoked as if `wallet` holds ADMIN on that node.**

**Outcome:** ACCEPTED. A token legitimately obtained from any same-key node is silently honoured by all other nodes sharing that key. The absence of `aud`/`iss` removes the only protocol-level defence against this scenario.

**Finding A3.b — Severity Medium:**
No `iss` (issuer) or `aud` (audience) claims are embedded in the JWT (`api.py:217-226`), and `jwt.decode()` is called without `issuer=` or `audience=` enforcement (`api.py:254-258`). Combined with `rol` not being re-validated against local config (see A3.a), a token legitimately minted on any node is accepted as-is by every other node sharing the same `SECRET_KEY`.

**Impact:** An operator with access on one node in a multi-node deployment can access all other nodes sharing the same `SECRET_KEY`, at the role level granted by the issuing node's config (which may be higher than the target node would allow). The attack requires only a legitimate auth on one node — no key extraction needed.

**Remediation sketch:** Add `iss` (e.g., the node's `NODE_HOST`) and `aud` (the fixed string `'cancelchain'` or the node's address) to the JWT payload at `api.py:217-226`. Pass matching `issuer=` and `audience=` to `jwt.decode()` at `api.py:254-258`. Combine with the A3.a fix (live `address_role` re-check) to get defence-in-depth: even with a shared key, a token is rejected if the issuer doesn't match or the address lacks a configured role on the receiving node.

**Demonstration test:** `test_a3_b_cross_node_token_replay`

---

#### Attack c: exp handling — clock-skew, leeway, expired-token acceptance

**Pre-state:** Any validly-issued JWT. The adversary attempts to replay it after expiry.

**Attack:** Present an expired JWT (past `exp` timestamp) to a protected endpoint.

**Trace:**
1. `api.py:221` — `'exp': now().timestamp() + API_TOKEN_SECONDS` — exp is set as a float Unix timestamp.
2. `api.py:254-258` — `jwt.decode(token, key, algorithms=['HS256'])` — PyJWT 2.13.0 validates `exp` by default; `leeway=0` (the default, confirmed by `inspect.signature(jwt.decode)`).
3. `api.py:263-264` — `except jwt.exceptions.ExpiredSignatureError: abort(401)` — expired tokens are explicitly caught and rejected.
4. No `leeway` argument is passed, so there is no grace window.
5. Tested empirically: tokens 1 second and 100 seconds past expiry both raise `ExpiredSignatureError` and reach `abort(401)`.

**Outcome:** REJECTED at step 3. No finding. PyJWT's default exp validation fires, and the except branch converts it to a 401 immediately.

**Result:** Correctly rejected. No finding.

---

#### Attack d: Algorithm pinning — alg=none / RS256 confusion

**Pre-state:** Adversary crafts a token with `alg: none` in the header, or attempts RS256 confusion by passing a public key as the HS256 secret.

**Attack:** Submit a token with header `{"alg":"none","typ":"JWT"}` and no signature, or an RS256-signed token.

**Trace:**
1. `api.py:254-258` — `jwt.decode(token, key, algorithms=['HS256'])` — the `algorithms` list is explicitly and exclusively `['HS256']`.
2. PyJWT 2.13.0 raises `InvalidAlgorithmError: The specified alg value is not allowed` for `alg=none` and for any algorithm not in the allow-list (confirmed empirically).
3. `api.py:265-267` — `except Exception as e: abort(401)` catches `InvalidAlgorithmError` and returns 401.
4. There is exactly one `jwt.decode` call in the entire codebase (`api.py:254`); no secondary decode path exists.

**Outcome:** REJECTED at step 2–3. No finding.

**Result:** Correctly rejected. Algorithm is pinned to `['HS256']`; alg=none and RS256 confusion both fail closed. This attack overlaps A1.b — see overlap notes.

### Adversary 4: Role-escalation attacker

**Capabilities:** Legitimately holds a key for some address with a LOW role (e.g. READER), wants a HIGHER role (TRANSACTOR/MILLER/ADMIN).

---

#### Attack a: Regex over-match / operator foot-gun

**Pre-state:** Attacker holds a `READER`-role key. Operator has configured `ADMIN_ADDRESSES` with a broad regex pattern (e.g. `'CC.*CC'`) believing it means "all nodes in our network" or "all cancelchain addresses". The app accepts the configuration silently.

**Attack:**
1. Attacker calls `GET /api/token/<reader_address>` to start the challenge handshake.
2. Attacker decrypts the challenge with their private key and calls `POST /api/token/<reader_address>`.
3. Server calls `Role.address_role(reader_address)` which internally calls `Role.address_roles()`.
4. The comprehension iterates over all four `Role` members and calls `re.fullmatch(x, address)` for each configured pattern.
5. `re.fullmatch('CC.*CC', reader_address)` returns a match (all valid CC-format addresses begin and end with `CC`).
6. Both `READER_ADDRESSES` and `ADMIN_ADDRESSES` match → `roles = [READER, ADMIN]`.
7. `address_role()` returns `roles[-1]` = `ADMIN`.
8. Server mints a JWT with `rol='ADMIN'`.
9. Attacker presents the JWT to a MILLER-restricted endpoint (e.g. `POST /api/block/<hash>`); `authorize(required_role=Role.MILLER)` computes `ADMIN.value=4 >= MILLER.value=3` → authorized.

**Trace:**
1. `api.py:176–181` — `address_roles()` iterates `Role` members, calls `re.fullmatch(x, address)` for each pattern list.
2. `api.py:173` — `Role.addresses()` reads `current_app.config.get(f'{self.name}_ADDRESSES')` — no validation of pattern content.
3. `api.py:184–186` — `address_role()` returns `roles[-1]`; with `ADMIN_ADDRESSES=['CC.*CC']` the list is `[READER, ADMIN]`, so `ADMIN` is returned.
4. `api.py:215` — `role = Role.address_role(address)` returns `ADMIN`.
5. `api.py:217–225` — `jwt.encode({'sub': address, 'rol': 'ADMIN', 'exp': ...})` is minted.
6. `api.py:261` — `authorize()` check: `ADMIN.value=4 >= MILLER.value=3` → `authorized = True`.

**Outcome:** ACCEPTED — the reader-key holder **received** a JWT with `rol=ADMIN` and could invoke MILLER and TRANSACTOR endpoints they were never intended to access. The code **never validated** that configured regex patterns were narrowly-scoped or distinct across role tiers. The (now-removed) `test_regex_roles` test modelled `'CC.*CC'` as a valid pattern for `READER_ADDRESSES`; the same construction in `ADMIN_ADDRESSES` silently escalated every authenticated user.

✅ Remediated (PR #105). **Finding A4.a — Severity High:**

**Impact:** Any authenticated user whose address matched an overbroad `{HIGHER_ROLE}_ADDRESSES` pattern received that higher role at token-issuance time. An operator who wrote `ADMIN_ADDRESSES = ['CC.*CC']` (a natural "all CC addresses" pattern) granted every key holder on the network ADMIN-level JWTs. No key compromise was required; normal challenge/response authentication sufficed. The blast radius was bounded to misconfigured deployments, but the code actively invited the mistake: the (now-removed) `test_regex_roles` modelled `'CC.*CC'` as a valid pattern for `READER_ADDRESSES` without noting the escalation risk of using the same pattern at higher-privilege tiers.

**Remediation sketch:** Add a startup-time or request-time warning (or hard error) when a `{ROLE}_ADDRESSES` regex would match any address that is also matched by a lower-role pattern. At minimum, document in `EnvAppSettings` (config.py) and in `CLAUDE.md`'s configuration section that `CC.*CC` is an unrestricted wildcard: it matches every valid cancelchain address. A lightweight guard: after computing `address_roles()`, log a WARNING (once, at startup) for any role whose configured patterns produce a non-trivial intersection with lower roles. (As implemented: regex matching replaced with exact-address membership + a READER-only `"*"` sentinel; `Role.validate_config` rejects non-address entries and out-of-READER `"*"` at `create_app` startup via `InvalidRoleConfigError`.)

**Demonstration test:** `test_a4_a_overbroad_admin_regex_does_not_escalate` (flipped from the original strict-xfail to a passing regression test).

---

#### Attack b: sub crafting — shape an address to match a higher-role regex

**Pre-state:** Attacker controls a wallet with an address that only matches `READER_ADDRESSES`. The higher-role regexes are narrowly scoped (e.g. exact-address literals for admin users). Attacker wants to craft or brute-force a key whose address also satisfies the higher-role pattern.

**Attack:** Attacker generates candidate wallets hoping one produces an address matching `MILLER_ADDRESSES` or `ADMIN_ADDRESSES`. Alternatively, attacker tries to inject regex metacharacters into the address used as `sub`.

**Trace:**
1. `application.py:113–117` — `AddressConverter.to_python()` calls `validate_address_format(value)` before the address reaches any view.
2. `schema.py:35–50` — `validate_address_format()` enforces: (a) value starts with `ADDRESS_TAG='CC'`, (b) value ends with `ADDRESS_TAG='CC'`, (c) `b58decode(value[2:-2])` decodes to exactly 32 bytes.
3. The base58check alphabet is `[1-9A-HJ-NP-Za-km-z]` — it contains zero regex metacharacters (`.`, `*`, `+`, `?`, `[`, `]`, `(`, `)`, `{`, `}`, `^`, `$`, `|` are all absent).
4. `wallet.py:155–157` — `Wallet.address` is computed as `CC + b58encode(mill_hash_bin(export_binary_key(public_key))) + CC`. The hash function (sha256(sha512(DER))) makes preimage attacks on a specific prefix computationally infeasible.
5. `api.py:259` — `address = data['sub']` — but the route converter already validated format before the view was invoked, so only a valid-format address can ever become `sub`.

**Outcome:** REJECTED at step 1 (route converter) / step 3 (character constraint). A valid address cannot contain regex metacharacters. A brute-force search for a key whose address matches a specific narrow higher-role regex requires a preimage on `sha256(sha512(...))`, which is infeasible. No finding.

**Result:** Correctly rejected. No finding.

---

#### Attack c: Multi-role precedence — verify `roles[-1]` is always the highest role

**Pre-state:** Address matches two non-contiguous roles, e.g. `READER` and `ADMIN` (skipping `TRANSACTOR` and `MILLER`).

**Attack:** Verify that `address_role()` returns `ADMIN` (value=4) and not `READER` (value=1) in this case, and that Python enum iteration order cannot cause `[-1]` to return a lower role.

**Trace:**
1. `api.py:166–170` — `Role` enum is defined in ascending value order: `READER=1`, `TRANSACTOR=2`, `MILLER=3`, `ADMIN=4`.
2. `api.py:177–181` — `address_roles()` iterates `for role in Role` — Python's `EnumMeta.__iter__` yields members in definition order, which matches ascending value order.
3. The comprehension builds the list in iteration order: if address matches READER and ADMIN, the list is `[READER, ADMIN]`.
4. `api.py:185–186` — `roles[-1]` is the last element, which in definition order is always the highest-numbered role present in the list.
5. Non-contiguous match `[READER, ADMIN]`: `[-1]` = `ADMIN` (value 4). Correct.
6. All-matching `[READER, TRANSACTOR, MILLER, ADMIN]`: `[-1]` = `ADMIN`. Correct.

**Outcome:** REJECTED — Python guarantees enum iteration in definition order; since definitions are ascending by value, `[-1]` is provably the highest role. No finding.

**Result:** Correctly rejected. No finding.

---

#### Attack d: `rol` claim integrity — can an attacker supply or forge a higher `rol`?

**Pre-state:** Attacker holds a valid READER-role JWT. Attacker wants to substitute `rol='ADMIN'` in the token.

**Attack vector 1 — JWT tampering:** Attacker edits the `rol` field in the JWT payload to `'ADMIN'`.

**Trace:**
1. `api.py:254–258` — `authorize()` calls `jwt.decode(token, current_app.config['SECRET_KEY'], algorithms=['HS256'])`. PyJWT verifies the HMAC-SHA256 signature using `SECRET_KEY`. Any modification to the payload invalidates the signature.
2. `api.py:265–267` — A bad signature raises `jwt.exceptions.InvalidSignatureError` (caught by the broad `except Exception`) → `abort(401)`.
3. Attacker cannot produce a valid signature without `SECRET_KEY`.

**Attack vector 2 — can an attacker choose their `rol` at token issuance?** No. The `rol` claim is set exclusively at `api.py:220` inside `TokenView.post()`, after `Role.address_role(address)` is called server-side. The challenge/response handshake authenticates the address but gives the client no input to the role computation.

**Attack vector 3 — `Role[data['rol']]` key-lookup behavior:** `api.py:260` does `Role[data['rol']]`. An unknown name like `'SUPERADMIN'` raises `KeyError`, which is caught by the broad `except Exception` at `api.py:265` → `abort(401)`. No privilege escalation.

**Outcome:** REJECTED at every vector. `rol` is minted server-side; the JWT is HMAC-protected; unknown role names fail closed. No finding.

**Result:** Correctly rejected. No finding.

### Adversary 5: Replay attacker

**Capabilities:** Has captured a valid artifact — a redeemed-but-still-valid JWT, or a challenge cipher/secret (within the TLS assumption: via a compromised client, logs, or shared-host side channel, NOT on-wire).

---

#### Attack a: Challenge single-use — second POST with the same secret

**Pre-state:** Attacker has obtained the plaintext `secret` (UUID4 string) that was decrypted from a `GET /api/token/<address>` cipher. The legitimate client already POSTed it successfully and received a JWT.

**Attack:** Attacker attempts a second `POST /api/token/<address>` with the same `challenge` value.

**Trace:**
1. `TokenView.post` — `api.py:210`: `ApiToken.get(address)` fetches the row. The row exists (was not deleted, only `reset()`).
2. `api.py:212`: `api_token.verify(request.json.get('challenge'))` calls `models.py:1014`.
3. `models.py:1015`: `verify()` checks `not self.hashed` — after a successful first POST, `reset()` at `models.py:1009-1012` has already set `self.hashed = None` and committed. So `not self.hashed` is `True` → `return False`.
4. `api.py:213`: `verify()` returned `False` → `abort(401)`.

**Outcome:** REJECTED at step 3 via `verify()` returning `False` when `hashed` is `None`. The challenge secret is single-use: `reset()` clears both `cipher` and `hashed` atomically (within a single `db.session.commit()`) before the JWT is returned. A second POST cannot succeed.

**Concurrency window note (OVERLAPS A2.d):** There is a narrow TOCTOU window between line `api.py:212` (`verify()` passes) and line `api.py:214` (`reset()` commits). Within this window a second concurrent request on the same address could call `verify()` against the still-extant `hashed` value and also return `True`, allowing both to proceed to `reset()` + JWT issuance. Both resets succeed (the second sets `None` on already-`None` columns); both JWTs are legitimately issued. This is a pre-existing race documented under **A2.d** — the present attack (serialised second POST by a replay attacker) is correctly blocked. The concurrency case is out of scope here.

**Result:** Correctly rejected. No finding.

---

#### Attack b: JWT reuse within the 4-hour window

**Pre-state:** Attacker has captured a valid, unexpired JWT (e.g., from application logs, a compromised client environment, or a shared-host side channel). The JWT was legitimately issued to an address that held a role at issuance time.

**Sub-case b1 — Plain bearer token replay**

**Attack:** Attacker presents the captured JWT in an `Authorization: Bearer <token>` header to any protected endpoint within the 4-hour expiry window.

**Trace:**
1. `authorize` wrapper — `api.py:248-258`: extracts the `Bearer` token.
2. `api.py:254`: `jwt.decode(token, SECRET_KEY, algorithms=['HS256'])` — signature validates; `exp` claim is not yet exceeded; decoding succeeds.
3. `api.py:259-261`: `address = data['sub']`; `role = Role[data['rol']]`; `role.value >= required_role.value` is `True`.
4. `api.py:268-271`: `authorized = True`; request proceeds.

**Outcome:** ACCEPTED — the replayed token grants full access. This is the **expected bearer-token model** (stateless JWT, no server-side session). There is no token-revocation mechanism; the only remediation is rotating `SECRET_KEY`, which invalidates every active token for every user system-wide. Within the TLS assumption (on-wire capture excluded), this is the documented design trade-off. The window is documented as `API_TOKEN_SECONDS = 14400` (4 hours), `api.py:51`.

**Observation (no finding):** a redeemed JWT is replayable for its full ~4h lifetime with no server-side revocation — an accepted property of the bearer-token model under the TLS precondition; noted for Cross-cutting.

**Sub-case b2 — Stale-role replay after config revocation**

**Pre-state:** Address A held `MILLER` role. A valid JWT with `rol=MILLER` was issued. The operator then removes A from `MILLER_ADDRESSES` (e.g., by updating config), intending to revoke elevated access immediately.

**Attack:** Attacker (or the now-demoted address) replays the captured JWT with `rol=MILLER` within the original 4-hour window.

**Trace:**
1. `authorize` — `api.py:254`: `jwt.decode()` succeeds (signature valid, not expired).
2. `api.py:260`: `role = Role[data['rol']]` — `rol` claim is `'MILLER'`; `Role['MILLER']` = `Role.MILLER`. **The live config is never consulted here.**
3. `api.py:261`: `Role.MILLER.value >= required_role.value` for a MILLER-required endpoint → `True`.
4. Request is processed with `_role = Role.MILLER`.

**Contrast with `Role.address_role`:** The `authorize` decorator does NOT call `Role.address_role(address)` (api.py:184-186) on the incoming request. `Role.address_role` re-reads `*_ADDRESSES` from live config on every call — but `authorize` trusts the `rol` embedded in the token instead.

**Outcome:** ACCEPTED — a JWT continues to grant the role it was issued with for the full 4-hour window, regardless of subsequent config changes. An operator who revokes a user's MILLER privileges in config has no way to enforce that revocation until all outstanding tokens expire, without a full `SECRET_KEY` rotation.

**Finding A5.b — Severity Medium:**
The `rol` claim is not re-validated against live config on each request. Revoking a role from `*_ADDRESSES` config takes up to 4 hours to take effect for already-issued tokens.

**Impact:** An address whose elevated role has been revoked retains that role for up to `API_TOKEN_SECONDS` (4 hours). An operator cannot enforce immediate privilege reduction without disrupting all users via SECRET_KEY rotation.

**Remediation sketch:** Either (a) re-evaluate `Role.address_role(address)` inside `authorize` on each request and compare against the token's `rol` — reject if live role is lower than token's claimed role; or (b) shorten `API_TOKEN_SECONDS` significantly (e.g., 15 minutes) to bound the revocation lag. Option (a) is stricter and does not require reducing token lifetime.

---

#### Attack c: Expired-token edges

**Pre-state:** Attacker has a JWT with `exp` at either the exact boundary (token exactly at `exp`) or has tampered the `exp` field to a far-future value.

**Sub-case c1 — Token exactly at exp**

**Trace:**
1. `authorize` — `api.py:254`: `jwt.decode()` — PyJWT compares `now >= exp` (integer Unix timestamp). A token exactly at `exp` is treated as expired by PyJWT (no default leeway).
2. `api.py:263`: `jwt.exceptions.ExpiredSignatureError` is raised and caught → `abort(401)`.

**Outcome:** REJECTED at step 2 via `ExpiredSignatureError`. Correct boundary enforcement.

**Result:** Correctly rejected. No finding.

**Sub-case c2 — Client-supplied far-future exp**

**Attack:** Attacker tampers the JWT payload's `exp` field to a far-future timestamp and replays the modified token.

**Trace:**
1. `authorize` — `api.py:254`: `jwt.decode()` verifies the HMAC-SHA256 signature against `SECRET_KEY`. The attacker does not possess `SECRET_KEY`, so the signature on the tampered token is invalid.
2. The decode raises a `DecodeError` / `InvalidSignatureError` (subclass of `PyJWTError`).
3. `api.py:265`: caught by `except Exception` → `abort(401)`.

**Outcome:** REJECTED at step 2 via signature mismatch. The `exp` claim is server-set and signed; a client cannot forge or modify it without the `SECRET_KEY`. Correct.

**Result:** Correctly rejected. No finding.

### Adversary 6: Authorized insider

**Capabilities:** Legitimately holds a key and a role. Acts within the system but tries to exceed their grant or affect OTHER addresses.

#### Attack a: Cross-address token request

**Pre-state:** Insider holds a valid key and role (e.g., TRANSACTOR). They attempt to request a challenge for a victim address (`miller_wallet.address`) whose private key they do not hold.

**Attack:** Call `GET /api/token/<victim_address>`.

**Trace:**

1. `api.py:191` — `TokenView.get(address)` is entered with `address = victim_address`.
2. `api.py:191` — `ApiToken.get(address)` (`models.py:1023`) looks up an existing token row. None exists.
3. `api.py:193` — `current_app.wallets.get(address)` is checked. The victim's PEM is not in `app.wallets` (only the node's own wallets are loaded from `WALLET_DIR`). Result: `None`.
4. `api.py:194-201` — The code falls through to the chain-lookup path: `lc_dao.address_transactions(address)`. If the victim has no on-chain transaction either, `txn` is `None`.
5. `api.py:204` — `if not wallet: abort(401)`. The request is rejected before an `ApiToken` row is created.
6. If the victim DOES have an on-chain transaction (their public key is visible on-chain), an `ApiToken` row IS created at `api.py:206` via `ApiToken.create(wallet)` where `wallet = Wallet(b64ks=txn.public_key)` (public-key-only Wallet). `refreshed_cipher()` at `models.py:1000` encrypts a fresh UUID secret with the victim's **public key** using RSA-OAEP (`wallet.encrypt(secret.encode())` — `wallet.py:209`). The insider cannot decrypt this cipher without the victim's private key.
7. `api.py:209-212` — `TokenView.post` requires `api_token.verify(challenge)` (`models.py:1014`), which uses argon2 to check `_PASSWORD_HASHER.verify(self.hashed, secret)`. The insider cannot supply the correct `secret` without decrypting the cipher.

**Outcome:** REJECTED at step 5 (no on-chain key) or step 7 (cannot decrypt cipher without private key). Creating the `ApiToken` row for a victim whose public key is on-chain has no privilege effect: the row only stores a hashed secret, the cipher is inaccessible to the insider, and the row expires after 60 seconds (`models.py:991`). **No finding.**

**Result:** Correctly rejected. No finding.

---

#### Attack b: Operate on another address's behalf at a protected endpoint

**Pre-state:** Insider holds a valid TRANSACTOR JWT (`sub=transactor_address`, `rol=TRANSACTOR`). They attempt to use a transaction-building endpoint (`GET /api/transaction/transfer`) specifying another address's `public_key` query parameter.

**Attack:** Submit a `GET /api/transaction/transfer?public_key=<victim_pubkey>&amount=1&address=<dest>` with their own valid Bearer token.

**Trace:**

1. `api.py:442-448` — The route is bound with `authorize_transactor(TransferTxnView.as_view(...))`.
2. `api.py:243-271` — `authorize()` wrapper verifies the Bearer JWT. `address = data['sub']` and `role = Role[data['rol']]` from the token. Checks `role.value >= Role.TRANSACTOR.value`. Passes; injects `kwargs['_address'] = transactor_address` and `kwargs['_role'] = Role.TRANSACTOR`.
3. `api.py:417-438` — `TransferTxnView.get(**kwargs)` is entered. `_address` and `_role` are present in `kwargs` but are **never read** by the view body.
4. `api.py:419` — `TransferTxnQueryModel.model_validate(request.args.to_dict(flat=True))`. The model has `model_config = ConfigDict(extra='forbid')` (`api.py:409`). The model fields are `public_key`, `amount`, `address` — none overlap with `_address`/`_role` kwarg names. No kwarg injection risk.
5. `api.py:429` — `wallet = Wallet(b64ks=public_key_b64)` builds a public-key-only Wallet from the caller-supplied `public_key`. The transaction is built referencing this wallet.
6. The returned transaction JSON is an unsigned template. To submit it, the caller must POST to `/api/transaction/<txid>` with a cryptographically valid signature from the wallet's **private key** (`transaction.py` validates the signature on receipt). The insider cannot produce that signature for the victim's wallet.

**Analysis of `_address` non-use:** The transaction-building endpoints (`/transfer`, `/subject`, `/forgive`, `/support`) are signed-transaction factories. The authorization check (`authorize_transactor`) gates *who can request a transaction template*, not *whose funds the template references* — that is enforced downstream by cryptographic signature at submission time. This is architecturally sound: the template itself carries no value until signed by the correct private key.

**Outcome:** REJECTED — the insider can obtain a transaction template referencing any `public_key`, but cannot submit it without the corresponding private key. The `_address` JWT claim is intentionally not cross-checked against `public_key` because security is enforced by the signature requirement at submission. **No finding.**

**Result:** Correctly rejected. No finding.

---

#### Attack c: Role downgrade/confusion and endpoint-to-role table

**Pre-state:** Insider holds a MILLER JWT. They hit endpoints at various role levels. Separately, verify the ladder is monotonic and every `blueprint.add_url_rule` binds the correct `authorize_*` level.

**Attack (role ladder test):** MILLER (value=3) calls a READER-gated (value=1) endpoint.

**Trace (ladder check):**

1. `api.py:166-170` — `Role` enum: `READER=1, TRANSACTOR=2, MILLER=3, ADMIN=4`. Definition order matches numeric ascending order.
2. `api.py:261` — `if address and role.value >= required_role.value: authorized = True`. Monotonic: MILLER (3) >= READER (1) → admitted. Correct.
3. `api.py:175-186` — `Role.address_roles` iterates `for role in Role` (definition order: READER→ADMIN). `address_role` returns `roles[-1]` — the last (highest) matched role. Correct: if an address matches READER and TRANSACTOR, it gets TRANSACTOR.
4. `api.py:215` — JWT `rol` field is set from `Role.address_role(address)` at issuance time. Role is read from config, never from user input.
5. `api.py:260` — `role = Role[data['rol']]` on each request reads the role from the (signed, HS256) JWT. Cannot be forged under TLS without SECRET_KEY.

**Endpoint-to-role table (all `blueprint.add_url_rule` calls in `api.py`):**

| Method | Path | View | authorize level | Line |
|---|---|---|---|---|
| GET | /block | reader_block_view | READER (1) | 344 |
| GET | /block/\<hash\> | reader_block_view | READER (1) | 346-350 |
| POST | /block/\<hash\> | miller_block_view | MILLER (3) | 352-356 |
| POST | /block/\<hash\>/\<process\> | miller_block_view | MILLER (3) | 358-362 |
| POST | /transaction/\<txid\> | transactor_txn_view | TRANSACTOR (2) | 396-399 |
| POST | /transaction/\<txid\>/\<process\> | transactor_txn_view | TRANSACTOR (2) | 400-405 |
| GET | /transaction/transfer | authorize_transactor inline | TRANSACTOR (2) | 442-448 |
| GET | /transaction/subject | authorize_transactor inline | TRANSACTOR (2) | 495-501 |
| GET | /transaction/forgive | authorize_transactor inline | TRANSACTOR (2) | 530-536 |
| GET | /transaction/support | authorize_transactor inline | TRANSACTOR (2) | 565-571 |
| GET | /transaction/pending | authorize_reader inline | READER (1) | 610-614 |
| GET | /wallet/\<address\>/balance | authorize_reader inline | READER (1) | 637-643 |
| GET | /subject/\<subject\>/balance | authorize_reader inline | READER (1) | 666-672 |
| GET | /subject/\<subject\>/support | authorize_reader inline | READER (1) | 695-701 |

All write/gossip endpoints (POST block, POST transaction) are gated at MILLER or TRANSACTOR. All read endpoints are gated at READER. No endpoint is under-gated. No endpoint exposes an admin-only function at a lower level.

**browser.py:** All browser routes (`/`, `/chains`, `/block`, `/transaction`) are unauthenticated HTML read-only views (`browser.py:23-110`). They display blockchain data already visible to any READER via the API. No write operations. Consistent with the design intent (public block explorer).

**Outcome:** REJECTED — the ladder is monotonic, every endpoint is bound at the correct authorization level, and `Role.address_role` correctly selects the highest-matching role. A MILLER can access READER and TRANSACTOR endpoints as expected (downward-compatible). **No finding.**

**Result:** Correctly rejected. No finding.

### Adversary 7: Resource / DoS attacker

**Capabilities:** Unauthenticated; sends volume. Application-amplification only — infra-level DoS (network flood, connection exhaustion) is out of scope.

---

#### Attack a: argon2 cost on unauthenticated paths — verify() re-runs on every failed POST

**Pre-state:** A valid `ApiToken` row exists for an on-chain address (created the first time that address GETs `/api/token/<address>`). The 60-second expiry window (`models.py:991`) means the `hashed` secret survives across multiple POST attempts within one minute.

**Attack:** Attacker obtains (or already knows) a valid on-chain address. They send one GET to create/refresh the token row (burning one argon2id *hash*, ~50 ms / 64 MiB server-side). Then they send an unbounded stream of `POST /api/token/<address>` requests with syntactically valid JSON but wrong challenge strings. Each POST hits the full `verify()` path.

**Trace:**

1. `GET /api/token/<address>` → `api.py:191` `ApiToken.get(address)` finds no row → `api.py:206` `ApiToken.create(wallet)` → `api.py:207` `api_token.refreshed_cipher()`.
2. `models.py:977-978` — on a brand-new row `self.expired` is `False` (timestamp was just set by `ApiToken.create`), but `refreshed_cipher` re-hashes because `not (self.cipher and self.hashed)` is `True` — both `cipher` and `hashed` columns are `NULL` on creation, so the null-column arm fires.
3. `models.py:1003` — `_PASSWORD_HASHER.hash(secret)` — argon2id, `time_cost=3`, `memory_cost=65536 KiB` (64 MiB), `parallelism=4` — measured ~50 ms per call on commodity hardware.
4. Server returns `{'cipher': ...}` — the argon2 hash of the UUID secret now lives in `api_token.hashed`; the ciphertext (RSA-OAEP of the UUID, encrypted to the legitimate holder's public key) lives in `api_token.cipher`. The row is committed.
5. Attacker now issues `POST /api/token/<address>` with `Content-Type: application/json` body `{"challenge": "<wrong-string>"}`.
6. `api.py:210` — `ApiToken.get(address)` returns the row (it exists and is not expired).
7. `api.py:212` — `api_token.verify(request.json.get('challenge'))` is called unconditionally.
8. `models.py:1015` — `self.expired` is `False` (within 60 s), `self.hashed` is set, `isinstance(secret, str)` is `True` — all guards pass.
9. `models.py:1018` — `_PASSWORD_HASHER.verify(self.hashed, secret)` — full argon2id verify, ~45 ms / 64 MiB. Returns `False` (wrong secret).
10. `models.py:1019` — `except (VerifyMismatchError, InvalidHashError): return False` — both a wrong secret and a malformed hash return `False` without propagating.
11. `api.py:212-213` — `abort(401)`. **No reset, no attempt counter, no backoff.**
12. The row remains live. Steps 5–11 repeat for every subsequent wrong-challenge POST within the 60-second window.
13. After 60 s, the next GET triggers a fresh `hash()` call, re-opening another 60-second bombardment window. The cycle continues indefinitely.

**Asymmetry quantification:**

| Side | Per request cost |
|---|---|
| Attacker | One UDP-sized (~60 byte) HTTPS POST body. Near-zero client CPU. |
| Server | ~45 ms CPU + 64 MiB memory per `verify()` call (argon2id defaults: `time_cost=3`, `memory_cost=65536`). |
| Amplification | A single-core server at 100% argon2 load saturates at ~22 concurrent verify calls/second. An attacker with a 10 Mbit/s connection can issue thousands of POST requests per second, each of which queues a verify. |

**Outcome:** ACCEPTED. No attempt counter, no token invalidation on repeated failure, no rate limiting exists anywhere in the token-handshake code path. The 60-second expiry window (`models.py:991`) is the only defense, and it does not prevent repeated verify calls within that window — it merely caps how long one challenge is valid before the attacker must issue a new GET (which itself burns a fresh `hash()` call).

**Finding A7.a — Severity Medium:**

**Impact:** An unauthenticated attacker who knows any valid on-chain address can sustain a sustained CPU/memory amplification attack by sending a stream of POST requests with wrong challenge values. Each POST burns one full argon2id verify (~45 ms, 64 MiB) with no server-side limit. Across a pool of known on-chain addresses the attacker can scale amplification linearly. No unearned authentication is granted — the blast radius is availability of the token-handshake and collateral API latency under load, not privilege escalation.

**Remediation sketch:** Add a failed-attempt counter to `ApiToken` (e.g., a `failed_attempts` integer column defaulting to 0). Increment it in `verify()` on mismatch and call `self.reset()` (clearing `hashed`/`cipher`) once the counter exceeds a threshold (e.g., 3). After reset, further wrong-challenge POSTs will hit the `not self.hashed` guard at `models.py:1015` and return `False` immediately, without executing the argon2 verify. The GET must be repeated to obtain a fresh challenge, which costs one `hash()` call — acceptable because it gates on an RSA-OAEP decryption the attacker cannot perform. Optionally, deploy a middleware rate-limit (e.g., Flask-Limiter) as defense-in-depth.

**Demonstration test:** `test_a7_a_repeated_wrong_challenge_invalidates_token`

---

#### Attack b: ApiToken row growth

**Pre-state:** Database is empty. Attacker submits GET `/api/token/<addr>` for many distinct on-chain addresses.

**Attack:** Each unique address that has ever appeared in the chain generates one `ApiToken` row. There is no pruning or TTL-based cleanup of stale rows.

**Trace:**

1. `api.py:191` — `ApiToken.get(address)` returns `None` for a fresh address.
2. `api.py:192–206` — wallet is resolved from `app.wallets` or the chain, then `ApiToken.create(wallet)` inserts a new row (`models.py:1029-1034`).
3. `models.py:975` — `address` column is `UNIQUE`, so one row per address — no duplicate rows per address.
4. No cleanup job, no `DELETE` on stale/old rows.

**Outcome:** Cross-reference to A2.c. The UNIQUE constraint on `address` caps growth to one row per distinct on-chain address, not one row per request. This is the same unbounded-row surface documented under A2.c. No independent finding raised here; see A2.c for analysis and remediation.

---

#### Attack c: Absence of endpoint rate limiting

**Observation:** No rate-limiting middleware (Flask-Limiter, nginx `limit_req`, etc.) exists anywhere in the application. `api.py` has no `@limiter.limit(...)` decorators; the Flask app factory in `__init__.py` does not register a limiter extension. This is a purely operational gap — the code cannot self-limit because Flask does not have a built-in rate-limiting primitive.

**Outcome:** Noted as an operational recommendation. Not a code-level finding per scope instructions. A reverse proxy (nginx/caddy with `limit_req`) or a Flask extension (Flask-Limiter backed by Redis) placed in front of `/api/token/*` would mitigate both A7.a and future token-handshake volume attacks.

## Clean categories

Negative evidence is a deliverable. The following were traced and found sound.

**Adversary 6 — Authorized insider: zero findings.** Every protected endpoint binds the correct `authorize_*` level (the `add_url_rule` → role table in the Adversary-6 trace is internally consistent: block POST = miller, txn POST = transactor, the balance/support/pending GETs = reader). Views consume the `_address`/`_role` injected by `authorize()` and do not read an effective identity/role from request params, so an insider cannot act as another address by parameter tampering (A6.b). A challenge requested for an address whose key the insider does not hold cannot be redeemed — the cipher is RSA-OAEP-encrypted to that address's public key (A6.a). The role ladder is monotonic (A6.c). (One caveat surfaced as a cross-cutting observation, not a finding: `authorize_admin` is bound to no endpoint, so the ADMIN tier currently confers nothing beyond MILLER.)

**Notable clean results inside the other categories:**
- **JWT decode is hardened (A1.b, A1.c, A3.d).** `jwt.decode(..., algorithms=['HS256'])` pins the algorithm; both `alg=none` and an RS256-signed token raise `InvalidAlgorithmError`. The `authorize()` exception funnel initializes `authorized = False` and routes *every* decode/lookup exception (`KeyError`, `DecodeError`, `ExpiredSignatureError`, `TypeError`, …) to `abort(401)` — there is no path that reaches `authorized = True` via an exception, and no 500 is leaked.
- **Empty/absent `sub` is rejected (A1.d)** by the `if address and ...` guard; an unknown `rol` raises `KeyError` → 401.
- **Challenge decrypt-bypass is closed (A2.a).** `ApiToken.verify(secret)` returns `False` unless `isinstance(secret, str)` and argon2 verifies, so `None`/empty/non-str challenges fail closed.
- **Challenge is single-use (A5.a).** `TokenView.post` calls `reset()` (clearing `hashed`) on a successful verify, so a replayed secret fails the next `verify()`. (The serialized second-POST replay is correctly rejected; the concurrency variant, A2.d, could not be reproduced as a strict-xfail and is recorded as an observation.)
- **`exp` is server-set, signed, and validated (A3.c, A5.c).** A client cannot supply a far-future `exp`; an expired token raises `ExpiredSignatureError` → 401.
- **Address shape and role precedence are constrained (A4.b, A4.c, A4.d).** `AddressConverter.to_python` rejects non-address path segments via `validate_address_format`; `Role.address_role` returns `roles[-1]` over enum order (READER=1 … ADMIN=4), genuinely the highest match; the `rol` claim is only ever set server-side from the verified role.

## Cross-cutting observations

Several findings are symptoms of a few shared roots:

1. **The `rol` claim is the sole authorization gate (A3.a, A5.b, partly A3.b).** `authorize()` reads the role from the signed JWT and never consults live config. This one omission produces three distinct demonstrations — a forged role is honored (A3.a), a config-revoked role keeps working for up to 4h (A5.b), and a token is honored on any node sharing the key (A3.b) — all closed primarily by a single per-request `Role.address_role(address)` re-check. A3.b additionally needs `iss`/`aud` binding.

2. **JWT claim hygiene is minimal.** The token carries only `sub`/`rol`/`exp` — no `iat`, `nbf`, `iss`, `aud`, or `jti`. The consequences: no issuer/audience binding (A3.b), no per-token revocation handle (`jti`), and the only revocation lever is rotating `SECRET_KEY`, which logs out everyone.

3. **An unauthenticated endpoint creates state and runs expensive crypto (A2.c, A7.a; A2.d observation).** `GET/POST /api/token/<address>` is reachable with no auth, yet it persists `ApiToken` rows and runs argon2id (~deliberately expensive) on the first GET (NULL columns) and on every POST verify, with no rate limiting, attempt counter, or row cap. That is both a resource-amplification surface (A7.a) and an address-enumeration surface (A2.c, and the A2.e content-type oracle).

4. **`reset()` runs before the role check (observation).** `TokenView.post` (`api.py:213-216`) verifies the challenge, immediately `reset()`s it, and only *then* checks the role. A legitimate key-holder with no configured role thus burns their challenge and must re-fetch one. Reorder to verify → role-check → reset → issue.

5. **The ADMIN tier is decorative (observation).** `authorize_admin` (`api.py:282`) is bound to no endpoint, so ADMIN currently confers nothing beyond MILLER. This *tempers* the practical blast radius of A3.a/A4.a (a forged or escalated ADMIN token is no more powerful than a MILLER one today; A4.a is now remediated) but is itself a latent foot-gun: adding an ADMIN-gated endpoint later would silently widen those findings.

6. **One symmetric key, no strength gate (A1.a).** A single HS256 `SECRET_KEY` signs every token (and would sign Flask sessions/CSRF if those are ever added), with no startup length/entropy check. Its compromise lets an attacker forge any token — which is precisely why A3.a is rated Medium, not High: the forge-a-role path's precondition (holding `SECRET_KEY`) is already total compromise.

7. **A roll-your-own challenge while standard primitives sit unused.** The handshake hand-rolls "encrypt a random UUID secret to the wallet's RSA key, argon2-hash it, compare on redemption," while `Wallet.sign`/`validate_signature` (ordinary RSA signatures) are never used by the auth path. Argon2 — a deliberately slow KDF for *low-entropy passwords* — is being applied to a 122-bit random secret, which is both unnecessary and the root of the A7.a cost-amplification. This is the structural smell that motivates the replacement analysis in Recommendations.

**Out-of-scope note (pre-existing, not an auth finding):** the `remote_app` test fixture (`tests/conftest.py:399`) references `host_netloc`/`remote_host_netloc` as bare module names rather than fixture parameters, so its `NODE_HOST`/`PEERS` resolve to fixture-object reprs. It does not affect the A3.b demonstration (the WSGI transport ignores `NODE_HOST`), but it is a real bug worth a separate `fix(test):` PR.

## Recommendations

### Targeted remediations (do these regardless), grouped by shared fix

Ordered by priority. The eight findings collapse to roughly five code changes:

1. ✅ (done — PR #105) **Validated `*_ADDRESSES` at config load — closed A4.a (High).** Was highest priority: it needed no key compromise, was triggered by an ordinary operator config mistake, and the code previously invited it silently. Regex matching was replaced with exact-address membership + a READER-only `"*"` sentinel; `Role.validate_config` rejects non-address entries and out-of-READER `"*"` at `create_app` startup via `InvalidRoleConfigError`.
2. **Re-validate the role against live config in `authorize()` — closes A3.a + A5.b (Medium), and the privilege half of A3.b.** After `jwt.decode`, call `live = Role.address_role(address)` and `abort(403)` if `live is None or live.value < required_role.value`. One change closes the largest finding cluster. (Decide product-side whether to honor the *lower* of token-vs-live role, or reject any mismatch.)
3. **Add and verify JWT claim hygiene — closes A3.b (Medium), hardens the rest.** Issue `iss` (node host/address), `aud` (`cancelchain`), `iat`, and a `jti`; pass `issuer=`/`audience=` to `jwt.decode`. The `jti` enables a small server-side revocation denylist (TTL = token lifetime) if per-token revocation is ever needed.
4. **Throttle the token endpoint — closes A2.c + A7.a (Medium), addresses the A2.d observation.** Add a per-address wrong-challenge attempt counter that invalidates the challenge after N failures; cap/evict unredeemed `ApiToken` rows; rate-limit the endpoint at the app or proxy layer; and reorder `TokenView.post` to verify → role-check → `reset()` → issue (fixes the challenge-burn observation). Consider requiring a signed proof before persisting a row at all, which folds into the replacement options below.
5. **`SECRET_KEY` length check (A1.a) and content-type rejection normalization (A2.e) — Low.** Assert `len(SECRET_KEY) >= 32` at `create_app()`; make the wrong-content-type rejection status independent of whether a token row exists (closes the enumeration oracle).

Housekeeping (not findings): bind or explicitly document `authorize_admin` so the ADMIN tier is meaningful; open a separate `fix(test):` PR for the `remote_app` fixture bug.

### Targeted fixes vs. protocol replacement

The findings cluster around two structural roots: **(R1)** the JWT is an unbound bearer token whose `rol` claim is trusted without live re-validation and which lacks issuer/audience/issued-at/jti hygiene; and **(R2)** the handshake is a roll-your-own challenge that hand-rolls RSA-OAEP encryption + argon2-hashing of a high-entropy secret while ordinary RSA *signatures* (`Wallet.sign`) sit unused. The targeted remediations above fully close every individual finding and are low-risk — A4.a is already done; the live re-check and the `SECRET_KEY` check remain the near-mandatory ones and should land next regardless of any larger decision. But targeted fixes leave R2 in place. Because the user has flagged the challenge protocol as a known roll-your-own chosen only to reuse the wallet key pairs, it is worth evaluating a replacement of the handshake half. Two candidate directions:

**Candidate (a) — signed-nonce challenge-response, reusing `Wallet.sign`/`validate_signature`.** The server issues a random nonce; the client signs it with its RSA private key; the server verifies with the address's public key. *For:* smallest change — it deletes the RSA-OAEP/AES-GCM encrypt path and the argon2-on-a-random-secret smell (there is no shared secret to hash), reuses primitives already present in `Wallet`, and keeps the rest of the stack intact. *Against:* still stateful (a nonce must be stored and single-used, so the A2.c/A7.a endpoint-abuse surface persists unless paired with the throttling above), and it still issues the same JWT — so the R1 findings (rol re-validation, claim hygiene) must still be fixed separately. Good as a low-risk interim that removes the worst of the hand-rolled crypto.

**Candidate (b) — RFC 9421 HTTP Message Signatures, or an RS256 `private_key_jwt` client assertion.** The client signs each request (or a short-lived self-signed assertion) with its private key; the server verifies with the public key. *For:* stateless — it removes the challenge round-trip, the `ApiToken` table, the argon2 cost, *and* the shared symmetric `SECRET_KEY` for issuance (eliminating the forge-anything-on-leak root behind A1.a/A3.a). With per-request signatures (RFC 9421) it also closes the bearer-replay window structurally. *Against:* the largest change — a new spec/dependency surface and a per-request signing change on every client (`ApiClient` and CLI). Best strategic fit; addresses the most roots at once.

**Recommendation.** A4.a is done (PR #105). Land the remaining near-mandatory targeted fixes — the live-role re-check and the `SECRET_KEY` length check — since they are cheap and close the largest open finding cluster. Separately, open a design cycle (its own brainstorm → spec → plan) to either (a) replace the handshake with signed-nonce as a low-risk interim, or (b) move to RFC 9421 / RS256 client-assertion as the strategic target; in either case add JWT claim hygiene (`iss`/`aud`/`iat`/`jti`) or move off the symmetric bearer model entirely. This audit does not design that replacement — it scopes the decision.
