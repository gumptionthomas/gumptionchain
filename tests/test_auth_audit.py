"""Demonstration tests for the API authentication threat-modeled audit.

Each test in this module corresponds to one finding in
docs/superpowers/audits/2026-05-31-api-authentication-audit.md
and is marked @pytest.mark.xfail(strict=True). The xfail demonstrates that
the documented gap exists today; strict=True means that if the test starts
unexpectedly passing (because remediation has been applied), CI fails,
forcing the remediation PR to remove the marker.

To verify each xfail genuinely demonstrates a gap (rather than failing for
an unrelated reason), run:

    uv run pytest --runxfail tests/test_auth_audit.py

That runs the xfail tests as if they were unmarked, surfacing the actual
failure mode.

Finding IDs are referenced in each test's docstring and xfail reason string
in the form A<N>.<letter> matching the audit document's per-adversary
sections.
"""

import json
import time

import httpx
import jwt
import pytest

from cancelchain import create_app
from cancelchain.api import Role
from cancelchain.api_client import ApiClient
from cancelchain.database import db
from cancelchain.miller import Miller
from cancelchain.models import ApiToken


@pytest.mark.xfail(
    strict=True,
    reason=(
        'Audit finding A1.a — severity Low — No startup guard against '
        'weak SECRET_KEY. '
        'See docs/superpowers/audits/2026-05-31-api-authentication-audit.md'
    ),
)
def test_a1_a_weak_secret_key_startup_check():
    """A1.a: create_app() should refuse (RuntimeError) or warn loudly when
    SECRET_KEY is shorter than 32 bytes, preventing silent weak-key deployments
    that allow offline HS256 JWT forgery.

    Today the check does not exist, so create_app() succeeds silently and
    short keys are accepted. Once remediated, this test will PASS.
    """

    # A 7-byte key is below RFC 7518 §3.2's 32-byte minimum for HS256.
    # The application must reject this at startup, not silently accept it.
    with pytest.raises(RuntimeError, match='SECRET_KEY'):
        create_app(
            config_map={
                'TESTING': True,
                'SECRET_KEY': 'tooshrt',  # 7 bytes — dangerously short
                'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
            },
            register_browser=False,
        )


@pytest.mark.xfail(
    strict=True,
    reason=(
        'Audit finding A2.c — severity Medium — unauthenticated GET '
        '/api/token/<address> creates persistent api_token rows for '
        'on-chain addresses with no eviction path. '
        'See docs/superpowers/audits/2026-05-31-api-authentication-audit.md'
    ),
)
def test_a2_c_unauthenticated_row_creation(app, requests_proxy, wallet):
    """An unauthenticated caller must NOT be able to create api_token rows
    for arbitrary on-chain addresses. The endpoint should require
    authentication or at minimum cap / evict rows for addresses that
    never complete the handshake.
    """

    # wallet.address is in app.wallets (app fixture); TokenView.get resolves
    # it without a chain.
    # An unauthenticated attacker issues a GET for the node-wallet address.
    # The address is in app.wallets so TokenView.get resolves it and creates
    # a row in api_token without any authentication.
    r = requests_proxy.get(f'/api/token/{wallet.address}')
    assert r.status_code == 200  # GET itself succeeds today

    with app.app_context():
        row = ApiToken.get(wallet.address)
        # SECURE behaviour: no row should be created by an unauthenticated GET
        # (or at minimum the row should be evicted / cleaned up).
        # Today a persistent row exists immediately after the first GET;
        # this assertion xfails because the row IS created.
        assert row is None, (
            'api_token row was created by an unauthenticated GET; '
            'no eviction mechanism exists'
        )


