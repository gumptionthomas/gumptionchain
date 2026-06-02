# N4 Remediation — Bound the Celery Broker Publish — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remediate audit finding N4 (Low) — bound the Celery broker publish so a down/slow broker fast-fails `post_process.delay()` (~2s) instead of stalling the web-request thread ~16s. This closes the networking audit at 0/0/0/0.

**Architecture:** A config-only change in `tasks.init_tasks` (set `task_publish_retry=False` + a short `broker_connection_timeout` + `broker_connection_max_retries=0` before `celery.conf.update(app.config)`), plus replacing the off-thread N4 demonstration test with a bounded-config regression.

**Tech Stack:** Python 3.12+, Celery, pytest.

**Authoritative design:** `docs/superpowers/specs/2026-06-02-n4-bounded-publish-design.md`. Read it first.

---

## File structure

| File | Change |
|---|---|
| `src/cancelchain/tasks.py` | `init_tasks`: bounded publish defaults before `celery.conf.update(app.config)` |
| `tests/test_network_audit.py` | Replace `test_n4_async_publish_blocks_request_thread` with `test_n4_broker_publish_is_bounded`; drop now-unused imports |
| `docs/superpowers/audits/2026-06-01-network-p2p-audit.md`, `CLAUDE.md`, `docs/superpowers/ROADMAP.md` | N4 close-out + audit fully closed (Task 2) |

Branch: `fix/n4-bounded-publish` (design spec already committed here).

---

## Task 1: Bound the publish + swap the demonstration test

**Files:**
- Modify: `src/cancelchain/tasks.py` (`init_tasks`)
- Test: `tests/test_network_audit.py`

- [ ] **Step 1: Branch + baseline**

```bash
cd /home/gumptionthomas/Development/cancelchain
git branch --show-current        # fix/n4-bounded-publish
uv run pytest tests/test_network_audit.py -q 2>&1 | tail -1   # baseline: 7 passed, 1 xfailed
```
If branch/baseline differ, STOP and report.

- [ ] **Step 2: Replace the N4 demonstration test**

In `tests/test_network_audit.py`, DELETE the entire `test_n4_async_publish_blocks_request_thread` function (including its `@pytest.mark.xfail(...)` decorator) and replace it with:

```python
def test_n4_broker_publish_is_bounded(app):
    """N4: the broker publish is bounded (no unbounded publish/connection
    retries, short connection timeout) so a down/slow broker fast-fails the
    enqueue (~2s) instead of stalling the web-request thread ~16s.

    init_tasks(app) runs during create_app, so celery.conf reflects the fix.
    """
    from cancelchain.tasks import celery

    assert celery.conf.task_publish_retry is False
    assert celery.conf.broker_connection_timeout <= 2.0
    assert celery.conf.broker_connection_max_retries == 0
```

- [ ] **Step 3: Run it — verify it FAILS (bounding not applied yet)**

```bash
uv run pytest tests/test_network_audit.py::test_n4_broker_publish_is_bounded -q 2>&1 | tail -6
```
Expected: FAIL on the first assertion — `assert True is False` (Celery default `task_publish_retry` is `True`). This is the TDD red.

- [ ] **Step 4: Drop now-unused imports left by the old test**

The old `test_n4_async_publish_blocks_request_thread` used imports that other tests in the file may not. Run ruff to find them and remove any now-unused ones:

```bash
uv run ruff check tests/test_network_audit.py 2>&1 | tail -20
```
Remove each `F401` (unused import) ruff reports — likely candidates are `threading` and the `unittest.mock.patch` import IF no other test uses them (the N3/N2/N1 tests use `Miller`, `ApiClient`, `Transaction`, etc., so keep those; only remove what ruff flags as unused). Do NOT remove imports still used by other tests. Re-run `uv run ruff check tests/test_network_audit.py` until clean.

- [ ] **Step 5: Implement the bounded publish config**

In `src/cancelchain/tasks.py`, change `init_tasks` from:

```python
def init_tasks(app: Flask) -> Celery:
    celery.conf.update(app.config)
```

to:

```python
def init_tasks(app: Flask) -> Celery:
    # Bounded publish defaults (audit N4): on a down/slow broker, the
    # synchronous post_process.delay() publish must fail fast (~2s) rather
    # than stalling the web-request thread ~16s on Celery's default
    # publish-retry policy. Applied before app.config so an operator can
    # still override.
    celery.conf.update(
        task_publish_retry=False,
        broker_connection_timeout=2.0,
        broker_connection_max_retries=0,
    )
    celery.conf.update(app.config)
```
(Leave the `ContextTask` class and `return celery` below unchanged.)

- [ ] **Step 6: Run the test — verify it PASSES**

```bash
uv run pytest tests/test_network_audit.py::test_n4_broker_publish_is_bounded -q 2>&1 | tail -3
```
Expected: PASS.

- [ ] **Step 7: Full suite + lint**

```bash
uv run pytest tests/test_network_audit.py -q 2>&1 | tail -3   # 8 passed, 0 xfailed
uv run pytest -q 2>&1 | tail -2          # full suite green
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy 2>&1 | tail -1
```
Run `uv run ruff format src tests` if format asks. Investigate any failure. Note: `tasks.py` carries module-level `# mypy: disable-error-code` directives already; the config change introduces no new typing issues.

- [ ] **Step 8: Commit**

