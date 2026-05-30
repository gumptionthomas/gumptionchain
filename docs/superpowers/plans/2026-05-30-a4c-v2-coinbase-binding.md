# A4.c v2 — coinbase-to-block binding implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bind each coinbase to its block by including the block's `prev_hash` in the coinbase transaction's hashed data (its txid), and validate `cb.prev_hash == block.prev_hash` in `Chain.validate_block_coinbase`. This makes legitimate consecutive blocks' coinbases unique (closing the A4.c balance-inflation surface at its root) and rejects coinbase replay via a purely local binding check.

**Architecture:** Add an optional `prev_hash` field to `Transaction` (conditional-append in `data_csv` so regular-txn txids are byte-unchanged; required on coinbases, forbidden on regular txns via the Pydantic models); persist it as a nullable `TransactionDAO` column with a regenerated base migration (pre-1.0, no legacy installs); thread `self.prev_hash` through `Block.create_coinbase` → `Transaction.coinbase`; add a local binding check raising a new `MismatchedCoinbaseError(InvalidCoinbaseError)`.

**Tech Stack:** Python 3.12 + SQLAlchemy 2.0.50 + Flask-SQLAlchemy 3.1 + Flask-Migrate/Alembic + Pydantic v2 + pytest. Companion design spec: `docs/superpowers/specs/2026-05-30-a4c-v2-coinbase-binding-design.md`.

---

## Prerequisites

- Working directory: cancelchain repo root.
- A4.c v1 docs merged. `git log --oneline -1 main` shows `d49bf29 docs(a4c): coinbase-txid uniqueness check design + plan (#88)` (the superseded v1).
- The v2 design spec is committed on `docs/a4c-v2-coinbase-binding` (`7ea2704`). This plan adds a second commit on that branch and ships both as the docs PR.
- Test baseline on main: **237 passed, 5 xfailed, 1 skipped**.
- CI gates: `ruff check`, `ruff format --check`, `pytest`, `mypy`, `cancelchain db upgrade` + `cancelchain db check`.
- Never push to main. wor + mwg handled by the controller.

---

## File Map

| Task | PR | Files |
|---|---|---|
| 1 | docs PR | this plan + the v2 spec (already on branch) |
| 2 | impl PR | branch off main; baseline |
| 3 | impl PR | `src/cancelchain/exceptions.py` — `MismatchedCoinbaseError` |
| 4 | impl PR | `src/cancelchain/transaction.py` — `prev_hash` field + conditional `data_csv` + Pydantic models + `Transaction.coinbase` param + `to_dao`/`from_dao` |
| 5 | impl PR | `src/cancelchain/models.py` — `TransactionDAO.prev_hash` nullable column |
| 6 | impl PR | regenerate `src/cancelchain/migrations/versions/` initial migration |
| 7 | impl PR | `src/cancelchain/block.py` — thread `self.prev_hash` into `create_coinbase` |
| 8 | impl PR | `src/cancelchain/chain.py` — binding check in `validate_block_coinbase` |
| 9 | impl PR | `tests/test_verification_audit.py` — un-xfail A4.c, invert cross-fork test, module docstring |
| 10 | impl PR | audit doc + ROADMAP + v1 supersession banners |
| 11 | impl PR | gates + commit + push + open PR |
| 12 | acceptance | none |

---

## Task 1: Ship the docs PR (spec + plan)

- [ ] **Step 1: Confirm branch + commit**

```bash
git rev-parse --abbrev-ref HEAD
git rev-list --count main..HEAD
```

Expected: branch `docs/a4c-v2-coinbase-binding`; count `1` (the spec commit).

- [ ] **Step 2: Stage + commit this plan**

