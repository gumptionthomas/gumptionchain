# EGU #208 — Mempool Prune of Confirmed Txns Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop already-confirmed transactions from appearing as "pending": prune a block's txns from the pending pool on live acceptance, exclude canonical-confirmed rows from the two mempool read surfaces, and make the home badge count match.

**Architecture:** Three parts from the approved spec (`docs/superpowers/specs/2026-06-09-egu-208-mempool-prune-confirmed-design.md`). Part A: `Node.process_block` discards the accepted block's regular txns from `PendingTxnSet` (live paths only; `fill_chain` intentionally bypasses). Part B: a correlated `NOT EXISTS` clause on `PendingTxnDAO` over `LongestChainBlockDAO` (reorg-safe), opt-in via `exclude_confirmed=` on `pending_q` / `json_datas` / `PendingTxnSet.query_json`, wired into `mempool_view` and the API `PendingTxnView`. Part C: `PendingTxnDAO.unconfirmed_count()` for the home badge. Orphan policy is **accept + document** (a pruned txn on a later-orphaned block relies on re-gossip/re-submit; the read filter keeps display correct regardless).

**Tech Stack:** Flask + SQLAlchemy 2.0 (`Mapped[]` DAOs in `src/gumptionchain/models.py`), pytest with the conftest fixtures (`app`, `host`, `mill_block`, `add_chain_block`, `requests_proxy`, `subject`, `wallet`, `time_stepper`). Gates: `uv run ruff format src tests`, `uv run ruff check src tests`, `uv run mypy`, `uv run pytest`, `uv run gumptionchain db check`.

**Branch:** `fix/egu-208-mempool-prune-confirmed` off `main` (after the docs PR with the spec + this plan merges). Single PR for all tasks, per the spec's PR decomposition.

**No schema change.** `uv run gumptionchain db check` must stay clean throughout.

**Verified-in-code facts the implementer needs:**

