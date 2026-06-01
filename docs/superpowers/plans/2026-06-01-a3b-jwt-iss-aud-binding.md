# A3.b JWT `iss`/`aud` Node-Binding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remediate audit finding **A3.b** by binding each JWT to its issuing node — mint `iss`/`aud` = `NODE_HOST` in `TokenView.post` and enforce `issuer=`/`audience=` in `authorize()`'s `jwt.decode` — so a token issued by one node is rejected (401) by another sharing `SECRET_KEY`. Flip the A3.b demonstration test to passing.

**Architecture:** Two small `api.py` changes (mint + verify), plus fixing the `remote_app` conftest fixture so it has a real distinct `NODE_HOST` for the cross-node test. The A3.b xfail is `strict=True`, so the fix forces its marker-removal in the same commit; the existing handshake tests round-trip `iss`/`aud` on the same node unchanged.

**Tech Stack:** Python 3.12, Flask, PyJWT, pytest. No schema change. `mypy --strict` covers `src/` only. Companion spec: `docs/superpowers/specs/2026-06-01-a3b-jwt-iss-aud-binding-design.md`.

---

## Prerequisites

- Working directory: cancelchain repo root.
- A3.a+A5.b is merged: `git log --oneline -1 main` shows `9495ff9` (`fix(a3a-a5b): live-role re-check …`) or later.
- The branch `docs/a3b-jwt-iss-aud-binding` exists with one commit (the spec). This plan adds the plan file as a second commit and ships both as the docs PR.
- Test baseline: **269 passed, 5 xfailed, 1 skipped**. After this work: the A3.b xfail flips to passing (−1 xfailed, +1 passed) and 1 new test is added → **271 passed, 4 xfailed, 1 skipped**.
- CI hard-gates: `ruff check`, `ruff format --check`, `pytest`, `mypy`, `cancelchain db upgrade` + `cancelchain db check`.
- **Review loop** (`feedback_internal_review_then_one_copilot`): internal cross-model review to convergence before the PR (include the **regression-impact check** — does the change break existing tests/callers, and does the `remote_app` `NODE_HOST` fix affect gossip/sync tests?), then one Copilot backstop. Copilot does **not** auto-re-review — trigger `gh pr comment <N> --body "/copilot review"` if a fix round is needed. `wor`/`mwg` are controller work.
- Never push directly to `main`.

---

## File Map

| Task | PR | Files |
|---|---|---|
| 1 | docs PR | `docs/superpowers/plans/2026-06-01-a3b-jwt-iss-aud-binding.md` (this file) + spec already on branch |
| 2 | impl PR | `src/cancelchain/api.py` (mint + verify); `tests/conftest.py` (`remote_app` fixture); `tests/test_auth_audit.py` (flip A3.b); `tests/test_api.py` (1 new test) |
| 3 | impl PR | `CLAUDE.md`, `docs/superpowers/audits/2026-05-31-api-authentication-audit.md`, `docs/superpowers/ROADMAP.md` |
| 4 | impl PR | push + open PR |
| 5 | acceptance | none (verification only) |

---

## Task 1: Ship the docs PR (spec + plan)

- [ ] **Step 1: Confirm branch + spec tracked**

```bash
git rev-parse --abbrev-ref HEAD
git ls-files docs/superpowers/specs/2026-06-01-a3b-jwt-iss-aud-binding-design.md
git status docs/superpowers/plans/2026-06-01-a3b-jwt-iss-aud-binding.md
```
Expected: branch `docs/a3b-jwt-iss-aud-binding`; spec tracked; plan untracked.

- [ ] **Step 2: Commit the plan**

```bash
git add docs/superpowers/plans/2026-06-01-a3b-jwt-iss-aud-binding.md
git commit -m "$(cat <<'EOF'
docs(a3b): JWT iss/aud node-binding implementation plan

Plan executes the A3.b remediation: mint iss=aud=NODE_HOST in
TokenView.post; enforce issuer=/audience= in authorize()'s jwt.decode
(mismatch/missing -> 401). Folds in the remote_app fixture fix (real
distinct NODE_HOST) and flips the A3.b xfail. Closes out the docs.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 3: Push + open the docs PR**

```bash
git push -u origin docs/a3b-jwt-iss-aud-binding
gh pr create --base main --head docs/a3b-jwt-iss-aud-binding --title "docs(a3b): JWT iss/aud node-binding design + plan" --body "$(cat <<'EOF'
## Summary
- Adds the A3.b remediation design spec + implementation plan.
- No code changes.

