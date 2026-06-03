# Opposition / Support / Rescind — transaction model redesign

**Date:** 2026-06-03
**Status:** Approved design, pre-implementation
**Type:** Hard fork (greenfield chain — no on-chain data to preserve)

## Summary

Two related changes, both shedding residual "CancelChain" framing now that the
project is GumptionChain:

1. **Rename the opposition transaction.** The `subject` outflow kind (the
   `cancel`-flavored stake) becomes **`opposition`**, sitting naturally beside
   the existing `support`. The word "subject" survives only where it means *the
   target string being acted on*.
2. **Make support rescindable, symmetrically.** The old `forgive` transaction
   (undo opposition only) becomes a single **`rescind`** transaction that undoes
   *either* opposition or support. This removes the asymmetry where opposition
   could be walked back but support could not.

The chain is greenfield, so we take the clean break: renamed wire fields, a new
metadata column, and a rebalanced coinbase rule, with no migration/replay story.

## Motivation

"Subject" was only ever a euphemism for "cancel" — a subject was the thing you
were cancelling. In a chain not marketed around cancellation, that name carries
no meaning and actively confuses, because "subject" is *also* the generic word
for the target string that both opposition and support point at. The rename
disambiguates: `subject` (the noun) stays as the target string; `opposition`
(the verb/kind) names the stake.

`forgive` existed mainly to soften "cancel" — and its one-sidedness (you could
rescind opposition but never support) was an artifact of that branding, on the
assumption that to "undo" support you'd post a compensating opposition. With the
softening no longer needed, a single neutral `rescind` that works in both
directions is both simpler to explain (one new term) and more honest.

## Terminology decisions

| Old (outflow kind) | New | Holds | Notes |
|---|---|---|---|
| `address` | `address` | a wallet address | unchanged |
| `subject` | **`opposition`** | the subject string being opposed | renamed |
| `support` | `support` | the subject string being supported | unchanged |
| `forgive` | **`rescind`** | the subject string being un-staked | renamed + generalized |
| — | **`rescind_kind`** | `'opposition'` \| `'support'` | new metadata field |

**"Subject" the noun survives** everywhere it denotes the 1–79 char target
string: the `Subject` Pydantic type, `encode_subject` / `decode_subject`,
`SubjectConverter` (URL routing), `MIN/MAX_SUBJECT_LENGTH`, the `human_subject`
template filter, and the per-subject query API (`/subject/<s>/…`). Only the two
outflow *kinds* (`subject` → `opposition`, `forgive` → `rescind`) are renamed.

The single new term users must learn is **`rescind`**. `rescind_kind` is
internal metadata, surfaced at the CLI/API as a required `--kind` / `kind`
parameter.

## Data model

### Outflow fields (`payload.py`, `models.py`)

The "at most one destination" invariant changes from
`{address, subject, forgive, support}` to:

> exactly one of `{address}` **xor** `{opposition, support, rescind}`,
> **and** `rescind_kind` is present if-and-only-if `rescind` is set.

`rescind_kind` is *not* a destination — the destination is the subject string in
the `rescind` field. `rescind_kind` is parallel metadata recording which kind of
stake is being undone. `OutflowModel.validate_destinations()` enforces both
clauses; `rescind_kind ∈ {'opposition', 'support'}` when present, else `None`.

### Canonical serialization (`Outflow.data_csv`)

Current order is `amount, address, subject, forgive, support`. The new canonical
order is:

```
amount, address, opposition, support, rescind, rescind_kind
```

with empty string for any `None` field. This changes the byte content of every
outflow, hence every transaction and block header hash — see **Hard fork**.

### DAO (`models.py` `OutflowDAO`)

Rename columns `subject → opposition`, `forgive → rescind`; keep `support`; add
`rescind_kind` (`String`, nullable). All `String(500)`-style sizing as today;
`rescind_kind` is a short enum-valued string.

## Economic model: burn stays burned (Model A)

Rescinding **never refunds**. The enforced invariant:

> Grains tagged to a subject (an `opposition` or `support` outflow) may only flow
> into a `rescind` outflow, or back into the same-subject / same-kind stake as
> change. They may **never** flow into a spendable `address` outflow.

So staking is always a one-way removal of those grains from circulation;
rescinding relocates them from an `opposition`/`support` outflow into a permanent
`rescind` outflow but never returns them to a wallet. (This mirrors how `forgive`
behaves today — `forgive` outflows are terminal — generalized to support.)

To make support symmetric with opposition, the consume rules change:

- **Support outflows become spendable — but only into a `rescind`.** Today the
  inflow guard (`chain.py` `validate_txn_inflow`) rejects *both* `forgive` and
  `support` outflows as inflows. The new guard rejects only `rescind` outflows
  (which stay terminal). `opposition` and `support` outflows are both
  consumable, exclusively by a `rescind`.
