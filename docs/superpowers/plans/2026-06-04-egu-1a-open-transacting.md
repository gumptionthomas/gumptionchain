# EGU 1a — open transacting — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permit the `"*"` match-all sentinel in `TRANSACTOR_ADDRESSES` (mirroring `READER`) so any authenticated wallet can spend grit it holds, unblocking open EGU participation.

**Architecture:** A two-line extension of the existing READER-wildcard in `Role` (`src/gumptionchain/api.py`) — honored at match-time (`address_roles`) and accepted at startup (`validate_config`) for `READER` and `TRANSACTOR` only; `MILLER`/`ADMIN` stay exact-allowlist. Auth-config capability change: no schema, no migration, opt-in via config.

**Tech Stack:** Python 3.12, Flask, Pydantic, pytest, uv, ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-06-04-egu-1a-open-transacting-design.md` (issue #151)

---

## File map

| File | Change |
|---|---|
| `src/gumptionchain/api.py` | `Role.address_roles` + `Role.validate_config` honor/accept `"*"` for `TRANSACTOR` too |
| `tests/test_auth_audit.py` | unit + integration tests for the TRANSACTOR wildcard |
| `CLAUDE.md` | deployment note: edge rate-limit guidance + submit-PoW deferred |

---

## Task 1: Wildcard `TRANSACTOR`

**Files:**
- Modify: `src/gumptionchain/api.py` (`Role.address_roles`, `Role.validate_config`)
- Test: `tests/test_auth_audit.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_auth_audit.py`. At the top, ensure imports: `import pytest`, `from gumptionchain.api import Role`, `from gumptionchain.exceptions import InvalidRoleConfigError` (add any not already present — check the existing import block first).

```python
def test_validate_config_allows_wildcard_in_transactor():
    # Should not raise.
    Role.validate_config({'TRANSACTOR_ADDRESSES': ['*']})


def test_validate_config_allows_wildcard_in_reader():
    Role.validate_config({'READER_ADDRESSES': ['*']})


def test_validate_config_rejects_wildcard_in_miller():
    with pytest.raises(InvalidRoleConfigError):
        Role.validate_config({'MILLER_ADDRESSES': ['*']})


def test_validate_config_rejects_wildcard_in_admin():
    with pytest.raises(InvalidRoleConfigError):
        Role.validate_config({'ADMIN_ADDRESSES': ['*']})


def test_wildcard_transactor_grants_transactor_role(app):
    with app.app_context():
        app.config['TRANSACTOR_ADDRESSES'] = ['*']
        # an address in no exact allowlist resolves to TRANSACTOR via "*"
        assert Role.address_role('not-a-listed-address') is Role.TRANSACTOR


def test_no_wildcard_unlisted_is_not_transactor(app):
    with app.app_context():
        app.config['TRANSACTOR_ADDRESSES'] = []
        assert Role.address_role('not-a-listed-address') is not Role.TRANSACTOR


def test_wildcard_not_honored_for_miller_at_match_time(app):
    # Defense-in-depth: even if MILLER_ADDRESSES were mutated to contain "*"
    # at runtime, address_roles must not grant MILLER.
    with app.app_context():
        app.config['MILLER_ADDRESSES'] = ['*']
        assert Role.MILLER not in Role.address_roles('not-a-listed-address')
```

- [ ] **Step 2: Run, expect FAIL**

Run: `uv run pytest tests/test_auth_audit.py -k "wildcard or validate_config" -q`
Expected: FAIL — `test_validate_config_allows_wildcard_in_transactor` raises `InvalidRoleConfigError` (currently rejected), and `test_wildcard_transactor_grants_transactor_role` returns `None`/`READER` not `TRANSACTOR`.

- [ ] **Step 3: Honor `"*"` for TRANSACTOR at match-time**

In `src/gumptionchain/api.py`, `Role.address_roles`, change the comprehension's wildcard clause from:
```python
            and (address in addrs or (role is cls.READER and '*' in addrs))
```
to:
```python
            and (
                address in addrs
                or (role in (cls.READER, cls.TRANSACTOR) and '*' in addrs)
            )
```

- [ ] **Step 4: Accept `"*"` for TRANSACTOR at startup**

In `Role.validate_config`, change:
```python
                if entry == '*':
                    if role is not cls.READER:
                        msg = (
                            f'{role.name}_ADDRESSES contains "*" '
                            '(match-all is permitted only in '
                            'READER_ADDRESSES)'
                        )
                        raise InvalidRoleConfigError(msg)
```
to:
```python
                if entry == '*':
                    if role not in (cls.READER, cls.TRANSACTOR):
                        msg = (
                            f'{role.name}_ADDRESSES contains "*" '
                            '(match-all is permitted only in '
                            'READER_ADDRESSES or TRANSACTOR_ADDRESSES)'
                        )
                        raise InvalidRoleConfigError(msg)
```

- [ ] **Step 5: Run, expect PASS**

Run: `uv run pytest tests/test_auth_audit.py -k "wildcard or validate_config" -q`
Expected: PASS.

- [ ] **Step 6: Full suite + lint + types**

Run: `uv run pytest -q && uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy`
Expected: all green. (Existing `test_auth_audit.py` cases — forged-claim, stale-role, overbroad-admin — must still pass; the change only adds a permitted wildcard for TRANSACTOR.)

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat(auth): permit wildcard \"*\" in TRANSACTOR_ADDRESSES (open transacting)"
```

---

## Task 2: Integration test + deployment docs

**Files:**
- Test: `tests/test_auth_audit.py`
- Modify: `CLAUDE.md`

- [ ] **Step 1: Write a failing end-to-end test**

