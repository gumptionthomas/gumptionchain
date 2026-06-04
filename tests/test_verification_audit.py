"""Demonstration and regression tests for the verification pipeline audit.

Each finding in
docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md
has a corresponding test here, in one of two states:

- **Open findings** carry `@pytest.mark.xfail(strict=True)`: the xfail
  demonstrates the gap still exists; strict=True means that if the test
  starts unexpectedly passing (because remediation landed), CI fails,
  forcing the remediation PR to remove the marker.
- **Remediated findings** have had the xfail decorator removed and now
  pass as plain regression tests guarding the fix (e.g. A2.e, A4.c, A7.b).

The module may also hold non-regression / invariant tests that assert a
fix's intended behavior — e.g. test_a4_c_coinbase_block_binding, which
checks coinbases are bound to their block via prev_hash.

To verify a still-xfailed test genuinely demonstrates a gap (rather than
failing for an unrelated reason), run:

    uv run pytest --runxfail tests/test_verification_audit.py

That runs the xfail tests as if unmarked, surfacing the actual failure
mode; the already-remediated tests pass under it too.

Finding IDs are referenced in each test's docstring (and, for still-open
findings, the xfail reason string) in the form A<N>.<letter> matching the
audit document's per-adversary sections.
"""

import datetime
from unittest.mock import patch

import pytest

from gumptionchain.block import TXN_TIMEOUT, Block
from gumptionchain.chain import GENESIS_HASH, REWARD
from gumptionchain.database import db
from gumptionchain.exceptions import (
    DuplicateGenesisError,
    DuplicateMinedTransactionError,
    InvalidCoinbaseError,
    InvalidTransactionError,
    MismatchedCoinbaseError,
    MissingBlockError,
)
from gumptionchain.miller import Miller
from gumptionchain.models import ChainDAO
from gumptionchain.payload import Inflow, Outflow, encode_subject
from gumptionchain.transaction import Transaction
from gumptionchain.util import dt_2_iso, now, now_iso

# Matches the `easy_mill_chain` session-scoped fixture's patched
# MAX_TARGET — every target in tests is the 64-character all-F hex
# string (the max 256-bit target) so PoW is trivially found and the
# chain.block_target retarget formula always returns this value.
TEST_TARGET = 'F' * 64


def test_a1_f_mined_txid_replay_into_pending(app, time_machine, wallet):
    """A1.f: a mined transaction replayed into the pending pool is
    rejected (regression test).

    Pre-state: Transaction T has been mined into a block at chain
    height >= 1; T is in TransactionDAO. We then drain the pending pool
    to simulate the cross-node case where T arrived only via block
    gossip and was never in this node's pending pool.
    Attack: POST T's exact JSON to Node.receive_transaction.
    Invariant under test (post-remediation): receive_transaction raises
    DuplicateMinedTransactionError (a TransactionDAO.get hit) before the
    pending-add, so T never re-enters pending.
    Pre-remediation, receive_transaction silently accepted T into pending,
    where it sat until TXN_TIMEOUT (4h) expiry.
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
        # Post-remediation this raises DuplicateMinedTransactionError
        # (pre-remediation it silently accepted the duplicate into pending).
        with pytest.raises(DuplicateMinedTransactionError):
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


def test_a2_e_partial_chain_adoption_via_invalid_tip(
    app, time_machine, wallet
) -> None:
    """A2.e: hostile peer's invalid tip no longer leaves blocks persisted.

    Pre-state: Local chain has only a mined genesis block (height 1).
    Attack: A hostile peer offers a 4-block chain whose tip block has an
    intentionally-skipped idx (idx_offset=99), so Chain.validate_block
    raises InvalidBlockIndexError on the tip. The three intermediate
    blocks are legitimately constructed (valid PoW, valid coinbase,
    correct target, idx-contiguous). Node.fill_chain is invoked with the
    invalid tip; request_block is patched to serve the intermediate
    blocks on backward walk.
    Behavior (post-remediation, verified by this test): fill_chain
    returns False AND no intermediate block enters BlockDAO. The fix
    threads a keyword-only `commit: bool = True` parameter through
    BlockDAO.commit / Block.to_db / Chain.to_db / Chain.add_block /
    Node.add_block / Node.create_chain so fill_chain can call
    self.add_block(block, commit=False) per iteration (flush instead
    of commit), then issue a single db.session.commit() after the
    loop. On exception (e.g., the InvalidBlockIndexError this test
    triggers on the tip), db.session.rollback() undoes every flushed
    block in the batch. ChainDAO's tip is unchanged from before the
    attack.
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


