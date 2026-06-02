# N1 Remediation — Bound `fill_chain` Against a Hostile Sync Peer — Design

**Status:** Draft for review
**Date:** 2026-06-01
**Remediates:** Audit finding **N1 (High)** from the [P2P/networking audit](../audits/2026-06-01-network-p2p-audit.md): `fill_chain` walks an attacker-controlled ancestor chain with no depth cap (one HTTP round-trip + one `ChainFillBlock` commit per ancestor), and `request_block` never verifies the returned block's hash equals the requested `prev_hash` — so a hostile sync peer can serve an endless stream of fresh structurally-valid blocks and the walk never terminates, pinning the worker and growing the staging table without bound.

## Problem

`Node.fill_chain` (`node.py:324-413`) syncs a peer's chain by walking backward from the peer's tip: for each `prev_hash` not already in the local DB, it calls `self.request_block(prev_hash)` and stages the returned block as a `ChainFillBlock` row, until it reaches a known ancestor, genesis, or a `None` response. Two gaps make this unbounded:

1. **`request_block` (`node.py:221-236`) does not verify the returned block.** It returns `Block.from_json(r.text)` for any HTTP 200 without checking that `block.block_hash == block_hash` (the hash it asked for). So a peer can answer *any* `request_block(H)` with a block that does **not** hash to `H`, fully controlling what the walk sees next (the next target is the returned block's `prev_hash`).

2. **The walk loop (`node.py:343-361`) has no depth cap.** Its only termination conditions — `prev_hash is None`, `Block.from_db(prev_hash)` truthy, or `is_genesis_block(block)` — are all attacker-controlled when (1) holds. A peer that answers every request with a fresh block whose `prev_hash` is a new never-stored non-genesis hash drives the loop forever.

Validation is deferred: `request_block` runs only structural Pydantic validation (`Block.from_json`), not `Block.validate()` (PoW/merkle/chain), which happens later in the apply phase — after the *entire* walk completes, which a hostile peer never lets it reach. So all the cost (round-trips + committed staging rows) is paid before any meaningful rejection.

## Goal

Bound a single `fill_chain` invocation against a hostile or buggy peer, and flip the N1 demonstration test (`tests/test_network_audit.py::test_n1_fill_chain_has_no_depth_cap`) from strict-xfail to a passing regression — without breaking legitimate deep catch-up sync.

## Approach

Two coordinated changes in `src/cancelchain/node.py`, plus one config field. The hash check is the primary fix (it makes the cheap attack impossible); the depth cap is defense-in-depth against the one residual vector (a resourceful peer serving a long *pre-mined* valid chain whose blocks hash correctly).

### Change 1 — `request_block` verifies the returned block's hash (primary)

After parsing the peer's response, require the returned block's hash to equal the requested hash before accepting it:

```python
def request_block(self, block_hash: str) -> Block | None:
    for peer in self.peers:
        client = self.clients.get(peer)
        if client is None:
            continue
        try:
            r = client.get_block(block_hash=block_hash, raise_for_status=False)
            if r.status_code == 200:
                block = Block.from_json(r.text)
                if block is not None and block.block_hash == block_hash:
                    return block
                self.logger.warning(
                    'request_block: peer %s returned a block whose hash '
                    'does not match the requested %s; ignoring',
                    peer,
                    block_hash,
                )
        except httpx.HTTPError as re:
            self.logger.error(re)
        except Exception as e:
            self.logger.exception(e)
    return None
```

A peer cannot produce a block that hashes to an attacker-chosen `prev_hash` (second-preimage resistance of `mill_hash = sha256(sha512(...))`), so it can no longer feed fresh fakes to steer the walk. A legitimate peer's real ancestors hash correctly and are accepted unchanged. A mismatched response is treated as a miss (try the next peer; `None` if none match), reusing the existing "request failed" path in `fill_chain`.

### Change 2 — `fill_chain` depth cap (defense-in-depth)

Bound the number of ancestors a single walk will request/stage. Read the limit from app config; abort cleanly when exceeded:

```python
# inside fill_chain, before the walk loop:
max_depth = current_app.config['MAX_CHAIN_FILL_DEPTH']
requested = 0
...
while True:
    assert block is not None
    is_genesis = is_genesis_block(block)
    prev_hash = block.prev_hash
    if (prev_hash is None or Block.from_db(prev_hash)) or is_genesis:
        break
    requested += 1
    if requested > max_depth:
        self.logger.warning(
            'fill_chain: exceeded MAX_CHAIN_FILL_DEPTH (%d) walking back '
            'from tip %s; aborting',
            max_depth,
            last_block.block_hash,
        )
        return False
    block = self.request_block(prev_hash)
    if block is None:
        self.logger.error(f'Block request failed: {prev_hash}')
        return False
    progress_next()
    ChainFillBlock(...).commit()
```

The cap is checked **before** issuing the `request_block` for the next ancestor, so when `requested` would exceed `max_depth` the walk aborts having requested exactly `max_depth` ancestors. `return False` exits through the function's existing `finally` block (`node.py:410-412`), which deletes the `ChainFill` and its staged `ChainFillBlock` rows — partial apply is forbidden by the A2.e atomic-apply invariant, so abort is the only safe outcome. The node stays at its current tip; an operator can raise the limit and re-sync if a legitimate gap genuinely exceeds it.

### Change 3 — config field

Add to `EnvAppSettings` (`src/cancelchain/config.py`), alongside the existing numeric `API_CLIENT_TIMEOUT`:

```python
MAX_CHAIN_FILL_DEPTH: int = field(default=50000)
```

Env var `CC_MAX_CHAIN_FILL_DEPTH`; the `CC_` prefix is stripped so the `app.config` key is `MAX_CHAIN_FILL_DEPTH`. Default **50,000** (≈ a year of 10-minute blocks) — generous enough that no realistic catch-up sync hits it, low enough to bound the unbounded attack. A node is never deployed yet (no legacy chain to size against), so the default is a theoretical ceiling.

### Why `current_app.config` rather than constructor injection

`node.py` already requires an active Flask app context — `fill_chain` uses `db.session` and the `ChainFill`/`ChainFillBlock` DAOs throughout — so reading `current_app.config` adds no new contextual requirement. It also matches the demonstration test's contract (the test sets `app.config['MAX_CHAIN_FILL_DEPTH']` and calls `fill_chain` directly). Constructor injection would touch every `Node`/`Miller` construction site (`api.py`, `command.py`) and force a test rewrite for no real benefit. `from flask import current_app` is added to `node.py`.

## Error handling

No new exception types. The hash mismatch is a logged warning + miss (existing `None`-return path). The depth-cap abort is a logged warning + `return False` (existing return-and-cleanup path). Both reuse `fill_chain`'s existing failure handling; the `finally` guarantees `ChainFill` cleanup on every exit.

## Testing

### Flip the demonstration (strict-xfail → passing regression)

`tests/test_network_audit.py::test_n1_fill_chain_has_no_depth_cap`: remove the `@pytest.mark.xfail(strict=True)` marker. With `app.config['MAX_CHAIN_FILL_DEPTH'] = 3` and the patched `request_block` serving fresh fakes, the walk now aborts after requesting 3 ancestors, so `call_count <= 3` holds and the test passes. (The test patches `request_block`, so it exercises the depth cap specifically; the hash check is exercised by the new test below.)

### New regression — `request_block` hash verification

Add `tests/test_network_audit.py::test_n1_request_block_rejects_hash_mismatch` (a plain passing test, no xfail): build a `Node`/`Miller` with one peer whose mocked `get_block` returns HTTP 200 with a valid block whose `block_hash` differs from the requested hash; assert `request_block(<some-other-hash>)` returns `None`. Guards the primary fix.

### Regression suite

Full suite stays green. After this change: `tests/test_network_audit.py` shows **3 xfailed** (N2/N3/N4 still open) **+ 2 passed** (the flipped N1 cap test and the new hash-mismatch test); `--runxfail tests/test_network_audit.py` fails only the three still-open demonstrations. All five CI gates green; `mypy --strict` accepts the `current_app` import and the new int config field.

## Documentation updates

- **Audit report** (`docs/superpowers/audits/2026-06-01-network-p2p-audit.md`): mark **N1** remediated (✅ on the finding, table row Status, and recommendation item 1; past-tense the gap; add an `(As implemented: …)` note). Update the headline **0 Critical / 1 High / 2 Medium / 1 Low → 0 Critical / 0 High / 2 Medium / 1 Low**.
- **CLAUDE.md**: in the `Node`/networking section, note that `fill_chain` bounds its ancestor walk at `MAX_CHAIN_FILL_DEPTH` and `request_block` verifies the returned block's hash matches the requested hash; add `CC_MAX_CHAIN_FILL_DEPTH` to the `CC_*` config settings list.
- **Roadmap** (`docs/superpowers/ROADMAP.md`): mark the N1 bullet ✅ with the impl PR number.

## Out of scope

- N2 (mempool cap), N3 (gossip dedup), N4 (async publish) — separate remediation cycles.
- The `ChainFill` orphan-rows-on-process-crash hygiene note (A5.c): the depth cap reduces the worst-case orphan accumulation (a wedged walk can no longer stage unboundedly), but a periodic sweep of stale `ChainFill` rows remains separate operational-hygiene work.
- Incremental/resumable deep sync (so a legitimate gap larger than the cap could complete across multiple calls) — not needed at current scale; the generous default and the configurability cover it.
- `fill_peer` (the inbound-push counterpart): its `blocks` accumulation and back-off were examined in the audit and did not surface as a confirmed finding; not touched here.

## Acceptance criteria

- `request_block` returns `None` (and logs) when a peer's returned block hash ≠ the requested hash; returns the block unchanged on a match.
- `fill_chain` aborts with a logged warning and `return False` (with `ChainFill` cleanup) once it has requested `MAX_CHAIN_FILL_DEPTH` ancestors; legitimate syncs within the cap are unaffected.
- `MAX_CHAIN_FILL_DEPTH` is a config field (env `CC_MAX_CHAIN_FILL_DEPTH`, default 50000).
- `test_n1_fill_chain_has_no_depth_cap` passes with its xfail marker removed; `test_n1_request_block_rejects_hash_mismatch` passes; full suite green (`tests/test_network_audit.py`: 3 xfailed + 2 passed).
- Audit report headline `0 Critical / 0 High / 2 Medium / 1 Low`; N1 ✅. CLAUDE.md + roadmap updated.
