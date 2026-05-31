# API authentication threat-modeled audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute the threat-modeled audit specified in `docs/superpowers/specs/2026-05-31-api-auth-audit-design.md` — produce a findings report at `docs/superpowers/audits/2026-05-31-api-authentication-audit.md` and a `tests/test_auth_audit.py` module with one `@pytest.mark.xfail(strict=True)` test per confirmed gap, across all 7 auth-adversary categories, plus a Recommendations section that resolves the targeted-fixes-vs-protocol-replacement question.

**Architecture:** Single impl PR (report + tests), then a small follow-up docs PR for the roadmap update. The audit is an exploratory exercise — per-category tasks trace attacks through the existing auth code, document the trace (positive or negative), and write demonstration tests for any gaps. Per-category tasks are largely independent, with three cross-reference edges to respect in ordering: Task 9 (A7.b) and Task 7 (A5.a) cross-reference Task 4's A2.c/A2.d, and Task 5 (A3.d) cross-references Task 3's A1.b. So run **Task 3 and Task 4 before Tasks 5, 7, and 9** — otherwise those later tasks would cite a finding ID that doesn't exist yet (write a forward reference, or duplicate a test, both of which the plan tells them not to do). Tasks 6 and 8 are free-floating. The synthesis task (Findings table, Clean categories, Cross-cutting observations, Recommendations, Executive summary) comes last, after all per-category findings exist.

**Tech Stack:** Python 3.12 + pytest (existing). `@pytest.mark.xfail(strict=True)` is the load-bearing marker — when remediation lands and a test starts passing, strict mode triggers a CI failure forcing the marker's removal. Auth tests drive the Flask app via the existing `requests_proxy` fixture (httpx `WSGITransport`) and `ApiClient`. The companion design spec is `docs/superpowers/specs/2026-05-31-api-auth-audit-design.md`.

---

## Prerequisites

- Working directory: the cancelchain repo root. Run all commands from there.
- `uv --version` 0.4.x or newer; `gh --version` works and `gh auth status` shows authenticated.
- The verification audit and all six of its remediations are merged (audit fully closed 0/0/0/0). Verify with `git log --oneline -1 main` showing `c087cf6` or later.
- The docs PR (spec + this plan) has already shipped: branch `docs/api-auth-audit-design`, opened as **PR #101**. Task 1 below is therefore already executed — it is retained for the record but its steps describe work the controller has done.
- CI hard-gates (per `.github/workflows/tests.yml`): `ruff check`, `ruff format --check`, `pytest`, `mypy`, and `cancelchain db upgrade` + `cancelchain db check`.
- Test baseline: **256 passed, 1 skipped**. After the audit, expect `256 passed, N xfailed, 1 skipped` where N is the number of confirmed gaps (estimated 3-8 based on the spec).
- **Review loop (per `feedback_internal_review_then_one_copilot`):** before opening each PR, run an internal cross-model adversarial review (reviewer on a *different* model than the author — author is Opus, so reviewer = Sonnet) to convergence, relay findings, fix. **Then** open the PR and do exactly **one** Copilot backstop pass. Copilot does **not** auto re-review on this repo (`project_copilot_auto_rereview`) — if a fix round is needed, the controller triggers it with `gh pr comment <N> --body "/copilot review"`. `wor` + `mwg` are controller work, not the implementer subagent's.
- Never push directly to `main`.

---

## File Map

| Task | PR | Files |
|---|---|---|
| 1 | docs PR | `docs/superpowers/plans/2026-05-31-api-auth-audit.md` (this file) + spec already on branch |
| 2 | impl PR | NEW `docs/superpowers/audits/2026-05-31-api-authentication-audit.md`, NEW `tests/test_auth_audit.py` |
| 3-9 | impl PR | populate audit doc per-category sections + append xfail tests |
| 10 | impl PR | synthesis (findings table, clean categories, cross-cutting, recommendations, exec summary) |
| 11 | impl PR | push + open PR |
| 12 | roadmap PR | `docs/superpowers/ROADMAP.md` — close audit entry, open remediation items |
| 13 | acceptance | none (verification only) |

The impl PR creates exactly two new files and modifies none. The `docs/superpowers/audits/` directory already exists (created by the verification audit).

---

## Task 1: Ship the docs PR (spec + plan) — ALREADY DONE

**Status:** Complete. The controller committed the spec (`778e811`) and this plan (`accfcf4`) on `docs/api-auth-audit-design`, opened **PR #101**, ran the internal cross-model review (this very review loop) + one Copilot backstop, applied the resulting fixes, and is merging via `mwg`. No implementer subagent action is required for Task 1. The steps below are retained only as a record of what shipping the docs PR entailed:

1. Commit spec + plan on `docs/api-auth-audit-design`.
2. Push and `gh pr create --base main` (became #101).
3. Internal cross-model adversarial review → fix → converge.
4. One Copilot backstop pass → fix → `mwg`.

Implementer subagents begin at **Task 2** (which branches off `main` after #101 merges).

---

## Task 2: Audit infrastructure bootstrap (impl PR)

**Files:**
- Create: `docs/superpowers/audits/2026-05-31-api-authentication-audit.md` (skeleton with all section headers + placeholders)
- Create: `tests/test_auth_audit.py` (module docstring only)

This task creates the structure that the per-category tasks populate. After this task, the audit doc has the right sections (some empty), and the test module exists and runs (zero tests, no impact on pytest output yet).

### Step 1: Branch off main + baseline gates

```bash
git checkout main && git pull --ff-only
git checkout -b feat/api-auth-audit
git log --oneline -1
```

Expected: top commit is the merged auth-audit docs PR (or later).

Confirm baseline gates are green BEFORE any edit:

```bash
uv run mypy
uv run ruff check src tests
uv run pytest 2>&1 | tail -3
```

Expected: mypy clean; ruff clean; pytest `256 passed, 1 skipped`.

### Step 2: Create the audit doc skeleton

Create `docs/superpowers/audits/2026-05-31-api-authentication-audit.md` with this content:

````markdown
# Cancelchain API authentication threat-modeled audit

**Date:** 2026-05-31
**Methodology spec:** `docs/superpowers/specs/2026-05-31-api-auth-audit-design.md`
**Demonstration tests:** `tests/test_auth_audit.py`

## Preconditions

- **TLS assumed.** HTTPS is an explicit deployment precondition. On-wire interception/replay of the bearer JWT or the decrypted challenge is out of scope as a transport concern.
- **Verification pipeline assumed sound** (audited separately, #84). This audit examines only the gate in front of it.
- **No browser auth exists** (`browser.py` has no sessions/login); nothing to audit there.

## Executive summary

[Placeholder — filled in by Task 10 after all per-category tasks complete.]

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

[Placeholder — built by Task 10.]

| ID | Category | Severity | Description | Remediation sketch | Test |
|---|---|---|---|---|---|

(`Category` = the adversary number 1–7 the finding came from, per the spec's findings-table schema — it's the cross-link back to the Per-adversary-traces section. `Remediation sketch` is a plan convenience beyond the spec's minimum columns.)

## Per-adversary traces

### Adversary 1: Anonymous outsider

[Placeholder — filled in by Task 3.]

### Adversary 2: Challenge attacker

[Placeholder — filled in by Task 4.]

### Adversary 3: Token forger / cryptanalyst

[Placeholder — filled in by Task 5.]

### Adversary 4: Role-escalation attacker

[Placeholder — filled in by Task 6.]

### Adversary 5: Replay attacker

[Placeholder — filled in by Task 7.]

### Adversary 6: Authorized insider

[Placeholder — filled in by Task 8.]

### Adversary 7: Resource / DoS attacker

[Placeholder — filled in by Task 9.]

## Clean categories

[Placeholder — filled in by Task 10. Explicit "no findings" results per category, with the rationale (what was checked, why it's sound). Negative evidence is a deliverable.]

## Cross-cutting observations

[Placeholder — filled in by Task 10. Patterns spanning categories: SECRET_KEY coupling, argon2-on-high-entropy-secret, claim hygiene, etc.]

## Recommendations

[Placeholder — filled in by Task 10. Prioritized remediation ordering AND the targeted-fixes-vs-protocol-replacement analysis, with the two named candidate directions (signed-nonce via Wallet.sign; RFC 9421 / RS256 client-assertion) each with a trade-off paragraph.]
````

Verify:

```bash
ls -la docs/superpowers/audits/
grep -c '^## ' docs/superpowers/audits/2026-05-31-api-authentication-audit.md
grep -c '^### Adversary' docs/superpowers/audits/2026-05-31-api-authentication-audit.md
```

Expected: directory exists with both audit files; `^## ` matches 9 (Preconditions, Executive summary, Threat model, Methodology, Findings table, Per-adversary traces, Clean categories, Cross-cutting observations, Recommendations — the single `# ` title line is not counted); `^### Adversary` matches 7.

### Step 3: Create the test module skeleton

Create `tests/test_auth_audit.py`:

```python
"""Demonstration tests for the API authentication threat-modeled audit.

Each test in this module corresponds to one finding in
docs/superpowers/audits/2026-05-31-api-authentication-audit.md
and is marked @pytest.mark.xfail(strict=True). The xfail demonstrates that
the documented gap exists today; strict=True means that if the test starts
unexpectedly passing (because remediation has been applied), CI fails,
forcing the remediation PR to remove the marker.

To verify each xfail genuinely demonstrates a gap (rather than failing for
an unrelated reason), run:

    uv run pytest --runxfail tests/test_auth_audit.py

That runs the xfail tests as if they were unmarked, surfacing the actual
failure mode.

Finding IDs are referenced in each test's docstring and xfail reason string
in the form A<N>.<letter> matching the audit document's per-adversary
sections.
"""
```

The module starts empty (just the docstring). Per-category tasks append tests.

Verify:

```bash
ls tests/test_auth_audit.py
uv run pytest tests/test_auth_audit.py 2>&1 | tail -3
```

Expected: file exists; pytest reports `no tests ran` (empty module).

### Step 4: Verify the existing test suite still passes

```bash
uv run pytest 2>&1 | tail -3
```

Expected: `256 passed, 1 skipped` (unchanged — the empty new module adds zero tests).

### Step 5: Verify other gates

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy
```

All three exit 0. The new test module has only a docstring.

### Step 6: Commit

```bash
git add docs/superpowers/audits/2026-05-31-api-authentication-audit.md tests/test_auth_audit.py
git commit -m "$(cat <<'EOF'
audit(infra): bootstrap auth audit doc + test module skeletons

Creates docs/superpowers/audits/2026-05-31-api-authentication-audit.md
with all section headers + placeholders for per-category content, and
tests/test_auth_audit.py with the module docstring explaining the
xfail(strict=True) pattern.

Subsequent tasks populate per-category sections (Tasks 3-9) and
synthesize the findings table + clean categories + cross-cutting +
recommendations + executive summary (Task 10).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Per-category tasks (Tasks 3-9) — shared structure

All 7 per-category tasks follow the same shape. Each task:

1. Reads the relevant source files for that category's attacks (paths listed per task).
2. For each attack attempt enumerated in the spec for that category:
   - Construct the trace (read each function in the call path; document what it checks, citing `file.py:line`).
   - Determine: does the attack succeed (gap) or get correctly rejected?
   - If rejected: document the trace in the audit doc under that category's section, citing the function that rejects. This feeds the "Clean categories" section in Task 10.
   - If gap: write a finding entry (ID, severity, remediation sketch) AND a demonstration test in `tests/test_auth_audit.py`.
3. Run the test suite to verify the new tests behave correctly (xfail tests show XFAIL; others continue to pass).
4. Commit.

**Severity rubric** (from spec, re-anchored for auth):

| Severity | Definition |
|---|---|
| Critical | Auth/authz existential. Obtain a role you don't hold, act as an address whose key you don't possess, or forge a token the server accepts. |
| High | Significant auth-integrity violation, bounded blast radius. A misconfiguration foot-gun the code invites and doesn't guard, or a single-use/replay gap exploitable only under a narrow race. |
| Medium | Edge case that misbehaves but grants no unearned access. Unhandled input that 500s instead of 401s (info exposure / robustness), or state amplification needing unrealistic volume. |
| Low | Cosmetic / documentation / theoretical. Claim-hygiene gaps (iat/iss/aud absent) with no demonstrated exploit under the TLS assumption, or design-smell observations. |

**Audit doc per-category section template** (paste for each category; fill per-attack):

````markdown
### Adversary N: <Name>

**Capabilities:** <verbatim from spec>

#### Attack a: <one-line description>

**Pre-state:** <config/wallets/chain/token-row state needed for the attack to be meaningful>

**Attack:** <concrete steps — request, token contents, exact input>

**Trace:**
1. `<file.py:line>` — `<function>` checks `<what>`. <outcome: continues / aborts / raises>
2. `<file.py:line>` — `<function>` checks `<what>`. <outcome>
3. ...

**Outcome:** REJECTED at step `<N>` via `<abort(NNN) / exception>` — OR — ACCEPTED (no rejection; gap exists).

[If REJECTED:]
**Result:** Correctly rejected. No finding. <one line on the defense that catches it>

[If ACCEPTED — gap:]
**Finding A<N>.a — Severity <S>:** <one-line description of the gap>.
**Impact:** <what an attacker actually achieves if this gap is exploited, and the bounds of the breach — this is the severity-vs-risk argument that feeds Recommendations; required per the spec's per-finding detail>.
**Remediation sketch:** <one sentence pointing at where the fix goes — file, function, what to add>.
**Demonstration test:** `test_a<N>_<letter>_<short_name>` in `tests/test_auth_audit.py`.

#### Attack b: ...

[repeat for each attack]
````

**Test module entry template** (each finding appends one of these to `tests/test_auth_audit.py`):

```python
@pytest.mark.xfail(
    reason=(
        'Audit finding A<N>.<letter> — severity <S> — <description>. '
        'See docs/superpowers/audits/2026-05-31-api-authentication-audit.md'
    ),
    strict=True,
)
def test_a<N>_<letter>_<short_name>(<fixtures>) -> None:
    """A<N>.<letter>: <one-line description>.

    Pre-state: <setup summary>.
    Attack: <action summary>.
    Expected after remediation: <secure behavior — e.g. response is 401/403,
        or the call raises X>.
    Observed today: <insecure behavior the xfail captures>.
    """
    # Set up the pre-state.
    ...
    # Attempt the attack and assert the SECURE behavior (xfails today).
    ...
```

**Imports** (add to `tests/test_auth_audit.py` as each test needs them — let ruff sort them):

```python
import httpx
import jwt
import pytest

from cancelchain.api import API_TOKEN_SECONDS, Role
from cancelchain.api_client import ApiClient
from cancelchain.models import ApiToken
from cancelchain.wallet import Wallet
# ...add more as tests reference them
```

**Available test fixtures** (from `tests/conftest.py`, already set up):

- `app` — Flask app with temporary SQLite DB; wallets pre-loaded into `app.wallets`; `*_ADDRESSES` config wired from `tests/.test.env`.
- `requests_proxy` — httpx client routed into the Flask app via `WSGITransport`; use it to make raw HTTP calls (`requests_proxy.get/post(path, ...)`). This is how `tests/test_api.py` exercises the token endpoints.
- `remote_requests_proxy` — same, for a second (peer) app instance.
- `host`, `remote_host`, `host_netloc` — base URLs / netlocs for `ApiClient`.
- `wallet` — the **ADMIN-role** wallet (`Wallet(b58ks=WALLET_PRIVATE_KEY_B58)`; its address is placed in `ADMIN_ADDRESSES`, and it is also the `NODE_HOST` address with its PEM written to `walletdir`). **Not** the transactor wallet — do not use it where you need a bounded, non-admin identity (matters for adversary 4 role-escalation and adversary 6 insider tests, where the exact privilege level is the point).
- `reader_wallet`, `transactor_wallet`, `miller_wallet`, `miller_2_wallet` — the per-role wallets, each pre-loaded into the corresponding `READER/TRANSACTOR/MILLER_ADDRESSES` config list.
- `mill_block(milling_wallet)` — adds a milled block to the current longest chain; returns `(miller, block)`.
- `add_chain_block(...)`, `time_stepper(start=..., delta=...)` — chain construction + deterministic time.

Use these rather than reimplementing setup. Reference patterns: `tests/test_api.py` (`test_post_token_none`, `test_post_token_invalid`, `test_no_role`, `test_roles`) and `tests/test_api_client.py`.

**When in doubt, write the trace pessimistically.** If the trace doesn't show clear rejection but you're unsure whether real-world behavior catches the attack, write the demonstration test and let it tell you. `uv run pytest --runxfail tests/test_auth_audit.py::test_a<N>_<letter>` runs the test in non-xfail mode — if it passes, the auth layer actually catches the attack and the finding is a false positive (remove the finding from the audit doc, move the trace to "correctly rejected" / Clean categories, delete the test). If it fails, the gap is real.

**Known existing coverage + verified Flask 3.x behavior for attack 2e:** `tests/test_api.py::test_post_token_invalid` covers two cases — a body of `'foo'` *with* `Content-Type: application/json` (Flask returns `400` on the JSON parse error) and a well-formed `{"challenge": "foo"}` (returns `401`). It does **not** exercise the attack-2e scenario: a `POST` with a *missing or non-JSON* `Content-Type`. Verified empirically on the installed stack (Flask 3.1.3): a non-JSON content-type makes `request.json` raise `werkzeug.exceptions.UnsupportedMediaType` → **415** *at the property access*, before `TokenView.post` reaches `request.json.get('challenge')`. So the `request.json is None → AttributeError → 500` mechanism does **not** occur. Attack 2e is therefore not a 500/bypass; the open question for the trace is whether a bare 415 is the appropriate rejection for an unauthenticated handshake endpoint or whether 400/401 would be more correct (a robustness/consistency observation). Still write a demonstration test that exercises the missing/wrong-content-type `POST` and asserts the behavior the audit concludes is correct, rather than assuming.

---

## Task 3: Adversary 1 — Anonymous outsider

**Adversary description (verbatim from spec):**

> **Capabilities:** No wallet, no key, no role. Can send arbitrary HTTP to any endpoint. Can read the public chain (and therefore recover the public key of any address that has ever transacted).

**Attacks to trace (4):**

- **a.** Reach a `@authorize_*`-protected endpoint with no token / a malformed `Authorization` header and have it admitted.
- **b.** Forge a JWT the server will accept — `alg=none`, algorithm confusion (RS256-signed token verified as HS256 using a known public key as the HMAC secret), or `SECRET_KEY` guessing/weakness.
- **c.** Exploit the `authorize()` exception funnel — does any decode path fall through to `authorized = True`, or does a non-JWT exception leak a 500 with detail rather than a clean 401?
- **d.** Submit a JWT with the right shape but a `rol` value that isn't a valid `Role` name, or a `sub` that's empty/None, and observe the failure mode.

**Files to read:**
- `src/cancelchain/api.py` — `authorize()` (lines ~236-277), the `authorize_*` aliases, `Role`.
- `src/cancelchain/api_client.py` — how a legitimate `Authorization` header is shaped (`auth_header`).

- [ ] **Step 1: Read the auth surface for attacks a-d**

Trace each attack through `authorize()` (header parse → `jwt.decode` with `algorithms=['HS256']` → `sub`/`rol` extraction → role-ladder check → `abort(401)` paths). For (b), note that `algorithms=['HS256']` is explicitly pinned and check there is no second decode path; construct an `alg=none` token and a RS256-confusion token mentally and confirm both raise inside `jwt.decode`. For (d), check `Role[data['rol']]` behavior on an unknown key (raises `KeyError` → caught → `abort(401)`?) and `address = data['sub']` when `sub` is empty/None (`if address and ...` guards it).

- [ ] **Step 2: Populate Adversary 1's section in the audit doc**

Use the per-category section template. Document each attack's trace and outcome. For correctly-rejected attacks, cite the exact line that aborts.

- [ ] **Step 3: For each gap found, add a demonstration test to `tests/test_auth_audit.py`**

Use the test entry template. Likely no-findings here (the decode path is pinned and fails closed), but write any gap pessimistically and verify with `--runxfail`.

- [ ] **Step 4: Run pytest, verify xfails show up correctly**

```bash
uv run pytest tests/test_auth_audit.py 2>&1 | tail -5
```

Expected: any new tests report XFAIL (not FAIL/ERROR).

- [ ] **Step 5: Verify the existing test suite still passes**

```bash
uv run pytest 2>&1 | tail -3
```

Expected: `256 passed, <running-total> xfailed, 1 skipped`.

- [ ] **Step 6: Verify gates**

```bash
uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy
```

All exit 0.

- [ ] **Step 7: Commit**

```bash
git add docs/superpowers/audits/2026-05-31-api-authentication-audit.md tests/test_auth_audit.py
git commit -m "$(cat <<'EOF'
audit(a1): anonymous outsider traces + any demonstration tests

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 4: Adversary 2 — Challenge attacker

**Adversary description (verbatim from spec):**

> **Capabilities:** Can call `GET`/`POST /api/token/<address>` for any address, including addresses whose private key it does not hold. Can read responses.

**Attacks to trace (5):**

- **a.** Complete the handshake for an address without holding its private key (decrypt-bypass: any path where `ApiToken.verify()` returns true without a correctly decrypted secret? — `None`/empty challenge, type confusion in `verify(secret: object)`).
- **b.** Exploit the 60-second cipher-reuse window — `refreshed_cipher()` returns the same cipher until `expired`; is the secret single-use (does `reset()` reliably fire before a second redemption)?
- **c.** Force-create `ApiToken` rows for arbitrary on-chain addresses (public key recoverable from chain) — unbounded table growth from an unauthenticated endpoint.
- **d.** Race two concurrent `GET`s or `GET`/`POST` interleavings against the `unique` constraints on `cipher`/`hashed`.
- **e.** Send `POST` with no JSON body / wrong content-type. (Verified: Flask 3.x raises `UnsupportedMediaType` → **415** at the `request.json` access, *before* `request.json.get('challenge')` — it does not return `None`, so there is no `AttributeError`/500. The question is whether a bare 415 is the right rejection or whether 400/401 fits better — robustness/consistency, not bypass. Trace `TokenView.post` and write a fresh test asserting the correct behavior; the existing `test_post_token_invalid` does not cover this path.)

**Files to read:**
- `src/cancelchain/api.py` — `TokenView.get` / `TokenView.post` (lines ~189-227).
- `src/cancelchain/models.py` — `ApiToken` (`refreshed_cipher`, `verify`, `reset`, `expired`, `get`, `create`; lines ~969-1034) and `_PASSWORD_HASHER`.
- `src/cancelchain/wallet.py` — `encrypt`/`decrypt`.

- [ ] **Step 1: Read the handshake surface for attacks a-e**

Trace `GET` (wallet/public-key resolution → `ApiToken.create`/`get` → `refreshed_cipher`) and `POST` (`get` → `verify(challenge)` → `reset` → role → JWT). For (a), examine `verify(secret: object)`: it returns `False` unless `isinstance(secret, str)` and argon2 verifies — confirm `None`/non-str/empty all fail closed. For (b), confirm `reset()` clears `hashed` so a second `POST` with the same secret fails `verify`. For (e), trace the wrong/missing-content-type path: Flask 3.x raises `UnsupportedMediaType` → **415** at the `request.json` access (verified — not a `None`/500 path; see the Known-coverage note above). Determine whether 415 is the appropriate rejection or whether 400/401 fits better, and write a demonstration test asserting that conclusion — do not presuppose the outcome.

- [ ] **Step 2: Populate Adversary 2's section in the audit doc**

Use the section template. (c) is the likeliest finding here — unauthenticated row creation for any on-chain address (Medium, state amplification). Note it with the right severity.

- [ ] **Step 3: For each gap found, add a demonstration test**

Use the test entry template. For (c), the test asserts the secure behavior (e.g. a cap, or that creation requires the caller to prove key possession first) — flag in the finding that the "secure behavior" may be design-dependent and the full fix could ride the redesign.

- [ ] **Step 4: Run pytest, verify xfails show up correctly**

```bash
uv run pytest tests/test_auth_audit.py 2>&1 | tail -5
```

- [ ] **Step 5: Verify the existing test suite still passes**

```bash
uv run pytest 2>&1 | tail -3
```

- [ ] **Step 6: Verify gates**

```bash
uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy
```

- [ ] **Step 7: Commit**

```bash
git add docs/superpowers/audits/2026-05-31-api-authentication-audit.md tests/test_auth_audit.py
git commit -m "$(cat <<'EOF'
audit(a2): challenge attacker traces + any demonstration tests

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 5: Adversary 3 — Token forger / cryptanalyst

**Adversary description (verbatim from spec):**

> **Capabilities:** Targets the JWT and its signing key directly. Knows the algorithm (HS256) and the claim set. May know or guess properties of `SECRET_KEY`.

**Attacks to trace (4):**

- **a.** `SECRET_KEY` reuse blast radius — the same symmetric key signs the JWT and would sign Flask sessions/CSRF if those ever exist. Document the coupling and what a leak compromises.
- **b.** Missing claims — no `iat`, `nbf`, `iss`, `aud`. Can a token minted for node A be replayed against node B that shares `SECRET_KEY` (peer mesh)? Is the lack of `aud`/`iss` a cross-node confusion vector?
- **c.** `exp` handling — `exp` is a float `timestamp()`; confirm PyJWT validates it, check clock-skew / no-`leeway` behavior, and that there's no path accepting an expired-but-well-formed token.
- **d.** Algorithm pinning — `decode(..., algorithms=['HS256'])` is explicitly pinned (good); confirm no second decode path, and that `alg=none` and RS256-confusion both fail closed (overlaps A1.b — cross-reference rather than duplicate the test).

**Files to read:**
- `src/cancelchain/api.py` — `TokenView.post` JWT encode (lines ~217-226), `authorize()` decode (lines ~254-258).
- `src/cancelchain/__init__.py` — how `SECRET_KEY` is configured (`from_prefixed_env`).
- `src/cancelchain/config.py` — `*_ADDRESSES` / peer config (for the cross-node question in b).

- [ ] **Step 1: Read the JWT surface for attacks a-d**

For (a)/(b), this is mostly documentation-class (Low) unless the peer mesh genuinely shares `SECRET_KEY` and lacks `aud`/`iss` — trace whether peers share a secret (they each have their own `SECRET_KEY` from env; if so, cross-node replay is not possible and (b) is Low/no-finding). For (c), construct a token with `exp` in the past and confirm `jwt.decode` raises `ExpiredSignatureError` → `abort(401)`.

- [ ] **Step 2: Populate Adversary 3's section in the audit doc**

Document the coupling and claim-hygiene observations even where they're Low/no-finding — they feed Cross-cutting observations and Recommendations.

- [ ] **Step 3: For each gap found, add a demonstration test**

Per the spec's Risks section, **write an xfail test for every confirmed gap, including claim-hygiene ones** (e.g. assert the issued JWT contains `iat`/`iss`/`aud`) — these are demonstrable as claim-*presence* assertions even though they have no exploit under TLS. Annotate any test whose full fix depends on the redesign by saying so in the xfail `reason` string (e.g. "full fix may land via protocol replacement — see Recommendations"). The only things that become Cross-cutting **observations** (no `**Finding`, no test) are pure design-smells with no assertable secure behavior (e.g. the `SECRET_KEY` coupling, argon2-on-high-entropy-secret). This keeps the finding-count == test-count invariant (Task 13 Step 4) honest: every `**Finding A...` has exactly one test.

- [ ] **Step 4: Run pytest, verify xfails show up correctly**

```bash
uv run pytest tests/test_auth_audit.py 2>&1 | tail -5
```

- [ ] **Step 5: Verify the existing test suite still passes**

```bash
uv run pytest 2>&1 | tail -3
```

- [ ] **Step 6: Verify gates**

```bash
uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy
```

- [ ] **Step 7: Commit**

```bash
git add docs/superpowers/audits/2026-05-31-api-authentication-audit.md tests/test_auth_audit.py
git commit -m "$(cat <<'EOF'
audit(a3): token forger / cryptanalyst traces + any demonstration tests

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 6: Adversary 4 — Role-escalation attacker

**Adversary description (verbatim from spec):**

> **Capabilities:** Legitimately holds a key for some address with a low role (e.g. READER), wants a higher role (TRANSACTOR/MILLER/ADMIN).

**Attacks to trace (4):**

- **a.** Regex over-match / escape — `Role.addresses()` returns operator-configured regexes matched with `re.fullmatch`. Can a legitimately-controlled address also match a broader role's regex? Document the foot-gun class and whether the code constrains regexes at all.
- **b.** `sub` crafting — the `<address:address>` URL converter feeds `sub`. Can a `sub` be shaped to satisfy a broader role regex while still corresponding to a key the attacker controls (so the challenge is decryptable)? Trace the address converter's validation.
- **c.** Multi-role precedence — `address_role` returns `roles[-1]` (highest by enum order). Confirm enum order equals privilege order and `roles[-1]` can't return a lower role when matches are non-contiguous.
- **d.** `rol` claim integrity — the `rol` string is trusted on decode; confirm it's only ever set server-side from the verified role and can't be influenced by the client beyond what the signature protects.

**Files to read:**
- `src/cancelchain/api.py` — `Role.addresses`, `Role.address_roles`, `Role.address_role` (lines ~166-186); `TokenView.post` role resolution + JWT `rol` set.
- `src/cancelchain/application.py` — `AddressConverter` (the `<address:address>` route converter, registered at `app.url_map.converters['address']`); its `to_python` rejects non-address path segments via `validate_address_format`.
- `src/cancelchain/schema.py` — `validate_address_format` / `AddressType` (the helper the converter calls; how an address string is validated).
- `src/cancelchain/config.py` — `*_ADDRESSES` loading (are values JSON lists of regexes; is there any anchoring/validation?).
- `tests/.test.env` — the actual configured regexes used in tests (informs what a realistic match looks like).

- [ ] **Step 1: Read the role-mapping surface for attacks a-d**

For (a), the key question: does the code anchor/validate operator regexes, or does it trust them verbatim? `re.fullmatch` anchors the whole string, which mitigates partial-match escapes — confirm and document. For (c), `address_role` returns `roles[-1]` where `roles` is built by iterating `Role` (enum order READER=1..ADMIN=4); `roles[-1]` is the last *in enum order*, i.e. highest — confirm the list-comprehension preserves enum order so `[-1]` is genuinely the max. For (b), trace whether the address converter constrains the path to the `CC...CC` address shape (so `sub` can't be an arbitrary regex-matching string that also has a decryptable key).

- [ ] **Step 2: Populate Adversary 4's section in the audit doc**

This is the likeliest category for a High finding (the regex foot-gun). If the code trusts unanchored operator regexes, that's a High config-foot-gun; if `re.fullmatch` + a validated address shape closes it, document the defense and call it clean.

- [ ] **Step 3: For each gap found, add a demonstration test**

For a regex foot-gun finding, the test configures an over-broad `*_ADDRESSES` regex and asserts the secure behavior (e.g. the loader rejects an unanchored/over-broad pattern, or escalation is impossible). Use `monkeypatch`/`app.config` to set the hostile regex within the test.

- [ ] **Step 4: Run pytest, verify xfails show up correctly**

```bash
uv run pytest tests/test_auth_audit.py 2>&1 | tail -5
```

- [ ] **Step 5: Verify the existing test suite still passes**

```bash
uv run pytest 2>&1 | tail -3
```

- [ ] **Step 6: Verify gates**

```bash
uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy
```

- [ ] **Step 7: Commit**

```bash
git add docs/superpowers/audits/2026-05-31-api-authentication-audit.md tests/test_auth_audit.py
git commit -m "$(cat <<'EOF'
audit(a4): role-escalation traces + any demonstration tests

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 7: Adversary 5 — Replay attacker

**Adversary description (verbatim from spec):**

> **Capabilities:** Has captured a valid artifact — a redeemed-but-still-valid JWT, or a challenge cipher/secret (within the TLS assumption: capture via a compromised client, logs, or a shared-host side channel, not on-wire).

**Attacks to trace (3):**

- **a.** Challenge single-use — after a successful `POST`, `reset()` clears the row. Confirm a second `POST` with the same secret fails, and that there's no window between `verify()` and `reset()` exploitable under concurrency (overlaps A2.d).
- **b.** JWT reuse within the 4h window — bounded and expected under the bearer model; document it and whether the window is appropriate. Confirm no server-side revocation is claimed-but-absent.
- **c.** Expired-token edges — token exactly at `exp`; token with a far-future `exp` the client supplied (can't — `exp` is server-set and signed; confirm).

**Files to read:**
- `src/cancelchain/api.py` — `TokenView.post` (`verify` → `reset` → JWT), `authorize()` `exp` handling.
- `src/cancelchain/models.py` — `ApiToken.verify` / `reset` / `expired`.
- `src/cancelchain/api_client.py` — `get`/`post` 401-retry loop (does a retry re-handshake cleanly?).

- [ ] **Step 1: Read the replay surface for attacks a-c**

For (a), confirm `reset()` is called unconditionally after a successful `verify()` in `TokenView.post`, and that a replayed secret then fails because `hashed` is `None`. For (b), this is a documentation/Low item (bearer tokens are replayable for their lifetime by design; the audit notes the 4h window and absence of revocation as an accepted trade-off under TLS).

- [ ] **Step 2: Populate Adversary 5's section in the audit doc**

- [ ] **Step 3: For each gap found, add a demonstration test**

- [ ] **Step 4: Run pytest, verify xfails show up correctly**

```bash
uv run pytest tests/test_auth_audit.py 2>&1 | tail -5
```

- [ ] **Step 5: Verify the existing test suite still passes**

```bash
uv run pytest 2>&1 | tail -3
```

- [ ] **Step 6: Verify gates**

```bash
uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy
```

- [ ] **Step 7: Commit**

```bash
git add docs/superpowers/audits/2026-05-31-api-authentication-audit.md tests/test_auth_audit.py
git commit -m "$(cat <<'EOF'
audit(a5): replay attacker traces + any demonstration tests

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 8: Adversary 6 — Authorized insider

**Adversary description (verbatim from spec):**

> **Capabilities:** Legitimately holds a key and a role. Acts within the system but tries to exceed their grant or affect other addresses.

**Attacks to trace (3):**

- **a.** Cross-address token request — request a challenge for an address whose key they don't hold; confirm they can't redeem it (can't decrypt), and that merely creating the row has no privilege effect.
- **b.** Operate on another address's behalf at a protected endpoint — confirm `_address`/`_role` injected by `authorize()` actually scope the downstream action and aren't overridable by request params.
- **c.** Role downgrade/confusion — a MILLER hitting a READER-gated endpoint (allowed by the ladder) — confirm the ladder is monotonic and no endpoint mis-binds its `authorize_*` level.

**Files to read:**
- `src/cancelchain/api.py` — `authorize()` `_address`/`_role` injection (lines ~268-271); every `blueprint.add_url_rule` + its `authorize_*` wrapper (audit the endpoint→role binding table); the views that consume `_address`/`_role` (`**kwargs` handling).

- [ ] **Step 1: Read the endpoint→role binding table + kwargs handling for attacks a-c**

For (c), build the full table of `(route, method) → authorize_* level` from the `add_url_rule` calls and confirm each endpoint's gate matches its intended privilege (e.g. block POST is miller, txn POST is transactor, balance GETs are reader). For (b), check whether any view reads an address/role from request params rather than the injected `_address`/`_role`.

- [ ] **Step 2: Populate Adversary 6's section in the audit doc**

Include the endpoint→role binding table in the doc (it's useful reference even if clean).

- [ ] **Step 3: For each gap found, add a demonstration test**

- [ ] **Step 4: Run pytest, verify xfails show up correctly**

```bash
uv run pytest tests/test_auth_audit.py 2>&1 | tail -5
```

- [ ] **Step 5: Verify the existing test suite still passes**

```bash
uv run pytest 2>&1 | tail -3
```

- [ ] **Step 6: Verify gates**

```bash
uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy
```

- [ ] **Step 7: Commit**

```bash
git add docs/superpowers/audits/2026-05-31-api-authentication-audit.md tests/test_auth_audit.py
git commit -m "$(cat <<'EOF'
audit(a6): authorized insider traces + endpoint-role binding table

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 9: Adversary 7 — Resource / DoS attacker

**Adversary description (verbatim from spec):**

> **Capabilities:** Unauthenticated; sends volume.

**Attacks to trace (3):**

- **a.** argon2 cost on an unauthenticated path — `refreshed_cipher()` runs argon2 `hash` on every cold/expired `GET`; `verify()` runs argon2 on every `POST`. Quantify the asymmetry (cheap request → expensive server work) as an observation.
- **b.** `ApiToken` row growth (overlaps A2.c) — unbounded rows keyed on any on-chain address.
- **c.** Note (not a code finding): absence of endpoint rate limiting.

**Files to read:**
- `src/cancelchain/models.py` — `ApiToken.refreshed_cipher` / `verify` (argon2 calls), `_PASSWORD_HASHER`.
- `src/cancelchain/api.py` — `TokenView.get`/`post` (the unauthenticated entry).

- [ ] **Step 1: Read the resource surface for attacks a-c**

These are application-amplification observations, not code-fix findings per the spec's Non-goals. Document the argon2 asymmetry and row-growth as observations with severity (likely Low/Medium observation), and cross-reference A2.c. Note the absence of rate limiting as an operational recommendation, not a finding.

- [ ] **Step 2: Populate Adversary 7's section in the audit doc**

Mostly observations; tests only if a concrete app-level gap is demonstrable (e.g. unbounded row creation is testable — assert a cap exists, xfail today, cross-referenced with A2.c rather than duplicated).

- [ ] **Step 3: For each gap found, add a demonstration test**

- [ ] **Step 4: Run pytest, verify xfails show up correctly**

```bash
uv run pytest tests/test_auth_audit.py 2>&1 | tail -5
```

- [ ] **Step 5: Verify the existing test suite still passes**

```bash
uv run pytest 2>&1 | tail -3
```

- [ ] **Step 6: Verify gates**

```bash
uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy
```

- [ ] **Step 7: Commit**

```bash
git add docs/superpowers/audits/2026-05-31-api-authentication-audit.md tests/test_auth_audit.py
git commit -m "$(cat <<'EOF'
audit(a7): resource/DoS observations + any demonstration tests

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 10: Synthesis (Findings table + Clean categories + Cross-cutting + Recommendations + Executive summary)

After all 7 per-category tasks complete, the per-category sections are populated and the test module has N xfail tests (one per finding). This task synthesizes the cross-cutting content.

- [ ] **Step 1: Build the Findings table**

Read every finding produced by Tasks 3-9 (search the audit doc for `**Finding A`). Populate one row per finding in the Findings table:

```markdown
| ID | Category | Severity | Description | Remediation sketch | Test |
|---|---|---|---|---|---|
| A4.a | 4 | High | Operator *_ADDRESSES regex over-match enables role escalation | Validate/anchor configured regexes at load | `test_a4_a_regex_overmatch` |
| A2.c | 2 | Medium | Unauthenticated GET creates ApiToken rows for any on-chain address | Gate row creation / cap unredeemed rows | `test_a2_c_token_row_amplification` |
| ... | ... | ... | ... | ... |
```

Sort rows by severity (Critical → Low) then by ID within each severity.

- [ ] **Step 2: Write Clean categories**

Replace the `Clean categories` placeholder with an explicit per-category "no findings" record for every category that produced zero findings: what was checked, and why it's sound (e.g. "Adversary 1 — the JWT decode path pins `algorithms=['HS256']` and funnels every exception to `abort(401)`; `alg=none` and RS256-confusion both raise; no fall-through to `authorized=True`"). Negative evidence is a deliverable — the verification audit's clean-miller result was a headline.

- [ ] **Step 3: Write Cross-cutting observations**

Replace the placeholder with patterns spanning categories. Look for:
- **`SECRET_KEY` coupling** — one symmetric key for JWT (and any future Flask session/CSRF). Single point of compromise.
- **Roll-your-own challenge vs available primitives** — the handshake uses `encrypt`/`decrypt` while `Wallet.sign`/`validate_signature` sit unused; argon2 hashes a 122-bit random secret (designed for low-entropy passwords).
- **Claim hygiene** — absent `iat`/`nbf`/`iss`/`aud`.
- **Unauthenticated state-creating endpoint** — `GET /api/token` writes DB rows.
Each observation 1-3 paragraphs, citing finding IDs. If nothing cross-cutting, say so (still useful).

- [ ] **Step 4: Write Recommendations (includes the replacement analysis)**

Replace the placeholder with:
- **Severity-ordered remediation** — Critical → High → Medium → Low, with each finding's fix sketch; group findings that share a fix.
- **Targeted-fixes-vs-protocol-replacement analysis** (the spec's required output): summarize whether the findings are isolated-and-patchable or point to a structurally weak protocol. Then present the two candidate replacement directions, each with a trade-off paragraph:
  - **(a) Signed-nonce challenge-response** reusing `Wallet.sign`/`validate_signature`: server issues a random nonce, client signs it, server verifies with the public key. Smallest change; drops the RSA-OAEP/AES-GCM encrypt path and the argon2-on-random-secret smell; still stateful (nonce storage) and still issues the same JWT.
  - **(b) RFC 9421 HTTP Message Signatures / RS256 client-assertion**: client signs each request (or a short-lived assertion) with its private key; server verifies with the public key. Stateless, no challenge round-trip, no shared `SECRET_KEY` for issuance; larger change, new dependency/spec surface.
- **Recommendation** — state which direction the audit recommends and why, as input to a future redesign spec (do not design it here).

- [ ] **Step 5: Write the Executive summary**

Replace the placeholder with a 200-400 word summary: total findings by severity; headline conclusion (is the auth layer sound, are issues isolated or structural); the single most important finding (pull-quote); the replacement recommendation in one sentence (pointer to Recommendations); recommended next action.

- [ ] **Step 6: Verify the audit doc passes its acceptance checks**

```bash
grep -c '^## ' docs/superpowers/audits/2026-05-31-api-authentication-audit.md
grep -c '^### Adversary' docs/superpowers/audits/2026-05-31-api-authentication-audit.md
grep -c '^| A[1-7]\.' docs/superpowers/audits/2026-05-31-api-authentication-audit.md
# Recommendations actually contains both named replacement candidates (spec's primary deliverable):
grep -cE 'Wallet\.sign|RFC 9421|RS256 client-assertion' docs/superpowers/audits/2026-05-31-api-authentication-audit.md
# No placeholder text left behind:
grep -ci 'placeholder' docs/superpowers/audits/2026-05-31-api-authentication-audit.md
```

Expected: `^## ` matches 9 (Preconditions, Executive summary, Threat model, Methodology, Findings table, Per-adversary traces, Clean categories, Cross-cutting observations, Recommendations); `^### Adversary` matches 7; `^| A[1-7]\.` matches N (the finding count, equal to the test count); the candidate-grep matches **≥ 2** (both replacement directions named); the placeholder-grep matches **0**.

- [ ] **Step 7: Verify the test module + audit doc are in sync**

```bash
grep -c '^\*\*Finding A' docs/superpowers/audits/2026-05-31-api-authentication-audit.md
grep -c '^def test_a' tests/test_auth_audit.py
```

Expected: same number. If they differ, find the missing entry and fix it.

- [ ] **Step 8: Verify gates one more time**

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy
uv run pytest 2>&1 | tail -3
```

Expected: all green; pytest reports `256 passed, N xfailed, 1 skipped`.

- [ ] **Step 9: Verify the demonstration tests genuinely fail without xfail**

```bash
uv run pytest --runxfail tests/test_auth_audit.py 2>&1 | tail -10
```

Expected: `N failed` where N matches the test count. If any test PASSES under `--runxfail`, the auth layer rejects the attack today — remove that test + its finding from the doc, move the trace to Clean categories.

- [ ] **Step 10: Commit**

```bash
git add docs/superpowers/audits/2026-05-31-api-authentication-audit.md
git commit -m "$(cat <<'EOF'
audit(synthesis): findings table + clean categories + cross-cutting + recommendations + summary

Cross-references every finding from Tasks 3-9 into a single findings
table, records explicit no-finding results per clean category,
identifies patterns spanning categories (SECRET_KEY coupling, roll-
your-own challenge vs unused Wallet.sign, claim hygiene), and writes
Recommendations including the targeted-fixes-vs-protocol-replacement
analysis with two candidate directions. Adds the executive summary.

Total findings: <N> (<Critical>/<High>/<Medium>/<Low>).

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 11: Push + open impl PR

- [ ] **Step 1: Push the branch**

```bash
git push -u origin feat/api-auth-audit
```

- [ ] **Step 2: Open the PR**

```bash
gh pr create --base main --title "audit(auth): API authentication threat-modeled audit findings + demonstration tests" --body "$(cat <<'EOF'
## Summary
Executes the audit specified in \`docs/superpowers/specs/2026-05-31-api-auth-audit-design.md\`. Produces:

- A findings report at \`docs/superpowers/audits/2026-05-31-api-authentication-audit.md\` (preconditions, executive summary, threat model, methodology, findings table, per-adversary traces, clean categories, cross-cutting observations, recommendations).
- A test module at \`tests/test_auth_audit.py\` with one \`@pytest.mark.xfail(strict=True)\` test per finding.

Total findings: <N> (<Critical>/<High>/<Medium>/<Low>). See the audit doc's Executive summary for headline conclusions and the Findings table for the full inventory.

## Why
The companion audit deferred by the verification-pipeline audit (#84), which assumed auth was correct. First systematic pass over the API authentication layer: the token handshake, JWT issuance/validation, and role-regex mapping. The xfail tests serve as both proof-of-gap and regression prevention — when remediation PRs fix the gaps, \`strict=True\` forces xfail removal as part of the fix. The Recommendations section resolves the targeted-fixes-vs-protocol-replacement question (the challenge/response is a known roll-your-own).

## Out of scope
- **Remediation.** Each finding becomes a downstream PR. Severity-ordered recommendations are in the audit doc.
- **Transport security.** HTTPS assumed as a deployment precondition (stated in the report).
- **Verification pipeline, browser layer, key management, infra DoS.** Per the spec's Non-goals.

## Test plan
- [x] All 5 CI gates clean (ruff check + ruff format + pytest + mypy + db check).
- [x] \`uv run pytest 2>&1 | tail -3\` shows \`256 passed, N xfailed, 1 skipped\`.
- [x] \`uv run pytest --runxfail tests/test_auth_audit.py 2>&1 | tail -3\` shows \`N failed\` (xfail tests genuinely demonstrate gaps).
- [x] Audit doc structure verified (9 top-level sections, 7 adversary subsections, findings table rows = test count).
- [ ] CI green on 3.12 and 3.13.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 3: Stop — controller handles wor + mwg + sync**

---

## Task 12: Roadmap update (follow-up docs PR)

After the impl PR merges to main. Mirrors how the verification audit's roadmap update (#85) shipped as its own PR.

- [ ] **Step 1: Branch + read the roadmap**

```bash
git checkout main && git pull --ff-only
git checkout -b docs/roadmap-auth-audit-closed
```

Read `docs/superpowers/ROADMAP.md` — locate the "Future audit — API authentication layer" entry.

- [ ] **Step 2: Close the audit entry + open remediation items**

Edit `docs/superpowers/ROADMAP.md`:
- Move the "Future audit — API authentication layer" entry to the "Closed items" section, marked ✅ with the docs PR + impl PR numbers and a one-paragraph result (finding counts, the replacement recommendation).
- Add one forward-looking entry per finding (or per grouped remediation), each pointing at its finding ID in the audit doc — these become the remediation PRs, exactly as the verification audit's six findings did.
- If the audit recommends a protocol replacement, add a single "API auth protocol replacement" entry pointing at the audit's Recommendations section and noting it needs its own brainstorm → spec → plan cycle.

- [ ] **Step 3: Commit + push + open PR**

```bash
git add docs/superpowers/ROADMAP.md
git commit -m "$(cat <<'EOF'
docs(roadmap): close API auth audit, add remediation items

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
git push -u origin docs/roadmap-auth-audit-closed
gh pr create --base main --title "docs(roadmap): close API auth audit + open remediation items" --body "$(cat <<'EOF'
## Summary
- Closes the "Future audit — API authentication layer" roadmap entry (audit landed in the auth-audit impl PR).
- Opens one remediation entry per finding (+ a protocol-replacement entry if the audit recommends one).
- No code changes.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 4: Stop — controller handles wor + mwg + sync**

---

## Task 13: Phase verification (acceptance)

After both PRs merge to main.

- [ ] **Step 1: Confirm clean main**

```bash
git checkout main && git pull --ff-only
git log --oneline -4
```

Expected: top commits include the roadmap PR squash, the audit impl PR squash, and the audit docs PR squash.

- [ ] **Step 2: Audit deliverables present + tracked**

```bash
git ls-files docs/superpowers/audits/2026-05-31-api-authentication-audit.md tests/test_auth_audit.py docs/superpowers/specs/2026-05-31-api-auth-audit-design.md docs/superpowers/plans/2026-05-31-api-auth-audit.md
```

Expected: all four files tracked.

- [ ] **Step 3: Audit doc structure**

```bash
grep -c '^## ' docs/superpowers/audits/2026-05-31-api-authentication-audit.md
grep -c '^### Adversary' docs/superpowers/audits/2026-05-31-api-authentication-audit.md
grep -c '^| A[1-7]\.' docs/superpowers/audits/2026-05-31-api-authentication-audit.md
grep -cE 'Wallet\.sign|RFC 9421|RS256 client-assertion' docs/superpowers/audits/2026-05-31-api-authentication-audit.md
grep -ci 'placeholder' docs/superpowers/audits/2026-05-31-api-authentication-audit.md
```

Expected: 9 top-level sections; 7 adversary subsections; N findings table rows; candidate-grep ≥ 2; placeholder-grep 0.

- [ ] **Step 4: Test module sync with audit doc**

```bash
audit_findings=$(grep -c '^\*\*Finding A' docs/superpowers/audits/2026-05-31-api-authentication-audit.md)
test_count=$(grep -c '^def test_a' tests/test_auth_audit.py)
echo "audit findings: $audit_findings; tests: $test_count"
```

Expected: same number.

- [ ] **Step 5: pytest reports xfails correctly**

```bash
uv run pytest 2>&1 | tail -3
```

Expected: `256 passed, N xfailed, 1 skipped` where N matches the finding count.

- [ ] **Step 6: xfail tests genuinely fail when forced to run**

```bash
uv run pytest --runxfail tests/test_auth_audit.py 2>&1 | tail -5
```

Expected: `N failed`.

- [ ] **Step 7: Roadmap updated**

```bash
grep -c 'API auth' docs/superpowers/ROADMAP.md
```

Expected: ≥1 (the closed entry + any remediation/replacement entries).