def test_a4_c_ii_coinbase_replay_inflates_balance(
    app, time_machine, wallet
) -> None:
    """A4.c.ii: replaying another miller's coinbase in a fresh block.

    Pre-state: Local chain has a single mined block B_orig whose coinbase
    T_cb is bound (via prev_hash) to B_orig's parent and pays the milling
    wallet REWARD.
    Attack: The adversary (MILLER) builds B_adv extending B_orig with
    txns=[T_cb] only, reusing T_cb verbatim as B_adv's coinbase, mills
    PoW, and invokes Node.receive_block.
    Behavior (post-remediation, verified by this test): T_cb's bound
    prev_hash is B_orig's parent, but B_adv.prev_hash is B_orig's hash.
    Chain.validate_block_coinbase raises MismatchedCoinbaseError (a
    subclass of InvalidCoinbaseError) on the binding mismatch;
    receive_block propagates the failure and B_adv is not persisted. The
    coinbase is intrinsically block-bound, so it cannot be replayed onto
    any other block-position.
    """
    with app.app_context():
        # Pre-state: mine B_orig with our wallet as the milling wallet, so
        # its coinbase T_cb pays REWARD to `wallet.address`.
        now_dt = now()
        when_dt = now_dt - datetime.timedelta(hours=1)
        time_machine.move_to(when_dt)
        m = Miller(milling_wallet=wallet)
        b_orig = m.create_block()
        m.mill_block(b_orig)
        t_cb = b_orig.coinbase
        assert t_cb is not None
        assert t_cb.address == wallet.address
        cb_outflow = t_cb.get_outflow(0)
        assert cb_outflow is not None
        assert cb_outflow.amount == REWARD
        chain = m.longest_chain
        assert chain is not None
        # Sanity: pre-attack balance is exactly REWARD (one coinbase).
        assert chain.balance(wallet.address) == REWARD

        # Step forward a beat so B_adv's timestamp won't trip
        # OutOfOrderBlockError (block.timestamp >= prev_block.timestamp).
        when_dt += datetime.timedelta(minutes=1)
        time_machine.move_to(when_dt)

        # Attack: simulate "adversary saw T_cb on the wire" by
        # round-tripping it through JSON to get a fresh Transaction
        # instance — same txid, same signature, same data_csv.
        t_cb_replayed = Transaction.from_json(t_cb.to_json())
        assert t_cb_replayed.txid == t_cb.txid

        # Hand-build B_adv extending the chain's tip with the replayed
        # coinbase as its only (last → coinbase-by-position) transaction.
        # We mirror Block.seal's contract manually so the coinbase slot
        # is occupied by T_cb instead of a fresh M_adv-paying coinbase.
        b_adv = Block()
        chain.link_block(b_adv)
        # add_txn(is_coinbase=True) calls validate_coinbase (schema +
        # signature + txid) on the replayed coinbase — all pass because
        # T_cb's bytes are unchanged from its original signing.
        b_adv.add_txn(t_cb_replayed, is_coinbase=True)
        b_adv.merkle_root = b_adv.get_merkle_root()
        b_adv.timestamp = now_iso()
        b_adv.mill()
        assert b_adv.block_hash is not None
        # Sanity: B_adv was honestly milled (block_hash satisfies target).
        assert b_adv.is_proved

        # After remediation: validate_block_coinbase rejects the
        # duplicate-coinbase-txid B_adv with InvalidCoinbaseError (or a
        # new DuplicateCoinbaseError subclass thereof). Today the chain
        # accepts B_adv and the duplicate m2m association inflates the
        # wallet balance to 2 * REWARD.
        with pytest.raises(InvalidCoinbaseError):
            m.receive_block(b_adv.to_json())


