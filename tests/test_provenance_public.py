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