@pytest.mark.xfail(
    strict=True,
    reason=(
        'Audit finding A2.e — severity Low — POST /api/token/<address> '
        'returns 415 (token row exists) vs 401 (no token row), leaking '
        'address-existence information to unauthenticated callers. '
        'See docs/superpowers/audits/2026-05-31-api-authentication-audit.md'
    ),
)
def test_a2_e_content_type_oracle(app, requests_proxy, wallet):
    """POST /api/token/<address> with wrong Content-Type must return the
    same status code regardless of whether an api_token row exists for
    that address. Today 415 vs 401 leaks row-existence information.
    """

    address = wallet.address

    # Ensure no row exists.
    with app.app_context():
        existing = ApiToken.get(address)
        if existing is not None:
            db.session.delete(existing)
            db.session.commit()

    # POST with wrong Content-Type for address with NO token row.
    r_no_row = requests_proxy.post(
        f'/api/token/{address}',
        content='{"challenge": "x"}',
        # no Content-Type header — Flask will reject as 415 if it reaches
        # request.json; but abort(401) fires first when no row exists.
    )

    # Create a token row via GET.
    requests_proxy.get(f'/api/token/{address}')

    # POST with wrong Content-Type for address WITH a token row.
    r_has_row = requests_proxy.post(
        f'/api/token/{address}',
        content='{"challenge": "x"}',
    )

    # SECURE behaviour: both responses must have the same status code.
    # Today r_no_row.status_code == 401, r_has_row.status_code == 415.
    assert r_no_row.status_code == r_has_row.status_code, (
        f'Status codes differ: no-row={r_no_row.status_code}, '
        f'has-row={r_has_row.status_code}. '
        'This leaks whether an api_token row exists for the address.'
    )


@pytest.mark.xfail(
    strict=True,
    reason=(
        'Audit finding A3.a — severity Medium — authorize() trusts rol JWT '
        'claim without re-validating against Role.address_role(). '
        'See docs/superpowers/audits/2026-05-31-api-authentication-audit.md'
    ),
)
def test_a3_a_forged_role_claim_accepted(
    app, requests_proxy, reader_wallet, mill_block, wallet
):
    """A SECRET_KEY bearer can forge any role for any address.

    reader_wallet is configured as READER only.  We mint a JWT directly
    (bypassing the handshake) that claims rol=MILLER for reader_wallet's
    address and present it to a MILLER-only endpoint.  Secure behaviour:
    authorize() re-checks address_role and returns 403.  Today it returns
    400 (auth passes, rol claim believed; block body {} raises CCError),
    proving the rol claim is blindly trusted.
    """

    with app.app_context():
        # Ensure there is a chain so the endpoint is reachable
        mill_block(wallet)

    secret_key = app.config['SECRET_KEY']
    fake_hash = '0' * 64  # valid 64-char base64, not a real block
    forged_token = jwt.encode(
        {
            'sub': reader_wallet.address,
            'rol': 'MILLER',  # reader_wallet only has READER in config
            'exp': int(time.time()) + 3600,
        },
        secret_key,
        algorithm='HS256',
    )
    response = requests_proxy.post(
        f'/api/block/{fake_hash}',
        headers={
            'Authorization': f'Bearer {forged_token}',
            'Content-Type': 'application/json',
        },
        content=b'{}',
        timeout=10,
    )
    # Secure: auth layer re-checks address_role and rejects with 403.
    # Today: auth passes (rol claim believed), endpoint returns 400
    # (CCError from malformed block body).
    assert response.status_code == httpx.codes.FORBIDDEN


