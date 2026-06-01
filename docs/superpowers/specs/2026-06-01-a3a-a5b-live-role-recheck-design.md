# A3.a + A5.b — Live-Role Re-check in `authorize()` Design

**Status:** Draft for review
**Date:** 2026-06-01
**Remediates:** Audit findings **A3.a (Medium)** and **A5.b (Medium)** from the [API authentication audit](../audits/2026-05-31-api-authentication-audit.md). Both share one root cause: `authorize()` trusts the `rol` claim baked into the JWT at issuance and never re-validates it against live `*_ADDRESSES` config.

## Problem

`authorize()` (`api.py`) decodes the bearer JWT and authorizes on the token's claimed `rol`:

```python
data = jwt.decode(token, current_app.config['SECRET_KEY'], algorithms=['HS256'])
address = data['sub']
role = Role[data['rol']]
if address and role.value >= required_role.value:
    authorized = True
```

The role is fixed at token-issuance time (`TokenView.post`) and trusted for the token's full ~4-hour lifetime. Two consequences, each a demonstrated audit finding:

- **A3.a** — a token whose `rol` claim exceeds the address's real role is honored. (Demonstrated by forging `rol=MILLER` for a READER-only address with `SECRET_KEY`; more broadly, the authorization decision never consults live config.)
- **A5.b** — an address removed from `*_ADDRESSES` keeps its access until the token expires; revocation has up to 4 hours of latency.

Both are closed by re-validating the role against live config on every request, rather than trusting the token's claim.

## Goal

Make the live `*_ADDRESSES` config the authority for every authorization decision. A valid token *authenticates* the caller (proves who they are); live config *authorizes* them (decides what they may do). Flip the A3.a and A5.b demonstration tests to passing regression tests, without spuriously "closing" A3.b (whose real fix is `iss`/`aud`).

## Approach

Restructure `authorize()` so that, after decoding a valid token, the authorization decision uses `Role.address_role(sub)` (live config) and **ignores the token's `rol` claim**.

### Component: `authorize()` (`src/cancelchain/api.py`)

New decision flow inside the `wrapper`:

1. Parse `Authorization: Bearer <token>` exactly as today.
2. If a token is present, `jwt.decode(...)` and set `address = data['sub']`. The `rol` claim is **no longer read** for the decision (it remains in the token, informational).
3. **Authentication failures → `abort(401)`** (unchanged): no token, malformed header, `jwt.decode` raising (bad signature / `alg`), expired signature, or an empty/absent `sub`.
4. **Authorization (live):** compute `role = Role.address_role(address)`. Admit iff `role is not None and role.value >= required_role.value`; otherwise **`abort(403)`**.
5. On success, inject `_address = address` and `_role = role` (the **live** role) into the view kwargs.

Sketch:

```python
@wraps(func)
def wrapper(*args: Any, **kwargs: Any) -> Any:
    address: str | None = None
    try:
        token = request.headers.get('Authorization')
        if token and token.startswith('Bearer '):
            token = token[7:]
        else:
            token = None
        if token:
            data = jwt.decode(
                token,
                current_app.config['SECRET_KEY'],
                algorithms=['HS256'],
            )
            address = data['sub']
    except jwt.exceptions.ExpiredSignatureError:
        abort(401)
    except Exception as e:
        current_app.logger.exception(e)
        abort(401)
    if not address:
        abort(401)
    # Live config is the authority — the token's `rol` claim is not trusted.
    role = Role.address_role(address)
    if role is None or role.value < required_role.value:
        abort(403)
    kwargs['_address'] = address
    kwargs['_role'] = role
    return func(*args, **kwargs)
```

### Behavior change (deliberate)

