# A7.e — Single `TXN_TIMEOUT` Expiry Definition Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Define the transaction-expiry boundary once and apply it consistently across all four `TXN_TIMEOUT` sites.

**Architecture:** Add a `txn_is_expired(txn_ts, reference_dt)` helper in `block.py` (expired ⟺ `txn_ts < reference_dt − TXN_TIMEOUT`; open boundary). Route the three Python sites through it; the SQL site already agrees and gets a cross-ref comment. No schema change.

**Tech Stack:** Python 3.12, pytest + time-machine, uv. The consensus site (`Block.validate_transaction`) is refactored behavior-identically.

**Spec:** `docs/superpowers/specs/2026-05-31-a7e-txn-timeout-boundary-design.md`

---

## Prerequisites (read before starting)

- **Full-suite pytest needs `COLUMNS=200`** (latent unrelated `tests/test_command.py::test_create_wallet` terminal-width bug). Use `COLUMNS=200 uv run pytest` for full-suite runs.
- **Canonical rule:** a txn is expired iff its timestamp is **strictly older than `TXN_TIMEOUT`** relative to the reference time (`txn_ts < reference_dt − TXN_TIMEOUT`). The boundary is **open** — a txn exactly `TXN_TIMEOUT` old is **alive**. This is forced by the consensus anchor (`Block.validate_transaction` already uses strict `<` and must not change).
- `test_a7_e_txn_timeout_boundary_inconsistency` is `@pytest.mark.xfail(strict=True)`. The fix makes it pass, so its xfail MUST be removed in the **same commit** as the fix (strict xfail → xpass → CI failure otherwise). Remove it first (Step 1).
- `block.py` already imports `datetime` (`from datetime import datetime, timedelta`, line 5). `now()` (from `cancelchain.util`) returns a second-resolution `datetime`.
- Import sorting: ruff isort uses `order-by-type` — constants (ALL_CAPS) first, then classes (CamelCase), then functions (lowercase). Honor this in the edited import lines (shown below) or `ruff check` will flag `I001`.

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `src/cancelchain/block.py` | `TXN_TIMEOUT` + block validation | Add `txn_is_expired`; route `validate_transaction` through it |
| `src/cancelchain/node.py` | pending-pool maintenance | Route `discard_expired_pending_txns` through helper; swap import |
| `src/cancelchain/miller.py` | miller txn selection | Route `pending_chain_txns` through helper; swap import |
| `src/cancelchain/models.py` | pending DB query | Cross-ref comment on `json_datas` (no behavior change) |
| `tests/test_block.py` | block unit tests | Add `txn_is_expired` boundary unit test |
| `tests/test_miller.py` | miller tests | Add `pending_chain_txns` boundary test |
| `tests/test_verification_audit.py` | audit regression | Un-xfail `test_a7_e…` |
| `docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md` | audit record | Mark A7.e remediated; update counts |
| `docs/superpowers/ROADMAP.md` | roadmap | Move A7.e to Closed; severity → 0/0/0/1 |

---

### Task 1: `txn_is_expired` helper + route all sites (un-xfail A7.e)

**Files:**
- Modify: `src/cancelchain/block.py` (add helper after constants ~line 52; `validate_transaction` line 276)
- Modify: `src/cancelchain/node.py` (import line 12; `discard_expired_pending_txns` lines 103-107)
- Modify: `src/cancelchain/miller.py` (import line 10; `pending_chain_txns` lines 71-79)
- Modify: `src/cancelchain/models.py` (`json_datas` line ~884 — comment only)
- Modify: `tests/test_block.py`, `tests/test_miller.py`, `tests/test_verification_audit.py`

- [ ] **Step 1: Un-xfail the acceptance demonstrator**

In `tests/test_verification_audit.py`, delete the `@pytest.mark.xfail(...)` decorator block directly above `def test_a7_e_txn_timeout_boundary_inconsistency` (line 482). The decorator to remove is exactly:

