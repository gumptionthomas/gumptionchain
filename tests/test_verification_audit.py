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

from cancelchain.block import TXN_TIMEOUT, Block
from cancelchain.chain import GENESIS_HASH, REWARD
from cancelchain.database import db
from cancelchain.exceptions import (
    InvalidBlockError,
    InvalidCoinbaseError,
    InvalidTransactionError,
)
from cancelchain.miller import Miller
from cancelchain.models import ChainDAO
from cancelchain.payload import Inflow, Outflow, encode_subject
from cancelchain.transaction import Transaction
from cancelchain.util import dt_2_iso, now, now_iso

# Matches the `easy_mill_chain` session-scoped fixture's patched
# MAX_TARGET — every target in tests is the 64-character all-F hex
# string (the max 256-bit target) so PoW is trivially found and the
# chain.block_target retarget formula always returns this value.
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


@pytest.mark.xfail(
    reason=(
        'Audit finding A4.c — severity Medium — Chain.validate_block_coinbase '
        'enforces only the REWARD amount and S/G/M shape; it does not check '
        "that the coinbase's txid is fresh (not already persisted in the "
        "chain's lineage). A MILLER-role adversary can mine a block whose "
        'coinbase is a verbatim replay of any prior block coinbase, '
        'appending a duplicate block_transactions m2m row that inflates the '
        "original miller's longest-chain wallet_balance by one REWARD per "
        'replay (the InflowDAO unique(txid, idx) still prevents the inflated '
        'balance from being directly spendable, but the accounting query '
        'layer reports the wrong number). See '
        'docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md'
    ),
    strict=True,
)
def test_a4_c_ii_coinbase_replay_inflates_balance(
    app, time_machine, wallet
) -> None:
    """A4.c.ii: replaying another miller's coinbase in a fresh block.

    Pre-state: Local chain has a single mined block B_orig whose coinbase
    T_cb pays the milling wallet REWARD. T_cb is in TransactionDAO and
    m2m-associated with B_orig.
    Attack: The adversary (acting as a MILLER) builds B_adv extending
    B_orig with txns=[T_cb] only (T_cb in the last position so
    Block.regular_txns is empty and the coinbase-positional rule
    identifies T_cb as B_adv's coinbase). They mill PoW honestly and
    invoke Node.receive_block on the constructed block. Today
    Chain.validate_block_coinbase passes (correct REWARD amount, empty
    S/G/M comps match T_cb's single-outflow shape), so B_adv is persisted
    with a new block_transactions m2m row.
    Expected after remediation: Chain.validate_block_coinbase raises
    InvalidCoinbaseError (e.g., via a new DuplicateCoinbaseError) when
    the candidate coinbase's txid is already persisted in the chain's
    lineage — analogous to the inflow-uniqueness check already enforced
    by Chain.validate_txn_inflow via get_inflows_count.
    Observed today: receive_block succeeds; T_cb is m2m'd with both
    B_orig and B_adv, so the longest_chain_outflows_q join produces two
    rows of T_cb's REWARD outflow and wallet_balance double-counts.
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


@pytest.mark.xfail(
    reason=(
        'Audit finding A7.b — severity Low — Chain.validate_block has no '
        '"is the canonical genesis already taken?" check, so any block '
        'with prev_hash=GENESIS_HASH, idx=0, target=MAX_TARGET is accepted '
        'as a fresh genesis. Each accepted alternate genesis creates a new '
        'ChainDAO row, fragmenting the chain registry into N parallel '
        'single-block chains and consuming DB rows without any operational '
        'recovery path. See '
        'docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md'
    ),
    strict=True,
)
def test_a7_b_alternate_genesis_fragments_chain_registry(
    app, time_machine, wallet, miller_2_wallet
) -> None:
    """A7.b: alternate genesis blocks are admitted, creating sibling chains.

    Pre-state: Empty BlockDAO. The first mined block becomes the canonical
    genesis (block_hash=G1, paying `wallet`).
    Attack: Mine a second block with prev_hash=GENESIS_HASH, idx=0, and a
    coinbase paying a different miller wallet (miller_2_wallet) at a
    different timestamp, yielding a block_hash G2 != G1.
    Expected after remediation: receive_block rejects the second genesis
    with InvalidBlockError (e.g., a new DuplicateGenesisError); the
    ChainDAO registry stays at one row.
    Observed today: receive_block accepts G2, Node.add_block's
    create_chain fallback (node.py:187) builds a fresh Chain instance,
    and a second ChainDAO row is committed pointing at G2 alongside the
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
        # should raise InvalidBlockError. Today it silently accepts.
        with pytest.raises(InvalidBlockError):
            m1.receive_block(g2.to_json())
        # Even if the call had not raised, post-remediation the chain
        # registry should still hold only the canonical chain. The
        # following assertion is the actual observable gap demonstrator:
        # today the count goes to 2 because Node.add_block created a
        # second ChainDAO row.
        assert _chain_count() == initial_chain_count


