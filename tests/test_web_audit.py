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

import pytest


@pytest.mark.xfail(
    strict=True,
    reason='WEB1: no response-hardening headers (CSP/X-Frame-Options/'
    'X-Content-Type-Options/Referrer-Policy) are wired yet.',
)
def test_web1_security_headers_present(app, test_client):
    """WEB1 (Low) — HTML responses ship no security-hardening headers
    (`application.py` wires no `after_request`/Talisman). Desired: every HTML
    response carries CSP, X-Frame-Options, X-Content-Type-Options, and
    Referrer-Policy, and (on HTTPS) HSTS. Issued over an https base_url so
    HSTS — which should only be set on secure requests — is exercised
    alongside the always-on headers. Served entirely in-process — no external
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


@pytest.mark.xfail(
    strict=True,
    reason='WEB2: browser views `return e` (a raw Exception), which is not a '
    'valid Flask response; flips once they return abort(500) / a real '
    'response.',
)
def test_web2_view_error_yields_clean_500(app, test_client, monkeypatch):
    """WEB2 (Low) — when a view's business logic raises an unexpected
    exception, the generic handler does `return e` (`browser.py`), returning a
    raw `Exception` that is not a valid Flask response. Desired: the view
    yields a clean 500 response (via `abort(500)`) with no internal detail in
    the body. A sentinel-bearing exception is injected via the `longest_chain`
    helper used by `index_view`.
    """

    def boom():
        msg = 'web2-sentinel-internal-detail'
        raise ValueError(msg)

    monkeypatch.setattr('cancelchain.browser.longest_chain', boom)
    with app.app_context():
        resp = test_client.get('/')
    assert resp.status_code == 500
    assert b'web2-sentinel' not in resp.data
