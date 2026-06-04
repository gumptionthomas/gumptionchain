# Net-stake coinbase metrics — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mint the coinbase sentiment metrics on *net new stake* instead of outflow existence, closing the stake-recycle inflation hole (#145).

**Architecture:** `validate_block_txn` becomes the single source of truth for per-transaction coinbase metrics: it keeps its balance check unchanged, additionally tallies `(kind,subject)` in/out/rescind sums, and returns a `CoinbaseMetrics`. Both the validation path (`validate_block`) and the miller (`create_block`) accumulate those returns and feed them to coinbase build (`seal_block`→`block.seal`) and verify (`validate_block_coinbase`). The per-outflow/txn/block metric properties are removed.

**Tech Stack:** Python 3.12, Flask, SQLAlchemy 2.0, Pydantic v2, pytest, uv, ruff, mypy.

**Spec:** `docs/superpowers/specs/2026-06-04-net-stake-coinbase-design.md`

---

## The rule (reference for all tasks)

Per `(kind, subject)` in a transaction: `in_K[S]` = consumed same-kind inflows, `out_K[S]` = stake outflows, `rescind_K[S]` = rescind outflows. Then:
- `new_K[S] = max(0, out_K[S] − in_K[S] + rescind_K[S])`
- mint-side: `schadenfreude = Σ_S new_opp[S] // 2`, `mudita = Σ_S new_sup[S] // 2`
- rescind-side: `grace = Σ_S rescind_opp[S] // 2`, `regret = Σ_S rescind_sup[S] // 2`

Examples: new stake `(out=100,in=0,resc=0)→new 100→mint 50`; restake `(100,100,0)→0`; partial-rescind-40-of-100 change-back `(out=60,in=100,resc=40)→new 0`, rescind-side `40//2=20`.

## Consensus / greenfield

This changes coinbase amounts only for blocks that recycle stake; normal blocks (new stakes + full rescinds) are bit-identical. Greenfield → no migration, no schema change. `data_csv`/txids untouched.

## File map

| File | Change |
|---|---|
| `transaction.py` | add `CoinbaseMetrics` dataclass; remove `Transaction.schadenfreude/grace/mudita/regret` |
| `chain.py` | `validate_block_txn` tallies + returns `CoinbaseMetrics`; `validate_block` accumulates; `validate_block_coinbase(block, metrics)`; `seal_block(block, wallet, metrics)` |
| `miller.py` | `create_block` accumulates metrics, passes to `seal_block` |
| `block.py` | `seal`/`add_coinbase`/`create_coinbase` take `CoinbaseMetrics`; remove `Block.schadenfreude/grace/mudita/regret` and `Block.validate_coinbase` |
| `payload.py` | remove `Outflow.schadenfreude/grace/mudita/regret` |
| tests | conservation + adversarial coinbase tests; update existing coinbase-amount expectations |

Task order keeps each commit green: Task 1 adds the computation (callers ignore the return) → Task 2 wires it through build+verify (behavior flips to net) → Task 3 deletes the now-dead properties → Task 4 final verification.

---

## Task 1: `CoinbaseMetrics` + `validate_block_txn` returns per-txn net metrics

Add the carrier and the net tally. Callers still ignore the return, so behavior is unchanged and the suite stays green; the net math is unit-tested directly via `validate_block_txn`.

**Files:** `src/gumptionchain/transaction.py`, `src/gumptionchain/chain.py`, `tests/test_chain.py`

- [ ] **Step 1: Add `CoinbaseMetrics` to `transaction.py`**

Near the top of `src/gumptionchain/transaction.py` (after imports, before/after the `Transaction` class — module level), add:
```python
@dataclass(frozen=True)
class CoinbaseMetrics:
    schadenfreude: int = 0
    grace: int = 0
    mudita: int = 0
    regret: int = 0

    def __add__(self, other: 'CoinbaseMetrics') -> 'CoinbaseMetrics':
        return CoinbaseMetrics(
            self.schadenfreude + other.schadenfreude,
            self.grace + other.grace,
            self.mudita + other.mudita,
            self.regret + other.regret,
        )

    def nonzero_amounts(self) -> list[int]:
        return [
            v
            for v in (self.schadenfreude, self.grace, self.mudita, self.regret)
            if v
        ]
```
Ensure `from dataclasses import dataclass` is imported (it almost certainly is for `@dataclass` elsewhere; check the top of the file).

- [ ] **Step 2: Write the failing unit tests for the returned metrics**