@pytest.mark.xfail(
    reason=(
        'Audit finding A7.e — severity Low — three call sites apply '
        'TXN_TIMEOUT with three different comparison operators around '
        'the boundary value: Block.validate_transaction uses strict < '
        '(block.py:269), Miller.pending_chain_txns uses strict > '
        '(miller.py:74), and Node.discard_expired_pending_txns uses <= '
        '(node.py:105). A txn timestamped exactly now-TXN_TIMEOUT is '
        'non-expired per the block validator but expired per pending-pool '
        'maintenance. See '
        'docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md'
    ),
    strict=True,
)
def test_a7_e_txn_timeout_boundary_inconsistency(
    app, time_machine, wallet
) -> None:
    """A7.e: same boundary value treated differently by three call sites.

    Pre-state: Local chain has a mined genesis paying `wallet` REWARD; a
    valid spending txn T exists in pending with timestamp exactly
    now - TXN_TIMEOUT.
    Attack: Call Node.discard_expired_pending_txns at time `now`. Today
    T is discarded (uses <=). Then construct an in-memory block with
    timestamp `now` and call block.validate_transaction(T) — it does
    NOT raise ExpiredTransactionError (Block uses strict <).
    Expected after remediation: All three sites agree on the same
    open/closed boundary semantics. Recommended: open (strict <),
    meaning T is "alive" at the boundary instant. Concretely,
    discard_expired_pending_txns should NOT discard T at the boundary.
    Observed today: discard_expired_pending_txns evicts T even though
    Block.validate_transaction would accept it.
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
        # `when_dt - TXN_TIMEOUT`; block.py:269's strict-< means
        # equality is non-expired).
        boundary_block = Block(timestamp=dt_2_iso(when_dt))
        # Should NOT raise ExpiredTransactionError; the block validator
        # treats this txn as alive at the boundary.
        boundary_block.validate_transaction(t)

        # Today: Node.discard_expired_pending_txns evicts T because it
        # uses `<= now() - TXN_TIMEOUT` (node.py:105). After remediation
        # (open-boundary semantics applied consistently), the eviction
        # check should align with Block.validate_transaction's strict-<,
        # leaving T in pending.
        m.discard_expired_pending_txns()
        assert len(m.pending_txns) == 1, (
            'A7.e gap demonstrated: T was discarded by '
            'discard_expired_pending_txns at the boundary even though '
            'Block.validate_transaction treats T as non-expired at the '
            'same instant.'
        )


@pytest.mark.xfail(
    reason=(
        'Audit finding A7.h — severity Low — validate_subject and '
        'validate_raw_subject (payload.py:39-55) enforce only length '
        'bounds and canonical base64-url round-trip; they accept any '
        'UTF-8 codepoint including null bytes, C0/C1 control characters, '
        'bidirectional override (RLO), and zero-width joiners. Subjects '
        'flow into CLI and API rendering paths that are unlikely to '
        'sanitize control bytes. See '
        'docs/superpowers/audits/2026-05-29-verification-pipeline-audit.md'
    ),
    strict=True,
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
        t.add_outflow(Outflow(amount=oppose_amount, subject=malicious_subject))
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
