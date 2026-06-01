# A4.a Exact-Match Role Allowlists Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remediate audit finding **A4.a (High)** by replacing regex role-matching with exact-address allowlists (plus a READER-only `"*"` sentinel) validated at `create_app` startup, and flip the A4.a demonstration test from `xfail` to a passing regression test.

**Architecture:** Three coordinated source changes — a new `InvalidRoleConfigError`, exact-match `Role.address_roles`, and a `Role.validate_config` startup gate wired into `create_app` (fail-hard). Then flip the A4.a `xfail` and add positive/negative role-config coverage, and close out the docs (CLAUDE.md, audit report severity headline, roadmap).

**Tech Stack:** Python 3.12, Flask, pytest. The fix is pure application/config logic — no DB schema change, no migration (pre-1.0, no deployed nodes). `mypy --strict` covers `src/` only (not tests). The companion spec is `docs/superpowers/specs/2026-05-31-a4a-exact-match-role-allowlists-design.md`.

---

## Prerequisites

- Working directory: the cancelchain repo root. Run all commands from there.
- `uv --version` 0.4.x+; `gh auth status` authenticated.
- The audit is merged: `git log --oneline -1 main` shows `e059204` (`docs(roadmap): close API auth audit …`) or later.
- The branch `docs/a4a-exact-match-role-allowlists` exists with one commit (the spec). This plan adds the plan file as a second commit and ships both as the docs PR.
- Test baseline: **256 passed, 8 xfailed, 1 skipped**. After this work: the obsolete `test_regex_roles` is removed and 7 new role-config tests are added (Task 2), and the A4.a xfail flips to a pass (Task 3) → **263 passed, 7 xfailed, 1 skipped** (256 − 1 removed + 7 new + 1 flipped = 263 passed; 8 − 1 = 7 xfailed).
- CI hard-gates: `ruff check`, `ruff format --check`, `pytest`, `mypy`, `cancelchain db upgrade` + `cancelchain db check`.
- **Review loop** (per `feedback_internal_review_then_one_copilot`): before opening each PR, run the internal cross-model (Sonnet) review to convergence, then exactly one Copilot backstop. Copilot does **not** auto-re-review here — trigger with `gh pr comment <N> --body "/copilot review"` if a fix round is needed. `wor`/`mwg` are controller work.
- Never push directly to `main`.

---

## File Map

| Task | PR | Files |
|---|---|---|
| 1 | docs PR | `docs/superpowers/plans/2026-05-31-a4a-exact-match-role-allowlists.md` (this file) + spec already on branch |
| 2 | impl PR | `src/cancelchain/exceptions.py`, `src/cancelchain/api.py`, `src/cancelchain/__init__.py`, `tests/test_api.py` |
| 3 | impl PR | `tests/test_auth_audit.py` (flip the A4.a xfail) |
| 4 | impl PR | `CLAUDE.md`, `docs/superpowers/audits/2026-05-31-api-authentication-audit.md`, `docs/superpowers/ROADMAP.md` |
| 5 | impl PR | push + open PR |
| 6 | acceptance | none (verification only) |

---

## Task 1: Ship the docs PR (spec + plan)

**Files:** spec committed on `docs/a4a-exact-match-role-allowlists`. Add this plan as a second commit; ship both.

- [ ] **Step 1: Confirm branch + spec tracked**

```bash
git rev-parse --abbrev-ref HEAD
git ls-files docs/superpowers/specs/2026-05-31-a4a-exact-match-role-allowlists-design.md
git status docs/superpowers/plans/2026-05-31-a4a-exact-match-role-allowlists.md
```
Expected: branch `docs/a4a-exact-match-role-allowlists`; spec tracked; plan file untracked.

- [ ] **Step 2: Commit the plan**

