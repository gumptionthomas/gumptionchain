import httpx


def test_verify_page_renders(app, test_client):
    with app.app_context():
        resp = test_client.get('/verify')
        assert resp.status_code == httpx.codes.OK
        body = str(resp.data)
        assert 'proof-input' in body  # the textarea is present
        assert 'verify-glue.mjs' in body  # glue module is wired in
        assert '/static/gumptionchain/' in body  # served from blueprint static
        # The verdict elements renderVerdict() drives must be in the markup.
        assert 'data-check="signature"' in body
        assert 'verdict-seal' in body
        assert 'verdict-reasons' in body
        # The verdict mark uses a project-functional class, not a design-system
        # one — base stays free of the proprietary hub vocabulary. The hub's CSS
        # paints `.verdict-mark` (themed) at serve time via its seam.
        assert 'verdict-mark' in body
        assert 'seal-dot' not in body
        # super() in the scripts block must keep base's bundled JS (Bootstrap).
        assert 'bootstrap' in body


def test_verify_page_links_to_the_attestation_signer(app, test_client):
    with app.app_context():
        resp = test_client.get('/verify')
        assert resp.status_code == httpx.codes.OK
        body = str(resp.data)
        # /verify points producers at the signer on /transact#attestation.
        assert '/transact#attestation' in body
