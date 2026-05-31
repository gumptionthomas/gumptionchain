# A7.b — Canonical-Genesis Uniqueness Design

**Audit finding:** A7.b (Low) — *Alternate-genesis admission fragments chain registry.*
**Also closes:** A7.j (Low, related — disjoint-ancestor reorg) — its only entry path is the alternate-genesis admission this fix rejects.
**Source audit:** `docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md`

---

## Problem

`Chain.validate_block` (`src/cancelchain/chain.py`) accepts any block whose
`prev_hash == GENESIS_HASH`, `idx == 0`, and `target == MAX_TARGET`,
**regardless of whether a different genesis is already persisted.**
`is_genesis_block(block)` is just a `prev_hash == GENESIS_HASH` flag, not an
"is this *the* canonical genesis" predicate. Each accepted alternate genesis
spawns a fresh `ChainDAO` row (via `Node.add_block`'s `create_chain`
fallback), fragmenting the chain registry into N parallel single-block
chains.

`ChainDAO.longest()` still picks the canonical winner by deterministic
tiebreaker, so **chain-correctness is preserved** — value conservation and
`wallet_balance` reads are unaffected (only the canonical chain's
`LongestChainBlockDAO` rows feed balances). The gap is **DB-bloat /
inventory-pollution**: a MILLER-role adversary can mint unlimited
cheap-to-mill alternate genesis blocks, each adding an unrooted
`ChainDAO`/`BlockDAO`/`TransactionDAO`/`OutflowDAO` row with no operational
recovery path. Severity Low.

**A7.j link.** A disjoint-ancestor reorg (Attack j) — where a longer chain
rooted at a *different* genesis displaces the canonical chain — can only be
mounted if that alternate genesis was first admitted. The
catastrophic-rebuild branch is itself correct PoW longest-chain behavior;
the actual gap is the alternate-genesis admission (A7.b). Rejecting
alternate genesis blocks closes A7.j's only entry path. Two-for-one.

## Goal

Enforce canonical-genesis uniqueness at the validation layer: once a genesis
block is persisted, reject any *different* block that also claims to be
genesis. Do this without changing the `is_genesis_block` predicate, the
bootstrap flow, or the schema.

## Approach