```bash
git add docs/superpowers/plans/2026-05-31-a4a-exact-match-role-allowlists.md
git commit -m "$(cat <<'EOF'
docs(a4a): exact-match role allowlists implementation plan

Plan executes the A4.a remediation specified in
2026-05-31-a4a-exact-match-role-allowlists-design.md: exact-address
allowlists + READER-only "*" sentinel, startup validation via
Role.validate_config (fail-hard, new InvalidRoleConfigError), flip the
A4.a xfail to a passing regression test, and close out the docs.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 3: Push + open the docs PR**

```bash
git push -u origin docs/a4a-exact-match-role-allowlists
gh pr create --base main --head docs/a4a-exact-match-role-allowlists --title "docs(a4a): exact-match role allowlists design + plan" --body "$(cat <<'EOF'
## Summary
- Adds the A4.a remediation design spec + implementation plan.
- No code changes.

Remediates audit finding A4.a (High): unvalidated `*_ADDRESSES` regexes let an overbroad pattern (`CC.*CC`) escalate every authenticated address. Replaces regex matching with exact-address allowlists + a READER-only `"*"` sentinel, validated at `create_app` startup (fail-hard via a new `InvalidRoleConfigError`). Flips the A4.a `xfail` to a passing regression test. No schema change; pre-1.0 so no migration.

## Test plan
- [x] Spec self-review passed.
- [x] Plan self-review passed.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 4: Stop — controller handles internal review + one Copilot backstop + mwg.**

---

## Task 2: Core implementation (exception + exact-match matching + startup validation)

**Files:**
- Modify: `src/cancelchain/exceptions.py` (add `InvalidRoleConfigError`)
- Modify: `src/cancelchain/api.py` (imports; `Role.address_roles`; new `Role.validate_config`)
- Modify: `src/cancelchain/__init__.py` (call `Role.validate_config` in `create_app`)
- Test: `tests/test_api.py` (new role-config tests)

Branch off main after the docs PR merges:

```bash
git checkout main && git pull --ff-only
git checkout -b fix/a4a-exact-match-role-allowlists
uv run pytest -q 2>&1 | tail -1   # baseline: 256 passed, 8 xfailed, 1 skipped
```

- [ ] **Step 1: Write the failing role-config tests in `tests/test_api.py`**

Add these imports to the top import block of `tests/test_api.py` (let ruff sort):

```python
from cancelchain import create_app
from cancelchain.api import Role
from cancelchain.exceptions import InvalidRoleConfigError
```

Append these tests to `tests/test_api.py`:

```python
# NOTE: the `app` fixture pre-loads all four *_ADDRESSES (the `wallet`
# fixture's address is in ADMIN_ADDRESSES). Each matching test below
# resets all four lists first so it controls the role config exactly —
# otherwise an unrelated pre-loaded entry (e.g. ADMIN) would win.

def _clear_role_config(app):
    for key in (
        'READER_ADDRESSES',
        'TRANSACTOR_ADDRESSES',
        'MILLER_ADDRESSES',
        'ADMIN_ADDRESSES',
    ):
        app.config[key] = []


def test_address_role_exact_match(app, wallet):
    other = Wallet()
    with app.app_context():
        _clear_role_config(app)
        app.config['MILLER_ADDRESSES'] = [wallet.address]
        assert Role.address_role(wallet.address) is Role.MILLER
        assert Role.address_role(other.address) is None


def test_address_role_reader_wildcard(app, wallet):
    with app.app_context():
        _clear_role_config(app)
        app.config['READER_ADDRESSES'] = ['*']
        assert Role.address_role(wallet.address) is Role.READER
        assert Role.address_role(Wallet().address) is Role.READER


def test_address_role_highest_wins(app, wallet):
    with app.app_context():
        _clear_role_config(app)
        app.config['READER_ADDRESSES'] = [wallet.address]
        app.config['MILLER_ADDRESSES'] = [wallet.address]
        assert Role.address_role(wallet.address) is Role.MILLER


def test_validate_config_rejects_nonaddress(app):
    app.config['ADMIN_ADDRESSES'] = ['CC.*CC']
    with pytest.raises(InvalidRoleConfigError, match='ADMIN_ADDRESSES'):
        Role.validate_config(app.config)


def test_validate_config_rejects_wildcard_outside_reader(app):
    for role_key in (
        'TRANSACTOR_ADDRESSES',
        'MILLER_ADDRESSES',
        'ADMIN_ADDRESSES',
    ):
        app.config[role_key] = ['*']
        with pytest.raises(InvalidRoleConfigError, match=role_key):
            Role.validate_config(app.config)
        app.config[role_key] = []


def test_validate_config_accepts_reader_wildcard_and_exact(app, wallet):
    app.config['READER_ADDRESSES'] = ['*']
    app.config['ADMIN_ADDRESSES'] = [wallet.address]
    Role.validate_config(app.config)  # must not raise


def test_create_app_rejects_overbroad_admin_config():
    with pytest.raises(InvalidRoleConfigError, match='ADMIN_ADDRESSES'):
        create_app(
            config_map={
                'TESTING': True,
                'SECRET_KEY': 'x' * 32,
                'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
                'ADMIN_ADDRESSES': ['CC.*CC'],
            },
            register_browser=False,
        )
```

