import datetime
import json
from datetime import timedelta

import httpx
import pytest

from gumptionchain import create_app, signing
from gumptionchain.api import Role
from gumptionchain.api_client import ApiClient
from gumptionchain.block import Block
from gumptionchain.chain import Chain
from gumptionchain.exceptions import InvalidRoleConfigError
from gumptionchain.miller import Miller
from gumptionchain.milling import mill_hash_str
from gumptionchain.models import PendingTxnDAO
from gumptionchain.signing_key import SigningKey
from gumptionchain.tasks import post_process
from gumptionchain.transaction import CoinbaseMetrics, Transaction
from gumptionchain.util import host_address, now

TIMEOUT = 60


def _node(host):
    return host_address(host)[0]


def test_no_role(app, host, mill_block, requests_proxy, subject, signing_key):
    with app.app_context():
        w = SigningKey()
        m, _b = mill_block(w)
        lc = m.longest_chain
        txn = lc.create_opposition(w, lc.balance(w.address), subject)
        txn.sign()
        response = ApiClient(host, signing_key).post_transaction(txn)
        assert response.status_code == httpx.codes.CREATED
        _, _b2 = mill_block(signing_key)
        with pytest.raises(httpx.HTTPStatusError, match='403'):
            ApiClient(host, w).get_block()


def test_roles(
    reader_signing_key,
    app,
    miller_signing_key,
    host,
    mill_block,
    requests_proxy,
    transactor_signing_key,
    signing_key,
):
    with app.app_context():
        m, b = mill_block(signing_key)
        # POST to /api/block has no route (only GET) -> 405, regardless of
        # role (Flask routing rejects the method before authorize runs).
        with pytest.raises(httpx.HTTPStatusError, match='405'):
            _ = ApiClient(host, reader_signing_key).post('/api/block')
        # Signature auth is self-certifying: roled signing_keys can read even
        # before they appear on-chain (no token handshake / chain lookup).
        assert (
            ApiClient(host, miller_signing_key).get_block().status_code
            == httpx.codes.OK
        )
        assert (
            ApiClient(host, transactor_signing_key).get_block().status_code
            == httpx.codes.OK
        )
        m, b = mill_block(reader_signing_key)
        response = ApiClient(host, reader_signing_key).get_block()
        assert response.status_code == httpx.codes.OK
        request_block = Block.from_json(response.text)
        assert request_block == b
        assert request_block == m.longest_chain.last_block
        m, b = mill_block(miller_signing_key)
        response = ApiClient(host, miller_signing_key).get_block()
        assert response.status_code == httpx.codes.OK
        request_block = Block.from_json(response.text)
        assert request_block == b
        assert request_block == m.longest_chain.last_block
        m, b = mill_block(transactor_signing_key)
        response = ApiClient(host, reader_signing_key).get_block()
        assert response.status_code == httpx.codes.OK
        request_block = Block.from_json(response.text)
        assert request_block == b
        assert request_block == m.longest_chain.last_block
        with pytest.raises(httpx.HTTPStatusError, match='405'):
            _ = ApiClient(host, transactor_signing_key).post('/api/block/foo')


def test_non_app_signing_key(
    app, host, mill_block, requests_proxy, signing_key
):
    # A key in no *_ADDRESSES list signs a valid request, but has no live
    # role -> 403 (forbidden), not 401: the signature itself verifies.
    with app.app_context():
        w = SigningKey()
        with pytest.raises(httpx.HTTPStatusError, match='403'):
            ApiClient(host, w).get_block()
        mill_block(signing_key)
        with pytest.raises(httpx.HTTPStatusError, match='403'):
            ApiClient(host, w).get_block()


def test_no_auth(app, requests_proxy, signing_key):
    response = requests_proxy.get('/api/block', timeout=TIMEOUT)
    assert response.status_code == httpx.codes.UNAUTHORIZED


def test_last_block(app, host, mill_block, requests_proxy, signing_key):
    with app.app_context():
        m, b = mill_block(signing_key)
        response = ApiClient(host, signing_key).get_block()
        assert response.status_code == httpx.codes.OK
        request_block = Block.from_json(response.text)
        assert request_block == b
        assert request_block == m.longest_chain.last_block


