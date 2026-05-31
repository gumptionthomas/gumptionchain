# API authentication threat-modeled audit — design spec

**Status:** Draft for review
**Date:** 2026-05-31
**Scope:** A threat-modeled security audit of the cancelchain API authentication layer — the token handshake (`TokenView` + `ApiToken` + `ApiClient.request_token`), JWT issuance and validation (`authorize()`), and role mapping (`Role.address_role(s)` against the `*_ADDRESSES` regexes). Defines 7 adversary categories, enumerates each adversary's attack attempts, traces each attempt through the existing auth surface, and documents gaps as findings. Produces a written audit report at `docs/superpowers/audits/2026-05-31-api-authentication-audit.md` plus a `tests/test_auth_audit.py` module containing one `@pytest.mark.xfail(strict=True)` test per confirmed gap. Remediation of individual findings is out of scope for this audit — each finding becomes the seed of a follow-up PR after the audit lands.

This is the companion audit deferred by the [verification pipeline audit](2026-05-29-verification-pipeline-audit-design.md), whose Non-goals explicitly carved auth out ("every TRANSACTOR / MILLER / ADMIN action originates from an address that legitimately holds that role" was *assumed*, not verified). This audit verifies that assumption.

## Goal

Build confidence (or surface lack thereof) that the cancelchain API authentication layer correctly establishes *who* is calling and *what role* they hold, under each plausible adversary category. The verification audit proved the chain enforces its economic invariants assuming the caller's identity and role are trustworthy; this audit examines whether that identity/role establishment is itself sound. The deliverable is a findings report with severity ratings plus a concrete failing-test demonstration of each gap.

A secondary, explicit goal: the user has flagged the challenge/response handshake as a known roll-your-own design (chosen to reuse the existing RSA wallet key pairs for auth) and is open to replacing it with an established best-practice method. The audit's Recommendations section must therefore serve a downstream decision — **targeted fixes vs. full protocol replacement** — by surfacing the protocol-design weaknesses clearly enough to justify (or not) a replacement, and naming concrete best-practice targets. The audit analyzes and recommends; any replacement is its own spec/plan cycle.

## Non-goals

