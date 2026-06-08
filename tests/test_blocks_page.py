def test_blocks_list_empty(test_client):
    resp = test_client.get('/blocks')
    assert resp.status_code == 200
    assert b'No blocks' in resp.data


def test_blocks_list_shows_mined_blocks(
    app, host, mill_block, requests_proxy, wallet
):
    with app.app_context():
        mill_block(wallet)
        _m, b2 = mill_block(wallet)
        tip_hash = b2.block_hash

        resp = app.test_client().get('/blocks')
        assert resp.status_code == 200
        assert tip_hash.encode() in resp.data
        assert b'/block/' in resp.data
