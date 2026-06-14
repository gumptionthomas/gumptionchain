from gumptionchain.database import db
from gumptionchain.signing_key import SigningKey

# ---- data-layer delegates ----------------------------------------------


def test_signing_key_leaderboard_includes_milled_address(
    app, host, mill_block, requests_proxy, signing_key
):
    with app.app_context():
        m, _b = mill_block(signing_key)
        lc = m.longest_chain
        rows = db.session.execute(lc.signing_key_leaderboard()).all()
        by_addr = {row.address: row.ct for row in rows}
        assert signing_key.address in by_addr
        assert by_addr[signing_key.address] == lc.balance(signing_key.address)


def test_address_holdings_ordered_amount_desc(
    app, host, mill_block, requests_proxy, signing_key
):
    with app.app_context():
        m, _b = mill_block(signing_key)
        m, _b = mill_block(signing_key)
        lc = m.longest_chain
        flows = list(
            db.session.scalars(lc.address_holdings(signing_key.address))
        )
        assert len(flows) >= 1
        amounts = [f.amount for f in flows]
        assert amounts == sorted(amounts, reverse=True)
        # all holdings belong to the queried address
        assert all(f.address == signing_key.address for f in flows)


def test_address_holdings_empty_for_unknown_address(
    app, host, mill_block, requests_proxy, signing_key
):
    with app.app_context():
        m, _b = mill_block(signing_key)
        lc = m.longest_chain
        unknown = SigningKey().address
        flows = list(db.session.scalars(lc.address_holdings(unknown)))
        assert flows == []


# ---- addresses index ---------------------------------------------------


def test_addresses_index_empty(test_client):
    resp = test_client.get('/addresses')
    assert resp.status_code == 200
    assert b'No addresses with a balance yet' in resp.data


def test_addresses_index_shows_milled_address(
    app, host, mill_block, requests_proxy, signing_key
):
    with app.app_context():
        m, _b = mill_block(signing_key)
        balance = m.longest_chain.balance(signing_key.address)

        resp = app.test_client().get('/addresses')
        assert resp.status_code == 200
        assert signing_key.address.encode() in resp.data
        assert f'/address/{signing_key.address}'.encode() in resp.data
        assert b'clickable' in resp.data  # rows clickable into address detail
        assert str(balance).encode() in resp.data


# ---- address detail ----------------------------------------------------


def test_address_detail_shows_balance_and_holdings(
    app, host, mill_block, requests_proxy, signing_key
):
    with app.app_context():
        m, _b = mill_block(signing_key)
        lc = m.longest_chain
        balance = lc.balance(signing_key.address)
        flows = list(
            db.session.scalars(lc.address_holdings(signing_key.address))
        )
        a_txid = flows[0].txid

        resp = app.test_client().get(f'/address/{signing_key.address}')
        assert resp.status_code == 200
        assert signing_key.address.encode() in resp.data
        assert str(balance).encode() in resp.data
        # a holding links to its source transaction
        assert f'/transaction/{a_txid}'.encode() in resp.data
        # breadcrumb back to the Addresses index
        assert b'&larr; Addresses' in resp.data


def test_address_detail_unknown_valid_address_is_200_zeros(
    app, host, mill_block, requests_proxy, signing_key
):
    with app.app_context():
        mill_block(signing_key)
        unknown = SigningKey().address
        resp = app.test_client().get(f'/address/{unknown}')
        assert resp.status_code == 200
        assert b'0' in resp.data
        assert b'none' in resp.data


def test_address_detail_invalid_address_is_404(test_client):
    resp = test_client.get('/address/notvalid')
    assert resp.status_code == 404


def test_address_detail_accepts_txn_page_arg(
    app, host, mill_block, requests_proxy, signing_key
):
    with app.app_context():
        mill_block(signing_key)
        resp = app.test_client().get(
            f'/address/{signing_key.address}?txn_page=2'
        )
        assert resp.status_code == 200
