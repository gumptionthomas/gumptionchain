from datetime import timedelta

import httpx
import pytest

from cancelchain import create_app, signing
from cancelchain.api import Role
from cancelchain.api_client import ApiClient
from cancelchain.block import Block
from cancelchain.exceptions import InvalidRoleConfigError
from cancelchain.miller import Miller
from cancelchain.tasks import post_process
from cancelchain.transaction import Transaction
from cancelchain.util import host_address, now
from cancelchain.wallet import Wallet

TIMEOUT = 60


def _node(host):
    return host_address(host)[0]


def test_no_role(app, host, mill_block, requests_proxy, subject, wallet):
    with app.app_context():
        w = Wallet()
        m, _b = mill_block(w)
        lc = m.longest_chain
        txn = lc.create_subject(w, lc.balance(w.address), subject)
        txn.sign()
        response = ApiClient(host, wallet).post_transaction(txn)
        assert response.status_code == httpx.codes.CREATED
        _, _b2 = mill_block(wallet)
        with pytest.raises(httpx.HTTPStatusError, match='403'):
            ApiClient(host, w).get_block()


def test_roles(
    reader_wallet,
    app,
    miller_wallet,
    host,
    mill_block,
    requests_proxy,
    transactor_wallet,
    wallet,
):
    with app.app_context():
        m, b = mill_block(wallet)
        # POST to /api/block has no route (only GET) -> 405, regardless of
        # role (Flask routing rejects the method before authorize runs).
        with pytest.raises(httpx.HTTPStatusError, match='405'):
            _ = ApiClient(host, reader_wallet).post('/api/block')
        # Signature auth is self-certifying: roled wallets can read even
        # before they appear on-chain (no token handshake / chain lookup).
        assert (
            ApiClient(host, miller_wallet).get_block().status_code
            == httpx.codes.OK
        )
        assert (
            ApiClient(host, transactor_wallet).get_block().status_code
            == httpx.codes.OK
        )
        m, b = mill_block(reader_wallet)
        response = ApiClient(host, reader_wallet).get_block()
        assert response.status_code == httpx.codes.OK
        request_block = Block.from_json(response.text)
        assert request_block == b
        assert request_block == m.longest_chain.last_block
        m, b = mill_block(miller_wallet)
        response = ApiClient(host, miller_wallet).get_block()
        assert response.status_code == httpx.codes.OK
        request_block = Block.from_json(response.text)
        assert request_block == b
        assert request_block == m.longest_chain.last_block
        m, b = mill_block(transactor_wallet)
        response = ApiClient(host, reader_wallet).get_block()
        assert response.status_code == httpx.codes.OK
        request_block = Block.from_json(response.text)
        assert request_block == b
        assert request_block == m.longest_chain.last_block
        with pytest.raises(httpx.HTTPStatusError, match='405'):
            _ = ApiClient(host, transactor_wallet).post('/api/block/foo')


def test_non_app_wallet(app, host, mill_block, requests_proxy, wallet):
    # A wallet in no *_ADDRESSES list signs a valid request, but has no live
    # role -> 403 (forbidden), not 401: the signature itself verifies.
    with app.app_context():
        w = Wallet()
        with pytest.raises(httpx.HTTPStatusError, match='403'):
            ApiClient(host, w).get_block()
        mill_block(wallet)
        with pytest.raises(httpx.HTTPStatusError, match='403'):
            ApiClient(host, w).get_block()


def test_no_auth(app, requests_proxy, wallet):
    response = requests_proxy.get('/api/block', timeout=TIMEOUT)
    assert response.status_code == httpx.codes.UNAUTHORIZED


def test_last_block(app, host, mill_block, requests_proxy, wallet):
    with app.app_context():
        m, b = mill_block(wallet)
        response = ApiClient(host, wallet).get_block()
        assert response.status_code == httpx.codes.OK
        request_block = Block.from_json(response.text)
        assert request_block == b
        assert request_block == m.longest_chain.last_block


def test_get_invalid_block(app, host, mill_block, requests_proxy, wallet):
    with app.app_context():
        _m, _b = mill_block(wallet)
        with pytest.raises(httpx.HTTPStatusError, match='404'):
            ApiClient(host, wallet).get_block(block_hash='foo')


