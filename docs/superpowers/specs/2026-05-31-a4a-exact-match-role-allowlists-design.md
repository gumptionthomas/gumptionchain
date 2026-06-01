# A4.a — Exact-Match Role Allowlists Design

**Status:** Draft for review
**Date:** 2026-05-31
**Remediates:** Audit finding **A4.a (High)** from the [API authentication audit](../audits/2026-05-31-api-authentication-audit.md) — *operator `*_ADDRESSES` regexes are unvalidated; an overbroad pattern (e.g. `CC.*CC`) silently escalates every authenticated address to that role.*

## Problem

`Role.address_roles` (`api.py`) matches the JWT `sub` (an address) against each role's configured `*_ADDRESSES` list using `re.fullmatch`:

```python
@classmethod
def address_roles(cls, address: str) -> list[Role]:
    return [
        role
        for role in Role
        if any(re.fullmatch(x, address) for x in role.addresses())
    ]
```

`re.fullmatch` anchors the whole string, but the **patterns themselves are operator-supplied and unvalidated**. A pattern such as `CC.*CC` fullmatches *every* valid address, so configuring it for `ADMIN_ADDRESSES` silently grants ADMIN to every authenticated wallet — no key compromise, no warning from the code. Demonstrated by `tests/test_auth_audit.py::test_a4_a_overbroad_admin_regex_escalates_reader` (currently `@pytest.mark.xfail(strict=True)`).