- **Not transport security.** HTTPS is assumed as an explicit deployment precondition. On-wire interception or replay of the bearer JWT or the decrypted challenge is a transport concern, out of scope. (Noted as a precondition in the report so the assumption is visible.)
- **Not the verification/consensus pipeline.** Already audited in [#84](https://github.com/gumptionthomas/cancelchain/pull/84). This audit assumes chain-correctness is sound and examines only the gate in front of it.
- **Not `browser.py`.** Confirmed during brainstorming: the browser views have no authentication layer at all (no Flask sessions, no login). There is nothing to audit there. (If browser auth is ever added, it inherits `SECRET_KEY` — noted as an observation, not a finding.)
- **Not infra-level DoS / rate limiting.** "Attacker floods the token endpoint" is an operational concern. Where the *application* amplifies a resource attack (e.g. argon2's deliberate cost on an unauthenticated endpoint, or forcing `ApiToken` rows for any on-chain address), that is noted as an observation with severity, but infra mitigations (rate limiters, WAF) are not treated as code findings.
- **Not key management.** Wallet generation, private-key file storage, key rotation, passphrase strength — separate concerns. The audit examines how keys are *used* by auth, not how they are stored.
- **Not remediation.** Each finding includes a one-or-two-sentence remediation sketch pointing at the right place to fix, but actual code changes ride downstream PRs.
- **No spec changes to the role model.** The four-tier `READER < TRANSACTOR < MILLER < ADMIN` ladder and the highest-role-wins precedence are taken as given. "The role ladder is wrong" is out of scope; "`address_role` doesn't actually enforce the ladder it claims to" is in scope.

## Decisions taken during brainstorming

- **Audit-first, not redesign-first.** The user is open to replacing the handshake, but the audit comes first: it produces the threat model and severity evidence that tells us whether a wholesale replacement is warranted or whether targeted fixes suffice. The replacement question lives in Recommendations, not in the audit body.
- **Threat-modeled approach**, identical in shape to the verification audit — 7 adversary lenses over the same code, chosen over a broad survey (misses composition bugs) or a single-concern deep-dive (too narrow for a first pass).
- **Assume TLS, audit application logic.** Drawing the boundary here keeps the findings catalog focused on code-fixable issues rather than padding it with "use TLS"-class items whose fix is operational.
- **Audit document goes under `docs/superpowers/audits/`**, matching the verification audit. Specs describe what to build; audits report what was found.
- **Demonstration tests use `@pytest.mark.xfail(strict=True)`** in a new `tests/test_auth_audit.py`. Strict mode forces xfail removal as part of each remediation PR (an unexpected pass fails the suite), exactly as `tests/test_verification_audit.py` does today.
- **Severity rubric is 4 levels**, re-anchored for the auth threat surface (see below) — the verification audit's "bad block in chain" anchor doesn't translate; for auth the existential anchor is "obtain a role you don't hold" / "act as an address whose key you don't possess."
- **No remediation grouping in the audit itself.** Each finding gets an individual severity and a fix sketch. Grouping into PRs is downstream planning.

## Architecture

### The auth surface under audit

A request is authenticated and authorized in three stages:

1. **Challenge issuance** — `GET /api/token/<address>` (`TokenView.get`). The server resolves a public key for `address` (from `app.wallets`, else from the first on-chain transaction's `public_key`), creates or reuses an `ApiToken` row, and returns `refreshed_cipher()`: a fresh random UUID4 secret, argon2-hashed into `ApiToken.hashed` and RSA-OAEP-encrypted (AES-GCM session key wrapped by the wallet's RSA public key) into `ApiToken.cipher`. The cipher is reused until the row's 60-second `expired` window lapses.
2. **Challenge redemption → JWT** — `POST /api/token/<address>` (`TokenView.post`) with `{"challenge": <decrypted secret>}`. The server argon2-verifies the secret against `hashed`, calls `reset()` (clears cipher + hashed), resolves the role via `Role.address_role`, and returns an **HS256 JWT** signed with `SECRET_KEY` carrying `sub` (address), `rol` (role name), and `exp` (`now().timestamp() + API_TOKEN_SECONDS`, a 4-hour float).
3. **Request authorization** — `authorize(required_role)` decorator (`authorize_reader/transactor/miller/admin`). Reads `Authorization: Bearer <jwt>`, `jwt.decode(..., algorithms=['HS256'])`, extracts `sub`/`rol`, and admits iff `address and role.value >= required_role.value` (`api.py:261`) — note the `address` guard, so an empty/absent `sub` is rejected even when `rol` satisfies the ladder. Any decode exception → `abort(401)`; expired signature → `abort(401)`.

Client mirror: `ApiClient.request_token` performs the raw two-step handshake (GET, `wallet.decrypt` the cipher, POST the secret) and returns the token string. Caching lives in `get_token()` (`api_client.py:96-99`, guards on `self.token is None`); the once-only 401 retry — `reset_token()` then re-issue — lives in `get()`/`post()` (`api_client.py:125-138`, `151-165`), not in `request_token()`.

Notable for the audit: `Wallet` already exposes `sign()` / `validate_signature()` (RSA PKCS1v15 SHA-384) that the auth path does **not** use — it reaches for `encrypt`/`decrypt` instead. `SECRET_KEY` is used *only* for the JWT (no Flask-session use today). `argon2-cffi`'s `PasswordHasher()` hashes a high-entropy 122-bit UUID.

### Threat categories (the seven lenses)

#### 1. Anonymous outsider
**Capabilities:** No wallet, no key, no role. Can send arbitrary HTTP to any endpoint. Can read the public chain (and therefore recover the public key of any address that has ever transacted).
**Goals:**
- a. Reach a `@authorize_*`-protected endpoint with no token / a malformed `Authorization` header and have it admitted.
- b. Forge a JWT the server will accept — `alg=none`, algorithm confusion (RS256-signed token verified as HS256 using a known public key as the HMAC secret), or `SECRET_KEY` guessing/weakness.
- c. Exploit the `authorize()` exception funnel — does any decode path fall through to `authorized = True`, or does a non-JWT exception leak a 500 with detail rather than a clean 401?
- d. Submit a JWT with the right shape but a `rol` value that isn't a valid `Role` name, or a `sub` that's empty/None, and observe the failure mode.

#### 2. Challenge attacker
**Capabilities:** Can call `GET`/`POST /api/token/<address>` for any address, including addresses whose private key it does **not** hold. Can read responses.
**Goals:**
- a. Complete the handshake for an address without holding its private key (decrypt-bypass: is there any path where `verify()` returns true without a correctly decrypted secret? e.g. `None`/empty challenge, type confusion in `verify(secret: object)`).
- b. Exploit the 60-second cipher-reuse window — `refreshed_cipher()` returns the *same* cipher until `expired`; the secret is constant for that window. Is the secret single-use (does `reset()` reliably fire before a second redemption), or can a captured-but-not-yet-redeemed cipher be redeemed by two parties?
- c. Force-create `ApiToken` rows for arbitrary on-chain addresses (public key recoverable from chain) — unbounded table growth / state amplification from an unauthenticated endpoint.
- d. Race two concurrent `GET`s or `GET`/`POST` interleavings against the `unique` constraints on `cipher`/`hashed` to wedge a row or bypass single-use.
- e. Send `POST` with no JSON body / wrong content-type. (Verified: under Flask 3.x, `request.json` on a non-JSON content-type raises `UnsupportedMediaType` → **415** *before* `TokenView.post` reaches `request.json.get('challenge')` — it does not return `None`, so there is no `AttributeError`/500. The audit question is therefore whether a bare 415 is the appropriate rejection for an unauthenticated handshake endpoint, or whether a 400/401 would be more correct — a robustness/consistency observation, not an auth bypass.)

#### 3. Token forger / cryptanalyst
**Capabilities:** Targets the JWT and its signing key directly. Knows the algorithm (HS256) and the claim set. May know or guess properties of `SECRET_KEY`.
**Goals:**
- a. `SECRET_KEY` reuse blast radius — the same symmetric key signs the JWT and would sign Flask sessions/CSRF if those ever exist. Document the coupling and what a leak compromises.
- b. Missing claims — no `iat`, `nbf`, `iss`, `aud`. Can a token minted for node A be replayed against node B that shares `SECRET_KEY` (peer mesh)? Is the lack of `aud`/`iss` a cross-node confusion vector?
- c. `exp` handling — `exp` is a float `timestamp()`; confirm PyJWT validates it, check clock-skew / no-`leeway` behavior, and that there's no path accepting an expired-but-well-formed token.
- d. Algorithm pinning — `decode(..., algorithms=['HS256'])` is explicitly pinned (good); confirm there is no second decode path, and that `alg=none` and RS256-confusion both fail closed.

#### 4. Role-escalation attacker
**Capabilities:** Legitimately holds a key for some address with a low role (e.g. READER), wants a higher role (TRANSACTOR/MILLER/ADMIN).
**Goals:**
- a. Regex over-match / escape — `Role.addresses()` returns operator-configured regexes matched with `re.fullmatch`. Can a legitimately-controlled address *also* match a broader role's regex (e.g. an `ADMIN_ADDRESSES` pattern with unanchored alternation or a `.*`-ish fragment)? Document the foot-gun class and whether the code constrains regexes at all.
- b. `sub` crafting — the `<address:address>` URL converter feeds `sub`. Can a `sub` be shaped to satisfy a broader role regex while still corresponding to a key the attacker controls (so the challenge is decryptable)? Trace the address converter's validation.
- c. Multi-role precedence — `address_role` returns `roles[-1]` (highest by enum order). Confirm enum order equals privilege order and that `roles[-1]` can't return a *lower* role when matches are non-contiguous.
- d. `rol` claim integrity — the `rol` string is trusted on decode; confirm it's only ever set server-side from the verified role and can't be influenced by the client beyond what the signature protects.

#### 5. Replay attacker
**Capabilities:** Has captured a valid artifact — a redeemed-but-still-valid JWT, or a challenge cipher/secret in flight (within the TLS assumption: capture via a compromised client, logs, or a shared-host side channel, not on-wire).
**Goals:**
- a. Challenge single-use — after a successful `POST`, `reset()` clears the row. Confirm a second `POST` with the same secret fails, and that there's no window between `verify()` and `reset()` exploitable under concurrency (overlaps with 2d).
- b. JWT reuse within the 4h window — bounded and expected under the bearer model; document it and whether the window is appropriate. Confirm no server-side revocation is claimed-but-absent.
- c. Expired-token edges — token exactly at `exp`, token with a far-future `exp` the client supplied (can't — `exp` is server-set and signed; confirm).

#### 6. Authorized insider
**Capabilities:** Legitimately holds a key and a role. Acts within the system but tries to exceed their grant or affect *other* addresses.
**Goals:**
- a. Cross-address token request — request a challenge for an address whose key they don't hold; confirm they can't redeem it (they can't decrypt), and that merely creating the row has no privilege effect.
- b. Operate on another address's behalf at a protected endpoint — confirm `_address`/`_role` injected by `authorize()` actually scope the downstream action and aren't overridable by request params.
- c. Role downgrade/confusion — a MILLER hitting a READER-gated endpoint (allowed by the ladder) — confirm the ladder is monotonic and no endpoint mis-binds its `authorize_*` level.

#### 7. Resource / DoS attacker (application-amplification only)
**Capabilities:** Unauthenticated; sends volume.
**Goals:**
- a. argon2 cost on an unauthenticated path — `refreshed_cipher()` runs argon2 `hash` on every cold/expired `GET`; `verify()` runs argon2 on every `POST`. Quantify the asymmetry (cheap request → expensive server work) as an observation.
- b. `ApiToken` row growth (overlaps 2c) — unbounded rows keyed on any on-chain address.
- c. Note (not a code finding): absence of endpoint rate limiting.

### Audit methodology (per attack attempt)

For each attack attempt in each category:

1. **State the attempt** concretely (the exact request / token / input).
2. **Trace it** through the actual code path — name the functions and the specific lines that accept or reject it.
3. **Classify:** no-finding (the code correctly rejects/handles it — record *why*, so the audit documents the defenses too) or **finding** with a severity.
4. **For each finding**, write a `@pytest.mark.xfail(strict=True)` demonstration test in `tests/test_auth_audit.py` that exercises the exact gap, plus a one-or-two-sentence remediation sketch.

Recording the no-finding rationales matters as much as the findings: the verification audit's most-capable adversary (malicious miller) produced *zero* findings, and saying so explicitly was a confidence result. The analogous "this category was clean" outcome here is a deliverable, not a gap in the report.

### Severity rubric (re-anchored for auth)

- **Critical** — authentication/authorization existential: obtain a role you don't hold, act as an address whose private key you don't possess, or forge a token the server accepts. Anyone-becomes-ADMIN class.
- **High** — significant auth-integrity violation with bounded blast radius: e.g. a misconfiguration foot-gun the code invites and doesn't guard, or a single-use/replay gap exploitable only under a narrow race.
- **Medium** — edge case that misbehaves but doesn't grant unearned access: e.g. an unhandled input that 500s instead of 401s (information exposure / robustness), or state amplification requiring an unrealistic volume.
- **Low** — cosmetic / documentation / theoretical: claim-hygiene gaps (`iat`/`iss`/`aud` absent) with no demonstrated exploit under the TLS assumption, or design-smell observations.

### Test module structure

`tests/test_auth_audit.py`, mirroring `tests/test_verification_audit.py`:

- Module docstring linking back to this spec and the audit report.
- One test function per finding. Because a finding id like `A3.b` is not a valid Python identifier (the `.` and uppercase), the test name lowercases it and replaces the dot: `test_a<N>_<letter>_<short_slug>` (e.g. `A3.b` → `test_a3_b_<slug>`), matching the existing `tests/test_verification_audit.py` convention. Decorate with `@pytest.mark.xfail(strict=True, reason='Audit finding A<N>.<letter> — severity <S> — <one-line>. See docs/superpowers/audits/2026-05-31-api-authentication-audit.md')`.
- Each test builds the minimal app/client/wallet fixture state to exercise the gap and asserts the *secure* behavior (so it xfails today and flips to pass when remediated).
- Reuse existing fixtures from `tests/conftest.py` (the four canonical wallets, the `requests_proxy` Flask-client routing) and patterns from `tests/test_api.py` / `tests/test_api_client.py`.

### Audit document structure

`docs/superpowers/audits/2026-05-31-api-authentication-audit.md`. Mirrors `tests/test_verification_audit.py`'s companion audit doc, so it carries two scaffolding sections (**Threat model**, **Methodology**) beyond the bare content sections, and organizes the per-finding detail under **Per-adversary traces** (one subsection per adversary) rather than as a flat list. The nine `##` sections are:

- **Preconditions** (TLS assumed, scope boundary).
- **Executive summary** — counts by severity (the `N Critical / N High / N Medium / N Low` headline) + headline conclusions.
- **Threat model** — the 7 adversary categories restated.
- **Methodology** — the per-attempt trace→classify→demonstrate procedure and the `A<N>.<letter>` finding-id scheme.
- **Findings table** — id, title, severity, category, one-line, test name.
- **Per-adversary traces** — one `### Adversary N` subsection each, holding every attack's attempt, trace, outcome, and (for gaps) impact, remediation sketch, demonstration-test reference.
- **Clean categories** — explicit "no findings" results with rationale.
- **Cross-cutting observations** — `SECRET_KEY` coupling, argon2-on-high-entropy-secret smell, etc.
- **Recommendations** — the targeted-fixes-vs-replacement analysis, with the two named candidate replacement directions (signed-nonce reusing `Wallet.sign`; RFC 9421 / RS256 client-assertion), each with a one-paragraph trade-off so the downstream redesign spec has a starting point.

## Changes

### Files (created by the audit, downstream of this spec)

- `docs/superpowers/audits/2026-05-31-api-authentication-audit.md` — the report.
- `tests/test_auth_audit.py` — the demonstration suite.
- `docs/superpowers/ROADMAP.md` — close the "Future audit — API authentication layer" entry; open per-finding remediation entries.

### Files (read during audit, not modified by it)

- `src/cancelchain/api.py` — `TokenView`, `authorize`, `Role`.
- `src/cancelchain/models.py` — `ApiToken` (+ `_PASSWORD_HASHER`).
- `src/cancelchain/api_client.py` — `ApiClient.request_token` / token caching / 401 retry.
- `src/cancelchain/wallet.py` — `encrypt`/`decrypt` (used) and `sign`/`validate_signature` (unused by auth).
- `src/cancelchain/application.py` — the `AddressConverter` for the `<address:address>` URL route segment (registered at `app.url_map.converters['address']`); its `to_python` calls `validate_address_format`.
- `src/cancelchain/schema.py` — `validate_address_format` / `AddressType` and the other validation helpers the converters call.
- `src/cancelchain/config.py` — `*_ADDRESSES` loading.
- `tests/test_api.py`, `tests/test_api_client.py`, `tests/conftest.py` — existing coverage + fixtures.

## Test plan

- The audit *is* test-producing: each finding ships a strict-xfail demonstration. Running `uv run pytest tests/test_auth_audit.py` after the audit lands must show N xfailed (one per finding) and 0 passed/0 xpassed.
- Full suite (`uv run pytest`) must stay green with the new xfails counted (no regressions, no accidental xpass).
- `ruff check` / `ruff format --check` / `mypy` clean on the new test module.

## Acceptance

- This design spec committed under `docs/superpowers/specs/`.
- (Downstream) audit report committed with a severity headline, a findings table, per-finding traces, explicit clean-category results, and a Recommendations section that resolves the targeted-fixes-vs-replacement question with reasoning.
- (Downstream) one strict-xfail test per finding in `tests/test_auth_audit.py`, all xfailing.
- (Downstream) roadmap updated: audit entry closed, remediation entries opened.

## Risks

- **Findings may recommend wholesale replacement, which is a larger change than the verification audit's per-finding fixes.** Mitigated by keeping the replacement out of the audit proper — the audit lands as analysis + xfail demos regardless, and replacement becomes its own brainstorm → spec → plan cycle.
- **Some findings' "secure behavior" assertion presupposes a design choice** (e.g. adding `aud`/`iss` only matters under one redesign direction). Mitigated by writing such tests against the *minimal* secure behavior and flagging in the finding that the full fix may land via the redesign rather than a targeted patch.
- **The TLS assumption could hide a real risk** if a deployment terminates TLS incorrectly. Mitigated by stating the precondition prominently in the report rather than silently.

## Open decisions

None blocking. Two to confirm during the audit, not before:

- Whether any finding warrants Critical (the verification audit landed 0 Critical / 0 High; auth may differ — the role-regex foot-gun and the JWT claim hygiene are the likeliest to push higher).
- Whether the `request.json` `None`-path actually 500s (Medium robustness) or is already guarded by Flask's content-type handling — to be confirmed by trace + test, not assumed here.

## What comes next

After this spec is approved and committed, the writing-plans skill produces the implementation plan for executing the audit (the per-category tracing, the report, the xfail suite). The audit then runs, lands as its own PR (report + tests + roadmap), and its Recommendations seed the protocol-replacement decision — which, if taken, becomes a fresh brainstorm → design → plan cycle of its own.
