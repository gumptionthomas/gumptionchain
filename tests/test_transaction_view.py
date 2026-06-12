from jinja2 import ChoiceLoader, FileSystemLoader

from gumptionchain.api_client import ApiClient
from gumptionchain.chain import Chain
from gumptionchain.database import db
from gumptionchain.milling import mill_hash_str
from gumptionchain.models import TransactionDAO
from gumptionchain.signing_key import SigningKey


def _post(host, txn, signing_key):
    txn.sign()
    ApiClient(host, signing_key).post_transaction(txn)
    return txn


def test_transaction_view_marks_coinbase(
    app, host, mill_block, requests_proxy, signing_key
):
    with app.app_context():
        mill_block(signing_key)  # every block carries a coinbase
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
    app, host, mill_block, requests_proxy, subject, signing_key
):
    with app.app_context():
        m, _b = mill_block(signing_key)
        opp = _post(
            host,
            m.longest_chain.create_opposition(signing_key, 300, subject),
            signing_key,
        )
        mill_block(signing_key)
        resc = _post(
            host,
            m.longest_chain.create_rescind(
                signing_key, 100, subject, 'opposition'
            ),
            signing_key,
        )
        mill_block(signing_key)

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
    app, host, mill_block, requests_proxy, signing_key
):
    with app.app_context():
        m, _b = mill_block(signing_key)
        xfer = _post(
            host,
            m.longest_chain.create_transfer(
                signing_key, 100, SigningKey().address
            ),
            signing_key,
        )
        mill_block(signing_key)
        page = (
            app.test_client()
            .get(f'/transaction/{xfer.txid}')
            .get_data(as_text=True)
        )
        assert 'transfer' in page
        assert 'Coinbase' not in page  # a regular transfer is not a coinbase


def test_transaction_view_pending_txn(
    app, host, mill_block, requests_proxy, subject, signing_key
):
    # #258: a freshly submitted, still-pending stake has a page (it used
    # to 404 until milled) with a Pending status and resolved inflow
    # amounts from the canonical parent.
    with app.app_context():
        m, _b = mill_block(signing_key)
        opp = _post(
            host,
            m.longest_chain.create_opposition(signing_key, 300, subject),
            signing_key,
        )
        resp = app.test_client().get(f'/transaction/{opp.txid}')
        assert resp.status_code == 200
        page = resp.get_data(as_text=True)
        assert 'Pending' in page
        assert opp.txid in page
        assert 'opposition' in page
        # the spent coinbase outflow's amount resolves from the chain
        assert 'None (pending)' in page  # block row placeholder


def test_transaction_view_confirmed_shows_status(
    app, host, mill_block, requests_proxy, subject, signing_key
):
    with app.app_context():
        m, _b = mill_block(signing_key)
        opp = _post(
            host,
            m.longest_chain.create_opposition(signing_key, 300, subject),
            signing_key,
        )
        mill_block(signing_key)  # confirms (1 confirmation)
        page = (
            app.test_client()
            .get(f'/transaction/{opp.txid}')
            .get_data(as_text=True)
        )
        assert 'Confirmed' in page
        assert '1 confirmation' in page


def test_transaction_view_orphaned_shows_status(
    add_chain_block, app, host, mill_block, requests_proxy, signing_key
):
    # Fork construction mirrors test_chain.py's
    # test_transaction_provenance_orphaned: b2's coinbase becomes
    # orphaned when a longer fork off b1 wins.
    with app.app_context():
        signing_key2 = SigningKey()
        _, b1 = mill_block(signing_key)
        _, b2 = mill_block(signing_key)
        coinbase_txid = b2.txns[-1].txid

        alt = Chain(block_hash=b1.block_hash)
        add_chain_block(chain=alt, milling_signing_key=signing_key2)
        add_chain_block(chain=alt, milling_signing_key=signing_key2)
        alt.to_db()

        page = (
            app.test_client()
            .get(f'/transaction/{coinbase_txid}')
            .get_data(as_text=True)
        )
        assert 'Orphaned' in page


def test_transaction_view_unknown_txid_404(app, mill_block, signing_key):
    with app.app_context():
        mill_block(signing_key)
        absent = mill_hash_str('no-such-transaction')
        resp = app.test_client().get(f'/transaction/{absent}')
        assert resp.status_code == 404


def test_transaction_extra_hook_renders_with_status_context(
    app, host, mill_block, requests_proxy, subject, signing_key, tmp_path
):
    # #258: the transaction/extra.html seam hook — a consumer template
    # injects per-transaction UI and can read the page's status context.
    (tmp_path / 'transaction').mkdir()
    (tmp_path / 'transaction' / 'extra.html').write_text(
        '<div id="hook">HOOKED status={{ status }} '
        'signer={{ transaction.address }}</div>'
    )
    app.jinja_loader = ChoiceLoader(
        [FileSystemLoader(str(tmp_path)), app.jinja_loader]
    )
    with app.app_context():
        m, _b = mill_block(signing_key)
        opp = _post(
            host,
            m.longest_chain.create_opposition(signing_key, 300, subject),
            signing_key,
        )
        page = (
            app.test_client()
            .get(f'/transaction/{opp.txid}')
            .get_data(as_text=True)
        )
        assert 'HOOKED status=pending' in page
        assert f'signer={signing_key.address}' in page


def test_transaction_view_block_row_shows_containing_block(
    app, host, mill_block, requests_proxy, subject, signing_key
):
    # Regression (#258 rewrite): the inflow loop used to reassign
    # transaction_dao, so the Block row showed the inflow parent's block
    # instead of the block containing this transaction.
    with app.app_context():
        m, _b1 = mill_block(signing_key)
        opp = _post(
            host,
            m.longest_chain.create_opposition(signing_key, 300, subject),
            signing_key,
        )
        _m, b2 = mill_block(signing_key)  # contains the stake
        page = (
            app.test_client()
            .get(f'/transaction/{opp.txid}')
            .get_data(as_text=True)
        )
        assert b2.block_hash in page
