# N2 Remediation — Mempool Admission Cap — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remediate audit finding N2 (Medium) — cap `pending_txns` admission at a configurable `MAX_PENDING_TXNS`, rejecting new txns when full via a `MempoolFullError` mapped to HTTP 503.

**Architecture:** A cheap `PendingTxnDAO.count()` check in `Node.receive_transaction`'s admission path raises `MempoolFullError` (a new `CCError` subclass) when the pool is full; `TxnView.post` maps it to a retryable 503. Reject-when-full; the now-bounded O(cap) read-path optimization is deferred.

**Tech Stack:** Python 3.12+, Flask `current_app.config`, the `requests_proxy` WSGI fixture, `time_machine`, pytest.

**Authoritative design:** `docs/superpowers/specs/2026-06-01-n2-mempool-cap-design.md`. Read it first.

---

## File structure

| File | Change |
|---|---|
| `src/cancelchain/config.py` | Add `MAX_PENDING_TXNS: int = field(default=10000)` to `EnvAppSettings` |
| `src/cancelchain/exceptions.py` | Add `class MempoolFullError(CCError)` |
| `src/cancelchain/node.py` | `receive_transaction` cap check + import `MempoolFullError` |
| `src/cancelchain/api.py` | `TxnView.post` maps `MempoolFullError` → 503 + import |
| `tests/test_network_audit.py` | Flip `test_n2_mempool_has_no_admission_cap`; add `test_n2_full_mempool_returns_503` |
| `docs/superpowers/audits/2026-06-01-network-p2p-audit.md`, `CLAUDE.md`, `docs/superpowers/ROADMAP.md` | N2 close-out (Task 3) |

Branch: `fix/n2-mempool-cap` (design spec already committed here).

---

## Task 1: Config + exception + admission cap + flip the demonstration

**Files:**
- Modify: `src/cancelchain/config.py`, `src/cancelchain/exceptions.py`, `src/cancelchain/node.py`
- Test: `tests/test_network_audit.py`

- [ ] **Step 1: Branch + baseline**

```bash
cd /home/gumptionthomas/Development/cancelchain
git branch --show-current        # fix/n2-mempool-cap
uv run pytest tests/test_network_audit.py -q 2>&1 | tail -1   # baseline: 3 passed, 3 xfailed
```
If branch/baseline differ, STOP and report.

- [ ] **Step 2: Add the config field**

In `src/cancelchain/config.py`, in `EnvAppSettings`, add after `MAX_CHAIN_FILL_DEPTH: int = field(default=50000)`:

```python
    MAX_PENDING_TXNS: int = field(default=10000)
```

- [ ] **Step 3: Add the exception**

In `src/cancelchain/exceptions.py`, add (place it near the other `CCError` subclasses — e.g. after the transaction-error block, as a direct `CCError` subclass, NOT under `InvalidTransactionError`):

```python
class MempoolFullError(CCError):
    pass
```

- [ ] **Step 4: Flip + adapt the demonstration test**

In `tests/test_network_audit.py`, modify `test_n2_mempool_has_no_admission_cap`:
1. Remove the `@pytest.mark.xfail(strict=True, reason=(...))` decorator block.
2. Wrap the `m.receive_transaction(...)` call in the loop with `try/except MempoolFullError: pass`.
3. Past-tense the docstring (the cap now exists).
4. Add `from cancelchain.exceptions import MempoolFullError` to the test file's imports.

The loop becomes:
```python
        for i in range(6):
            t = Transaction()
            t.add_inflow(Inflow(outflow_txid='0' * 64, outflow_idx=0))
            t.add_outflow(
                Outflow(amount=1, subject=encode_subject(f'subj-{i}'))
            )
            t.set_wallet(wallet)
            t.seal()
            t.sign()
            try:
                m.receive_transaction(t.txid, t.to_json())
            except MempoolFullError:
                pass

        # Cap honored: 3 admit, submissions 4-6 raise MempoolFullError
        # (caught) -> len == 3.
        assert len(m.pending_txns) <= 3
```
(Keep the `app.config['MAX_PENDING_TXNS'] = 3` line and the final assertion unchanged.)

