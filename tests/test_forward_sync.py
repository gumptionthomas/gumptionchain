"""Tests for EGU #163 PR 2 — resumable forward block sync.

`Node.sync_forward` network-imports a peer's longest chain forward by
height, validating + committing each block genesis-first. These tests stand
up a peer chain in `remote_app` (routed via `remote_requests_proxy`), build
an `ApiClient` that signs as the MILLER signing_key `remote_app` trusts, and run
`sync_forward` against the empty/behind local `app`.

The headline case proves the `MAX_CHAIN_FILL_DEPTH` ceiling is gone: a peer
chain LONGER than the cap is adopted in full, because forward-sync never
consults that bound.
"""

from __future__ import annotations

import datetime

from gumptionchain.api_client import ApiClient
from gumptionchain.block import Block
from gumptionchain.chain import GENESIS_HASH
from gumptionchain.miller import Miller
from gumptionchain.node import Node
from gumptionchain.util import now


def _mill_chain(milling_signing_key, count, time_step):
    """Mill `count` blocks onto the current app's chain (one Miller, the
    standard create_block -> mill_block flow) under the supplied
    time_stepper so successive timestamps strictly increase. Returns the
    final block."""
    m = Miller(milling_signing_key=milling_signing_key)
    last: Block | None = None
    for _ in range(count):
        next(time_step)
        b = m.create_block()
        m.mill_block(b)
        last = b
    assert last is not None
    return last


def _peer_client(remote_host_netloc, miller_2_signing_key):
    """An ApiClient that (under the active remote_requests_proxy) routes
    into remote_app and signs as miller_2_signing_key — MILLER on remote_app,
    which satisfies the READER-authed /api/blocks endpoint."""
    host = f'http://{miller_2_signing_key.address}@{remote_host_netloc}'
    return ApiClient(host, miller_2_signing_key)


def _local_node(app):
    return Node(
        host=app.config['NODE_HOST'],
        peers=app.config['PEERS'],
        clients=app.clients,
        logger=app.logger,
    )


def test_sync_forward_adopts_chain_deeper_than_cap(
    app,
    remote_app,
    remote_requests_proxy,
    remote_host_netloc,
    miller_2_signing_key,
    time_machine,
    time_stepper,
):
    """Headline #163 test: a peer chain LONGER than MAX_CHAIN_FILL_DEPTH is
    adopted in full by forward-sync, with SYNC_BATCH_SIZE forcing multiple
    batches. Proves the depth ceiling does not apply to this path."""
    start = now() - datetime.timedelta(hours=2)
    time_step = time_stepper(start=start)
    # Peer holds a 7-block chain (idx 0..6).
    with remote_app.app_context():
        tip = _mill_chain(miller_2_signing_key, 7, time_step)
        assert tip.idx == 6

    app.config['MAX_CHAIN_FILL_DEPTH'] = 3
    app.config['SYNC_BATCH_SIZE'] = 2
    with app.app_context():
        node = _local_node(app)
        assert node.longest_chain is None
        client = _peer_client(remote_host_netloc, miller_2_signing_key)
        result = node.sync_forward(client)
        assert result == 'caught_up'
        lc = node.longest_chain
        assert lc is not None
        assert lc.last_block is not None
        assert lc.last_block.idx == 6


def test_sync_forward_is_resumable_and_idempotent(
    app,
    remote_app,
    remote_requests_proxy,
    remote_host_netloc,
    miller_2_signing_key,
    time_machine,
    time_stepper,
):
    """After a partial sync (committed prefix), a re-run resumes from the
    committed tip and finishes; once caught up it is a no-op returning
    'caught_up'."""
    start = now() - datetime.timedelta(hours=2)
    time_step = time_stepper(start=start)
    with remote_app.app_context():
        tip = _mill_chain(miller_2_signing_key, 5, time_step)
        assert tip.idx == 4

    app.config['SYNC_BATCH_SIZE'] = 2
    with app.app_context():
        node = _local_node(app)
        client = _peer_client(remote_host_netloc, miller_2_signing_key)

        # Partial sync: stop after the first batch by capping get_blocks.
        real_get_blocks = client.get_blocks
        calls = [0]

        def limited_get_blocks(from_idx, limit):
            calls[0] += 1
            if calls[0] > 1:
                return []
            return real_get_blocks(from_idx, limit)

        client.get_blocks = limited_get_blocks  # type: ignore[method-assign]
        result = node.sync_forward(client)
        assert result == 'caught_up'  # saw an empty batch -> stopped
        lc = node.longest_chain
        assert lc is not None and lc.last_block is not None
        partial_idx = lc.last_block.idx
        assert partial_idx == 1  # only the first batch (idx 0,1) committed

        # Resume from the committed tip with the real client.
        client.get_blocks = real_get_blocks  # type: ignore[method-assign]
        result = node.sync_forward(client)
        assert result == 'caught_up'
        lc = node.longest_chain
        assert lc is not None and lc.last_block is not None
        assert lc.last_block.idx == 4

        # Idempotent: already caught up -> no-op.
        result = node.sync_forward(client)
        assert result == 'caught_up'
        lc = node.longest_chain
        assert lc is not None and lc.last_block is not None
        assert lc.last_block.idx == 4