def test_a7_b_alternate_genesis_fragments_chain_registry(
    app, time_machine, wallet, miller_2_wallet
) -> None:
    """A7.b: an alternate genesis block is rejected (regression test).

    Pre-state: Empty BlockDAO. The first mined block becomes the canonical
    genesis (block_hash=G1, paying `wallet`).
    Attack: Mine a second block with prev_hash=GENESIS_HASH, idx=0, and a
    coinbase paying a different miller wallet (miller_2_wallet) at a
    different timestamp, yielding a block_hash G2 != G1.
    Post-remediation (this test): receive_block rejects the second genesis
    with DuplicateGenesisError (an InvalidBlockError); the ChainDAO registry
    stays at one row.
    Pre-remediation (the gap this guards): receive_block accepted G2,
    Node.add_block's create_chain fallback built a fresh Chain instance, and
    a second ChainDAO row was committed pointing at G2 alongside the
    canonical one pointing at G1.
    """
    with app.app_context():

        def _chain_count() -> int:
            return len(db.session.execute(db.select(ChainDAO)).scalars().all())

        now_dt = now()
        when_dt = now_dt - datetime.timedelta(hours=1)
        time_machine.move_to(when_dt)
        # Mine the canonical genesis with `wallet` as miller.
        m1 = Miller(milling_wallet=wallet)
        g1 = m1.create_block()
        m1.mill_block(g1)
        assert g1.block_hash is not None
        assert g1.idx == 0
        # Sanity: exactly one ChainDAO row exists for the canonical chain.
        initial_chain_count = _chain_count()
        assert initial_chain_count == 1

        # Step forward in time so the alternate genesis has a different
        # timestamp (and therefore a different block_hash even if the
        # coinbase wallet were the same).
        when_dt += datetime.timedelta(minutes=5)
        time_machine.move_to(when_dt)

        # Hand-build an alternate genesis with miller_2_wallet's coinbase.
        # Cannot use Miller.create_block — it would link off the
        # canonical longest chain (g1) instead of GENESIS_HASH.
        g2 = Block()
        # Mirror Chain.link_block for the empty-chain case: idx=0,
        # prev_hash=GENESIS_HASH, target=MAX_TARGET (the easy_mill_chain
        # fixture's patched value, also returned by Chain.block_target at
        # index=0).
        g2.link(0, GENESIS_HASH, TEST_TARGET)
        g2.seal(miller_2_wallet, REWARD)
        g2.mill()
        assert g2.block_hash is not None
        assert g2.block_hash != g1.block_hash
        assert g2.idx == 0
        assert g2.prev_hash == GENESIS_HASH
        # Sanity: g2 was honestly milled.
        assert g2.is_proved

        # Attack: submit the alternate genesis. After remediation, this
        # should raise DuplicateGenesisError. Today it silently accepts.
        with pytest.raises(DuplicateGenesisError):
            m1.receive_block(g2.to_json())
        # Even if the call had not raised, post-remediation the chain
        # registry should still hold only the canonical chain. The
        # following assertion is the actual observable gap demonstrator:
        # today the count goes to 2 because Node.add_block created a
        # second ChainDAO row.
        assert _chain_count() == initial_chain_count


def test_a7_j_disjoint_genesis_reorg_rejected(
    app, time_machine, wallet, miller_2_wallet
) -> None:
    """A7.j: a longer fork rooted at an alternate genesis cannot displace
    the canonical chain — its root genesis is rejected at admission.

    A7.j (disjoint-ancestor reorg) has no standalone finding: the
    catastrophic-rebuild branch is correct PoW longest-chain behavior. The
    gap is the alternate-genesis admission (A7.b). This test proves closing
    A7.b closes A7.j: even a LONGER fork (g2 + child b2, length 2 vs the
    canonical length 1) cannot win. Its root genesis g2 is rejected
    (DuplicateGenesisError), and its child b2 is then unrootable — submitting
    b2 raises MissingBlockError because its parent g2 was never admitted. The
    reorg never completes.
    """
    with app.app_context():

        def _chain_count() -> int:
            return len(db.session.execute(db.select(ChainDAO)).scalars().all())

        now_dt = now()
        when_dt = now_dt - datetime.timedelta(hours=1)
        time_machine.move_to(when_dt)
        # Canonical genesis g1 paying `wallet`.
        m1 = Miller(milling_wallet=wallet)
        g1 = m1.create_block()
        m1.mill_block(g1)
        assert g1.block_hash is not None
        assert g1.idx == 0
        canonical_chain = ChainDAO.longest()
        assert canonical_chain is not None
        canonical_tip = canonical_chain.block.block_hash
        assert canonical_tip == g1.block_hash
        assert _chain_count() == 1

        # Build a LONGER disjoint fork rooted at an alternate genesis.
        when_dt += datetime.timedelta(minutes=5)
        time_machine.move_to(when_dt)
        g2 = Block()
        g2.link(0, GENESIS_HASH, TEST_TARGET)
        g2.seal(miller_2_wallet, REWARD)
        g2.mill()
        assert g2.block_hash is not None
        assert g2.block_hash != g1.block_hash
        assert g2.idx == 0
        # Child b2 chains off g2 — fork length 2 > canonical length 1.
        when_dt += datetime.timedelta(minutes=5)
        time_machine.move_to(when_dt)
        b2 = Block()
        b2.link(1, g2.block_hash, TEST_TARGET)
        b2.seal(miller_2_wallet, REWARD)
        b2.mill()
        assert b2.idx == 1
        assert b2.prev_hash == g2.block_hash

        # The fork's root g2 is rejected at admission.
        with pytest.raises(DuplicateGenesisError):
            m1.receive_block(g2.to_json())
        # b2 is therefore unrootable: its parent g2 was never persisted, so
        # receive_block rejects it locally with MissingBlockError (no peer
        # fill is attempted). The longer fork can never be assembled.
        with pytest.raises(MissingBlockError):
            m1.receive_block(b2.to_json())

        # Canonical chain unchanged; registry still single.
        post_chain = ChainDAO.longest()
        assert post_chain is not None
        assert post_chain.block.block_hash == canonical_tip
        assert _chain_count() == 1