def test_get_invalid_block(app, host, mill_block, requests_proxy, signing_key):
    with app.app_context():
        _m, _b = mill_block(signing_key)
        with pytest.raises(httpx.HTTPStatusError, match='404'):
            ApiClient(host, signing_key).get_block(block_hash='foo')


def test_get_blocks_range(app, host, mill_block, requests_proxy, signing_key):
    with app.app_context():
        _m, b0 = mill_block(signing_key)  # idx 0 (genesis)
        _m, b1 = mill_block(signing_key)  # idx 1
        response = ApiClient(host, signing_key).get(
            '/api/blocks', params={'from_idx': '0', 'limit': '2'}
        )
        assert response.status_code == httpx.codes.OK
        blocks = [Block.from_json(json.dumps(b)) for b in response.json()]
        assert [b.idx for b in blocks] == [0, 1]
        assert blocks[0].block_hash == b0.block_hash
        assert blocks[1].block_hash == b1.block_hash


def test_get_blocks_clamps_limit(
    app, host, mill_block, requests_proxy, signing_key
):
    with app.app_context():
        for _ in range(4):
            mill_block(signing_key)
        app.config['SYNC_BATCH_SIZE'] = 2
        response = ApiClient(host, signing_key).get(
            '/api/blocks', params={'from_idx': '0', 'limit': '1000'}
        )
        assert response.status_code == httpx.codes.OK
        assert len(response.json()) == 2


def test_get_blocks_past_tip_empty(
    app, host, mill_block, requests_proxy, signing_key
):
    with app.app_context():
        mill_block(signing_key)
        response = ApiClient(host, signing_key).get(
            '/api/blocks', params={'from_idx': '50', 'limit': '10'}
        )
        assert response.status_code == httpx.codes.OK
        assert response.json() == []


def test_get_blocks_invalid_query(
    app, host, mill_block, requests_proxy, signing_key
):
    with app.app_context():
        mill_block(signing_key)
        client = ApiClient(host, signing_key)
        bad_from = client.get(
            '/api/blocks',
            params={'from_idx': '-1', 'limit': '2'},
            raise_for_status=False,
        )
        assert bad_from.status_code == httpx.codes.BAD_REQUEST
        bad_limit = client.get(
            '/api/blocks',
            params={'from_idx': '0', 'limit': '0'},
            raise_for_status=False,
        )
        assert bad_limit.status_code == httpx.codes.BAD_REQUEST


def test_get_blocks_excludes_fork(
    app, host, mill_block, requests_proxy, time_stepper, signing_key
):
    """Only longest-chain blocks are returned; a fork block at a shared
    height is absent."""
    with app.app_context():
        time_step = time_stepper(start=datetime.datetime.now(datetime.UTC))
        _ = next(time_step)
        chain_a = Chain()
        block_1 = Block()
        chain_a.link_block(block_1)
        chain_a.seal_block(block_1, signing_key, CoinbaseMetrics())
        block_1.mill()
        chain_a.add_block(block_1)
        chain_a.to_db()

        _ = next(time_step)
        block_2a = Block()
        chain_a.link_block(block_2a)
        chain_a.seal_block(block_2a, signing_key, CoinbaseMetrics())
        block_2a.mill()

        _ = next(time_step)
        block_2b = Block()
        chain_a.link_block(block_2b)
        chain_a.seal_block(block_2b, signing_key, CoinbaseMetrics())
        block_2b.mill()

        _ = next(time_step)
        chain_a.add_block(block_2a)
        chain_a.to_db()

        _ = next(time_step)
        chain_b = Chain()
        chain_b.add_block(block_2b)
        chain_b.to_db()

        response = ApiClient(host, signing_key).get(
            '/api/blocks', params={'from_idx': '1', 'limit': '1'}
        )
        assert response.status_code == httpx.codes.OK
        rows = response.json()
        assert len(rows) == 1
        canonical = rows[0]['block_hash']
        assert canonical in {block_2a.block_hash, block_2b.block_hash}
        other = (
            block_2b.block_hash
            if canonical == block_2a.block_hash
            else block_2a.block_hash
        )
        assert all(r['block_hash'] != other for r in rows)