def test_post_block(app, host, requests_proxy, wallet):
    with app.app_context():
        client = ApiClient(host, wallet)
        m = Miller(milling_wallet=wallet)
        m2 = Miller(milling_wallet=wallet)
        b = m2.create_block()
        m2.mill_block(b)
        response = client.post_block(b)
        assert response.status_code == httpx.codes.OK
        response = client.get_block()
        assert response.status_code == httpx.codes.OK
        request_block = Block.from_json(response.text)
        assert request_block == b
        assert request_block == m.longest_chain.last_block


def test_post_invalid_block(app, host, requests_proxy, wallet):
    with app.app_context():
        client = ApiClient(host, wallet)
        m = Miller(milling_wallet=wallet)
        b = m.create_block()
        with pytest.raises(httpx.HTTPStatusError, match='405'):
            client.post_block(b)
        with pytest.raises(httpx.HTTPStatusError, match='404'):
            client.get_block()


def test_post_txn(app, host, mill_block, requests_proxy, subject, wallet):
    with app.app_context():
        m, _b = mill_block(wallet)
        txn = m.longest_chain.create_subject(wallet, 1, subject)
        txn.sign()
        response = ApiClient(host, wallet).post_transaction(txn)
        assert response.status_code == httpx.codes.CREATED
        assert len(m.pending_txns) == 1


def test_post_invalid_txn(
    app, host, mill_block, requests_proxy, subject, wallet
):
    with app.app_context():
        m, _b = mill_block(wallet)
        txn = m.longest_chain.create_subject(wallet, 1, subject)
        with pytest.raises(httpx.HTTPStatusError, match='400'):
            ApiClient(host, wallet).post_transaction(txn)
        assert len(m.pending_txns) == 0


def test_pending_transactions(
    app, host, mill_block, requests_proxy, subject, time_stepper, wallet
):
    with app.app_context():
        time_step = time_stepper()
        _ = next(time_step)
        m, _b = mill_block(wallet)
        _ = next(time_step)
        m, _b = mill_block(wallet)
        response = ApiClient(host, wallet).get_pending_transactions()
        assert response.status_code == httpx.codes.OK
        assert response.json() == []
        _ = next(time_step)
        txn = m.longest_chain.create_subject(wallet, 1, subject)
        txn.sign()
        response = ApiClient(host, wallet).post_transaction(txn)
        response = ApiClient(host, wallet).get_pending_transactions()
        assert response.status_code == httpx.codes.OK
        txns = [Transaction.from_dict(t) for t in response.json()]
        assert txns == [txn]
        _ = next(time_step)
        txn2 = m.longest_chain.create_subject(wallet, 2, subject)
        txn2.sign()
        response = ApiClient(host, wallet).post_transaction(txn2)
        response = ApiClient(host, wallet).get_pending_transactions()
        assert response.status_code == httpx.codes.OK
        txns = [Transaction.from_dict(t) for t in response.json()]
        assert txns == [txn, txn2]
        _ = next(time_step)
        response = ApiClient(host, wallet).get_pending_transactions(
            earliest=now()
        )
        assert response.status_code == httpx.codes.OK
        assert response.json() == []


def test_pending_transactions_earliest_returns_recent_txns(
    app, host, mill_block, requests_proxy, subject, time_stepper, wallet
):
    """Regression test: when earliest is in the past, pending txns received
    after that time should be returned. Was silently broken in PR #56 when
    the Pydantic PlainSerializer re-cast the parsed datetime back to a ciso
    string on model.model_dump(), causing SQLAlchemy to do a string comparison
    against the TIMESTAMP column (lexically wrong ordering).
    """
    with app.app_context():
        time_step = time_stepper()
        _ = next(time_step)
        m, _b = mill_block(wallet)
        _ = next(time_step)
        past = now() - timedelta(hours=1)
        txn = m.longest_chain.create_subject(wallet, 1, subject)
        txn.sign()
        ApiClient(host, wallet).post_transaction(txn)
        response = ApiClient(host, wallet).get_pending_transactions(
            earliest=past
        )
        assert response.status_code == httpx.codes.OK
        txns = [Transaction.from_dict(t) for t in response.json()]
        assert len(txns) >= 1
        assert txn in txns


# NOTE: the `app` fixture pre-loads all four *_ADDRESSES (the `wallet`
# fixture's address is in ADMIN_ADDRESSES). Each matching test below
# resets all four lists first so it controls the role config exactly —
# otherwise an unrelated pre-loaded entry (e.g. ADMIN) would win.


def _clear_role_config(app):
    for key in (
        'READER_ADDRESSES',
        'TRANSACTOR_ADDRESSES',
        'MILLER_ADDRESSES',
        'ADMIN_ADDRESSES',
    ):
        app.config[key] = []