@pytest.mark.xfail(
    strict=True,
    reason=(
        'Audit finding A3.b — severity Medium — JWT lacks iss/aud; '
        'token minted on node A accepted verbatim by node B sharing '
        'SECRET_KEY. '
        'See docs/superpowers/audits/2026-05-31-api-authentication-audit.md'
    ),
)
def test_a3_b_cross_node_token_replay(
    app, remote_app, remote_requests_proxy, wallet, mill_block
):
    """A JWT issued by app (where wallet=ADMIN) is accepted by remote_app
    (where wallet has NO configured role) because both share the same
    SECRET_KEY and the token carries no iss/aud claim.

    remote_app ADDRESSES config (conftest.py:409-423) lists only
    miller_2_wallet as MILLER; wallet is absent from all *_ADDRESSES.
    Secure behaviour: remote_app rejects with 403.  Today it accepts
    (returning 404 — no chain — instead of 403) because rol=ADMIN is
    trusted from the claim.
    """

    with app.app_context():
        mill_block(wallet)

    # Forge (or re-issue) a JWT for wallet.address as ADMIN,
    # signed with the shared TEST_SECRET_KEY.
    secret_key = app.config['SECRET_KEY']  # same value in remote_app
    assert secret_key == remote_app.config['SECRET_KEY'], (
        'Precondition: both nodes share SECRET_KEY'
    )

    cross_node_token = jwt.encode(
        {
            'sub': wallet.address,
            'rol': 'ADMIN',
            'exp': int(time.time()) + 3600,
            # no 'iss', no 'aud'
        },
        secret_key,
        algorithm='HS256',
    )
    # Present the token to remote_app, where wallet has NO role.
    response = remote_requests_proxy.get(
        '/api/block',
        headers={'Authorization': f'Bearer {cross_node_token}'},
        timeout=10,
    )
    # Secure: remote_app re-checks address_role (or enforces aud/iss) and
    # returns 403.  Today: auth passes, returns 404 (no chain on remote_app).
    assert response.status_code == httpx.codes.FORBIDDEN


def test_a4_a_overbroad_admin_regex_does_not_escalate(
    app, host, mill_block, requests_proxy, reader_wallet
):
    """A4.a (remediated): an overbroad ADMIN_ADDRESSES entry does not
    escalate a reader-role wallet.

    Pre-remediation, *_ADDRESSES were regex-matched, so 'CC.*CC' matched
    every valid CC-format address and Role.address_role() returned ADMIN
    for the reader wallet — it received an ADMIN JWT. Now matching is
    exact-membership: 'CC.*CC' is an inert non-matching literal, so the
    reader wallet resolves to READER. (A fresh app configured this way is
    also rejected at startup by the startup-validation test
    test_create_app_rejects_overbroad_admin_config in tests/test_api.py.)
    """
    with app.app_context():
        # Give the reader wallet some chain presence so the token
        # handshake (GET /api/token/<addr>) can resolve the public key.
        mill_block(reader_wallet)

        # An overbroad entry that previously matched every address. Mutated
        # at runtime, so it bypasses startup validation — this exercises
        # the matching defense (the literal no longer matches anything).
        app.config['ADMIN_ADDRESSES'] = ['CC.*CC']

        # Perform the normal challenge/response handshake as reader_wallet.
        client = ApiClient(host, reader_wallet)
        raw_token = client.request_token(rfs=True)
        assert raw_token is not None, (
            'handshake failed — reader wallet not in chain'
        )

        # Decode the token (no signature verification needed — we just
        # want to inspect the rol claim the server minted).
        payload = jwt.decode(
            raw_token,
            options={'verify_signature': False},
            algorithms=['HS256'],
        )

        # Exact-match role allowlists must not honor the overbroad literal:
        # the reader wallet's only legitimate role is READER.
        awarded_role = Role[payload['rol']]
        assert awarded_role.value <= Role.READER.value, (
            f'reader_wallet was awarded {awarded_role.name!r}; exact-match '
            'role allowlists must not honor the overbroad literal CC.*CC'
        )


