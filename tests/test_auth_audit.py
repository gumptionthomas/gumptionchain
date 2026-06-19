"""Regression tests for the API authentication threat-modeled audit.

Each surviving test corresponds to a finding from the API authentication
audit, remediated by the per-request signing_key-signature protocol
(gc-sig-v1). The
token-endpoint and symmetric-key findings (A1.a/A2.c/A2.e/A7.a) were
dissolved by the protocol replacement (no /api/token endpoint, no ApiToken
table, no argon2, no SECRET_KEY-as-auth) and their demonstrations are
removed. The survivors (A3.a/A3.b/A4.a/A5.b) are re-expressed against signed
requests; they are passing regressions, not xfails.

Finding IDs are referenced in each test's docstring in the form A<N>.<letter>
matching the audit document's per-adversary sections.
"""

import httpx
import pytest

from gumptionchain import signing
from gumptionchain.api import Role
from gumptionchain.api_client import ApiClient
from gumptionchain.exceptions import InvalidRoleConfigError
from gumptionchain.miller import Miller
from gumptionchain.util import host_address


def _node(host):
    return host_address(host)[0]


def test_a3_a_forged_role_claim_not_honored(
    app, host, requests_proxy, reader_signing_key, mill_block
):
    """A3.a (remediated): an over-claimed role is not honored.

    reader_signing_key is configured READER only. It signs a request to a
    MILLER-only endpoint (POST /api/block/<hash>). authorize() verifies the
    signature, then re-checks Role.address_role(reader)=READER < MILLER and
    returns 403 — the caller cannot escalate beyond its live role.
    """
    with app.app_context():
        mill_block(reader_signing_key)
        fake_hash = '0' * 64  # valid 64-char base64, not a real block
        path = f'/api/block/{fake_hash}'
        body = b'{}'
        headers = signing.sign_headers(
            reader_signing_key,
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
    app,
    host,
    remote_app,
    remote_requests_proxy,
    mill_block,
    miller_2_signing_key,
):
    """A3.b (remediated): a signature bound to node A is rejected by node B.

    The request is signed for `app`'s node_host (http://localhost:8080) but
    sent to `remote_app` (NODE_HOST http://peer.node:8888). remote_app
    reconstructs node_host from its own config, so the node-binding in the
    canonical string fails -> 401, even though miller_2_signing_key would
    otherwise be authorized there.
    """
    with remote_app.app_context():
        # miller_2_signing_key is MILLER on remote_app; sign for the WRONG node.
        headers = signing.sign_headers(
            miller_2_signing_key,
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
    app, host, mill_block, requests_proxy, reader_signing_key
):
    """A4.a (remediated): an overbroad ADMIN_ADDRESSES entry does not
    escalate a reader-role signing_key at request time.

    Pre-remediation, *_ADDRESSES were regex-matched, so 'CC.*CC' matched
    every valid CC-format address and a reader was awarded ADMIN. Now
    matching is exact-membership: 'CC.*CC' is an inert non-matching literal.
    A validly-signed request from reader_signing_key (READER only) to a MILLER
    endpoint is forbidden (403). (The startup-rejection aspect is covered by
    test_create_app_rejects_overbroad_admin_config in tests/test_api.py.)
    """
    with app.app_context():
        _m, b = mill_block(reader_signing_key)
        # Overbroad literal, mutated at runtime to bypass startup validation.
        app.config['ADMIN_ADDRESSES'] = ['CC.*CC']
        path = f'/api/block/{b.block_hash}'
        body = b.to_json()
        body_bytes = body.encode() if isinstance(body, str) else body
        headers = signing.sign_headers(
            reader_signing_key,
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
    app, host, mill_block, requests_proxy, miller_signing_key
):
    """A5.b (remediated): role is re-validated against live config on every
    request, so a revoked address loses access immediately.

    A MILLER-signed request succeeds; then MILLER_ADDRESSES is emptied and a
    fresh MILLER-signed request is forbidden (live role now None) -> 403.
    """
    with app.app_context():
        mill_block(miller_signing_key)
        client = ApiClient(host, miller_signing_key)
        # miller_signing_key is in MILLER_ADDRESSES: reads succeed.
        r = client.get('/api/block')
        assert r.status_code == httpx.codes.OK

        # Revoke MILLER role from config.
        original_miller_addresses = app.config['MILLER_ADDRESSES']
        app.config['MILLER_ADDRESSES'] = []
        try:
            m2 = Miller(milling_signing_key=miller_signing_key)
            b = m2.create_block()
            m2.mill_block(b)
            path = f'/api/block/{b.block_hash}'
            body = b.to_json()
            body_bytes = body.encode() if isinstance(body, str) else body
            headers = signing.sign_headers(
                miller_signing_key,
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


def test_validate_config_allows_wildcard_in_transactor():
    # Should not raise.
    Role.validate_config({'TRANSACTOR_ADDRESSES': ['*']})


def test_validate_config_allows_wildcard_in_reader():
    Role.validate_config({'READER_ADDRESSES': ['*']})


def test_validate_config_rejects_wildcard_in_miller():
    with pytest.raises(InvalidRoleConfigError):
        Role.validate_config({'MILLER_ADDRESSES': ['*']})


def test_validate_config_rejects_wildcard_in_admin():
    with pytest.raises(InvalidRoleConfigError):
        Role.validate_config({'ADMIN_ADDRESSES': ['*']})


def test_wildcard_transactor_grants_transactor_role(app):
    with app.app_context():
        app.config['TRANSACTOR_ADDRESSES'] = ['*']
        assert Role.address_role('not-a-listed-address') is Role.TRANSACTOR


def test_no_wildcard_unlisted_is_not_transactor(app):
    with app.app_context():
        app.config['TRANSACTOR_ADDRESSES'] = []
        assert Role.address_role('not-a-listed-address') is not Role.TRANSACTOR


def test_wildcard_not_honored_for_miller_at_match_time(app):
    # Defense-in-depth: a runtime-mutated MILLER "*" must NOT grant MILLER.
    with app.app_context():
        app.config['MILLER_ADDRESSES'] = ['*']
        assert Role.MILLER not in Role.address_roles('not-a-listed-address')


def test_wildcard_not_honored_for_admin_at_match_time(app):
    # Defense-in-depth: a runtime-mutated ADMIN "*" must NOT grant ADMIN.
    with app.app_context():
        app.config['ADMIN_ADDRESSES'] = ['*']
        assert Role.ADMIN not in Role.address_roles('not-a-listed-address')


def test_wildcard_transactor_authorizes_arbitrary_signing_key(
    app, host, requests_proxy, reader_signing_key, mill_block
):
    """End-to-end: wildcard TRANSACTOR_ADDRESSES grants access through
    the real authorize_transactor decorator.

    reader_signing_key is configured READER-only (not in TRANSACTOR_ADDRESSES).
    Without the wildcard it is 403 at a transactor-gated GET endpoint;
    with TRANSACTOR_ADDRESSES=['*'] the same signed request gets past auth
    (the endpoint may still 400 on missing query params, but NOT 403).
    """
    with app.app_context():
        mill_block(reader_signing_key)
        path = '/api/transaction/opposition'
        # Without wildcard: reader_signing_key has no TRANSACTOR role -> 403.
        app.config['TRANSACTOR_ADDRESSES'] = []
        headers = signing.sign_headers(
            reader_signing_key,
            method='GET',
            path=path,
            query='',
            body=b'',
            node_host=_node(host),
        )
        denied = requests_proxy.get(path, headers=headers, timeout=60)
        assert denied.status_code == httpx.codes.FORBIDDEN
        # With the wildcard: any authenticated signing_key -> TRANSACTOR.
        # The endpoint may 400 on missing query params; that still proves
        # authorize_transactor did NOT reject the request (not 403).
        app.config['TRANSACTOR_ADDRESSES'] = ['*']
        headers = signing.sign_headers(
            reader_signing_key,
            method='GET',
            path=path,
            query='',
            body=b'',
            node_host=_node(host),
        )
        allowed = requests_proxy.get(path, headers=headers, timeout=60)
        assert allowed.status_code != httpx.codes.FORBIDDEN
