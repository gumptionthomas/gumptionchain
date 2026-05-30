# A4.c v2 — coinbase-to-block binding via prev_hash — design spec

**Status:** Draft for review
**Date:** 2026-05-30
**Supersedes:** `docs/superpowers/specs/2026-05-30-a4c-coinbase-uniqueness-design.md` and its plan `docs/superpowers/plans/2026-05-30-a4c-coinbase-uniqueness.md` (merged in PR #88). The v1 validation-layer approach is abandoned as unimplementable — see "Why v1 failed" below.

## Why v1 failed

The v1 design added a chain-lineage uniqueness check in `Chain.validate_block_coinbase`: reject a block whose coinbase `txid` already exists in the candidate's lineage. Implementation surfaced a fatal flaw the 7-round docs review never caught: **coinbase txids are not unique across legitimately-mined consecutive blocks.**

A coinbase has no inflows, and `Transaction.data_csv` (`src/cancelchain/transaction.py:136-146`) hashes only `(timestamp, address, public_key, inflows='', outflows, version)`. `now()` is second-resolution (`src/cancelchain/util.py:42`, `replace(microsecond=0)`). So two coinbases from the same miller, same REWARD, same S/G/M, mined in the same wall-clock second are **byte-identical → identical txid**. Under the test suite's trivial PoW (`easy_mill_chain`), consecutive blocks mine in the same second, so v1's check rejected legitimate consecutive blocks — 17 existing tests (`test_chain`, `test_models`, `test_miller`, `test_command`) failed with the proposed `DuplicateCoinbaseError`. The check could not distinguish a malicious verbatim replay from two legitimate same-second blocks because they are the same bytes.

This also reframes the A4.c *finding*: the "balance inflation via duplicate `block_transactions` m2m row" is a consequence of coinbase-txid determinism, not solely a malicious-replay attack — it manifests whenever two same-second same-reward blocks share a miller. In production (real PoW difficulty), consecutive blocks are spaced apart in time, so legitimate coinbases differ by timestamp; but the validator still cannot tell a colliding-legitimate coinbase from a replay. The root cause is that **the coinbase txid carries no block identity.**

## Goal

Make every coinbase intrinsically bound to the block-position it rewards, by including the block's `prev_hash` in the coinbase transaction's hashed data (its txid). Then validate that binding. After this change:

1. Two legitimate consecutive blocks have **distinct coinbase txids** (their `prev_hash` differs), even when mined in the same second — closing the read-side balance-inflation surface at its root and unbreaking the 17 tests.
2. A malicious coinbase replay (placing a coinbase bound to block N's parent into a block M extending a different parent) is **rejected by a local binding check** (`cb.prev_hash != block.prev_hash`) — no lineage walk, no `self.last_block` parent-start subtlety, no `Chain.validate()` revalidation hazard (the entire class of bug that consumed v1's review).

## Non-goals

- **Read-side accounting dedupe.** Once coinbase txids are unique per block-position, the `longest_chain_transactions_q` / `wallet_balance` join no longer double-counts a coinbase, so no read-layer change is needed. (A read-side dedupe was considered as an alternative remediation path and rejected in favor of the root-cause binding.)
- **Regular-transaction binding.** Only the coinbase is bound to its block. Regular transactions are unchanged — their `prev_hash` field stays `None` and is stripped from serialization, preserving their existing txids and the legitimate cross-fork replay of regular spends (audit Attack b).
- **Higher-resolution timestamps.** Making `now()` microsecond-resolution would reduce but not eliminate collisions and would not stop replay (a replay reuses the timestamp). Binding `prev_hash` is the correct fix; timestamp resolution is left unchanged.
- **Coinbase-author / miller-identity protocol field.** cancelchain has no protocol notion of "the miller." The prev_hash binding achieves block-uniqueness without introducing one.
- **Append-only Alembic migration.** Pre-1.0.0 cancelchain folds schema changes into the single base migration (no legacy installs); this spec regenerates the initial migration rather than adding a delta. Append-only discipline begins at the first tagged release.

## Decisions taken during brainstorming

- **Bind `prev_hash` (not a random nonce, not `idx`).** `prev_hash` uniquely identifies the block's parent, so it disambiguates forks (a block extending parent P vs. a block extending parent Q have different coinbase txids) and enables a direct binding check that rejects replays without any lineage walk. A random nonce would make coinbases globally unique but would not itself stop a replay (the replayed bytes are self-consistent), still requiring v1's lineage check. `idx` (block height) is weaker — two forks at the same height share an `idx`.
- **Bind into the txid AND validate the binding.** Both are required. Binding into `data_csv` (hence the txid) makes legitimate blocks' coinbases unique (fixes the inflation / the 17 tests). The explicit `cb.prev_hash == block.prev_hash` check rejects the replay (a replayed coinbase carries the wrong parent). Binding alone doesn't stop replay; validation alone doesn't fix the same-second legitimate collision.
- **Coinbases are block-bound; replay onto a different-parent block is rejected.** v1's "cross-fork coinbase replay is legitimate" premise (extending audit Attack b) was wrong for coinbases. A coinbase is the reward minted for a specific *parent* — binding it via `prev_hash` makes reusing it on a block extending a *different* parent invalid (the binding check `cb.prev_hash == block.prev_hash` fails). **The one residual case the binding does not reject is replay onto a *same-parent sibling*** (two competing blocks extending the same parent P share `prev_hash = P`, so a coinbase from one passes the other's binding check). That case is harmless and is the same "accepted non-issue" documented below: same-parent siblings sit at the same height on different forks, so at most one is ever in the longest chain and the read-side balance never double-counts. So the precise invariant the tests assert is "replay onto a block with a *different* `prev_hash` is rejected," NOT "all cross-fork replay is rejected." Attack b's legitimacy applies to *regular* txns (a spend valid on competing forks), not coinbases.
- **New exception `MismatchedCoinbaseError(InvalidCoinbaseError)`.** The v1 name `DuplicateCoinbaseError` never shipped (v1 was BLOCKED before any code merged). v2's failure mode is "coinbase bound to the wrong block," which `MismatchedCoinbaseError` names accurately. Subclassing `InvalidCoinbaseError` keeps the demonstration test's `pytest.raises(InvalidCoinbaseError)` matching.
- **Regenerate the base migration** rather than add a delta (pre-1.0 convention: no legacy installs, so fold schema changes into the single base migration rather than adding deltas).
- **Single PR for spec + impl plan; second PR for implementation** (mirrors the PR #86/#87 and #88 precedent).

## Architecture

### Part A — bind `prev_hash` into the coinbase txid

`Transaction` (`src/cancelchain/transaction.py`) gains an optional field:

```python
prev_hash: str | None = field(default=None)
```

`data_csv` (line 136) **conditionally** appends `prev_hash` after `version` — only when it is set:

```python
def data_csv(self) -> str:
    fields = [
        str(self.timestamp),
        str(self.address),
        str(self.public_key),
        ','.join(i.data_csv for i in self.inflows),
        ','.join(o.data_csv for o in self.outflows),
        str(self.version),
    ]
    if self.prev_hash is not None:
        fields.append(str(self.prev_hash))
    return ','.join(fields)
```

The conditional append is deliberate: a regular txn (`prev_hash is None`) produces a `data_csv` byte-identical to today, so **regular transactions' txids are unchanged** and only coinbases gain the extra field. (An unconditional append — always adding a trailing field, empty for regulars — would also work given no legacy chain, but needlessly churns every regular-txn txid. The conditional form is the chosen approach; the exact diff is in the impl plan.)

For a coinbase, `prev_hash` is set to the block's parent hash → the coinbase txid becomes a function of `(timestamp, address, public_key, outflows, version, prev_hash)`. Two consecutive blocks differ in `prev_hash` → distinct coinbase txids.

### Part B — validate the binding

`Chain.validate_block_coinbase` (`src/cancelchain/chain.py:278`) adds a local check:

```python
def validate_block_coinbase(self, block: Block) -> None:
    block.validate_coinbase()
    reward = self.block_reward(block)
    cb = block.coinbase
    if cb is not None:
        if cb.prev_hash != block.prev_hash:
            raise MismatchedCoinbaseError()
        outflow = cb.get_outflow(0)
        if outflow is not None and outflow.amount != reward:
            raise InvalidCoinbaseErrorRewardError()
```

This is purely local — it compares the coinbase's bound `prev_hash` against the block's `prev_hash`. No DB read, no walk. It behaves identically in the add-block path and in `Chain.validate()` full-chain revalidation, because it does not reference `self.last_block`. Tamper-resistance is free: `prev_hash` is in `data_csv`, so it is covered by `validate_txid` (txid recompute) and the signature; altering it changes the coinbase txid → merkle-root mismatch → block invalid.

### How this closes the attack

- **Legitimate consecutive blocks:** block N coinbase bound to `prev_hash = P_n`; block N+1 coinbase bound to `prev_hash = block_N.hash`. Different binding → different coinbase txid → no shared m2m row → no balance inflation. Binding check passes for each (the coinbase's `prev_hash` equals its block's `prev_hash`).
- **Malicious replay:** adversary copies block N's coinbase (bound to `P_n`) into block M extending the current tip (`block_M.prev_hash = tip_hash ≠ P_n`). `validate_block_coinbase` raises `MismatchedCoinbaseError` on the binding mismatch. The replay never persists.
- **Adversary rebuilds a fresh coinbase paying the original miller at block M:** that is a unique, correctly-bound coinbase (prev_hash = block M's parent) for a block the adversary actually mined — it is the adversary donating their own block reward, not an inflation. No invariant is violated.

### Accepted non-issue: same-parent sibling coinbases

Two competing *sibling* blocks (same parent P, hence same `prev_hash`) built by the *same* miller in the same second still produce identical coinbase txids. This is harmless: siblings sit at the same height on different forks, so at most one is ever in the longest chain — the read-side balance never double-counts. Cross-miller siblings differ anyway (different payout address → different outflows → different txid). No validation or accounting consequence.

## Changes

### Files (in scope)

- **Modify:** `src/cancelchain/transaction.py` — add the `prev_hash` field to the `Transaction` dataclass; append it to `data_csv` only when non-None; add `prev_hash` (optional) to `TransactionModel`, require it non-None on `CoinbaseTransactionModel`, and reject a non-None `prev_hash` on `RegularTransactionModel`; thread a `prev_hash` parameter through the `Transaction.coinbase(...)` classmethod and set it on the constructed coinbase.
- **Modify:** `src/cancelchain/block.py` — `create_coinbase` / `add_coinbase` pass `self.prev_hash` into `Transaction.coinbase(...)`. `seal` already requires `prev_hash` set before building the coinbase, so this is transparent for all callers that build blocks via `seal`.
- **Modify:** `src/cancelchain/chain.py` — `validate_block_coinbase` gains the `cb.prev_hash != block.prev_hash` binding check raising `MismatchedCoinbaseError`; import the new exception.
- **Modify:** `src/cancelchain/exceptions.py` — add `class MismatchedCoinbaseError(InvalidCoinbaseError): pass`.
- **Modify:** `src/cancelchain/models.py` — `TransactionDAO` gains a nullable `prev_hash` column so the coinbase's binding persists and its txid is recomputable on load. The domain↔DAO round-trip (`to_dao` / `from_dao`) carries it.
- **Modify:** `src/cancelchain/schema.py` if needed — ensure `prev_hash` is serialized for coinbases and `asdict_sans_none` strips it for regular txns (it already strips `None`, so likely no change beyond confirming the field flows through).
- **Regenerate:** `src/cancelchain/migrations/versions/0ca0de5fb211_initial_schema.py` — delete and regenerate the single initial migration so the `transaction` table's `create_table` includes the nullable `prev_hash` column. Hand-review per Phase 8 convention. (Pre-1.0 convention — no delta migration; fold into the single base migration.)
- **Modify:** `tests/test_verification_audit.py` — remove the `@pytest.mark.xfail` decorator on `test_a4_c_ii_coinbase_replay_inflates_balance` (it now passes — the replay onto a different-parent block is rejected by the binding mismatch); update its docstring to the v2 behavior; **do not add** a cross-fork-acceptance test (v2 rejects replay onto a *different-parent* block; a same-parent sibling still passes the binding, harmlessly) — instead add a binding test asserting a mismatched-`prev_hash` coinbase is rejected and that two consecutive blocks have distinct coinbase txids; update the module docstring (already due per the merged plan).
- **Modify:** `docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md` — close A4.c (Findings table, Attack c.ii trace, Executive summary, Recommendations §2) describing the v2 binding fix instead of the v1 lineage check.
- **Modify:** `docs/superpowers/ROADMAP.md` — move A4.c from open to closed, referencing the v2 design + impl PRs.
- **Modify (supersession banners):** prepend a "**Superseded by v2 — see `2026-05-30-a4c-v2-coinbase-binding-design.md`**" note to the top of the merged v1 spec and plan, so the historical docs point forward.

### Files (read but not modified)

- `src/cancelchain/util.py` — `now()` second-resolution (the collision root cause; left unchanged).
- `src/cancelchain/milling.py` — `mill_hash` / `data_csv` hashing (the txid computation; consumed, not changed).
- `tests/conftest.py` — the `add_chain_block` / `valid_chain` fixtures that surfaced the v1 collision; they pass unchanged under v2.

## Test plan

- **The 17 v1-breaking tests pass** under v2 (legitimate consecutive coinbases are now unique). Verified by the full suite.
- **`test_a4_c_ii_coinbase_replay_inflates_balance`** flips xfail→pass: the replayed coinbase's `prev_hash` (B_orig's parent) ≠ B_adv's `prev_hash` (B_orig's hash) → `MismatchedCoinbaseError` (an `InvalidCoinbaseError`) → `receive_block` rejects.
- **New binding tests:** (a) a coinbase whose `prev_hash` mismatches its block is rejected with `MismatchedCoinbaseError`; (b) a correctly-bound coinbase validates; (c) two consecutive legitimate blocks have *different* coinbase txids (the core invariant).
- **`uv run pytest --runxfail tests/test_verification_audit.py`** — the remaining open-finding xfails still fail; the A4.c test passes.
- **`cancelchain db check`** passes — the regenerated initial migration matches the `prev_hash`-bearing model.
- **`cancelchain db upgrade`** against a fresh SQLite builds the `transaction` table with `prev_hash`.
- **Round-trip:** a coinbase serializes (`to_json`) with `prev_hash`, deserializes (`from_json`), and recomputes the same txid; a regular txn serializes without a `prev_hash` key and its txid is unchanged from today (conditional-append form).
- **Docker builder build** succeeds (no Python-config surprises from the schema change).

