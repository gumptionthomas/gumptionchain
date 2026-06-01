# A3.a + A5.b Live-Role Re-check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remediate audit findings **A3.a + A5.b** by making `authorize()` re-validate the caller's role against live `*_ADDRESSES` config on every request (ignoring the JWT's `rol` claim), and flip both demonstration tests to passing regression tests — without spuriously closing A3.b.

**Architecture:** One source change (`authorize()` in `api.py`): authenticate via the bearer token, then authorize on `Role.address_role(sub)`. Auth failures → 401; insufficient/absent live role → 403. Because the A3.a/A5.b xfails are `strict=True`, the fix forces their marker-removal in the same commit; the A3.b test is strengthened in that same commit so its xfail stays valid (its real fix is the separate iss/aud PR).

**Tech Stack:** Python 3.12, Flask, pytest. Pure application logic — no schema change. `mypy --strict` covers `src/` only. Companion spec: `docs/superpowers/specs/2026-06-01-a3a-a5b-live-role-recheck-design.md`.

---

## Prerequisites

- Working directory: cancelchain repo root.
- A4.a is merged: `git log --oneline -1 main` shows `3a9c8c3` (`fix(a4a): exact-match role allowlists …`) or later.
- The branch `docs/a3a-a5b-live-role-recheck` exists with one commit (the spec). This plan adds the plan file as a second commit and ships both as the docs PR.
- Test baseline: **265 passed, 7 xfailed, 1 skipped**. After this work: A3.a + A5.b flip to passing (−2 xfailed, +2 passed) and 2 new `test_api.py` tests are added → **269 passed, 5 xfailed, 1 skipped**.
- CI hard-gates: `ruff check`, `ruff format --check`, `pytest`, `mypy`, `cancelchain db upgrade` + `cancelchain db check`.
- **Review loop** (`feedback_internal_review_then_one_copilot`): internal cross-model review to convergence before the PR (include a **regression-impact check** — grep existing tests/callers that the change could break), then one Copilot backstop. Copilot does **not** auto-re-review — trigger with `gh pr comment <N> --body "/copilot review"` if a fix round is needed. `wor`/`mwg` are controller work.
- Never push directly to `main`.

---

## File Map

| Task | PR | Files |
|---|---|---|
| 1 | docs PR | `docs/superpowers/plans/2026-06-01-a3a-a5b-live-role-recheck.md` (this file) + spec already on branch |
| 2 | impl PR | `src/cancelchain/api.py`; `tests/test_auth_audit.py` (flip A3.a + A5.b, strengthen A3.b); `tests/test_api.py` (2 new tests) |
| 3 | impl PR | `CLAUDE.md`, `docs/superpowers/audits/2026-05-31-api-authentication-audit.md`, `docs/superpowers/ROADMAP.md` |
| 4 | impl PR | push + open PR |
| 5 | acceptance | none (verification only) |

---

## Task 1: Ship the docs PR (spec + plan)

- [ ] **Step 1: Confirm branch + spec tracked**

```bash
git rev-parse --abbrev-ref HEAD
git ls-files docs/superpowers/specs/2026-06-01-a3a-a5b-live-role-recheck-design.md
git status docs/superpowers/plans/2026-06-01-a3a-a5b-live-role-recheck.md
```
Expected: branch `docs/a3a-a5b-live-role-recheck`; spec tracked; plan untracked.

- [ ] **Step 2: Commit the plan**

```bash
git add docs/superpowers/plans/2026-06-01-a3a-a5b-live-role-recheck.md
git commit -m "$(cat <<'EOF'
docs(a3a-a5b): live-role re-check implementation plan

Plan executes the A3.a + A5.b remediation: authorize() re-validates the
role against live *_ADDRESSES config (ignoring the JWT rol claim); auth
failures -> 401, insufficient/absent live role -> 403. Flips A3.a + A5.b
xfails, strengthens A3.b to stay a valid xfail, adds test_api.py coverage,
and closes out the docs.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 3: Push + open the docs PR**

```bash
git push -u origin docs/a3a-a5b-live-role-recheck
gh pr create --base main --head docs/a3a-a5b-live-role-recheck --title "docs(a3a-a5b): live-role re-check design + plan" --body "$(cat <<'EOF'
## Summary
- Adds the A3.a + A5.b remediation design spec + implementation plan.
- No code changes.

