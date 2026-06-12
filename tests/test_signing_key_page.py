import httpx


def test_signing_key_page_renders(app, test_client):
    with app.app_context():
        resp = test_client.get('/signing-key')
        assert resp.status_code == httpx.codes.OK
        body = str(resp.data)

        # Prominent security banner: persistence is a trust decision.
        assert 'Persist only on a node you trust' in body
        assert 'never sent to the node' in body

        # No-signing_key section: create + import, each with a passphrase field.
        assert 'id="no-signing_key"' in body
        assert 'id="create-btn"' in body
        assert 'id="create-passphrase"' in body
        assert 'id="import-b58"' in body
        assert 'id="import-passphrase"' in body
        assert 'id="import-btn"' in body
        # .pem is deferred-with-message (like /transact), but the input is here.
        assert 'id="import-pem"' in body
        assert 'id="import-backup-btn"' in body
        assert 'id="import-backup-status"' in body

        # First-persist trust acknowledgment element.
        assert 'id="trust-ack"' in body

        # Has-key section: address, unlock/lock, add passkey, backup, forget.
        assert 'id="has-signing_key"' in body
        assert 'id="signing_key-address"' in body
        assert 'id="unlock-section"' in body
        assert 'id="unlock-passphrase"' in body
        assert 'id="unlock-btn"' in body
        assert 'id="unlock-passkey-btn"' in body
        assert 'id="lock-btn"' in body
        assert 'id="add-passkey-btn"' in body
        assert 'id="backup-btn"' in body
        assert 'id="forget-btn"' in body

        # Glue module is wired in from the blueprint static js dir.
        assert 'js/signing-key-glue.mjs' in body
        assert '/static/gumptionchain/' in body
        # super() in the scripts block keeps base's bundled JS (Bootstrap).
        assert 'bootstrap' in body


def test_signing_key_page_passes_rp_name(app, test_client):
    with app.app_context():
        resp = test_client.get('/signing-key')
        assert resp.status_code == httpx.codes.OK
        # The WebAuthn RP name reaches the page (labels the passkey).
        assert 'data-rp-name="GumptionChain"' in str(resp.data)


def test_signing_key_in_nav(app, test_client):
    with app.app_context():
        resp = test_client.get('/')
        assert resp.status_code == httpx.codes.OK
        body = str(resp.data)
        # Nav link to /signing_key is present (after Transact).
        assert 'href="/signing-key"' in body
        assert '>Signing key</a>' in body
