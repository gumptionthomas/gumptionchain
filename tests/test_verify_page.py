import httpx


def test_verify_page_renders(app, test_client):
    with app.app_context():
        resp = test_client.get('/verify')
        assert resp.status_code == httpx.codes.OK
        body = str(resp.data)
        assert 'proof-input' in body  # the textarea is present
        assert 'verify-glue.mjs' in body  # glue module is wired in
        assert '/static/gumptionchain/' in body  # served from blueprint static