Remediates audit findings A3.a + A5.b (shared root cause: `authorize()` trusts the JWT's `rol` claim, never re-validating against live config). `authorize()` will authorize on `Role.address_role(sub)`; auth failures → 401, insufficient/absent live role → 403. Keeps A3.b a valid xfail (its real fix is the separate iss/aud PR). No schema change.

## Test plan
- [x] Spec + plan self-review passed.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 4: Stop — controller handles internal review + one Copilot backstop + mwg.**

---

## Task 2: Live-role re-check + test changes (impl PR)

**Files:**
- Modify: `src/cancelchain/api.py` (`authorize()` wrapper, lines 286-315)
- Modify: `tests/test_auth_audit.py` (flip A3.a + A5.b; strengthen A3.b)
- Modify: `tests/test_api.py` (2 new tests)

**Why one task:** the A3.a/A5.b xfails are `strict=True`; the moment `authorize()` is fixed they xpass and fail the suite unless their markers are removed in the same change. The A3.b test must also change in the same commit (the fix would otherwise flip it to a spurious xpass). So the source change and all test edits land together.

Branch off main after the docs PR merges:

```bash
git checkout main && git pull --ff-only
git checkout -b fix/a3a-a5b-live-role-recheck
uv run pytest -q 2>&1 | tail -1   # baseline: 265 passed, 7 xfailed, 1 skipped
```

- [ ] **Step 1: Rewrite the `authorize()` wrapper**

In `src/cancelchain/api.py`, replace the `wrapper` body (currently lines 286-315) with:

```python
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
            # Live config is the authority — the token's `rol` claim is
            # informational and is NOT trusted for authorization. Re-checking
            # Role.address_role on every request closes the forged-claim
            # (A3.a) and stale-role-after-revocation (A5.b) gaps.
            role = Role.address_role(address)
            if role is None or role.value < required_role.value:
                abort(403)
            kwargs['_address'] = address
            kwargs['_role'] = role
            return func(*args, **kwargs)
```

Note: the local `authorized = False` and `role: Role | None = None` initializers and the `Role[data['rol']]` line are removed. `role` is now the live role. (`Role` and `abort` are already imported.)

- [ ] **Step 2: Run the impact check — see which tests move**

```bash
uv run pytest tests/test_auth_audit.py tests/test_api.py -q 2>&1 | tail -20
```
Expected (before any test edits): `test_a3_a_*` and `test_a5_b_*` now **XPASS** (strict → reported as failures), `test_a3_b_*` also XPASSES, and `test_roles`/`test_no_role` still pass. This confirms the source change works and shows exactly which tests need editing. (If `test_roles` or `test_no_role` FAIL here, stop and investigate — the 401→403 change should not affect them.)

- [ ] **Step 3: Flip `test_a3_a` (remove xfail, past-tense)**

In `tests/test_auth_audit.py`, remove the `@pytest.mark.xfail(...)` decorator above `test_a3_a_forged_role_claim_accepted` and rewrite its docstring/comments to past tense. Replace the docstring and the trailing comment with:

```python
    """A3.a (remediated): a forged/over-claimed `rol` is not honored.

    reader_wallet is configured READER only. We mint a JWT directly
    (bypassing the handshake) claiming rol=MILLER for reader_wallet's
    address and present it to a MILLER-only endpoint. authorize() now
    re-checks Role.address_role(reader)=READER < MILLER and returns 403;
    pre-remediation the rol claim was trusted and the request reached the
    view (400 on the malformed block body).
    """
```
and the assertion's preceding comment:
```python
    # authorize() authorizes on the live role (READER), not the forged
    # rol=MILLER claim, so the request is rejected before the view runs.
    assert response.status_code == httpx.codes.FORBIDDEN
```
Leave the body (mint token, POST, assert FORBIDDEN) unchanged.

- [ ] **Step 4: Flip `test_a5_b` (remove xfail, past-tense)**

In `tests/test_auth_audit.py`, remove the `@pytest.mark.xfail(...)` decorator above `test_a5_b_stale_role_rejected_after_config_revocation` and rewrite its docstring to past tense:

```python
    """A5.b (remediated): a token's role is re-validated against live
    config, so a revoked address loses access immediately.

    A MILLER token is issued, then MILLER_ADDRESSES is emptied. authorize()
    re-checks Role.address_role and finds no role -> 403, rather than
    honoring the stale rol=MILLER claim for the token's 4h lifetime.
    """
```
Leave the body unchanged (it already asserts `r2.status_code in (UNAUTHORIZED, FORBIDDEN)`; the live re-check yields 403).

- [ ] **Step 5: Strengthen `test_a3_b` so it stays a valid xfail**

`test_a3_b_cross_node_token_replay` keeps its `@pytest.mark.xfail(strict=True)` marker (A3.b is NOT fixed here). But the live re-check would now reject the cross-node token on `remote_app` (where `wallet` has no role) → spurious xpass. Give `wallet.address` a qualifying role on `remote_app` so the live re-check passes there and the residual iss/aud gap is isolated. Edit the test: after the `assert secret_key == remote_app.config['SECRET_KEY']` precondition, add:

```python
    # Give wallet a legitimate role on remote_app so the per-request
    # live-role re-check (A3.a/A5.b fix) passes there. The token is then
    # accepted purely because both nodes share SECRET_KEY and the JWT has
    # no iss/aud binding — which is the A3.b gap this test isolates.
    with remote_app.app_context():
        remote_app.config['READER_ADDRESSES'] = [wallet.address]
```
and update the docstring + the trailing comment to reflect that the live-recheck no longer masks the gap:
```python
    """A JWT minted by `app` is accepted by `remote_app` purely because the
    two nodes share SECRET_KEY and the token carries no iss/aud claim.

    `wallet` is given a READER role on remote_app here, so the per-request
    live-role re-check (the A3.a/A5.b fix) passes — isolating the residual
    A3.b gap: nothing binds the token to the node that issued it. Secure
    behaviour (once iss/aud lands): remote_app rejects with 403. Today it
    accepts (404 — no chain on remote_app). Remains xfail until the iss/aud
    remediation.
    """
```
Also update the xfail `reason=` string's text if it references "wallet has no role" — keep it accurate ("accepted cross-node due to shared SECRET_KEY + no iss/aud").

- [ ] **Step 6: Add two tests to `tests/test_api.py`**

Append:

```python
def test_authorize_insufficient_live_role_forbidden(
    app, host, mill_block, reader_wallet
):
    # A wallet with a valid token but a live role below the endpoint's
    # requirement is forbidden (403), not unauthorized (401).
    with app.app_context():
        m, b = mill_block(reader_wallet)  # reader on-chain -> can get a token
        with pytest.raises(httpx.HTTPStatusError, match='403'):
            ApiClient(host, reader_wallet).post(
                f'/api/block/{b.block_hash}',
                data=b.to_json(),
                headers={'Content-Type': 'application/json'},
            )


def test_authorize_honors_live_downgrade(
    app, host, mill_block, miller_wallet
):
    # An address demoted mid-token-life is governed by its live role, not
    # the higher role baked into its still-valid token.
    with app.app_context():
        m, b = mill_block(miller_wallet)
        client = ApiClient(host, miller_wallet)
        assert client.get('/api/block').status_code == httpx.codes.OK
        # Demote: remove from MILLER, add to READER.
        app.config['MILLER_ADDRESSES'] = []
        app.config['READER_ADDRESSES'] = [
            *app.config['READER_ADDRESSES'],
            miller_wallet.address,
        ]
        # Same cached (MILLER-claim) token: still reads (live READER >= READER)
        assert client.get('/api/block').status_code == httpx.codes.OK
        # but is forbidden on the MILLER endpoint (live READER < MILLER).
        with pytest.raises(httpx.HTTPStatusError, match='403'):
            client.post(
                f'/api/block/{b.block_hash}',
                data=b.to_json(),
                headers={'Content-Type': 'application/json'},
            )
```
(`ApiClient`, `httpx`, `pytest` are already imported in `tests/test_api.py`.)

- [ ] **Step 7: Run the audit + api suites**

```bash
uv run pytest tests/test_auth_audit.py tests/test_api.py -q 2>&1 | tail -6
```
Expected: all pass except `test_a3_b_*` shows XFAIL; no XPASS/ERROR. Specifically `test_a3_a_*` and `test_a5_b_*` PASS, the 2 new tests PASS, `test_a3_b_*` XFAILs, `test_roles`/`test_no_role` PASS.

- [ ] **Step 8: Full suite + gates + xfail integrity**

```bash
uv run pytest 2>&1 | tail -2
uv run pytest --runxfail tests/test_auth_audit.py -q 2>&1 | tail -2
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy
```
Expected: `269 passed, 5 xfailed, 1 skipped`; `--runxfail` → `5 failed` (the remaining A1.a/A2.c/A2.e/A3.b/A7.a demos; A3.a/A5.b no longer among them); ruff + mypy clean. If `ruff format --check` wants changes, run `uv run ruff format src tests` and re-verify. Re-derive counts from actual output; do not hand-tune.

- [ ] **Step 9: Commit**

```bash
git add src/cancelchain/api.py tests/test_auth_audit.py tests/test_api.py
git commit -m "$(cat <<'EOF'
fix(a3a-a5b): re-validate role against live config in authorize()

authorize() now authorizes on Role.address_role(sub) (live *_ADDRESSES
config) instead of the JWT's claimed rol, which is no longer read. Auth
failures still -> 401; an authenticated caller whose live role is None or
below the requirement -> 403. Closes A3.a (a forged/over-claimed rol is
ignored) and A5.b (a revoked address loses access immediately, not after
the 4h token lifetime). The injected _role is the live role.

Flips test_a3_a + test_a5_b from strict-xfail to passing regression tests;
strengthens test_a3_b (gives wallet a role on remote_app) so the live
re-check does not spuriously close the still-open iss/aud gap; adds
test_authorize_insufficient_live_role_forbidden and
test_authorize_honors_live_downgrade.

Remediates audit findings A3.a + A5.b (Medium).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Documentation close-out (impl PR)

**Files:** `CLAUDE.md`, `docs/superpowers/audits/2026-05-31-api-authentication-audit.md`, `docs/superpowers/ROADMAP.md`. Use `#PRNUM` as the placeholder for the impl PR number (filled after Task 4 opens the PR).

**Anti-drift rule:** a remediated finding must read past-tense; no prose anywhere may still call A3.a/A5.b open. After editing, `grep -niE "A3\.a|A5\.b|rol-not-revalidated|trusts the signed rol|5 Medium"` the audit doc and confirm each hit reads remediated/consistent.

- [ ] **Step 1: CLAUDE.md**

`grep -n "authorize\|address_role\|JWT" CLAUDE.md` to find the API-auth description. Add/adjust a sentence so it states: `authorize()` re-validates the caller's role against live `*_ADDRESSES` config on every request via `Role.address_role`; the JWT `rol` claim is informational, not the authorization gate (so a revoked address loses access immediately and a forged/over-claimed role is not honored).

- [ ] **Step 2: Audit report — mark A3.a + A5.b remediated**

In `docs/superpowers/audits/2026-05-31-api-authentication-audit.md`:
1. **Executive summary headline:** `0 Critical / 0 High (1 remediated) / 5 Medium / 2 Low` → `0 Critical / 0 High (1 remediated) / 3 Medium (2 remediated) / 2 Low`.
2. **Executive-summary "Medium cluster" paragraph:** it currently says the cluster "is closed primarily by a one-line per-request `Role.address_role()` re-check (plus `iss`/`aud` for A3.b)". Update to past tense: A3.a + A5.b are now closed by that re-check (PR #PRNUM); A3.b's privilege half is mitigated (cross-node now requires a qualifying role on the target node) but its `iss`/`aud` binding remains its open fix.
3. **Findings-table intro:** update the count line to `0 Critical / 0 High / 3 Medium / 2 Low` framing (A3.a, A5.b remediated).
4. **Findings-table rows A3.a and A5.b:** prepend `✅ (remediated, PR #PRNUM) ` to each Description and past-tense it.
5. **A3.b findings-table row + test reference:** note the test now isolates the iss/aud gap (the live re-check no longer masks it); A3.b stays open.
6. **Per-adversary traces:** for **Adversary 3 → Attack a (A3.a)** and **Adversary 5 → Attack b (A5.b)**, prepend `✅ Remediated (PR #PRNUM). ` to the Finding line, past-tense the gap prose, and append `(As implemented: authorize() re-validates Role.address_role(sub) on every request; the JWT rol claim is no longer trusted for authorization; insufficient/absent live role → 403.)` For **A3.b**, add a sentence that the live re-check mitigates the privilege escalation but the iss/aud binding is still required (finding stays open).
7. **Cross-cutting observation #1** ("rol claim is the sole authorization gate"): rewrite to past tense — `authorize()` now consults live config every request; A3.a/A5.b closed; A3.b's residual is the missing iss/aud binding.
8. **Recommendations item 2** ("Re-validate the role against live config … closes A3.a + A5.b"): prepend `✅ (done — PR #PRNUM) ` and past-tense it. Update the "Recommended next action" / targeted-fixes prose so A3.a/A5.b are no longer listed as pending (the remaining near-term items are A3.b iss/aud, A2.c/A7.a throttling, A1.a, A2.e).

- [ ] **Step 3: Roadmap**

In `docs/superpowers/ROADMAP.md`, under "Audit remediation — API authentication findings", change the `- **A3.a + A5.b (Medium) — re-validate `rol` …` bullet to lead with `- ✅ **A3.a + A5.b (Medium) — live-role re-check in `authorize()`** — closed by PR #PRNUM.` and keep the description (now past tense). Add a half-sentence to the A3.b bullet that the live re-check mitigates the privilege half; iss/aud remains its fix.

- [ ] **Step 4: Verify**

```bash
grep -n "3 Medium" docs/superpowers/audits/2026-05-31-api-authentication-audit.md
grep -niE "✅.*A3\.a|✅.*A5\.b|A3.a.*A5.b.*live-role" docs/superpowers/ROADMAP.md
grep -niE "re-validates the caller|live .*_ADDRESSES|address_role" CLAUDE.md
uv run pytest 2>&1 | tail -1
uv run ruff check src tests && uv run ruff format --check src tests
```
Expected: audit headline shows 3 Medium; roadmap A3.a+A5.b ✅; CLAUDE.md mentions the live re-check; suite `269 passed, 5 xfailed, 1 skipped`; gates clean.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md docs/superpowers/audits/2026-05-31-api-authentication-audit.md docs/superpowers/ROADMAP.md
git commit -m "$(cat <<'EOF'
docs(a3a-a5b): close out — CLAUDE.md, audit report (A3.a+A5.b remediated), roadmap

Audit headline 0 Critical / 0 High / 3 Medium / 2 Low; A3.a + A5.b marked
remediated (✅, past tense, as-implemented note); A3.b annotated as
mitigated-but-open (iss/aud still required). CLAUDE.md notes authorize()
re-validates the role against live config. Roadmap A3.a+A5.b closed.
PR number placeholder #PRNUM to be filled.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Push + open impl PR

- [ ] **Step 1: Push**

```bash
git push -u origin fix/a3a-a5b-live-role-recheck
```

- [ ] **Step 2: Open the PR**

```bash
gh pr create --base main --title "fix(a3a-a5b): live-role re-check in authorize() (audit remediation)" --body "$(cat <<'EOF'
## Summary
Remediates audit findings **A3.a + A5.b** (shared root cause: `authorize()` trusted the JWT's `rol` claim, never re-validating against live config).

- `authorize()` now authorizes on `Role.address_role(sub)` (live `*_ADDRESSES` config); the token's `rol` claim is no longer read for the decision.
- Auth failures → 401 (unchanged); an authenticated caller whose live role is `None` or below the requirement → **403**.
- Closes A3.a (a forged/over-claimed `rol` is ignored) and A5.b (a revoked address loses access immediately, not after the 4h token lifetime). Injected `_role` is the live role.

## Tests
- Flips `test_a3_a_forged_role_claim_accepted` and `test_a5_b_stale_role_rejected_after_config_revocation` from strict-xfail to passing regression tests.
- Strengthens `test_a3_b_cross_node_token_replay` (gives `wallet` a role on `remote_app`) so the live re-check doesn't spuriously close it — **A3.b stays open** (its fix is the separate iss/aud PR; the live re-check only mitigates its privilege half).
- Adds `test_authorize_insufficient_live_role_forbidden` and `test_authorize_honors_live_downgrade` in `tests/test_api.py`.

## Out of scope
A3.b (iss/aud), A2.c/A7.a (throttling), A1.a (`SECRET_KEY` length), A2.e (content-type oracle) — separate PRs.

## Test plan
- [x] `uv run pytest` → `269 passed, 5 xfailed, 1 skipped`.
- [x] `uv run pytest --runxfail tests/test_auth_audit.py` → `5 failed` (A3.a/A5.b no longer among them).
- [x] `test_roles`/`test_no_role` unaffected (their 401s are token-acquisition failures; no test asserts 401-for-insufficient-role).
- [x] `ruff check` + `ruff format --check` + `mypy` clean.
- [ ] CI green on 3.12 and 3.13.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Fill the PR number** — replace `#PRNUM` in the audit report + roadmap with the real number, commit, push:

```bash
# (after noting the PR number, e.g. 106)
sed -i 's/#PRNUM/#<actual>/g' docs/superpowers/audits/2026-05-31-api-authentication-audit.md docs/superpowers/ROADMAP.md
git add docs/superpowers/audits/2026-05-31-api-authentication-audit.md docs/superpowers/ROADMAP.md
git commit -m "docs(a3a-a5b): fill impl PR number into audit report + roadmap"
git push
```

- [ ] **Step 4: Stop — controller handles internal review + one Copilot backstop + mwg.**

---

## Task 5: Acceptance (after the impl PR merges)

- [ ] **Step 1: Sync main + confirm merges**

```bash
git checkout main && git pull --ff-only
git log --oneline -3
```

- [ ] **Step 2: Behavior on main**

```bash
grep -n "Role.address_role(address)" src/cancelchain/api.py
grep -q "Role\[data\['rol'\]\]" src/cancelchain/api.py && echo "FAIL: rol still gates" || echo "ok: rol claim not gating"
grep -n "abort(403)" src/cancelchain/api.py
```
Expected: `authorize()` calls `Role.address_role(address)`; no `Role[data['rol']]` in the gate; an `abort(403)` present.

- [ ] **Step 3: Suite + xfail integrity**

```bash
uv run pytest 2>&1 | tail -2                                   # 269 passed, 5 xfailed, 1 skipped
uv run pytest --runxfail tests/test_auth_audit.py -q 2>&1 | tail -2   # 5 failed
uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy
```

- [ ] **Step 4: Docs reflect remediation**

```bash
grep -n "3 Medium" docs/superpowers/audits/2026-05-31-api-authentication-audit.md
grep -niE "✅.*A3\.a|A3.a.*A5.b.*live-role" docs/superpowers/ROADMAP.md
```
Expected: audit headline shows 3 Medium; roadmap A3.a+A5.b ✅ with the impl PR number.