def test_sync_forward_detects_divergence(
    app,
    remote_app,
    remote_requests_proxy,
    remote_host_netloc,
    miller_2_signing_key,
    time_machine,
    time_stepper,
):
    """A block whose prev_hash does not link to the local tip is a fork:
    sync_forward stops, returns 'diverged', and commits nothing past the
    fork point (local tip unchanged)."""
    start = now() - datetime.timedelta(hours=2)
    time_step = time_stepper(start=start)
    with remote_app.app_context():
        _mill_chain(miller_2_signing_key, 3, time_step)

    app.config['SYNC_BATCH_SIZE'] = 8
    with app.app_context():
        node = _local_node(app)
        client = _peer_client(remote_host_netloc, miller_2_signing_key)
        # Sync the genuine chain first so a tip exists.
        assert node.sync_forward(client) == 'caught_up'
        lc = node.longest_chain
        assert lc is not None and lc.last_block is not None
        tip_before = lc.last_block.block_hash
        idx_before = lc.last_block.idx

        # Serve, at the NEXT height, a block whose prev_hash does NOT link to
        # our tip (a forged, well-formed-but-unlinked block).
        forged = Block.from_dict(lc.last_block.to_dict())
        # A valid-shaped but non-linking prev_hash (still 64-char hash):
        forged_prev = 'a' * 64
        next_idx = idx_before + 1

        def diverging_get_blocks(from_idx, limit):
            if from_idx > idx_before:
                # Reuse the real tip block's body but relink it to a
                # non-tip parent at the next height, recompute its hash so
                # the integrity check passes (only the LINKAGE check fails).
                b = Block.from_dict(forged.to_dict())
                b.link(next_idx, forged_prev, b.target)
                b.proof_of_work = 0
                # Re-mill against the easy target so PoW would pass; but the
                # linkage check fires before validate, so just set a
                # consistent block_hash for the integrity check.
                b.block_hash = b.get_header_hash()
                return [b]
            return []

        client.get_blocks = diverging_get_blocks  # type: ignore[method-assign]
        result = node.sync_forward(client)
        assert result == 'diverged'
        lc = node.longest_chain
        assert lc is not None and lc.last_block is not None
        assert lc.last_block.block_hash == tip_before  # tip unchanged
        assert lc.last_block.idx == idx_before


def test_sync_forward_rejects_header_hash_mismatch(
    app,
    remote_app,
    remote_requests_proxy,
    remote_host_netloc,
    miller_2_signing_key,
    time_machine,
    time_stepper,
):
    """A returned block whose computed header hash != its self-reported
    block_hash is rejected: sync_forward returns 'diverged' and the tip is
    unchanged."""
    start = now() - datetime.timedelta(hours=2)
    time_step = time_stepper(start=start)
    with remote_app.app_context():
        _mill_chain(miller_2_signing_key, 2, time_step)

    app.config['SYNC_BATCH_SIZE'] = 8
    with app.app_context():
        node = _local_node(app)
        client = _peer_client(remote_host_netloc, miller_2_signing_key)

        def tampered_get_blocks(from_idx, limit):
            # The genesis block, but with a forged self-reported block_hash
            # that won't equal its computed header hash.
            blocks = ApiClient.get_blocks(client, from_idx, limit)
            if blocks:
                blocks[0].block_hash = 'f' * 64
            return blocks

        client.get_blocks = tampered_get_blocks  # type: ignore[method-assign]
        result = node.sync_forward(client)
        assert result == 'diverged'
        # Nothing committed: local chain still empty.
        assert node.longest_chain is None


def test_sync_forward_links_genesis_to_sentinel(
    app,
    remote_app,
    remote_requests_proxy,
    remote_host_netloc,
    miller_2_signing_key,
    time_machine,
    time_stepper,
):
    """From an empty node the first block must link to GENESIS_HASH; a real
    peer genesis does, so the empty-node path adopts it without diverging."""
    start = now() - datetime.timedelta(hours=2)
    time_step = time_stepper(start=start)
    with remote_app.app_context():
        genesis = _mill_chain(miller_2_signing_key, 1, time_step)
        assert genesis.prev_hash == GENESIS_HASH

    app.config['SYNC_BATCH_SIZE'] = 8
    with app.app_context():
        node = _local_node(app)
        assert node.longest_chain is None
        client = _peer_client(remote_host_netloc, miller_2_signing_key)
        assert node.sync_forward(client) == 'caught_up'
        lc = node.longest_chain
        assert lc is not None and lc.last_block is not None
        assert lc.last_block.idx == 0


def test_sync_forward_stops_on_no_progress(
    app,
    remote_app,
    remote_requests_proxy,
    remote_host_netloc,
    miller_2_signing_key,
    time_machine,
    time_stepper,
    monkeypatch,
):
    """No-progress guard: if a non-empty batch fails to advance the committed
    tip (e.g. blocks already in the DB that add_block swallows), sync_forward
    stops with 'diverged' instead of spinning forever. SYNC_BATCH_SIZE=1
    isolates the guard from the per-block linkage check."""
    start = now() - datetime.timedelta(hours=2)
    time_step = time_stepper(start=start)
    with remote_app.app_context():
        _mill_chain(
            miller_2_signing_key, 2, time_step
        )  # peer: genesis + block 1

    app.config['SYNC_BATCH_SIZE'] = 1
    with app.app_context():
        node = _local_node(app)
        client = _peer_client(remote_host_netloc, miller_2_signing_key)
        # Simulate the add_block swallow path: never advance the tip.
        monkeypatch.setattr(node, 'add_block', lambda *a, **k: None)
        result = node.sync_forward(client)  # must terminate, not hang
        assert result == 'diverged'
        # nothing committed (add_block was a no-op), no infinite loop
        assert node.longest_chain is None
