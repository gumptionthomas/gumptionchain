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
        # The power tools live on /advanced now (#260) — covered by
        # tests/test_advanced_page.py.
        assert 'Coming soon' not in body
        # The page exposes the node host (gc-sig is node-bound).
        assert 'data-node-host' in body
        # Glue module is wired in from the blueprint static js dir.
        assert 'js/transact-glue.mjs' in body
        assert '/static/gumptionchain/' in body
        # super() in the scripts block keeps base's bundled JS (Bootstrap).
        assert 'bootstrap' in body


def test_transact_page_key_panel_states(app, test_client):
    with app.app_context():
        body = str(test_client.get('/transact').data)
        # The three-state key panel (#262): exactly one state is
        # shown by JS; all ship in markup.
        assert 'data-key-state="none"' in body
        assert 'data-key-state="locked"' in body
        assert 'data-key-state="unlocked"' in body
        # Inline mini-create (the conversion moment).
        assert 'id="key-create-passphrase"' in body
        assert 'id="key-trust-ack"' in body
        assert 'id="key-create-btn"' in body
        assert 'Create your signing key' in body
        # Explicit unlock state.
        assert 'id="unlock-passphrase"' in body
        assert 'id="unlock-saved-btn"' in body
        # Unlocked state: badge + explicit lock.
        assert 'id="key-badge"' in body
        assert 'id="key-lock-btn"' in body
        # One-session key collapsed under Advanced.
        assert 'id="session-key"' in body
        assert 'class="collapse' in body
        assert 'one-session key' in body
        assert 'id="key-b58"' in body
        assert 'id="import-key-btn"' in body
        # Key-first copy; self-explanatory, no glossary needed.
        assert 'Create your signing key' in body


def test_transact_actions_disabled_until_unlocked(app, test_client):
    with app.app_context():
        body = str(test_client.get('/transact').data)
        # Markup-level gating: build/confirm ship disabled; the glue
        # enables them only in the unlocked state.
        assert 'id="build-review-btn"' in body
        assert 'disabled' in body.split('id="build-review-btn"')[1][:120]
        assert 'disabled' in body.split('id="confirm-submit-btn"')[1][:120]


def test_transact_page_carries_configured_node_host(app, test_client):
    with app.app_context():
        node_host = app.config['NODE_HOST']
        resp = test_client.get('/transact')
        assert resp.status_code == httpx.codes.OK
        # The exact configured host must reach the page so the glue can sign
        # node-bound gc-sig-v1 requests against this node.
        assert node_host in str(resp.data)
