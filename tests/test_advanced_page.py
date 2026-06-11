from pathlib import Path

import httpx


def test_advanced_page_renders(app, test_client):
    with app.app_context():
        resp = test_client.get('/advanced')
        assert resp.status_code == httpx.codes.OK
        body = str(resp.data)
        # The two relocated power-user sections (#260).
        assert 'id="broadcast"' in body
        assert 'id="attestation"' in body
        assert 'id="broadcast-input"' in body
        assert 'id="broadcast-btn"' in body
        assert 'id="att-txid"' in body
        assert 'id="att-kind"' in body
        assert 'id="att-subject"' in body
        assert 'id="att-amount"' in body
        assert 'id="att-sign-btn"' in body
        # Both sections need a signing key: the shared key area is here.
        assert 'id="saved-wallet"' in body
        assert 'id="key-b58"' in body
        assert 'id="import-key-btn"' in body
        # The security framing travels with the key area.
        assert 'never leaves your browser' in body
        # Verify is presented as an advanced tool (link card, own route).
        assert '/verify' in body
        # gc-sig is node-bound; the glue signs for this node.
        assert 'data-node-host' in body
        assert 'js/transact-glue.mjs' in body
        # super() in the scripts block keeps base's bundled JS.
        assert 'bootstrap' in body


def test_advanced_page_has_seam_hook():
    # The hook is an optional include and base ships no such file, so
    # the rendered page can't show it — assert via the template source.
    template = (
        Path(__file__).parent.parent
        / 'src'
        / 'gumptionchain'
        / 'templates'
        / 'advanced.html'
    ).read_text()
    assert '{% include "advanced/extra.html" ignore missing %}' in template


def test_transact_page_no_longer_carries_advanced_sections(app, test_client):
    with app.app_context():
        body = str(test_client.get('/transact').data)
        assert 'id="broadcast"' not in body
        assert 'id="attestation"' not in body
        assert 'id="att-sign-btn"' not in body
        # The build & sign flow and its key area remain.
        assert 'id="txn-type"' in body
        assert 'id="key-b58"' in body
        # A quiet pointer to the relocated tools.
        assert '/advanced' in body
        assert 'Advanced tools' in body


def test_nav_links_advanced(app, test_client):
    with app.app_context():
        body = str(test_client.get('/advanced').data)
        assert '>Advanced</a>' in body
