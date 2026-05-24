import datetime

from cancelchain.block import Block
from cancelchain.chain import Chain
from cancelchain.models import BlockDAO
from cancelchain.payload import Inflow, Outflow
from cancelchain.transaction import Transaction


def test_unspent_outflows(app, subject, time_stepper, wallet):
    with app.app_context():
        time_step = time_stepper(start=datetime.datetime.now(datetime.UTC))
        _ = next(time_step)
        chain_a = Chain()
        block_1 = Block()
        chain_a.link_block(block_1)
        chain_a.seal_block(block_1, wallet)
        block_1.mill()
        chain_a.add_block(block_1)
        cb_1 = block_1.coinbase
        cb_1_amount = next(iter(cb_1.outflows)).amount
        chain_a.to_db()
        dao_a = chain_a.to_dao()

        assert BlockDAO.query.count() == 1
        assert dao_a is not None
        assert dao_a.unspent_outflows(wallet.address).count() == 1
        balance = chain_a.block_reward()
        assert dao_a.wallet_balance(wallet.address) == balance

        _ = next(time_step)
        t_2a = Transaction()
        t_2a.add_inflow(Inflow(outflow_txid=cb_1.txid, outflow_idx=0))
        t_2a.add_outflow(Outflow(amount=cb_1_amount, subject=subject))
        t_2a.set_wallet(wallet)
        t_2a.seal()
        t_2a.sign()

        _ = next(time_step)
        block_2a = Block()
        block_2a.add_txn(t_2a)
        chain_a.link_block(block_2a)
        chain_a.seal_block(block_2a, wallet)
        block_2a.mill()

        _ = next(time_step)
        block_2b = Block()
        chain_a.link_block(block_2b)
        chain_a.seal_block(block_2b, wallet)
        block_2b.mill()

        _ = next(time_step)
        chain_a.add_block(block_2a)
        chain_a.to_db()

        assert BlockDAO.query.count() == 2
        assert dao_a.unspent_outflows(wallet.address).count() == 2
        balance = int(1.5 * chain_a.block_reward())
        assert dao_a.wallet_balance(wallet.address) == balance
        assert dao_a.subject_balance(subject) == cb_1_amount

        _ = next(time_step)
        chain_b = Chain()
        chain_b.add_block(block_2b)
        chain_b.to_db()
        dao_b = chain_b.to_dao()
        assert dao_b is not None

        assert BlockDAO.query.count() == 3
        assert dao_b.unspent_outflows(wallet.address).count() == 2
        balance = 2 * chain_b.block_reward()
        assert dao_b.wallet_balance(wallet.address) == balance
        assert dao_b.subject_balance(subject) == 0

        assert dao_a.unspent_outflows(wallet.address).count() == 2
        balance = int(1.5 * chain_a.block_reward())
        assert dao_a.wallet_balance(wallet.address) == balance
        assert dao_a.subject_balance(subject) == cb_1_amount