- [ ] **Step 5: Run the flipped test — verify it FAILS (cap not implemented yet)**

```bash
uv run pytest tests/test_network_audit.py::test_n2_mempool_has_no_admission_cap -q 2>&1 | tail -6
```
Expected: FAIL — `assert 6 <= 3` (no cap yet; nothing raises, all 6 admit). (Removing the xfail marker means it now fails loudly rather than xfailing — that's the TDD red for this task.)

- [ ] **Step 6: Implement the cap in `receive_transaction`**

In `src/cancelchain/node.py`, add `MempoolFullError` to the `cancelchain.exceptions` import block (it already imports `DuplicateMinedTransactionError`, `InvalidBlockError`, etc.). Then change the admission block (currently):

```python
        if txn not in self.pending_txns:
            try:
                self.pending_txns.add(txn)
            except SQLAlchemyError:
                rollback_session()
                if txn not in self.pending_txns:
                    raise
            added = True
```

to:

```python
        if txn not in self.pending_txns:
            if len(self.pending_txns) >= current_app.config['MAX_PENDING_TXNS']:
                raise MempoolFullError()
            try:
                self.pending_txns.add(txn)
            except SQLAlchemyError:
                rollback_session()
                if txn not in self.pending_txns:
                    raise
            added = True
```

(`len(self.pending_txns)` is `PendingTxnDAO.count()` — a cheap SQL COUNT. `current_app` is already imported in `node.py`.)

- [ ] **Step 7: Run the flipped test — verify it PASSES**

```bash
uv run pytest tests/test_network_audit.py::test_n2_mempool_has_no_admission_cap -q 2>&1 | tail -3
```
Expected: PASS (3 admit, 3 raise-and-caught, `len == 3`).

- [ ] **Step 8: Full suite + lint**

```bash
uv run pytest -q 2>&1 | tail -2          # green; existing txn tests (test_api.py, test_miller.py) unaffected (default cap 10000 >> their volumes)
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy 2>&1 | tail -1
```
Run `uv run ruff format src tests` if format asks. Investigate any failure — note tests that submit a handful of txns are well under the 10000 default and must still pass.

- [ ] **Step 9: Commit**

```bash
git add src/cancelchain/config.py src/cancelchain/exceptions.py src/cancelchain/node.py tests/test_network_audit.py
git commit -m "$(cat <<'EOF'
fix(n2): cap mempool admission at MAX_PENDING_TXNS

receive_transaction rejects a new txn with MempoolFullError once the pending
pool holds MAX_PENDING_TXNS rows (cheap PendingTxnDAO.count() check, inside the
not-already-pending guard so re-receipts are unaffected). Default 10000.
Flips test_n2_mempool_has_no_admission_cap to a passing regression.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 2: 503 at the API layer

**Files:**
- Modify: `src/cancelchain/api.py` (`TxnView.post`)
- Test: `tests/test_network_audit.py`

- [ ] **Step 1: Write the failing 503 regression test**

Append to `tests/test_network_audit.py` (the file imports `ApiClient`, `Transaction`, `Inflow`, `Outflow`, `encode_subject`, `httpx`; it will import `MempoolFullError` from Task 1):

```python
def test_n2_full_mempool_returns_503(app, host, time_machine, requests_proxy, wallet):
    """N2 (view layer): a valid txn submitted to a full mempool returns a
    retryable 503, not a 400 — the txn is well-formed and authorized; the
    node is temporarily at capacity.
    """
    with app.app_context():
        time_machine.move_to(now() - datetime.timedelta(hours=1))
        app.config['MAX_PENDING_TXNS'] = 1
        client = ApiClient(host, wallet)

        def make_txn(i):
            t = Transaction()
            t.add_inflow(Inflow(outflow_txid='0' * 64, outflow_idx=0))
            t.add_outflow(
                Outflow(amount=1, subject=encode_subject(f's503-{i}'))
            )
            t.set_wallet(wallet)
            t.seal()
            t.sign()
            return t

        # Cap = 1: the first valid txn is admitted.
        r1 = client.post_transaction(make_txn(0), raise_for_status=False)
        assert r1.status_code in (
            httpx.codes.OK,
            httpx.codes.CREATED,
            httpx.codes.ACCEPTED,
        )
        # The pool is now full -> the next valid txn is rejected with 503.
        r2 = client.post_transaction(make_txn(1), raise_for_status=False)
        assert r2.status_code == httpx.codes.SERVICE_UNAVAILABLE
```

- [ ] **Step 2: Run it — verify it FAILS**

```bash
uv run pytest tests/test_network_audit.py::test_n2_full_mempool_returns_503 -q 2>&1 | tail -8
```
Expected: FAIL — the second POST currently returns 400 (the `MempoolFullError` from Task 1 hits the generic `except CCError` → `make_error_response` → 400), so `assert r2.status_code == 503` fails with `400 == 503`. (This confirms the node-layer cap from Task 1 fires; Task 2 fixes the status code.)

- [ ] **Step 3: Map `MempoolFullError` → 503 in `TxnView.post`**

In `src/cancelchain/api.py`, add `MempoolFullError` to the `cancelchain.exceptions` import block (alongside `CCError`, `EmptyChainError`, etc.). Then in `TxnView.post`, insert an explicit catch BEFORE the existing `except CCError` (order matters — `MempoolFullError` is a `CCError`):

```python
        except MempoolFullError:
            return make_json_response({'error': 'mempool full'}, 503)
        except CCError as err:
            return make_error_response(err)
        except Exception as e:
            exception_response(e)
```

- [ ] **Step 4: Run it — verify it PASSES**

```bash
uv run pytest tests/test_network_audit.py::test_n2_full_mempool_returns_503 -q 2>&1 | tail -3
```
Expected: PASS (r1 admitted, r2 → 503).

- [ ] **Step 5: Full suite + lint**

```bash
uv run pytest tests/test_network_audit.py -q 2>&1 | tail -3   # 5 passed, 2 xfailed
uv run pytest --runxfail tests/test_network_audit.py -q 2>&1 | tail -3   # only N3/N4 fail
uv run pytest -q 2>&1 | tail -2          # full suite green
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy 2>&1 | tail -1
```
Run `uv run ruff format src tests` if format asks.

- [ ] **Step 6: Commit**

```bash
git add src/cancelchain/api.py tests/test_network_audit.py
git commit -m "$(cat <<'EOF'
fix(n2): TxnView returns 503 for a full mempool

MempoolFullError maps to a retryable 503 (Service Unavailable) rather than the
generic CCError 400 — the txn is valid and authorized; the node is temporarily
full. The explicit catch precedes the generic CCError catch.

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
EOF
)"
```

---

## Task 3: N2 close-out (docs) + open PR

**Files:**
- Modify: `docs/superpowers/audits/2026-06-01-network-p2p-audit.md`, `CLAUDE.md`, `docs/superpowers/ROADMAP.md`

- [ ] **Step 1: Audit report**

In `docs/superpowers/audits/2026-06-01-network-p2p-audit.md`:
- Executive-summary headline: change `0 Critical / 0 High / 2 Medium / 1 Low` to **`0 Critical / 0 High / 1 Medium / 1 Low`** and adjust the surrounding sentence (N2 now remediated).
- Findings table, N2 row: Status `⏳ open (xfail)` → **`✅ remediated`**.
- The N2 trace (under "### 4. Protocol / framing abuser" and/or "### 1. Resource-exhaustion peer" — N2 appears in the findings; find the `**N2 (Medium) — ...**` bullet): prepend `✅ Remediated. ` and append: `(As implemented: receive_transaction rejects a new txn with MempoolFullError once the pool holds MAX_PENDING_TXNS rows (default 10000, cheap PendingTxnDAO.count() check); TxnView.post maps it to a retryable 503. The read path is now O(cap); an indexed/SQL-filtered expiry query is deferred as a separate perf item.)`
- Recommendations item 2 (the mempool-cap item): prepend `✅ (done) `.
- Cross-cutting observation 3 (the O(mempool) re-materialization): append a note that with the admission cap, this read cost is now O(cap); the indexed-expiry optimization is deferred (now-bounded perf, not a security gap).

- [ ] **Step 2: CLAUDE.md**

In the configuration section's "Key `CC_*` settings:" paragraph, add `MAX_PENDING_TXNS` (env `CC_MAX_PENDING_TXNS`, default 10000 — caps `pending_txns` mempool admission; a full pool returns 503).

- [ ] **Step 3: Roadmap**

In `docs/superpowers/ROADMAP.md`, "Audit remediation — P2P/networking findings": change the N2 bullet from `⏳` to `✅` and append ` Closed by PR #PRNUM.` (literal placeholder; filled after the PR opens). Update the section intro (which currently says "N1 is closed; N2–N4 remain open.") to "N1 and N2 are closed; N3–N4 remain open." Add a new follow-up bullet under the section (or in the appropriate forward-looking area): `- **(perf follow-up) Indexed/SQL-filtered mempool expiry + reads** — discard_expired_pending_txns / PendingTxnView / create_block iterate the full pool re-parsing every row via PendingTxnSet.__iter__. Now bounded to O(cap) by the N2 admission cap; convert to an indexed timestamp query to drop the Python re-parse on the mill critical path. Surfaced by audit N2.`