Remediates audit finding A3.b: the JWT carries no `iss`/`aud`, so a token minted on one node is accepted by another sharing `SECRET_KEY`. Mint `iss`/`aud` = `NODE_HOST` and enforce `issuer=`/`audience=` in `authorize()`'s `jwt.decode` (mismatch/missing → 401). Folds in the pre-existing `remote_app` fixture fix (needed for a distinct remote `NODE_HOST`). No schema change.

## Test plan
- [x] Spec + plan self-review passed.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 4: Stop — controller handles internal review + one Copilot backstop + mwg.**

---

## Task 2: Bind tokens to the node + fixture fix + test changes (impl PR)

**Files:**
- Modify: `src/cancelchain/api.py` (`TokenView.post` mint ~lines 260-268; `authorize()` decode ~lines 295-300)
- Modify: `tests/conftest.py` (`remote_app` fixture signature, ~line 399)
- Modify: `tests/test_auth_audit.py` (flip `test_a3_b`)
- Modify: `tests/test_api.py` (1 new test + `import jwt`)

**Why one task:** the A3.b xfail is `strict=True`; fixing mint+verify makes it xpass and fails the suite unless its marker is removed in the same change.

Branch off main after the docs PR merges:

```bash
git checkout main && git pull --ff-only
git checkout -b fix/a3b-jwt-iss-aud-binding
uv run pytest -q 2>&1 | tail -1   # baseline: 269 passed, 5 xfailed, 1 skipped
```
If baseline differs, STOP and report BLOCKED.

- [ ] **Step 1: Fix the `remote_app` fixture (`tests/conftest.py`)**

The `remote_app` fixture body references `host_netloc` and `remote_host_netloc` as bare names, but they are fixture functions, not parameters — so `NODE_HOST`/`PEERS` resolve to fixture-object reprs. Add them as parameters. Change the signature:
```python
def remote_app(miller_2_wallet, miller_wallet, wallet):
```
to:
```python
def remote_app(
    miller_2_wallet, miller_wallet, wallet, host_netloc, remote_host_netloc
):
```
(The body already uses `host_netloc` for `peer_host` and `remote_host_netloc` for `NODE_HOST`; no body change needed.) After this, `remote_app`'s `NODE_HOST` is `http://peer.node:8888` and `app`'s is `http://localhost:8080`.

- [ ] **Step 2: Mint `iss`/`aud` in `TokenView.post` (`src/cancelchain/api.py`)**

Replace the `token = jwt.encode(...)` block (currently ~lines 260-268):
```python
        token = jwt.encode(
            {
                'sub': address,
                'rol': str(role.name),
                'exp': now().timestamp() + API_TOKEN_SECONDS,
            },
            current_app.config['SECRET_KEY'],
            algorithm='HS256',
        )
```
with:
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

- [ ] **Step 3: Enforce `issuer`/`audience` in `authorize()` (`src/cancelchain/api.py`)**

Replace the decode block (currently ~lines 295-300):
```python
                if token:
                    data = jwt.decode(
                        token,
                        current_app.config['SECRET_KEY'],
                        algorithms=['HS256'],
                    )
                    address = data['sub']
```
with:
```python
                if token:
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
A token whose `iss`/`aud` doesn't match `node_host` (or that lacks the claims) raises a `jwt` error caught by the existing `except Exception` → `abort(401)`.

- [ ] **Step 4: Impact check (see which tests move)**

```bash
uv run pytest tests/test_auth_audit.py tests/test_api.py -q 2>&1 | tail -20
```
Expected before test edits: `test_a3_b_*` now XPASSES (strict → reported as a failure) — `remote_app` (aud `peer.node:8888`) rejects the cross-node token (aud `localhost:8080`/none) with 401, but the test still asserts 403. The handshake-based tests (`test_roles`, `test_no_role`, A4.a/A3.a/A5.b regressions, live-role tests) still PASS (same-node round-trip of `iss`/`aud`). If any of those FAIL, STOP and report BLOCKED.

- [ ] **Step 5: Flip `test_a3_b` (`tests/test_auth_audit.py`)**

Remove the `@pytest.mark.xfail(strict=True)` decorator above `def test_a3_b_cross_node_token_replay`. Remove the now-unnecessary strengthening block:
```python
    # Give wallet a legitimate role on remote_app so the per-request
    # live-role re-check (the A3.a/A5.b fix) passes there. The token is then
    # accepted purely because both nodes share SECRET_KEY and the JWT has
    # no iss/aud binding — which is the A3.b gap this test isolates.
    with remote_app.app_context():
        remote_app.config['READER_ADDRESSES'] = [wallet.address]
