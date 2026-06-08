import httpx


def test_transact_page_renders(app, test_client):
    with app.app_context():
        resp = test_client.get('/transact')
        assert resp.status_code == httpx.codes.OK
        body = str(resp.data)
        # The type selector and its four kinds are present.
        assert 'id="txn-type"' in body
        assert 'value="transfer"' in body
        assert 'value="opposition"' in body
        assert 'value="support"' in body
        assert 'value="rescind"' in body
        # The security framing must be loud and present.
        assert 'never leaves your browser' in body
        # Build & sign + broadcast + attestation sections.
        assert 'id="broadcast"' in body
        assert 'id="attestation"' in body
        # The attestation section is wired (not a "coming soon" placeholder):
        # it has its inputs and a Sign attestation button.
        assert 'Coming soon' not in body
        assert 'id="att-txid"' in body
        assert 'id="att-kind"' in body
        assert 'id="att-subject"' in body
        assert 'id="att-amount"' in body
        assert 'id="att-sign-btn"' in body
        assert 'Sign attestation' in body
        # The page exposes the node host (gc-sig is node-bound).
        assert 'data-node-host' in body
        # Glue module is wired in from the blueprint static js dir.
        assert 'js/transact-glue.mjs' in body
        assert '/static/gumptionchain/' in body
        # super() in the scripts block keeps base's bundled JS (Bootstrap).
        assert 'bootstrap' in body


def test_transact_page_carries_configured_node_host(app, test_client):
    with app.app_context():
        node_host = app.config['NODE_HOST']
        resp = test_client.get('/transact')
        assert resp.status_code == httpx.codes.OK
        # The exact configured host must reach the page so the glue can sign
        # node-bound gc-sig-v1 requests against this node.
        assert node_host in str(resp.data)
