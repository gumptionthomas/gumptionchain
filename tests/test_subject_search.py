from gumptionchain.api_client import ApiClient
from gumptionchain.database import db
from gumptionchain.models import OutflowDAO
from gumptionchain.payload import encode_subject


def test_outflow_populates_plaintext_columns_for_a_stake(app):
    with app.app_context():
        enc = encode_subject('Tabs > Spaces')
        row = OutflowDAO('txid1', 0, 100, support=enc)
        assert row.subject_plain == 'Tabs > Spaces'
        assert row.subject_lower == 'tabs > spaces'


def test_outflow_populates_plaintext_columns_for_opposition(app):
    with app.app_context():
        enc = encode_subject('Loud Chewing')
        row = OutflowDAO('txid_opp', 0, 100, opposition=enc)
        assert row.subject_plain == 'Loud Chewing'
        assert row.subject_lower == 'loud chewing'


def test_outflow_plaintext_columns_none_for_non_stake(app):
    with app.app_context():
        row = OutflowDAO('txid2', 0, 100, address='GCwhoeverGC')
        assert row.subject_plain is None
        assert row.subject_lower is None


def _stake(host, chain, signing_key, *, oppose=None, support=None):
    if oppose is not None:
        subject, amount = oppose
        txn = chain.create_opposition(signing_key, amount, subject)
    else:
        subject, amount = support
        txn = chain.create_support(signing_key, amount, subject)
    txn.sign()
    ApiClient(host, signing_key).post_transaction(txn)
    return txn


def test_search_prefix_is_case_insensitive_returns_canonical(
    app, host, mill_block, requests_proxy, signing_key
):
    tabs = encode_subject('Tabs')
    table = encode_subject('TABLE')
    zebra = encode_subject('Zebra')
    with app.app_context():
        m, _b = mill_block(signing_key)
        _stake(host, m.longest_chain, signing_key, oppose=(tabs, 300))
        mill_block(signing_key)
        _stake(host, m.longest_chain, signing_key, support=(table, 100))
        mill_block(signing_key)
        _stake(host, m.longest_chain, signing_key, oppose=(zebra, 999))
        mill_block(signing_key)

        dao = m.longest_chain.to_dao()
        rows = db.session.execute(dao.search_subjects('tab', 8)).all()
        subjects = [r.subject for r in rows]
        assert set(subjects) == {'Tabs', 'TABLE'}


def test_search_ranks_by_total_and_caps(
    app, host, mill_block, requests_proxy, signing_key
):
    ta = encode_subject('Tango')
    tb = encode_subject('Tankard')
    with app.app_context():
        m, _b = mill_block(signing_key)
        _stake(host, m.longest_chain, signing_key, oppose=(ta, 100))
        mill_block(signing_key)
        _stake(host, m.longest_chain, signing_key, oppose=(tb, 500))
        mill_block(signing_key)

        dao = m.longest_chain.to_dao()
        rows = db.session.execute(dao.search_subjects('tan', 8)).all()
        assert [r.subject for r in rows] == ['Tankard', 'Tango']
        top = db.session.execute(dao.search_subjects('tan', 1)).all()
        assert [r.subject for r in top] == ['Tankard']


def test_opposition_balance_endpoint_uses_symmetric_key(
    app, host, mill_block, requests_proxy, signing_key, reader_signing_key
):
    # The /opposition balance endpoint returns grains under "opposition",
    # symmetric with /support's "support" key (#283) — not the old "balance".
    sub = encode_subject('loud chewing')
    with app.app_context():
        m, _b = mill_block(signing_key)
        _stake(host, m.longest_chain, signing_key, oppose=(sub, 200))
        mill_block(signing_key)
    client = ApiClient(host, reader_signing_key)
    body = client.get_opposition_balance(sub).json()
    assert body['opposition'] == 200
    assert 'balance' not in body
    assert 'as_of_block' in body


def test_search_blank_query_returns_nothing(
    app, host, mill_block, requests_proxy, signing_key
):
    sub = encode_subject('Anything')
    with app.app_context():
        m, _b = mill_block(signing_key)
        _stake(host, m.longest_chain, signing_key, oppose=(sub, 100))
        mill_block(signing_key)
        dao = m.longest_chain.to_dao()
        assert db.session.execute(dao.search_subjects('', 8)).all() == []
        assert db.session.execute(dao.search_subjects('   ', 8)).all() == []


def test_search_escapes_like_metacharacters(
    app, host, mill_block, requests_proxy, signing_key
):
    pct = encode_subject('50% off')
    plain = encode_subject('500 dollars')
    with app.app_context():
        m, _b = mill_block(signing_key)
        _stake(host, m.longest_chain, signing_key, oppose=(pct, 100))
        mill_block(signing_key)
        _stake(host, m.longest_chain, signing_key, oppose=(plain, 100))
        mill_block(signing_key)
        dao = m.longest_chain.to_dao()
        rows = db.session.execute(dao.search_subjects('50%', 8)).all()
        assert [r.subject for r in rows] == ['50% off']


def test_search_endpoint_returns_shape(
    app, host, mill_block, requests_proxy, signing_key, reader_signing_key
):
    tabs = encode_subject('Tabs')
    with app.app_context():
        m, _b = mill_block(signing_key)
        _stake(host, m.longest_chain, signing_key, oppose=(tabs, 250))
        mill_block(signing_key)

    client = ApiClient(host, reader_signing_key)
    resp = client.get('/api/subjects/search', params={'q': 'tab', 'limit': '8'})
    body = resp.json()
    assert resp.status_code == 200
    assert body['subjects'] == [
        {'subject': 'Tabs', 'opposition': 250, 'support': 0}
    ]
    assert 'as_of_block' in body


def test_search_endpoint_blank_query_is_empty(
    app, host, mill_block, requests_proxy, signing_key, reader_signing_key
):
    sub = encode_subject('Tabs')
    with app.app_context():
        m, _b = mill_block(signing_key)
        _stake(host, m.longest_chain, signing_key, oppose=(sub, 100))
        mill_block(signing_key)
    client = ApiClient(host, reader_signing_key)
    resp = client.get('/api/subjects/search', params={'q': '', 'limit': '8'})
    assert resp.status_code == 200
    assert resp.json()['subjects'] == []


def test_api_client_get_subject_search_round_trips(
    app, host, mill_block, requests_proxy, signing_key, reader_signing_key
):
    tabs = encode_subject('Tabs')
    with app.app_context():
        m, _b = mill_block(signing_key)
        _stake(host, m.longest_chain, signing_key, oppose=(tabs, 75))
        mill_block(signing_key)
    resp = ApiClient(host, reader_signing_key).get_subject_search('tab', 8)
    assert resp.status_code == 200
    assert resp.json()['subjects'] == [
        {'subject': 'Tabs', 'opposition': 75, 'support': 0}
    ]