def test_post_block(app, host, requests_proxy, signing_key):
    with app.app_context():
        client = ApiClient(host, signing_key)
        m = Miller(milling_signing_key=signing_key)
        m2 = Miller(milling_signing_key=signing_key)
        b = m2.create_block()
        m2.mill_block(b)
        response = client.post_block(b)
        assert response.status_code == httpx.codes.OK
        response = client.get_block()
        assert response.status_code == httpx.codes.OK
        request_block = Block.from_json(response.text)
        assert request_block == b
        assert request_block == m.longest_chain.last_block


def test_post_invalid_block(app, host, requests_proxy, signing_key):
    with app.app_context():
        client = ApiClient(host, signing_key)
        m = Miller(milling_signing_key=signing_key)
        b = m.create_block()
        with pytest.raises(httpx.HTTPStatusError, match='405'):
            client.post_block(b)
        with pytest.raises(httpx.HTTPStatusError, match='404'):
            client.get_block()


def test_post_txn(app, host, mill_block, requests_proxy, subject, signing_key):
    with app.app_context():
        m, _b = mill_block(signing_key)
        txn = m.longest_chain.create_opposition(signing_key, 1, subject)
        txn.sign()
        response = ApiClient(host, signing_key).post_transaction(txn)
        assert response.status_code == httpx.codes.CREATED
        assert len(m.pending_txns) == 1


def test_post_invalid_txn(
    app, host, mill_block, requests_proxy, subject, signing_key
):
    with app.app_context():
        m, _b = mill_block(signing_key)
        txn = m.longest_chain.create_opposition(signing_key, 1, subject)
        with pytest.raises(httpx.HTTPStatusError, match='400'):
            ApiClient(host, signing_key).post_transaction(txn)
        assert len(m.pending_txns) == 0


def test_pending_transactions(
    app, host, mill_block, requests_proxy, subject, time_stepper, signing_key
):
    with app.app_context():
        time_step = time_stepper()
        _ = next(time_step)
        m, _b = mill_block(signing_key)
        _ = next(time_step)
        m, _b = mill_block(signing_key)
        response = ApiClient(host, signing_key).get_pending_transactions()
        assert response.status_code == httpx.codes.OK
        assert response.json() == []
        _ = next(time_step)
        txn = m.longest_chain.create_opposition(signing_key, 1, subject)
        txn.sign()
        response = ApiClient(host, signing_key).post_transaction(txn)
        response = ApiClient(host, signing_key).get_pending_transactions()
        assert response.status_code == httpx.codes.OK
        txns = [Transaction.from_dict(t) for t in response.json()]
        assert txns == [txn]
        _ = next(time_step)
        txn2 = m.longest_chain.create_opposition(signing_key, 2, subject)
        txn2.sign()
        response = ApiClient(host, signing_key).post_transaction(txn2)
        response = ApiClient(host, signing_key).get_pending_transactions()
        assert response.status_code == httpx.codes.OK
        txns = [Transaction.from_dict(t) for t in response.json()]
        assert txns == [txn, txn2]
        _ = next(time_step)
        response = ApiClient(host, signing_key).get_pending_transactions(
            earliest=now()
        )
        assert response.status_code == httpx.codes.OK
        assert response.json() == []


