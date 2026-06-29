import httpx


def test_node_page_empty_db(app, test_client):
    # Renders without a chain: identity/mempool/peers still show; no 500.
    with app.app_context():
        resp = test_client.get('/node')
        assert resp.status_code == httpx.codes.OK
        body = resp.get_data(as_text=True)
        assert app.config['NODE_HOST'] in body
        assert 'No chain yet' in body


def test_node_page_with_chain_shows_cadence(
    app, mill_block, test_client, signing_key
):
    # The trimmed chain section carries only what /chains + /blocks don't:
    # block cadence (target block time + last-block age/stale). Tip
    # height/hash/difficulty live on those explorer pages, not here.
    with app.app_context():
        mill_block(signing_key)
        resp = test_client.get('/node')
        assert resp.status_code == httpx.codes.OK
        body = resp.get_data(as_text=True)
        assert 'Block cadence' in body
        assert 'Target block time' in body
        assert 's ago' in body  # last-block age rendered


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


def test_node_page_fresh_block_not_stale(
    app, mill_block, test_client, signing_key
):
    with app.app_context():
        mill_block(signing_key)
        body = test_client.get('/node').get_data(as_text=True)
        assert 's ago' in body  # age rendered
        assert 'badge bg-warning' not in body  # a fresh tip is not stale


def test_node_dashboard_links_to_miller_page(
    app, mill_block, test_client, miller_signing_key
):
    with app.app_context():
        mill_block(miller_signing_key)
        body = test_client.get('/node').get_data(as_text=True)
        assert f'/node/miller/{miller_signing_key.address}' in body


def test_miller_view_lists_milled_blocks(
    app, mill_block, test_client, miller_signing_key
):
    with app.app_context():
        _m, b1 = mill_block(miller_signing_key)
        _m, b2 = mill_block(miller_signing_key)
        resp = test_client.get(f'/node/miller/{miller_signing_key.address}')
        assert resp.status_code == httpx.codes.OK
        body = resp.get_data(as_text=True)
        assert miller_signing_key.address in body
        for block in (b1, b2):
            assert block.block_hash in body
            assert f'/block/{block.block_hash}' in body


def test_miller_view_empty_state(app, test_client, miller_signing_key):
    # A well-formed address that has milled nothing → 200 + empty state.
    with app.app_context():
        resp = test_client.get(f'/node/miller/{miller_signing_key.address}')
        assert resp.status_code == httpx.codes.OK
        body = resp.get_data(as_text=True)
        assert 'This address has milled no canonical blocks.' in body


def test_miller_view_malformed_address_404(app, test_client):
    with app.app_context():
        resp = test_client.get('/node/miller/not-an-address')
        assert resp.status_code == httpx.codes.NOT_FOUND


def test_node_page_peers_show_host_only(app, test_client):
    # PEERS are http://<addr>@<host>; the open page must show the host, never
    # the addr@ userinfo (which would reveal the local per-peer signing key).
    app.config['PEERS'] = ['http://gc1abc@peer.example:8080']
    with app.app_context():
        body = test_client.get('/node').get_data(as_text=True)
        assert 'peer.example' in body
        assert 'gc1abc@' not in body