## Acceptance

- `Transaction` carries `prev_hash`; coinbase `data_csv`/txid depends on it; regular txns are unaffected (no `prev_hash` key, unchanged txid).
- `validate_block_coinbase` raises `MismatchedCoinbaseError` when `cb.prev_hash != block.prev_hash`.
- `TransactionDAO` has a nullable `prev_hash` column; the single regenerated initial migration creates it; `cancelchain db check` is clean.
- `tests/test_verification_audit.py::test_a4_c_ii_coinbase_replay_inflates_balance` passes with no xfail; the new binding tests pass; two consecutive legitimate blocks have distinct coinbase txids.
- Full suite green; `uv run ruff check src tests`, `ruff format --check`, `mypy`, `cancelchain db check` all exit 0.
- Audit doc + ROADMAP record A4.c closed by the v2 binding fix; v1 spec/plan carry supersession banners.
- `docker build --target builder` succeeds.

## Risks

### Risk: serialization / schema flow for `prev_hash` is incomplete

A new transaction field has to round-trip through dataclass ↔ Pydantic model ↔ DAO ↔ JSON, and the coinbase/regular distinction must be enforced (coinbase requires it, regular forbids it). A missed path means either a coinbase loses its binding on reload (txid mismatch) or a regular txn unexpectedly carries one. **Mitigation:** the impl plan walks each layer (`data_csv`, the three Pydantic models, `to_dao`/`from_dao`, `asdict_sans_none`, `to_json`/`from_json`) with a round-trip test (coinbase preserves prev_hash + txid; regular has no prev_hash + unchanged txid). The `mypy --strict` gate catches `str | None` handling gaps.