def test_a7_e_txn_timeout_boundary_inconsistency(
    app, time_machine, wallet
) -> None:
    """A7.e: the boundary value is treated consistently across call sites
    (regression test).

    Pre-state: Local chain has a mined genesis paying `wallet` REWARD; a
    valid spending txn T exists in pending with timestamp exactly
    now - TXN_TIMEOUT.
    Invariant under test (post-remediation): all sites share the open-
    boundary `txn_is_expired` rule — a txn exactly TXN_TIMEOUT old is
    "alive". So `Block.validate_transaction(T)` does NOT raise
    ExpiredTransactionError at the boundary, AND
    `Node.discard_expired_pending_txns` does NOT discard T at the boundary.
    Pre-remediation, the block validator used strict `<` (alive) while
    discard used `<=` (evicted), disagreeing at the boundary instant.
    """
    with app.app_context():
        # Mine a genesis paying `wallet` so we have a spendable outflow.
        m = Miller(milling_wallet=wallet)
        now_dt = now()
        # Set the clock to a known anchor so TXN_TIMEOUT subtraction is
        # exact.
        when_dt = now_dt
        # Mine the genesis well before the boundary instant so its
        # timestamp is not contested by the txn we'll craft.
        time_machine.move_to(when_dt - datetime.timedelta(hours=5))
        g = m.create_block()
        m.mill_block(g)
        cb = g.coinbase
        assert cb is not None
        cb_amount = next(iter(cb.outflows)).amount

        # Move to the boundary moment. Build txn T whose timestamp is
        # exactly `when_dt - TXN_TIMEOUT` and call discard at `when_dt`.
        time_machine.move_to(when_dt)
        boundary_dt = when_dt - TXN_TIMEOUT
        t = Transaction(timestamp=dt_2_iso(boundary_dt))
        t.add_inflow(Inflow(outflow_txid=cb.txid, outflow_idx=0))
        t.add_outflow(Outflow(amount=cb_amount, address=wallet.address))
        t.set_wallet(wallet)
        t.seal()
        t.sign()
        # Inject T directly into pending (sidestepping send_transaction's
        # peer fan-out). `add` is the canonical PendingTxnSet API.
        m.pending_txns.add(t)
        assert len(m.pending_txns) == 1
        assert t in m.pending_txns

        # Cross-check that Block.validate_transaction at the boundary
        # accepts T (block timestamp = `when_dt`, txn timestamp =
        # `when_dt - TXN_TIMEOUT`; the open-boundary `txn_is_expired`
        # check treats equality as non-expired).
        boundary_block = Block(timestamp=dt_2_iso(when_dt))
        # Should NOT raise ExpiredTransactionError; the block validator
        # treats this txn as alive at the boundary.
        boundary_block.validate_transaction(t)

        # Pre-remediation, Node.discard_expired_pending_txns evicted T
        # because it used `<= now() - TXN_TIMEOUT`. After remediation
        # (open-boundary semantics applied consistently via txn_is_expired),
        # the eviction check aligns with Block.validate_transaction's
        # strict `<`, leaving T in pending.
        m.discard_expired_pending_txns()
        assert len(m.pending_txns) == 1, (
            'A7.e regression: T was discarded by '
            'discard_expired_pending_txns at the boundary even though '
            'Block.validate_transaction treats T as non-expired at the '
            'same instant — the open-boundary txn_is_expired rule should '
            'keep them consistent.'
        )


