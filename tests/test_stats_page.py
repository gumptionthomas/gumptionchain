from gumptionchain.api_client import ApiClient
from gumptionchain.models import SubmissionDAO


def test_stats_page_lists_transactors(
    app,
    host,
    test_client,
    mill_block,
    requests_proxy,
    transactor_signing_key,
    signing_key,
):
    with app.app_context():
        app.config['MAX_PENDING_PER_TRANSACTOR'] = 10
        m, _ = mill_block(transactor_signing_key)
        txn = m.longest_chain.create_transfer(
            transactor_signing_key, 1, signing_key.address
        )
        txn.sign()
        ApiClient(host, transactor_signing_key).post(
            f'/api/transaction/{txn.txid}',
            data=txn.to_json(),
            headers={'Content-Type': 'application/json'},
        )
    resp = test_client.get('/stats')
    assert resp.status_code == 200
    assert transactor_signing_key.address.encode() in resp.data


def _seed_two_transactors(app):
    with app.app_context():
        # 'GCfewGC' has one submission, 'GCmanyGC' has three.
        SubmissionDAO.record('txF1', 'GCfewGC')
        SubmissionDAO.record('txM1', 'GCmanyGC')
        SubmissionDAO.record('txM2', 'GCmanyGC')
        SubmissionDAO.record('txM3', 'GCmanyGC')


def test_stats_sort_count_asc_orders_few_before_many(app, test_client):
    _seed_two_transactors(app)
    resp = test_client.get('/stats?sort=count&dir=asc')
    assert resp.status_code == 200
    body = resp.data.decode()
    assert body.index('GCfewGC') < body.index('GCmanyGC')


def test_stats_sort_bogus_key_falls_back_to_default(app, test_client):
    _seed_two_transactors(app)
    resp = test_client.get('/stats?sort=bogus')
    assert resp.status_code == 200


def test_stats_active_count_header_toggles_and_indicates(app, test_client):
    _seed_two_transactors(app)
    resp = test_client.get('/stats?sort=count&dir=desc')
    assert resp.status_code == 200
    body = resp.data.decode()
    assert 'sort=count&amp;dir=asc' in body or 'sort=count&dir=asc' in body
    assert '▼' in body