### Risk: the regenerated initial migration diverges from the model

Hand-regeneration can miss a constraint or type flavor. **Mitigation:** `cancelchain db check` (a CI gate) fails if the regenerated migration's schema doesn't match `db.create_all()`. The plan runs it explicitly. Per the project convention that DB changes go through models, the column is defined on the model first, then the migration is regenerated from it — never hand-edited in isolation.

### Risk: existing tests that construct coinbases directly (not via `Block.seal`) break

Most coinbases are built via `Block.seal` → `create_coinbase`, which will thread `prev_hash` automatically. Any test calling `Transaction.coinbase(...)` directly without a `prev_hash` would now build an unbound (or mis-validated) coinbase. **Mitigation:** the plan greps for direct `Transaction.coinbase(` / `.coinbase(` call sites and updates them; the full-suite run surfaces any missed caller.

### Risk: `data_csv` change silently alters regular-txn txids

If the field is appended unconditionally, every regular txn's `data_csv` (and txid) changes. **Mitigation:** the spec mandates the **conditional-append** form — `prev_hash` is appended to `data_csv` only when non-None — so regular txns are byte-identical to today and only coinbases gain the field. A round-trip test asserts a regular txn's txid is unchanged.

### Risk: v1 docs left contradictory

The merged v1 spec/plan describe an abandoned approach; the audit doc / ROADMAP reference them. **Mitigation:** supersession banners on the v1 docs point to v2; the audit-doc closure + ROADMAP entry describe the v2 binding fix, not the v1 lineage check.

## Open decisions

None at design time. Brainstorming resolved:

- Bind `prev_hash` (over random nonce / `idx`).
- Bind into the txid AND validate the binding (both required).
- Coinbases are block-bound; replay onto a *different-parent* block is rejected (same-parent siblings still pass, harmlessly) — v1's blanket "cross-fork replay is legitimate" premise inverted.
- New `MismatchedCoinbaseError(InvalidCoinbaseError)`.
- Conditional-append in `data_csv` (regular txids unchanged).
- Regenerate the base migration (no delta, pre-1.0).
- Single-PR spec + plan; second-PR implementation.

## What comes next

- **Impl PR.** Executes this design. Branch `fix/a4c-v2-coinbase-binding`. Touches `transaction.py`, `block.py`, `chain.py`, `exceptions.py`, `models.py`, the regenerated migration, `tests/test_verification_audit.py`, the audit doc, ROADMAP, and the v1-doc supersession banners.
- **Next audit remediations** (per ROADMAP, after A4.c closes): A7.b (alternate-genesis admission — Low, two-for-one with A7.j), then A7.h, A7.e, A1.f.
- **API auth audit.** Still deferred.