```python
@pytest.mark.xfail(
    reason=(
        'Audit finding A7.e — severity Low — three call sites apply '
        'TXN_TIMEOUT with three different comparison operators around '
        'the boundary value: Block.validate_transaction uses strict < '
        '(block.py:269), Miller.pending_chain_txns uses strict > '
        '(miller.py:74), and Node.discard_expired_pending_txns uses <= '
        '(node.py:105). A txn timestamped exactly now-TXN_TIMEOUT is '
        'non-expired per the block validator but expired per pending-pool '
        'maintenance. See '
        'docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md'
    ),
    strict=True,
)
```
Leave the test function body unchanged.

- [ ] **Step 2: Write the failing unit + miller tests**

In `tests/test_block.py`, add `txn_is_expired` to the block import. The current single-line `from cancelchain.block import MAX_TRANSACTIONS, TXN_TIMEOUT, Block` would exceed the 80-char limit with a fourth name, so convert it to the parenthesized multi-line form:
```python
from cancelchain.block import (
    MAX_TRANSACTIONS,
    TXN_TIMEOUT,
    Block,
    txn_is_expired,
)
```
(`datetime`, `TXN_TIMEOUT`, and `now` are already imported elsewhere in the file.) Append:

```python
def test_txn_is_expired_boundary():
    ref = now()
    one_sec = datetime.timedelta(seconds=1)
    # Strictly older than TXN_TIMEOUT -> expired.
    assert txn_is_expired(ref - TXN_TIMEOUT - one_sec, ref) is True
    # Exactly TXN_TIMEOUT old -> alive (open boundary).
    assert txn_is_expired(ref - TXN_TIMEOUT, ref) is False
    # Younger than TXN_TIMEOUT -> alive.
    assert txn_is_expired(ref - TXN_TIMEOUT + one_sec, ref) is False
```

In `tests/test_miller.py` (which imports `TXN_TIMEOUT`, `Miller`, `now`, `datetime`), append:

```python
def test_pending_chain_txns_boundary_alive(app, time_machine, wallet):
    """A7.e: a pending txn timestamped exactly now - TXN_TIMEOUT is
    yielded by pending_chain_txns (alive at the open boundary)."""
    with app.app_context():
        m = Miller(milling_wallet=wallet)
        b0 = m.create_block()
        m.mill_block(b0)
        now_dt = now()
        # Craft a valid transfer timestamped exactly at the boundary.
        time_machine.move_to(now_dt - TXN_TIMEOUT)
        t = m.longest_chain.create_transfer(
            wallet, m.longest_chain.balance(wallet.address), wallet.address
        )
        t.sign()
        time_machine.move_to(now_dt)
        m.pending_txns.add(t)
        yielded = list(m.pending_chain_txns(m.longest_chain))
        assert t in yielded


def test_pending_chain_txns_expired_excluded(app, time_machine, wallet):
    """A7.e: a pending txn one second older than the boundary
    (strictly older than TXN_TIMEOUT) is NOT yielded by pending_chain_txns.

    Directly exercises pending_chain_txns's helper use (the existing
    test_expired_transaction routes through create_block, which calls
    discard_expired_pending_txns first, so it does not cover this site)."""
    with app.app_context():
        m = Miller(milling_wallet=wallet)
        b0 = m.create_block()
        m.mill_block(b0)
        now_dt = now()
        # One second past the boundary -> strictly older -> expired.
        time_machine.move_to(now_dt - TXN_TIMEOUT - datetime.timedelta(seconds=1))
        t = m.longest_chain.create_transfer(
            wallet, m.longest_chain.balance(wallet.address), wallet.address
        )
        t.sign()
        time_machine.move_to(now_dt)
        m.pending_txns.add(t)
        yielded = list(m.pending_chain_txns(m.longest_chain))
        assert t not in yielded
```

- [ ] **Step 3: Run to verify failure**