def test_address_role_exact_match(app, wallet):
    other = Wallet()
    with app.app_context():
        _clear_role_config(app)
        app.config['MILLER_ADDRESSES'] = [wallet.address]
        assert Role.address_role(wallet.address) is Role.MILLER
        assert Role.address_role(other.address) is None


def test_address_role_reader_wildcard(app, wallet):
    with app.app_context():
        _clear_role_config(app)
        app.config['READER_ADDRESSES'] = ['*']
        assert Role.address_role(wallet.address) is Role.READER
        assert Role.address_role(Wallet().address) is Role.READER


def test_address_role_highest_wins(app, wallet):
    with app.app_context():
        _clear_role_config(app)
        app.config['READER_ADDRESSES'] = [wallet.address]
        app.config['MILLER_ADDRESSES'] = [wallet.address]
        assert Role.address_role(wallet.address) is Role.MILLER


def test_validate_config_rejects_nonaddress(app):
    app.config['ADMIN_ADDRESSES'] = ['CC.*CC']
    with pytest.raises(InvalidRoleConfigError, match='ADMIN_ADDRESSES'):
        Role.validate_config(app.config)


def test_validate_config_rejects_wildcard_outside_reader(app):
    for role_key in (
        'TRANSACTOR_ADDRESSES',
        'MILLER_ADDRESSES',
        'ADMIN_ADDRESSES',
    ):
        app.config[role_key] = ['*']
        with pytest.raises(InvalidRoleConfigError, match=role_key):
            Role.validate_config(app.config)
        app.config[role_key] = []


def test_validate_config_accepts_reader_wildcard_and_exact(app, wallet):
    app.config['READER_ADDRESSES'] = ['*']
    app.config['ADMIN_ADDRESSES'] = [wallet.address]
    Role.validate_config(app.config)  # must not raise


def test_create_app_rejects_overbroad_admin_config():
    with pytest.raises(InvalidRoleConfigError, match='ADMIN_ADDRESSES'):
        create_app(
            config_map={
                'TESTING': True,
                'SECRET_KEY': 'x' * 32,
                'SQLALCHEMY_DATABASE_URI': 'sqlite:///:memory:',
                'ADMIN_ADDRESSES': ['CC.*CC'],
            },
            register_browser=False,
        )


def test_address_role_wildcard_ignored_outside_reader(app, wallet):
    # Defense-in-depth: even if '*' is injected into a higher tier at
    # runtime (bypassing startup validation), match-time honors '*' only
    # for READER — it must not escalate.
    with app.app_context():
        _clear_role_config(app)
        app.config['MILLER_ADDRESSES'] = ['*']
        assert Role.address_role(wallet.address) is None


def test_validate_config_rejects_non_list(app):
    # A non-list value (e.g. a bare string from a malformed env var) must
    # fail-hard with a clear message, not iterate character-by-character.
    app.config['ADMIN_ADDRESSES'] = 'CCnotalistCC'
    with pytest.raises(InvalidRoleConfigError, match='must be a JSON list'):
        Role.validate_config(app.config)


def test_authorize_insufficient_live_role_forbidden(
    app, host, mill_block, reader_wallet, requests_proxy
):
    # A wallet with a valid token but a live role below the endpoint's
    # requirement is forbidden (403), not unauthorized (401).
    with app.app_context():
        _m, b = mill_block(reader_wallet)  # reader on-chain -> can get a token
        with pytest.raises(httpx.HTTPStatusError, match='403'):
            ApiClient(host, reader_wallet).post(
                f'/api/block/{b.block_hash}',
                data=b.to_json(),
                headers={'Content-Type': 'application/json'},
            )


def test_authorize_honors_live_downgrade(
    app, host, mill_block, miller_wallet, requests_proxy
):
    # An address demoted mid-token-life is governed by its live role, not
    # the higher role baked into its still-valid token.
    with app.app_context():
        _m, b = mill_block(miller_wallet)
        client = ApiClient(host, miller_wallet)
        assert client.get('/api/block').status_code == httpx.codes.OK
        # Demote: remove from MILLER, add to READER.
        app.config['MILLER_ADDRESSES'] = []
        app.config['READER_ADDRESSES'] = [
            *app.config['READER_ADDRESSES'],
            miller_wallet.address,
        ]
        # Same cached (MILLER-claim) token: still reads (live READER >= READER)
        assert client.get('/api/block').status_code == httpx.codes.OK
        # but is forbidden on the MILLER endpoint (live READER < MILLER).
        with pytest.raises(httpx.HTTPStatusError, match='403'):
            client.post(
                f'/api/block/{b.block_hash}',
                data=b.to_json(),
                headers={'Content-Type': 'application/json'},
            )


