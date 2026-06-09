# EGU #164 indexed longest() + prune stale forks — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `ChainDAO.longest()` cost independent of F (fork-tip count) via a denormalized indexed `tip_idx`, and bound F by pruning stale fork `ChainDAO` rows on canonical block-add — preserving the exact consensus tiebreak.

**Architecture:** `ChainDAO` gains an indexed `tip_idx` (the tip block's height), maintained wherever the tip is set. `longest()` becomes a `MAX(tip_idx)` index lookup + the existing (timestamp, block_hash) tiebreak over only the tied rows. Pruning deletes non-canonical rows >`FORK_PRUNE_DEPTH` behind, hooked into `sync_longest_chain_blocks`'s is-longest branch (chain rows only — no cascade to blocks). Schema change folded into the baseline migration (greenfield, no backfill).

**Spec:** `docs/superpowers/specs/2026-06-09-egu-164-chaindao-prune-indexed-longest-design.md`

**Reference (read first):**
- `src/gumptionchain/models.py` — `ChainDAO` (`:701`), `__init__`/`set_block_hash`, `chains()`/`longest()` (`:1141`), `_is_longest()`, `sync_longest_chain_blocks` (the `if not self._is_longest(): return` guard + is-longest body), `count()`. `BlockDAO.idx` is `Mapped[int]` (non-null).
- `src/gumptionchain/migrations/versions/63d32cd7621a_initial_schema.py` — `op.create_table('chain', ...)` (`:80`) + the `batch_alter_table('chain')` index block (`:87`). EDIT THIS (greenfield rule), don't stack a new migration.
- `src/gumptionchain/config.py` — `EnvAppSettings` (`MAX_CHAIN_FILL_DEPTH`/`SYNC_BATCH_SIZE` fields to mirror).
- `src/gumptionchain/chain.py` — `Chain.to_db()` calls `dao.sync_longest_chain_blocks()`.
- Memory: greenfield — fold migrations into the single baseline (rev `63d32cd7621a`), don't stack.

---

## PR 1 — `tip_idx` column + indexed `longest()`

Branch: `feat/egu-164-indexed-longest` off fresh `main`.

### Task 1: add the `tip_idx` column (model + baseline migration)

**Files:** `src/gumptionchain/models.py`, `src/gumptionchain/migrations/versions/63d32cd7621a_initial_schema.py`

- [ ] **Step 1: Model** — add to `ChainDAO`:
  ```python
  tip_idx: Mapped[int] = mapped_column(Integer, index=True)
  ```
  Maintain it where the tip is set:
  - in `__init__`, after `self.block = block_dao or BlockDAO.get(block_hash)`, add `self.tip_idx = self.block.idx`.
  - in `set_block_hash`, after `self.block = BlockDAO.get(block_hash)`, add `self.tip_idx = self.block.idx`.
  (`self.block.idx` is a non-null `int`.)

- [ ] **Step 2: Baseline migration** — in `63d32cd7621a_initial_schema.py`, add `sa.Column('tip_idx', sa.Integer(), nullable=False)` to the `chain` `create_table`, and a `batch_op.create_index(batch_op.f('ix_chain_tip_idx'), ['tip_idx'], unique=False)` alongside the existing chain indexes.

- [ ] **Step 3: db check** — `uv run gumptionchain db check` passes (model == migration). Commit: `feat(models): denormalized indexed tip_idx on ChainDAO`.

### Task 2: rewrite `longest()` (TDD)

**Files:** `src/gumptionchain/models.py`, `tests/test_models.py`

- [ ] **Step 1: Failing/parity test** — build a multi-fork fixture (mirror the existing `test_longest_chain_block_non_longest_extend_noop` fork-building pattern: chain_a + chain_b, plus a same-height tie). Assert:
  - `ChainDAO.longest()` returns the chain with the **highest tip idx**;
  - on a same-tip-idx **tie**, returns the one with the (earliest timestamp, then lowest block_hash) tip — i.e. the SAME row the old `chains().first()` would (you can compute the expected directly from the fork blocks);
  - empty DB → `None`.

- [ ] **Step 2: Implement** the MAX-subquery `longest()`:
  ```python
  @classmethod
  def longest(cls) -> ChainDAO | None:
      max_idx = db.select(db.func.max(cls.tip_idx)).scalar_subquery()
      return (  # type: ignore[no-any-return]
          db.session.execute(
              db.select(cls)
              .join(cls.block)
              .where(cls.tip_idx == max_idx)
              .order_by(BlockDAO.timestamp, BlockDAO.block_hash)
          )
          .scalars()
          .first()
      )
  ```
  Leave `chains()` and `_is_longest()` unchanged (chains() still backs the browser /chains view; _is_longest() benefits from the cheaper longest()).

- [ ] **Step 3: Run** → PASS. Confirm the existing materialization/longest tests (`test_unspent_outflows`, `test_longest_chain_block_*`, the oracle-fork tests) still pass (they exercise reorg + longest selection). `uv run mypy`.

- [ ] **Step 4: tip_idx maintenance test** — extending the canonical chain advances the in-place row's `tip_idx`; a fork creates a new row whose `tip_idx` is the fork tip's height. Run full gates. Commit: `feat(models): indexed longest() via tip_idx MAX subquery`. Open PR.

---

## PR 2 — prune stale fork rows

Branch: `feat/egu-164-prune-forks` off fresh `main` (after PR 1).

### Task 3: `FORK_PRUNE_DEPTH` config

**Files:** `src/gumptionchain/config.py`

- [ ] Add `FORK_PRUNE_DEPTH: int = field(default=100)` to `EnvAppSettings` (env `GC_FORK_PRUNE_DEPTH`). Commit: `feat(config): FORK_PRUNE_DEPTH for stale-fork pruning`.

### Task 4: prune on canonical block-add (TDD)

**Files:** `src/gumptionchain/models.py`, `tests/test_models.py`

- [ ] **Step 1: Failing test** — set `app.config['FORK_PRUNE_DEPTH'] = 2`; build a fork at height H (a `ChainDAO` row at tip_idx H that loses), then advance the canonical chain to height > H + 2 (mill several blocks). Assert:
  - the stale fork's `ChainDAO` row is **deleted** (`ChainDAO.count()` reflects it; or query by the fork tip hash → None);
  - the canonical `ChainDAO` row remains;
  - a fork **within** depth (tip_idx >= canonical_tip_idx - 2) is **not** pruned;
  - the fork's **`BlockDAO` rows still exist** (`BlockDAO.get(fork_tip_hash)` is not None) — no cascade — and the materialization/ancestry are intact.

- [ ] **Step 2: Implement** — add `from flask import current_app` to `models.py` (runs in app context here). Add:
  ```python
  def _prune_stale_forks(self) -> None:
      # Drop non-canonical fork rows whose tip is too far behind to win a
      # reorg. Deletes `chain` rows only — no cascade to `block` (orphan
      # blocks remain for provenance / double-spend / fill_chain).
      depth = current_app.config['FORK_PRUNE_DEPTH']
      db.session.execute(
          db.delete(ChainDAO).where(
              ChainDAO.id != self.id,
              ChainDAO.tip_idx < self.tip_idx - depth,
          )
      )
  ```
  Call `self._prune_stale_forks()` inside `sync_longest_chain_blocks`, in the **is-longest path** (i.e. after the early `if not self._is_longest(): return` — `self` is the canonical chain there, with a current `tip_idx`). Place it after the materialization is updated (end of the method) so a prune can't interfere with the reorg walk.

- [ ] **Step 3: Run** → PASS. Confirm no existing reorg/materialization test regresses (prune only removes rows far behind the canonical tip; the within-depth fork tests must still pass). `uv run mypy`.

- [ ] **Step 4: Full gates** (`uv run ruff format src tests && uv run ruff check src tests && uv run mypy && uv run pytest`) + `uv run gumptionchain db check`. Commit: `feat(models): prune stale ChainDAO fork rows on canonical add`. Open PR.

---

## Final

After both PRs merge: final reviewer over the combined diff (focus the `longest()` tiebreak equivalence + the prune safety/no-cascade); update the EGU checklist (#190) to mark #164. Note the deferred milling-round `longest()` caching as a possible follow-up if profiling shows it.
