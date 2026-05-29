"""Demonstration tests for the verification pipeline threat-modeled audit.

Each test in this module corresponds to one finding in
docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md
and is marked @pytest.mark.xfail(strict=True). The xfail demonstrates that
the documented gap exists today; strict=True means that if the test starts
unexpectedly passing (because remediation has been applied), CI fails,
forcing the remediation PR to remove the marker.

To verify each xfail genuinely demonstrates a gap (rather than failing for
an unrelated reason), run:

    uv run pytest --runxfail tests/test_verification_audit.py

That runs the xfail tests as if they were unmarked, surfacing the actual
failure mode.

Finding IDs are referenced in each test's docstring and xfail reason string
in the form A<N>.<letter> matching the audit document's per-adversary
sections.
"""

import datetime
from unittest.mock import patch

import pytest

from cancelchain.block import Block
from cancelchain.chain import REWARD
from cancelchain.exceptions import InvalidTransactionError
from cancelchain.miller import Miller
from cancelchain.payload import Inflow, Outflow
from cancelchain.transaction import Transaction
from cancelchain.util import now

# Matches the `easy_mill_chain` session-scoped fixture's patched
# MAX_TARGET — every target in tests is the 64-byte all-F hash so PoW
# is trivially found and the chain.block_target retarget formula always
# returns this value.
TEST_TARGET = 'F' * 64


@pytest.mark.xfail(
    reason=(
        'Audit finding A1.f — severity Low — Node.receive_transaction '
        'does not reject txids that already exist in the persisted chain '
        '(TransactionDAO), so an adversary can replay any mined '
        'transaction back into the pending pool where it lives until '
        'TXN_TIMEOUT (4h). The chain is unaffected — block assembly '
        'filters mined txids out — but the pending pool can be inflated '
        'with stale entries. See '
        'docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md'
    ),
    strict=True,
)
def test_a1_f_mined_txid_replay_into_pending(app, time_machine, wallet):
    """A1.f: replaying a mined transaction back into the pending pool.

    Pre-state: Transaction T has been mined into a block at chain
    height >= 1; T is in TransactionDAO. We then drain the pending pool
    to simulate the cross-node case where T arrived only via block
    gossip and was never in this node's pending pool.
    Attack: POST T's exact JSON to Node.receive_transaction.
    Expected after remediation: receive_transaction raises
    InvalidTransactionError (e.g. via a new DuplicateMinedTransactionError)
    on the lookup-then-pending-add path.
    Observed today: receive_transaction silently accepts T into pending,
    where it sits until TXN_TIMEOUT (4h) expiry.
    """
    with app.app_context():
        now_dt = now()
        when_dt = now_dt - datetime.timedelta(hours=1)
        time_machine.move_to(when_dt)
        m = Miller(milling_wallet=wallet)
        # Mine a coinbase-bearing genesis block so the wallet has balance
        # to spend in the subsequent transaction.
        b0 = m.create_block()
        m.mill_block(b0)
        cb0 = b0.coinbase
        assert cb0 is not None
        cb0_amount = next(iter(cb0.outflows)).amount
        when_dt += datetime.timedelta(minutes=1)
        time_machine.move_to(when_dt)
        # Build a regular spending transaction.
        t = Transaction()
        t.add_inflow(Inflow(outflow_txid=cb0.txid, outflow_idx=0))
        t.add_outflow(Outflow(amount=cb0_amount, address=wallet.address))
        t.set_wallet(wallet)
        t.seal()
        t.sign()
        # Submit to pending and mine it into a block.
        m.receive_transaction(t.txid, t.to_json())
        when_dt += datetime.timedelta(minutes=1)
        time_machine.move_to(when_dt)
        b1 = m.create_block()
        m.mill_block(b1)
        # Confirm the transaction is committed to the chain.
        chain = m.longest_chain
        assert chain is not None
        assert chain.get_transaction(t.txid) is not None
        # Drain the pending pool so any lingering reference to T is gone
        # — simulating a peer node that only learned about T via block
        # gossip and never had it in its own pending pool. (On the same
        # node, pending-by-txid would short-circuit at the
        # `if txn not in self.pending_txns` guard; the gap is observable
        # on any node where T isn't already in pending.)
        for ptxn in list(m.pending_txns):
            m.pending_txns.discard(ptxn)
        assert len(m.pending_txns) == 0
        when_dt += datetime.timedelta(minutes=1)
        time_machine.move_to(when_dt)
        # Attack: replay the mined transaction's JSON to receive_transaction.
        # After remediation, this should raise InvalidTransactionError.
        # Today, it silently accepts the duplicate into pending.
        with pytest.raises(InvalidTransactionError):
            m.receive_transaction(t.txid, t.to_json())