- **`support_balance` becomes unspent-only** (mirrors `opposition_balance`), so
  rescinding support drops the tally automatically as the support outflow is
  consumed.

## Coinbase reward & sentiment metrics

This is the crux, and the reason the change is consensus-affecting.

### What the metrics are

`schadenfreude` / `grace` / `mudita` are **not cosmetic**. Each block's totals
are minted as brand-new grains paid to the miller in the coinbase, on top of the
base block `reward` (`block.create_coinbase` → `Transaction.coinbase`).
`Block.validate_coinbase` enforces that the coinbase outflows *exactly* equal
`[reward, schadenfreude, grace, mudita]` (nonzero components, in order) — so
every node re-derives these and rejects a block whose miller minted the wrong
amount. The weights are monetary policy baked into consensus.

### The rule the weights encode

A staked grain pays the miller its **full face value over its lifetime**, split
half at mint and half at rescind, and staking removes the grain from circulation
permanently. Opposition splits ½ (oppose) + ½ (forgive/rescind); support
currently pays the full value at mint because it had no rescind half. Each fully
cycled stake is a **zero-sum transfer from staker to millers** — supply
conserved.

### Change: symmetric halves

Adding a rescind half to support means support's mint half must drop to ½, with
the other ½ paid as the new `regret` metric on rescind. Final scheme — all four
at half weight:

| Metric | Trigger | Weight |
|---|---|---|
| `schadenfreude` | `opposition` set | `amount // 2` |
| `grace` | `rescind` set & `rescind_kind == 'opposition'` | `amount // 2` |
| `mudita` | `support` set | `amount // 2` ← **changed from full** |
| `regret` (new) | `rescind` set & `rescind_kind == 'support'` | `amount // 2` |

Every stake now pays the miller ½ at mint and ½ at rescind, fully symmetric
across opposition and support, supply-neutral over a full lifecycle. Picking full
weight instead would make the chain inflationary (millers minting up to 2× each
stake) and was explicitly rejected.

### Plumbing

- `Outflow` gains a `regret` property; `grace`/`schadenfreude` gain the
  `rescind_kind` / field-rename conditions; `mudita` divisor changes to `// 2`.
- `Transaction` and `Block` gain `regret` aggregations alongside the existing
  three.
- `Transaction.coinbase` gains a `regret` parameter; `Block.create_coinbase`
  passes `self.regret`.
- `Block.validate_coinbase` extends the expected component list to
  `[reward, schadenfreude, grace, mudita, regret]` (nonzero, in that order).

## Validation & accounting (`chain.py`)

`validate_block_txn` currently tracks a single `subject_amounts[subject]` pool
(fed by opposition inflows, drained by `forgive`/`subject` outflows) plus an
`other_amounts` pool, and requires both to net to zero. Two changes:

1. **Per-kind pools.** Track opposition and support amounts separately per
   subject (e.g. keyed by `(subject, kind)`), because the two are opposite
   sentiments and a rescind is single-kind. `validate_txn_inflow` must report
   *which* kind a consumed outflow was (`opposition` vs `support`) so the inflow
   routes to the correct pool — today it returns only the opposition subject.
2. **Rescind cross-check.** A `rescind` outflow drains the pool named by its
   `rescind_kind`. Validation rejects the transaction if `rescind_kind` does not
   match the kind of the outflows actually consumed (can't claim
   `--kind opposition` while eating support outflows). The
   "grains can't reach an `address` outflow" invariant falls out of the pool
   bookkeeping, as it does today for opposition.

Single-kind rescind is enforced: a rescind transaction consumes outflows of one
kind only.

## Balances & queries (`models.py`, `chain.py`)

- `subject_balance` → **`opposition_balance`** (unchanged logic: sum of unspent
  `opposition` outflows for a subject).
- `subject_support` → **`support_balance`**, logic changed to **unspent-only**
  (join inflows, filter spent), mirroring `opposition_balance`.
- `unforgiven_outflows` / `unforgiven_address_outflows` → rename to
  `unrescinded_*`; add a support-kind variant (or parameterize by kind) so
  `create_rescind` can gather unspent outflows of the requested kind.

## Domain construction (`chain.py`)

- `create_subject` → **`create_opposition`** (rename).
- `create_forgive` → **`create_rescind(wallet, amount, subject, kind)`**.
  Gathers the caller's unspent outflows *of `kind`* for `subject`, emits an
  `Outflow(amount, rescind=subject, rescind_kind=kind)`, with any change going
  back to the same-kind stake of the same subject.
