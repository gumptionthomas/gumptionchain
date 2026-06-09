# EGU #163 — resumable forward block sync

**Date:** 2026-06-08
**Issue:** #163 (initial network sync can't adopt a chain deeper than `MAX_CHAIN_FILL_DEPTH`; non-resumable). EGU readiness (#151), on the launch checklist (#190).
**Status:** design approved

## Goal

Let a node **network-sync a chain of any depth from genesis** (or catch up when
far behind), purely peer-to-peer, **resumably** — no out-of-band JSONL export,
no `MAX_CHAIN_FILL_DEPTH` ceiling, no multi-GB single transaction.

## Why the current path can't do it (the key insight)

A block can only be committed **genesis-first** — `validate_block` needs the
parent block + UTXO history in the DB. But `Node.fill_chain` walks **backward**
from the peer's tip, reaching the *recent* end first and genesis *last*. So a
capped backward walk on a fresh node gathers the **wrong end** (an un-anchored
tip suffix it can't apply); to commit a genesis-anchored prefix it must first
stage the *entire* chain back to genesis — exactly what `MAX_CHAIN_FILL_DEPTH`
forbids (the N1 anti-DoS bound). Committing genesis-anchored prefixes
incrementally therefore **requires fetching forward**, which the protocol can't
do today (peers only answer `get_block(hash)` and `get_block()`=latest).

## Approach: "network import" — forward fetch by height, commit per block

Fetch the peer's **longest chain forward by height** in batches and
validate + commit each block genesis-first — the same proven model as
`gumptionchain import`, but the source is a peer's HTTP API instead of a file.

- **The committed local tip *is* the progress.** No `ChainFill` staging: each
  block is committed as it's validated (`add_block(commit=True)`), so an
  interruption just leaves a shorter valid chain and a re-run resumes from the
  new tip. Inherently resumable; no watermark/abort-type bookkeeping.
- **No giant transaction** (per-block commit) and **no depth ceiling**
  (`MAX_CHAIN_FILL_DEPTH` is not consulted on this path).
- **Anti-tamper / anti-DoS** comes from three existing-or-cheap checks per block,
  not from a staging cap:
  1. `block.get_header_hash() == block.block_hash` (computed-hash integrity, as
     in `request_block`) — a peer can't forge a block's identity.
  2. **prev_hash linkage**: each block's `prev_hash` must equal the local tip's
     hash (genesis: `prev_hash == GENESIS_HASH`) — a peer can't splice a fork
     into our chain.
  3. **PoW + full `validate_block`** before commit — a hostile peer can't
     cheaply mint valid blocks, so it can't make us commit garbage; an invalid
     block stops the sync (rejected), having committed only valid ancestors.

## Coexistence with backward `fill_chain`

`fill_chain` (backward walk + `MAX_CHAIN_FILL_DEPTH` + `ChainFill` staging +
A2.e atomic apply) **stays unchanged** for the gossip path — `receive_block` /
`Miller.poll_latest_blocks` filling a few missing ancestors of a just-received
block (a short divergent suffix, where it works fine). The new forward-sync
powers the **`sync` command / explicit catch-up** (the #163 fresh-node and
far-behind cases). Two tools, each used where it fits.

## New peer endpoint: height-range batch fetch

`GET /api/blocks?from_idx=K&limit=N` (READER-authed, like `get_block`):

- Returns a JSON array of the **longest-chain** blocks at heights
  `K .. K+N-1` (in ascending order), as many as exist — served from the
  `LongestChainBlockDAO` materialization joined to `BlockDAO` on `idx`
  (canonical block per height; O(1)-ish height lookups).
- `limit` is clamped server-side to a max (`GC_SYNC_BATCH_SIZE`, see config) to
  bound response size; a request beyond the tip returns `[]`.
- A pydantic query model validates `from_idx >= 0`, `limit >= 1` (mirrors the
  existing query-validated views).
- `ApiClient.get_blocks(from_idx, limit) -> list[Block]` signs + sends it like
  the other client methods.

## Forward-sync routine

`Node.sync_forward(client, progress=None) -> SyncResult` (exact name TBD in
plan):

```
tip = longest_chain tip (or None)
next_idx = (tip.idx + 1) if tip else 0
loop:
    batch = client.get_blocks(next_idx, BATCH)        # ascending by idx
    if batch is empty: break                          # caught up
    for block in batch:                               # ascending
        verify get_header_hash == block_hash          # integrity
        verify links to current tip                   # prev_hash == tip.hash
            (genesis: prev_hash == GENESIS_HASH)       # ... or divergence
        if diverged: stop, report DIVERGED at idx      # detect + defer
        add_block(block, commit=True)                  # validate + commit
        tip = block; next_idx += 1
return CAUGHT_UP
```

- **Resumable**: progress is the committed tip; a crash/interruption mid-batch
  loses only the uncommitted remainder of that batch, and a re-run continues
  from the new tip.
- **Divergence (detect + defer)**: a `prev_hash`-linkage mismatch means the
  peer's chain at that height doesn't extend ours (a real fork). Forward-sync is
  extend-only this pass: it **stops and reports DIVERGED at height K** with the
  expected vs received `prev_hash`, committing nothing past the fork point. A
  fresh node from genesis never diverges; a same-chain behind node never
  diverges; only an actual fork triggers it — and full reorg-via-forward-sync
  (backward common-ancestor search + adopt-if-more-work) is a follow-up.

## `sync` command

`sync` (`command.py`) calls `request_latest_blocks()` per peer, then — instead
of `fill_chain` — runs `sync_forward` against that peer's client when the peer's
tip is ahead of ours (forward-sync fetches forward until caught up to the peer's
longest chain). Reports CAUGHT_UP / DIVERGED / a request failure per peer.
`Miller.poll_latest_blocks` keeps calling `fill_chain` (gossip short-fill).

## Config

- `GC_SYNC_BATCH_SIZE` (env `GC_SYNC_BATCH_SIZE`, default e.g. 256) — blocks per
  forward request; also the server-side `limit` clamp.
- `MAX_CHAIN_FILL_DEPTH` is unchanged and remains the backward-`fill_chain`
  bound; forward-sync does not use it.

## Testing

- **Deep-chain adoption past the cap**: set `MAX_CHAIN_FILL_DEPTH` small, build a
  chain *longer* than it on a "peer," forward-sync from an empty node, assert the
  whole chain is adopted (proves the ceiling is gone). Use the
  `requests_proxy`/`remote_requests_proxy` pattern to route peer HTTP into a test
  client.
- **Resumability**: interrupt forward-sync partway (e.g. fail a batch), assert a
  re-run resumes from the committed tip and finishes; the committed prefix is a
  valid chain throughout.
- **Divergence detect**: peer on a fork (different block at a shared height) →
  forward-sync stops with DIVERGED, commits nothing past the fork, no corruption.
- **Integrity**: a block whose `get_header_hash()` ≠ `block_hash`, or whose
  `prev_hash` doesn't link, is rejected (sync stops cleanly).
- **Endpoint**: returns the longest-chain blocks for a height range in ascending
  order; clamps `limit`; `[]` beyond the tip; only canonical (longest-chain)
  blocks (not fork blocks at the same height); query-validation 400s.
- **`sync` command**: catches a behind node up to a peer; reports per-peer
  outcomes; unchanged gossip/miller path still uses `fill_chain`.
- Hard gates: ruff + format, mypy strict, pytest.

## PR decomposition (sequential)

0. **docs** — this spec + the plan.
1. **Forward-fetch endpoint + client** — `GET /api/blocks?from_idx=&limit=`,
   the query model + `LongestChainBlockDAO`-backed range query, `GC_SYNC_BATCH_SIZE`
   config, `ApiClient.get_blocks`, and endpoint/client tests. Server primitive,
   no sync logic yet.
2. **Forward-sync routine + `sync` command** — `Node.sync_forward` (integrity +
   linkage + per-block validate/commit + divergence detect + resumability),
   wire it into `sync`, leave `fill_chain`/gossip untouched; deep-chain,
   resumability, divergence, and command tests.

## Out of scope / follow-ups

- **Full reorg via forward-sync** (divergence → common-ancestor search → adopt
  the heavier branch). This pass only *detects* divergence.
- **Parallel / pipelined block download** (the routine is serial-batch; batching
  already removes the one-request-per-block cost).
- Replacing or removing backward `fill_chain` / `ChainFill` (kept for gossip).