def test_a7_h_non_printable_subject_accepted(app, time_machine, wallet) -> None:
    """A7.h: subject with control characters is accepted into pending.

    Pre-state: Local chain has a mined genesis paying `wallet` REWARD.
    Attack: Build a transaction with an outflow whose subject decodes
    to a string containing a C1 control character (ESC, 0x1b) followed
    by an ANSI color escape sequence — the kind of payload that would
    re-render terminal output. Submit via Node.receive_transaction.
    Expected after remediation: receive_transaction raises
    InvalidTransactionError because the subject contains a codepoint in
    Unicode general category Cc (control) or Cf (format).
    Observed today: the subject passes validate_subject (length and
    round-trip only) and the transaction lands in pending.
    """
    with app.app_context():
        m = Miller(milling_wallet=wallet)
        now_dt = now()
        when_dt = now_dt - datetime.timedelta(hours=1)
        time_machine.move_to(when_dt)
        g = m.create_block()
        m.mill_block(g)
        cb = g.coinbase
        assert cb is not None
        cb_amount = next(iter(cb.outflows)).amount
        when_dt += datetime.timedelta(minutes=1)
        time_machine.move_to(when_dt)

        # Build a subject that decodes to ESC followed by an ANSI red
        # color sequence — 7 characters total, well inside [1, 79].
        raw_subject = '\x1b[31mRED'
        assert 1 <= len(raw_subject) <= 79
        malicious_subject = encode_subject(raw_subject)

        # Build a spending transaction whose outflow opposes the
        # malicious subject. The remainder goes back to `wallet`.
        t = Transaction()
        t.add_inflow(Inflow(outflow_txid=cb.txid, outflow_idx=0))
        oppose_amount = 10
        t.add_outflow(
            Outflow(amount=oppose_amount, opposition=malicious_subject)
        )
        t.add_outflow(
            Outflow(amount=cb_amount - oppose_amount, address=wallet.address)
        )
        t.set_wallet(wallet)
        t.seal()
        t.sign()

        # Attack: submit. After remediation, this should raise
        # InvalidTransactionError because the decoded subject contains a
        # control character (ESC, Cc). Today it silently lands in
        # pending.
        with pytest.raises(InvalidTransactionError):
            m.receive_transaction(t.txid, t.to_json())


def test_a4_c_coinbase_block_binding(app, time_machine, wallet) -> None:
    """A4.c v2: coinbases are bound to their block via prev_hash.

    Verifies the two halves of the fix:
    1. Two consecutive legitimate blocks (same wallet, same second under
       easy-mill) have DIFFERENT coinbase txids, because each coinbase's
       prev_hash differs (block N+1 extends block N). This is the
       root-cause fix for the read-side balance inflation.
    2. validate_block_coinbase raises MismatchedCoinbaseError when a
       coinbase's bound prev_hash does not equal its block's prev_hash.
    """
    with app.app_context():
        now_dt = now()
        when_dt = now_dt - datetime.timedelta(hours=1)
        time_machine.move_to(when_dt)
        m = Miller(milling_wallet=wallet)
        # Two consecutive blocks, no time advance (same wall-clock second).
        b0 = m.create_block()
        m.mill_block(b0)
        b1 = m.create_block()
        m.mill_block(b1)
        cb0 = b0.coinbase
        cb1 = b1.coinbase
        assert cb0 is not None
        assert cb1 is not None
        # Part 1: distinct coinbase txids despite same wallet/second/reward.
        assert cb0.txid != cb1.txid
        # And each coinbase is bound to its own block's parent.
        assert cb0.prev_hash == b0.prev_hash
        assert cb1.prev_hash == b1.prev_hash

        # Part 2: a coinbase whose binding mismatches its block is rejected.
        chain = m.longest_chain
        assert chain is not None
        # b_mismatch is a NEW block linked off the current tip (b1), so
        # chain.link_block sets b_mismatch.prev_hash = b1.block_hash. We
        # place cb0 (bound to b0.prev_hash, i.e. GENESIS_HASH) as its
        # coinbase. cb0.prev_hash (GENESIS_HASH) != b_mismatch.prev_hash
        # (b1.block_hash) → binding mismatch → MismatchedCoinbaseError.
        b_mismatch = Block()
        chain.link_block(b_mismatch)
        cb0_replay = Transaction.from_json(cb0.to_json())
        b_mismatch.add_txn(cb0_replay, is_coinbase=True)
        b_mismatch.merkle_root = b_mismatch.get_merkle_root()
        b_mismatch.timestamp = now_iso()
        b_mismatch.mill()
        with pytest.raises(MismatchedCoinbaseError):
            chain.validate_block_coinbase(b_mismatch)