```
Add `iss`/`aud` (= `app`'s `NODE_HOST`) to the minted token so it is a faithful app-issued token — change the `cross_node_token` payload:
```python
    cross_node_token = jwt.encode(
        {
            'sub': wallet.address,
            'rol': 'ADMIN',
            'iss': app.config['NODE_HOST'],
            'aud': app.config['NODE_HOST'],
            'exp': int(time.time()) + 3600,
        },
        secret_key,
        algorithm='HS256',
    )
```
Replace the docstring with:
```python
    """A3.b (remediated): a JWT minted for one node is rejected by another.

    The token is issued for `app` (iss/aud = app's NODE_HOST,
    http://localhost:8080) and presented to `remote_app` (NODE_HOST
    http://peer.node:8888). authorize() now verifies audience against the
    local NODE_HOST, so the mismatch raises InvalidAudienceError -> 401,
    even though both nodes share SECRET_KEY. Pre-remediation the token was
    accepted (no iss/aud binding).
    """
```
Replace the comment + assertion at the end:
```python
    # remote_app verifies `audience` against its own NODE_HOST; the token's
    # aud (app's NODE_HOST) doesn't match -> rejected at decode (401).
    response = remote_requests_proxy.get(
        '/api/block',
        headers={'Authorization': f'Bearer {cross_node_token}'},
        timeout=10,
    )
    assert response.status_code == httpx.codes.UNAUTHORIZED
```
Also update the `assert secret_key == remote_app.config['SECRET_KEY']` precondition line — keep it (it documents that the shared key is *not* what saves us; the binding is). Leave the `with app.app_context(): mill_block(wallet)` setup as-is.

- [ ] **Step 5b: Keep `test_a3_a` passing — add `iss`/`aud` to its forged token (`tests/test_auth_audit.py`)**

`test_a3_a_forged_role_claim_accepted` hand-mints a forged token (it is NOT handshake-issued), and currently omits `iss`/`aud`. Once `authorize()` enforces `audience=`/`issuer=`, a token lacking those claims raises `MissingRequiredClaimError` → 401, which would break that test's `403` assertion. The test's intent is to prove a forged *role* is rejected by the live-role check — so give the forged token valid node-binding claims (so it passes the audience/issuer gate) while keeping the bogus `rol`. Change its `forged_token` payload:
```python
    forged_token = jwt.encode(
        {
            'sub': reader_wallet.address,
            'rol': 'MILLER',  # reader_wallet only has READER in config
            'exp': int(time.time()) + 3600,
        },
        secret_key,
        algorithm='HS256',
    )
```
to:
```python
    forged_token = jwt.encode(
        {
            'sub': reader_wallet.address,
            'rol': 'MILLER',  # reader_wallet only has READER in config
            # valid node-binding (A3.b) so the token passes the audience
            # check; the forged ROLE is still rejected by the live-role
            # re-check (A3.a) -> 403.
            'iss': app.config['NODE_HOST'],
            'aud': app.config['NODE_HOST'],
            'exp': int(time.time()) + 3600,
        },
        secret_key,
        algorithm='HS256',
    )
