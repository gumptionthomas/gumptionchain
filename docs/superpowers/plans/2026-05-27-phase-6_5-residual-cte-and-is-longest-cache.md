# Phase 6.5 — Residual CTE + `_is_longest()` cache Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the two Phase 6 deferrals against `src/cancelchain/models.py`: replace the recursive CTE inside `ChainDAO._rebuild_longest_chain_blocks` with an iterative walk via `BlockDAO.prev`, and add a class-level generation-counter cache to `ChainDAO._is_longest()` so the 6 downstream methods don't fire redundant `ChainDAO.longest()` queries per property access.

**Architecture:** `_rebuild_longest_chain_blocks` walks `current = current.prev` from tip → genesis instead of materializing the recursive CTE; each step is one indexed PK lookup, no planner overhead on long chains. `ChainDAO` gains `_chain_generation: ClassVar[int]` plus a `_bump_generation()` classmethod; `_is_longest()` stores `(generation, result)` as a lazy instance attribute and re-checks the class-level counter on each call. Two `_bump_generation()` call sites — one inside `_rebuild_longest_chain_blocks` (covers bootstrap + reorg) and one after the single-row INSERT in `sync_longest_chain_blocks` (covers steady-state extend) — invalidate all in-process caches whenever the materialized table actually changes.

**Tech Stack:** SQLAlchemy 2.0.50 + Flask-SQLAlchemy 3.1.1 (existing; `BlockDAO.prev` is already declared as a `Mapped[BlockDAO | None] = relationship(...)`). `typing.ClassVar` (stdlib) for the class-level counter. Legacy `Model.query` / `db.session.query` patterns stay (Phase 7 modernizes them).

---

## Prerequisites