Also **delete** the existing `test_regex_roles` function from `tests/test_api.py` (currently at ~line 95). It asserts the regex-matching behavior this change removes — `READER_ADDRESSES=['.*']` granting an arbitrary wallet READER, and `['CC.*CC']` matching every CC-address — i.e. the exact over-match A4.a eliminates. Under exact-match its assertions invert, so it must go; the legitimate "open read via pattern" intent it expressed is preserved by the new `test_address_role_reader_wildcard` (the `"*"` sentinel). Leave `test_roles` and `test_no_role` untouched.

- [ ] **Step 2: Run the new tests — verify they fail**

```bash
uv run pytest tests/test_api.py -k "role or validate_config or overbroad" -q 2>&1 | tail -15
```
Expected: failures/errors — `InvalidRoleConfigError` does not exist yet (ImportError at collection) and `Role.validate_config` is undefined.

- [ ] **Step 3: Add `InvalidRoleConfigError` to `src/cancelchain/exceptions.py`**

Append at the end of the file:

```python
class InvalidRoleConfigError(CCError):
    pass
```

(`CCError` is the module's base exception. This is a startup/config error category; A1.a's future `SECRET_KEY` startup check can add a sibling here.)

- [ ] **Step 4: Rewrite matching + add validation in `src/cancelchain/api.py`**

a. **Imports.** Remove the now-unused `import re` (line 4 — `re.fullmatch` was its only use). Add `Mapping` to the `collections.abc` import, `InvalidRoleConfigError` to the exceptions import, and `validate_address_format` to the schema import:

```python
# line 5: was `from collections.abc import Callable`
from collections.abc import Callable, Mapping
# line 36: add InvalidRoleConfigError
from cancelchain.exceptions import (
    CCError,
    EmptyChainError,
    InvalidRoleConfigError,
    MissingBlockError,
)
# line 40 block: add validate_address_format
from cancelchain.schema import (
    AddressType,
    PublicKeyType,
    pydantic_errors_to_messages,
    truncate,
    validate_address_format,
)
```

b. **Replace `Role.address_roles`** (currently uses `re.fullmatch`):

```python
    @classmethod
    def address_roles(cls, address: str) -> list[Role]:
        return [
            role
            for role in Role
            if (addrs := role.addresses()) is not None
            and (address in addrs or '*' in addrs)
        ]
```

c. **Add `Role.validate_config`** as a new classmethod on `Role` (place it right after `address_role`):