Run:
```bash
uv run pytest tests/test_block.py::test_txn_is_expired_boundary tests/test_miller.py::test_pending_chain_txns_boundary_alive tests/test_miller.py::test_pending_chain_txns_expired_excluded tests/test_verification_audit.py::test_a7_e_txn_timeout_boundary_inconsistency -v
```
Expected: FAIL — `tests/test_block.py` errors at collection (`ImportError: cannot import name 'txn_is_expired'`); the miller test fails (today `pending_chain_txns` uses strict `>`, so the boundary txn is NOT yielded); `test_a7_e…` fails (today `discard_expired_pending_txns` uses `<=`, evicting the boundary txn).

- [ ] **Step 4: Implement the helper**

In `src/cancelchain/block.py`, immediately after the module constants block (after the `MISSED_TARGET_MSG = 'Missed target'` line, before the first function/class), add:

```python
def txn_is_expired(
    txn_timestamp_dt: datetime, reference_dt: datetime
) -> bool:
    """A txn is expired iff its timestamp is strictly older than
    TXN_TIMEOUT relative to reference_dt. Open boundary: a txn exactly
    TXN_TIMEOUT old (txn_timestamp_dt == reference_dt - TXN_TIMEOUT) is
    NOT expired. Single source of truth for the expiry boundary — every
    other site (Node.discard_expired_pending_txns, Miller.pending_chain_txns,
    and the PendingTxnDAO.json_datas SQL query) applies this same rule.
    """
    return txn_timestamp_dt < reference_dt - TXN_TIMEOUT
```

- [ ] **Step 5: Route `Block.validate_transaction` through it (behavior-identical)**

In `src/cancelchain/block.py`, in `validate_transaction`, replace:

```python
            if txn_ts_dt < self.timestamp_dt - TXN_TIMEOUT:
                raise ExpiredTransactionError()
```
with:
```python
            if txn_is_expired(txn_ts_dt, self.timestamp_dt):
                raise ExpiredTransactionError()
```
(`TXN_TIMEOUT` remains defined and is now referenced only inside `txn_is_expired`.)

- [ ] **Step 6: Route `Node.discard_expired_pending_txns` through it**

In `src/cancelchain/node.py`, change the import on line 12 from:
```python
from cancelchain.block import TXN_TIMEOUT, Block
```
to:
```python
from cancelchain.block import Block, txn_is_expired
```
Then replace the method body:
```python
    def discard_expired_pending_txns(self) -> None:
        expired_dt = now() - TXN_TIMEOUT
        for txn in self.pending_txns:
            if txn.timestamp_dt is not None and txn.timestamp_dt <= expired_dt:
                self.pending_txns.discard(txn)
```
with:
```python
    def discard_expired_pending_txns(self) -> None:
        reference_dt = now()
        for txn in self.pending_txns:
            if txn.timestamp_dt is not None and txn_is_expired(
                txn.timestamp_dt, reference_dt
            ):
                self.pending_txns.discard(txn)
```

- [ ] **Step 7: Route `Miller.pending_chain_txns` through it**

In `src/cancelchain/miller.py`, change the import on line 10 from:
```python
from cancelchain.block import MAX_TRANSACTIONS, TXN_TIMEOUT, Block
```
to:
```python
from cancelchain.block import MAX_TRANSACTIONS, Block, txn_is_expired
```
Then replace:
```python
        expired_dt = now() - TXN_TIMEOUT
        for txn in self.pending_txns:
            if (
                txn.timestamp_dt is not None
                and txn.timestamp_dt > expired_dt
                and not chain.get_transaction(
                    txn.txid  # type: ignore[arg-type]
                )
            ):
                yield txn
```
with:
```python
        reference_dt = now()
        for txn in self.pending_txns:
            if (
                txn.timestamp_dt is not None
                and not txn_is_expired(txn.timestamp_dt, reference_dt)
                and not chain.get_transaction(
                    txn.txid  # type: ignore[arg-type]
                )
            ):
                yield txn
```

- [ ] **Step 8: Cross-ref comment on the SQL site (no behavior change)**