Mirror the existing harness in `tests/test_auth_audit.py` (see `test_a5_b_stale_role_rejected_after_config_revocation` for the `app.config` mutation + `requests_proxy` + `signing.sign_headers` pattern, and how it picks an endpoint + asserts status). Add a test that an arbitrary wallet — one NOT in `TRANSACTOR_ADDRESSES` by exact match — is authorized at a `authorize_transactor` endpoint when `TRANSACTOR_ADDRESSES=['*']`, and is rejected (`403`) when it is `[]`.

```python
def test_wildcard_transactor_authorizes_arbitrary_wallet(
    app, host, requests_proxy, reader_wallet, mill_block
):
    # reader_wallet is configured READER-only (not in TRANSACTOR_ADDRESSES).
    with app.app_context():
        mill_block(reader_wallet)
        # pick any authorize_transactor-gated endpoint; the rescind txn-build
        # endpoint is one. Sign a GET to it the way the other tests sign.
        path = '/api/transaction/opposition'
        # without the wildcard, an unlisted wallet is 403 at a transactor route
        app.config['TRANSACTOR_ADDRESSES'] = []
        headers = signing.sign_headers(
            reader_wallet,
            method='GET',
            path=path,
            query='',
            body=b'',
            node_host=_node(host),
        )
        denied = requests_proxy.get(path, headers=headers, timeout=60)
        assert denied.status_code == httpx.codes.FORBIDDEN

        # with the wildcard, the same wallet gets past auth (no 403); it may
        # still get a 4xx for missing query params, but NOT 403.
        app.config['TRANSACTOR_ADDRESSES'] = ['*']
        headers = signing.sign_headers(
            reader_wallet,
            method='GET',
            path=path,
            query='',
            body=b'',
            node_host=_node(host),
        )
        allowed = requests_proxy.get(path, headers=headers, timeout=60)
        assert allowed.status_code != httpx.codes.FORBIDDEN
```
Adapt imports/helpers (`signing`, `_node`, `httpx`) to whatever the file already uses — check the top of `test_auth_audit.py` and copy its exact patterns (e.g. how `_node(host)` / `signing.sign_headers` are referenced in the existing tests). If `/api/transaction/opposition` is awkward to sign as a GET in this harness, use whichever transactor-gated endpoint the existing tests already exercise.

- [ ] **Step 2: Run, expect (initially) FAIL or PASS-after-Task-1**

Run: `uv run pytest tests/test_auth_audit.py -k wildcard_transactor_authorizes -q`
Expected: PASS (Task 1 already landed the behavior). If it FAILS, the wildcard wiring from Task 1 is incomplete — fix before proceeding. (This test is end-to-end confirmation of Task 1 through the real auth decorator.)

- [ ] **Step 3: Add deployment note to `CLAUDE.md`**

In `CLAUDE.md`, under the supply-chain/deployment area (near the Dockerfile/production notes), add a short subsection:

```markdown
## Open transacting & anti-spam (EGU)

`TRANSACTOR_ADDRESSES` accepts the `"*"` match-all sentinel (like `READER`), so
any authenticated wallet may submit transactions — opt in with
`GC_TRANSACTOR_ADDRESSES='["*"]'`. This exposes *load, not theft*: balance /
ownership / double-spend validation still hold, and `MILLER`/`ADMIN` stay
exact-allowlist. Operators running with the wildcard should:
- keep the `MAX_PENDING_TXNS` cap (full pool → `503`, graceful), and
- put a **per-IP rate limit at the reverse proxy** in front of the node.

A heavier defense — a hashcash-style **submit-PoW** verified before
signature/validation work — is specced (issue #151) as the escalation if real
flooding appears; it is intentionally **not** built yet.
```

- [ ] **Step 4: Cheap-reject ordering check (verify; reorder only if needed)**

Read the transaction-submission path (`Node.receive_transaction` in `node.py` and the `/api/transaction` POST view in `api.py`). Confirm cheap structural validation (`Transaction.validate()` — Pydantic model + signature + txid) runs **before** any expensive chain resolution (`get_transaction` / inflow walks / balance checks). If it already does (likely — `validate()` is structural and runs first), make **no code change** and note the finding in the commit message. Only if expensive chain work currently precedes cheap structural rejection, reorder so the cheap check is first. Do not add new mechanisms.

- [ ] **Step 5: Full suite + lint + types**

Run: `uv run pytest -q && uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "docs+test(auth): open-transacting deployment note + e2e wildcard test"
```

---

## Self-review notes

- Spec coverage: wildcard `TRANSACTOR` (Task 1) + safety/posture (the change is additive; `MILLER`/`ADMIN` unaffected) + anti-spam docs/cheap-reject (Task 2). Submit-PoW + 1b retune are explicitly out of scope per the spec.
- The match-time and startup edits both gate on `(cls.READER, cls.TRANSACTOR)` — kept identical so the two layers can't drift.
- `validate_config` takes a plain config `Mapping`, so its tests need no app context; `address_roles`/`address_role` read `current_app.config`, so those tests use `app.app_context()`.
- No schema/migration; `db check` is unaffected (not run in this plan).

## Definition of done

- `"*"` permitted in `TRANSACTOR_ADDRESSES` (match-time + startup), `MILLER`/`ADMIN` still reject it.
- Any authenticated wallet resolves to `TRANSACTOR` (and, by hierarchy, READER) when `TRANSACTOR_ADDRESSES=['*']`; exact-match behavior unchanged without `"*"`.
- Deployment anti-spam note in `CLAUDE.md`; submit-PoW documented as deferred.
- Full suite + ruff + ruff-format + mypy green. No schema/migration.