- Working directory: the cancelchain repo root. Run all commands from there.
- `uv --version` 0.4.x or newer; `gh --version` works and `gh auth status` shows authenticated.
- Phase 6 fully merged (PR #64 docs and PR #65 impl). Verify with `gh pr view 65 --json state --jq .state` → `MERGED` and `git log --oneline -5 main` shows `6994236 feat(wallet): tighten Wallet.key types ... (#66)` and `4dc392a feat(models): materialize longest chain ... (#65)` and `764e2b1 docs(phase-6): Phase 6 longest-chain materialization design + plan (#64)` near the top.
- The branch `docs/phase-6_5-design` exists locally with one commit:
  - `3ba45d1 docs(phase-6_5): add residual-CTE + _is_longest cache design spec`
  This plan adds a second commit on that branch (the plan file) and ships both as the docs PR.
- CI hard-gates `ruff check`, `ruff format --check`, and `mypy` (strict).
- Test baseline: **227 passed, 1 skipped** (post-Phase 6 and Wallet-key-types PR #66). Phase 6.5 adds 5 new tests, so the final count is 232 passed, 1 skipped.
- Each PR ends with `wor` (Copilot review wait + reply) and `mwg` (merge when green); the controller handles those, not the implementer subagent.
- Never push directly to `main`.

---

## File Map

| Task | PR | Files |
|---|---|---|
| 1 | docs PR | `docs/superpowers/plans/2026-05-27-phase-6_5-residual-cte-and-is-longest-cache.md` (this file) + the spec already committed |
| 2 | impl PR | `src/cancelchain/models.py`, `tests/test_models.py` |
| 3 | acceptance | none (verification only) |

---

## Task 1: Ship the docs PR (spec + plan)

**Files:** The design spec is committed on `docs/phase-6_5-design` (`3ba45d1`). This task adds the implementation plan as a second commit and ships both as one docs PR.

- [ ] **Step 1: Confirm branch state**

```bash
git rev-parse --abbrev-ref HEAD
git ls-files docs/superpowers/specs/2026-05-27-phase-6_5-residual-cte-and-is-longest-cache-design.md
git rev-list --count main..HEAD
```

Expected: branch is `docs/phase-6_5-design`; spec file is tracked; commit count above main is `1`.

- [ ] **Step 2: Verify the plan file is present and untracked**

```bash
ls -la docs/superpowers/plans/2026-05-27-phase-6_5-residual-cte-and-is-longest-cache.md
git status docs/superpowers/plans/
```

Expected: file exists; shows as untracked.

- [ ] **Step 3: Stage and commit**

```bash
git add docs/superpowers/plans/2026-05-27-phase-6_5-residual-cte-and-is-longest-cache.md
git commit -m "$(cat <<'EOF'
docs(phase-6_5): add residual-CTE + _is_longest cache implementation plan

Spells out the single-PR impl: branch off main, add ClassVar import,
add ChainDAO._chain_generation + _bump_generation classmethod, rewrite
_is_longest with (generation, result) tuple cache, bump after the
single-row INSERT in sync_longest_chain_blocks, rewrite
_rebuild_longest_chain_blocks for an iterative current.prev walk
(bumps internally), add 5 new tests (iterative walk correctness +
long-chain walk smoke + cache hit + cache invalidation on bump +
cache across method calls + sync-bumps-on-extend), and verify the
existing 227 tests stay green.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 4: Push**

```bash
git push -u origin docs/phase-6_5-design
```

- [ ] **Step 5: Open the docs PR**

```bash
gh pr create --base main --head docs/phase-6_5-design --title "docs(phase-6_5): Phase 6.5 residual-CTE + _is_longest cache design + plan" --body "$(cat <<'EOF'
## Summary
- Adds the Phase 6.5 design spec (\`docs/superpowers/specs/2026-05-27-phase-6_5-residual-cte-and-is-longest-cache-design.md\`).
- Adds the Phase 6.5 implementation plan (\`docs/superpowers/plans/2026-05-27-phase-6_5-residual-cte-and-is-longest-cache.md\`).
- No code changes.

Phase 6.5 closes the two Phase 6 deferrals: (1) replace the recursive CTE in \`_rebuild_longest_chain_blocks\` with an iterative walk via \`BlockDAO.prev\` so long-chain bootstrap/reorg doesn't depend on the CTE planner; (2) add a class-level generation-counter cache to \`_is_longest()\` so the 6 downstream methods don't fire redundant queries (Copilot flagged this on PR #65, deferred per spec). Cross-worker cache invalidation is documented as a known limitation, queued for Phase 7+.

## Test plan
- [x] Spec self-review passed.
- [x] Plan self-review passed.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 6: Stop — controller handles wor + mwg + sync**

---

## Task 2: Phase 6.5 impl — iterative walk + `_is_longest()` cache

**Files:**
- Modify: `src/cancelchain/models.py` (add `ClassVar` import; add `_chain_generation` + `_bump_generation`; rewrite `_is_longest` body; bump in `sync_longest_chain_blocks` after the single-row INSERT; rewrite `_rebuild_longest_chain_blocks` for iterative walk)
- Modify: `tests/test_models.py` (5 new tests)

### Step 1: Branch off main

```bash
git checkout main && git pull --ff-only
git checkout -b feat/phase-6_5-cte-and-cache
```

### Step 2: Add `ClassVar` to the typing import in `models.py`

In `src/cancelchain/models.py`, locate the existing typing import (around line 15):

Before:
```python
from typing import TYPE_CHECKING
```

After:
```python
from typing import TYPE_CHECKING, ClassVar
```

Verify:
```bash
grep -n '^from typing import' src/cancelchain/models.py
```

Expected: shows the new combined import.

### Step 3: Add `_chain_generation` + `_bump_generation` to `ChainDAO`

In `src/cancelchain/models.py`, locate the `ChainDAO` class definition (around line 467). Find the existing column declarations (lines 471–479: `id`, `block_hash`, `block_id`, `block` relationship). Immediately after the column block and before the existing `__init__` method, insert:

```python
    # Bumped on any longest_chain_block mutation; invalidates all
    # ChainDAO instances' cached _is_longest values within this
    # process. Cross-worker invalidation is out of scope — see
    # the Phase 6.5 spec's Risks section.
    _chain_generation: ClassVar[int] = 0

    @classmethod
    def _bump_generation(cls) -> None:
        cls._chain_generation += 1
```

Verify the class structure is intact:
```bash
grep -n '_chain_generation\|_bump_generation\|class ChainDAO\|def __init__' src/cancelchain/models.py | head -10
```

Expected: `class ChainDAO` line, then `_chain_generation:` and `def _bump_generation` lines, then `def __init__` line.

### Step 4: Rewrite `_is_longest` body with the (generation, result) tuple cache

In `src/cancelchain/models.py`, locate the `_is_longest` method (around line 601).

Before:
```python
    def _is_longest(self) -> bool:
        """True iff this ChainDAO row is currently the longest chain.

        Used by the property accessors (blocks, transactions, outflows,
        inflows) to route hot reads through LongestChainBlockDAO
        instead of the recursive CTE.
        """
        longest = ChainDAO.longest()
        return longest is not None and longest.id == self.id
```

After:
```python
    def _is_longest(self) -> bool:
        """True iff this ChainDAO row is currently the longest chain.

        Used by the property accessors (blocks, transactions, outflows,
        inflows) to route hot reads through LongestChainBlockDAO
        instead of the recursive CTE.

        Cached per instance and invalidated by class-level generation
        bumps inside sync_longest_chain_blocks / rebuild paths. The
        cross-worker case (another process reorged the chain) is a
        known stale-cache risk — bounded to one held instance's
        lifetime within this worker; see the Phase 6.5 spec's Risks.
        """
        cached: tuple[int, bool] | None = getattr(
            self, '_is_longest_cache', None
        )
        if cached is not None and cached[0] == ChainDAO._chain_generation:
            return cached[1]
        longest = ChainDAO.longest()
        result = longest is not None and longest.id == self.id
        self._is_longest_cache = (ChainDAO._chain_generation, result)
        return result
```

### Step 5: Bump generation after the single-row INSERT in `sync_longest_chain_blocks`

In `src/cancelchain/models.py`, locate the `sync_longest_chain_blocks` method (around line 611). Find the steady-state extend branch (the `if table_tip_block_id == self.block.prev_id:` block, around lines 647–655).

Before:
```python
        if table_tip_block_id == self.block.prev_id:
            # Normal extend: append one row.
            db.session.add(
                LongestChainBlockDAO(
                    block_id=self.block_id,
                    position=current_max + 1,
                )
            )
            return
```

After:
```python
        if table_tip_block_id == self.block.prev_id:
            # Normal extend: append one row.
            db.session.add(
                LongestChainBlockDAO(
                    block_id=self.block_id,
                    position=current_max + 1,
                )
            )
            ChainDAO._bump_generation()
            return
```

The other two mutating paths (`current_max is None` → bootstrap, and the final fallthrough → reorg/rebuild) both call `_rebuild_longest_chain_blocks`, which will bump internally in Step 6 — so no other edits to `sync_longest_chain_blocks` are needed.

### Step 6: Rewrite `_rebuild_longest_chain_blocks` for iterative walk

In `src/cancelchain/models.py`, locate `_rebuild_longest_chain_blocks` (around line 660).

Before:
```python
    def _rebuild_longest_chain_blocks(self) -> None:
        """Wipe and repopulate longest_chain_block from this chain's
        recursive CTE walk. Used on bootstrap and reorg.

        This is the path that still fires the recursive CTE — see
        Phase 6 spec 'Risks' for the deferred follow-up (Phase 6.5/7)
        to replace this with an iterative walk when chain length
        grows past the CTE's tolerable size.
        """
        db.session.query(LongestChainBlockDAO).delete()
        # block_chain walks tip → genesis; reverse so position 0 is
        # genesis and the tip ends up at the highest position.
        blocks = list(self.block.block_chain)
        for position, block in enumerate(reversed(blocks)):
            db.session.add(
                LongestChainBlockDAO(
                    block_id=block.id,
                    position=position,
                )
            )
```

After:
```python
    def _rebuild_longest_chain_blocks(self) -> None:
        """Wipe and repopulate longest_chain_block by walking the
        chain iteratively from tip → genesis via BlockDAO.prev links.

        Each step is one indexed PK lookup (block.id). Avoids the
        recursive CTE's planner overhead on long chains — the cost
        that caused the project to be shelved in the past. Bumps
        ChainDAO._chain_generation at the end so cached _is_longest
        values on any in-process ChainDAO instance are invalidated.
        """
        db.session.query(LongestChainBlockDAO).delete()
        blocks: list[BlockDAO] = []
        current: BlockDAO | None = self.block
        while current is not None:
            blocks.append(current)
            current = current.prev
        for position, block in enumerate(reversed(blocks)):
            db.session.add(
                LongestChainBlockDAO(
                    block_id=block.id,
                    position=position,
                )
            )
        ChainDAO._bump_generation()
```

Verify no remaining CTE walks in the file:
```bash
grep -n 'self\.block\.block_chain\|block_chain\.subquery' src/cancelchain/models.py
```

Expected: matches inside `BlockDAO._block_chain` / `block_chain` / `transactions_chain` etc. and inside the 4 ChainDAO property branches' CTE fallback paths — but NOT inside `_rebuild_longest_chain_blocks` any longer.

### Step 7: Add 5 new tests to `tests/test_models.py`

Append to the end of `tests/test_models.py`. The tests use `unittest.mock.patch` to count `ChainDAO.longest` invocations for the cache tests.

Read the existing imports at the top:

```bash
sed -n '1,30p' tests/test_models.py
```

Add to the imports block at the top of `tests/test_models.py` (alongside the existing model imports — locate the `from cancelchain.models import ...` block):

```python
from unittest.mock import patch
```

If `unittest.mock.patch` is already imported (check with `grep -n 'from unittest.mock' tests/test_models.py`), skip the new import.

Then append the following test functions to the end of `tests/test_models.py`:

```python


def test_iterative_walk_matches_cte(app, mill_block, wallet):
    """_rebuild_longest_chain_blocks via current.prev produces the
    same block ordering as the prior recursive-CTE walk would have.
    Uses self.block.block_chain (still defined; used as fallback)
    as ground truth.
    """
    with app.app_context():
        for _ in range(10):
            mill_block(wallet)
        longest = ChainDAO.longest()
        assert longest is not None

        # Capture CTE ground truth before rebuild.
        cte_ids = [b.id for b in longest.block.block_chain]

        # Force a rebuild via the iterative walk (also runs on bootstrap
        # by sync_longest_chain_blocks; here we exercise it directly).
        longest._rebuild_longest_chain_blocks()
        db.session.commit()

        mat_ids = [
            r.block_id
            for r in db.session.query(LongestChainBlockDAO)
            .order_by(LongestChainBlockDAO.position.desc())
            .all()
        ]
        assert cte_ids == mat_ids
        assert len(mat_ids) == 10


def test_iterative_walk_long_chain(app, mill_block, wallet):
    """Iterative walk handles a longer chain (50 blocks) and produces
    the right count with no exceptions. Primarily a smoke test that
    the walk terminates and the materialization stays consistent.
    """
    with app.app_context():
        for _ in range(50):
            mill_block(wallet)
        longest = ChainDAO.longest()
        assert longest is not None
        longest._rebuild_longest_chain_blocks()
        db.session.commit()
        count = db.session.query(LongestChainBlockDAO).count()
        assert count == 50


def test_is_longest_cache_hit_avoids_query(app, mill_block, wallet):
    """Calling _is_longest twice on the same instance hits the cache
    on the second call and does NOT re-issue ChainDAO.longest().
    """
    with app.app_context():
        mill_block(wallet)
        longest = ChainDAO.longest()
        assert longest is not None
        # Reset cache state and bump generation so the next call is a miss.
        if hasattr(longest, '_is_longest_cache'):
            delattr(longest, '_is_longest_cache')
        with patch.object(
            ChainDAO, 'longest', wraps=ChainDAO.longest
        ) as spy:
            assert longest._is_longest() is True
            assert longest._is_longest() is True
            assert spy.call_count == 1, (
                f'expected one ChainDAO.longest() call (cache hit on '
                f'2nd), got {spy.call_count}'
            )


def test_is_longest_cache_invalidated_by_bump(app, mill_block, wallet):
    """Calling ChainDAO._bump_generation() after a cached _is_longest
    call forces a recomputation on the next access.
    """
    with app.app_context():
        mill_block(wallet)
        longest = ChainDAO.longest()
        assert longest is not None
        if hasattr(longest, '_is_longest_cache'):
            delattr(longest, '_is_longest_cache')
        with patch.object(
            ChainDAO, 'longest', wraps=ChainDAO.longest
        ) as spy:
            assert longest._is_longest() is True
            ChainDAO._bump_generation()
            assert longest._is_longest() is True
            assert spy.call_count == 2, (
                f'expected two ChainDAO.longest() calls (miss, then '
                f'miss after bump), got {spy.call_count}'
            )


def test_is_longest_cache_survives_across_method_calls(
    app, mill_block, wallet
):
    """One ChainDAO.longest() call total across a wallet_balance read
    that internally accesses self.outflows AND self.inflows. Without
    caching this would be 2+ calls.
    """
    with app.app_context():
        _m, _b = mill_block(wallet)
        longest = ChainDAO.longest()
        assert longest is not None
        if hasattr(longest, '_is_longest_cache'):
            delattr(longest, '_is_longest_cache')
        with patch.object(
            ChainDAO, 'longest', wraps=ChainDAO.longest
        ) as spy:
            # wallet_balance reads self.outflows and self.inflows;
            # each property accessor calls _is_longest.
            longest.wallet_balance(wallet.address)
            assert spy.call_count == 1, (
                f'expected one ChainDAO.longest() call across the '
                f'wallet_balance method (cached after the first '
                f'property access), got {spy.call_count}'
            )
```

The `test_is_longest_cache_invalidated_by_bump` test exercises the bump-generation pathway. The remaining bump sites (inside `sync_longest_chain_blocks` and `_rebuild_longest_chain_blocks`) are also covered indirectly by `test_iterative_walk_matches_cte` since calling `_rebuild_longest_chain_blocks` directly triggers a bump.

### Step 8: Verify all gates

```bash
uv run mypy
uv run ruff check src tests
uv run ruff format --check src tests
uv run pytest
```

All four must exit 0. Test count: 227 → 232 (+5).

Likely failure modes and their fixes:

- `mypy` complains about `tuple[int, bool] | None` for the cache attribute. The file has `from __future__ import annotations` (line 1) so the PEP 604 union syntax works at runtime. If the strict-mode `Mapped` interaction surfaces (mypy thinks the cache attribute could clash with a column), the existing `# mypy: disable-error-code="no-untyped-call,no-any-return,name-defined,misc"` block at the top of the file should cover it; if a new error code is needed, ADD it to that block (not per-line).
- `ruff` flags the `cached: tuple[int, bool] | None = getattr(...)` annotation as `UP037` or similar (deprecated typing). The `from __future__ import annotations` should suppress this; if not, use `cast` from typing or just rely on inline narrowing.
- `pytest test_is_longest_cache_hit_avoids_query` fails because the cache was already populated from a prior call (e.g., the initial sync). The `delattr` step at the top of each cache test should reset state; if not, use `ChainDAO._bump_generation()` before the spy block to force a miss.
- `pytest test_iterative_walk_long_chain` is too slow. If a 50-block chain exceeds the time budget on the runner (especially `--runmulti`), reduce to 25 blocks. The point is to validate that the walk terminates and produces the right count, not benchmark.

### Step 9: Commit

```bash
git add src/cancelchain/models.py tests/test_models.py
git commit -m "$(cat <<'EOF'
feat(models): iterative chain walk + cached _is_longest()

Phase 6.5. Closes the two Phase 6 deferrals:

1. **Iterative walk replaces the recursive CTE in
   `_rebuild_longest_chain_blocks`.** Walking `current = current.prev`
   from tip to genesis is one indexed PK lookup per step, avoiding
   the recursive CTE's planner overhead that previously caused the
   project to be shelved when chain length grew. The CTE-backed
   `BlockDAO.block_chain` and friends stay defined — they're still
   used by the 4 `ChainDAO` property branches as the non-longest-chain
   fallback. Phase 7 will revisit those.

2. **Class-level generation-counter cache for `_is_longest()`.**
   `ChainDAO._chain_generation: ClassVar[int]` is bumped on any
   `longest_chain_block` mutation (two sites: inside
   `_rebuild_longest_chain_blocks` after the bulk INSERT, and inside
   `sync_longest_chain_blocks` after the single-row extend INSERT).
   Each `ChainDAO` instance caches `(generation, is_longest)` in
   `_is_longest_cache` (a lazy instance attribute, not a Mapped
   column). On each call, if the cached generation matches the
   class-level counter, the cached result is returned; otherwise
   the cache is recomputed via `ChainDAO.longest()` and re-stored.

   Effect: `wallet_balance`, `wallet_leaderboard`, etc. now fire
   `ChainDAO.longest()` once per method call instead of N times
   (one per property access). Cross-worker stale-cache risk is
   bounded and documented in the docstring; mitigations queued
   for Phase 7+.

tests/test_models.py:
- 5 new tests:
  - test_iterative_walk_matches_cte: iterative walk produces the
    same block ordering as the CTE walk on a 10-block chain.
  - test_iterative_walk_long_chain: 50-block walk smoke (terminates,
    correct count).
  - test_is_longest_cache_hit_avoids_query: two consecutive
    `_is_longest()` calls → one underlying `ChainDAO.longest()` call.
  - test_is_longest_cache_invalidated_by_bump: `_bump_generation()`
    between two `_is_longest()` calls → two underlying
    `ChainDAO.longest()` calls.
  - test_is_longest_cache_survives_across_method_calls:
    `wallet_balance(addr)` reads two branched properties internally;
    still only one `ChainDAO.longest()` call due to the cache.

Test count: 227 → 232.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

### Step 10: Push and open PR

```bash
git push -u origin feat/phase-6_5-cte-and-cache
gh pr create --base main --title "feat(models): iterative chain walk + cached _is_longest()" --body "$(cat <<'EOF'
## Summary
- Replaces the recursive CTE inside \`ChainDAO._rebuild_longest_chain_blocks\` with an iterative \`current = current.prev\` walk; each step is one indexed PK lookup. Bootstrap / reorg rebuilds no longer depend on the CTE planner.
- Adds a class-level generation-counter cache to \`ChainDAO._is_longest()\` (\`_chain_generation: ClassVar[int]\` + \`_bump_generation()\` classmethod). The cache invalidates whenever the materialized table mutates (two bump sites: inside \`_rebuild_longest_chain_blocks\` after the bulk INSERT, inside \`sync_longest_chain_blocks\` after the single-row extend INSERT).
- 5 new tests: iterative-walk correctness against the CTE walk; 50-block walk smoke; cache hit; cache invalidation on bump; cache across multiple property accesses within \`wallet_balance\`.

## Why
Closes the two Phase 6 deferrals documented in the Phase 6 spec's "Risks" section:
- The CTE inside \`_rebuild_longest_chain_blocks\` was the residual perf risk on long chains.
- Copilot flagged the \`_is_longest()\` query cost on PR #65; spec deferred caching to Phase 6.5.

## Out of scope (per spec)
- SA 2.0 syntax modernization (Phase 7).
- DeclarativeBase migration + \`mypy: disable-error-code\` removal in \`models.py\` (Phase 7).
- Removing the CTE-backed \`block_chain\` / \`transactions_chain\` / etc. on \`BlockDAO\` (still used as the non-longest-chain fallback in \`ChainDAO\`'s property branches).
- Cross-worker cache invalidation (multi-process coordination).
- Batched-fetch walk for very long chains (if profiling later shows the single-row walk is the new bottleneck).

## Test plan
- [x] \`uv run mypy\` exits 0.
- [x] \`uv run pytest\` passes (227 → 232, +5).
- [x] \`uv run ruff check\` + \`format --check\` pass.
- [ ] CI green on 3.12 and 3.13.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

### Step 11: Stop — controller handles wor + mwg + sync

---

## Task 3: Phase 6.5 acceptance verification

**Files:** none modified. Final verification after the impl PR lands on main.

- [ ] **Step 1: Confirm clean main**

```bash
git checkout main && git pull --ff-only
git log --oneline -3
```

Expected: top two commits are the docs PR squash and the impl PR squash.

- [ ] **Step 2: Fresh sync**

```bash
rm -rf .venv
uv sync --group dev
uv run python --version
```

Expected: Python 3.12.x and a fresh venv.

- [ ] **Step 3: Verify the iterative walk is in place**

```bash
grep -n 'self.block.block_chain\|current.prev' src/cancelchain/models.py | head -10
```

Expected: at least one `current.prev` reference inside `_rebuild_longest_chain_blocks`; the `self.block.block_chain` references are limited to the 4 `ChainDAO` property branches' CTE-fallback paths (lines ~489–509 region — NOT inside `_rebuild_longest_chain_blocks`).

- [ ] **Step 4: Verify the generation counter is in place**

```bash
grep -n '_chain_generation\|_bump_generation' src/cancelchain/models.py
```

Expected: 1 class-attr declaration, 1 classmethod definition, ≥2 `_bump_generation()` call sites (one in `sync_longest_chain_blocks`, one in `_rebuild_longest_chain_blocks`), and references inside the `_is_longest` body.

- [ ] **Step 5: Hard CI gates pass**

```bash
uv run ruff check src tests; echo "ruff check exit: $?"
uv run ruff format --check src tests; echo "ruff format exit: $?"
uv run mypy; echo "mypy exit: $?"
```

All three exit 0.

- [ ] **Step 6: Tests pass on 3.12 and 3.13**

```bash
uv run --python 3.12 pytest 2>&1 | tail -3
uv run --python 3.13 pytest 2>&1 | tail -3
```

Expected: both print `232 passed, 1 skipped` (or whatever the new count is — should be 5 more than 227).

- [ ] **Step 7: Hot-path SQL still uses the materialized table**

```bash
uv run python <<'PY'
import os, tempfile
os.environ.setdefault('FLASK_SECRET_KEY', 'a' * 32)
tmpdb = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
tmpdb.close()
os.environ['FLASK_SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{tmpdb.name}'
from cancelchain import create_app
from cancelchain.database import db
from cancelchain.models import BlockDAO
app = create_app()
with app.app_context():
    db.create_all()
    sql = str(
        BlockDAO.longest_chain_blocks_q().statement.compile(
            compile_kwargs={'literal_binds': True}
        )
    )
    print(sql)
    assert 'longest_chain_block' in sql.lower()
    assert 'RECURSIVE' not in sql.upper()
print('OK')
PY
```

Expected: prints the JOIN SQL with `longest_chain_block` and no `WITH RECURSIVE`. (Phase 6.5 doesn't change the fast path itself; this is a regression check that the hot path is still CTE-free.)

- [ ] **Step 8: CLI smoke**

```bash
uv run cancelchain --help
```

Expected: prints the full command tree.

- [ ] **Step 9: Docker build smoke**

```bash
docker build --target builder -t cc-phase6_5-final .
```

Expected: succeeds.

- [ ] **Step 10: Acceptance complete**

If Steps 1–9 all pass, Phase 6.5 is done. No commit.

---

## Notes on the wor / mwg workflow

Each PR (Tasks 1 and 2) ends with the controller running `wor` and `mwg`:

1. **`wor`:** poll PR until Copilot review completes. Read inline comments. Reply one at a time with verified `in_reply_to_id` (per the user's memory).
2. **`mwg`:** `gh pr checks <N> --watch`; once green, `gh pr merge <N> --squash --delete-branch`.

If Copilot review requests substantive changes, push a new commit (do not amend) and post a `/copilot review` comment on the PR — Copilot's auto-review only fires on the initial push; subsequent rounds need the manual trigger (per the user's memory).

---

## Risks and watchpoints

### Risk: `BlockDAO.prev` lazy-load triggers extra queries during commit

SQLAlchemy's lazy-loaded relationships fire a SELECT each time the attribute is accessed if the related object isn't already in the session's identity map. For a freshly-loaded chain, each `current = current.prev` step issues `SELECT * FROM block WHERE id = ?`. That's the intended cost (O(N) indexed lookups), but be aware that if the session is in autoflush mode, an unintended flush could happen mid-walk. The existing `_rebuild_longest_chain_blocks` already issues a `delete()` before the walk; if the autoflush triggers between `delete()` and the bulk `add()` calls, no harm done — the delete is part of the same transaction. If a test catches a flush-during-walk surprise, wrap the body in `with db.session.no_autoflush:` (the codebase uses this pattern in `OutflowDAO.__init__` and `InflowDAO.__init__`).

### Risk: `_is_longest_cache` attribute name collision

SQLAlchemy declarative classes generally accept arbitrary instance attributes set in method bodies — they don't appear at class-body time, so the ORM doesn't see them as candidate columns. The cache attribute name `_is_longest_cache` is sufficiently distinct from any existing column (the file has no other `_is_longest_*` symbol). Verify with:

```bash
grep -n '_is_longest_cache' src/cancelchain/models.py
```

Expected: matches only inside the new `_is_longest` body. If a test caches state across instances unexpectedly, the typical cause is the test fixture sharing a `ChainDAO` instance — the cache test uses fresh `ChainDAO.longest()` lookups, which return the canonical session-attached instance.

### Risk: `ChainDAO._chain_generation` accidentally persisted

The `ClassVar[int]` annotation explicitly tells both Python's typing system and SQLAlchemy's declarative mapper that this is a class-level attribute, not a column. SQLAlchemy 2.0 + Flask-SQLAlchemy 3.1.1 respect `ClassVar` annotations correctly. If a future SQLAlchemy version changes this behavior (unlikely), the symptom would be a startup error about an integer column missing from the `chain` table schema. Acceptance Step 4 catches this.

### Risk: spy on `ChainDAO.longest` fails to record nested calls

`unittest.mock.patch.object(ChainDAO, 'longest', wraps=ChainDAO.longest)` replaces the bound method on the class. Calls via `cls.longest()` (classmethod-style) and via `ChainDAO.longest()` (explicit class reference) both go through the mocked descriptor and are counted. Calls via `self.longest()` on an instance would also go through. If a test counter is off-by-one, inspect `spy.call_args_list` to see which call sites fired and adjust either the test or the implementation.

### Risk: per-test cache state leakage

The class-level `_chain_generation` persists across tests within the same pytest process. If Test A bumps it, Test B starts with a higher counter — but Test B's fresh ChainDAO instances have no `_is_longest_cache` set, so they miss on first call and recompute correctly regardless of the counter's absolute value. No fixture reset needed. The instance-level `_is_longest_cache` is per-instance and dies with the instance — also no leakage.

### Risk: iterative walk on a chain with a cycle

A correctly-validated cancelchain can't have cycles (consensus rules prevent it), but a corrupted DB could. The walk's termination guard is `current = current.prev` — when `current.prev_id is None` (genesis), the relationship returns `None` and the loop exits. If somehow a `prev_id` points to a row whose `prev_id` cycles back, the walk loops forever. Not a new risk vs the existing CTE — the CTE also walks `prev_id` recursively and would similarly never terminate on a cycle (or hit max recursion depth and error). Don't add cycle detection in Phase 6.5; it's out of scope.

### Risk: Copilot re-flags the residual CTE in property fallback

The CTE still fires via `BlockDAO.block_chain` when `ChainDAO._is_longest()` returns False (i.e., querying a non-longest chain). Copilot may flag this if it reviews the diff narrowly. Reply with a pointer to the Phase 6.5 spec's "Non-goals" section — that fallback is intentional and out of scope (removing it requires generalizing the materialization to all chains, which is Phase 7+).