@pytest.mark.xfail(
    strict=True,
    reason=(
        'Audit finding A5.b — severity Medium — JWT rol claim not '
        're-validated against live config; a token issued with MILLER '
        'role continues to be accepted after the address is removed '
        'from MILLER_ADDRESSES, until the 4-hour JWT expires. '
        'See docs/superpowers/audits/2026-05-31-api-authentication-audit.md'
    ),
)
def test_a5_b_stale_role_rejected_after_config_revocation(
    app, host, mill_block, requests_proxy, miller_wallet
):
    """A JWT issued with role MILLER must be rejected after the address
    is removed from MILLER_ADDRESSES config, even if the token has not expired.

    Secure behaviour: authorize() re-validates the role against live config
    and rejects a request whose live role is lower than the token's claimed rol.

    Today's behaviour: the rol claim is trusted as-is, so the token continues
    to grant MILLER access for the full 4-hour window.
    """

    with app.app_context():
        # Establish a chain so the MILLER endpoint has something to POST to.
        mill_block(miller_wallet)

        # Obtain a JWT for miller_wallet while it holds the MILLER role.
        client = ApiClient(host, miller_wallet)
        # This succeeds: miller_wallet is in MILLER_ADDRESSES.
        r = client.get('/api/block')
        assert r.status_code == httpx.codes.OK

        # Capture the issued token.
        miller_token = client.token
        assert miller_token is not None

        # Revoke MILLER role from config (simulates operator removing address).
        original_miller_addresses = app.config['MILLER_ADDRESSES']
        app.config['MILLER_ADDRESSES'] = []

        try:
            # The MILLER-protected POST /api/block/<hash> endpoint requires
            # at least MILLER role.  With the live config now empty, a freshly
            # issued token would get role=None (403).  The stale token should
            # also be rejected — but today it is not.
            m2 = Miller(milling_wallet=miller_wallet)
            b = m2.create_block()
            m2.mill_block(b)

            headers = {
                'Authorization': f'Bearer {miller_token}',
                'Content-Type': 'application/json',
            }
            r2 = requests_proxy.post(
                f'/api/block/{b.block_hash}',
                content=b.to_json(),
                headers=headers,
                timeout=30,
            )
            # Secure behaviour: the now-revoked role must cause a 401/403.
            assert r2.status_code in (
                httpx.codes.UNAUTHORIZED,
                httpx.codes.FORBIDDEN,
            ), (
                f'Expected 401 or 403 after role revocation, '
                f'got {r2.status_code}'
            )
        finally:
            app.config['MILLER_ADDRESSES'] = original_miller_addresses


@pytest.mark.xfail(
    strict=True,
    reason=(
        'Audit finding A7.a — severity Medium — unlimited wrong-challenge '
        'POSTs each run full argon2id verify with no attempt counter or '
        'token invalidation. '
        'See docs/superpowers/audits/2026-05-31-api-authentication-audit.md'
    ),
)
def test_a7_a_repeated_wrong_challenge_invalidates_token(
    app, requests_proxy, wallet
):
    """After repeated wrong challenge submissions the ApiToken must be
    invalidated (reset) so that further wrong-challenge POSTs return 401
    WITHOUT running another argon2id verify.

    Secure behaviour: the third (or N-th, for any small N) wrong-challenge
    POST causes the row's hashed/cipher to be cleared, meaning a subsequent
    POST with a wrong challenge hits the `not self.hashed` guard and returns
    False before executing _PASSWORD_HASHER.verify(). We assert this by
    confirming that after the threshold is exceeded the ApiToken row has been
    reset (hashed is None) — proving the expensive verify path is no longer
    reachable.

    Today this xfails because no attempt counter exists: the row is never
    reset on wrong-challenge POSTs, so hashed remains set indefinitely.
    """

    path = f'/api/token/{wallet.address}'
    headers = {'Content-Type': 'application/json'}

    # Step 1: issue GET to create/refresh the ApiToken row.
    r = requests_proxy.get(path, timeout=60)
    assert r.status_code == httpx.codes.OK

    # Step 2: POST with wrong challenge strings repeatedly (3 times).
    for _ in range(3):
        r = requests_proxy.post(
            path,
            content=json.dumps({'challenge': 'definitely-wrong-secret'}),
            headers=headers,
            timeout=60,
        )
        assert r.status_code == httpx.codes.UNAUTHORIZED

    # Secure behaviour: after 3 failed attempts the token is invalidated.
    # hashed must be None so the next verify() call returns False immediately
    # without running argon2 — the expensive path is no longer reachable.
    with app.app_context():
        token_row = ApiToken.get(wallet.address)
        assert token_row is not None
        # This is the key assertion: hashed should be cleared (None) after
        # N wrong-challenge attempts, proving the argon2 path is bypassed.
        assert token_row.hashed is None, (
            'Expected ApiToken.hashed to be None after repeated '
            'wrong-challenge POSTs (token should be reset to prevent '
            'argon2 amplification), but hashed is still set — '
            'no attempt counter/invalidation exists.'
        )