- [ ] **Step 4: Gates + commit + push + PR**

```bash
uv run pytest -q 2>&1 | tail -2
uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy 2>&1 | tail -1
git add docs/superpowers/audits/2026-06-01-network-p2p-audit.md CLAUDE.md docs/superpowers/ROADMAP.md
git commit -m "docs(n2): close out N2 — audit 0/0/1/1, CLAUDE.md, roadmap + perf follow-up

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
git push -u origin fix/n2-mempool-cap
gh pr create --base main --title "fix(n2): cap mempool admission (audit N2, Medium)" --body "<summary: MAX_PENDING_TXNS cap + MempoolFullError -> 503, reject-when-full, flipped demonstration + new 503 test, headline 0/0/2/1 -> 0/0/1/1, indexed-expiry deferred, link to design spec>"
```
Then edit the roadmap N2 bullet to the real PR number, commit, push.

- [ ] **Step 5: Internal review, then the single Copilot backstop**

Run the internal cross-model review (a different-model reviewer checking: the cap check is inside the `not in pending` guard so re-receipts aren't rejected; `len()` is the cheap count not a materialization; the 503 catch precedes the `CCError` catch; the flipped test genuinely passes only with the cap; the 503 test exercises the view path; the gossip/Miller pull paths handle the new raise gracefully) to convergence BEFORE the PR's Copilot pass. Then address Copilot's single backstop. `mwg` once green.

---

## Self-review checklist (controller, before execution)

- **Spec coverage:** config field → T1S2; `MempoolFullError` → T1S3; `receive_transaction` cap → T1S6; flip demonstration → T1S4; 503 mapping → T2S3; 503 test → T2S1; audit/CLAUDE.md/roadmap + perf follow-up → T3. ✓
- **Type consistency:** config key `MAX_PENDING_TXNS` (stripped, no `CC_`) identical in config.py field, `current_app.config['MAX_PENDING_TXNS']`, and the tests' `app.config['MAX_PENDING_TXNS']`. `MempoolFullError` imported in node.py (raise site) and api.py (catch site). ✓
- **Ordering:** config + exception + node cap land in Task 1; the 503 mapping in Task 2 depends on the Task 1 raise (the Task 2 test fails at 400 until the mapping lands). ✓
- **No placeholders:** complete code in every step; only the PR body/number are filled at PR time. ✓