Validate-time uniqueness check (the audit's "more conservative fix"), using
a domain helper symmetric with the existing `Block.from_db`. A DB-layer
partial unique index was considered and rejected: a plain `UNIQUE(prev_hash)`
would wrongly forbid legitimate sibling/fork blocks that share a parent, and
a partial index needs migration work plus SQLite-specific handling for a Low
finding.

### Components

Four small touches, no schema/migration change.

**1. `src/cancelchain/exceptions.py` — new exception**

```python
class DuplicateGenesisError(InvalidBlockError):
    pass
```

Placed alongside the other `InvalidBlockError` subclasses. Mirrors the
`MismatchedCoinbaseError(InvalidCoinbaseError)` pattern introduced by A4.c.

**2. `src/cancelchain/block.py` — new classmethod**

Symmetric with the existing `Block.from_db(block_hash)`:

```python
@classmethod
def genesis_from_db(cls) -> Self | None:
    dao = BlockDAO.get(idx=0)
    return cls.from_dao(dao) if dao else None
```

Returns the persisted canonical genesis (or `None` if none exists yet).

- Reuses the existing `BlockDAO.get(idx=0)` classmethod (which executes
  `db.select(BlockDAO).filter_by(idx=0)` → `scalar_one_or_none()`).
- **Deliberately keyed on `idx == 0`, not `prev_hash == GENESIS_HASH`.**
  `idx == 0 ⟺ genesis` is guaranteed by `validate_block`'s
  `idx == prev_index + 1` rule (only a genesis has `prev_index == -1`).
  Keying on `idx` lets `block.py` avoid importing `GENESIS_HASH` from
  `chain.py`, which would create a circular import (`chain.py` already
  imports `Block` from `block.py`). `BlockDAO.idx` is an indexed column,
  so the lookup is cheap.
- `scalar_one_or_none()` is correct because the remediation guarantees at
  most one `idx == 0` row exists going forward; if that invariant were ever
  violated it would raise loudly (acceptable — it signals corruption this
  fix is designed to prevent). No pre-fix fragmented DBs exist to migrate
  (no deployed installs).

**3. `src/cancelchain/chain.py` — the check**

In `validate_block`, in the genesis branch, immediately after the
`FutureBlockError` check and before `prev_block` is fetched:

```python
if is_genesis_block(block):
    existing = Block.genesis_from_db()
    if existing is not None and existing.block_hash != block.block_hash:
        raise DuplicateGenesisError()
```

Plus importing `DuplicateGenesisError`. Early, cheap rejection for genesis
candidates; non-genesis blocks skip it entirely.

### Data flow & idempotency

The `existing.block_hash != block.block_hash` guard is the whole correctness
story:

| Scenario | `genesis_from_db()` | Result |
|---|---|---|
| First genesis (empty `BlockDAO`) | `None` | passes → persists |
| Alternate genesis (different hash) | canonical g1 | `DuplicateGenesisError`; `ChainDAO` count stays 1 |
| Byte-identical resubmit of canonical | g1 (same hash) | no raise (idempotent); `Node.receive_block` already short-circuits duplicate hashes before validation regardless |
| `Chain.validate()` full-chain revalidation | the canonical genesis itself | same hash → no raise (safe) |

The revalidation row is the critical one: this repeats the A4.c lesson —
the check must not break `cancelchain validate`. It is safe here because it
is a hash-equality guard against the *single* persisted genesis, not a
lineage walk or a "does any prior block exist" query, so revalidating the
canonical genesis compares it against itself and passes.

### Error handling

`DuplicateGenesisError` subclasses `InvalidBlockError`, so it propagates
through the existing `InvalidBlockError` handling in `Node.receive_block`,
`Node.fill_chain`, and `Chain.validate` (which wraps block errors into
`InvalidChainError`). No new catch sites.

## Testing

**Acceptance — un-xfail the existing demonstrator.**
`tests/test_verification_audit.py::test_a7_b_alternate_genesis_fragments_chain_registry`
already asserts the post-remediation behavior: `m1.receive_block(g2.to_json())`
raises `InvalidBlockError`, and the `ChainDAO` registry stays at one row.
Remove its `@pytest.mark.xfail` so it becomes a passing regression test.

**New — A7.j disjoint-reorg regression test.**
Add a test (e.g. `test_a7_j_disjoint_genesis_reorg_rejected`) that:
1. Mines the canonical genesis g1 (paying `wallet`); records the canonical
   tip and `ChainDAO` count (1).
2. Hand-builds a *longer* fork rooted at an alternate genesis: g2
   (`prev_hash=GENESIS_HASH`, `idx=0`, paying `miller_2_wallet`) plus at
   least one honestly-milled child b2 chaining off g2 — a chain that, if
   admitted, would be longer than the canonical chain and would trigger the
   catastrophic-rebuild reorg.
3. Submits the fork's **root** g2 directly via `m1.receive_block(g2.to_json())`
   and asserts it is rejected with `DuplicateGenesisError`. Then submits b2
   and asserts it is rejected with `MissingBlockError` — because g2 was never
   admitted, b2's parent resolves to no persisted block, so the longer fork
   is unrootable. (`Node.receive_block` raises `MissingBlockError` locally on
   a missing parent; no peer fill is attempted, keeping the test
   self-contained.) Asserts the canonical tip is unchanged and the `ChainDAO`
   count stays 1.

This proves closing A7.b closes A7.j: a longer disjoint fork cannot displace
the canonical chain because its root genesis is rejected at admission, and its
descendants are unrootable. Submitting g2 and b2 directly (rather than relying
on a peer `fill_chain` walk) keeps the test self-contained and independent of
peer-proxy wiring, while still exercising the exact gates that block the reorg.

**Docstring counts.** Update the `tests/test_verification_audit.py` module
docstring to reflect A7.b moving from open to remediated.

## Documentation updates

- **Audit doc** (`docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md`):
  mark A7.b remediated (status banner like the A2.e / A4.c entries),
  flip the A7.b Outcome/Result to REJECTED, and note A7.j is closed via the
  A7.b remediation. Update the intro summary — currently "Two have since been
  remediated (A2.e, A4.c); four remain open" → "Three have since been
  remediated (A2.e, A4.c, A7.b); three remain open (A7.h, A7.e, A1.f)" — and
  the remediation-priority section's A7.b entry. Also update the findings-
  table count line ("4 open findings: 0 Critical / 0 High / 0 Medium / 4 Low
  (post-A4.c)" → "3 open findings: 0 Critical / 0 High / 0 Medium / 3 Low
  (post-A7.b)").
- **ROADMAP** (`docs/superpowers/ROADMAP.md`): remove A7.b from the open-
  findings list, add a remediated entry linking the spec/plan + impl PRs,
  and update the severity line to **0 Critical / 0 High / 0 Medium / 3 Low**
  (remaining open: A7.h, A7.e, A1.f). A7.j was never a counted finding;
  note it as closed-via-A7.b.

## Out of scope

- The other open Low findings (A7.h non-printable subjects, A7.e
  `TXN_TIMEOUT` operator inconsistency, A1.f mined-txid mempool replay) —
  each is a separate follow-up PR.
- Any change to the `is_genesis_block` predicate, the bootstrap/genesis
  milling flow, or the schema.
- Hardcoding a canonical genesis block into `db.create_all()` / migrations
  (the rejected alternative — it changes the bootstrap flow).

## Acceptance criteria

1. `test_a7_b_alternate_genesis_fragments_chain_registry` passes with its
   `xfail` removed.
2. The new A7.j disjoint-reorg regression test passes.
3. Full suite green (`COLUMNS=200 uv run pytest`), `ruff check`/`format`
   clean, `mypy` clean. No migration / `db check` impact (no schema change).
