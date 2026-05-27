# Roadmap — Phase 7 and beyond

Consolidated list of forward-looking items deferred from prior phase specs. Each entry links to the originating spec for the full rationale. Items are not strictly ordered — pick by current priority.

When closing an item: remove it from this file (or mark it ✅ with the closing PR/commit) and move on. When discovering a new item during a phase: add a one-line entry here pointing at the spec section that introduced it.

---

## Phase 7 (next big phase) — SQLAlchemy 2.0 modernization

A bundled phase covering the three tightly-coupled items in `src/cancelchain/models.py`. Originally planned as Phase 6 before that slot was repurposed for the recursive-CTE bottleneck fix.

- **SA 2.0 query syntax.** Translate `Model.query` / `db.session.query(...)` to `db.session.execute(db.select(...)).scalar() / .scalars().all()`. ~30 call sites in `models.py`, ~3 in `tests/test_models.py`, 1 in `api.py`.
- **Typed `DeclarativeBase`.** Switch from Flask-SQLAlchemy's dynamic `db.Model` to a typed base — required to remove the mypy override below.
- **Remove `# mypy: disable-error-code="no-untyped-call,no-any-return,name-defined,misc"`** block at the top of `models.py`. Originally added in Phase 3 with an explicit Phase 7 sunset note.

Originating specs:
- [Phase 6 spec — What comes next](specs/2026-05-27-phase-6-longest-chain-materialization-design.md)
- [Phase 3 spec](specs/2026-05-24-phase-3-lint-typing-ci-gating-design.md) — initial Phase 7 sunset commitment
- `src/cancelchain/models.py:7-11` — header comment promising the cleanup

---

## Phase 6.6 (medium, high priority) — Smart-reorg rebuild

Today any reorg triggers a full `_rebuild_longest_chain_blocks` — even a shallow 1-block tip change rewalks from tip to genesis. On a long chain that's the perf cliff: a 5-year chain at the 10-min block target is ~263 k blocks; full rebuild on a 1-block reorg takes 4 minutes (local Postgres warm cache) to ~22 minutes (cloud Postgres), exceeding the block-time budget.

**Smart-reorg algorithm:** walk the new tip back via `current.prev` *only* until we hit a `block_id` already in `longest_chain_block`. That's the common ancestor; its `position` is K. Then:
1. `DELETE FROM longest_chain_block WHERE position > K`.
2. Insert the diverging suffix we just walked back through (collected in a list).
3. Bump `_chain_generation`.

A shallow reorg becomes O(reorg depth) instead of O(chain length). Bootstrap still needs the full walk (one-time cost), and a deep reorg (catastrophically rare) falls back to a full rebuild — keep the existing code as the fallback path.

This is upstream of Phase 6.7 batched-fetch: smart-reorg removes the algorithmic issue (full rebuild on shallow reorgs), and batched-fetch is then a constant-factor optimization on whichever walks remain.

Originating analysis:
- Back-of-envelope: 5-year chain × 10 min/block = 263 k blocks; 1ms-per-lookup × 263 k = 4+ min per rebuild, exceeding the block-time budget on remote-DB / cloud configs.
- [Phase 6.5 spec — Risks](specs/2026-05-27-phase-6_5-residual-cte-and-is-longest-cache-design.md) "Iterative walk slow on very long chains" (introduced the concern; smart-reorg is the better fix).

---

## Phase 6.7 (small) — Batched-fetch chain walk

Replace the single-row iterative walk in `ChainDAO._rebuild_longest_chain_blocks` (currently `current = current.prev` per step) with batched fetch (`WHERE id IN (...)` over N collected prev_ids at a time) **if profiling shows the per-step lazy-load is the new bottleneck on long chains**. After Phase 6.6 (smart-reorg) lands, the only walks that benefit are bootstrap (one-time) and catastrophic deep-reorg fallback (rare) — so this drops to lower priority.

Originating spec:
- [Phase 6.5 spec — Risks](specs/2026-05-27-phase-6_5-residual-cte-and-is-longest-cache-design.md) "Iterative walk slow on very long chains"

---

## Phase 7+ — Generalize materialization to all chains

Today the `longest_chain_block` table tracks only the canonical chain. The 4 `ChainDAO` property accessors (`blocks`, `transactions`, `outflows`, `inflows`) branch on `_is_longest()` and fall back to the CTE-backed `BlockDAO.block_chain` / `transactions_chain` / `outflows_chain` / `inflows_chain` for non-longest chains. Removing the CTE-backed fallback entirely requires generalizing the materialization to all chains — e.g., a many-to-many `chain_blocks(chain_id, block_id, position)` updated on every chain extension.

Trade-off: storage grows (N chains × M blocks); reorg-handling complexity grows. Only worth it if non-longest chain queries become hot.

Originating spec:
- [Phase 6.5 spec — Non-goals](specs/2026-05-27-phase-6_5-residual-cte-and-is-longest-cache-design.md)
- [Phase 6 spec — Risks](specs/2026-05-27-phase-6-longest-chain-materialization-design.md) "Residual CTE on non-longest chain API queries"

---

## Phase 7+ — Cross-worker `_is_longest()` cache invalidation

The class-level generation counter from Phase 6.5 invalidates all in-process `ChainDAO` instances' cached `_is_longest` values, but it's **process-local**. Multi-worker Gunicorn setups have one counter per worker — Worker A's reorg doesn't bump Worker B's counter. A held `ChainDAO` instance in Worker B could return a stale `True` for a brief window after a cross-worker reorg, routing the next property read to the materialized table aligned with the new (different) longest chain.

Mitigations to consider when this matters:
- Session-scoped cache (cleared at request boundaries via Flask `g` / equivalent).
- Cross-process notification (Redis pubsub, Postgres `NOTIFY` channel, or similar).

Originating spec:
- [Phase 6.5 spec — Risks](specs/2026-05-27-phase-6_5-residual-cte-and-is-longest-cache-design.md) "Cross-worker stale cache"

---

## Phase 7+ — Alembic migration framework

Introduce Alembic for schema migrations. Greenfield posture means we don't have an installed-base problem yet, but adding the framework before going to prod prevents pain later (e.g., column adds, index changes, table renames).

Originating spec:
- [Phase 3 spec — What comes next](specs/2026-05-24-phase-3-lint-typing-ci-gating-design.md) "Phase 7 — Alembic"

---

## Closed items (historical reference)

Each removed from this file when the closing PR landed. Keep here for now so future Claude sessions can see what was on the list.

- ✅ **`app.clients` teardown** — closed by [PR #63](https://github.com/gumptionthomas/cancelchain/pull/63) (Phase 5b follow-up). Was originally Phase 5b deferral.
- ✅ **`Wallet.key` type tightening** — closed by [PR #66](https://github.com/gumptionthomas/cancelchain/pull/66). Was originally Phase 5a deferral.
- ✅ **Recursive CTE in `_rebuild_longest_chain_blocks`** — closed by [PR #68](https://github.com/gumptionthomas/cancelchain/pull/68) (Phase 6.5). Was originally Phase 6 deferral.
- ✅ **`_is_longest()` per-call query cost** — closed by [PR #68](https://github.com/gumptionthomas/cancelchain/pull/68) (Phase 6.5). Was originally raised by Copilot on PR #65.
