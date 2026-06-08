from gumptionchain.api_client import ApiClient


def _stake_opposition(host, chain, wallet, amount, subject):
    txn = chain.create_opposition(wallet, amount, subject)
    txn.sign()
    ApiClient(host, wallet).post_transaction(txn)
    return txn


def test_home_empty_chain(test_client):
    resp = test_client.get('/')
    assert resp.status_code == 200
    assert b'No chain' in resp.data


def test_home_shows_stats_and_recent_blocks(
    app, host, mill_block, requests_proxy, subject, wallet
):
    with app.app_context():
        m, _b1 = mill_block(wallet)
        _stake_opposition(host, m.longest_chain, wallet, 300, subject)
        _m, tip = mill_block(wallet)

        resp = app.test_client().get('/')
        assert resp.status_code == 200
        body = resp.data
        # stats strip labels
        assert b'Height' in body or b'Blocks' in body
        assert b'Transactions' in body
        assert b'Subjects' in body
        # links into the explorer
        assert b'/blocks' in body
        assert b'/subjects' in body
        # the chain tip hash appears in the recent-blocks table
        assert tip.block_hash.encode() in body