def test_signed_request_accepted(
    app, host, mill_block, requests_proxy, reader_wallet
):
    with app.app_context():
        mill_block(reader_wallet)  # reader in READER_ADDRESSES, on chain
        headers = signing.sign_headers(
            reader_wallet,
            method='GET',
            path='/api/block',
            query='',
            body=b'',
            node_host=_node(host),
        )
        r = requests_proxy.get('/api/block', headers=headers, timeout=60)
        assert r.status_code == httpx.codes.OK


def test_unsigned_request_rejected(
    app, host, mill_block, requests_proxy, reader_wallet
):
    with app.app_context():
        mill_block(reader_wallet)
        r = requests_proxy.get('/api/block', timeout=60)  # no CC-* headers
        assert r.status_code == httpx.codes.UNAUTHORIZED


def test_tampered_path_rejected(
    app, host, mill_block, requests_proxy, reader_wallet
):
    with app.app_context():
        mill_block(reader_wallet)
        headers = signing.sign_headers(
            reader_wallet,
            method='GET',
            path='/api/block',
            query='',
            body=b'',
            node_host=_node(host),
        )
        # signed for /api/block, sent to a different protected path
        r = requests_proxy.get(
            '/api/transaction/pending', headers=headers, timeout=60
        )
        assert r.status_code == httpx.codes.UNAUTHORIZED


def test_stale_timestamp_rejected(
    app, host, mill_block, requests_proxy, reader_wallet
):
    with app.app_context():
        mill_block(reader_wallet)
        old = int(now().timestamp()) - (signing.FRESHNESS_SECONDS + 5)
        headers = signing.sign_headers(
            reader_wallet,
            method='GET',
            path='/api/block',
            query='',
            body=b'',
            node_host=_node(host),
            timestamp=old,
        )
        r = requests_proxy.get('/api/block', headers=headers, timeout=60)
        assert r.status_code == httpx.codes.UNAUTHORIZED


def test_future_timestamp_rejected(
    app, host, mill_block, requests_proxy, reader_wallet
):
    with app.app_context():
        mill_block(reader_wallet)
        future = int(now().timestamp()) + (signing.FRESHNESS_SECONDS + 1)
        headers = signing.sign_headers(
            reader_wallet,
            method='GET',
            path='/api/block',
            query='',
            body=b'',
            node_host=_node(host),
            timestamp=future,
        )
        r = requests_proxy.get('/api/block', headers=headers, timeout=60)
        assert r.status_code == httpx.codes.UNAUTHORIZED


def test_missing_one_signature_header_rejected(
    app, host, mill_block, requests_proxy, reader_wallet
):
    with app.app_context():
        mill_block(reader_wallet)
        headers = signing.sign_headers(
            reader_wallet,
            method='GET',
            path='/api/block',
            query='',
            body=b'',
            node_host=_node(host),
        )
        del headers[signing.H_TIMESTAMP]  # all CC-* present except one
        r = requests_proxy.get('/api/block', headers=headers, timeout=60)
        assert r.status_code == httpx.codes.UNAUTHORIZED


def test_pubkey_address_mismatch_rejected(
    app, host, mill_block, requests_proxy, reader_wallet
):
    with app.app_context():
        mill_block(reader_wallet)
        headers = signing.sign_headers(
            reader_wallet,
            method='GET',
            path='/api/block',
            query='',
            body=b'',
            node_host=_node(host),
        )
        headers[signing.H_PUBKEY] = Wallet().public_key_b64  # pubkey != address
        r = requests_proxy.get('/api/block', headers=headers, timeout=60)
        assert r.status_code == httpx.codes.UNAUTHORIZED


def test_post_process_signs_at_send_time(
    app, host, mill_block, requests_proxy, wallet
):
    # `wallet` is the ADMIN node wallet (in app.wallets); post_process should
    # sign the outbound /process request at send time and it should verify.
    with app.app_context():
        _m, b = mill_block(wallet)
        # POST the block to its own /process endpoint (miller-gated; wallet is
        # ADMIN). Raises on non-2xx via ApiClient.post -> proves the signed
        # request verified.
        post_process(
            host,
            wallet.address,
            f'/api/block/{b.block_hash}/process',
            data=b.to_json(),
            vhosts=None,
        )
