# A7.e — Single `TXN_TIMEOUT` Expiry Definition Design

**Audit finding:** A7.e (Low) — *`TXN_TIMEOUT` comparison-operator inconsistency across call sites.*
**Source audit:** `docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md`

---

## Problem

`TXN_TIMEOUT = timedelta(hours=4)` (`src/cancelchain/block.py`) bounds how old a
transaction may be. The "is this txn expired?" comparison is hand-coded at
multiple sites, and the operators drifted, so a transaction whose timestamp is
*exactly* `TXN_TIMEOUT` old (`txn_ts == reference − TXN_TIMEOUT`) is treated
inconsistently:

| Site | Reference | Comparison | Boundary txn |
|---|---|---|---|
| `Block.validate_transaction` (block.py) | block timestamp | expired iff `txn_ts < ref − TXN_TIMEOUT` | **alive** |
| `PendingTxnDAO.json_datas` (api pending query) | `now()` | keeps `timestamp >= cutoff` | **alive** |
| `Node.discard_expired_pending_txns` (node.py) | `now()` | discards `txn_ts <= cutoff` | **dropped** |
| `Miller.pending_chain_txns` (miller.py) | `now()` | yields `txn_ts > cutoff` | **excluded** |

A boundary txn is "non-expired" per the block validator but "expired" per
pending-pool maintenance and miller selection. No correctness invariant is
violated in practice today (a miller would exclude such a txn from a block
anyway, so the block validator never sees a contested boundary txn), but the
inconsistency is a latent refactor hazard: the four sites encode the same
concept four different ways, so a future edit to one can silently diverge from
the others. Severity Low.

## Goal

Define the expiry boundary **once** and apply it consistently, so the four
sites can never drift again.

## Canonical rule (forced by the consensus anchor)

A transaction is **expired iff its timestamp is strictly older than
`TXN_TIMEOUT`** relative to the reference time:

```
expired(txn_ts, reference) ⟺ txn_ts < reference − TXN_TIMEOUT
```

The boundary is **open**: a txn exactly `TXN_TIMEOUT` old is **alive**.

This direction is not a free choice. `Block.validate_transaction` is consensus
code (it decides which txns are valid in a block) and already uses strict `<`;
changing it would be a consensus change. The SQL pending-query site already
agrees (`timestamp >= cutoff` keeps the boundary). So the canonical rule is
pinned to the unchangeable consensus anchor, and only the two drifting
pending-pool sites move to match it.

## Approach