```python
    @classmethod
    def validate_config(cls, config: Mapping[str, Any]) -> None:
        """Reject malformed role allowlists at startup.

        Each *_ADDRESSES entry must be a valid cancelchain address,
        except the '*' match-all sentinel which is permitted only in
        READER_ADDRESSES. Raises InvalidRoleConfigError on any violation.
        """
        for role in cls:
            for entry in config.get(f'{role.name}_ADDRESSES', []) or []:
                if entry == '*':
                    if role is not cls.READER:
                        msg = (
                            f'{role.name}_ADDRESSES contains "*" '
                            '(match-all is permitted only in '
                            'READER_ADDRESSES)'
                        )
                        raise InvalidRoleConfigError(msg)
                elif not validate_address_format(entry):
                    msg = (
                        f'{role.name}_ADDRESSES entry {entry!r} '
                        'is not a valid cancelchain address'
                    )
                    raise InvalidRoleConfigError(msg)
```

- [ ] **Step 5: Wire `validate_config` into `create_app` (`src/cancelchain/__init__.py`)**

In `create_app`, immediately after the `config_map` merge (current lines 67-68) and before `init_app` (line 70), add a deferred import + the validation call:

```python
    if config_map is not None:
        app.config.from_mapping(config_map)

    from .api import Role  # noqa: PLC0415 — deferred (api imports app modules)

    Role.validate_config(app.config)

    init_app(app, register_browser=register_browser)
```

This runs after all config layers (`from_prefixed_env`, `from_env`, `CANCELCHAIN_SETTINGS`, `config_map`) are applied and **before** `db`/`cache`/`tasks` init. It is deliberately NOT wrapped in the log-and-continue `try/except` those resource steps use — a malformed auth allowlist must raise and abort startup.

- [ ] **Step 6: Run the new tests — verify they pass**

```bash
uv run pytest tests/test_api.py -k "role or validate_config or overbroad" -q 2>&1 | tail -5
```
Expected: all the new tests pass (7 tests).

- [ ] **Step 7: Run the full suite + gates**

```bash
uv run pytest 2>&1 | tail -2
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy
```
Expected: pytest `262 passed, 8 xfailed, 1 skipped` (256 baseline − the removed `test_regex_roles` + 7 new test functions; the A4.a flip happens in Task 3, so xfailed is still 8 here). ruff/mypy clean. (If `ruff check` flags `import re` as still present/unused, confirm it was removed in Step 4a.)

> Count note: Step 1 removes `test_regex_roles` (−1) and adds 7 test functions (+7) → 262 passed at this task. Task 3 flips the A4.a xfail (8 → 7 xfailed, +1 passed) → final `263 passed, 7 xfailed, 1 skipped`. Re-derive from actual output; do not hand-tune.

- [ ] **Step 8: Commit**

```bash
git add src/cancelchain/exceptions.py src/cancelchain/api.py src/cancelchain/__init__.py tests/test_api.py
git commit -m "$(cat <<'EOF'
fix(a4a): exact-match role allowlists with startup validation

Replaces re.fullmatch role matching with exact-address membership plus
a READER-only "*" sentinel, and validates *_ADDRESSES at create_app
startup via Role.validate_config (fail-hard, new InvalidRoleConfigError).
An overbroad pattern like CC.*CC can no longer escalate any wallet: at
startup it is rejected as a non-address entry, and at match time it is
an inert non-matching literal. Drops the now-unused `import re`.

Remediates audit finding A4.a (High).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: Flip the A4.a demonstration test

**Files:**
- Modify: `tests/test_auth_audit.py` (remove the A4.a `xfail`, rename, past-tense the docstring)

The current test mutates `app.config['ADMIN_ADDRESSES'] = ['CC.*CC']` at runtime (after startup) and checks the minted `rol`. With exact-match, `'CC.*CC'` is now an inert non-matching literal, so the reader wallet resolves to READER — the test passes once the marker is removed. (The startup-validation path is covered by `test_create_app_rejects_overbroad_admin_config` in Task 2.)

- [ ] **Step 1: Replace the A4.a test**

In `tests/test_auth_audit.py`, replace the entire `@pytest.mark.xfail(...)`-decorated `test_a4_a_overbroad_admin_regex_escalates_reader` (decorator + function) with this un-marked, renamed regression test:

```python
def test_a4_a_overbroad_admin_regex_does_not_escalate(
    app, host, mill_block, requests_proxy, reader_wallet
):
    """A4.a (remediated): an overbroad ADMIN_ADDRESSES entry does not
    escalate a reader-role wallet.

    Pre-remediation, *_ADDRESSES were regex-matched, so 'CC.*CC' matched
    every valid address and Role.address_role() returned ADMIN for the
    reader wallet. Now matching is exact-membership: 'CC.*CC' is an inert
    non-matching literal, so the reader wallet resolves to READER. (A
    fresh app configured this way is also rejected at startup — see
    test_create_app_rejects_overbroad_admin_config in tests/test_api.py.)
    """
    with app.app_context():
        # Give the reader wallet chain presence so the handshake can
        # resolve its public key.
        mill_block(reader_wallet)

        # An overbroad entry that previously matched every address.
        app.config['ADMIN_ADDRESSES'] = ['CC.*CC']

        client = ApiClient(host, reader_wallet)
        raw_token = client.request_token(rfs=True)
        assert raw_token is not None, (
            'handshake failed — reader wallet not in chain'
        )
        payload = jwt.decode(
            raw_token,
            options={'verify_signature': False},
            algorithms=['HS256'],
        )
        awarded_role = Role[payload['rol']]
        assert awarded_role.value <= Role.READER.value, (
            f'reader_wallet was awarded {awarded_role.name!r}; exact-match '
            'role allowlists must not honor the overbroad literal CC.*CC'
        )
