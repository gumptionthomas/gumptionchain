"""Demonstration tests for the 2026-06-01 P2P/networking threat-model audit.

Each test below demonstrates one audit finding and is marked
``@pytest.mark.xfail(strict=True)`` -- strict mode means the test MUST fail
today (the gap is real) and forces the marker's removal when the finding is
remediated (the xfail would otherwise "unexpectedly pass" and error the
suite). See docs/superpowers/audits/2026-06-01-network-p2p-audit.md.

Availability findings use a *bounded-observation* convention: drive the
uncapped behavior only up to a small, safe bound and assert the missing cap
is observable. No test exhausts real memory, disk, or wall-clock.
"""

import contextlib
import datetime
import threading
from unittest.mock import patch

import httpx
import pytest

from cancelchain.api_client import ApiClient
from cancelchain.block import Block
from cancelchain.chain import REWARD
from cancelchain.exceptions import MempoolFullError
from cancelchain.miller import Miller
from cancelchain.payload import Inflow, Outflow, encode_subject
from cancelchain.transaction import Transaction
from cancelchain.util import now

# Matches the `easy_mill_chain` session-scoped fixture's patched
# MAX_TARGET — every target in tests is the 64-character all-F hex string
# (the max 256-bit target) so PoW is trivially found.
TEST_TARGET = 'F' * 64

# Per-finding tests (and any further imports: pytest, Block, Node, ...) are
# appended below this scaffold. Shared fixtures (app, *_wallet,
# requests_proxy, remote_requests_proxy, mill_block, host, time_stepper) come
# from tests/conftest.py.


def _hostile_block(prev_block: Block, wallet, idx_offset: int = 1) -> Block:
    """Construct a fully-mined Block extending `prev_block` without
    persisting anything to the DB.

    Mirrors tests/test_verification_audit.py: linked to `prev_block` by
    hash + idx, sealed with a coinbase paying `wallet`, given a merkle
    root, timestamped at now() (under the active time_machine), and milled
    to satisfy the TEST_TARGET (all-F) proof-of-work requirement.
    """
    b = Block()
    assert prev_block.idx is not None
    assert prev_block.block_hash is not None
    b.link(prev_block.idx + idx_offset, prev_block.block_hash, TEST_TARGET)
    b.seal(wallet, REWARD)
    b.mill()
    return b


def test_n1_fill_chain_has_no_depth_cap(app, time_machine, wallet) -> None:
    """N1 (depth-cap half): fill_chain's ancestor walk is now bounded by
    app.config['MAX_CHAIN_FILL_DEPTH']. A hostile peer that drives the walk
    past max_depth causes fill_chain to abort (returning False and cleaning up
    the ChainFill staging rows) rather than requesting an unbounded number of
    ancestors.

    Regression test: with MAX_CHAIN_FILL_DEPTH=3, a fake peer returning
    ever-extending blocks must result in at most 3 request_block calls before
    abort, so call_count <= 3.
    """
    with app.app_context():
        now_dt = now()
        time_machine.move_to(now_dt - datetime.timedelta(hours=1))
        m = Miller(milling_wallet=wallet)
        g = m.create_block()
        m.mill_block(g)

        # Build a hostile tip whose ancestors are NOT in the DB, so the
        # backward walk must request_block its way down.
        tip = _hostile_block(g, wallet)
        tip2 = _hostile_block(tip, wallet)
        assert tip2.block_hash is not None
        assert Block.from_db(tip2.block_hash) is None

        # Remediation contract: cap the ancestor walk at this depth.
        app.config['MAX_CHAIN_FILL_DEPTH'] = 3

        call_count = [0]
        # SAFETY: cap our fake peer so today's uncapped walk terminates
        # rather than hanging. Each call ignores the requested hash and
        # returns a FRESH extending hostile block whose prev_hash is a
        # never-stored non-genesis hash, so the walk keeps going.
        safety = 8
        current = [tip2]

        def counting_fake(block_hash):
            call_count[0] += 1
            if call_count[0] > safety:
                return None
            nxt = _hostile_block(current[0], wallet)
            current[0] = nxt
            return nxt

        with patch.object(m, 'request_block', side_effect=counting_fake):
            m.fill_chain(tip2)

        # TODAY: no cap -> walk runs to the SAFETY bound -> call_count == 9
        # (8 served + 1 terminating None) -> 9 <= 3 is FALSE -> xfail.
        # AFTER FIX: walk stops at the cap ->
        # call_count <= 3 -> passes -> remove the marker.
        assert call_count[0] <= 3