A single shared helper encodes the rule; the three Python sites call it. The
SQL site (a `where` clause, which cannot call a Python helper) is already
canonical and gets a cross-referencing comment. Chosen over a pure inline
operator-swap (the audit's literal sketch) because A7.e's root cause is the
*absence of a single definition* — an inline fix corrects today's operators but
leaves four hand-maintained comparison sites that can drift again.

### Component: the helper (`src/cancelchain/block.py`, beside `TXN_TIMEOUT`)

```python
def txn_is_expired(
    txn_timestamp_dt: datetime, reference_dt: datetime
) -> bool:
    """A txn is expired iff its timestamp is strictly older than
    TXN_TIMEOUT relative to reference_dt. Open boundary: a txn exactly
    TXN_TIMEOUT old (txn_timestamp_dt == reference_dt - TXN_TIMEOUT) is
    NOT expired.
    """
    return txn_timestamp_dt < reference_dt - TXN_TIMEOUT
```

No circular-import risk: `block.py` owns `TXN_TIMEOUT`, and `node.py` /
`miller.py` already import from `block.py`. The helper takes two non-`None`
datetimes; callers keep their existing `None`-guards.

### Call-site changes

1. **`Block.validate_transaction`** (consensus, block.py) — replace
   `if txn_ts_dt < self.timestamp_dt - TXN_TIMEOUT:` with
   `if txn_is_expired(txn_ts_dt, self.timestamp_dt):`. **Byte-identical
   behavior** (same expression), so no consensus change — this routes the
   anchor through the shared definition so the pool sites can't diverge from
   it.

2. **`Node.discard_expired_pending_txns`** (node.py) — replace the
   `expired_dt = now() - TXN_TIMEOUT` + `txn.timestamp_dt <= expired_dt` logic
   with `if txn.timestamp_dt is not None and txn_is_expired(txn.timestamp_dt, now()):`.
   Boundary txn is now **kept** (was dropped under `<=`).

3. **`Miller.pending_chain_txns`** (miller.py) — replace the
   `expired_dt = now() - TXN_TIMEOUT` + `txn.timestamp_dt > expired_dt` logic
   with `... and not txn_is_expired(txn.timestamp_dt, now()) and ...`.
   Boundary txn is now **yielded** (was excluded under `>`).

4. **`PendingTxnDAO.json_datas`** (models.py) — unchanged
   (`stmt.where(cls.timestamp >= expired)` already implements the canonical
   convention: it keeps `timestamp >= cutoff`, i.e. expires iff `< cutoff`).
   Add a one-line comment cross-referencing `txn_is_expired`'s open-boundary
   semantics so the SQL site is recognizably the same rule.

### Imports

- `node.py` and `miller.py`: swap the `TXN_TIMEOUT` import for `txn_is_expired`
  (they no longer compute `now() - TXN_TIMEOUT` themselves). `miller.py` keeps
  `MAX_TRANSACTIONS` and `Block`.
- `api.py`: keeps `TXN_TIMEOUT` (it still computes the SQL cutoff
  `now() - TXN_TIMEOUT` to pass into `json_datas`).

## Behavior change

Only the pending-pool boundary moves: a txn whose timestamp is *exactly*
`TXN_TIMEOUT` old is now retained by `discard_expired_pending_txns` and
selected by `pending_chain_txns`, instead of being dropped/excluded — aligning
the pool with the block validator. Consensus (block validity) is unchanged.
The effect is negligible in practice (it only matters at the exact one-second
boundary instant) but removes the inconsistency.

## Error handling

Unchanged. `Block.validate_transaction` still raises `ExpiredTransactionError`
on a genuinely-expired txn; the pending-pool sites still silently
drop/exclude. No new exceptions.

## Testing

- **Acceptance:** un-xfail `test_a7_e_txn_timeout_boundary_inconsistency` in
  `tests/test_verification_audit.py` — a txn timestamped exactly
  `now − TXN_TIMEOUT` survives `discard_expired_pending_txns` (and the test
  already cross-checks that `Block.validate_transaction` accepts it at the
  boundary). Remove its `@pytest.mark.xfail`.
- **Unit tests** for `txn_is_expired` (in `tests/test_block.py`): just-before
  the boundary (`reference - TXN_TIMEOUT - 1s`) → `True`; exactly at the
  boundary (`reference - TXN_TIMEOUT`) → `False`; just-after
  (`reference - TXN_TIMEOUT + 1s`) → `False`.
- **Miller boundary test** (in `tests/test_miller.py`): a pending txn
  timestamped exactly `now − TXN_TIMEOUT` IS yielded by `pending_chain_txns`
  (the site the existing audit test does not directly exercise), and a txn
  one second older is NOT.

## Documentation updates

- **Audit doc**: mark A7.e remediated (status lead-in matching the
  A4.c/A7.b/A7.h convention; mark the finding's pre-remediation description as
  past tense), flip its sub-attack Outcome to REJECTED, remove its row from the
  open findings table, update the intro count ("two remain open (A7.e, A1.f)" →
  "one remains open (A1.f)") and the findings-table count ("2 open findings …
  2 Low (post-A7.h)" → "1 open finding … 1 Low (post-A7.e)"), and update the
  remediation-priority A7.e entry to `✅ Implemented`.
- **ROADMAP**: update the open-findings count prose, remove the A7.e numbered
  item (renumber so A1.f becomes the sole item), and add an A7.e entry to the
  Closed items section (mirroring the A7.h entry), severity → **0 Critical /
  0 High / 0 Medium / 1 Low**. Do not modify earlier closed-item historical
  tallies.

## Out of scope (non-goals)

- Changing the `TXN_TIMEOUT` *value* (4h) or making block validation use
  `now()` instead of the block timestamp (the block-relative reference is
  deliberate and consensus-correct).
- Unifying the reference-time choice across sites (block timestamp vs `now()`)
  — those references are correct for their contexts; A7.e is only about the
  boundary *operator*.

## Acceptance criteria

1. `test_a7_e_txn_timeout_boundary_inconsistency` passes with its `xfail`
   removed.
2. New `txn_is_expired` unit tests and the miller boundary test pass.
3. Full suite green (`COLUMNS=200 uv run pytest`), `ruff check`/`format`
   clean, `mypy` clean. No migration / schema change.
