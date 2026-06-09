# EGU #164 — indexed `longest()` + prune stale fork rows

**Date:** 2026-06-09
**Issue:** #164 (`ChainDAO.longest()` re-sorts all fork tips on every add/read; stale fork rows never pruned). EGU readiness (#151), launch checklist (#190).
**Status:** design approved

## Goal

Make `ChainDAO.longest()` cost **independent of F** (the number of `ChainDAO`
fork-tip rows) and **bound F** so the `chain` table doesn't grow forever — both
without changing the canonical-chain winner rule. Correctness is unaffected;
this is a slow-growing perf item that worsens under EGU 1b's faster blocks.

## Background (from the audit map)

- `longest() = SELECT chain JOIN block ORDER BY block.idx DESC, block.timestamp
  ASC, block.block_hash ASC` → `.first()` — sorts **all** F chain rows (no
  index covers the join+sort because `chain` doesn't store the tip's idx).
  Called on every API request and every milling round (the hot path).
- Winner rule = **pure tip height** (`block.idx`), tiebroken by (timestamp asc,
  block_hash asc). The tiebreak is **consensus-critical** (nodes must agree on
  the canonical tip) and must be preserved exactly.
- A `ChainDAO` row's tip updates **in place** on extend (`set_block_hash`); a new
  row is born only on a fork (a block whose parent isn't any chain's tip).
  Stale fork rows are **never deleted**.
- Pruning a fork's `ChainDAO` row is **safe**: no cascade to `BlockDAO`, so
  orphan blocks remain (provenance / double-spend detection / `fill_chain` /
  ancestry all read `BlockDAO`, not fork `ChainDAO` rows). The only effect: a
  block later building on a pruned fork tip starts a fresh `ChainDAO` row.
- There is **no `CONFIRMATION_DEPTH`** today; the prune depth is a new knob.

## Part A — indexed `longest()` via a denormalized `tip_idx`

Add `tip_idx: Mapped[int]` to `ChainDAO` (the tip block's height), maintained
wherever the tip is set:
- `ChainDAO.__init__(block_hash, block_dao=None)` — after resolving `self.block`,
  set `self.tip_idx = self.block.idx`.
- `ChainDAO.set_block_hash(block_hash)` — after re-resolving `self.block`, set
  `self.tip_idx = self.block.idx`.

Index it: `index=True` on the column (and the baseline migration, see Migration).

Rewrite `longest()` to a MAX-subquery + tiebreak over ties:

```python
@classmethod
def longest(cls) -> ChainDAO | None:
    max_idx = db.select(db.func.max(cls.tip_idx)).scalar_subquery()
    return (
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

- `MAX(tip_idx)` is served by the `tip_idx` index — O(log F), no full sort.
- The outer query joins `block` only for the rows **at** the max tip_idx
  (usually exactly 1; rarely a few near-simultaneous tied tips), preserving the
  **exact** existing tiebreak (timestamp asc, block_hash asc). Empty table → no
  rows → `None`.

`chains()` is **kept unchanged** for the browser `/chains` display (paginated,
shows all tips) — it's not the hot path, and after pruning F is bounded.
`longest()` no longer depends on `chains()`.

`_is_longest()` is **unchanged** — it calls `longest()`, which is now cheap, so
it benefits automatically. (No need to rewrite it; the generation-cache stays.)

## Part B — prune stale fork rows on block-add

When a chain becomes/extends the canonical chain, drop non-canonical fork rows
whose tip is more than `FORK_PRUNE_DEPTH` blocks behind:

```python
def _prune_stale_forks(self) -> None:
    depth = current_app.config['FORK_PRUNE_DEPTH']
    db.session.execute(
        db.delete(ChainDAO).where(
            ChainDAO.id != self.id,
            ChainDAO.tip_idx < self.tip_idx - depth,
        )
    )
```

- Called from `sync_longest_chain_blocks`'s **is-longest branch** (after it
  updates the materialization) — the one spot that already knows `self` is the
  canonical chain and its `tip_idx`, runs in the same session/transaction, and
  fires on every canonical block accept. (`models.py` gains
  `from flask import current_app`; it runs in app context here.)
- A bulk `DELETE` filtered on the indexed `tip_idx` — cheap. Deletes **only**
  `chain` rows (no cascade to `block`); orphan blocks remain.
- `FORK_PRUNE_DEPTH` is a new `EnvAppSettings` field (env `GC_FORK_PRUNE_DEPTH`,
  default **100** — ~8h behind at 5-min blocks; a fork that far back can't
  realistically out-work the confirmed suffix to win a reorg).

## Migration (greenfield — fold into the baseline)

Per the greenfield rule, **edit the single baseline migration**
(`src/gumptionchain/migrations/versions/63d32cd7621a_initial_schema.py`), not a
new stacked one: add the `tip_idx` column to the `chain` `create_table` + a
`ix_chain_tip_idx` index (mirroring `ix_chain_block_id`). No backfill (no prod
data). Tests use `db.create_all()` (the model), so they pick up `tip_idx`
automatically; `gumptionchain db check` enforces model == migration.

## Testing

- **`longest()` correctness + tiebreak preserved:** build forks at the same and
  different tip heights; assert `longest()` returns the highest-tip chain, and on
  an equal-height tie returns the (earliest-timestamp, then lowest-hash) one —
  identical to the old behavior. A focused test that the new `longest()` agrees
  with the old `chains().first()` over a multi-fork fixture.
- **`tip_idx` maintained:** extend the canonical chain → the in-place row's
  `tip_idx` advances; create a fork → a new row with the fork tip's `tip_idx`.
- **Prune:** with `FORK_PRUNE_DEPTH` small (e.g. 2), advance the canonical chain
  past a fork → the stale fork `ChainDAO` row is deleted, the canonical row and
  recent (within-depth) forks remain; assert the fork's **`BlockDAO` rows still
  exist** (no cascade) and ancestry/provenance still resolve them.
- **`db check`:** `uv run gumptionchain db check` passes (model matches the
  edited baseline).
- Hard gates: ruff + format, mypy strict, pytest.

## PR decomposition (sequential)

0. **docs** — this spec + the plan.
1. **`tip_idx` + indexed `longest()`** — the column (model + baseline migration),
   maintenance in `__init__`/`set_block_hash`, the rewritten `longest()`,
   `db check` green, and the longest()-correctness/tiebreak + tip_idx tests.
2. **Prune stale forks** — `FORK_PRUNE_DEPTH` config + `_prune_stale_forks`
   wired into `sync_longest_chain_blocks`, with the prune/no-cascade tests.

## Out of scope / follow-ups

- Caching `longest()`/`longest_chain` **within a milling round** (the miller
  calls it per PoW iteration) — a separate per-call-frequency optimization,
  orthogonal to this F-dependence fix.
- Pruning orphan `BlockDAO` rows (dangerous — breaks provenance/double-spend
  detection; explicitly not done).
- A `CONFIRMATION_DEPTH`/finality policy (the prune depth is a perf bound, not a
  consensus finality rule).