- `Node.process_block` is at `src/gumptionchain/node.py:175-185`; `self.pending_txns` is a `PendingTxnSet` (`node.py:48`). `Block` and `expiry_cutoff` are already imported in `node.py` (line 13).
- `PendingTxnSet.discard(txn)` (`src/gumptionchain/transaction.py:451-459`) is a safe no-op for absent txids and cascades `PendingIOflowDAO` children via the ORM relationship.
- `Block.regular_txns` (`src/gumptionchain/block.py:139`) is `self.txns[0:-1]` — user txns, excluding the sealed coinbase.
- `PendingTxnDAO` is at `src/gumptionchain/models.py:1199`; `pending_q` at `:1250`, `json_datas` at `:1232`, `count` at `:1226`. `ColumnElement` is already imported (`models.py:11`). `TransactionDAO.blocks` is a many-to-many to `BlockDAO` via the `block_transactions` table (`models.py:66`). `LongestChainBlockDAO` (`models.py:627`) has `block_id` FK to `block.id`.
- The `NOT EXISTS` idiom precedent is `ChainDAO._unspent_clause` (`models.py:759`).
- `mempool_view` is at `src/gumptionchain/browser.py:269`; `index_view` at `:76` (uses `PendingTxnDAO.count()` at `:87`). `expiry_cutoff` and `now` are already imported in `browser.py`.
- API `PendingTxnView.get` is at `src/gumptionchain/api.py:686` (calls `node.pending_txns.query_json(...)` at `:700`).
- `scripts/populate_dev_chain.py` was checked: it has **no** manual mempool-clear workaround (the spec's "remove if present" is a no-op). No change there.
- Ruff style notes: `line-length = 80`, single quotes, `FBT` rules enabled — new boolean params must be **keyword-only** (`*,`) to avoid `FBT001/FBT002` noise (matches `filter_pending`'s `# noqa` precedent without needing a noqa).

---

## Existing-test impact of Part A (must be updated in Task 1)

With prune-on-acceptance, any test that mills a block containing pooled txns sees the pool shrink at `mill_block` time instead of lingering. Exactly three tests pin the old behavior:

1. `tests/test_miller.py::test_duplicate_transaction` (lines 114-171) — pool assertions after the confirming mill change from 1→0, 2→1, etc. (exact rewrite in Task 1 Step 5).
2. `tests/test_command.py::test_rescind` (lines 303-322) — `mill_block` at line 314 confirms the opposition; the two later assertions change 1→0 and 2→1.
3. `tests/test_command.py::test_rescind_support_kind` (lines 379-413) — same pattern; assertions at lines 408 and 413 change 1→0 and 2→1.

No other test asserts pool size after a confirming mill (verified by grepping `pending_txns`/`PendingTxnDAO.count` across `tests/`).

---

### Task 1: Part A — prune confirmed txns on block acceptance

**Files:**
- Modify: `src/gumptionchain/node.py:175-185` (`process_block` + new helper)
- Create: `tests/test_mempool_prune.py`
- Modify: `tests/test_miller.py:136-171` (`test_duplicate_transaction` expectations)
- Modify: `tests/test_command.py:319,322,408,413` (`test_rescind`, `test_rescind_support_kind` expectations)

- [ ] **Step 1: Write the failing prune test**

Create `tests/test_mempool_prune.py`:

```python
"""EGU #208: confirmed txns are pruned from the pending pool on live
block acceptance (Node.process_block), and a txn pruned on a
later-orphaned block stays out of the pool (accept + document)."""

from gumptionchain.api_client import ApiClient
from gumptionchain.chain import Chain
from gumptionchain.database import db
from gumptionchain.models import PendingIOflowDAO, PendingTxnDAO
from gumptionchain.wallet import Wallet


def _post_pending(host, chain, wallet, amount, subject):
    txn = chain.create_opposition(wallet, amount, subject)
    txn.sign()
    ApiClient(host, wallet).post_transaction(txn)
    return txn


def _count_ioflows():
    return (
        db.session.scalar(
            db.select(db.func.count()).select_from(PendingIOflowDAO)
        )
        or 0
    )


def test_process_block_prunes_confirmed_pending(
    app, host, mill_block, requests_proxy, subject, wallet
):
    with app.app_context():
        m, _b1 = mill_block(wallet)  # genesis funds the wallet
        txn = _post_pending(host, m.longest_chain, wallet, 300, subject)
        assert PendingTxnDAO.count() == 1
        assert _count_ioflows() == 1  # spends the mined coinbase

        m2, b2 = mill_block(wallet)  # b2 confirms txn

        # the confirmed txn is pruned from the pool (ioflow children
        # cascade with it)...
        assert PendingTxnDAO.get(txn.txid) is None
        assert _count_ioflows() == 0
        # ...and only it: the coinbase discard is a no-op, so the pool
        # count drops by exactly the number of regular txns
        assert len(b2.regular_txns) == 1
        assert PendingTxnDAO.count() == 0
        # the txn is canonical
        assert m2.longest_chain.get_transaction(txn.txid) is not None
```

- [ ] **Step 2: Run the new test to verify it fails**

Run: `uv run pytest tests/test_mempool_prune.py -v`
Expected: FAIL — `assert PendingTxnDAO.get(txn.txid) is None` fails (the confirmed txn still lingers in the pool).

- [ ] **Step 3: Implement the prune in `Node.process_block`**

In `src/gumptionchain/node.py`, replace the current `process_block` (lines 175-185):

```python
    def process_block(
        self,
        block: Block,
        visited_hosts: list[str] | None = None,
    ) -> Block | None:
        if block.block_hash and Block.from_db(block.block_hash):
            return None
        if block := self.add_block(block):  # type: ignore[assignment]
            self._discard_confirmed_pending(block)
            new_block_signal.send(self, block=block)
            self.send_block(block, visited_hosts=visited_hosts)
        return block

    def _discard_confirmed_pending(self, block: Block) -> None:
        # A confirmed txn no longer belongs in the mempool: discard the
        # accepted block's regular txns from the pending pool (discard
        # is a no-op for txids not pooled here, e.g. txns first seen
        # inside a gossip-received block). Lives in process_block — not
        # add_block — so it fires only on live acceptance and never
        # inside fill_chain's commit=False batch.
        #
        # Orphan caveat (accept + document, #208): if this block is
        # later orphaned by a reorg, its txns are already gone from
        # THIS node's pool and will not be re-mined here unless a peer
        # re-gossips them or the sender re-submits. The read-time
        # canonical filter (exclude_confirmed) keeps the mempool views
        # correct regardless, so this is a resource trade-off, not a
        # display or consensus bug.
        for txn in block.regular_txns:
            self.pending_txns.discard(txn)
```

- [ ] **Step 4: Run the new test to verify it passes**

Run: `uv run pytest tests/test_mempool_prune.py -v`
Expected: PASS

- [ ] **Step 5: Update the three lingering-behavior tests**

Run: `uv run pytest tests/test_miller.py tests/test_command.py -v`
Expected: 3 failures — `test_duplicate_transaction`, `test_rescind`, `test_rescind_support_kind` (pool counts now shrink at mill time).

In `tests/test_miller.py::test_duplicate_transaction`, the block of assertions after `m.mill_block(b1)` (currently lines 139-171) becomes — note every pool count after the confirming mill drops, and the final `t1` double-spend is discarded at `create_block`, emptying the pool:

```python
        assert len(m.pending_txns) == 1
        b1 = m.create_block()
        assert len(m.pending_txns) == 1
        m.mill_block(b1)
        assert len(b1.txns) == 2
        # b1 confirmed t0 -> pruned from the pool on acceptance (#208)
        assert len(m.pending_txns) == 0
        b2 = m.create_block()
        assert len(m.pending_txns) == 0
        m.mill_block(b2)
        assert len(b2.txns) == 1
        assert len(m.pending_txns) == 0
        when_dt = when_dt + datetime.timedelta(minutes=1)
        time_machine.move_to(when_dt)
        assert len(m.pending_txns) == 0
        b3 = m.create_block()
        assert len(m.pending_txns) == 0
        m.mill_block(b3)
        assert len(b3.txns) == 1
        assert len(m.pending_txns) == 0
        when_dt = when_dt + datetime.timedelta(minutes=1)
        time_machine.move_to(when_dt)
        t1 = Transaction()
        t1.add_inflow(Inflow(outflow_txid=cb0.txid, outflow_idx=0))
        t1.add_outflow(Outflow(amount=cb0_amount, address=wallet.address))
        t1.set_wallet(wallet)
        t1.seal()
        t1.sign()
        m.receive_transaction(t1.txid, t1.to_json())
        when_dt = when_dt + datetime.timedelta(minutes=1)
        time_machine.move_to(when_dt)
        assert len(m.pending_txns) == 1
        b4 = m.create_block()
        # t1 double-spends t0's confirmed inflow -> discarded at build
        assert len(m.pending_txns) == 0
        m.mill_block(b4)
        assert len(b4.txns) == 1
        assert len(m.pending_txns) == 0
```

In `tests/test_command.py::test_rescind` (lines 313-322), the `mill_block` at line 314 confirms (and now prunes) the opposition:

```python
        result = run_txn_opposition(runner, subject_raw, txn_wallet, txnwf)
        assert len(m.pending_txns) == 1
        m, _ = mill_block(txn_wallet)
        result = run_txn_rescind(
            runner, subject_raw, txn_wallet, txnwf, confirm=False
        )
        assert 'Rescind aborted' in result.output
        # the mill confirmed + pruned the opposition (#208)
        assert len(m.pending_txns) == 0
        result = run_txn_rescind(runner, subject_raw, txn_wallet, txnwf)
        assert 'Rescind created' in result.output
        assert len(m.pending_txns) == 1
```

In `tests/test_command.py::test_rescind_support_kind` (lines 395-413), same change:

```python
        result = run_txn_support(runner, subject_raw, txn_wallet, txnwf)
        assert 'Support created' in result.output
        assert len(m.pending_txns) == 1
        m, _ = mill_block(txn_wallet)
        result = run_txn_rescind(
            runner,
            subject_raw,
            txn_wallet,
            txnwf,
            confirm=False,
            kind='support',
        )
        assert 'Rescind aborted' in result.output
        # the mill confirmed + pruned the support stake (#208)
        assert len(m.pending_txns) == 0
        result = run_txn_rescind(
            runner, subject_raw, txn_wallet, txnwf, kind='support'
        )
        assert 'Rescind created' in result.output
        assert len(m.pending_txns) == 1
```

- [ ] **Step 6: Run the full suite**

Run: `uv run pytest`
Expected: all green.

- [ ] **Step 7: Lint, format, type-check**

Run: `uv run ruff format src tests && uv run ruff check src tests && uv run mypy`
Expected: clean (mypy may show only the pre-existing baseline errors, which are non-blocking; no NEW errors in `node.py`).

- [ ] **Step 8: Commit**

```bash
git add src/gumptionchain/node.py tests/test_mempool_prune.py tests/test_miller.py tests/test_command.py
git commit -m "fix(node): prune confirmed txns from mempool on block acceptance (#208)"
```

---

### Task 2: Orphan policy pin — pruned txn stays out after a reorg

**Files:**
- Modify: `tests/test_mempool_prune.py` (append one test)

This pins the **accept + document** decision: no implementation change, just a behavioral contract test. The fork construction mirrors `tests/test_chain.py::test_transaction_provenance_orphaned` (build a strictly-longer fork off the parent block with `add_chain_block`, then `alt.to_db()` re-syncs the canonical materialization).

- [ ] **Step 1: Write the orphan test**

Append to `tests/test_mempool_prune.py`:

```python
def test_orphaned_block_txn_stays_pruned(
    add_chain_block, app, host, mill_block, requests_proxy, subject, wallet
):
    # Accept + document (#208): a txn pruned on a later-orphaned block
    # is NOT auto-re-added to this node's pool; recovery relies on peer
    # re-gossip or sender re-submit. Fork construction mirrors
    # tests/test_chain.py::test_transaction_provenance_orphaned.
    with app.app_context():
        wallet2 = Wallet()
        m, b1 = mill_block(wallet)  # genesis
        txn = _post_pending(host, m.longest_chain, wallet, 300, subject)
        m, _b2 = mill_block(wallet)  # b2 confirms + prunes txn
        assert PendingTxnDAO.count() == 0

        # build a strictly-longer fork off b1 that excludes b2
        alt = Chain(block_hash=b1.block_hash)
        add_chain_block(chain=alt, milling_wallet=wallet2)
        _, _ = add_chain_block(chain=alt, milling_wallet=wallet2)
        alt.to_db()  # sync_longest_chain_blocks -> alt is canonical

        # the txn is orphaned (not in the canonical chain)...
        assert m.longest_chain.get_transaction(txn.txid) is None
        # ...and stays out of the pool (the documented trade-off)
        assert PendingTxnDAO.count() == 0
```

- [ ] **Step 2: Run it — expected to pass immediately (it pins current post-Task-1 behavior)**

Run: `uv run pytest tests/test_mempool_prune.py -v`
Expected: PASS. (If it fails, that's a real bug in Task 1 — debug before moving on.)

- [ ] **Step 3: Commit**

```bash
git add tests/test_mempool_prune.py
git commit -m "test(node): pin accept+document orphan policy for mempool prune (#208)"
```

---

### Task 3: Part B data layer — `_unconfirmed_clause` + opt-in filter params

**Files:**
- Modify: `src/gumptionchain/models.py:1226-1262` (`PendingTxnDAO`: new clause + `json_datas` / `pending_q` params)
- Modify: `src/gumptionchain/transaction.py:469-474` (`PendingTxnSet.query_json` pass-through)
- Modify: `tests/test_mempool_page.py` (new data-layer tests)

- [ ] **Step 1: Write the failing data-layer test**

In `tests/test_mempool_page.py`, the existing `_post_pending` helper (line 10) is reused. Append:

```python
def _reinsert_pending(txn):
    # Simulate re-gossip of an already-mined txn: its pending row exists
    # while its txid is already canonical.
    PendingTxnDAO(
        txid=txn.txid,
        timestamp=txn.timestamp_dt,
        json_data=txn.to_json(),
    ).commit()


def test_pending_q_exclude_confirmed(
    app, host, mill_block, requests_proxy, subject, wallet
):
    with app.app_context():
        m, _b = mill_block(wallet)
        confirmed = _post_pending(host, m.longest_chain, wallet, 300, subject)
        m, _b = mill_block(wallet)  # confirms + prunes `confirmed`
        _reinsert_pending(confirmed)
        unconfirmed = _post_pending(
            host, m.longest_chain, wallet, 200, subject
        )

        # default (opt-out): both rows return -> no behavior change for
        # the miller's pending_chain_txns / PendingTxnSet.__iter__
        txids = {
            row.txid
            for row in db.session.scalars(PendingTxnDAO.pending_q())
        }
        assert txids == {confirmed.txid, unconfirmed.txid}

        # opt-in: the canonical-confirmed row is excluded
        txids = {
            row.txid
            for row in db.session.scalars(
                PendingTxnDAO.pending_q(exclude_confirmed=True)
            )
        }
        assert txids == {unconfirmed.txid}


def test_json_datas_exclude_confirmed(
    app, host, mill_block, requests_proxy, subject, wallet
):
    with app.app_context():
        m, _b = mill_block(wallet)
        confirmed = _post_pending(host, m.longest_chain, wallet, 300, subject)
        m, _b = mill_block(wallet)  # confirms + prunes `confirmed`
        _reinsert_pending(confirmed)
        unconfirmed = _post_pending(
            host, m.longest_chain, wallet, 200, subject
        )

        # default: both
        datas = list(PendingTxnDAO.json_datas())
        assert len(datas) == 2

        # opt-in: only the unconfirmed txn
        datas = list(PendingTxnDAO.json_datas(exclude_confirmed=True))
        assert len(datas) == 1
        assert unconfirmed.txid in datas[0]
        assert confirmed.txid not in datas[0]
```

- [ ] **Step 2: Run the new tests to verify they fail**

Run: `uv run pytest tests/test_mempool_page.py -v`
Expected: the two new tests FAIL with `TypeError: ... got an unexpected keyword argument 'exclude_confirmed'`.

- [ ] **Step 3: Implement the clause and params in `PendingTxnDAO`**

In `src/gumptionchain/models.py`, inside `PendingTxnDAO` (after `count`, before `json_datas`), add:

```python
    @classmethod
    def _unconfirmed_clause(cls) -> ColumnElement[bool]:
        # True iff this pending txid is NOT in the canonical chain.
        # Correlated NOT EXISTS over the canonical materialization
        # (LongestChainBlockDAO), so it is reorg-safe: an orphaned
        # block leaves the table and its txns re-qualify as pending.
        # Same idiom as ChainDAO._unspent_clause (#165).
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

Replace `json_datas` (currently `models.py:1232-1248`) with:

```python
    @classmethod
    def json_datas(
        cls,
        earliest: datetime.datetime | None = None,
        expired: datetime.datetime | None = None,
        *,
        exclude_confirmed: bool = False,
    ) -> Generator[str, None, None]:
        stmt = db.select(cls.json_data)
        if earliest is not None:
            stmt = stmt.where(cls.received >= earliest)
        if expired is not None:
            # Same open-boundary rule as block.txn_is_expired: a txn is
            # expired iff its timestamp is strictly older than the cutoff,
            # so keep timestamp >= cutoff (the boundary txn is alive).
            stmt = stmt.where(cls.timestamp >= expired)
        if exclude_confirmed:
            stmt = stmt.where(cls._unconfirmed_clause())
        stmt = stmt.order_by(cls.timestamp, cls.txid)
        for (json_data,) in db.session.execute(stmt):
            yield json_data
```

Replace `pending_q` (currently `models.py:1250-1262`) with:

```python
    @classmethod
    def pending_q(
        cls,
        expired: datetime.datetime | None = None,
        *,
        exclude_confirmed: bool = False,
    ) -> Select[tuple[PendingTxnDAO]]:
        stmt = db.select(cls)
        if expired is not None:
            # open-boundary expiry, read-only (no prune): keep
            # timestamp >= cutoff (mirrors json_datas / txn_is_expired).
            stmt = stmt.where(cls.timestamp >= expired)
        if exclude_confirmed:
            stmt = stmt.where(cls._unconfirmed_clause())
        return stmt.order_by(  # type: ignore[no-any-return]
            cls.received.desc(), cls.txid
        )
```

In `src/gumptionchain/transaction.py`, replace `PendingTxnSet.query_json` (lines 469-474) with:

```python
    def query_json(
        self,
        earliest: datetime | None = None,
        expired: datetime | None = None,
        *,
        exclude_confirmed: bool = False,
    ) -> Iterator[str]:
        return PendingTxnDAO.json_datas(
            earliest=earliest,
            expired=expired,
            exclude_confirmed=exclude_confirmed,
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_mempool_page.py -v`
Expected: PASS (all, including the pre-existing four).

- [ ] **Step 5: Run the full suite + gates**

Run: `uv run pytest && uv run ruff format src tests && uv run ruff check src tests && uv run mypy`
Expected: all green; no new mypy errors in `models.py` / `transaction.py`.

- [ ] **Step 6: Commit**

```bash
git add src/gumptionchain/models.py src/gumptionchain/transaction.py tests/test_mempool_page.py
git commit -m "fix(models): opt-in canonical NOT EXISTS filter on pending reads (#208)"
```

---

### Task 4: Part B wiring — `/mempool` view and API pending view opt in

**Files:**
- Modify: `src/gumptionchain/browser.py:275` (`mempool_view`)
- Modify: `src/gumptionchain/api.py:700-702` (`PendingTxnView.get`)
- Modify: `tests/test_mempool_page.py` (browser test)
- Modify: `tests/test_api.py` (API test)

- [ ] **Step 1: Write the failing view tests**

Append to `tests/test_mempool_page.py`:

```python
def test_mempool_view_hides_confirmed_txn(
    app, host, mill_block, requests_proxy, subject, wallet
):
    with app.app_context():
        m, _b = mill_block(wallet)
        confirmed = _post_pending(host, m.longest_chain, wallet, 300, subject)
        m, _b = mill_block(wallet)  # confirms + prunes `confirmed`
        _reinsert_pending(confirmed)
        unconfirmed = _post_pending(
            host, m.longest_chain, wallet, 200, subject
        )

        resp = app.test_client().get('/mempool')
        assert resp.status_code == 200
        assert unconfirmed.txid.encode() in resp.data
        assert confirmed.txid.encode() not in resp.data
```

Append to `tests/test_api.py` (after `test_pending_transactions_earliest_returns_recent_txns`); also add `PendingTxnDAO` to the existing `gumptionchain.models` import in that file (or add `from gumptionchain.models import PendingTxnDAO` if no models import exists):

```python
def test_pending_transactions_exclude_confirmed(
    app, host, mill_block, requests_proxy, subject, time_stepper, wallet
):
    with app.app_context():
        time_step = time_stepper()
        _ = next(time_step)
        m, _b = mill_block(wallet)
        _ = next(time_step)
        confirmed = m.longest_chain.create_opposition(wallet, 1, subject)
        confirmed.sign()
        ApiClient(host, wallet).post_transaction(confirmed)
        _ = next(time_step)
        m, _b = mill_block(wallet)  # confirms + prunes `confirmed`
        # simulate re-gossip of the already-mined txn
        PendingTxnDAO(
            txid=confirmed.txid,
            timestamp=confirmed.timestamp_dt,
            json_data=confirmed.to_json(),
        ).commit()
        _ = next(time_step)
        txn2 = m.longest_chain.create_opposition(wallet, 2, subject)
        txn2.sign()
        ApiClient(host, wallet).post_transaction(txn2)

        response = ApiClient(host, wallet).get_pending_transactions()
        assert response.status_code == httpx.codes.OK
        txids = [t['txid'] for t in response.json()]
        assert txids == [txn2.txid]
```

- [ ] **Step 2: Run them to verify they fail**

Run: `uv run pytest tests/test_mempool_page.py::test_mempool_view_hides_confirmed_txn tests/test_api.py::test_pending_transactions_exclude_confirmed -v`
Expected: both FAIL — the confirmed txid still appears in the responses.

- [ ] **Step 3: Wire the two views**

In `src/gumptionchain/browser.py` (`mempool_view`, line 274-277), change the paginate call:

```python
        pending_page = db.paginate(
            PendingTxnDAO.pending_q(
                expired=expiry_cutoff(now()), exclude_confirmed=True
            ),
            error_out=False,
        )
```

In `src/gumptionchain/api.py` (`PendingTxnView.get`, lines 700-702), change the `query_json` call:

```python
            pending_json = node.pending_txns.query_json(
                earliest=earliest, expired=expired, exclude_confirmed=True
            )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_mempool_page.py tests/test_api.py -v`
Expected: PASS.

- [ ] **Step 5: Run the full suite + gates**

Run: `uv run pytest && uv run ruff format src tests && uv run ruff check src tests && uv run mypy`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/gumptionchain/browser.py src/gumptionchain/api.py tests/test_mempool_page.py tests/test_api.py
git commit -m "fix(api,browser): exclude canonical-confirmed txns from mempool reads (#208)"
```

---

### Task 5: Part B reorg safety — orphaned txn re-qualifies in filtered reads

**Files:**
- Modify: `tests/test_mempool_page.py` (append one test; add `Chain` and `Wallet` imports)

Pins the reorg-safety property of the `NOT EXISTS`-over-`LongestChainBlockDAO` design: no implementation change expected.

- [ ] **Step 1: Write the reorg test**

Add to the imports at the top of `tests/test_mempool_page.py`:

```python
from gumptionchain.chain import Chain
from gumptionchain.wallet import Wallet
```

Append:

```python
def test_exclude_confirmed_is_reorg_safe(
    add_chain_block, app, host, mill_block, requests_proxy, subject, wallet
):
    # An orphaned block leaves LongestChainBlockDAO, so its txns
    # re-qualify as pending in the filtered reads. Fork construction
    # mirrors tests/test_chain.py::test_transaction_provenance_orphaned.
    with app.app_context():
        wallet2 = Wallet()
        m, b1 = mill_block(wallet)  # genesis
        txn = _post_pending(host, m.longest_chain, wallet, 300, subject)
        m, _b2 = mill_block(wallet)  # b2 confirms + prunes txn
        _reinsert_pending(txn)

        # while canonical-confirmed: excluded
        rows = db.session.scalars(
            PendingTxnDAO.pending_q(exclude_confirmed=True)
        ).all()
        assert rows == []

        # orphan b2 with a strictly-longer fork off b1
        alt = Chain(block_hash=b1.block_hash)
        add_chain_block(chain=alt, milling_wallet=wallet2)
        _, _ = add_chain_block(chain=alt, milling_wallet=wallet2)
        alt.to_db()  # sync_longest_chain_blocks -> alt is canonical

        # the orphaned txn re-qualifies as pending
        txids = [
            row.txid
            for row in db.session.scalars(
                PendingTxnDAO.pending_q(exclude_confirmed=True)
            )
        ]
        assert txids == [txn.txid]
```

- [ ] **Step 2: Run it — expected to pass immediately (pins designed-in behavior)**

Run: `uv run pytest tests/test_mempool_page.py -v`
Expected: PASS. (A failure means `_unconfirmed_clause` is reading something other than the canonical materialization — debug before moving on.)

- [ ] **Step 3: Commit**

```bash
git add tests/test_mempool_page.py
git commit -m "test(models): pin reorg-safety of the exclude_confirmed filter (#208)"
```

---

### Task 6: Part C — home badge counts unexpired + unconfirmed

**Files:**
- Modify: `src/gumptionchain/models.py` (`PendingTxnDAO.unconfirmed_count`)
- Modify: `src/gumptionchain/browser.py:87` (`index_view`)
- Modify: `tests/test_home_page.py` (new test)

- [ ] **Step 1: Write the failing test**

In `tests/test_home_page.py`, extend the imports:

```python
import datetime

from gumptionchain.api_client import ApiClient
from gumptionchain.block import expiry_cutoff
from gumptionchain.models import PendingTxnDAO
from gumptionchain.util import now
```

Append:

```python
def test_home_pending_count_excludes_confirmed_and_expired(
    app, host, mill_block, requests_proxy, subject, wallet
):
    with app.app_context():
        m, _b = mill_block(wallet)
        confirmed = _stake_opposition(
            host, m.longest_chain, wallet, 300, subject
        )
        m, _b = mill_block(wallet)  # confirms + prunes `confirmed`
        # re-insert the confirmed txn (simulates re-gossip after mining)
        PendingTxnDAO(
            txid=confirmed.txid,
            timestamp=confirmed.timestamp_dt,
            json_data=confirmed.to_json(),
        ).commit()
        # an expired row (the /mempool view hides it; so must the badge)
        PendingTxnDAO(
            txid='a' * 64,
            timestamp=now() - datetime.timedelta(hours=8),
            json_data='{}',
        ).commit()
        # one live, unconfirmed txn
        _stake_opposition(host, m.longest_chain, wallet, 200, subject)

        # raw count sees all three; the badge count sees only the live one
        assert PendingTxnDAO.count() == 3
        assert (
            PendingTxnDAO.unconfirmed_count(expired=expiry_cutoff(now()))
            == 1
        )

        resp = app.test_client().get('/')
        assert resp.status_code == 200
        assert b'>1<' in resp.data
```

- [ ] **Step 2: Run it to verify it fails**

Run: `uv run pytest tests/test_home_page.py -v`
Expected: the new test FAILS with `AttributeError: ... has no attribute 'unconfirmed_count'`.

- [ ] **Step 3: Implement `unconfirmed_count` and wire `index_view`**

In `src/gumptionchain/models.py`, inside `PendingTxnDAO` directly after `count` (line 1226-1230), add:

```python
    @classmethod
    def unconfirmed_count(
        cls, expired: datetime.datetime | None = None
    ) -> int:
        # The home-badge count: unexpired + unconfirmed, matching what
        # /mempool displays via pending_q(exclude_confirmed=True). Same
        # open-boundary expiry rule as pending_q / json_datas.
        stmt = db.select(db.func.count()).select_from(cls)
        if expired is not None:
            stmt = stmt.where(cls.timestamp >= expired)
        stmt = stmt.where(cls._unconfirmed_clause())
        return db.session.scalar(stmt) or 0
```

In `src/gumptionchain/browser.py` (`index_view`, line 86-87), change:

```python
        # Pending-pool size is independent of the chain (always available).
        # Count what /mempool displays: unexpired + unconfirmed (#208).
        pending_count = PendingTxnDAO.unconfirmed_count(
            expired=expiry_cutoff(now())
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_home_page.py -v`
Expected: PASS (including the pre-existing `test_home_shows_pending_count` — its single posted txn is unexpired and unconfirmed, so the badge still reads 1).

- [ ] **Step 5: Run the full suite + gates**

Run: `uv run pytest && uv run ruff format src tests && uv run ruff check src tests && uv run mypy`
Expected: all green.

- [ ] **Step 6: Commit**

```bash
git add src/gumptionchain/models.py src/gumptionchain/browser.py tests/test_home_page.py
git commit -m "fix(browser): home pending badge counts unexpired+unconfirmed (#208)"
```

---

### Task 7: Final verification + PR

**Files:** none (verification only). Note: `scripts/populate_dev_chain.py` was verified to contain **no** manual mempool-clear workaround, so the spec's "remove if present" item is a no-op — nothing to change there. With Part A, in-process mining naturally leaves an empty mempool.

- [ ] **Step 1: Run every gate from a clean tree**

```bash
uv run ruff format --check src tests
uv run ruff check src tests
uv run mypy
uv run pytest
uv run gumptionchain db check
```

Expected: all green; `db check` clean (no schema change in this PR).

- [ ] **Step 2: Push and open the PR**

```bash
git push -u origin fix/egu-208-mempool-prune-confirmed
gh pr create --title "fix: prune confirmed txns from mempool + read-time canonical filter (#208)" --body "$(cat <<'EOF'
## Summary
- **Part A** — `Node.process_block` prunes an accepted block's regular txns from the pending pool (live paths only; `fill_chain` batch intentionally bypasses). Orphan policy: accept + document.
- **Part B** — reorg-safe `NOT EXISTS`-over-`LongestChainBlockDAO` filter, opt-in via `exclude_confirmed=` on `pending_q`/`json_datas`/`query_json`; wired into `/mempool` and `GET /api/transaction/pending`. Miller semantics unchanged (flag defaults off).
- **Part C** — home Pending badge counts unexpired + unconfirmed, matching `/mempool`.

Spec: `docs/superpowers/specs/2026-06-09-egu-208-mempool-prune-confirmed-design.md`. Closes #208.

## Test plan
- [ ] `uv run pytest` (new: `tests/test_mempool_prune.py`; extended: mempool/home/api tests; updated lingering-pool expectations in `test_miller`/`test_command`)
- [ ] `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy`
- [ ] `uv run gumptionchain db check` (no schema change)

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Then post the "ready for review" summary and **stop** — per repo convention, no merge until the author's explicit signal.