```bash
git add src/cancelchain/tasks.py tests/test_network_audit.py
git commit -m "$(cat <<'EOF'
fix(n4): bound the Celery broker publish so a dead broker fast-fails

init_tasks now sets task_publish_retry=False + broker_connection_timeout=2.0
+ broker_connection_max_retries=0 (before celery.conf.update(app.config) so
operators can override), so post_process.delay() on a down/slow broker fails
in ~2s instead of stalling the web-request thread ~16s. Replaces the
off-thread N4 demonstration with a bounded-config regression.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: N4 close-out — audit fully closed (docs) + open PR

**Files:**
- Modify: `docs/superpowers/audits/2026-06-01-network-p2p-audit.md`, `CLAUDE.md`, `docs/superpowers/ROADMAP.md`

- [ ] **Step 1: Audit report — close it at 0/0/0/0**

In `docs/superpowers/audits/2026-06-01-network-p2p-audit.md` (read each section first):
- Executive-summary headline: change `0 Critical / 0 High / 0 Medium / 1 Low` to **`0 Critical / 0 High / 0 Medium / 0 Low`** and update the surrounding sentence to state the audit is **fully closed** — all four findings remediated.
- Findings table, N4 row: Status `⏳ open (xfail)` → **`✅ remediated`**.
- The N4 trace (under "### 6. Async post-process path", the `**N4 (Low) — ...**` bullet): prepend `✅ Remediated. ` and append: `(As implemented: init_tasks sets task_publish_retry=False, broker_connection_timeout=2.0, and broker_connection_max_retries=0 before celery.conf.update(app.config), so a down/slow broker fails the synchronous post_process.delay() publish in ~2s instead of stalling the request thread ~16s. Operators can still override via config.)`
- Recommendations item 4 (the async-publish hardening item): prepend `✅ (done) `.

- [ ] **Step 2: CLAUDE.md**

In the `### Async post-processing` section (the paragraph describing `CC_API_ASYNC_PROCESSING`, `queue_post_process`, `handle_http_post`, and `tasks.post_process`), add a sentence: the broker publish is bounded — `init_tasks` sets `task_publish_retry=False` and a short `broker_connection_timeout` so a degraded broker fast-fails the enqueue rather than stalling the request thread. Read the section first and integrate naturally.

- [ ] **Step 3: Roadmap — mark the audit fully closed**

In `docs/superpowers/ROADMAP.md`, "## Audit remediation — P2P/networking findings":
- Change the N4 bullet's `⏳` to `✅` and append ` Closed by PR #PRNUM.` (literal placeholder; filled after the PR opens).
- Update the section intro to state all four findings (N1–N4) are closed and the **P2P/networking audit is fully closed at 0/0/0/0**.
- Move the whole "Audit remediation — P2P/networking findings" section into the "## Closed items (historical reference)" area as a single ✅ entry (mirroring how the verification-pipeline and API-auth audits are recorded once closed), preserving the per-finding bullets + their PR numbers. **Keep the "(perf follow-up) Indexed/SQL-filtered mempool expiry" bullet as an OPEN forward-looking item** (move it out of the closed audit block to the appropriate open/forward-looking area, since it's not a finding and remains to do).
- If unsure exactly how the prior closed audits are formatted, match their structure (a `✅ **<audit name>** — closed by PRs #… ` summary line with the finding/PR details).

- [ ] **Step 4: Gates + commit + push + PR**

```bash
uv run pytest -q 2>&1 | tail -2
uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy 2>&1 | tail -1
git add docs/superpowers/audits/2026-06-01-network-p2p-audit.md CLAUDE.md docs/superpowers/ROADMAP.md
git commit -m "docs(n4): close out N4 — networking audit fully closed (0/0/0/0)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
git push -u origin fix/n4-bounded-publish
gh pr create --base main --title "fix(n4): bound the Celery broker publish (audit N4, Low) — closes networking audit 0/0/0/0" --body "<summary: task_publish_retry=False + short broker_connection_timeout + max_retries=0 in init_tasks (operator-overridable), so a dead broker fast-fails the publish ~2s vs ~16s; off-thread test replaced with bounded-config regression; headline 0/0/0/1 -> 0/0/0/0, audit fully closed; link to design spec>"
```
Then edit the roadmap N4 bullet to the real PR number, commit, push.

- [ ] **Step 5: Internal review, then the single Copilot backstop**

Run the internal cross-model review (a different-model reviewer checking: the three Celery settings are valid names and actually bound the publish; applied before `app.config.update` so operators can override AND the test still passes given app.config doesn't carry those keys; the bounded-config test genuinely fails pre-fix and passes post-fix; no unused imports left; the audit report/roadmap consistently show 0/0/0/0 with the perf follow-up correctly kept open) to convergence BEFORE the PR's Copilot pass. Then address Copilot's single backstop. `mwg` once green.

---

## Self-review checklist (controller, before execution)

- **Spec coverage:** bounded config → T1S5; test swap → T1S2; unused-import cleanup → T1S4; audit 0/0/0/0 + CLAUDE.md + roadmap (audit closed, perf item kept open) → T2. ✓
- **Setting names:** `task_publish_retry`, `broker_connection_timeout`, `broker_connection_max_retries` — confirmed valid `celery.conf` attributes (defaults True / 4 / 100); the test asserts the post-fix values. ✓
- **TDD ordering:** new bounded-config test written first (red against Celery defaults), then the init_tasks change (green). ✓
- **No placeholders:** complete code in every step; only the PR body/number filled at PR time. ✓
- **Audit-closing detail:** roadmap moves the audit to closed items but KEEPS the indexed-mempool-expiry perf follow-up open. ✓