In `tests/test_chain.py`, first READ the existing helpers used by `test_rescind_support_drops_support_balance` and `test_support_rescind_mints_regret` (how they build a chain, fund a wallet, stake/mine, and access a `Chain`/`Block`). Mirror that harness. Add tests that call `chain.validate_block_txn(block, txn)` and assert the returned `CoinbaseMetrics` (import it: `from gumptionchain.transaction import CoinbaseMetrics`):

```python
def test_validate_block_txn_returns_new_stake_metric(...):
    # build a NEW support stake txn of `amt` (funded from wallet/general outflows)
    # call chain.validate_block_txn(block, stake_txn) and assert:
    m = chain.validate_block_txn(block, stake_txn)
    assert m.mudita == amt // 2
    assert m.schadenfreude == 0 and m.grace == 0 and m.regret == 0


def test_validate_block_txn_restake_mints_nothing(...):
    # stake support `amt` and mine it; build a restake txn that consumes that
    # support outflow and emits a new support outflow of `amt` (same subject)
    m = chain.validate_block_txn(block, restake_txn)
    assert m.mudita == 0  # net new stake is zero


def test_validate_block_txn_partial_rescind_metrics(...):
    # stake support `amt` (even) and mine; build a partial rescind of amt//2
    # via chain.create_rescind(wallet, amt // 2, subject, 'support')
    m = chain.validate_block_txn(block, rescind_txn)
    assert m.regret == (amt // 2) // 2   # rescind-side for the rescinded part
    assert m.mudita == 0                 # change-back remainder mints nothing
```
Add the symmetric opposition versions (`schadenfreude`/`grace`) following the same shape with `create_opposition`/`create_rescind(..., 'opposition')`. Adapt names/fixtures to the file.

- [ ] **Step 3: Run, expect FAIL**

Run: `uv run pytest tests/test_chain.py -k "validate_block_txn_returns or restake_mints or partial_rescind_metrics" -q`
Expected: FAIL (`validate_block_txn` returns `None`; `None` has no `.mudita`).

- [ ] **Step 4: Add the tally + return to `validate_block_txn` (chain.py)**

Do NOT alter the existing balance logic (the `opposition_amounts`/`support_amounts`/`other_amounts` mutation and the three `ImbalancedTransactionError` checks). ADD parallel tally dicts and a final computation.

(a) Change the signature return type from `-> None:` to `-> CoinbaseMetrics:` and import it: in the existing `from gumptionchain.transaction import Transaction` line, make it `from gumptionchain.transaction import CoinbaseMetrics, Transaction`.

(b) At the top of the method, next to the existing `opposition_amounts`/`support_amounts`/`other_amounts` initialisation, add six tally dicts:
```python
        in_opp: dict[str, int] = {}
        in_sup: dict[str, int] = {}
        out_opp: dict[str, int] = {}
        out_sup: dict[str, int] = {}
        resc_opp: dict[str, int] = {}
        resc_sup: dict[str, int] = {}
```

(c) In the **inflow loop**, inside the existing `if opposition:` / `elif support:` branches, add the tally alongside the existing pool credit:
- in the `if opposition:` branch: `in_opp[opposition] = in_opp.get(opposition, 0) + amount`
- in the `elif support:` branch: `in_sup[support] = in_sup.get(support, 0) + amount`

(d) In the **outflow loop**, add the tally alongside the existing balance handling:
- in the `if o.rescind:` branch, under `if o.rescind_kind == 'support':` add `resc_sup[o.rescind] = resc_sup.get(o.rescind, 0) + (o.amount or 0)`; under `elif o.rescind_kind == 'opposition':` add `resc_opp[o.rescind] = resc_opp.get(o.rescind, 0) + (o.amount or 0)`
- in the `elif o.opposition:` branch (first line of it): `out_opp[o.opposition] = out_opp.get(o.opposition, 0) + (o.amount or 0)`
- in the `elif o.support:` branch (first line of it): `out_sup[o.support] = out_sup.get(o.support, 0) + (o.amount or 0)`

(e) After the existing three balance `ImbalancedTransactionError` checks (at the very end of the method), compute and return the metrics:
```python
        schadenfreude = sum(
            max(0, out_opp.get(s, 0) - in_opp.get(s, 0) + resc_opp.get(s, 0))
            // 2
            for s in out_opp.keys() | in_opp.keys() | resc_opp.keys()
        )
        mudita = sum(
            max(0, out_sup.get(s, 0) - in_sup.get(s, 0) + resc_sup.get(s, 0))
            // 2
            for s in out_sup.keys() | in_sup.keys() | resc_sup.keys()
        )
        grace = sum(v // 2 for v in resc_opp.values())
        regret = sum(v // 2 for v in resc_sup.values())
        return CoinbaseMetrics(schadenfreude, grace, mudita, regret)
```

