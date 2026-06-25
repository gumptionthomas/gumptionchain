import httpx

from gumptionchain.block import Block


def test_index(app, mill_block, test_client, signing_key):
    with app.app_context():
        response = test_client.get('/')
        assert response.status_code == httpx.codes.OK
        assert 'No chain' in str(response.data)
        _m, b = mill_block(signing_key)
        response = test_client.get('/')
        assert response.status_code == httpx.codes.OK
        assert b.block_hash in str(response.data)


def test_chains(app, mill_block, test_client, signing_key):
    with app.app_context():
        response = test_client.get('/chains')
        assert response.status_code == httpx.codes.OK
        assert 'No chains' in str(response.data)
        _m, b = mill_block(signing_key)
        response = test_client.get('/chains')
        assert response.status_code == httpx.codes.OK
        assert b.block_hash in str(response.data)


def test_block(app, mill_block, test_client, signing_key):
    with app.app_context():
        response = test_client.get('/block')
        assert response.status_code == httpx.codes.NOT_FOUND
        _m, b = mill_block(signing_key)
        response = test_client.get('/block')
        assert response.status_code == httpx.codes.OK
        assert b.block_hash in str(response.data)
        response = test_client.get(f'/block/{b.block_hash}')
        assert response.status_code == httpx.codes.OK
        assert b.block_hash in str(response.data)


def test_transaction(app, add_chain_block, subject, test_client, signing_key):
    with app.app_context():
        response = test_client.get('/transaction/foo')
        assert response.status_code == httpx.codes.NOT_FOUND
        c, _ = add_chain_block()
        c.to_db()
        t = c.create_support(signing_key, 1, subject)
        t.seal()
        t.sign()
        b = Block()
        b.add_txn(t)
        c, _ = add_chain_block(chain=c, block=b)
        response = test_client.get(f'/transaction/{t.txid}')
        assert response.status_code == httpx.codes.OK
        assert t.txid in str(response.data)


def test_signing_key_rp_id_default_empty(app, test_client):
    # Unconfigured: data-rp-id renders empty, so the page glue falls back to the
    # origin hostname as the WebAuthn RP ID (self-scoped, non-breaking default).
    with app.app_context():
        response = test_client.get('/signing-key')
        assert response.status_code == httpx.codes.OK
        assert 'data-rp-id=""' in str(response.data)


def test_browser_pages_render_configured_rp_id(app, test_client):
    # A configured canonical RP_ID reaches every passkey page's data-rp-id, so
    # the glue threads it into makePasskey for a federated EGU rpId.
    app.config['RP_ID'] = 'gumption.example'
    with app.app_context():
        for path in ('/signing-key', '/transact', '/advanced'):
            response = test_client.get(path)
            assert response.status_code == httpx.codes.OK
            assert 'data-rp-id="gumption.example"' in str(response.data), path
