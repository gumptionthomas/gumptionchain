from gumptionchain.api_client import ApiClient
from gumptionchain.payload import encode_subject


def _stake_opposition(host, chain, signing_key, amount, subject):
    txn = chain.create_opposition(signing_key, amount, subject)
    txn.sign()
    ApiClient(host, signing_key).post_transaction(txn)
    return txn


def _stake_support(host, chain, signing_key, amount, subject):
    txn = chain.create_support(signing_key, amount, subject)
    txn.sign()
    ApiClient(host, signing_key).post_transaction(txn)
    return txn


# ---- subjects index ----------------------------------------------------


def test_subjects_index_empty(test_client):
    resp = test_client.get('/subjects')
    assert resp.status_code == 200
    assert b'No subjects staked yet' in resp.data


def test_subjects_index_shows_stakes(
    app, host, mill_block, requests_proxy, subject, signing_key
):
    with app.app_context():
        m, _b = mill_block(signing_key)
        _stake_opposition(host, m.longest_chain, signing_key, 300, subject)
        mill_block(signing_key)
        _stake_support(host, m.longest_chain, signing_key, 150, subject)
        mill_block(signing_key)

        resp = app.test_client().get('/subjects')
        assert resp.status_code == 200
        # human-readable subject name rendered
        assert b'failing tests' in resp.data
        # opposition + support + total present
        assert b'300' in resp.data
        assert b'150' in resp.data
        assert b'450' in resp.data
        # net stance: opposition 300 leads support 150 by 150
        assert b'150 opposed' in resp.data
        # links to the detail page using the encoded subject
        assert f'/subject/{subject}'.encode() in resp.data


def test_subjects_index_paginates_across_pages(
    app, host, mill_block, requests_proxy, signing_key
):
    # Stake three subjects with distinct totals so ranking is deterministic,
    # then page with per_page=2 to exercise the _RowPagination offset/count
    # path (the leaderboard is a multi-column Core select, not an ORM entity).
    top = encode_subject('top-subject')
    mid = encode_subject('mid-subject')
    low = encode_subject('low-subject')
    with app.app_context():
        m, _b = mill_block(signing_key)
        _stake_opposition(host, m.longest_chain, signing_key, 300, top)
        mill_block(signing_key)
        _stake_opposition(host, m.longest_chain, signing_key, 200, mid)
        mill_block(signing_key)
        _stake_opposition(host, m.longest_chain, signing_key, 100, low)
        mill_block(signing_key)

        client = app.test_client()
        page1 = client.get('/subjects?per_page=2&page=1')
        assert page1.status_code == 200
        assert f'/subject/{top}'.encode() in page1.data  # rank 1
        assert f'/subject/{mid}'.encode() in page1.data  # rank 2
        assert f'/subject/{low}'.encode() not in page1.data

        page2 = client.get('/subjects?per_page=2&page=2')
        assert page2.status_code == 200
        # only the lowest-total subject spills onto page 2
        assert f'/subject/{low}'.encode() in page2.data
        assert f'/subject/{top}'.encode() not in page2.data
        assert f'/subject/{mid}'.encode() not in page2.data


# ---- subject detail ----------------------------------------------------


def test_subject_detail_shows_totals_and_links(
    app, host, mill_block, requests_proxy, subject, signing_key
):
    with app.app_context():
        m, _b = mill_block(signing_key)
        txn = _stake_opposition(
            host, m.longest_chain, signing_key, 300, subject
        )
        mill_block(signing_key)
        _stake_support(host, m.longest_chain, signing_key, 150, subject)
        mill_block(signing_key)

        resp = app.test_client().get(f'/subject/{subject}')
        assert resp.status_code == 200
        assert b'300' in resp.data  # opposition total
        assert b'150' in resp.data  # support total
        # net stance indicator: opposition leads support by 150
        assert b'Net:' in resp.data
        assert b'150 opposed' in resp.data
        # link to the staking transaction
        assert f'/transaction/{txn.txid}'.encode() in resp.data
        # explicit link back to the Subjects index (not the browser back button)
        assert b'href="/subjects"' in resp.data


def test_subject_detail_unknown_valid_subject_is_200_zeros(
    app, host, mill_block, requests_proxy, signing_key
):
    unknown = encode_subject('never-staked')
    with app.app_context():
        mill_block(signing_key)
        resp = app.test_client().get(f'/subject/{unknown}')
        assert resp.status_code == 200
        # zero totals, no staking outflows
        assert b'none' in resp.data


def test_subject_detail_invalid_subject_is_404(test_client):
    # '!!!' is not a valid base64url-encoded subject; converter rejects it.
    resp = test_client.get('/subject/!!!')
    assert resp.status_code == 404
