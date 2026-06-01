"""Regression tests for the API authentication threat-modeled audit.

Each surviving test corresponds to a finding in
docs/superpowers/audits/2026-05-31-api-authentication-audit.md that is
remediated by the per-request wallet-signature protocol (cc-sig-v1). The
token-endpoint and symmetric-key findings (A1.a/A2.c/A2.e/A7.a) were
dissolved by the protocol replacement (no /api/token endpoint, no ApiToken
table, no argon2, no SECRET_KEY-as-auth) and their demonstrations are
removed. The survivors (A3.a/A3.b/A4.a/A5.b) are re-expressed against signed
requests; they are passing regressions, not xfails.

Finding IDs are referenced in each test's docstring in the form A<N>.<letter>
matching the audit document's per-adversary sections.
"""

import httpx

from cancelchain import signing
from cancelchain.api_client import ApiClient
from cancelchain.miller import Miller
from cancelchain.util import host_address


def _node(host):
    return host_address(host)[0]


def test_a3_a_forged_role_claim_not_honored(
    app, host, requests_proxy, reader_wallet, mill_block
):
    """A3.a (remediated): an over-claimed role is not honored.

    reader_wallet is configured READER only. It validly signs a request to a
    MILLER-only endpoint (POST /api/block/<hash>). authorize() verifies the
    signature, then re-checks Role.address_role(reader)=READER < MILLER and
    returns 403 — the caller cannot escalate beyond its live role.
    """
    with app.app_context():
        mill_block(reader_wallet)
        fake_hash = '0' * 64  # valid 64-char base64, not a real block
        path = f'/api/block/{fake_hash}'
        body = b'{}'
        headers = signing.sign_headers(
            reader_wallet,
            method='POST',
            path=path,
            query='',
            body=body,
            node_host=_node(host),
        )
        headers['Content-Type'] = 'application/json'
        response = requests_proxy.post(
            path, headers=headers, content=body, timeout=10
        )
        assert response.status_code == httpx.codes.FORBIDDEN


def test_a3_b_cross_node_signature_rejected(
    app, host, remote_app, remote_requests_proxy, mill_block, miller_2_wallet
):
    """A3.b (remediated): a signature bound to node A is rejected by node B.

    The request is signed for `app`'s node_host (http://localhost:8080) but
    sent to `remote_app` (NODE_HOST http://peer.node:8888). remote_app
    reconstructs node_host from its own config, so the node-binding in the
    canonical string fails -> 401, even though miller_2_wallet would
    otherwise be authorized there.
    """
    with remote_app.app_context():
        # miller_2_wallet is MILLER on remote_app; sign for the WRONG node.
        headers = signing.sign_headers(
            miller_2_wallet,
            method='GET',
            path='/api/block',
            query='',
            body=b'',
            node_host=_node(host),  # app's node_host, not remote_app's
        )
        response = remote_requests_proxy.get(
            '/api/block', headers=headers, timeout=10
        )
        assert response.status_code == httpx.codes.UNAUTHORIZED


def test_a4_a_overbroad_admin_literal_does_not_escalate(
    app, host, mill_block, requests_proxy, reader_wallet
):
    """A4.a (remediated): an overbroad ADMIN_ADDRESSES entry does not
    escalate a reader-role wallet at request time.

    Pre-remediation, *_ADDRESSES were regex-matched, so 'CC.*CC' matched
    every valid CC-format address and a reader was awarded ADMIN. Now
    matching is exact-membership: 'CC.*CC' is an inert non-matching literal.
    A validly-signed request from reader_wallet (READER only) to a MILLER
    endpoint is forbidden (403). (The startup-rejection aspect is covered by
    test_create_app_rejects_overbroad_admin_config in tests/test_api.py.)
    """
    with app.app_context():
        _m, b = mill_block(reader_wallet)
        # Overbroad literal, mutated at runtime to bypass startup validation.
        app.config['ADMIN_ADDRESSES'] = ['CC.*CC']
        path = f'/api/block/{b.block_hash}'
        body = b.to_json()
        body_bytes = body.encode() if isinstance(body, str) else body
        headers = signing.sign_headers(
            reader_wallet,
            method='POST',
            path=path,
            query='',
            body=body_bytes,
            node_host=_node(host),
        )
        headers['Content-Type'] = 'application/json'
        response = requests_proxy.post(
            path, headers=headers, content=body, timeout=10
        )
        # The overbroad literal must not escalate reader -> ADMIN/MILLER.
        assert response.status_code == httpx.codes.FORBIDDEN


def test_a5_b_stale_role_rejected_after_config_revocation(
    app, host, mill_block, requests_proxy, miller_wallet
):
    """A5.b (remediated): role is re-validated against live config on every
    request, so a revoked address loses access immediately.

    A MILLER-signed request succeeds; then MILLER_ADDRESSES is emptied and a
    fresh MILLER-signed request is forbidden (live role now None) -> 403.
    """
    with app.app_context():
        mill_block(miller_wallet)
        client = ApiClient(host, miller_wallet)
        # miller_wallet is in MILLER_ADDRESSES: reads succeed.
        r = client.get('/api/block')
        assert r.status_code == httpx.codes.OK

        # Revoke MILLER role from config.
        original_miller_addresses = app.config['MILLER_ADDRESSES']
        app.config['MILLER_ADDRESSES'] = []
        try:
            m2 = Miller(milling_wallet=miller_wallet)
            b = m2.create_block()
            m2.mill_block(b)
            path = f'/api/block/{b.block_hash}'
            body = b.to_json()
            body_bytes = body.encode() if isinstance(body, str) else body
            headers = signing.sign_headers(
                miller_wallet,
                method='POST',
                path=path,
                query='',
                body=body_bytes,
                node_host=_node(host),
            )
            headers['Content-Type'] = 'application/json'
            r2 = requests_proxy.post(
                path, headers=headers, content=body, timeout=30
            )
            assert r2.status_code == httpx.codes.FORBIDDEN
        finally:
            app.config['MILLER_ADDRESSES'] = original_miller_addresses
