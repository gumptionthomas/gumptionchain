from gumptionchain.api_client import ApiClient
from gumptionchain.database import db
from gumptionchain.models import SubmissionDAO


def _post_transfer(host, chain, key, to_key):
    txn = chain.create_transfer(key, 1, to_key.address)
    txn.sign()
    return ApiClient(host, key).post(
        f'/api/transaction/{txn.txid}',
        data=txn.to_json(),
        headers={'Content-Type': 'application/json'},
        raise_for_status=False,
    )


def test_transactor_over_cap_gets_429(
    app, host, mill_block, requests_proxy, transactor_signing_key, signing_key
):
    with app.app_context():
        app.config['MAX_PENDING_PER_TRANSACTOR'] = 1
        # Mine two blocks so both transfers have separate confirmed UTXOs
        # (filter_pending excludes outputs still in the pending pool).
        mill_block(transactor_signing_key)
        m, _ = mill_block(transactor_signing_key)
        lc = m.longest_chain
        r1 = _post_transfer(host, lc, transactor_signing_key, signing_key)
        assert r1.status_code in (200, 201, 202)
        r2 = _post_transfer(host, lc, transactor_signing_key, signing_key)
        assert r2.status_code == 429
        assert 'quota' in r2.json()['error']


def test_under_cap_admits_and_records_submission(
    app, host, mill_block, requests_proxy, transactor_signing_key, signing_key
):
    with app.app_context():
        app.config['MAX_PENDING_PER_TRANSACTOR'] = 10
        m, _ = mill_block(transactor_signing_key)
        lc = m.longest_chain
        r = _post_transfer(host, lc, transactor_signing_key, signing_key)
        assert r.status_code in (200, 201, 202)
        rows = db.session.execute(db.select(SubmissionDAO)).scalars().all()
        assert len(rows) == 1
        assert rows[0].transactor_address == transactor_signing_key.address


def test_miller_is_exempt_from_cap(
    app, host, mill_block, requests_proxy, miller_signing_key, signing_key
):
    with app.app_context():
        app.config['MAX_PENDING_PER_TRANSACTOR'] = 1
        m, _ = mill_block(miller_signing_key)
        lc = m.longest_chain
        r1 = _post_transfer(host, lc, miller_signing_key, signing_key)
        mill_block(miller_signing_key)
        lc = m.longest_chain
        r2 = _post_transfer(host, lc, miller_signing_key, signing_key)
        assert r1.status_code in (200, 201, 202)
        assert r2.status_code in (200, 201, 202)