```

- [ ] **Step 2: Verify the flipped test passes and no xfail remains for A4.a**

```bash
uv run pytest tests/test_auth_audit.py -k a4_a -q 2>&1 | tail -5
grep -c 'def test_a' tests/test_auth_audit.py        # still 8 (one renamed, none added)
grep -c '@pytest.mark.xfail' tests/test_auth_audit.py # now 7
```
Expected: the A4.a test passes (1 passed); 8 `def test_a`; 7 xfail markers.

- [ ] **Step 3: Verify the audit suite + full suite**

```bash
uv run pytest tests/test_auth_audit.py -q 2>&1 | tail -2   # 1 passed, 7 xfailed
uv run pytest 2>&1 | tail -2                                # 263 passed, 7 xfailed, 1 skipped
uv run pytest --runxfail tests/test_auth_audit.py -q 2>&1 | tail -2  # 7 failed (A4.a no longer demonstrates a gap)
```
Expected as annotated.

- [ ] **Step 4: Commit**

```bash
git add tests/test_auth_audit.py
git commit -m "$(cat <<'EOF'
test(a4a): flip the A4.a xfail to a passing regression test

Exact-match role allowlists make the overbroad 'CC.*CC' literal inert,
so the reader wallet no longer escalates. Removes the strict xfail and
reframes the test in past tense as a regression guard. Startup-rejection
of the same config is covered by test_create_app_rejects_overbroad_admin_config.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Documentation close-out

**Files:**
- Modify: `CLAUDE.md`
- Modify: `docs/superpowers/audits/2026-05-31-api-authentication-audit.md`
- Modify: `docs/superpowers/ROADMAP.md`

- [ ] **Step 1: Update `CLAUDE.md`**

In the "API authentication" section, the text describes role keying. Find the phrase describing `*_ADDRESSES` as regex-matched (it reads roughly: "keyed off wallet addresses listed in config" / "`*_ADDRESSES` (regex-matched against the JWT `sub` in `api.Role.address_role`)"). Update the regex description to:

> `{ADMIN,MILLER,TRANSACTOR,READER}_ADDRESSES` are **exact-address allowlists** matched against the JWT `sub` in `api.Role.address_role`. `READER_ADDRESSES` may contain the literal `"*"` to grant READER to any authenticated wallet; a non-address entry, or `"*"` outside `READER_ADDRESSES`, is rejected at startup (`Role.validate_config` → `InvalidRoleConfigError`).

Run `grep -n "ADDRESSES" CLAUDE.md` first to locate the exact sentence, and edit it in place. Do not change unrelated text.