In `src/cancelchain/models.py`, in `json_datas`, replace:
```python
        if expired is not None:
            stmt = stmt.where(cls.timestamp >= expired)
```
with:
```python
        if expired is not None:
            # Same open-boundary rule as block.txn_is_expired: a txn is
            # expired iff its timestamp is strictly older than the cutoff,
            # so keep timestamp >= cutoff (the boundary txn is alive).
            stmt = stmt.where(cls.timestamp >= expired)
```

- [ ] **Step 9: Run to verify pass**

Run:
```bash
uv run pytest tests/test_block.py::test_txn_is_expired_boundary tests/test_miller.py::test_pending_chain_txns_boundary_alive tests/test_miller.py::test_pending_chain_txns_expired_excluded tests/test_verification_audit.py::test_a7_e_txn_timeout_boundary_inconsistency -v
```
Expected: all PASS.

- [ ] **Step 10: Regression check**

Run:
```bash
COLUMNS=200 uv run pytest tests/test_block.py tests/test_miller.py tests/test_node.py tests/test_chain.py tests/test_api.py -q
```
> Note: `tests/test_node.py` may not exist; if pytest reports "file or directory not found" for it, drop it from the command — the other suites are the ones that matter. Expected: PASS.

- [ ] **Step 11: Lint/type, then commit**

Run:
```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy
```
Expected: all clean. Fix anything flagged (additively). Then:
```bash
git add src/cancelchain/block.py src/cancelchain/node.py src/cancelchain/miller.py src/cancelchain/models.py tests/test_block.py tests/test_miller.py tests/test_verification_audit.py
git commit -m "fix(a7e): single txn_is_expired() definition for the TXN_TIMEOUT boundary"
```

---

### Task 2: Docs — audit + ROADMAP

**Files:**
- Modify: `docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md`
- Modify: `docs/superpowers/ROADMAP.md`

> All Find/Replace edits below are **substring replacements** (use the Edit tool): match the quoted "Find" text and swap only that span, preserving any other text on the same line.

- [ ] **Step 1: Update the audit doc**

(a) Intro count (line 9):
Find: `Four have since been remediated (A2.e, A4.c, A7.b, A7.h); two remain open (A7.e, A1.f).`
Replace: `Five have since been remediated (A2.e, A4.c, A7.b, A7.h, A7.e); one remains open (A1.f).`

(b) Findings-table count (line 38):
Find: `2 open findings: 0 Critical / 0 High / 0 Medium / 2 Low (post-A7.h).`
Replace: `1 open finding: 0 Critical / 0 High / 0 Medium / 1 Low (post-A7.e).`

(c) REMOVE the entire findings-table row for A7.e — the markdown line beginning `| A7.e | Low |` and ending `| `test_a7_e_txn_timeout_boundary_inconsistency` |`. (After removal the table lists only A1.f.)

(d) Sub-attack Outcome (line 976) — a single substring swap.

Find exactly:
```
**Outcome:** REJECTED operationally (the txn gets discarded from pending before any miller picks it up) but ACCEPTED structurally (block-layer validation considers it non-expired). The boundary inconsistency is observable: the same txn-timestamp is "alive" per Block layer and "dead" per Node/Miller layers.
```
Replace with:
```
**Outcome:** RESOLVED (post-remediation). All four sites now agree on a single boundary rule (expired ⟺ strictly older than `TXN_TIMEOUT`; open boundary): the three Python sites (Block validator, Node discard, Miller selection) via the shared `txn_is_expired()` helper, and the pending-query SQL via the equivalent `timestamp >= cutoff` predicate. A boundary txn is consistently "alive" across all four. (Pre-remediation, the same txn-timestamp was "alive" per the Block layer but "dead" per Node/Miller.)
```

(e) Finding A7.e paragraph (line 978) — replace the entire paragraph (a single substring swap). This applies the "past-tense Finding paragraph under a ✅ Remediated banner" convention and drops the now-stale per-site line numbers (avoiding doc line-drift).