- `create_support` — construction unchanged (consume general unspent outflows,
  emit a `support` outflow, change back to `address`); its output simply becomes
  rescind-eligible downstream.

## CLI / API / client surface

| Layer | Old | New |
|---|---|---|
| CLI txn | `txn subject` / `txn forgive` / `txn support` | `txn opposition` / `txn rescind … --kind opposition\|support` / `txn support` |
| CLI query | `subject balance` / `subject support` | `subject opposition` / `subject support` |
| API route | `/transaction/{subject,forgive,support}` | `/transaction/{opposition,rescind,support}` (rescind takes a `kind` param) |
| API route | `/subject/<s>/balance` | `/subject/<s>/opposition` (`/support` unchanged) |
| Client | `get_subject_transaction` / `get_forgive_transaction` / `get_subject_balance` / `get_subject_support` | `get_opposition_transaction` / `get_rescind_transaction(…, kind)` / `get_opposition_balance` / `get_support_balance` |

The query command group `subject` stays (it operates on subjects-the-noun); its
`balance` subcommand becomes `opposition` to name what it tallies. View classes,
Pydantic query models, and templates referencing the renamed kinds/metrics
update accordingly, and templates gain `regret`.

## Schema migration

Two hand-reviewed Alembic migrations, one per PR: PR 1 renames columns
`subject → opposition` and `forgive → rescind`; PR 2 adds `rescind_kind`. The
`support_balance` change is code-only (query shape). After each, `gumptionchain
db check` must still pass — the `db.create_all()` metadata and the migration head
must agree.

## Hard fork

This is a clean break, acceptable only because the chain is greenfield:

1. `data_csv` changes → all block header hashes change.
2. `mudita` full → ½ changes the coinbase consensus rule → blocks valid under
   the old rule fail under the new one.

No genesis/data is carried forward. Any local `gumptionchain.db` is discarded and
re-`init`'d (consistent with the existing Phase-8 "rm the db" guidance).

## Decomposition

Two PRs, matching the project's phased style:

- **PR 1 — pure rename, no behavior change.** `subject → opposition`,
  `forgive → rescind` (rescind still opposition-only, no `--kind` yet,
  `rescind_kind` absent), metrics keep current weights (`mudita` full). Purely
  mechanical: field/column/method/route/client/template renames + migration.
  Ships green on its own.
- **PR 2 — new capability.** Support becomes rescind-eligible; `rescind` gains
  `--kind` and the `rescind_kind` column; `support_balance` → unspent-only;
  per-kind validation pools + cross-check; `mudita` → ½ and the new `regret`
  metric + coinbase plumbing. This PR carries the consensus change.

Splitting this way keeps PR 1 a low-risk rename reviewable in isolation, and
isolates every consensus-affecting change in PR 2.

## Testing

- **Rename coverage (PR 1):** existing `test_payload`, `test_transaction`,
  `test_chain` suites updated to the new names; assert renamed routes/CLI verbs
  respond and old ones 404 / error.
- **Rescind symmetry (PR 2):** stake support then rescind it; assert
  `support_balance` drops to zero and grains land in a terminal `rescind`
  outflow, never an `address` outflow.
- **Cross-check:** a rescind whose `rescind_kind` disagrees with the consumed
  outflows is rejected (`ImbalancedTransactionError` / a dedicated error).
- **Single-kind:** a rescind attempting to consume mixed opposition+support
  outflows is rejected.
- **Coinbase economics:** for opposition and support lifecycles, assert the
  coinbase mints `amount // 2` at mint and `amount // 2` at rescind, and that
  `validate_coinbase` rejects a miscomputed `regret`/`mudita`.
- **Burn invariant:** attempting to spend an `opposition`/`support` outflow into
  an `address` outflow is rejected.

## Decisions log (resolved during brainstorming)

- Undo verb: **`rescind`** (over revoke/withdraw/retract), single term for both
  directions.
- Economics: **Model A** (burn stays burned) — rescinding never refunds.
- Disambiguation: **single-kind** rescind, **one** command/route with an
  explicit **`--kind`** parameter (not two subcommands).
- `rescind` is **self-describing** — `rescind_kind` stored on the outflow,
  cross-checked against consumed inflows.
- Sentiment weights: **symmetric halves** — `schadenfreude / grace / mudita /
  regret` all `amount // 2`; `mudita` drops from full weight. New metric for
  rescinded support is **`regret`**.
- Chain is **greenfield** — hard fork accepted, no migration/replay.

## Out of scope

- Any refund/withdrawable-stake economics (explicitly rejected: Model A only).
- Reworking the base block `reward` schedule or difficulty retargeting.
- Renaming `schadenfreude` / `mudita` (kept as-is; only `grace` is joined by the
  new `regret`).
