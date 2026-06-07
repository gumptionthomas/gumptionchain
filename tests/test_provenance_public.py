import httpx

from gumptionchain.api_client import ApiClient
from gumptionchain.provenance import lookup_provenance


def test_lookup_provenance_returns_dict_for_canonical_txn(
    app, host, mill_block, requests_proxy, subject, wallet
):
    with app.app_context():
        assert lookup_provenance('a' * 64) is None
        m, _b1 = mill_block(wallet)
        txn = m.longest_chain.create_opposition(wallet, 300, subject)
        txn.sign()
        ApiClient(host, wallet).post_transaction(txn)
        mill_block(wallet)
        prov = lookup_provenance(txn.txid)
        assert prov is not None
        assert prov['status'] == 'canonical'


def test_provenance_json_route_unknown_returns_404(app, test_client):
    with app.app_context():
        resp = test_client.get('/transaction/' + 'a' * 64 + '/provenance.json')
        assert resp.status_code == httpx.codes.NOT_FOUND
        assert resp.is_json
        assert resp.get_json()['error'] == 'transaction not found'


def test_provenance_json_route_canonical(
    app, host, mill_block, requests_proxy, subject, test_client, wallet
):
    with app.app_context():
        m, _b1 = mill_block(wallet)
        txn = m.longest_chain.create_opposition(wallet, 300, subject)
        txn.sign()
        ApiClient(host, wallet).post_transaction(txn)
        _m, b2 = mill_block(wallet)

        resp = test_client.get(f'/transaction/{txn.txid}/provenance.json')
        assert resp.status_code == httpx.codes.OK
        assert resp.is_json
        data = resp.get_json()
        assert data['txid'] == txn.txid
        assert data['status'] == 'canonical'
        assert data['confirmations'] == 1
        assert data['block_hash'] == b2.block_hash