Find exactly:
```
**Finding A7.e — Severity Low:** Three call sites apply `TXN_TIMEOUT` with three different comparison operators around the boundary value: `Block.validate_transaction` uses strict `<` (`src/cancelchain/block.py:269`), `Miller.pending_chain_txns` uses strict `>` (`src/cancelchain/miller.py:74`), and `Node.discard_expired_pending_txns` uses `<=` (`src/cancelchain/node.py:105`). A txn whose `timestamp` is *exactly* `now - TXN_TIMEOUT` is therefore "non-expired" per the block validator but "expired" per pending-pool maintenance and miller selection. No chain-correctness invariant is violated (the txn would be REJECTED via the miller's exclusion before reaching a block), but the inconsistency is a latent foot-gun: a future refactor that swaps the miller's `>` for `>=` (or removes `discard_expired_pending_txns`'s `<=` branch) would let the txn drift to a state where the block layer accepts what the miller silently rejected, complicating debugging of "why didn't this txn get mined." The spec intent ("`TXN_TIMEOUT` window") is ambiguous about whether the boundary is open or closed; pick one and apply consistently.
```
Replace with:
```
✅ **Remediated.** **Finding A7.e — Severity Low** (pre-remediation behavior described below): Three call sites applied `TXN_TIMEOUT` with three different comparison operators around the boundary value: `Block.validate_transaction` used strict `<`, `Miller.pending_chain_txns` used strict `>`, and `Node.discard_expired_pending_txns` used `<=`. A txn whose `timestamp` was *exactly* `now - TXN_TIMEOUT` was therefore "non-expired" per the block validator but "expired" per pending-pool maintenance and miller selection. No chain-correctness invariant was violated (the txn would have been REJECTED via the miller's exclusion before reaching a block), but the inconsistency was a latent foot-gun: a future refactor that swapped the miller's `>` for `>=` (or removed `discard_expired_pending_txns`'s `<=` branch) would have let the txn drift to a state where the block layer accepts what the miller silently rejected. Remediated: a single `txn_is_expired(txn_ts, reference_dt)` helper in `src/cancelchain/block.py` now defines the open boundary; `Block.validate_transaction` (behavior-identical), `Node.discard_expired_pending_txns`, and `Miller.pending_chain_txns` all call it, and the `PendingTxnDAO.json_datas` SQL (`timestamp >= cutoff`) carries a cross-ref comment. Regression: `test_a7_e_txn_timeout_boundary_inconsistency` plus `test_txn_is_expired_boundary`, `test_pending_chain_txns_boundary_alive`, and `test_pending_chain_txns_expired_excluded`.
```

(f) Remediation-priority section (heading line 1169, body line 1171). Three substring swaps:

Change the heading — find exactly:
```
### 5. A7.e (Low) — pick one `TXN_TIMEOUT` comparison operator
```
Replace with:
```
### 5. A7.e (Low) — ✅ Implemented — single `txn_is_expired()` definition
```

Prefix the body paragraph (line 1171, beginning `The fix lives at three sites:`) with `✅ **Implemented.** `.

In that body, replace the acceptance-signal sentence — find exactly:
```
Acceptance signal: `test_a7_e_txn_timeout_boundary_inconsistency` flips from xfail to pass.
```
Replace with:
```
Acceptance signal: `test_a7_e_txn_timeout_boundary_inconsistency` is now a passing regression test (xfail removed), plus `test_txn_is_expired_boundary`, `test_pending_chain_txns_boundary_alive`, and `test_pending_chain_txns_expired_excluded`. Implemented as a shared `txn_is_expired()` helper rather than three inline operator swaps, so the boundary is defined once and cannot drift again.
```

- [ ] **Step 2: Update the ROADMAP**