```
(`secret_key = app.config['SECRET_KEY']` is already set just above this block.) The test still asserts `FORBIDDEN` (403) — the forged role is caught by `Role.address_role(reader) = READER < MILLER`, not by the audience gate.

- [ ] **Step 6: Add a same-node audience test (`tests/test_api.py`)**

Add `import jwt` to the top import block (let ruff sort). Append this test (`now` and `API_TOKEN_SECONDS` are already imported):
```python
def test_authorize_rejects_wrong_audience_token(app, requests_proxy, wallet):
    # A token signed with the live SECRET_KEY but bound to a different node
    # (wrong `aud`) is rejected at decode -> 401, regardless of the address's
    # role, proving the audience binding is enforced on the local node.
    wrong_aud = jwt.encode(
        {
            'sub': wallet.address,
            'rol': 'READER',
            'iss': 'http://elsewhere:9999',
            'aud': 'http://elsewhere:9999',
            'exp': now().timestamp() + API_TOKEN_SECONDS,
        },
        app.config['SECRET_KEY'],
        algorithm='HS256',
    )
    r = requests_proxy.get(
        '/api/block',
        headers={'Authorization': f'Bearer {wrong_aud}'},
        timeout=60,
    )
    assert r.status_code == httpx.codes.UNAUTHORIZED

    # A token with no `aud` claim at all is likewise rejected (decode
    # requires the audience claim when `audience=` is passed).
    no_aud = jwt.encode(
        {
            'sub': wallet.address,
            'rol': 'READER',
            'exp': now().timestamp() + API_TOKEN_SECONDS,
        },
        app.config['SECRET_KEY'],
        algorithm='HS256',
    )
    r2 = requests_proxy.get(
        '/api/block',
        headers={'Authorization': f'Bearer {no_aud}'},
        timeout=60,
    )
    assert r2.status_code == httpx.codes.UNAUTHORIZED
```

- [ ] **Step 7: Run the audit + api suites**

```bash
uv run pytest tests/test_auth_audit.py tests/test_api.py -q 2>&1 | tail -6
```
Expected: `test_a3_b_*` PASSES, the new test PASSES, the other 4 audit demos XFAIL, the rest pass; no XPASS/ERROR.

- [ ] **Step 8: Full suite + gates (watch for gossip/sync regressions from the `remote_app` NODE_HOST fix)**

```bash
uv run pytest 2>&1 | tail -2
uv run pytest --runxfail tests/test_auth_audit.py -q 2>&1 | tail -2
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy
```
Expected: `271 passed, 4 xfailed, 1 skipped`; `--runxfail` → `4 failed`; ruff + mypy clean (run `uv run ruff format src tests` if format-check wants changes). **If any gossip/sync test (e.g. in `tests/test_node.py` or peer tests in `tests/test_api.py`) now fails, it is because `remote_app`'s `NODE_HOST` changed from a garbage repr to `http://peer.node:8888` — investigate and report rather than papering over; the new value is correct, so a failing assertion likely encoded the old broken value.** Re-derive counts from actual output; do not hand-tune.

- [ ] **Step 9: Commit**

```bash
git add src/cancelchain/api.py tests/conftest.py tests/test_auth_audit.py tests/test_api.py
git commit -m "$(cat <<'EOF'
fix(a3b): bind the JWT to its issuing node via iss/aud

TokenView.post now mints iss=aud=NODE_HOST; authorize() passes
issuer=/audience=NODE_HOST to jwt.decode. A token whose iss/aud doesn't
match the receiving node (or that lacks the claims) raises a jwt error
-> abort(401), so a token issued by one node is no longer accepted by
another sharing SECRET_KEY. Same-node tokens round-trip unchanged.

Fixes the remote_app conftest fixture (host_netloc/remote_host_netloc as
fixture params) so it has a real distinct NODE_HOST (http://peer.node:8888)
to test the audience binding against. Flips test_a3_b from strict-xfail to
a passing regression test (asserts 401 cross-node) and drops its now-moot
role-grant strengthening; adds test_authorize_rejects_wrong_audience_token.

Remediates audit finding A3.b (Medium).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Documentation close-out (impl PR)

**Files:** `CLAUDE.md`, `docs/superpowers/audits/2026-05-31-api-authentication-audit.md`, `docs/superpowers/ROADMAP.md`. Use `#PRNUM` placeholder for the impl PR number (filled after Task 4 opens the PR).

