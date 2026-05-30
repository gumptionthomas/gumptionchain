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
- Test baseline on main: **237 passed, 5 xfailed, 1 skipped** — **but only when pytest runs with a wide terminal.** `tests/test_command.py::test_create_wallet` is terminal-width-dependent: with a narrow `COLUMNS` the CLI wraps the wallet filename and embeds a newline that `result.output.strip()` does not remove, raising `FileNotFoundError`. It is a pre-existing bug on `main`, **unrelated to A4.c** (CI passes because GitHub Actions' non-TTY width is wide enough). **Run every `pytest` command in this plan with `COLUMNS=200`** (e.g. `COLUMNS=200 uv run pytest ...`) so the baseline is the clean `237 passed`. Do NOT fix `test_create_wallet` here (no scope creep — it warrants its own PR); just make the gate robust with `COLUMNS=200`.
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
| 7b | impl PR | `tests/conftest.py`, `tests/test_transaction.py`, `tests/test_chain.py` — update direct + bare-`Transaction()` coinbase callers to pass `prev_hash`; add the regular-txn `data_csv` regression test |
| 8 | impl PR | `src/cancelchain/chain.py` — binding check in `validate_block_coinbase` |
| 9 | impl PR | `tests/test_verification_audit.py` — un-xfail A4.c, add the v2 binding test (no v1 cross-fork test exists to invert), module docstring |
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

Expected: branch `docs/a4c-v2-coinbase-binding`; count `>= 2` (the spec commit, this plan commit, plus any review-revision commits — the docs PR went through review rounds before merging, so do not gate on an exact count). Both `docs/superpowers/specs/2026-05-30-a4c-v2-coinbase-binding-design.md` and `docs/superpowers/plans/2026-05-30-a4c-v2-coinbase-binding.md` are already git-tracked (Task 1 was completed when the docs PR opened). If the docs PR has already merged to main, this whole Task 1 is done — skip to Task 2.

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
demonstration test, and adds a v2 block-binding test (coinbases are
block-bound; no v1 cross-fork test ever existed to invert).

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
uv run mypy && uv run ruff check src tests && COLUMNS=200 uv run pytest 2>&1 | tail -3
```

Expected: clean mypy/ruff; pytest `237 passed, 5 xfailed, 1 skipped`. **The `COLUMNS=200` is required** — without it `test_create_wallet` fails on a narrow terminal (pre-existing, unrelated; see Prerequisites). If you still see a failure OTHER than `test_create_wallet`, STOP / BLOCKED.

- [ ] **Step 2:** Confirm the A4.c test is currently xfail:

```bash
uv run pytest tests/test_verification_audit.py::test_a4_c_ii_coinbase_replay_inflates_balance 2>&1 | tail -3
```

Expected: `1 xfailed`.

---

## Task 3: Add `MismatchedCoinbaseError`

**Files:** Modify `src/cancelchain/exceptions.py`.

- [ ] **Step 1:** Locate the existing `InvalidCoinbaseErrorRewardError` class (near line 101):

```python
class InvalidCoinbaseErrorRewardError(InvalidCoinbaseError):
    pass
```

Append **only this new class** immediately after it (two blank lines, then the class — do NOT re-paste `InvalidCoinbaseErrorRewardError`):

```python
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

Find the existing `version` field on the `Transaction` dataclass (around line 121):

```python
    version: str = field(default=VERSION_1, compare=False, repr=False)
```

Append **only this new field line** immediately after it (do NOT re-paste the `version` line):

```python
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

## Task 7b: Update direct `Transaction.coinbase(...)` callers in tests

**Files:** Modify `tests/conftest.py`, `tests/test_transaction.py`.

`Transaction.coinbase` now requires a `prev_hash` argument. Production has exactly one caller (`Block.create_coinbase`, updated in Task 7). Tests have three *direct* callers that build standalone coinbases (not via `Block.seal`), and they will fail with a missing-argument `TypeError` unless updated. Use `GENESIS_HASH` (`mill_hash_str('GENESIS')`, a valid `MillHashType`) as the binding for these standalone, block-less coinbases — they test serialization / DB round-trip / pending-pool behavior, not block binding, so any valid hash works and `GENESIS_HASH` reads as "no real parent block."

- [ ] **Step 1: `tests/conftest.py` — the `valid_coinbase_txn` fixture**

The fixture params (around conftest.py:273-280) are `(reward, S, G, M)` tuples spread into `Transaction.coinbase(wallet, *request.param)`. Add `GENESIS_HASH` as the `prev_hash` keyword. First ensure `GENESIS_HASH` is imported (check the top of conftest.py; if absent, add `from cancelchain.chain import GENESIS_HASH` — confirm the exact existing `from cancelchain.chain import ...` line and extend it).

Find (around conftest.py:279-280):

```python
def valid_coinbase_txn(request, wallet):
    return Transaction.coinbase(wallet, *request.param)
```

Replace with:

```python
def valid_coinbase_txn(request, wallet):
    return Transaction.coinbase(wallet, *request.param, prev_hash=GENESIS_HASH)
```

- [ ] **Step 2: `tests/test_transaction.py` — `test_db` and `test_pending_txns`**

Ensure `GENESIS_HASH` is imported at the top of `test_transaction.py` (add `from cancelchain.chain import GENESIS_HASH` if absent — confirm any existing `from cancelchain.chain import ...` line and extend it).

Find `test_db` (around test_transaction.py:122-127):

```python
def test_db(app, wallet):
    with app.app_context():
        cb = Transaction.coinbase(wallet, 20, 10, 9, 8)
        cb.to_db()
        cb_copy = Transaction.from_db(cb.txid)
        assert cb_copy == cb
```

Replace with (thread `prev_hash`, AND add an explicit `prev_hash` round-trip assertion — **this is load-bearing**: `Transaction.__eq__` only compares `timestamp` + `txid` (`prev_hash` is declared `compare=False`), and `from_dao` copies `txid` verbatim without recomputing it, so `cb_copy == cb` passes even if `from_dao` drops `prev_hash`. Without the explicit assertion, the `to_dao`/`from_dao` `prev_hash` wiring (Task 4 Steps 6-7) is NOT covered by `test_db`; the only loud signal of an omission would be an unrelated-looking `chain.validate()` failure elsewhere):

```python
def test_db(app, wallet):
    with app.app_context():
        cb = Transaction.coinbase(wallet, 20, 10, 9, 8, prev_hash=GENESIS_HASH)
        cb.to_db()
        cb_copy = Transaction.from_db(cb.txid)
        assert cb_copy == cb
        # prev_hash is compare=False, so == ignores it; assert the
        # DAO round-trip restored it explicitly, and that the reloaded
        # coinbase's recomputed txid still matches (validate_txid).
        assert cb_copy.prev_hash == cb.prev_hash == GENESIS_HASH
        cb_copy.validate_coinbase()
```

Find (around test_transaction.py:131):

```python
    cb = Transaction.coinbase(wallet, 10, 0, 0, 0)
```

Replace with:

```python
    cb = Transaction.coinbase(wallet, 10, 0, 0, 0, prev_hash=GENESIS_HASH)
```

- [ ] **Step 3: Update the bare-`Transaction()` coinbase constructions in `tests/test_chain.py`**

**Critical (caught by the A4.c-v2 review workflow — a `grep '.coinbase('` does NOT find these):** `test_validate_block_coinbase` in `tests/test_chain.py` builds two coinbases *without* `Transaction.coinbase()` — it constructs a bare `Transaction()`, adds an outflow, then attaches it via `block.add_txn(cb, is_coinbase=True)`. Post-fix, `add_txn(is_coinbase=True)` calls `cb.validate_coinbase()` → `CoinbaseTransactionModel.model_validate(...)`, which now *requires* a non-None `prev_hash`. These bare coinbases have `prev_hash=None`, so `validate_coinbase` raises `InvalidTransactionError` AT the `add_txn` line — *before* the test's `with pytest.raises(InvalidBlockError, match='InvalidCoinbaseError'):` block — crashing `test_validate_block_coinbase`. Both blocks are already linked when their coinbase is built, so `blockN.prev_hash` is available; set it on the coinbase before sealing.

Find (around test_chain.py:562, the `cb2` construction — `block2` was linked at the preceding `chain.link_block(block2)`):

```python
        cb2 = Transaction()
        cb2.add_outflow(
            Outflow(amount=chain.block_reward(), address=wallet.address)
        )
        cb2.set_wallet(wallet)
        cb2.seal()
        cb2.sign()
        block2.add_txn(cb2, is_coinbase=True)
```

Replace the first line so the coinbase is bound to its block (the rest is unchanged):

```python
        cb2 = Transaction(prev_hash=block2.prev_hash)
        cb2.add_outflow(
            Outflow(amount=chain.block_reward(), address=wallet.address)
        )
        cb2.set_wallet(wallet)
        cb2.seal()
        cb2.sign()
        block2.add_txn(cb2, is_coinbase=True)
```

Find (around test_chain.py:595, the `cb3` construction — `block3` was linked at the preceding `chain.link_block(block3)`):

```python
        cb3 = Transaction()
        cb3.add_outflow(
            Outflow(amount=chain.block_reward(), address=wallet.address)
        )
        cb3.set_wallet(wallet)
        cb3.seal()
        cb3.sign()
        block3.add_txn(cb3, is_coinbase=True)
```

Replace the first line:

```python
        cb3 = Transaction(prev_hash=block3.prev_hash)
        cb3.add_outflow(
            Outflow(amount=chain.block_reward(), address=wallet.address)
        )
        cb3.set_wallet(wallet)
        cb3.seal()
        cb3.sign()
        block3.add_txn(cb3, is_coinbase=True)
```

Both tests still exercise their intended assertion. To be precise about *why* setting `prev_hash` is the right fix (the two checks are at different layers): `add_txn(is_coinbase=True)` → `cb.validate_coinbase()` runs `CoinbaseTransactionModel`, which only requires `prev_hash` to be a **non-None, well-formed `MillHashType`** (a FORMAT check) — it does NOT compare `prev_hash` to the block. With a bare `Transaction()` (`prev_hash=None`) that format check raises at the `add_txn` line, before the test's `pytest.raises` block — the crash this step fixes. Setting `cb.prev_hash = blockN.prev_hash` satisfies that format requirement (any valid `MillHashType` would, but the block's own `prev_hash` is the semantically correct value). The test then proceeds to `chain.add_block`, where `block.validate()` raises the original S/G/M-mismatch `InvalidCoinbaseError` (the coinbase has no S/G/M outflows matching the block's regular txn) — *inside* `block.validate()`, before `Chain.validate_block_coinbase`'s binding-equality check is even reached. So these two scenarios exercise the S/G/M check, not the binding-value check; the binding-equality check (`cb.prev_hash != block.prev_hash`) is covered separately by `test_a4_c_coinbase_block_binding` (Task 9). Either way the assertion (`InvalidCoinbaseError`) now fires inside the `pytest.raises` block as intended.

- [ ] **Step 4: Confirm every coinbase-construction site is covered**

A `grep '.coinbase('` is insufficient (it misses bare `Transaction()` coinbases). Audit by where a coinbase is *attached to a block* instead:

```bash
grep -rn 'is_coinbase=True' src tests
grep -rn '\.coinbase(' src tests
```

`is_coinbase=True` sites: `src/cancelchain/block.py` (`add_coinbase`, fed by `create_coinbase` — Task 7), `tests/test_chain.py` ×2 (Step 3, now `prev_hash=blockN.prev_hash`), `tests/test_verification_audit.py` (the A4.c demonstration test, which replays a *real* sealed coinbase that already carries `prev_hash` — Task 9 handles that area). `.coinbase(` classmethod sites: `block.py` (Task 7), `conftest.py` (Step 1), `test_transaction.py` ×2 (Step 2). Confirm each either threads `prev_hash` or replays a coinbase that already has one. No bare coinbase with `prev_hash=None` reaches `validate_coinbase`.

- [ ] **Step 5: Verify these tests pass**

```bash
uv run pytest tests/test_transaction.py tests/test_chain.py 2>&1 | tail -5
uv run ruff check tests/conftest.py tests/test_transaction.py tests/test_chain.py
uv run mypy 2>&1 | tail -2
```

`test_transaction.py` and `test_chain.py` green; ruff/mypy clean.

- [ ] **Step 6: Add an explicit regression test that regular-txn `data_csv` (hence txid) is unchanged**

The conditional-append in Task 4 Step 2 is load-bearing: regular txns must produce a `data_csv` byte-identical to the pre-binding 6-field format (so their txids do not change). The existing `test_transaction.py` tests only round-trip / equality-check transactions — they do NOT pin a fixed txid, so an accidental unconditional append would go uncaught. Add a direct structural assertion. Append to `tests/test_transaction.py` (it already imports `Transaction`; the `wallet` fixture is available):

```python
def test_regular_txn_data_csv_excludes_prev_hash(wallet):
    """A4.c v2 guard: a regular txn's data_csv (and therefore its txid)
    is unchanged by the coinbase prev_hash binding.

    The prev_hash field is conditionally appended to data_csv only when
    set; regular txns leave it None, so their data_csv must be the exact
    6-field join (timestamp, address, public_key, inflows, outflows,
    version) with no trailing prev_hash field.
    """
    t = Transaction()
    t.add_inflow(Inflow(outflow_txid='a' * 64, outflow_idx=0))
    t.add_outflow(Outflow(amount=5, address=wallet.address))
    t.set_wallet(wallet)
    t.seal()
    assert t.prev_hash is None
    expected = ','.join([
        str(t.timestamp),
        str(t.address),
        str(t.public_key),
        ','.join(i.data_csv for i in t.inflows),
        ','.join(o.data_csv for o in t.outflows),
        str(t.version),
    ])
    assert t.data_csv == expected
    # to_dict (asdict_sans_none) must not surface a prev_hash key.
    assert 'prev_hash' not in t.to_dict()
```

Confirm the imports `Inflow`, `Outflow` are present in `test_transaction.py` (they are used by other tests there; if not, add `from cancelchain.payload import Inflow, Outflow`). Run:

```bash
uv run pytest tests/test_transaction.py::test_regular_txn_data_csv_excludes_prev_hash -v 2>&1 | tail -5
uv run ruff check tests/test_transaction.py
```

Passes; ruff clean. This is the canary for T4's concern — if `data_csv` appended `prev_hash` unconditionally, `t.data_csv == expected` would fail (the actual would have a trailing empty field).

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
COLUMNS=200 uv run pytest 2>&1 | tail -3
```

At this point Task 7b has already added `test_regular_txn_data_csv_excludes_prev_hash` (a passing test), so the passed count is baseline 237 **+1 = 238**. The A4.c demonstration test is still decorated but the fix makes it XPASS(strict) — counted as `1 failed`. So a plain run shows `238 passed, 4 xfailed, 1 failed (A4.c XPASS), 1 skipped`. The XPASS is expected (the fix works; Task 9 removes the decorator). Crucially: NO OTHER failures — the legitimate-consecutive-block tests (`test_chain`, `test_models`, `test_miller`, `test_command`) all pass because coinbases are now unique per block.

To confirm cleanly, deselect the not-yet-un-xfailed A4.c test:

```bash
COLUMNS=200 uv run pytest --deselect 'tests/test_verification_audit.py::test_a4_c_ii_coinbase_replay_inflates_balance' 2>&1 | tail -3
```

Expected: `238 passed, 4 xfailed, 1 skipped`, no failures.

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

- [ ] **Step 4: Add the v2 binding test (there is no v1 cross-fork test to invert)**

The v1 cross-fork test (`test_a4_c_cross_fork_coinbase_replay_accepted`) was never added to main (it lived only in the v1 plan, which shipped docs-only and v1 was BLOCKED before impl). So this step is a pure ADDITION, not a mutation of an existing test — v2 *inverts v1's premise* (coinbases are block-bound, so cross-fork replay onto a different-parent block is rejected), but there is no test artifact to invert. **Confirm no such test exists:**

```bash
grep -n 'cross_fork' tests/test_verification_audit.py
```

Expected: no matches (the v1 cross-fork test was never implemented). If it somehow exists, delete it — v2 rejects coinbase replay onto a *different-parent* block (binding mismatch), so a test asserting cross-fork replay is "accepted" would target the wrong invariant. (A same-parent sibling does still pass the binding check, harmlessly — but no test should assert acceptance of different-parent replay.)

Then add a v2 binding test that asserts the block-bound semantics directly. Append to `tests/test_verification_audit.py`. The test body uses `m.longest_chain` (a `Chain` instance) via its methods but does NOT reference the `Chain` class name, so do NOT import `Chain` (it would be an unused import → ruff `F401`). The only new import needed is `MismatchedCoinbaseError` from `cancelchain.exceptions` (add it to the existing `from cancelchain.exceptions import (...)` block); `Block`, `Miller`, `Transaction`, `now`, `now_iso`, and `datetime` are already imported:

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
        # b_mismatch is a NEW block linked off the current tip (b1), so
        # chain.link_block sets b_mismatch.prev_hash = b1.block_hash. We
        # place cb0 (bound to b0.prev_hash, i.e. GENESIS_HASH) as its
        # coinbase. cb0.prev_hash (GENESIS_HASH) != b_mismatch.prev_hash
        # (b1.block_hash) → binding mismatch → MismatchedCoinbaseError.
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

The module docstring claims every test is `@pytest.mark.xfail(strict=True)` — stale (A2.e already remediated; A4.c now too). Replace the module docstring (lines 1-21) with this exact text, which describes the open-finding (xfail) vs remediated (plain pass) vs non-regression states and names `test_a4_c_coinbase_block_binding` (the v2 binding test) as the example invariant test:

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
COLUMNS=200 uv run pytest 2>&1 | tail -3
```

All exit 0. Pytest shows `240 passed, 4 xfailed, 1 skipped` (was 237+5; A4.c un-xfailed +1, the audit binding test +1, the regular-txn data_csv regression test +1).

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
git add src/cancelchain/exceptions.py src/cancelchain/transaction.py src/cancelchain/models.py src/cancelchain/block.py src/cancelchain/chain.py src/cancelchain/migrations/versions/ tests/conftest.py tests/test_transaction.py tests/test_chain.py tests/test_verification_audit.py docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md docs/superpowers/ROADMAP.md docs/superpowers/specs/2026-05-30-a4c-coinbase-uniqueness-design.md docs/superpowers/plans/2026-05-30-a4c-coinbase-uniqueness.md
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
- Coinbases are now block-bound: replay onto a different-parent block
  is rejected by the binding mismatch (a same-parent sibling still
  passes, harmlessly) — inverting v1's blanket "legitimate" premise.

Test went from xfail to a real pass; a new test_a4_c_coinbase_block_
binding asserts consecutive blocks have distinct coinbase txids and
that a mismatched binding is rejected. Full suite 240 passed, 4 xfailed,
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
- Coinbases are now block-bound; replay onto a different-parent block is rejected by the binding mismatch (a same-parent sibling still passes, harmlessly) — inverting v1's blanket "cross-fork replay is legitimate" premise.

## Documentation

- Audit doc: A4.c closed (Findings table, Attack c.ii trace, Executive summary, Recommendations §2) describing the v2 binding fix.
- ROADMAP: A4.c moved to closed.
- v1 spec/plan carry supersession banners pointing to v2.

## Test plan

- [x] All 5 CI gates clean (ruff check + ruff format + pytest + mypy + db check).
- [x] \`COLUMNS=200 uv run pytest\` → \`240 passed, 4 xfailed, 1 skipped\` (COLUMNS guard avoids the pre-existing terminal-width-dependent test_create_wallet failure).
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
COLUMNS=200 uv run pytest 2>&1 | tail -3                              # 240 passed, 4 xfailed, 1 skipped
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

The conditional-append in `data_csv` (Task 4 Step 2) is load-bearing: it must append `prev_hash` ONLY when non-None. The existing `test_transaction.py` tests do NOT pin a fixed regular-txn txid (they only round-trip / equality-check), so they would NOT catch an unconditional append that silently churns every regular txid. Task 7b Step 5 adds an explicit `test_regular_txn_data_csv_excludes_prev_hash` that asserts a regular txn's `data_csv` equals the exact 6-field join (no trailing `prev_hash`) — the direct canary for this risk. Run it after Task 4.