- **401 vs 403.** Previously an insufficient *claimed* role left `authorized = False` → `abort(401)`. Now an authenticated caller whose *live* role is `None` or below the requirement → `abort(403)` (authenticated-but-forbidden, semantically correct). Authentication failures stay 401. No existing test asserts 401 for an authenticated-but-insufficient request (the 401s in `test_roles` are token-acquisition failures for not-yet-on-chain wallets; `test_no_role`'s 403 comes from `TokenView.post` at issuance) — to be re-confirmed during implementation.
- **`rol` claim no longer gates.** `TokenView.post` still mints `rol` (informational, unchanged); `authorize()` stops reading it. A token with a bogus/over-claimed `rol` but a valid signature is now authorized by its live role, not its claim.

### Error handling

`Role.address_role` is total (returns `Role | None`, no raise). All token/parse failures funnel to `abort(401)` as today; the live-role gate is the only new `abort(403)`. No 500 path is introduced.

## Testing

### Flip the two demonstrations (strict-xfail → passing regression)

Because the markers are `strict=True`, implementing the fix makes these xpass and *forces* marker removal in the same change (as with A4.a):

- **`test_a3_a_forged_role_claim_accepted`** — forges `rol=MILLER` for the READER-only reader wallet, POSTs to a MILLER endpoint, asserts `403`. With the live re-check, `address_role(reader) = READER < MILLER` → 403. Remove the `xfail` marker; reframe the docstring/comments to past tense.
- **`test_a5_b_stale_role_rejected_after_config_revocation`** — obtains a MILLER token, sets `MILLER_ADDRESSES = []`, POSTs a block, asserts `401 or 403`. With the live re-check, `address_role` returns `None` → 403. Remove the marker; reframe.

### Keep A3.b a valid xfail (required — avoids a spurious xpass)

`test_a3_b_cross_node_token_replay` forges a token for `wallet.address` and presents it to `remote_app`, asserting `403`. `remote_app` configures only `MILLER_ADDRESSES = [miller_2_wallet.address]` (conftest.py:418), so `wallet.address` has **no** role there — meaning the live re-check alone would now return `403` and the test would **xpass**, breaking the strict-xfail even though A3.b's real fix (`iss`/`aud` binding) is not implemented.

Fix: strengthen the test so the live re-check *passes* on `remote_app` and the residual gap is isolated. Within the test, give `wallet.address` a qualifying role on `remote_app` (e.g. `remote_app.config['MILLER_ADDRESSES'] = [..., wallet.address]` under `remote_app.app_context()`), so the only thing that *should* reject the token is that it was issued for a different node — which, absent `iss`/`aud`, it is not. The test keeps asserting `403` and therefore **keeps xfailing** until the iss/aud PR lands. Update its docstring to state it now isolates the iss/aud gap (the live-recheck no longer masks it).

### New coverage (`tests/test_api.py`)

- **`test_authorize_insufficient_live_role_forbidden`** — an on-chain wallet with a low live role (e.g. configured READER only) presents a *valid* token and hits a MILLER-gated endpoint → `403` (exercises the gate-on-live path and the 401→403 change directly, without forging).
- **`test_authorize_honors_live_downgrade`** — an address configured MILLER obtains a token; its config is then changed to READER only; it still reads a READER endpoint (`200`) but is `403` on a MILLER endpoint — proving the live role (not the token's higher claim) governs.

(A3.a covers forged-over-claim; A5.b covers revoked-to-none; these add the demoted-but-still-has-a-role and the plain insufficient-role cases.)

### Regression

`test_roles` and `test_no_role` are unaffected: their 401s are token-acquisition failures, legitimate access stays `200`, and no-role is still rejected at issuance. Suite moves from `265 passed, 7 xfailed, 1 skipped` to **`269 passed, 5 xfailed, 1 skipped`** (265 + 2 flipped + 2 new positive; A3.a and A5.b leave the xfail set, A3.b stays). `--runxfail tests/test_auth_audit.py` then shows `5 failed` (A3.a/A5.b no longer among them). All five CI gates green; `mypy --strict` over `src/` accepts the restructured `wrapper`.

## Documentation updates

- **Audit report** (`docs/superpowers/audits/2026-05-31-api-authentication-audit.md`): mark **A3.a** and **A5.b** remediated per the convention (✅ on each finding + table row + recommendation, gap prose past-tensed, `(As implemented: …)` reconciliation). Update the severity headline (Executive summary + Findings-table intro) from `0 Critical / 0 High / 5 Medium / 2 Low` to **`0 Critical / 0 High / 3 Medium / 2 Low`**. Update the cross-cutting "rol-not-revalidated cluster" note: A3.a/A5.b are now closed by the live re-check; A3.b's *privilege* half is also mitigated (cross-node now requires a qualifying role on the target node) but its `iss`/`aud` binding remains its fix. Update the A3.b finding/test reference to note the test now isolates the iss/aud gap.
- **CLAUDE.md**: in the API-auth section, note that `authorize()` re-validates the caller's role against live `*_ADDRESSES` config on every request (the JWT `rol` claim is informational, not the authorization gate).
- **Roadmap** (`docs/superpowers/ROADMAP.md`): mark the "A3.a + A5.b — re-validate `rol` against live config" remediation bullet ✅ with the impl PR number; note A3.b's privilege half is mitigated but it stays open for iss/aud.

## Out of scope

- **A3.b (`iss`/`aud` binding)** — remains a separate remediation PR. This change *mitigates* but does not close it.
- No database schema change; `TokenView.post` continues to mint the `rol` claim unchanged.
- A2.c/A7.a (endpoint throttling), A1.a (`SECRET_KEY` length), A2.e (content-type oracle) — separate PRs.
- No JWT claim-hygiene additions (`iss`/`aud`/`iat`/`jti`), no rate limiting, no `authorize_admin` binding.

## Acceptance criteria

- `authorize()` authorizes on `Role.address_role(sub)` (live config), not the token's `rol` claim; authentication failures → 401, insufficient/absent live role → 403; injected `_role` is the live role.
- `test_a3_a_*` and `test_a5_b_*` pass as regression tests (xfail markers removed); `test_a3_b_*` still xfails (strengthened to isolate the iss/aud gap); new `tests/test_api.py` coverage passes.
- Suite `269 passed, 5 xfailed, 1 skipped`; `--runxfail tests/test_auth_audit.py` → `5 failed`. All five CI gates green.
- Audit report headline `0 Critical / 0 High / 3 Medium / 2 Low`; A3.a/A5.b marked ✅; A3.b annotated as mitigated-but-open. CLAUDE.md + roadmap updated.
