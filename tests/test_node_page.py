import httpx


def test_node_page_empty_db(app, test_client):
    # Renders without a chain: identity/mempool/peers still show; no 500.
    with app.app_context():
        resp = test_client.get('/node')
        assert resp.status_code == httpx.codes.OK
        body = resp.get_data(as_text=True)
        assert app.config['NODE_HOST'] in body
        assert 'No chain yet' in body


def test_node_page_with_chain_shows_health(
    app, mill_block, test_client, signing_key
):
    with app.app_context():
        _m, b = mill_block(signing_key)
        resp = test_client.get('/node')
        assert resp.status_code == httpx.codes.OK
        body = resp.get_data(as_text=True)
        assert b.block_hash in body  # tip hash
        assert 'Mempool' in body


def test_node_page_miller_section(
    app, mill_block, test_client, miller_signing_key, signing_key
):
    with app.app_context():
        # Mill a block to the MILLER key so it has produced + earned.
        mill_block(miller_signing_key)
        body = test_client.get('/node').get_data(as_text=True)
        assert 'Miller' in body
        # the MILLER key appears in a miller row
        assert f'data-miller-address="{miller_signing_key.address}"' in body
        # a non-MILLER loaded key (the ADMIN signing_key) is NOT a miller row
        assert f'data-miller-address="{signing_key.address}"' not in body


def test_node_page_peers_show_host_only(app, test_client):
    # PEERS are http://<addr>@<host>; the open page must show the host, never
    # the addr@ userinfo (which would reveal the local per-peer signing key).
    app.config['PEERS'] = ['http://gc1abc@peer.example:8080']
    with app.app_context():
        body = test_client.get('/node').get_data(as_text=True)
        assert 'peer.example' in body
        assert 'gc1abc@' not in body