def _hostile_block(
    prev_block: Block,
    wallet,
    idx_offset: int = 1,
) -> Block:
    """Construct a fully-mined Block extending `prev_block` without
    persisting anything to the DB.

    The block is linked to `prev_block` by hash + idx, sealed with a
    coinbase paying `wallet`, given a merkle root, timestamped at
    `now()` (under the active time_machine), and milled to satisfy the
    `TEST_TARGET` (all-F) proof-of-work requirement. `idx_offset` lets
    callers manufacture an idx-skip (e.g., `idx_offset=99`) to force
    `Chain.validate_block` to raise `InvalidBlockIndexError`.
    """
    b = Block()
    assert prev_block.idx is not None
    assert prev_block.block_hash is not None
    b.link(prev_block.idx + idx_offset, prev_block.block_hash, TEST_TARGET)
    b.seal(wallet, REWARD)
    b.mill()
    return b


@pytest.mark.xfail(
    reason=(
        'Audit finding A2.e — severity Medium — Node.fill_chain applies '
        'staged blocks in a non-atomic loop. When the last block of a '
        'staged chain fails Chain.validate_block, earlier blocks that '
        'passed validation remain persisted in BlockDAO and advance '
        "ChainDAO's tip — a hostile peer can force partial adoption of "
        'a fork prefix by appending a cheap-to-construct invalid tip. '
        'See '
        'docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md'
    ),
    strict=True,
)
def test_a2_e_partial_chain_adoption_via_invalid_tip(
    app, time_machine, wallet
) -> None:
    """A2.e: hostile peer's invalid tip leaves earlier blocks persisted.

    Pre-state: Local chain has only a mined genesis block (height 1).
    Attack: A hostile peer offers a 4-block chain whose tip block has an
    intentionally-skipped idx (idx_offset=99), so Chain.validate_block
    raises InvalidBlockIndexError on the tip. The three intermediate
    blocks are legitimately constructed (valid PoW, valid coinbase,
    correct target, idx-contiguous). Node.fill_chain is invoked with the
    invalid tip; request_block is patched to serve the intermediate
    blocks on backward walk.
    Expected after remediation: fill_chain returns False AND no
    intermediate block enters BlockDAO (the apply loop rolls back any
    successful per-block commits when a later block fails validation,
    e.g., via db.session.begin_nested or a validate-then-persist split).
    Observed today: the three intermediate blocks are committed to
    BlockDAO and ChainDAO advances to the tip of the partial fork,
    even though fill_chain returns False overall.
    """
    with app.app_context():
        # Step 1: persist a local genesis block so our node has a known
        # parent. This is the only block in BlockDAO at the start of the
        # attack.
        m = Miller(milling_wallet=wallet)
        local_genesis = m.create_block()
        m.mill_block(local_genesis)
        assert local_genesis.block_hash is not None
        local_genesis_hash = local_genesis.block_hash
        original_chain = m.longest_chain
        assert original_chain is not None
        original_length = original_chain.length
        assert original_length == 1

        # Step 2: construct the hostile chain off-line. A, B, C are
        # legitimately valid extensions (idx 1, 2, 3) chaining off our
        # local genesis. D_prime jumps to idx 102 to force
        # InvalidBlockIndexError at apply time.
        a_block = _hostile_block(local_genesis, wallet)
        b_block = _hostile_block(a_block, wallet)
        c_block = _hostile_block(b_block, wallet)
        d_prime = _hostile_block(c_block, wallet, idx_offset=99)
        assert a_block.block_hash is not None
        assert b_block.block_hash is not None
        assert c_block.block_hash is not None
        assert d_prime.block_hash is not None
        # Confirm none of the hostile blocks are persisted yet.
        assert Block.from_db(a_block.block_hash) is None
        assert Block.from_db(b_block.block_hash) is None
        assert Block.from_db(c_block.block_hash) is None

        # Step 3: patch request_block to serve hostile ancestors when
        # fill_chain walks backwards from d_prime.
        hostile_by_hash = {
            a_block.block_hash: a_block,
            b_block.block_hash: b_block,
            c_block.block_hash: c_block,
            # local_genesis is already in BlockDAO; fill_chain's walk
            # terminates there before requesting it.
        }

        def fake_request_block(block_hash):
            return hostile_by_hash.get(block_hash)

        # Step 4: invoke fill_chain with the invalid tip.
        with patch.object(m, 'request_block', side_effect=fake_request_block):
            result = m.fill_chain(d_prime)

        # Step 5: assert that the partial chain was NOT adopted.
        # After remediation: result is False AND no hostile block was
        # persisted AND longest_chain is unchanged from the original.
        assert result is False
        assert Block.from_db(a_block.block_hash) is None, (
            'A2.e gap demonstrated: hostile block A was persisted to '
            'BlockDAO even though the chain tip D_prime failed validation.'
        )
        assert Block.from_db(b_block.block_hash) is None
        assert Block.from_db(c_block.block_hash) is None
        assert Block.from_db(d_prime.block_hash) is None
        post_chain = m.longest_chain
        assert post_chain is not None
        assert post_chain.length == original_length
        assert post_chain.block_hash == local_genesis_hash