```bash
git add docs/superpowers/plans/2026-05-30-a4c-v2-coinbase-binding.md
git commit -m "$(cat <<'EOF'
docs(a4c-v2): coinbase-to-block binding implementation plan

Plan executes the v2 design (prev_hash binding into the coinbase txid +
local binding validation), superseding the unimplementable v1 lineage
check. Adds a prev_hash field to Transaction (conditional-append in
data_csv so regular txids are unchanged), a nullable TransactionDAO
column via a regenerated base migration, threads prev_hash through
Block.create_coinbase, adds MismatchedCoinbaseError, un-xfails the A4.c
demonstration test, and inverts the v1 cross-fork-acceptance test
(coinbases are block-bound).

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 3: Push + open docs PR**

```bash
git push -u origin docs/a4c-v2-coinbase-binding
gh pr create --base main --head docs/a4c-v2-coinbase-binding --title "docs(a4c-v2): coinbase-to-block binding design + plan" --body "$(cat <<'EOF'
## Summary
- Adds the A4.c v2 design spec (\`docs/superpowers/specs/2026-05-30-a4c-v2-coinbase-binding-design.md\`) and implementation plan.
- No code changes.

Supersedes the v1 A4.c remediation (PR #88), which proved unimplementable: coinbase txids are not unique across legitimately-mined consecutive blocks (no inflows; \`data_csv\` hashes only timestamp+address+pubkey+outflows+version at second resolution), so v1's lineage-uniqueness check rejected 17 legitimate-block tests. v2 binds the block's \`prev_hash\` into the coinbase txid (consecutive blocks differ → unique coinbases) and validates \`cb.prev_hash == block.prev_hash\` locally (rejects replay; no lineage walk, no \`self.last_block\` hazard). Includes a nullable \`TransactionDAO.prev_hash\` column via a regenerated base migration (pre-1.0, no legacy installs).

## Test plan
- [x] Spec self-review passed.
- [x] Plan self-review passed.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 4: Stop — controller handles wor + mwg + sync.**

---

## Task 2: Impl branch + baseline

- [ ] **Step 1:** After the docs PR merges:

```bash
git checkout main && git pull --ff-only
git checkout -b fix/a4c-v2-coinbase-binding
uv run mypy && uv run ruff check src tests && uv run pytest 2>&1 | tail -3
```

Expected: clean mypy/ruff; pytest `237 passed, 5 xfailed, 1 skipped`. If not, STOP / BLOCKED.

- [ ] **Step 2:** Confirm the A4.c test is currently xfail:

```bash
uv run pytest tests/test_verification_audit.py::test_a4_c_ii_coinbase_replay_inflates_balance 2>&1 | tail -3
```

Expected: `1 xfailed`.

---

## Task 3: Add `MismatchedCoinbaseError`

**Files:** Modify `src/cancelchain/exceptions.py`.

- [ ] **Step 1:** Find (near line 101):

```python
class InvalidCoinbaseErrorRewardError(InvalidCoinbaseError):
    pass
```

Add immediately after:

```python
class InvalidCoinbaseErrorRewardError(InvalidCoinbaseError):
    pass


class MismatchedCoinbaseError(InvalidCoinbaseError):
    pass
```

- [ ] **Step 2:** Verify:

```bash
uv run ruff check src/cancelchain/exceptions.py && uv run mypy 2>&1 | tail -2
grep -n 'class MismatchedCoinbaseError' src/cancelchain/exceptions.py
```

Both clean; one match.

---

## Task 4: Add `prev_hash` to `Transaction`

**Files:** Modify `src/cancelchain/transaction.py`.

- [ ] **Step 1: Add the dataclass field**

Find (around line 121):

```python
    version: str = field(default=VERSION_1, compare=False, repr=False)
```

Add immediately after it (a new field on the `Transaction` dataclass):

```python
    version: str = field(default=VERSION_1, compare=False, repr=False)
    prev_hash: str | None = field(default=None, compare=False, repr=False)
```

- [ ] **Step 2: Conditional-append `prev_hash` in `data_csv`**

Find (around line 135):

```python
    @property
    def data_csv(self) -> str:
        return ','.join(
            [
                str(self.timestamp),
                str(self.address),
                str(self.public_key),
                ','.join(i.data_csv for i in self.inflows),
                ','.join(o.data_csv for o in self.outflows),
                str(self.version),
            ]
        )
```

Replace with:

```python
    @property
    def data_csv(self) -> str:
        fields = [
            str(self.timestamp),
            str(self.address),
            str(self.public_key),
            ','.join(i.data_csv for i in self.inflows),
            ','.join(o.data_csv for o in self.outflows),
            str(self.version),
        ]
        # A4.c v2: coinbases bind their block's prev_hash into the txid.
        # Conditional append keeps regular-txn data_csv (and txids)
        # byte-identical to the pre-binding scheme.
        if self.prev_hash is not None:
            fields.append(str(self.prev_hash))
        return ','.join(fields)
```

- [ ] **Step 3: Pydantic models — base optional, coinbase required, regular forbidden**

Find (around line 78-109):

```python
class TransactionModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

    timestamp: TimestampType
    txid: MillHashType
    address: AddressType
    public_key: PublicKeyType
    signature: Base64Type | None = None
    inflows: Annotated[
        list[InflowModel], Field(min_length=0, max_length=MAX_FLOWS)
    ]
    outflows: Annotated[
        list[OutflowModel], Field(min_length=1, max_length=MAX_FLOWS)
    ]
    version: Literal['1']

    @model_validator(mode='after')
    def validate_pk_address(self) -> Self:
        if not validate_address(self.public_key, self.address):
            raise ValueError(ADDRESS_MISMATCH_MSG)
        return self


class RegularTransactionModel(TransactionModel):
    inflows: Annotated[
        list[InflowModel], Field(min_length=1, max_length=MAX_FLOWS)
    ]


class CoinbaseTransactionModel(TransactionModel):
    inflows: Annotated[list[InflowModel], Field(min_length=0, max_length=0)]
    outflows: Annotated[list[OutflowModel], Field(min_length=1, max_length=4)]
```

Replace with (add `prev_hash` to the base as optional; require it on coinbase; forbid non-None on regular). `prev_hash` is a `MillHashType | None` — reuse the existing `MillHashType` validator used by `txid`:

```python
class TransactionModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

    timestamp: TimestampType
    txid: MillHashType
    address: AddressType
    public_key: PublicKeyType
    signature: Base64Type | None = None
    inflows: Annotated[
        list[InflowModel], Field(min_length=0, max_length=MAX_FLOWS)
    ]
    outflows: Annotated[
        list[OutflowModel], Field(min_length=1, max_length=MAX_FLOWS)
    ]
    version: Literal['1']
    prev_hash: MillHashType | None = None

    @model_validator(mode='after')
    def validate_pk_address(self) -> Self:
        if not validate_address(self.public_key, self.address):
            raise ValueError(ADDRESS_MISMATCH_MSG)
        return self


class RegularTransactionModel(TransactionModel):
    inflows: Annotated[
        list[InflowModel], Field(min_length=1, max_length=MAX_FLOWS)
    ]
    # Regular transactions are not block-bound; a prev_hash must not be set.
    prev_hash: None = None


class CoinbaseTransactionModel(TransactionModel):
    inflows: Annotated[list[InflowModel], Field(min_length=0, max_length=0)]
    outflows: Annotated[list[OutflowModel], Field(min_length=1, max_length=4)]
    # Coinbases must carry their block's prev_hash binding.
    prev_hash: MillHashType
```

Note: `MillHashType` is already imported/defined in this module (it types `txid`). If it's a Pydantic `Annotated` alias, `MillHashType | None` is valid. Confirm `MillHashType` exists in the file's type-alias block; if it's only usable as a bare annotation, set `prev_hash: MillHashType | None = None` exactly as written (it mirrors `signature: Base64Type | None = None` two lines up, which uses the same pattern).

- [ ] **Step 4: `txn_from_model_data` already forwards `prev_hash`**

`from_json` / `from_dict` call `txn_from_model_data(model.model_dump())`, which does `{**data, 'inflows': ..., 'outflows': ...}`. `model_dump()` includes `prev_hash` (None for regular, value for coinbase), and `**data` forwards it to the `Transaction(**...)` constructor. No change needed here — but confirm by reading `txn_from_model_data` (around line 63) that it spreads `**data` (it does). The new `Transaction.prev_hash` field accepts it.

- [ ] **Step 5: Thread `prev_hash` through `Transaction.coinbase`**

Find (around line 340):

```python
    def coinbase(
        cls,
        wallet: Wallet,
        reward: int,
        schadenfreude: int,
        grace: int,
        mudita: int,
    ) -> Self:
        outflows: list[Outflow] = []
        if reward:
            outflows.append(Outflow(amount=reward, address=wallet.address))
        if schadenfreude:
            outflows.append(
                Outflow(amount=schadenfreude, address=wallet.address)
            )
        if grace:
            outflows.append(Outflow(amount=grace, address=wallet.address))
        if mudita:
            outflows.append(Outflow(amount=mudita, address=wallet.address))
        cb = cls(outflows=outflows)
        cb.set_wallet(wallet)
        cb.seal()
        cb.sign()
        return cb
```

Replace with (add a required `prev_hash` param, set it BEFORE `seal()` so it's in `data_csv`/txid):

```python
    def coinbase(
        cls,
        wallet: Wallet,
        reward: int,
        schadenfreude: int,
        grace: int,
        mudita: int,
        prev_hash: str,
    ) -> Self:
        outflows: list[Outflow] = []
        if reward:
            outflows.append(Outflow(amount=reward, address=wallet.address))
        if schadenfreude:
            outflows.append(
                Outflow(amount=schadenfreude, address=wallet.address)
            )
        if grace:
            outflows.append(Outflow(amount=grace, address=wallet.address))
        if mudita:
            outflows.append(Outflow(amount=mudita, address=wallet.address))
        cb = cls(outflows=outflows, prev_hash=prev_hash)
        cb.set_wallet(wallet)
        cb.seal()
        cb.sign()
        return cb
```

- [ ] **Step 6: `to_dao` — persist `prev_hash`**

Find (around line 257):

```python
        return TransactionDAO.get(txid) or TransactionDAO(
            txid,
            self.version,
            timestamp_dt,
            address=self.address,
            public_key=self.public_key,
            signature=self.signature,
            inflow_daos=[
```

Replace the constructor call to pass `prev_hash` (Task 5 adds the DAO param):

```python
        return TransactionDAO.get(txid) or TransactionDAO(
            txid,
            self.version,
            timestamp_dt,
            address=self.address,
            public_key=self.public_key,
            signature=self.signature,
            prev_hash=self.prev_hash,
            inflow_daos=[
```

- [ ] **Step 7: `from_dao` — read `prev_hash` back**

Find (around line 307):

```python
    @classmethod
    def from_dao(cls, dao: Any) -> Self:
        return cls(
            timestamp=dt_2_iso(dao.timestamp),
            txid=dao.txid,
            address=dao.address,
            public_key=dao.public_key,
            signature=dao.signature,
            inflows=[
```

Replace to add `prev_hash=dao.prev_hash`:

```python
    @classmethod
    def from_dao(cls, dao: Any) -> Self:
        return cls(
            timestamp=dt_2_iso(dao.timestamp),
            txid=dao.txid,
            address=dao.address,
            public_key=dao.public_key,
            signature=dao.signature,
            prev_hash=dao.prev_hash,
            inflows=[
```

- [ ] **Step 8: Syntax + type check (will fail at DAO until Task 5; that's expected)**

```bash
uv run ruff check src/cancelchain/transaction.py
```

ruff clean. (mypy may flag `TransactionDAO(... prev_hash=...)` and `dao.prev_hash` until Task 5 adds the column — proceed to Task 5, then both type-check together.)

---

## Task 5: Add the `TransactionDAO.prev_hash` column

**Files:** Modify `src/cancelchain/models.py`.

- [ ] **Step 1: Add the mapped column**

Find (around line 64):

```python
    signature: Mapped[str | None] = mapped_column(String(500))
    blocks: Mapped[list[BlockDAO]] = relationship(
```

Add the `prev_hash` column after `signature`:

```python
    signature: Mapped[str | None] = mapped_column(String(500))
    prev_hash: Mapped[str | None] = mapped_column(String(100))
    blocks: Mapped[list[BlockDAO]] = relationship(
```

- [ ] **Step 2: Add the `__init__` parameter**

Find (around line 75):

```python
    def __init__(
        self,
        txid: str,
        version: str,
        timestamp: datetime.datetime,
        address: str | None = None,
        public_key: str | None = None,
        signature: str | None = None,
        inflow_daos: list[InflowDAO] | None = None,
        outflow_daos: list[OutflowDAO] | None = None,
    ) -> None:
        self.txid = txid
        self.version = version
        self.timestamp = timestamp
        self.address = address
        self.public_key = public_key
        self.signature = signature
        for inflow_dao in inflow_daos or []:
            inflow_dao.transaction = self
        for outflow_dao in outflow_daos or []:
            outflow_dao.transaction = self
```

Replace with (add `prev_hash` param + assignment, keyword-only-friendly position after `signature`):

```python
    def __init__(
        self,
        txid: str,
        version: str,
        timestamp: datetime.datetime,
        address: str | None = None,
        public_key: str | None = None,
        signature: str | None = None,
        prev_hash: str | None = None,
        inflow_daos: list[InflowDAO] | None = None,
        outflow_daos: list[OutflowDAO] | None = None,
    ) -> None:
        self.txid = txid
        self.version = version
        self.timestamp = timestamp
        self.address = address
        self.public_key = public_key
        self.signature = signature
        self.prev_hash = prev_hash
        for inflow_dao in inflow_daos or []:
            inflow_dao.transaction = self
        for outflow_dao in outflow_daos or []:
            outflow_dao.transaction = self
```

- [ ] **Step 3: Type check**

```bash
uv run ruff check src/cancelchain/models.py src/cancelchain/transaction.py
uv run ruff format --check src/cancelchain/models.py src/cancelchain/transaction.py
uv run mypy 2>&1 | tail -3
```

All clean now (the DAO column resolves the Task 4 mypy gaps). If `ruff format --check` reports a diff, run `uv run ruff format` on the two files.

---

## Task 6: Regenerate the base migration

**Files:** Modify `src/cancelchain/migrations/versions/`.

Per the pre-1.0 convention (no legacy installs), fold the schema change into the single initial migration rather than adding a delta.

- [ ] **Step 1: Delete the existing initial migration**

```bash
git rm src/cancelchain/migrations/versions/0ca0de5fb211_initial_schema.py
rm -f src/cancelchain/migrations/versions/__pycache__/0ca0de5fb211_initial_schema.*.pyc
```

- [ ] **Step 2: Regenerate against an empty DB**

```bash
TMPDB=$(mktemp --suffix=.db)
FLASK_SQLALCHEMY_DATABASE_URI="sqlite:///${TMPDB}" uv run cancelchain db migrate -m "initial schema"
rm -f "${TMPDB}"
```

This writes a new `src/cancelchain/migrations/versions/<rev>_initial_schema.py` autogenerated from the current models (which now include `transaction.prev_hash`).

- [ ] **Step 3: Hand-review the regenerated migration**

Open the new file. Confirm:
- `down_revision = None` (it is the initial migration).
- The `op.create_table('transaction', ...)` includes `sa.Column('prev_hash', sa.String(length=100), nullable=True)`.
- All 11 ORM tables + the `block_transaction` association table are present (compare against the deleted file's table list: `transaction`, `outflow`, `inflow`, `block`, `block_transaction`, `longest_chain_block`, `chain`, `pending_txn`, `pending_ioflow`, `chain_fill`, `chain_fill_block`, `api_token`).
- The naming convention prefixes (`ix_`/`uq_`/`fk_`/`pk_`) match the deleted file (Alembic uses `Base.metadata`'s naming_convention from Phase 8).

If autogenerate dropped a CHECK constraint or server default that the old file had, hand-edit it back in (per CLAUDE.md — autogenerate is imperfect).

- [ ] **Step 4: db check gate**

```bash
git add src/cancelchain/migrations/versions/
TMPDB=$(mktemp --suffix=.db)
FLASK_SQLALCHEMY_DATABASE_URI="sqlite:///${TMPDB}" uv run cancelchain db upgrade
FLASK_SQLALCHEMY_DATABASE_URI="sqlite:///${TMPDB}" uv run cancelchain db check
rm -f "${TMPDB}"
```

`db upgrade` succeeds; `db check` reports "No differences detected" (models == regenerated migration).

---

## Task 7: Thread `prev_hash` into `Block.create_coinbase`

**Files:** Modify `src/cancelchain/block.py`.

- [ ] **Step 1:** Find (around line 208):

```python
    def create_coinbase(self, wallet: Wallet, reward: int) -> Transaction:
        return Transaction.coinbase(
            wallet, reward, self.schadenfreude, self.grace, self.mudita
        )
```

`seal` (line 221-229) calls `add_coinbase` → `create_coinbase`, and `seal` already raises `UnlinkedBlockError` if `self.prev_hash` is None — so `self.prev_hash` is guaranteed set here. Replace with:

```python
    def create_coinbase(self, wallet: Wallet, reward: int) -> Transaction:
        if self.prev_hash is None:
            raise UnlinkedBlockError()
        return Transaction.coinbase(
            wallet,
            reward,
            self.schadenfreude,
            self.grace,
            self.mudita,
            prev_hash=self.prev_hash,
        )
```

(The explicit `prev_hash is None` guard satisfies mypy's `str | None` → `str` narrowing for the `Transaction.coinbase(prev_hash: str)` param; `UnlinkedBlockError` is already imported in block.py — confirm with `grep -n UnlinkedBlockError src/cancelchain/block.py`; it is used by `seal`.)

- [ ] **Step 2:** Verify:

```bash
uv run ruff check src/cancelchain/block.py
uv run ruff format --check src/cancelchain/block.py
uv run mypy 2>&1 | tail -2
```

All clean.

---

## Task 8: Add the binding check in `validate_block_coinbase`

**Files:** Modify `src/cancelchain/chain.py`.

- [ ] **Step 1: Add the binding check**

Find (around line 278):

```python
    def validate_block_coinbase(self, block: Block) -> None:
        block.validate_coinbase()
        reward = self.block_reward(block)
        cb = block.coinbase
        if cb is not None:
            outflow = cb.get_outflow(0)
            if outflow is not None and outflow.amount != reward:
                raise InvalidCoinbaseErrorRewardError()
```

Replace with:

```python
    def validate_block_coinbase(self, block: Block) -> None:
        block.validate_coinbase()
        reward = self.block_reward(block)
        cb = block.coinbase
        if cb is not None:
            # A4.c v2: a coinbase is bound to the block it rewards via its
            # prev_hash (which is part of the coinbase's hashed txid). A
            # replay carries the wrong parent; reject on the mismatch. This
            # is a purely local check — no lineage walk, no self.last_block
            # dependence — so it is correct in both the add-block path and
            # Chain.validate() full-chain revalidation.
            if cb.prev_hash != block.prev_hash:
                raise MismatchedCoinbaseError()
            outflow = cb.get_outflow(0)
            if outflow is not None and outflow.amount != reward:
                raise InvalidCoinbaseErrorRewardError()
```

- [ ] **Step 2: Import `MismatchedCoinbaseError`**

```bash
grep -n 'InvalidCoinbaseErrorRewardError\|MismatchedCoinbaseError' src/cancelchain/chain.py
```

chain.py imports `InvalidCoinbaseErrorRewardError` from `cancelchain.exceptions`. Add `MismatchedCoinbaseError` to that import block (alphabetical order — it sorts after `InvalidTransactionError` etc.; place it correctly per ruff's isort). Do NOT add `InvalidCoinbaseError` (unused in chain.py).

- [ ] **Step 3: Verify**

```bash
uv run ruff check src/cancelchain/chain.py
uv run ruff format --check src/cancelchain/chain.py
uv run mypy 2>&1 | tail -2
```

All clean.

- [ ] **Step 4: Full suite — the 17 v1-breaking scenarios now pass**

```bash
uv run pytest 2>&1 | tail -3
```

Expected: `237 passed, 5 xfailed, 1 skipped` EXCEPT the A4.c test is now XPASS(strict) — so you'll see `1 failed` (the XPASS) + `236 passed, 4 xfailed, 1 skipped`. The XPASS is expected (the fix works; Task 9 removes the decorator). Crucially: NO other failures — the legitimate-consecutive-block tests (`test_chain`, `test_models`, `test_miller`, `test_command`) all pass because coinbases are now unique per block.

To confirm cleanly, deselect the not-yet-un-xfailed A4.c test:

```bash
uv run pytest --deselect 'tests/test_verification_audit.py::test_a4_c_ii_coinbase_replay_inflates_balance' 2>&1 | tail -3
```

Expected: `237 passed, 4 xfailed, 1 skipped`, no failures.

---

## Task 9: Update the audit test module

**Files:** Modify `tests/test_verification_audit.py`.

- [ ] **Step 1: Confirm the fix works (under --runxfail)**

```bash
uv run pytest --runxfail tests/test_verification_audit.py::test_a4_c_ii_coinbase_replay_inflates_balance 2>&1 | tail -10
```

Expected: `1 passed` — the replayed coinbase (bound to B_orig's parent) is rejected when placed in B_adv (whose prev_hash is B_orig's hash) via `MismatchedCoinbaseError` (an `InvalidCoinbaseError`, which the test's `pytest.raises(InvalidCoinbaseError)` matches).

- [ ] **Step 2: Remove the A4.c xfail decorator**

Find and delete the `@pytest.mark.xfail(reason=..., strict=True,)` decorator block immediately above `def test_a4_c_ii_coinbase_replay_inflates_balance`. Leave the `def` + body.

- [ ] **Step 3: Update the A4.c test docstring**

Replace the docstring's pre-fix "Expected after remediation / Observed today" wording with the v2 behavior. The new docstring:

```python
    """A4.c.ii: replaying another miller's coinbase in a fresh block.

    Pre-state: Local chain has a single mined block B_orig whose coinbase
    T_cb is bound (via prev_hash) to B_orig's parent and pays the milling
    wallet REWARD.
    Attack: The adversary (MILLER) builds B_adv extending B_orig with
    txns=[T_cb] only, reusing T_cb verbatim as B_adv's coinbase, mills
    PoW, and invokes Node.receive_block.
    Behavior (post-remediation, verified by this test): T_cb's bound
    prev_hash is B_orig's parent, but B_adv.prev_hash is B_orig's hash.
    Chain.validate_block_coinbase raises MismatchedCoinbaseError (a
    subclass of InvalidCoinbaseError) on the binding mismatch;
    receive_block propagates the failure and B_adv is not persisted. The
    coinbase is intrinsically block-bound, so it cannot be replayed onto
    any other block-position.
    """
```

- [ ] **Step 4: Invert the cross-fork test**

The v1 cross-fork test (`test_a4_c_cross_fork_coinbase_replay_accepted`) was never added to main (it lived only in the v1 plan, which shipped docs-only and v1 was BLOCKED before impl). **Confirm it does not exist:**

```bash
grep -n 'cross_fork' tests/test_verification_audit.py
```

Expected: no matches (the v1 cross-fork test was never implemented). If it somehow exists, delete it — v2 makes cross-fork coinbase replay invalid (binding mismatch), so an "accepted" assertion would be wrong.

Then add a v2 binding test that asserts the block-bound semantics directly. Append to `tests/test_verification_audit.py` (the imports `Block`, `Miller`, `REWARD`, `Transaction`, `now`, `now_iso` are already present; add `Chain` from `cancelchain.chain` and `MismatchedCoinbaseError` from `cancelchain.exceptions` to the import block if not present):

```python
def test_a4_c_coinbase_block_binding(app, time_machine, wallet) -> None:
    """A4.c v2: coinbases are bound to their block via prev_hash.

    Verifies the two halves of the fix:
    1. Two consecutive legitimate blocks (same wallet, same second under
       easy-mill) have DIFFERENT coinbase txids, because each coinbase's
       prev_hash differs (block N+1 extends block N). This is the
       root-cause fix for the read-side balance inflation.
    2. validate_block_coinbase raises MismatchedCoinbaseError when a
       coinbase's bound prev_hash does not equal its block's prev_hash.
    """
    with app.app_context():
        now_dt = now()
        when_dt = now_dt - datetime.timedelta(hours=1)
        time_machine.move_to(when_dt)
        m = Miller(milling_wallet=wallet)
        # Two consecutive blocks, no time advance (same wall-clock second).
        b0 = m.create_block()
        m.mill_block(b0)
        b1 = m.create_block()
        m.mill_block(b1)
        cb0 = b0.coinbase
        cb1 = b1.coinbase
        assert cb0 is not None
        assert cb1 is not None
        # Part 1: distinct coinbase txids despite same wallet/second/reward.
        assert cb0.txid != cb1.txid
        # And each coinbase is bound to its own block's parent.
        assert cb0.prev_hash == b0.prev_hash
        assert cb1.prev_hash == b1.prev_hash

        # Part 2: a coinbase whose binding mismatches its block is rejected.
        chain = m.longest_chain
        assert chain is not None
        # cb0 is bound to b0.prev_hash (genesis hash), but we validate it
        # as if it were the coinbase of b1 (whose prev_hash is b0.hash).
        b_mismatch = Block()
        chain.link_block(b_mismatch)
        cb0_replay = Transaction.from_json(cb0.to_json())
        b_mismatch.add_txn(cb0_replay, is_coinbase=True)
        b_mismatch.merkle_root = b_mismatch.get_merkle_root()
        b_mismatch.timestamp = now_iso()
        b_mismatch.mill()
        with pytest.raises(MismatchedCoinbaseError):
            chain.validate_block_coinbase(b_mismatch)
```

- [ ] **Step 5: Update the module docstring**

The module docstring claims every test is `@pytest.mark.xfail(strict=True)` — stale (A2.e already remediated; A4.c now too). Replace the module docstring (lines 1-21) with the version describing open-finding (xfail) vs remediated (plain pass) vs non-regression states — use the exact text from the merged v1 plan's Task 5 Step 8 (it was written for this purpose):

```python
"""Demonstration and regression tests for the verification pipeline audit.

Each finding in
docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md
has a corresponding test here, in one of two states:

- **Open findings** carry `@pytest.mark.xfail(strict=True)`: the xfail
  demonstrates the gap still exists; strict=True means that if the test
  starts unexpectedly passing (because remediation landed), CI fails,
  forcing the remediation PR to remove the marker.
- **Remediated findings** have had the xfail decorator removed and now
  pass as plain regression tests guarding the fix (e.g. A2.e, A4.c).

The module may also hold non-regression / invariant tests that assert a
fix's intended behavior — e.g. test_a4_c_coinbase_block_binding, which
checks coinbases are bound to their block via prev_hash.

To verify a still-xfailed test genuinely demonstrates a gap (rather than
failing for an unrelated reason), run:

    uv run pytest --runxfail tests/test_verification_audit.py

That runs the xfail tests as if unmarked, surfacing the actual failure
mode; the already-remediated tests pass under it too.

Finding IDs are referenced in each test's docstring (and, for still-open
findings, the xfail reason string) in the form A<N>.<letter> matching the
audit document's per-adversary sections.
"""
```

- [ ] **Step 6: Verify the module**

```bash
uv run pytest tests/test_verification_audit.py 2>&1 | tail -5
uv run pytest --runxfail tests/test_verification_audit.py 2>&1 | tail -5
uv run ruff check tests/test_verification_audit.py
uv run ruff format --check tests/test_verification_audit.py
uv run mypy 2>&1 | tail -2
```

Expected: module shows `3 passed, 4 xfailed` (A4.c demonstration + the new binding test + … wait: count = A2.e pass + A4.c pass + binding test pass = 3 passed; A1.f, A7.b, A7.e, A7.h = 4 xfailed). `--runxfail` shows `3 passed, 4 failed`. ruff/mypy clean. (If `ruff format --check` flags the new test, run `uv run ruff format tests/test_verification_audit.py`.)

---

## Task 10: Audit doc + ROADMAP + v1 supersession banners

**Files:** Modify `docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md`, `docs/superpowers/ROADMAP.md`, and prepend banners to the two v1 docs.

- [ ] **Step 1: Audit doc — close A4.c (4 spots)**

Same 4 closure edits the v1 plan specified, but describing the v2 binding fix:
1. Remove the A4.c row from the Findings table.
2. Replace the §Adversary 4 → Attack c.ii run (`**Outcome:**` through `**Demonstration test:**`) with a post-remediation block: `**Outcome:** REJECTED` — the coinbase is bound to its block via `prev_hash` (part of its txid); `validate_block_coinbase` raises `MismatchedCoinbaseError` when `cb.prev_hash != block.prev_hash`, so the replay (carrying B_orig's parent) is rejected in B_adv. Include a one-paragraph historical note (the pre-fix m2m inflation) + `**Result:** Validation correctly rejects. No finding.` + the demonstration-test pointer (`test_a4_c_ii_coinbase_replay_inflates_balance`, plus `test_a4_c_coinbase_block_binding`).
3. Executive summary: "Six findings ... two remediated (A2.e, A4.c); four remain open"; severity "0 Critical / 0 High / 0 Medium / 4 Low (post-A4.c)".
4. Recommendations §2 (A4.c): mark ✅ Implemented, describing the v2 prev_hash binding (not the v1 lineage check).

Verify:

```bash
grep -c '^| A[1-7]\.' docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md
grep -c '^\*\*Finding A' docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md
```

Both = 4.

- [ ] **Step 2: ROADMAP — close A4.c**

Remove A4.c from the open "Audit remediation" list (renumber remaining). Add to "Closed items":

```markdown
- ✅ **Audit finding A4.c — coinbase-txid replay inflates miller `wallet_balance`** — closed by docs PRs [#88](https://github.com/gumptionthomas/cancelchain/pull/88) (v1, superseded) + [#<N_docs>](https://github.com/gumptionthomas/cancelchain/pull/<N_docs>) (v2 design+plan) and impl PR [#<N_impl>](https://github.com/gumptionthomas/cancelchain/pull/<N_impl>). The v1 lineage-uniqueness check proved unimplementable (coinbase txids collide for legitimate same-second blocks); v2 binds the block's `prev_hash` into the coinbase txid so consecutive blocks have unique coinbases, and validates `cb.prev_hash == block.prev_hash` (raising `MismatchedCoinbaseError`) to reject replays. Added a nullable `TransactionDAO.prev_hash` column via a regenerated base migration (pre-1.0, no legacy installs). Brings audit severity to 0 Critical / 0 High / 0 Medium / 4 Low.
```

- [ ] **Step 3: v1 supersession banners**

Prepend to the TOP of both `docs/superpowers/specs/2026-05-30-a4c-coinbase-uniqueness-design.md` and `docs/superpowers/plans/2026-05-30-a4c-coinbase-uniqueness.md`:

```markdown
> **⚠️ SUPERSEDED (2026-05-30).** This v1 approach (chain-lineage coinbase-txid uniqueness check) proved unimplementable: coinbase txids are not unique across legitimately-mined consecutive blocks (no inflows; second-resolution timestamps). See `docs/superpowers/specs/2026-05-30-a4c-v2-coinbase-binding-design.md` for the v2 design (bind the block's prev_hash into the coinbase txid). Kept for historical reference only.

```

---

## Task 11: Gates + commit + push + open PR

- [ ] **Step 1: Full gate sweep**

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy
uv run pytest 2>&1 | tail -3
```

All exit 0. Pytest shows `239 passed, 4 xfailed, 1 skipped` (was 237+5; A4.c un-xfailed +1, new binding test +1).

- [ ] **Step 2: --runxfail + db check**

```bash
uv run pytest --runxfail tests/test_verification_audit.py 2>&1 | tail -3
TMPDB=$(mktemp --suffix=.db)
FLASK_SQLALCHEMY_DATABASE_URI="sqlite:///${TMPDB}" uv run cancelchain db upgrade
FLASK_SQLALCHEMY_DATABASE_URI="sqlite:///${TMPDB}" uv run cancelchain db check
rm -f "${TMPDB}"
```

`--runxfail` → `3 passed, 4 failed`. db upgrade OK; db check "No differences detected".

- [ ] **Step 3: Commit**

```bash
git add src/cancelchain/exceptions.py src/cancelchain/transaction.py src/cancelchain/models.py src/cancelchain/block.py src/cancelchain/chain.py src/cancelchain/migrations/versions/ tests/test_verification_audit.py docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md docs/superpowers/ROADMAP.md docs/superpowers/specs/2026-05-30-a4c-coinbase-uniqueness-design.md docs/superpowers/plans/2026-05-30-a4c-coinbase-uniqueness.md
git commit -m "$(cat <<'EOF'
fix(a4c-v2): bind coinbase to its block via prev_hash

Closes audit finding A4.c (Medium). Binds the block's prev_hash into
the coinbase transaction's hashed data (its txid), so legitimately-
mined consecutive blocks have distinct coinbase txids even in the same
second — closing the read-side wallet_balance inflation at its root —
and validates cb.prev_hash == block.prev_hash in
Chain.validate_block_coinbase (raising MismatchedCoinbaseError) to
reject coinbase replay. The binding check is purely local: no lineage
walk, no self.last_block parent-start, no Chain.validate() revalidation
hazard (the bug class that made the v1 lineage-check approach
unimplementable — coinbase txids collide for legitimate same-second
blocks).

- Transaction gains an optional prev_hash field, conditionally appended
  to data_csv so regular-txn txids are byte-unchanged; coinbases set it
  to their block's parent hash. CoinbaseTransactionModel requires it,
  RegularTransactionModel forbids it.
- TransactionDAO gains a nullable prev_hash column; the single initial
  migration is regenerated to include it (pre-1.0, no legacy installs).
- Block.create_coinbase threads self.prev_hash into Transaction.coinbase.
- Coinbases are now block-specific: cross-fork coinbase replay is
  correctly rejected (inverting v1's mistaken "legitimate" premise).

Test went from xfail to a real pass; a new test_a4_c_coinbase_block_
binding asserts consecutive blocks have distinct coinbase txids and
that a mismatched binding is rejected. Full suite 239 passed, 4 xfailed,
1 skipped. Audit doc + ROADMAP record A4.c closed; v1 spec/plan carry
supersession banners.

Design: docs/superpowers/specs/2026-05-30-a4c-v2-coinbase-binding-design.md
Plan: docs/superpowers/plans/2026-05-30-a4c-v2-coinbase-binding.md

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 4: Push + open PR**

```bash
git push -u origin fix/a4c-v2-coinbase-binding
gh pr create --base main --title "fix(a4c-v2): bind coinbase to its block via prev_hash" --body "$(cat <<'EOF'
## Summary

Closes audit finding A4.c (Medium) via coinbase-to-block binding, superseding the unimplementable v1 lineage check.

The coinbase now carries its block's \`prev_hash\` in its hashed data (its txid), so legitimately-mined consecutive blocks have distinct coinbase txids even in the same second — closing the \`wallet_balance\` inflation at its root. \`Chain.validate_block_coinbase\` validates \`cb.prev_hash == block.prev_hash\` (raising \`MismatchedCoinbaseError\`) to reject replays. The check is purely local — no lineage walk, no \`self.last_block\` hazard.

## Why v1 was abandoned

The v1 lineage-uniqueness check (PR #88, docs-only) proved unimplementable: a coinbase has no inflows and \`data_csv\` hashes only \`(timestamp, address, pubkey, outflows, version)\` at second resolution, so two same-miller same-reward coinbases in the same second are byte-identical. v1's check rejected 17 legitimate-block tests. v2 fixes the root cause.

## Implementation notes

- \`Transaction.prev_hash\` (optional), conditionally appended to \`data_csv\` so regular-txn txids are byte-unchanged; \`CoinbaseTransactionModel\` requires it, \`RegularTransactionModel\` forbids it.
- Nullable \`TransactionDAO.prev_hash\` column; the single initial migration is **regenerated** to include it (pre-1.0, no legacy installs — append-only Alembic begins at the first tagged release).
- \`Block.create_coinbase\` threads \`self.prev_hash\` into \`Transaction.coinbase\`.
- Coinbases are now block-specific; cross-fork coinbase replay is correctly rejected (inverting v1's mistaken premise).

## Documentation

- Audit doc: A4.c closed (Findings table, Attack c.ii trace, Executive summary, Recommendations §2) describing the v2 binding fix.
- ROADMAP: A4.c moved to closed.
- v1 spec/plan carry supersession banners pointing to v2.

## Test plan

- [x] All 5 CI gates clean (ruff check + ruff format + pytest + mypy + db check).
- [x] \`uv run pytest\` → \`239 passed, 4 xfailed, 1 skipped\`.
- [x] \`uv run pytest --runxfail tests/test_verification_audit.py\` → \`3 passed, 4 failed\`.
- [x] The 17 v1-breaking legitimate-block tests pass (coinbases unique per block).
- [ ] CI green on 3.12 and 3.13.
- [ ] \`docker build --target builder\` succeeds.

Design: \`docs/superpowers/specs/2026-05-30-a4c-v2-coinbase-binding-design.md\`
Plan: \`docs/superpowers/plans/2026-05-30-a4c-v2-coinbase-binding.md\`

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

- [ ] **Step 5: Fill in ROADMAP PR numbers**

After `gh pr create` returns the number, `sed` the `#<N_docs>` (the v2 docs PR number, already merged — find in `git log main`) and `#<N_impl>` placeholders in `docs/superpowers/ROADMAP.md`, then commit as a SEPARATE additive commit (don't amend) and push.

- [ ] **Step 6: Stop — controller handles wor + mwg.**

---

## Task 12: Acceptance verification (post-merge)

```bash
git checkout main && git pull --ff-only
# Source: binding field + check
grep -n 'prev_hash' src/cancelchain/transaction.py | head
grep -n 'MismatchedCoinbaseError' src/cancelchain/chain.py src/cancelchain/exceptions.py
grep -n "prev_hash" src/cancelchain/models.py
# Migration includes the column
grep -rn "prev_hash" src/cancelchain/migrations/versions/
# Tests
uv run pytest 2>&1 | tail -3                              # 239 passed, 4 xfailed, 1 skipped
uv run pytest --runxfail tests/test_verification_audit.py 2>&1 | tail -3   # 3 passed, 4 failed
# Gates
uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy
TMPDB=$(mktemp --suffix=.db); FLASK_SQLALCHEMY_DATABASE_URI="sqlite:///${TMPDB}" uv run cancelchain db upgrade && FLASK_SQLALCHEMY_DATABASE_URI="sqlite:///${TMPDB}" uv run cancelchain db check; rm -f "${TMPDB}"
# Audit doc + ROADMAP
grep -c '^| A[1-7]\.' docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md   # 4
grep 'SUPERSEDED' docs/superpowers/specs/2026-05-30-a4c-coinbase-uniqueness-design.md     # banner present
# Docker
docker build --target builder -t cc-a4cv2-final .
```

All pass → A4.c is closed (v2). Audit severity: 0 Critical / 0 High / 0 Medium / 4 Low. Next remediation per ROADMAP: A7.b.

---

## Risks and watchpoints

### Risk: a coinbase built directly via `Transaction.coinbase(...)` without `prev_hash`

`Transaction.coinbase` now requires `prev_hash` (positional/keyword). Any caller that built a coinbase without it breaks at call time (good — surfaces immediately). Grep before committing: `grep -rn '\.coinbase(' src tests` — confirm every call passes `prev_hash` (production: only `Block.create_coinbase`, which Task 7 updates; tests: any direct construction needs the param, but most go through `Block.seal`/`mill_block`).

### Risk: `MillHashType | None` Pydantic annotation

If `MillHashType` is a constrained `Annotated` type, `MillHashType | None = None` on the base model and `prev_hash: None = None` on `RegularTransactionModel` must both validate. The pattern mirrors `signature: Base64Type | None = None` (already in the model). If Pydantic rejects the `None`-override on the regular model, fall back to a `@model_validator(mode='after')` on `RegularTransactionModel` that raises if `prev_hash is not None`. Task 4 Step 3's form is the primary; this is the fallback.

### Risk: regenerated migration drifts from the deleted one (beyond the new column)

The regenerated initial migration must be identical to the old one EXCEPT for the added `prev_hash` column. Task 6 Step 3's hand-review compares table-by-table. The `cancelchain db check` gate (Step 4) is the backstop — it fails on any model/migration mismatch.

### Risk: regular-txn txids accidentally change

The conditional-append in `data_csv` (Task 4 Step 2) is load-bearing: it must append `prev_hash` ONLY when non-None. A regression test in `test_a4_c_coinbase_block_binding` could be extended, but the existing regular-txn tests (`test_transaction.py`) already pin regular-txn txids — if the conditional were wrong (unconditional append), those tests fail. Run `uv run pytest tests/test_transaction.py` explicitly after Task 4.
