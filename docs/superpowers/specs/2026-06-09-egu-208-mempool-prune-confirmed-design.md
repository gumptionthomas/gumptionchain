# EGU #208 — prune confirmed txns from the mempool + filter them at read time

**Date:** 2026-06-09
**Issue:** #208 (confirmed transactions stay in the pending pool until expiry → phantom `/mempool` + API entries). Follow-up to #203/#207. EGU launch checklist (#190) — *not a gate*, but a phantom mempool is a bad pre-launch look.
**Status:** design approved

## Goal

Stop already-confirmed transactions from appearing as "pending." Two
complementary changes:

1. **Prune on acceptance** — when a block is accepted, drop its txns from the
   pending pool (resource win + an empty mempool after mining, which removes the
   `populate_dev_chain.py` manual-clear workaround).
2. **Read-time canonical filter** — the mempool reads exclude any pending row
   whose txid is already in the canonical chain (the correctness guarantee, and
   reorg-safe).

Decided during brainstorming: **do both** (issue option 3), and for the
reorg/orphan case **accept + document** (issue's lightest option) — a txn pruned
on a later-orphaned block is dropped from this node's pool and relies on
re-gossip / re-submit. The read-time filter keeps *display* correct regardless,
so the prune's worst case is "a rare orphaned txn must be re-broadcast" — never
wrong display, never a consensus issue. This is **not** a consensus/correctness
bug today: `Miller.pending_chain_txns` already prevents double-inclusion at
mining time; #208 is an accuracy + resource fix.

## Background (verified in code)

- **Live-acceptance chokepoint = `Node.process_block`** (`src/gumptionchain/node.py`).
  Both paths funnel through it: gossip-received blocks (`receive_block` →
  `process_block` when `process=True`) and locally-milled blocks
  (`Miller.mill_block` persists the solved block via `self.receive_block(...)`,
  `miller.py:137`). `process_block` calls `add_block`, and on a genuinely-new
  accepted block fires `new_block_signal` + gossips. `fill_chain` is the **only**
  acceptance path that bypasses `process_block` — it calls `add_block` directly
  with `commit=False` in a batch. During bulk historical sync the pending pool is
  empty, so not pruning there is correct (and we must not inject per-txn commits
  into a `commit=False` batch).
- **`PendingTxnSet.discard(txn)`** (`transaction.py:451`) → `PendingTxnDAO.get` +
  `dao.delete()`, which commits independently (`models.py:1222`). It is a no-op
  when the txid is absent (so discarding a coinbase txid is harmless). Safe to
  call after `add_block` has already committed the block.
- **Read paths today** (both expiry-only, no confirmed-exclusion):
  - Browser `mempool_view` (`browser.py:270`) → `db.paginate(PendingTxnDAO.pending_q(expired=cutoff))`.
  - API `PendingTxnView` (`api.py:686`) → `node.pending_txns.query_json(earliest, expired)` → `PendingTxnDAO.json_datas`.
  - Home `index_view` (`browser.py:87`) → `PendingTxnDAO.count()` (counts **all**
    rows, including expired and confirmed).
- **Canonical membership** is materialized in `LongestChainBlockDAO` (one row per
  canonical block). A `TransactionDAO` is confirmed-canonical iff it belongs to a
  block in that table. Reading off it makes the filter reorg-safe: an orphaned
  block leaves `LongestChainBlockDAO`, so its txns re-qualify as pending.

## Part A — prune on acceptance

In `Node.process_block`, after `add_block` returns a non-`None` (new, accepted)
block and before/after the gossip send, discard the block's txns:

```python
def process_block(self, block, visited_hosts=None):
    if block.block_hash and Block.from_db(block.block_hash):
        return None
    if block := self.add_block(block):
        self._discard_confirmed_pending(block)
        new_block_signal.send(self, block=block)
        self.send_block(block, visited_hosts=visited_hosts)
    return block

def _discard_confirmed_pending(self, block: Block) -> None:
    # A confirmed txn no longer belongs in the mempool. Discard the accepted
    # block's regular txns from the pending pool (discard is a no-op for
    # txids not present).
    #
    # Orphan caveat (accept + document, #208): if this block is later orphaned
    # by a reorg, its txns are already gone from THIS node's pool and will not
    # be re-mined here unless a peer re-gossips them or the sender re-submits.
    # The read-time canonical filter (exclude_confirmed) keeps the mempool
    # *views* correct regardless, so this is a resource trade-off, not a
    # display or consensus bug.
    for txn in block.regular_txns:
        self.pending_txns.discard(txn)
```

`block.regular_txns` (`block.py:139`) is `block.txns[0:-1]` — the user txns,
excluding the sealed coinbase at `txns[-1]` (which is never in the pool anyway).
Placed in `process_block` (not the shared `add_block`) so it fires only on the
live-acceptance paths and never inside the `fill_chain` batch.

## Part B — read-time canonical filter

Add a shared correlated-`NOT EXISTS` clause on `PendingTxnDAO` and apply it
opt-in to the two read queries.

```python
@classmethod
def _unconfirmed_clause(cls) -> ColumnElement[bool]:
    # True iff this pending txid is NOT in the canonical chain. Correlated
    # NOT EXISTS over the canonical materialization (LongestChainBlockDAO),
    # so it is reorg-safe: an orphaned block leaves the table and its txns
    # re-qualify as pending. Same idiom as ChainDAO._unspent_clause (#165).
    confirmed = (
        db.select(db.literal(1))
        .select_from(TransactionDAO)
        .join(TransactionDAO.blocks)
        .join(
            LongestChainBlockDAO,
            LongestChainBlockDAO.block_id == BlockDAO.id,
        )
        .where(TransactionDAO.txid == cls.txid)
    )
    return ~confirmed.exists()
```

`pending_q` and `json_datas` each gain an `exclude_confirmed: bool = False`
parameter; when `True` they add `.where(cls._unconfirmed_clause())`:

```python
@classmethod
def pending_q(cls, expired=None, exclude_confirmed=False):
    stmt = db.select(cls)
    if expired is not None:
        stmt = stmt.where(cls.timestamp >= expired)
    if exclude_confirmed:
        stmt = stmt.where(cls._unconfirmed_clause())
    return stmt.order_by(cls.received.desc(), cls.txid)

@classmethod
def json_datas(cls, earliest=None, expired=None, exclude_confirmed=False):
    stmt = db.select(cls.json_data)
    if earliest is not None:
        stmt = stmt.where(cls.received >= earliest)
    if expired is not None:
        stmt = stmt.where(cls.timestamp >= expired)
    if exclude_confirmed:
        stmt = stmt.where(cls._unconfirmed_clause())
    stmt = stmt.order_by(cls.timestamp, cls.txid)
    for (json_data,) in db.session.execute(stmt):
        yield json_data
```

`PendingTxnSet.query_json` gains a pass-through `exclude_confirmed=False` param
so the API view can opt in. The flag is **opt-in** so the miller's
`pending_chain_txns` (which calls `query_json` and does its own chain check at
build time) and `PendingTxnSet.__iter__` keep their current behavior.

Wire the two read views to opt in:
- `mempool_view`: `PendingTxnDAO.pending_q(expired=cutoff, exclude_confirmed=True)`.
- `PendingTxnView`: `query_json(earliest=earliest, expired=expired, exclude_confirmed=True)`.

`ColumnElement` must be imported in `models.py` (already added in #165) — confirm
it is in the `from sqlalchemy import (...)` block.

## Part C — home badge consistency

`index_view`'s `pending_count` switches from `PendingTxnDAO.count()` (all rows)
to a count over the same filter `/mempool` displays — **unexpired + unconfirmed**
— so the badge matches the list. Add:

```python
@classmethod
def unconfirmed_count(cls, expired: datetime.datetime | None = None) -> int:
    stmt = db.select(db.func.count()).select_from(cls)
    if expired is not None:
        stmt = stmt.where(cls.timestamp >= expired)
    stmt = stmt.where(cls._unconfirmed_clause())
    return db.session.scalar(stmt) or 0
```

`index_view`: `pending_count = PendingTxnDAO.unconfirmed_count(expired=expiry_cutoff(now()))`.
This also corrects a pre-existing mismatch (the old count included *expired*
rows the view hid) — an intentional, in-scope consistency fix, not silent creep.
`PendingTxnDAO.count()` is left in place (still used elsewhere / by tests).

## Testing

- **Prune on acceptance:** build a chain; submit a txn to the pool; mill (or
  `receive_block`) a block that includes it; after `process_block`, assert
  `PendingTxnDAO.get(txid) is None` and `txid` resolves in the canonical chain.
  Assert the coinbase discard is a harmless no-op (pool count drops by exactly
  the number of regular txns, not more).
- **Orphan (accept + document):** confirm a txn on a block, then drive a reorg
  that orphans it; assert the txn is **not** auto-re-added to the pool (it stays
  discarded) — pinning the documented behavior. (It still would not display as
  confirmed: it's neither in the pool nor in the canonical chain.)
- **Read filter — confirmed excluded:** insert a `PendingTxnDAO` row whose txid
  is in the canonical chain; assert `pending_q(exclude_confirmed=True)`,
  `json_datas(exclude_confirmed=True)`, `mempool_view`, and the API view all
  exclude it, while a genuinely-unconfirmed txid is included. Assert the default
  (`exclude_confirmed=False`) still returns it (opt-in, no behavior change for
  the miller / `__iter__`).
- **Read filter — reorg-safe:** a txid confirmed then orphaned (no longer in
  `LongestChainBlockDAO`) re-qualifies and reappears in the filtered read.
- **Home count:** `unconfirmed_count` excludes confirmed and expired rows;
  matches the `/mempool` item count over the same fixture.
- Hard gates: `uv run ruff format src tests && uv run ruff check src tests &&
  uv run mypy && uv run pytest`, plus `uv run gumptionchain db check` (no schema
  change — must stay clean).

## Scope / care

- **Not consensus-relevant:** block validation and `pending_chain_txns`
  double-inclusion guarding are untouched. The prune only removes mempool rows;
  the read filter only changes what's *displayed*.
- **Opt-in filter:** `exclude_confirmed` defaults `False` so only the two human
  read surfaces change; the miller and set-iteration keep current semantics.
- **`populate_dev_chain.py`:** with Part A landed, in-process mining naturally
  leaves an empty mempool. Remove the manual `PendingTxnDAO`/`PendingIOflowDAO`
  clear workaround in that script (if present) and note it.

## PR decomposition

Single PR (`fix/egu-208-mempool-prune-confirmed`): Part A (prune) + Part B
(filter) + Part C (count) + the dev-script cleanup. One coherent fix for one
issue; the tests interlock (the prune test and the read-filter test share the
confirm-a-pending-txn fixture). Docs (this spec + plan) land first per cadence.

## Out of scope / follow-ups

- **Reorg re-add** (returning orphaned txns to the pool) — needs orphan-detection
  the codebase lacks; explicitly deferred (accept + document chosen instead).
- **`CONFIRMATION_DEPTH`/finality** — not introduced; prune is immediate on
  acceptance, display correctness comes from the canonical filter.
- No schema/migration change.
