# A3.b — Bind the JWT to its Issuing Node (`iss`/`aud`) Design

**Status:** Draft for review
**Date:** 2026-06-01
**Remediates:** Audit finding **A3.b (Medium)** from the [API authentication audit](../audits/2026-05-31-api-authentication-audit.md): the JWT carries no `iss`/`aud`, so a token minted on one node is accepted verbatim by any other node sharing `SECRET_KEY` (cross-node replay). The A3.a/A5.b live-role re-check already mitigated the *privilege* half (the target node now re-checks the address's local role); this closes the structural half — a token must be bound to the node that issued it.

## Problem

`TokenView.post` (`api.py`) mints a JWT with only `sub`/`rol`/`exp`:

```python
token = jwt.encode(
    {'sub': address, 'rol': str(role.name), 'exp': now().timestamp() + API_TOKEN_SECONDS},
    current_app.config['SECRET_KEY'], algorithm='HS256',
)
```

and `authorize()` decodes with no issuer/audience enforcement:

```python
data = jwt.decode(token, current_app.config['SECRET_KEY'], algorithms=['HS256'])
```

Nothing binds the token to the node that issued it. In a multi-node deployment where operators share `SECRET_KEY` across the mesh (a plausible misconfiguration), a token legitimately obtained from node A is structurally valid on node B. After the A3.a/A5.b remediation, B still re-checks the address's *local* role — so the practical escalation is bounded — but a token that was never issued for B should not be a valid credential on B at all, regardless of role.

## Goal

Bind every token to its issuing node and reject, at decode time, any token not issued for the node receiving it. Close A3.b; flip its demonstration test from `xfail` to a passing regression test.

## Approach

Use the node's own identity — `NODE_HOST` — as both the JWT `iss` (issuer) and `aud` (audience), and enforce both on decode. Because a client obtains its token from the same node whose API it then calls, the issuer and the intended audience are the same node; a token minted by a different node fails the check.

### Component: `TokenView.post` (`src/cancelchain/api.py`) — mint

Add `iss` and `aud` to the payload, both set to the node's identity:

```python
node_host = current_app.config['NODE_HOST']
token = jwt.encode(
    {
        'sub': address,
        'rol': str(role.name),
        'iss': node_host,
        'aud': node_host,
        'exp': now().timestamp() + API_TOKEN_SECONDS,
    },
    current_app.config['SECRET_KEY'],
    algorithm='HS256',
)
```

### Component: `authorize()` (`src/cancelchain/api.py`) — verify

Pass the local node's identity to `jwt.decode` so issuer/audience are enforced:

```python
node_host = current_app.config['NODE_HOST']
data = jwt.decode(
    token,
    current_app.config['SECRET_KEY'],
    algorithms=['HS256'],
    issuer=node_host,
    audience=node_host,
)
address = data['sub']
```

A token whose `iss`/`aud` doesn't match this node — or that lacks the claims — raises `jwt.exceptions.InvalidIssuerError` / `InvalidAudienceError` / `MissingRequiredClaimError`, all subclasses of the `jwt` error hierarchy already funneled by the existing `except Exception` branch to **`abort(401)`**. So a cross-node or unbound token is rejected as an invalid credential for this node, before the live-role check runs. The rest of `authorize()` (the A3.a/A5.b live-role re-check on `Role.address_role(address)`) is unchanged.

### `NODE_HOST` assumption

A functioning node always has `NODE_HOST` set — it is how the node derives its own address (`host_address(NODE_HOST)`) for coinbase and peer identity, and the test fixtures set it. A3.b uses it as the binding identity. If `NODE_HOST` were `None` (a misconfigured node), `aud`/`iss` would be `None` and PyJWT would skip the checks — the binding degrades to absent rather than failing. This is an accepted limitation: a startup check that *requires* `NODE_HOST` belongs with A1.a's `SECRET_KEY` startup validation, not this PR. No new startup guard is added here.

### Error handling

No new error paths in `authorize()` — the `iss`/`aud` failures reuse the existing `except Exception → abort(401)`. `TokenView.post` mint cannot fail on a set `NODE_HOST` (it is a plain string claim). Matching tokens decode exactly as before plus the issuer/audience assertions.

## Pre-existing fixture fix (in scope)

A3.b's cross-node test requires `remote_app` to have a real, distinct `NODE_HOST`. The `remote_app` fixture (`tests/conftest.py`) currently references `host_netloc`/`remote_host_netloc` as **bare module names** (they are fixture functions, not parameters), so its `NODE_HOST` resolves to a fixture-object repr string rather than `http://peer.node:8888`. This is the pre-existing bug flagged as a cross-cutting observation in the audit. Fix it by adding `host_netloc, remote_host_netloc` to the `remote_app` fixture signature so `NODE_HOST` and `PEERS` resolve correctly. This gives the audience-binding test a genuinely distinct remote node to reject against, and removes the audit's out-of-scope note.

## Testing

### Flip the demonstration (strict-xfail → passing)

`test_a3_b_cross_node_token_replay` (`tests/test_auth_audit.py`): remove the `@pytest.mark.xfail(strict=True)` marker **and** the now-unnecessary `remote_app.config['READER_ADDRESSES'] = [wallet.address]` strengthening line (the rejection is now at `decode`, before any role check, so granting a role on `remote_app` is irrelevant). Mint a token exactly as `app` would — `iss`/`aud` set to `app`'s `NODE_HOST` (`http://localhost:8080`) — and present it to `remote_app`, which verifies `audience` against its own `NODE_HOST` (`http://peer.node:8888`). The mismatch raises `InvalidAudienceError` → `abort(401)`. Change the assertion from `== FORBIDDEN` to **`== UNAUTHORIZED`** and reframe the docstring to past tense: A3.b remediated — a token issued for one node is rejected by another (`aud` mismatch), regardless of `SECRET_KEY` sharing.

### New coverage (`tests/test_api.py`)

- **`test_authorize_rejects_wrong_audience_token`** — for a valid on-chain address, hand-mint a token (signed with the live `SECRET_KEY`) whose `aud` is some other node's host (e.g. `'http://elsewhere:9999'`), present it to a protected endpoint, and assert `401`. Proves the audience check is enforced on the same node, independent of `remote_app`. (A companion assertion may mint a token with `aud` *omitted* and confirm it is likewise rejected `401`, since `decode(audience=...)` requires the claim.)

### Regression

The handshake-based tests (`test_roles`, `test_no_role`, the A4.a/A5.b regression tests, and the live-role tests) obtain their tokens through the real handshake, which now mints `iss`/`aud` = the app's `NODE_HOST` and verifies against the same value on the same node — so they round-trip cleanly and must pass unchanged. **One exception that needs a one-line test edit:** `test_a3_a_forged_role_claim_accepted` *hand-mints* its token (it is not handshake-issued) and currently omits `iss`/`aud`; once `authorize()` enforces `audience=`/`issuer=`, a claim-less token → `MissingRequiredClaimError` → 401, which would break its `403` assertion. Its forged token must gain valid `iss`/`aud` = the app's `NODE_HOST` so it passes the node-binding gate while the bogus `rol` is still rejected by the live-role check (403) — preserving the test's intent. Suite moves from `269 passed, 5 xfailed, 1 skipped` to **`271 passed, 4 xfailed, 1 skipped`** (the A3.b xfail flips to a pass, +1 new test; A3.b leaves the xfail set, 5 → 4). `--runxfail tests/test_auth_audit.py` → `4 failed` (A1.a, A2.c, A2.e, A7.a). All five CI gates green; `mypy --strict` over `src/` accepts the new claims/keyword args.

## Documentation updates

- **Audit report** (`docs/superpowers/audits/2026-05-31-api-authentication-audit.md`): mark **A3.b** remediated (✅ on the finding, table row, and recommendation item 3; gap prose past-tensed; `(As implemented: …)` note). Update the severity headline from `0 Critical / 0 High / 3 Medium / 2 Low` to **`0 Critical / 0 High / 2 Medium / 2 Low`**. Update cross-cutting observation #1 (the rol/cross-node cluster) — A3.b's residual is now closed; the whole cluster is remediated. Update cross-cutting observation #2 ("claim hygiene is minimal") — `iss`/`aud` are now present; `iat`/`jti` remain absent. Update/remove the out-of-scope note about the `remote_app` fixture bug (now fixed).
- **CLAUDE.md**: in the API-auth section, note that the JWT is bound to its issuing node — `iss`/`aud` are set to `NODE_HOST` and verified on decode, so a token issued by one node is rejected by another even when they share `SECRET_KEY`.
- **Roadmap** (`docs/superpowers/ROADMAP.md`): mark the A3.b remediation bullet ✅ with the impl PR number.

## Out of scope

- `iat`/`nbf`/`jti` claims, and any server-side `jti` revocation denylist — separate claim-hygiene/revocation work.
- A startup check that requires `NODE_HOST` to be set (relates to A1.a's `SECRET_KEY` startup validation).
- A2.c/A7.a (endpoint throttling), A1.a (`SECRET_KEY` length), A2.e (content-type oracle) — separate PRs.
- No database schema change; `rol` continues to be minted (now re-validated live per A3.a/A5.b, and the token is node-bound here).

## Acceptance criteria

- `TokenView.post` mints `iss` and `aud` = `NODE_HOST`; `authorize()` enforces `issuer=`/`audience=` on `jwt.decode`; a cross-node or `aud`-mismatched/omitted token → 401.
- `remote_app` fixture has a real distinct `NODE_HOST` (`http://peer.node:8888`).
- `test_a3_b_*` passes as a regression test (xfail marker + role-grant strengthening removed; asserts 401); new `test_authorize_rejects_wrong_audience_token` passes; existing handshake tests pass unchanged.
- Suite `271 passed, 4 xfailed, 1 skipped`; `--runxfail tests/test_auth_audit.py` → `4 failed`. All five CI gates green.
- Audit report headline `0 Critical / 0 High / 2 Medium / 2 Low`; A3.b ✅; claim-hygiene/fixture observations updated. CLAUDE.md + roadmap updated.