def test_n2_mempool_has_no_admission_cap(app, time_machine, wallet) -> None:
    """N2 (regression): receive_transaction now enforces a configurable
    MAX_PENDING_TXNS cap. Submissions past the cap raise MempoolFullError;
    the pool never exceeds the configured limit.

    Remediation contract: a configurable cap app.config['MAX_PENDING_TXNS'].
    """
    with app.app_context():
        now_dt = now()
        time_machine.move_to(now_dt - datetime.timedelta(hours=1))
        m = Miller(milling_wallet=wallet)

        # Remediation contract: cap the mempool BEFORE submitting.
        app.config['MAX_PENDING_TXNS'] = 3

        # Submit 6 DISTINCT structurally-valid signed txns. Each varies the
        # subject so its txid differs; validate() is shape+sig+txid only
        # (NO balance check), so these unfunded opposition txns admit — that
        # IS the finding.
        for i in range(6):
            t = Transaction()
            # A dummy inflow reference satisfies the shape check (regular
            # txns require >= 1 inflow); validate() is shape+sig+txid only
            # (no chain lookup / balance check), so the unfunded txn admits.
            t.add_inflow(Inflow(outflow_txid='0' * 64, outflow_idx=0))
            t.add_outflow(
                Outflow(amount=1, subject=encode_subject(f'subj-{i}'))
            )
            t.set_wallet(wallet)
            t.seal()
            t.sign()
            with contextlib.suppress(MempoolFullError):
                m.receive_transaction(t.txid, t.to_json())

        assert len(m.pending_txns) <= 3


@pytest.mark.xfail(
    strict=True,
    reason=(
        'AUDIT N3: an already-pending txn is re-gossiped on every receipt '
        "(send_transaction called outside the 'newly added' guard), enabling "
        '1->N fan-out amplification. Remove this marker when '
        'receive_transaction only re-gossips a txn it actually newly added '
        "(mirroring the block path's dedup-before-gossip)."
    ),
)
def test_n3_pending_txn_regossiped_on_every_receipt(
    app, time_machine, wallet
) -> None:
    """N3: an ALREADY-PENDING txn is re-gossiped on every receipt because
    send_transaction is called unconditionally outside the
    `if txn not in self.pending_txns` guard (node.py:95-105), unlike the
    block path which dedups before gossiping.
    """
    with app.app_context():
        now_dt = now()
        time_machine.move_to(now_dt - datetime.timedelta(hours=1))
        m = Miller(milling_wallet=wallet)

        # One valid signed txn (dummy inflow satisfies the shape check).
        t = Transaction()
        t.add_inflow(Inflow(outflow_txid='0' * 64, outflow_idx=0))
        t.add_outflow(Outflow(amount=1, subject=encode_subject('subj-n3')))
        t.set_wallet(wallet)
        t.seal()
        t.sign()

        # First receipt: admits to pending (and gossips — expected).
        m.receive_transaction(t.txid, t.to_json())
        assert t in m.pending_txns

        # Wire a spy peer that records every gossip call, installed AFTER the
        # first receipt so `calls` reflects only the second-receipt fan-out.
        calls: list[str] = []
        peer = 'http://peer.host:8000'

        class SpyClient:
            host = peer

            def post_transaction(self, txn, visited_hosts=None):
                calls.append(txn.txid)

        m.peers = [peer]
        m.clients = {peer: SpyClient()}

        # SECOND receipt of the SAME already-pending txn. With m.host=None,
        # visited_hosts starts empty so send_transaction would contact the
        # peer.
        m.receive_transaction(t.txid, t.to_json())

        # TODAY: send_transaction fires unconditionally -> calls == [t.txid]
        # -> calls == [] FALSE -> xfail. AFTER FIX (gossip gated on
        # newly-added, mirroring the block path) -> no gossip on the 2nd
        # receipt -> calls == [] -> passes -> remove marker.
        assert calls == []


@pytest.mark.xfail(
    strict=True,
    reason=(
        'AUDIT N4: the Celery broker publish (post_process.delay) runs '
        'synchronously on the web-request thread via the http_post signal, '
        'coupling POST latency to broker liveness. Remove this marker when '
        'the publish is moved off the request thread.'
    ),
)
def test_n4_async_publish_blocks_request_thread(
    app, host, host_netloc, requests_proxy, wallet
) -> None:
    """N4: the Celery broker publish runs synchronously on the web-request
    thread — handle_http_post -> post_process.delay() is fired in-thread via
    the http_post signal during the POST, so it executes on the request
    handler's thread rather than off it.
    """
    with app.app_context():
        # Enable the async post-processing path. The CC_ prefix is stripped
        # in app.config, so the gate key is API_ASYNC_PROCESSING. A valid
        # in-memory broker URL keeps .delay() from erroring on config, and
        # NODE_HOST must embed the local wallet address (http://<addr>@host)
        # so queue_post_process doesn't warn-and-skip before the signal.
        app.config['API_ASYNC_PROCESSING'] = True
        app.config['CELERY_BROKER_URL'] = 'memory://'
        app.config['NODE_HOST'] = f'http://{wallet.address}@{host_netloc}'

        main_tid = threading.get_ident()
        delay_tid: list[int] = []

        def fake_delay(*args, **kwargs):
            delay_tid.append(threading.get_ident())

        # Mine a block as the (ADMIN-privileged) wallet and POST it through
        # the real ApiClient/requests_proxy. Use create_block() + b.mill()
        # (NOT mill_block, which calls receive_block and would persist the
        # block locally) so the POSTed block is NEW to the server's DB —
        # otherwise receive_block short-circuits (block already present) and
        # the async queue path is never reached.
        m = Miller(milling_wallet=wallet)
        b = m.create_block()
        b.mill()

        # Patch where handle_http_post looks it up: post_process is imported
        # into cancelchain.api and called as post_process.delay(...).
        with patch(
            'cancelchain.api.post_process.delay', side_effect=fake_delay
        ):
            response = ApiClient(host, wallet).post_block(b)

        # The async path returns 202 ACCEPTED after queueing.
        assert response.status_code == httpx.codes.ACCEPTED
        # The signal fired synchronously, so delay recorded the publish
        # thread id.
        assert delay_tid, 'post_process.delay was never called'

        # TODAY: the signal fires synchronously in the request handler, which
        # (under httpx WSGITransport) runs in the test's own thread ->
        # delay_tid[0] == main_tid -> != FALSE -> xfail. AFTER FIX (publish
        # moved off the request thread) -> different thread -> passes ->
        # remove marker.
        assert delay_tid[0] != main_tid


