from gumptionchain.api_client import ApiClient
from gumptionchain.database import db
from gumptionchain.models import TransactionDAO
from gumptionchain.wallet import Wallet


def _post(host, txn, wallet):
    txn.sign()
    ApiClient(host, wallet).post_transaction(txn)
    return txn


def test_transaction_view_marks_coinbase(
    app, host, mill_block, requests_proxy, wallet
):
    with app.app_context():
        mill_block(wallet)  # every block carries a coinbase
        cb = db.session.scalar(
            db.select(TransactionDAO).where(
                TransactionDAO.prev_hash.is_not(None)
            )
        )
        page = (
            app.test_client()
            .get(f'/transaction/{cb.txid}')
            .get_data(as_text=True)
        )
        assert 'Coinbase' in page  # the header badge
        assert 'newly minted' in page  # the no-inputs message
        # a coinbase's reward outputs are address transfers
        assert 'transfer' in page


def test_transaction_view_labels_stake_and_rescind_kinds(
    app, host, mill_block, requests_proxy, subject, wallet
):
    with app.app_context():
        m, _b = mill_block(wallet)
        opp = _post(
            host,
            m.longest_chain.create_opposition(wallet, 300, subject),
            wallet,
        )
        mill_block(wallet)
        resc = _post(
            host,
            m.longest_chain.create_rescind(wallet, 100, subject, 'opposition'),
            wallet,
        )
        mill_block(wallet)

        c = app.test_client()
        # the opposition stake txn labels its stake + change outflows
        opp_page = c.get(f'/transaction/{opp.txid}').get_data(as_text=True)
        assert 'Kind' in opp_page  # new column header
        assert 'opposition' in opp_page
        assert 'transfer' in opp_page  # the change output is a transfer

        # the rescind txn labels the rescind (with its kind) + re-staked change
        resc_page = c.get(f'/transaction/{resc.txid}').get_data(as_text=True)
        assert 'rescind opposition' in resc_page
        assert 'opposition' in resc_page  # the 200 re-staked as opposition


def test_transaction_view_labels_transfer(
    app, host, mill_block, requests_proxy, wallet
):
    with app.app_context():
        m, _b = mill_block(wallet)
        xfer = _post(
            host,
            m.longest_chain.create_transfer(wallet, 100, Wallet().address),
            wallet,
        )
        mill_block(wallet)
        page = (
            app.test_client()
            .get(f'/transaction/{xfer.txid}')
            .get_data(as_text=True)
        )
        assert 'transfer' in page
        assert 'Coinbase' not in page  # a regular transfer is not a coinbase