def test_pending_transactions_earliest_returns_recent_txns(
    app, host, mill_block, requests_proxy, subject, time_stepper, signing_key
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
        m, _b = mill_block(signing_key)
        _ = next(time_step)
        past = now() - timedelta(hours=1)
        txn = m.longest_chain.create_opposition(signing_key, 1, subject)
        txn.sign()
        ApiClient(host, signing_key).post_transaction(txn)
        response = ApiClient(host, signing_key).get_pending_transactions(
            earliest=past
        )
        assert response.status_code == httpx.codes.OK
        txns = [Transaction.from_dict(t) for t in response.json()]
        assert len(txns) >= 1
        assert txn in txns


def test_pending_transactions_exclude_confirmed(
    app, host, mill_block, requests_proxy, subject, time_stepper, signing_key
):
    with app.app_context():
        time_step = time_stepper()
        _ = next(time_step)
        m, _b = mill_block(signing_key)
        _ = next(time_step)
        confirmed = m.longest_chain.create_opposition(signing_key, 1, subject)
        confirmed.sign()
        ApiClient(host, signing_key).post_transaction(confirmed)
        _ = next(time_step)
        m, _b = mill_block(signing_key)  # confirms + prunes `confirmed`
        # simulate re-gossip of the already-mined txn
        PendingTxnDAO(
            txid=confirmed.txid,
            timestamp=confirmed.timestamp_dt,
            json_data=confirmed.to_json(),
        ).commit()
        _ = next(time_step)
        txn2 = m.longest_chain.create_opposition(signing_key, 2, subject)
        txn2.sign()
        ApiClient(host, signing_key).post_transaction(txn2)

        response = ApiClient(host, signing_key).get_pending_transactions()
        assert response.status_code == httpx.codes.OK
        txids = [t['txid'] for t in response.json()]
        assert txids == [txn2.txid]


# NOTE: the `app` fixture pre-loads all four *_ADDRESSES (the `signing_key`
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


def test_address_role_exact_match(app, signing_key):
    other = SigningKey()
    with app.app_context():
        _clear_role_config(app)
        app.config['MILLER_ADDRESSES'] = [signing_key.address]
        assert Role.address_role(signing_key.address) is Role.MILLER
        assert Role.address_role(other.address) is None


def test_address_role_reader_wildcard(app, signing_key):
    with app.app_context():
        _clear_role_config(app)
        app.config['READER_ADDRESSES'] = ['*']
        assert Role.address_role(signing_key.address) is Role.READER
        assert Role.address_role(SigningKey().address) is Role.READER


def test_address_role_highest_wins(app, signing_key):
    with app.app_context():
        _clear_role_config(app)
        app.config['READER_ADDRESSES'] = [signing_key.address]
        app.config['MILLER_ADDRESSES'] = [signing_key.address]
        assert Role.address_role(signing_key.address) is Role.MILLER


def test_validate_config_rejects_nonaddress(app):
    app.config['ADMIN_ADDRESSES'] = ['CC.*CC']
    with pytest.raises(InvalidRoleConfigError, match='ADMIN_ADDRESSES'):
        Role.validate_config(app.config)


def test_validate_config_rejects_wildcard_in_miller_and_admin(app):
    # READER and TRANSACTOR permit "*"; MILLER and ADMIN must not.
    for role_key in (
        'MILLER_ADDRESSES',
        'ADMIN_ADDRESSES',
    ):
        app.config[role_key] = ['*']
        with pytest.raises(InvalidRoleConfigError, match=role_key):
            Role.validate_config(app.config)
        app.config[role_key] = []


def test_validate_config_accepts_reader_wildcard_and_exact(app, signing_key):
    app.config['READER_ADDRESSES'] = ['*']
    app.config['ADMIN_ADDRESSES'] = [signing_key.address]
    Role.validate_config(app.config)  # must not raise


def test_validate_config_accepts_transactor_wildcard_and_exact(
    app, signing_key
):
    # "*" in TRANSACTOR plus an exact entry in a higher tier is valid.
    app.config['TRANSACTOR_ADDRESSES'] = ['*']
    app.config['ADMIN_ADDRESSES'] = [signing_key.address]
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


def test_address_role_wildcard_ignored_for_miller_and_admin(app, signing_key):
    # Defense-in-depth: even if '*' is injected into a higher tier at
    # runtime (bypassing startup validation), match-time honors '*' only
    # for READER or TRANSACTOR — MILLER/ADMIN must never escalate via '*'.
    with app.app_context():
        _clear_role_config(app)
        app.config['MILLER_ADDRESSES'] = ['*']
        assert Role.address_role(signing_key.address) is None


def test_validate_config_rejects_non_list(app):
    # A non-list value (e.g. a bare string from a malformed env var) must
    # fail-hard with a clear message, not iterate character-by-character.
    app.config['ADMIN_ADDRESSES'] = 'CCnotalistCC'
    with pytest.raises(InvalidRoleConfigError, match='must be a JSON list'):
        Role.validate_config(app.config)


def test_authorize_insufficient_live_role_forbidden(
    app, host, mill_block, reader_signing_key, requests_proxy
):
    # A signing_key with a valid token but a live role below the endpoint's
    # requirement is forbidden (403), not unauthorized (401).
    with app.app_context():
        _m, b = mill_block(
            reader_signing_key
        )  # reader on-chain -> can get a token
        with pytest.raises(httpx.HTTPStatusError, match='403'):
            ApiClient(host, reader_signing_key).post(
                f'/api/block/{b.block_hash}',
                data=b.to_json(),
                headers={'Content-Type': 'application/json'},
            )


def test_authorize_honors_live_downgrade(
    app, host, mill_block, miller_signing_key, requests_proxy
):
    # An address demoted mid-token-life is governed by its live role, not
    # the higher role baked into its still-valid token.
    with app.app_context():
        _m, b = mill_block(miller_signing_key)
        client = ApiClient(host, miller_signing_key)
        assert client.get('/api/block').status_code == httpx.codes.OK
        # Demote: remove from MILLER, add to READER.
        app.config['MILLER_ADDRESSES'] = []
        app.config['READER_ADDRESSES'] = [
            *app.config['READER_ADDRESSES'],
            miller_signing_key.address,
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
    app, host, mill_block, requests_proxy, reader_signing_key
):
    with app.app_context():
        mill_block(reader_signing_key)  # reader in READER_ADDRESSES, on chain
        headers = signing.sign_headers(
            reader_signing_key,
            method='GET',
            path='/api/block',
            query='',
            body=b'',
            node_host=_node(host),
        )
        r = requests_proxy.get('/api/block', headers=headers, timeout=60)
        assert r.status_code == httpx.codes.OK


def test_unsigned_request_rejected(
    app, host, mill_block, requests_proxy, reader_signing_key
):
    with app.app_context():
        mill_block(reader_signing_key)
        r = requests_proxy.get('/api/block', timeout=60)  # no CC-* headers
        assert r.status_code == httpx.codes.UNAUTHORIZED


def test_tampered_path_rejected(
    app, host, mill_block, requests_proxy, reader_signing_key
):
    with app.app_context():
        mill_block(reader_signing_key)
        headers = signing.sign_headers(
            reader_signing_key,
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
    app, host, mill_block, requests_proxy, reader_signing_key
):
    with app.app_context():
        mill_block(reader_signing_key)
        old = int(now().timestamp()) - (signing.FRESHNESS_SECONDS + 5)
        headers = signing.sign_headers(
            reader_signing_key,
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
    app, host, mill_block, requests_proxy, reader_signing_key
):
    with app.app_context():
        mill_block(reader_signing_key)
        # Sit well past the freshness window's far edge. A tight +1s margin
        # is flaky: if >1s elapses before the server re-reads now() at verify,
        # the "future" timestamp drifts back inside the window and is accepted.
        future = int(now().timestamp()) + (signing.FRESHNESS_SECONDS + 60)
        headers = signing.sign_headers(
            reader_signing_key,
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
    app, host, mill_block, requests_proxy, reader_signing_key
):
    with app.app_context():
        mill_block(reader_signing_key)
        headers = signing.sign_headers(
            reader_signing_key,
            method='GET',
            path='/api/block',
            query='',
            body=b'',
            node_host=_node(host),
        )
        del headers[signing.H_TIMESTAMP]  # all CC-* present except one
        r = requests_proxy.get('/api/block', headers=headers, timeout=60)
        assert r.status_code == httpx.codes.UNAUTHORIZED


def test_post_process_signs_at_send_time(
    app, host, mill_block, requests_proxy, signing_key
):
    # `signing_key` is the ADMIN key (in app.signing_keys); post_process should
    # sign the outbound /process request at send time and it should verify.
    with app.app_context():
        _m, b = mill_block(signing_key)
        # POST the block to its own /process endpoint (miller-gated; key is
        # ADMIN). Raises on non-2xx via ApiClient.post -> proves the signed
        # request verified.
        post_process(
            host,
            signing_key.address,
            f'/api/block/{b.block_hash}/process',
            data=b.to_json(),
            vhosts=None,
        )


def test_rescind_missing_kind_returns_validation_error(
    app, host, mill_block, requests_proxy, subject_raw, transactor_signing_key
):
    """Regression guard: /api/transaction/rescind without `kind` returns 400."""
    with app.app_context():
        mill_block(transactor_signing_key)
        client = ApiClient(host, transactor_signing_key)
        # Omit `kind` entirely — Pydantic validation must reject the request.
        with pytest.raises(httpx.HTTPStatusError, match='400'):
            client.get(
                '/api/transaction/rescind',
                params={
                    'signer': transactor_signing_key.address,
                    'amount': '1',
                    'subject': subject_raw,
                    # `kind` deliberately absent
                },
            )


def test_rescind_invalid_kind_returns_validation_error(
    app, host, mill_block, requests_proxy, subject_raw, transactor_signing_key
):
    """A `kind` value outside {'opposition','support'} returns 400."""
    with app.app_context():
        mill_block(transactor_signing_key)
        client = ApiClient(host, transactor_signing_key)
        with pytest.raises(httpx.HTTPStatusError, match='400'):
            client.get(
                '/api/transaction/rescind',
                params={
                    'signer': transactor_signing_key.address,
                    'amount': '1',
                    'subject': subject_raw,
                    'kind': 'invalid',
                },
            )


def test_transfer_build_accepts_signer_address(
    app, host, mill_block, requests_proxy, transactor_signing_key
):
    """The transfer builder takes a `signer` address, not a `public_key`.

    Mills a block to fund the transactor, then requests an unsigned transfer
    keyed by the signer's gc1… address. The request SHAPE must be accepted
    (no 400 on an unknown `public_key` field or a missing `signer`).
    """
    with app.app_context():
        mill_block(transactor_signing_key)
        dest = SigningKey().address
        client = ApiClient(host, transactor_signing_key)
        r = client.get(
            '/api/transaction/transfer',
            params={
                'signer': transactor_signing_key.address,
                'amount': '1',
                'address': dest,
            },
        )
        assert r.status_code == httpx.codes.OK
        # The builder echoes the signer as the unsigned txn's `address`.
        assert r.json()['address'] == transactor_signing_key.address


def test_transaction_provenance_endpoint_canonical(
    app, host, mill_block, requests_proxy, subject, signing_key
):
    with app.app_context():
        m, _b1 = mill_block(signing_key)
        txn = m.longest_chain.create_opposition(signing_key, 300, subject)
        txn.sign()
        ApiClient(host, signing_key).post_transaction(txn)
        m, b2 = mill_block(signing_key)

        resp = ApiClient(host, signing_key).get(f'/api/transaction/{txn.txid}')
        assert resp.status_code == httpx.codes.OK
        body = resp.json()
        assert body['txid'] == txn.txid
        assert body['address'] == signing_key.address
        assert body['status'] == 'canonical'
        assert body['confirmations'] == 1
        assert body['block_hash'] == b2.block_hash
        assert body['as_of_block'] == b2.block_hash
        assert {
            'kind': 'opposition',
            'subject': subject,
            'amount': 300,
        } in body['outflows']


def test_transaction_provenance_endpoint_pending(
    app, host, mill_block, requests_proxy, subject, signing_key
):
    with app.app_context():
        m, _b1 = mill_block(signing_key)
        txn = m.longest_chain.create_opposition(signing_key, 5, subject)
        txn.sign()
        ApiClient(host, signing_key).post_transaction(txn)

        resp = ApiClient(host, signing_key).get(f'/api/transaction/{txn.txid}')
        assert resp.status_code == httpx.codes.OK
        assert resp.json()['status'] == 'pending'


def test_transaction_provenance_endpoint_unknown_404(
    app, host, mill_block, requests_proxy, signing_key
):
    with app.app_context():
        mill_block(signing_key)
        absent = mill_hash_str('absent-txn')
        with pytest.raises(httpx.HTTPStatusError, match='404'):
            ApiClient(host, signing_key).get(f'/api/transaction/{absent}')


def test_transaction_provenance_endpoint_requires_auth(
    app, host, mill_block, requests_proxy, subject, signing_key
):
    with app.app_context():
        m, _b1 = mill_block(signing_key)
        txn = m.longest_chain.create_opposition(signing_key, 1, subject)
        txn.sign()
        ApiClient(host, signing_key).post_transaction(txn)
        mill_block(signing_key)
        # unsigned request -> 401
        resp = requests_proxy.get(
            f'/api/transaction/{txn.txid}', timeout=TIMEOUT
        )
        assert resp.status_code == httpx.codes.UNAUTHORIZED
