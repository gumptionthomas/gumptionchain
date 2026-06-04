# Net-stake coinbase metrics — design

**Date:** 2026-06-04
**Status:** Approved design, pre-implementation
**Issue:** #145
**Type:** Consensus change (coinbase rule), greenfield — no migration

## Summary

Close the stake-recycle inflation hole (#145) by minting the coinbase
"sentiment" metrics on **net new stake** instead of an outflow's mere existence.
A stake outflow funded by recycling a prior same-kind stake (a restake, or a
partial-rescind change-back) mints nothing; only stake funded from wallet/general
funds mints. This makes a stake's **lifetime minting equal its face value** —
half when first staked, half when rescinded — regardless of how it is recycled in
between.

## Problem

The four coinbase metrics are minted from an outflow's existence:
`schadenfreude`/`mudita` from any opposition/support outflow, `grace`/`regret`
from any rescind outflow (each `amount // 2`). They flow
`Outflow` property → `Transaction` sum → `Block` sum → coinbase, and the coinbase
mints them to the miller; `validate_coinbase` enforces the amounts.

Because minting keys off outflow *existence*, recycling a stake re-mints it:

- **Restake loop (severe, no rescind):** consume your own unspent
  `support='foo'` outflow (100) and emit a new identical `support='foo'` outflow
  (100). It balances in `validate_block_txn` (the support pool is credited by the
  inflow and drained by the outflow), and the new outflow mints `mudita = 50`
  again — repeatable every block, unbounded inflation from a fixed stake. The
  opposition variant is identical and predates the rename.
- **Partial-rescind change-back:** a partial rescind emits a `rescind` outflow
  (mints the rescind-side metric for the rescinded part) **plus** a same-kind
  change-back outflow for the remainder — which re-mints the mint-side metric for
  grains already minted at stake time.

These are coinbase-only metrics: no template, leaderboard, or API reads them
(only `create_coinbase` and `validate_coinbase`).

## The minting rule

For each `(kind, subject)` within a transaction, define three sums:
- `in_K[S]`  — value of consumed same-kind inflows on `S`
- `out_K[S]` — value of stake outflows of kind `K` on `S`
- `rescind_K[S]` — value of rescind outflows of kind `K` on `S`

Then:
- **net new stake:** `new_K[S] = out_K[S] − in_K[S] + rescind_K[S]` (≥ 0 for any
  valid transaction)
- **mint-side:** `schadenfreude += new_opp[S] // 2`, `mudita += new_sup[S] // 2`
- **rescind-side (unchanged in spirit):** `grace += rescind_opp[S] // 2`,
  `regret += rescind_sup[S] // 2`

Worked cases:

| Action | out | in | rescind | new (mints `//2`) | rescind-side |
|---|---|---|---|---|---|
| New stake (wallet-funded) | 100 | 0 | 0 | **100** → 50 | 0 |
| Restake (the exploit) | 100 | 100 | 0 | **0** | 0 |
| Partial rescind 40 of 100 | 60 (change) | 100 | 40 | **0** | 40 → 20 |
| Full rescind 100 | 0 | 100 | 100 | **0** | 100 → 50 |

Lifetime conservation: stake 100 → mint 50; later rescind (in any number of
partial steps) → rescind-side totals 50; restakes in between mint 0. Total minted
== 100 == face value.

### Why metric-only (no validation restriction)

We considered also forbidding "consume a stake outflow except into a rescind."
Rejected: it blocks only the *naked* restake. A 1-grain rescind that produces a
99-grain change-back is a legal rescind that recycles 99% of the stake, so the
restriction adds consensus-validation surface without closing the hole. The
net-stake metric closes it in every form (naked or disguised), so it is necessary
and sufficient on its own. Restake remains a permitted but pointless no-op that
mints nothing.

## Architecture

The mint-side now depends on what each transaction **consumes**, so it can no
longer be computed from a block in isolation — it needs chain context to resolve
each inflow's `(kind, subject)`. Coinbase metric computation therefore moves from
**block-local** to **chain-aware**, with a single source of truth shared by the
miller (build) and every node (verify).

- **`validate_block_txn` returns the per-transaction metrics.** It keeps its
  existing, tested balance check **unchanged**, and additionally tallies
  `in_K[S]` / `out_K[S]` / `rescind_K[S]` in the loops it already runs, then
  returns a `CoinbaseMetrics(schadenfreude, grace, mudita, regret)` computed by
  the rule above. A small `CoinbaseMetrics` carrier supports `+` for
  accumulation.
- **The validation loop accumulates and checks:**
  `metrics = sum(validate_block_txn(block, t) for t in block.regular_txns)`, then
  the **existing** `validate_block_coinbase` (which already checks the reward and
  the coinbase→block binding) is extended to take `metrics` and compare the
  coinbase's metric outflows to it (replacing the block-local `Block.validate_coinbase`).
- **The miller** already validates each candidate transaction in `create_block`;
  it accumulates the same `CoinbaseMetrics` during that pass and supplies them to
  sealing.

### Performance

Performance-neutral by construction. The only non-trivial cost is inflow
resolution (`validate_txn_inflow` → `get_transaction`), which **both block
validation and block creation already perform** on every transaction. The metric
tally is computed from data those passes already hold — a handful of integer ops.
Read/display paths are unaffected because nothing outside coinbase build/validate
reads these metrics.

**Implementation requirement (non-negotiable):** the metric tally must be *fused*
into the existing per-transaction validation pass (via `validate_block_txn`'s
return value). A separate `coinbase_metrics(block)` pass that re-resolves every
inflow after validation already did would roughly **double** the per-inflow chain
lookups per block — that is forbidden.

## File-by-file changes

| File | Change |
|---|---|
| `chain.py` | `validate_block_txn` keeps its balance check, adds `(kind,subject)` sum tallies, returns `CoinbaseMetrics`; new `CoinbaseMetrics` carrier; validation loop accumulates; **extend** existing `validate_block_coinbase` to take `metrics` and check the coinbase metric outflows; `Miller.create_block` accumulates metrics for sealing |
| `block.py` | `seal` / `create_coinbase` take the four metric values as parameters (from the chain-aware caller); remove `Block.schadenfreude/grace/mudita/regret` and `Block.validate_coinbase` |
| `transaction.py` | remove `Transaction.schadenfreude/grace/mudita/regret`; `Transaction.coinbase(...)` unchanged (already takes the four ints) |
| `payload.py` | remove `Outflow.schadenfreude/grace/mudita/regret` (all four) |
| tests | conservation + adversarial coinbase tests; update existing coinbase-amount expectations for recycled cases |

Mostly *deletions* of scattered properties plus one focused accounting addition
in `validate_block_txn` and a relocated coinbase check. No schema/migration
change — `data_csv` and txids are untouched, so normal blocks (new stakes + full
rescinds) stay bit-identical; only recycled-stake blocks get different coinbase
amounts.

The balance check in `validate_block_txn` stays as-is (tested consensus logic);
the metric tally is purely additive. Refactoring the balance check itself to the
aggregate `(in/out/rescind)` form is explicitly **out of scope** — additive
tally only, to minimize consensus risk.

## Consensus / hard fork

A coinbase consensus-rule change, but it only alters blocks that actually recycle
stake. Greenfield/pre-launch — no chain to preserve, no migration. Normal blocks
are unchanged (bit-identical coinbase).

## Testing

Conservation tests over real mined blocks (issue #145 acceptance):
1. **Restake mints nothing:** stake support (block mints `mudita = amt//2`);
   restake it; assert the restake block's `mudita == 0`; loop N blocks → total
   minted stays `amt//2`.
2. **Change-back mints nothing on the remainder:** stake 100 (`mudita 50`);
   partial-rescind 40 → assert `regret == 20` **and** `mudita == 0` for that
   block.
3. **Lifecycle == face value:** stake 100 → 50; partial rescind 40 then 60 →
   20 + 30; assert total minted across all blocks `== 100`.
4. **New-stake regression:** a plain new stake still mints `amt//2` (unchanged).
5. **Opposition symmetric:** repeat 1–4 for opposition / `schadenfreude` /
   `grace`.
6. **Consensus safety (adversarial):** a block whose coinbase claims the old
   *gross* mint for a recycled-stake transaction is **rejected** by
   `validate_block_coinbase` (the verifier recomputes net; amounts mismatch →
   `InvalidCoinbaseError`).
7. **Miller builds correct net coinbase:** a block the miller seals over a
   recycled-stake transaction carries the net (not gross) coinbase amounts.

## Decisions log

- Mint-side becomes **net new stake** per `(kind, subject)`:
  `new = out − in + rescind`, minted `// 2`. Rescind-side unchanged
  (`rescind // 2`).
- **Metric-only** fix; no "consume-only-to-rescind" validation rule (the disguised
  restake bypasses it; net metric is necessary and sufficient).
- Coinbase metric computation moves **block-local → chain-aware**, single source
  of truth for build + verify, **fused** into the existing per-transaction
  validation pass (no second resolution pass).
- Per-outflow/transaction/block metric properties removed (coinbase-only;
  misleading once minting depends on funding source).
- Existing `validate_block_txn` balance check left **unchanged**; metric tally is
  additive. Greenfield → no migration; normal blocks bit-identical.

## Out of scope

- Refactoring the `validate_block_txn` balance check to aggregate form.
- The pre-existing N+1 in `unrescinded_outflows` (rescind-building path; separate
  perf item).
- Any new validation restriction on consuming stake outflows.
