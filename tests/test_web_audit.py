"""Demonstration tests for the 2026-06-02 web / browser-UI audit.

Each test demonstrates one finding and asserts the DESIRED post-fix
behavior. While a finding is open it carries
``@pytest.mark.xfail(strict=True)``; once remediated the marker is dropped
and it becomes a passing regression (tests below may be a mix). Tests drive
routes via the Flask test client and assert on response headers/body; no
test makes a real external network request. See
docs/superpowers/audits/2026-06-02-web-audit.md.

The `app`, `test_client` fixtures come from tests/conftest.py; route
invocation mirrors tests/test_browser.py.
"""


def test_web1_security_headers_present(app, test_client):
    """WEB1 (Low) — REMEDIATED. HTML responses used to ship no
    security-hardening headers; an `@app.after_request` hook
    (`application.py` `set_security_headers`) now sets CSP, X-Frame-Options,
    X-Content-Type-Options, Referrer-Policy, and HSTS (set unconditionally —
    browsers honor it only over HTTPS, but emitting it always keeps it working
    behind a TLS-terminating proxy). Served entirely in-process — no external
    network.
    """
    with app.app_context():
        resp = test_client.get('/', base_url='https://localhost')
    assert resp.status_code == 200
    assert 'Content-Security-Policy' in resp.headers
    assert resp.headers.get('X-Content-Type-Options') == 'nosniff'
    assert resp.headers.get('X-Frame-Options') in ('DENY', 'SAMEORIGIN')
    assert 'Referrer-Policy' in resp.headers
    assert 'Strict-Transport-Security' in resp.headers


def test_web2_view_error_yields_clean_500(app, test_client, monkeypatch):
    """WEB2 (Low) — REMEDIATED. The browser views' generic handler used to do
    `return e`, returning a raw `Exception` that is not a valid Flask response
    (`make_response` TypeError). It now logs the traceback and `abort(500)`s,
    yielding a clean 500 response with no internal detail in the body. A
    sentinel-bearing exception is injected via the `longest_chain` helper used
    by `index_view`; this regression asserts the controlled 500 with no leak.
    """

    def boom():
        msg = 'web2-sentinel-internal-detail'
        raise ValueError(msg)

    monkeypatch.setattr('cancelchain.browser.longest_chain', boom)
    with app.app_context():
        resp = test_client.get('/')
    assert resp.status_code == 500
    assert b'web2-sentinel' not in resp.data
