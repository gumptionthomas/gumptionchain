import httpx

from gumptionchain.block import Block
from gumptionchain.provenance import lookup_provenance


def test_lookup_provenance_returns_dict_for_canonical_txn(
    app, add_chain_block, subject, wallet
):
    with app.app_context():
        assert lookup_provenance('a' * 64) is None
        c, _ = add_chain_block()
        c.to_db()
        t = c.create_support(wallet, 1, subject)
        t.seal()
        t.sign()
        b = Block()
        b.add_txn(t)
        add_chain_block(chain=c, block=b)
        prov = lookup_provenance(t.txid)
        assert prov is not None
        assert 'status' in prov


def test_provenance_json_route(
    app, add_chain_block, subject, test_client, wallet
):
    with app.app_context():
        resp = test_client.get('/transaction/' + 'a' * 64 + '/provenance.json')
        assert resp.status_code == httpx.codes.NOT_FOUND
        assert resp.is_json
        assert resp.get_json()['error'] == 'transaction not found'

        c, _ = add_chain_block()
        c.to_db()
        t = c.create_support(wallet, 1, subject)
        t.seal()
        t.sign()
        b = Block()
        b.add_txn(t)
        add_chain_block(chain=c, block=b)

        resp = test_client.get(f'/transaction/{t.txid}/provenance.json')
        assert resp.status_code == httpx.codes.OK
        assert resp.is_json
        data = resp.get_json()
        assert data['txid'] == t.txid
        assert 'status' in data
