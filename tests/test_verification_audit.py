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

import pytest

from cancelchain.exceptions import InvalidTransactionError
from cancelchain.miller import Miller
from cancelchain.payload import Inflow, Outflow
from cancelchain.transaction import Transaction
from cancelchain.util import now


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
