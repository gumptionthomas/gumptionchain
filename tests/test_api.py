from datetime import timedelta

import httpx
import pytest

from cancelchain.api import API_TOKEN_SECONDS
from cancelchain.api_client import ApiClient
from cancelchain.block import Block
from cancelchain.miller import Miller
from cancelchain.transaction import Transaction
from cancelchain.util import now
from cancelchain.wallet import Wallet

TIMEOUT = 60


def test_post_token_none(app, requests_proxy, wallet):
    response = requests_proxy.post(
        f'/api/token/{wallet.address}', timeout=TIMEOUT
    )
    assert response.status_code == httpx.codes.UNAUTHORIZED


def test_post_token_invalid(app, requests_proxy, wallet):
    headers = {'Content-Type': 'application/json'}
    path = f'/api/token/{wallet.address}'
    _ = requests_proxy.get(path, timeout=TIMEOUT)
    response = requests_proxy.post(
        path, content='foo', headers=headers, timeout=TIMEOUT
    )
    assert response.status_code == httpx.codes.BAD_REQUEST
    response = requests_proxy.post(
        path,
        content='{"challenge": "foo"}',
        headers=headers,
        timeout=TIMEOUT,
    )
    assert response.status_code == httpx.codes.UNAUTHORIZED


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
        with pytest.raises(httpx.HTTPStatusError, match='401'):
            _ = ApiClient(host, reader_wallet).post('/api/block')
        with pytest.raises(httpx.HTTPStatusError, match='401'):
            _ = ApiClient(host, miller_wallet).get_block()
        with pytest.raises(httpx.HTTPStatusError, match='401'):
            _ = ApiClient(host, transactor_wallet).get_block()
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


def test_regex_roles(
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
        w = Wallet()
        _m, _b = mill_block(w)
        with pytest.raises(httpx.HTTPStatusError, match='403'):
            _ = ApiClient(host, w).get_block()
        app.config['READER_ADDRESSES'] = ['.*']
        _ = ApiClient(host, w).get_block()
        app.config['READER_ADDRESSES'] = ['CC.*CC']
        _ = ApiClient(host, w).get_block()
        app.config['READER_ADDRESSES'] = ['CC.*DD']
        with pytest.raises(httpx.HTTPStatusError, match='403'):
            _ = ApiClient(host, w).get_block()


def test_non_app_wallet(app, host, mill_block, requests_proxy, wallet):
    with app.app_context():
        w = Wallet()
        with pytest.raises(httpx.HTTPStatusError, match='401'):
            ApiClient(host, w).get_block()
        mill_block(wallet)
        with pytest.raises(httpx.HTTPStatusError, match='401'):
            ApiClient(host, w).get_block()


def test_no_auth(app, requests_proxy, wallet):
    response = requests_proxy.get('/api/block', timeout=TIMEOUT)
    assert response.status_code == httpx.codes.UNAUTHORIZED


def test_expired_auth(
    app, host, mill_block, requests_proxy, time_stepper, wallet
):
    time_step = time_stepper(delta=API_TOKEN_SECONDS + 1)
    _ = next(time_step)
    client = ApiClient(host, wallet)
    with app.app_context():
        _, _ = mill_block(wallet)
        _ = client.get('/api/block')
        _ = next(time_step)
        response = client.get('/api/block')
        assert response.status_code == httpx.codes.OK


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