def test_n1_request_block_rejects_hash_mismatch(app, time_machine, wallet):
    """N1 (hash-check half): request_block must reject a peer response whose
    returned block hash does not equal the requested hash, instead of
    returning the mismatched block. This is the primary fix -- it stops a
    hostile peer from steering fill_chain's walk with fresh fakes.
    """
    with app.app_context():
        time_machine.move_to(now() - datetime.timedelta(hours=1))
        m = Miller(milling_wallet=wallet)
        g = m.create_block()
        m.mill_block(g)
        # A valid block whose hash is known; the peer will serve it in
        # response to a request for a DIFFERENT hash.
        served = _hostile_block(g, wallet)
        assert served.block_hash is not None

        class _Resp:
            status_code = 200
            text = served.to_json()

        class _PeerClient:
            def get_block(self, block_hash=None, raise_for_status=False):
                return _Resp()

        peer = 'http://peer.host:8000'
        m.peers = [peer]
        m.clients = {peer: _PeerClient()}

        requested = 'f' * 64
        assert requested != served.block_hash
        # Today: request_block returns `served` (no hash check) -> not None.
        # After the fix: the hash mismatch is rejected -> None.
        assert m.request_block(requested) is None


def test_n1_request_block_rejects_forged_block_hash_field(
    app, time_machine, wallet
):
    """N1 (hash-check half, second-preimage): a peer cannot bypass the check
    by forging the self-reported ``block_hash`` JSON field to equal the
    requested hash over junk/unrelated content. ``block_hash`` is a stored,
    peer-controlled field; the fix compares the COMPUTED header hash
    (``get_header_hash()``), which binds the block's actual content, so a
    forged field with mismatched content is rejected.
    """
    with app.app_context():
        time_machine.move_to(now() - datetime.timedelta(hours=1))
        m = Miller(milling_wallet=wallet)
        g = m.create_block()
        m.mill_block(g)
        # A real block; we then LIE about its block_hash field, claiming the
        # requested hash while the content still hashes to the real value.
        forged = _hostile_block(g, wallet)
        real_hash = forged.block_hash
        assert real_hash is not None
        requested = 'a' * 64
        assert requested != real_hash
        forged.block_hash = requested  # forged self-reported field
        # The computed header hash still reflects the real content.
        assert forged.get_header_hash() == real_hash

        class _Resp:
            status_code = 200
            text = forged.to_json()

        class _PeerClient:
            def get_block(self, block_hash=None, raise_for_status=False):
                return _Resp()

        peer = 'http://peer.host:8000'
        m.peers = [peer]
        m.clients = {peer: _PeerClient()}

        # The claimed field equals `requested`, but the computed hash does
        # not -> rejected (without the computed-hash check this would have
        # let a hostile peer steer fill_chain with no PoW).
        assert m.request_block(requested) is None


def test_n2_full_mempool_returns_503(
    app, host, time_machine, requests_proxy, wallet
):
    """N2 (view layer): a valid txn submitted to a full mempool returns a
    retryable 503, not a 400 -- the txn is well-formed and authorized; the
    node is temporarily at capacity.
    """
    with app.app_context():
        time_machine.move_to(now() - datetime.timedelta(hours=1))
        app.config['MAX_PENDING_TXNS'] = 1
        client = ApiClient(host, wallet)

        def make_txn(i):
            t = Transaction()
            t.add_inflow(Inflow(outflow_txid='0' * 64, outflow_idx=0))
            t.add_outflow(
                Outflow(amount=1, subject=encode_subject(f's503-{i}'))
            )
            t.set_wallet(wallet)
            t.seal()
            t.sign()
            return t

        # Cap = 1: the first valid txn is admitted.
        r1 = client.post_transaction(make_txn(0), raise_for_status=False)
        assert r1.status_code in (
            httpx.codes.OK,
            httpx.codes.CREATED,
            httpx.codes.ACCEPTED,
        )
        # The pool is now full -> the next valid txn is rejected with 503.
        r2 = client.post_transaction(make_txn(1), raise_for_status=False)
        assert r2.status_code == httpx.codes.SERVICE_UNAVAILABLE
