from gumptionchain.api_client import ApiClient


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