- [ ] **Step 2: Mark A4.a remediated in the audit report**

In `docs/superpowers/audits/2026-05-31-api-authentication-audit.md`, make these edits (use `grep -n` to locate each):

a. **Executive summary** — change the headline `**8 findings: 0 Critical / 1 High / 5 Medium / 2 Low.**` to `**8 findings: 0 Critical / 0 High (1 remediated) / 5 Medium / 2 Low.**`. Reframe the paragraph that calls A4.a "The single most important finding" to past tense and note remediation, e.g. start it with: "The lone High, **A4.a** (now remediated, PR #<impl>), was an unvalidated `*_ADDRESSES` regex …" and keep the rest descriptive. Update the closing "Recommended next action" sentence to drop A4.a from the "land immediately" list (it's done) while keeping the live-role re-check + `SECRET_KEY` check.

b. **Findings table** — in the intro line change `0 Critical / 1 High / 5 Medium / 2 Low` to `0 Critical / 0 High (A4.a remediated) / 5 Medium / 2 Low`. In the A4.a row, prepend `✅ ` to the Description cell and append ` (remediated, PR #<impl>)`.

c. **Per-adversary trace — Adversary 4, Attack a** — prepend the Finding line with `✅ Remediated (PR #<impl>). ` and rewrite the gap sentence(s) in PAST tense (e.g. "`*_ADDRESSES` **were** regex-matched, so an overbroad pattern **matched** every address"). Append to the Remediation sketch: ` (As implemented: regex matching replaced with exact-address membership + a READER-only "*" sentinel; Role.validate_config rejects non-address entries and out-of-READER "*" at create_app startup via InvalidRoleConfigError.)`

d. **Recommendations** — in the A4.a remediation bullet (item 1 of "Targeted remediations"), prepend `✅ (done — PR #<impl>) `.

Replace `#<impl>` with the actual impl PR number (known once Task 5 opens the PR — update in a follow-up commit or fill before opening; the controller substitutes the number).

- [ ] **Step 3: Update the roadmap**

In `docs/superpowers/ROADMAP.md`, under "Audit remediation — API authentication findings (PR #102)", change the A4.a bullet from `- **A4.a (High) — validate \`*_ADDRESSES\` regexes at config load.** …` to lead with `- ✅ **A4.a (High) — exact-match role allowlists** — closed by PR #<impl>. Replaced regex matching with exact-address membership + a READER-only "*" sentinel, validated at startup (`Role.validate_config` → `InvalidRoleConfigError`). …` keeping the remaining description.

- [ ] **Step 4: Verify docs + full gates**

```bash
grep -n "0 High" docs/superpowers/audits/2026-05-31-api-authentication-audit.md
grep -n "✅.*A4.a\|A4.a.*remediated\|exact-match" docs/superpowers/ROADMAP.md
uv run pytest 2>&1 | tail -2
uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy
```
Expected: headline shows the High remediated; roadmap shows A4.a ✅; suite `263 passed, 7 xfailed, 1 skipped`; gates clean.

- [ ] **Step 5: Commit**

```bash
git add CLAUDE.md docs/superpowers/audits/2026-05-31-api-authentication-audit.md docs/superpowers/ROADMAP.md
git commit -m "$(cat <<'EOF'
docs(a4a): close out — CLAUDE.md, audit report (A4.a remediated), roadmap

Updates the auth-config description in CLAUDE.md to exact-match
allowlists + READER-only "*"; marks audit finding A4.a remediated
(headline 0 High; ✅ on the finding, table row, and recommendation);
moves the A4.a roadmap bullet to closed.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Push + open impl PR

- [ ] **Step 1: Push**

```bash
git push -u origin fix/a4a-exact-match-role-allowlists
```

- [ ] **Step 2: Open the PR**

```bash
gh pr create --base main --title "fix(a4a): exact-match role allowlists (audit remediation)" --body "$(cat <<'EOF'
## Summary
Remediates audit finding **A4.a (High)** — unvalidated `*_ADDRESSES` regexes let an overbroad pattern (`CC.*CC`) silently escalate every authenticated address.

- Exact-address membership replaces `re.fullmatch` in `Role.address_roles` (drops `import re`).
- New `Role.validate_config` rejects, at `create_app` startup, any non-address entry and any `"*"` outside `READER_ADDRESSES`, raising the new `InvalidRoleConfigError` (fail-hard — a malformed auth allowlist stops the node).
- `READER_ADDRESSES` may contain `"*"` (any authenticated wallet → READER); the higher tiers are strictly exact-address.
- The A4.a `xfail` is flipped to a passing regression test; positive/negative role-config coverage added in `tests/test_api.py`.

Two complementary defenses: at startup the overbroad entry is rejected; at match time it is an inert non-matching literal.

## Why
Addresses are opaque hashes — regex over them only ever meant `.*` (the foot-gun). Exact-match makes mass-escalation structurally impossible rather than heuristically detected. Pre-1.0, no deployed nodes; `*_ADDRESSES` env format is unchanged (only the semantics change).

## Out of scope
The other audit findings (A3.a/A5.b live-role re-check, A3.b iss/aud, A2.c/A7.a throttling, A1.a SECRET_KEY, A2.e oracle) are separate PRs.

## Test plan
- [x] `uv run pytest` → `263 passed, 7 xfailed, 1 skipped`.
- [x] `uv run pytest --runxfail tests/test_auth_audit.py` → `7 failed` (A4.a no longer demonstrates a gap).
- [x] New role-config tests in `tests/test_api.py` (exact match, READER `"*"`, precedence, startup rejection of non-address + out-of-READER `"*"`).
- [x] `ruff check` + `ruff format --check` + `mypy` clean.
- [ ] CI green on 3.12 and 3.13.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Fill the impl PR number into the docs** — once the PR number is known, update the `#<impl>` placeholders in the audit report (Task 4 Step 2) and roadmap (Step 3) to the real number, commit, and push:

```bash
git add docs/superpowers/audits/2026-05-31-api-authentication-audit.md docs/superpowers/ROADMAP.md
git commit -m "docs(a4a): fill impl PR number into audit report + roadmap"
git push
```

- [ ] **Step 4: Stop — controller handles internal review + one Copilot backstop + mwg.**

---

## Task 6: Acceptance (after the impl PR merges)

- [ ] **Step 1: Sync main + confirm merges**

```bash
git checkout main && git pull --ff-only
git log --oneline -3
```
Expected: top commits include the A4.a impl PR squash and (earlier) the docs PR squash.

- [ ] **Step 2: Matching + validation behavior on main**

```bash
grep -n "re.fullmatch" src/cancelchain/api.py || echo "re.fullmatch gone (good)"
grep -n "import re" src/cancelchain/api.py || echo "import re gone (good)"
grep -n "class InvalidRoleConfigError" src/cancelchain/exceptions.py
grep -n "def validate_config" src/cancelchain/api.py
grep -n "Role.validate_config" src/cancelchain/__init__.py
```
Expected: no `re.fullmatch` / `import re` in api.py; `InvalidRoleConfigError` defined; `validate_config` defined; wired into `create_app`.

- [ ] **Step 3: Suite + xfail integrity**

```bash
uv run pytest 2>&1 | tail -2                                   # 263 passed, 7 xfailed, 1 skipped
uv run pytest --runxfail tests/test_auth_audit.py -q 2>&1 | tail -2   # 7 failed
uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy
```
Expected as annotated; gates clean.

- [ ] **Step 4: Docs reflect remediation**

```bash
grep -n "0 High" docs/superpowers/audits/2026-05-31-api-authentication-audit.md
grep -niE "exact-address allowlist|exact-match" CLAUDE.md
grep -n "✅.*A4.a\|A4.a.*exact-match" docs/superpowers/ROADMAP.md
```
Expected: audit headline shows the High remediated; CLAUDE.md describes exact-match; roadmap A4.a ✅ with the impl PR number.
