from gumptionchain.api_client import ApiClient
from gumptionchain.payload import encode_subject


def _stake_opposition(host, chain, wallet, amount, subject):
    txn = chain.create_opposition(wallet, amount, subject)
    txn.sign()
    ApiClient(host, wallet).post_transaction(txn)
    return txn


def _stake_support(host, chain, wallet, amount, subject):
    txn = chain.create_support(wallet, amount, subject)
    txn.sign()
    ApiClient(host, wallet).post_transaction(txn)
    return txn


# ---- subjects index ----------------------------------------------------


def test_subjects_index_empty(test_client):
    resp = test_client.get('/subjects')
    assert resp.status_code == 200
    assert b'No subjects staked yet' in resp.data


def test_subjects_index_shows_stakes(
    app, host, mill_block, requests_proxy, subject, wallet
):
    with app.app_context():
        m, _b = mill_block(wallet)
        _stake_opposition(host, m.longest_chain, wallet, 300, subject)
        mill_block(wallet)
        _stake_support(host, m.longest_chain, wallet, 150, subject)
        mill_block(wallet)

        resp = app.test_client().get('/subjects')
        assert resp.status_code == 200
        # human-readable subject name rendered
        assert b'failing tests' in resp.data
        # opposition + support + total present
        assert b'300' in resp.data
        assert b'150' in resp.data
        assert b'450' in resp.data
        # links to the detail page using the encoded subject
        assert f'/subject/{subject}'.encode() in resp.data


# ---- subject detail ----------------------------------------------------


def test_subject_detail_shows_totals_and_links(
    app, host, mill_block, requests_proxy, subject, wallet
):
    with app.app_context():
        m, _b = mill_block(wallet)
        txn = _stake_opposition(host, m.longest_chain, wallet, 300, subject)
        mill_block(wallet)
        _stake_support(host, m.longest_chain, wallet, 150, subject)
        mill_block(wallet)

        resp = app.test_client().get(f'/subject/{subject}')
        assert resp.status_code == 200
        assert b'300' in resp.data  # opposition total
        assert b'150' in resp.data  # support total
        # link to the staking transaction
        assert f'/transaction/{txn.txid}'.encode() in resp.data


def test_subject_detail_unknown_valid_subject_is_200_zeros(
    app, host, mill_block, requests_proxy, wallet
):
    unknown = encode_subject('never-staked')
    with app.app_context():
        mill_block(wallet)
        resp = app.test_client().get(f'/subject/{unknown}')
        assert resp.status_code == 200
        # zero totals, no staking outflows
        assert b'none' in resp.data


def test_subject_detail_invalid_subject_is_404(test_client):
    # '!!!' is not a valid base64url-encoded subject; converter rejects it.
    resp = test_client.get('/subject/!!!')
    assert resp.status_code == 404