- [ ] **Step 5: Run, expect PASS**

Run: `uv run pytest tests/test_chain.py -k "validate_block_txn_returns or restake_mints or partial_rescind_metrics" -q`
Expected: PASS.

- [ ] **Step 6: Full suite + lint + types**

Run: `uv run pytest -q` (the existing suite must stay green — `validate_block_txn`'s return is currently ignored by `validate_block`/`miller`, and the old coinbase path still uses `block.schadenfreude`). Then `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy`.
Expected: all green (existing count + the new tests).

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "feat(coinbase): validate_block_txn returns net-stake CoinbaseMetrics"
```

---

## Task 2: Wire net metrics through coinbase build + verify

Flip the coinbase to use the accumulated net metrics on both the miller (build) and validator (verify) sides. Keep the old `Block`/`Transaction`/`Outflow` properties in place for now (they become unused; Task 3 deletes them) so each change is isolated.

**Files:** `src/gumptionchain/chain.py`, `src/gumptionchain/miller.py`, `src/gumptionchain/block.py`, `tests/test_chain.py`, `tests/test_miller.py`, `tests/test_block.py`

- [ ] **Step 1: Conservation + adversarial tests (failing)**

In `tests/test_chain.py` (mirror the existing mine-a-block harness), add end-to-end tests over real mined blocks:
```python
def test_restake_block_mints_no_mudita(...):
    # stake support `amt` from wallet, mine -> that block's coinbase mints amt//2
    # restake (consume + re-emit same support), mine -> assert the restake block's
    # coinbase has NO mudita outflow (only the reward outflow), i.e. the miller
    # minted 0 mudita for the restake.

def test_partial_rescind_block_conserves(...):
    # stake 100 support, mine; partial-rescind 40, mine -> assert the rescind
    # block's coinbase mints regret 20 and mudita 0.

def test_stake_lifecycle_mints_face_value(...):
    # stake 100 support -> 50; rescind 40 then 60 -> 20 + 30; assert total minted
    # across all mined blocks == 100.

def test_new_stake_still_mints_half(...):
    # a plain new stake still mints amt//2 (regression).

def test_forged_gross_coinbase_rejected(...):
    # build a restake block but hand-forge its coinbase to claim the OLD gross
    # mudita (amt//2); assert chain.validate_block(block) (or add_block) raises
    # InvalidCoinbaseError.
```
Add the opposition-symmetric versions. Read the existing coinbase/miller tests (`test_miller.py`, `test_block.py`) to learn how blocks are sealed and validated, and how the coinbase outflows are inspected. To hand-forge in the adversarial test, mirror however the existing tests construct/seal a block, then replace/append the coinbase metric outflow.

- [ ] **Step 2: Run, expect FAIL**

Run: `uv run pytest tests/test_chain.py -k "restake_block or partial_rescind_block or lifecycle or forged_gross" -q`
Expected: FAIL (today the miller mints gross via `block.mudita`, so the restake block still mints `amt//2`).

- [ ] **Step 3: `block.py` — take metrics through the seal path**

Change `seal` / `add_coinbase` / `create_coinbase` to accept a `CoinbaseMetrics` and use it (import: `from gumptionchain.transaction import CoinbaseMetrics, Transaction` — `Transaction` is already imported there). Keep the `Block.schadenfreude` etc. properties for now (Task 3 removes them).
```python
    def create_coinbase(
        self, wallet: Wallet, reward: int, metrics: CoinbaseMetrics
    ) -> Transaction:
        if self.prev_hash is None:
            raise UnlinkedBlockError()
        return Transaction.coinbase(
            wallet,
            reward,
            metrics.schadenfreude,
            metrics.grace,
            metrics.mudita,
            metrics.regret,
            prev_hash=self.prev_hash,
        )

    def add_coinbase(
        self, wallet: Wallet, reward: int, metrics: CoinbaseMetrics
    ) -> None:
        self.add_txn(
            self.create_coinbase(wallet, reward, metrics), is_coinbase=True
        )

    def seal(
        self, wallet: Wallet, reward: int, metrics: CoinbaseMetrics
    ) -> None:
        if self.is_sealed:
            raise SealedBlockError()
        if (self.prev_hash is None) or (self.idx is None):
            raise UnlinkedBlockError()
        self.txns.sort()
        self.add_coinbase(wallet, reward, metrics)
        self.merkle_root = self.get_merkle_root()
        self.timestamp = now_iso()
```

- [ ] **Step 4: `chain.py` — `seal_block` computes/passes metrics; `validate_block` accumulates; `validate_block_coinbase` checks**

`seal_block` gains a `metrics` param and threads it:
```python
    def seal_block(
        self, block: Block, wallet: Wallet, metrics: CoinbaseMetrics
    ) -> None:
        block.seal(wallet, self.block_reward(block), metrics)
```
`validate_block` (the `for txn in block.regular_txns:` loop, ~lines 205-207) accumulates and passes:
```python
        metrics = CoinbaseMetrics()
        for txn in block.regular_txns:
            metrics += self.validate_block_txn(block, txn)
        self.validate_block_coinbase(block, metrics)
```
`validate_block_coinbase` gains `metrics` and performs the metric comparison itself (moving it off `Block.validate_coinbase`), keeping the existing reward + binding checks:
```python
    def validate_block_coinbase(
        self, block: Block, metrics: CoinbaseMetrics
    ) -> None:
        cb = block.coinbase
        if cb is None:
            raise MissingCoinbaseError()
        cb.validate_coinbase()
        if metrics.nonzero_amounts() != [o.amount for o in cb.outflows[1:]]:
            raise InvalidCoinbaseError()
        reward = self.block_reward(block)
        # A4.c v2: coinbase is bound to the block it rewards via prev_hash.
        if cb.prev_hash != block.prev_hash:
            raise MismatchedCoinbaseError()
        outflow = cb.get_outflow(0)
        if outflow is not None and outflow.amount != reward:
            raise InvalidCoinbaseErrorRewardError()
```
Add the imports/exceptions used: `MissingCoinbaseError`, `InvalidCoinbaseError` (check `from gumptionchain.exceptions import (...)` — `MismatchedCoinbaseError`, `InvalidCoinbaseErrorRewardError` are already imported; add `MissingCoinbaseError` and `InvalidCoinbaseError`). `CoinbaseMetrics` is already imported from Task 1.

- [ ] **Step 5: `miller.py` — accumulate metrics in `create_block`, pass to `seal_block`**

In `create_block` (the inclusion loop ~lines 84-99), accumulate the returned metrics for txns that are successfully added, and pass them to `seal_block`:
```python
        i = 0
        discard_txns: list[Transaction] = []
        metrics = CoinbaseMetrics()
        self.update_pending_txns()
        for txn in self.pending_chain_txns(chain):
            try:
                m = chain.validate_block_txn(block, txn, txn_in_block=False)
                block.add_txn(txn)
                metrics += m
                i += 1
                if i >= MAX_TRANSACTIONS - 1:
                    break
            except Exception as e:
                discard_txns.append(txn)
                txn_failed_signal.send(self, txn=txn, e=e)
        for txn in discard_txns:
            self.pending_txns.discard(txn)
        chain.seal_block(block, self.milling_wallet, metrics)  # type: ignore[arg-type]
        return block
```
Import `CoinbaseMetrics` in `miller.py`: `from gumptionchain.transaction import CoinbaseMetrics` (add to existing transaction import if present).

- [ ] **Step 6: Update existing coinbase callers/tests**

Run: `grep -rn "\.seal(\|seal_block(\|create_coinbase(\|add_coinbase(" src tests`
Update every call to pass a `CoinbaseMetrics`. For tests that seal a block directly via `block.seal(wallet, reward)`, pass the metrics the chain would compute — simplest is to compute them the same way (`metrics = sum((chain.validate_block_txn(block, t) for t in block.regular_txns), CoinbaseMetrics())`) or, for blocks with no metric-bearing txns, `CoinbaseMetrics()`. Prefer routing through `chain.seal_block`/`chain.create_block` where the harness allows. Also update any test asserting concrete coinbase metric amounts for recycled-stake cases (new-stake/full-rescind amounts are unchanged).

- [ ] **Step 7: Run new + full suite + gates**

Run: `uv run pytest tests/test_chain.py -k "restake_block or partial_rescind_block or lifecycle or forged_gross" -q` → PASS.
Run: `uv run pytest -q` → all green.
Run: `uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy` → clean.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat(coinbase): mint sentiment metrics on net new stake (closes restake/change-back inflation)"
```

---

## Task 3: Remove the dead block-local metric properties

After Task 2 the coinbase is built and verified from accumulated `CoinbaseMetrics`; the per-`Outflow`/`Transaction`/`Block` metric properties and `Block.validate_coinbase` are now unused. Delete them.

**Files:** `src/gumptionchain/payload.py`, `src/gumptionchain/transaction.py`, `src/gumptionchain/block.py`, `tests/*`

- [ ] **Step 1: Confirm they are unused, then delete**

Run: `grep -rn "\.schadenfreude\|\.grace\b\|\.mudita\|\.regret\|def schadenfreude\|def grace\|def mudita\|def regret\|validate_coinbase" src`
The only `*.schadenfreude/grace/mudita/regret` references in `src` should now be inside `CoinbaseMetrics` (its fields) and the metric *computation* in `validate_block_txn`/`validate_block_coinbase`. Delete:
- `Outflow.schadenfreude/grace/mudita/regret` (payload.py)
- `Transaction.schadenfreude/grace/mudita/regret` (transaction.py)
- `Block.schadenfreude/grace/mudita/regret` (block.py)
- `Block.validate_coinbase` (block.py) — its logic now lives in `Chain.validate_block_coinbase`

Keep `Transaction.validate_coinbase` (a different, transaction-structural check) — only `Block.validate_coinbase` is removed.

- [ ] **Step 2: Update tests that referenced the removed properties**

Run: `grep -rn "\.schadenfreude\|\.grace\b\|\.mudita\|\.regret\|\.validate_coinbase()" tests`
Any test reading `outflow.mudita` / `txn.schadenfreude` / `block.grace` / `block.validate_coinbase()` directly must be rewritten to assert via the coinbase (the block's coinbase outflow amounts) or via `chain.validate_block_txn(...)`'s returned `CoinbaseMetrics` (the Task 1 pattern). Delete tests that only exercised the removed per-outflow properties if they're now redundant with the conservation tests; otherwise port them.

- [ ] **Step 3: Run full suite + gates**

Run: `uv run pytest -q && uv run ruff check src tests && uv run ruff format --check src tests && uv run mypy`
Expected: all green.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "refactor(coinbase): remove dead per-outflow/block sentiment metric properties"
```

---

## Task 4: Final verification

**Files:** verification only.

- [ ] **Step 1: Grep sweep**

Run: `grep -rn "def schadenfreude\|def grace\|def mudita\|def regret\|block.validate_coinbase\|block.schadenfreude\|block.mudita" src`
Expected: NO matches (the only metric references should be `CoinbaseMetrics` fields and the `validate_block_txn`/`validate_block_coinbase` computation).

- [ ] **Step 2: Full gate**

Run:
```bash
uv run pytest -q
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy
```
Then schema parity (no schema change expected, but confirm):
```bash
set -a; source tests/.test.env; set +a
export FLASK_SQLALCHEMY_DATABASE_URI="sqlite:///$(pwd)/.dbcheck_tmp.db"; rm -f .dbcheck_tmp.db
uv run gumptionchain db upgrade && uv run gumptionchain db check; rm -f .dbcheck_tmp.db
```
Expected: all clean; db check `No new upgrade operations detected.`

- [ ] **Step 3: Commit (if Step 1/2 required any cleanup)**

```bash
git add -A
git commit -m "test: net-stake coinbase final verification"
```

---

## Definition of done

- Coinbase mints `schadenfreude`/`mudita` on net new stake (`out − in + rescind`, floored, `//2`); `grace`/`regret` per rescind outflow.
- `validate_block_txn` is the single source of truth (returns per-txn `CoinbaseMetrics`); build (`miller.create_block`→`seal_block`→`block.seal`) and verify (`validate_block`→`validate_block_coinbase`) both consume the accumulated metrics — fused into the existing per-txn pass, no second inflow-resolution walk.
- Restake and partial-rescind change-back mint nothing; a stake's lifetime minting == face value; new-stake/full-rescind blocks are unchanged.
- A forged gross-mint coinbase is rejected (`InvalidCoinbaseError`).
- Per-outflow/txn/block metric properties and `Block.validate_coinbase` removed.
- Full suite + ruff + ruff-format + mypy + db check green. No migration.

## Self-review notes

- `CoinbaseMetrics` lives in `transaction.py` (below `block`/`chain`) to avoid a circular import; `block.py` and `chain.py` and `miller.py` import it from there.
- `validate_block_txn`'s existing balance logic is **untouched**; the tally is additive (six parallel dicts), computed into the return after the balance checks pass (so `new ≥ 0`; `max(0, …)` is belt-and-suspenders).
- The metric is computed per `(kind, subject)` then `//2` (not per-outflow); for the ordinary single-outflow stake this equals today's `amount//2`, so new-stake blocks stay bit-identical.
