from flask import Flask

from gumptionchain import static_assets_blueprint


def test_static_assets_blueprint_serves_a_signing_key_module():
    # A consumer that embeds the package but does NOT register the full
    # browser blueprint can still serve base's ESM modules.
    app = Flask(__name__)
    app.register_blueprint(static_assets_blueprint())
    client = app.test_client()

    resp = client.get('/static/gumptionchain/sdk/gc-keyring.mjs')
    assert resp.status_code == 200
    assert b'export' in resp.data  # it served the real module, not a 404 page


def test_served_barrel_is_the_stable_import_surface():
    # The served barrel (index.mjs) is THE documented stable import surface for
    # member apps consuming base's SDK over HTTP. A rename/move that breaks this
    # served path — or a wrong MIME that breaks ESM `import` — must fail base CI
    # HERE, not silently at a consumer's deploy/runtime (the egu-352 404 trap).
    app = Flask(__name__)
    app.register_blueprint(static_assets_blueprint())
    client = app.test_client()

    resp = client.get('/static/gumptionchain/sdk/index.mjs')
    assert resp.status_code == 200
    # A JS MIME type is required for the browser to evaluate it as an ES module.
    assert 'javascript' in resp.headers.get('Content-Type', '').lower()
    body = resp.get_data(as_text=True)
    # It's the public-API barrel (carries the embedder-API version +
    # re-exports), not a 404 page or some other file.
    assert 'export const version' in body
    assert 'SigningKey' in body


def test_static_assets_blueprint_url_path_is_overridable():
    app = Flask(__name__)
    app.register_blueprint(static_assets_blueprint(url_path='/assets/gc'))
    client = app.test_client()

    mounted = client.get('/assets/gc/sdk/gc-keyring.mjs')
    assert mounted.status_code == 200
    default = client.get('/static/gumptionchain/sdk/gc-keyring.mjs')
    assert default.status_code == 404