**Anti-drift rule:** A3.b reads remediated/past-tense after this; no prose may still call it open. After editing, `grep -niE "A3\.b|3 Medium|no iss/aud|cross-node"` the audit doc and confirm each hit is consistent.

- [ ] **Step 1: CLAUDE.md**

`grep -n "authorize\|JWT\|iss\|aud\|NODE_HOST" CLAUDE.md`. In the API-auth section, add a sentence: the JWT is bound to its issuing node — `iss`/`aud` are set to `NODE_HOST` at issuance and verified on decode, so a token issued by one node is rejected by another even when they share `SECRET_KEY`.

- [ ] **Step 2: Audit report — mark A3.b remediated**

In `docs/superpowers/audits/2026-05-31-api-authentication-audit.md`:
1. **Exec summary headline (~line 15):** `3 Medium (2 remediated)` → `2 Medium (A3.b also remediated)` so the line reads `0 Critical / 0 High (1 remediated) / 2 Medium / 2 Low`.
2. **Exec summary "Medium cluster" paragraph (~line 19):** it currently says A3.a/A5.b closed and A3.b's iss/aud "remains its open fix". Update: A3.b is now **closed** (PR #PRNUM) — `iss`/`aud` bind each token to its issuing node; the whole `rol`/cross-node cluster is remediated. The remaining Mediums are A2.c + A7.a.
3. **Findings-table intro (~line 44):** update to `2 Medium` (A3.a/A5.b/A3.b remediated); the remaining Mediums are A2.c, A7.a.
4. **Findings-table row A3.b (~line 51):** prepend `✅ (remediated, PR #PRNUM) ` and past-tense; note iss/aud now bind the token.
5. **A3.b per-adversary finding (~lines 363-368):** change the heading from `**Finding A3.b — Severity Medium (open):**` to `✅ Remediated (PR #PRNUM). **Finding A3.b — Severity Medium:**`; past-tense the gap prose; replace the "A3.b remains open" Note with an `(As implemented: TokenView.post mints iss=aud=NODE_HOST; authorize() enforces issuer=/audience= on decode; a token not issued for the receiving node → 401.)` reconciliation.
6. **Cross-cutting observation #1 (~line 785):** it says A3.b's residual is the missing iss/aud binding — update to: that binding has now landed (PR #PRNUM); the cluster is fully closed.
7. **Cross-cutting observation #2 ("claim hygiene is minimal", ~line 787):** update — `iss`/`aud` are now present; `iat`/`jti` remain absent (the remaining hygiene gap; no per-token revocation handle yet).
8. **Out-of-scope note about the `remote_app` fixture bug (~line 797):** remove it (or mark fixed) — the fixture is corrected in this PR.
9. **Recommendations item 3 ("Add and verify JWT claim hygiene — closes A3.b", ~line 807):** prepend `✅ (done — PR #PRNUM) ` for the iss/aud part; note `iat`/`jti` remain if desired.

- [ ] **Step 3: Roadmap**

In `docs/superpowers/ROADMAP.md`, under "Audit remediation — API authentication findings", change the `- **A3.b (Medium) — add + verify JWT iss/aud …`** bullet to lead with `- ✅ **A3.b (Medium) — JWT iss/aud node-binding** — closed by PR #PRNUM.` and past-tense the description.

- [ ] **Step 4: Verify**

```bash
grep -n "2 Medium" docs/superpowers/audits/2026-05-31-api-authentication-audit.md
grep -niE "✅.*A3.b" docs/superpowers/ROADMAP.md
grep -niE "iss.*aud|bound to its issuing node|NODE_HOST" CLAUDE.md
uv run pytest 2>&1 | tail -1
uv run ruff check src tests && uv run ruff format --check src tests
```
Expected: audit headline `2 Medium`; roadmap A3.b ✅; CLAUDE.md mentions the node-binding; suite `271 passed, 4 xfailed, 1 skipped`; gates clean.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md docs/superpowers/audits/2026-05-31-api-authentication-audit.md docs/superpowers/ROADMAP.md
git commit -m "$(cat <<'EOF'
docs(a3b): close out — CLAUDE.md, audit report (A3.b remediated), roadmap