(a) Open-count prose (line 48):
Find: `Two open findings from the 2026-05-29 verification pipeline audit (A2.e, A4.c, A7.b, and A7.h are closed; see Closed items).`
Replace: `One open finding from the 2026-05-29 verification pipeline audit (A2.e, A4.c, A7.b, A7.h, and A7.e are closed; see Closed items).`

(b) Remove the A7.e numbered item (line 52, beginning `1. **A7.e — Low — `TXN_TIMEOUT` comparison operator inconsistency.**`) and renumber so A1.f becomes `1.`.

(c) At the END of the "## Closed items (historical reference)" section (after the A7.h bullet), add:
```
- ✅ **Audit finding A7.e — `TXN_TIMEOUT` comparison-operator inconsistency** — closed by docs PR [#<N_docs>](https://github.com/gumptionthomas/cancelchain/pull/<N_docs>) (design+plan) and impl PR [#<N_impl>](https://github.com/gumptionthomas/cancelchain/pull/<N_impl>). The expiry boundary is now defined once by a `txn_is_expired(txn_ts, reference_dt)` helper in `block.py` (expired ⟺ strictly older than `TXN_TIMEOUT`; open boundary). `Block.validate_transaction` (behavior-identical), `Node.discard_expired_pending_txns`, and `Miller.pending_chain_txns` route through it; the `PendingTxnDAO.json_datas` SQL already matched and carries a cross-ref comment. No schema change. Brings audit severity to 0 Critical / 0 High / 0 Medium / 1 Low.
```
The `#<N_docs>` / `#<N_impl>` placeholders are filled when the PRs open (docs PR number on this branch; impl PR number after it opens). **Do NOT modify the historical severity tallies in earlier closed-item bullets** (A4.c "4 Low", A7.b "3 Low", A7.h "2 Low").

- [ ] **Step 3: Commit**

```bash
git add docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md docs/superpowers/ROADMAP.md
git commit -m "docs(a7e): mark A7.e remediated; update audit + ROADMAP counts"
```

---

### Task 3: Final gates

**Files:** none (verification only)

- [ ] **Step 1: Full suite**

Run: `COLUMNS=200 uv run pytest`
Expected: **254 passed, 1 xfailed, 1 skipped** (baseline 250 passed / 2 xfailed / 1 skipped; this PR adds `test_txn_is_expired_boundary`, `test_pending_chain_txns_boundary_alive`, and `test_pending_chain_txns_expired_excluded` as passing and moves `test_a7_e…` from xfailed to passed). No unexpectedly-passing xfails. If the baseline differs, the invariant is: +3 net new passing tests, and A7.e moved from xfailed to passed (xfailed drops by exactly 1, leaving only A1.f).

- [ ] **Step 2: xfail cross-check**

Run: `uv run pytest --runxfail tests/test_verification_audit.py -q`
Expected: A7.e now passes; only `test_a1_f_mined_txid_replay_into_pending` surfaces as a failure under `--runxfail`.

- [ ] **Step 3: Lint + types**

Run:
```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy
```
Expected: all clean. No schema change ⇒ no migration / `db check` impact.

- [ ] **Step 4: Confirm no migration drift**

Run: `git status --porcelain src/cancelchain/migrations/`
Expected: empty.

---

## Notes for the implementer

- The helper must be named `txn_is_expired` and take `(txn_timestamp_dt, reference_dt)` in that order. Define it at module level in `block.py` so `node.py`/`miller.py` can import it (no circular import — they already import from `block.py`).
- `Block.validate_transaction`'s change must be behavior-identical (the same `<` expression, now inside the helper). Do NOT change the boundary direction there — it is consensus code.
- Leave `TXN_TIMEOUT` defined in `block.py` (the helper uses it). `api.py` still imports/uses `TXN_TIMEOUT` for the SQL cutoff — do NOT touch `api.py`.
- This is a fix PR: no adjacent refactors, no `TXN_TIMEOUT` value change, no reference-time unification (block-ts vs `now()` are deliberate). The pre-existing `test_create_wallet` terminal-width bug stays out of scope.
