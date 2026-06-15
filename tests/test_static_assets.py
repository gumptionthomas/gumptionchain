from flask import Flask

from gumptionchain import static_assets_blueprint


def test_static_assets_blueprint_serves_a_signing_key_module():
    # A consumer that embeds the package but does NOT register the full
    # browser blueprint can still serve base's ESM modules.
    app = Flask(__name__)
    app.register_blueprint(static_assets_blueprint())
    client = app.test_client()

    resp = client.get('/static/gumptionchain/signing-key/gc-keyring.mjs')
    assert resp.status_code == 200
    assert b'export' in resp.data  # it served the real module, not a 404 page


def test_static_assets_blueprint_url_path_is_overridable():
    app = Flask(__name__)
    app.register_blueprint(static_assets_blueprint(url_path='/assets/gc'))
    client = app.test_client()

    mounted = client.get('/assets/gc/signing-key/gc-keyring.mjs')
    assert mounted.status_code == 200
    default = client.get('/static/gumptionchain/signing-key/gc-keyring.mjs')
    assert default.status_code == 404