Audit headline 0 Critical / 0 High / 2 Medium / 2 Low; A3.b marked
remediated (✅, past tense, as-implemented note); the rol/cross-node
cluster (A3.a/A5.b/A3.b) is fully closed; claim-hygiene observation
updated (iss/aud present, iat/jti still absent); removed the now-fixed
remote_app fixture out-of-scope note. CLAUDE.md notes the JWT is bound to
its issuing node. Roadmap A3.b closed. PR number placeholder #PRNUM.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Push + open impl PR

- [ ] **Step 1: Push**

```bash
git push -u origin fix/a3b-jwt-iss-aud-binding
```

- [ ] **Step 2: Open the PR**

```bash
gh pr create --base main --title "fix(a3b): JWT iss/aud node-binding (audit remediation)" --body "$(cat <<'EOF'
## Summary
Remediates audit finding **A3.b**: the JWT carried no `iss`/`aud`, so a token minted on one node was accepted by another sharing `SECRET_KEY`.

- `TokenView.post` mints `iss` = `aud` = `NODE_HOST`.
- `authorize()` passes `issuer=`/`audience=` `NODE_HOST` to `jwt.decode`; a token whose `iss`/`aud` doesn't match the receiving node (or that lacks the claims) → **401** (via the existing `except`).
- Same-node tokens round-trip unchanged (the handshake mints and verifies against the same `NODE_HOST`).

Together with the A3.a/A5.b live-role re-check, the whole `rol`/cross-node cluster is now closed.

## Fixture fix (in scope)
Fixes the `remote_app` conftest fixture (it referenced `host_netloc`/`remote_host_netloc` as bare names) so it has a real distinct `NODE_HOST` (`http://peer.node:8888`) to test the audience binding against — the pre-existing bug the audit flagged.

## Tests
- Flips `test_a3_b_cross_node_token_replay` to passing (asserts 401 cross-node), drops its now-moot role-grant strengthening.
- Adds `test_authorize_rejects_wrong_audience_token` (wrong/missing `aud` → 401, same node).

## Out of scope
`iat`/`jti` claim hygiene + revocation, A2.c/A7.a (throttling), A1.a (`SECRET_KEY` length), A2.e (content-type oracle) — separate PRs. No startup `NODE_HOST` guard.

## Test plan
- [x] `uv run pytest` → `271 passed, 4 xfailed, 1 skipped`.
- [x] `uv run pytest --runxfail tests/test_auth_audit.py` → `4 failed` (A3.b no longer among them).
- [x] Existing handshake tests pass unchanged (same-node `iss`/`aud` round-trip); no gossip/sync regression from the `remote_app` `NODE_HOST` fix.
- [x] `ruff check` + `ruff format --check` + `mypy` clean.
- [ ] CI green on 3.12 and 3.13.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Fill the PR number** — replace `#PRNUM` in the audit report + roadmap with the real number, commit, push:

```bash
# (after noting the PR number, e.g. 108)
sed -i 's/#PRNUM/#<actual>/g' docs/superpowers/audits/2026-05-31-api-authentication-audit.md docs/superpowers/ROADMAP.md
git add docs/superpowers/audits/2026-05-31-api-authentication-audit.md docs/superpowers/ROADMAP.md
git commit -m "docs(a3b): fill impl PR number into audit report + roadmap"
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
grep -n "'iss': node_host" src/cancelchain/api.py && echo "ok: iss/aud minted"
grep -n "issuer=node_host" src/cancelchain/api.py && grep -n "audience=node_host" src/cancelchain/api.py && echo "ok: issuer/audience enforced"
grep -n "def remote_app(" tests/conftest.py   # should include host_netloc, remote_host_netloc
```

- [ ] **Step 3: Suite + xfail integrity**

```bash
uv run pytest 2>&1 | tail -2                                   # 271 passed, 4 xfailed, 1 skipped
uv run pytest --runxfail tests/test_auth_audit.py -q 2>&1 | tail -2   # 4 failed
uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy
```

- [ ] **Step 4: Docs reflect remediation**

```bash
grep -n "2 Medium" docs/superpowers/audits/2026-05-31-api-authentication-audit.md
grep -niE "✅.*A3.b" docs/superpowers/ROADMAP.md
```
Expected: audit headline `2 Medium`; roadmap A3.b ✅ with the impl PR number.