**Why regex is the wrong tool here.** Addresses are opaque cryptographic identifiers (`CC` + base58(32 bytes) + `CC`, per `validate_address_format`). There is no legitimate "pattern" semantics over them — a regex either names one address (an exact string, since base58 contains no regex metacharacters) or matches a *family* it has no business matching (the `.*` foot-gun). The regex capability **is** exercised today, but only to express exactly that foot-gun: `tests/test_api.py::test_regex_roles` asserts that `READER_ADDRESSES=['.*']` grants READER to an arbitrary wallet, and that `['CC.*CC']` matches every CC-format address — i.e. the test codifies the over-match this remediation eliminates. Every *legitimate* `*_ADDRESSES` entry (in `tests/.test.env` and `tests/conftest.py`'s `app` fixture) is already an exact address. So dropping regex removes a capability whose only demonstrated use is the vulnerability itself; `test_regex_roles` is removed as part of this change (the "open read" intent it expressed is preserved by the new READER-only `"*"` sentinel). The chain is also pre-1.0 with no deployed nodes, so there is no migration burden.

## Goal

Make the overbroad-allowlist class of misconfiguration *structurally impossible* rather than heuristically detected: replace regex matching with exact-address membership, and validate the allowlists at startup so a malformed entry stops the node instead of silently mis-authorizing. Flip the A4.a demonstration test from `xfail` to a passing regression test.

## Approach

Two coordinated changes plus a startup gate:

1. **Exact-match membership** in `Role.address_roles` — drop `re`.
2. **Startup validation** of all four `*_ADDRESSES` lists in `create_app`, raising on any entry that is neither a valid address nor the explicit `'*'` sentinel (and the sentinel only in `READER_ADDRESSES`).
3. A deliberate **fail-hard** policy: a bad auth allowlist aborts startup.

### Components

**`Role.address_roles` (`src/cancelchain/api.py`) — matching.**

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

The `re` import is removed from this path. `'*'` is the explicit match-all sentinel meaning "any authenticated wallet." Because startup validation (below) guarantees `'*'` can appear only in `READER_ADDRESSES`, the generic `'*' in addrs` check is safe for every role — a higher-privilege list can never contain it. `address_role` (highest-role-wins via `roles[-1]`) is unchanged.

**`Role.validate_config(config)` (new classmethod on `Role`, `src/cancelchain/api.py`) — startup validation.**

```python
@classmethod
def validate_config(cls, config: Mapping[str, Any]) -> None:
    for role in cls:
        for entry in config.get(f'{role.name}_ADDRESSES', []) or []:
            if entry == '*':
                if role is not cls.READER:
                    raise InvalidRoleConfigError(
                        f'{role.name}_ADDRESSES contains "*" '
                        '(match-all is permitted only in READER_ADDRESSES)'
                    )
            elif not validate_address_format(entry):
                raise InvalidRoleConfigError(
                    f'{role.name}_ADDRESSES entry {entry!r} '
                    'is not a valid cancelchain address'
                )
```

Validation rules, per entry of each `{READER,TRANSACTOR,MILLER,ADMIN}_ADDRESSES` list:
- `'*'` → permitted **only** when `role is READER`; otherwise raise.
- any other value → must satisfy `validate_address_format` (`schema.py`); otherwise raise.

**`InvalidRoleConfigError(CCError)` (new, `src/cancelchain/exceptions.py`).** A dedicated startup/config exception under the existing `CCError` base. (A1.a's future `SECRET_KEY` startup check can add a sibling under the same category.)

**`create_app` wiring (`src/cancelchain/__init__.py`).** Call `Role.validate_config(app.config)` once, immediately after config is finalized (after the `config_map` merge, before/around `init_app`). It is **not** wrapped in the log-and-continue `try/except` that guards `db`/`cache`/`tasks` init — it must raise.

### Error handling

**Fail-hard at startup, by design.** A malformed auth allowlist is a security-relevant misconfiguration: continuing to boot would either lock out legitimate operators or over-grant a role. So `validate_config` raises `InvalidRoleConfigError` and aborts `create_app`, unlike the resource-init steps (db/cache/tasks) which log and continue. The exception message names the role and the offending entry so the operator can fix it immediately.

Matching itself is total (no exceptions): `address_roles` returns `[]` for an unknown address, and `address_role` returns `None`, which existing callers (`TokenView.post` → `abort(403)`) already handle.

## Testing

**Flip the demonstration test.** `tests/test_auth_audit.py::test_a4_a_overbroad_admin_regex_escalates_reader` drops its `@pytest.mark.xfail(strict=True)` marker and becomes a passing regression test. It keeps its structure (mutate `ADMIN_ADDRESSES=['CC.*CC']` at runtime, handshake as the reader wallet, inspect the minted `rol`) but now asserts the secure outcome: under exact-match the overbroad literal is inert, so the reader wallet resolves to READER (not ADMIN). Renamed to `test_a4_a_overbroad_admin_regex_does_not_escalate`, docstring reframed to past tense. The *startup-validation* defense — that a fresh app configured with `ADMIN_ADDRESSES=["CC.*CC"]` raises `InvalidRoleConfigError` — is covered separately by `test_create_app_rejects_overbroad_admin_config` in `tests/test_api.py` (the existing A4.a test mutates config post-startup, so it exercises the matching defense, not the startup gate).

**Remove the obsolete regex test.** `tests/test_api.py::test_regex_roles` asserts the now-removed regex-matching behavior (`READER_ADDRESSES=['.*']` granting an arbitrary wallet access) — exactly the over-match being eliminated. It is deleted; its "open read via pattern" intent is preserved by the new READER-only `"*"` sentinel (covered by `test_address_role_reader_wildcard`).

**New positive + negative coverage** (`tests/test_api.py`, where `Role`/auth tests live):
- exact address listed in a role → `Role.address_role` returns that role; an unlisted address → `None`.
- `READER_ADDRESSES=["*"]` → an arbitrary valid address resolves to `READER` (deliberate open-read works).
- `'*'` in `TRANSACTOR_ADDRESSES` / `MILLER_ADDRESSES` / `ADMIN_ADDRESSES` → `validate_config` raises `InvalidRoleConfigError` at startup.
- a non-address junk entry (`"CC.*CC"`, `"notanaddress"`) in any role list → raises.
- multi-role precedence preserved: an address in both `READER_ADDRESSES` and `MILLER_ADDRESSES` → `address_role` returns `MILLER`.

**Regression baseline.** `tests/conftest.py` and `tests/.test.env` already use exact addresses, so existing tests are unaffected. Suite moves from `256 passed, 8 xfailed, 1 skipped` to `263 passed, 7 xfailed, 1 skipped` — the obsolete `test_regex_roles` is removed (−1), the A4.a xfail flips to a pass (8→7 xfailed, +1), and 7 new role-config tests are added (256 − 1 + 7 + 1 = 263 passed). `--runxfail tests/test_auth_audit.py` then shows `7 failed` (A4.a no longer among them). All five CI gates (`ruff check`, `ruff format`, `pytest`, `mypy`, `db check`) stay green; `mypy --strict` over `src/` must accept the new classmethod and exception.

## Documentation updates

- **`CLAUDE.md`** — the API-auth section describes role keying as "regex-matched against the JWT `sub` in `api.Role.address_role`." Update to: exact-address allowlists; `READER_ADDRESSES` may contain `"*"` to grant READER to any authenticated wallet; non-address entries (and `"*"` outside READER) are rejected at startup.
- **Audit report** (`docs/superpowers/audits/2026-05-31-api-authentication-audit.md`) — mark A4.a remediated per the established convention: prefix the finding with `✅ Remediated`, rewrite the gap description in past tense, and add an `(As implemented: …)` reconciliation to the remediation sketch noting exact-match + startup validation + READER-only `"*"`. Update the severity headline (Executive summary, Findings table intro) from `0/1/5/2` to `0 Critical / 0 High / 5 Medium / 2 Low`, and reframe any prose that calls A4.a "the High finding" / open.
- **Roadmap** (`docs/superpowers/ROADMAP.md`) — move the A4.a bullet in the "Audit remediation — API authentication findings" section to ✅ closed with the impl PR number.

## Out of scope

- No database schema change.
- No change to the `CC_*_ADDRESSES` env **format** (still a JSON list of strings) — only the *semantics* change (regex → exact + `"*"` sentinel).
- The other audit findings — A3.a/A5.b (`authorize()` live-role re-check), A3.b (`iss`/`aud`), A2.c/A7.a (endpoint throttling), A1.a (`SECRET_KEY` length), A2.e (content-type oracle) — remain separate remediation PRs.
- The cross-cutting observations (`authorize_admin` unbound; the `remote_app` conftest fixture bug) are not addressed here.
- No rate limiting / per-request authorization changes.

## Acceptance criteria

- `Role.address_roles` uses exact membership + the `'*'` sentinel; `re` is no longer used for role matching.
- `Role.validate_config` rejects, at `create_app` time, any non-address entry and any `'*'` outside `READER_ADDRESSES`, raising `InvalidRoleConfigError`; `create_app` calls it and does not swallow the exception.
- `test_a4_a_*` passes as a real regression test (xfail marker removed); new positive/negative role-config tests pass.
- `ADMIN_ADDRESSES=["CC.*CC"]` can no longer grant ADMIN to any wallet — it fails the node at startup.
- All five CI gates green; suite `263 passed, 7 xfailed, 1 skipped` (256 baseline − the removed `test_regex_roles` + 7 new role-config tests + the flipped A4.a xfail).
- CLAUDE.md, the audit report (A4.a marked remediated, headline `0/0/5/2`), and the roadmap are updated.
