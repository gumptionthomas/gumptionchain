from urllib.parse import urljoin

import pytest
import requests

from cancelchain.api import API_TOKEN_SECONDS
from cancelchain.api_client import ApiClient
from cancelchain.block import Block
from cancelchain.miller import Miller
from cancelchain.transaction import Transaction
from cancelchain.util import now
from cancelchain.wallet import Wallet

TIMEOUT = 60


def test_post_token_none(app, host, requests_proxy, wallet):
    response = requests.post(
        urljoin(host, f'/api/token/{wallet.address}'), timeout=TIMEOUT
    )
    assert response.status_code == requests.codes.unauthorized


def test_post_token_invalid(app, host, requests_proxy, wallet):
    headers = {'Content-Type': 'application/json'}
    url = urljoin(host, f'/api/token/{wallet.address}')
    _ = requests.get(url, timeout=TIMEOUT)
    response = requests.post(url, data='foo', headers=headers, timeout=TIMEOUT)
    assert response.status_code == requests.codes.bad_request
    response = requests.post(
        url, data='{"challenge": "foo"}', headers=headers, timeout=TIMEOUT
    )
    assert response.status_code == requests.codes.unauthorized


def test_no_role(app, host, mill_block, requests_proxy, subject, wallet):
    with app.app_context():
        w = Wallet()
        m, _b = mill_block(w)
        lc = m.longest_chain
        txn = lc.create_subject(w, lc.balance(w.address), subject)
        txn.sign()
        response = ApiClient(host, wallet).post_transaction(txn)
        assert response.status_code == requests.codes.created
        _, _b2 = mill_block(wallet)
        with pytest.raises(requests.exceptions.HTTPError, match='403'):
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
        with pytest.raises(requests.exceptions.HTTPError, match='401'):
            _ = ApiClient(host, reader_wallet).post('/api/block')
        with pytest.raises(requests.exceptions.HTTPError, match='401'):
            _ = ApiClient(host, miller_wallet).get_block()
        with pytest.raises(requests.exceptions.HTTPError, match='401'):
            _ = ApiClient(host, transactor_wallet).get_block()
        m, b = mill_block(reader_wallet)
        response = ApiClient(host, reader_wallet).get_block()
        assert response.status_code == requests.codes.ok
        request_block = Block.from_json(response.text)
        assert request_block == b
        assert request_block == m.longest_chain.last_block
        m, b = mill_block(miller_wallet)
        response = ApiClient(host, miller_wallet).get_block()
        assert response.status_code == requests.codes.ok
        request_block = Block.from_json(response.text)
        assert request_block == b
        assert request_block == m.longest_chain.last_block
        m, b = mill_block(transactor_wallet)
        response = ApiClient(host, reader_wallet).get_block()
        assert response.status_code == requests.codes.ok
        request_block = Block.from_json(response.text)
        assert request_block == b
        assert request_block == m.longest_chain.last_block
        with pytest.raises(requests.exceptions.HTTPError, match='405'):
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
        with pytest.raises(requests.exceptions.HTTPError, match='403'):
            _ = ApiClient(host, w).get_block()
        app.config['READER_ADDRESSES'] = ['.*']
        _ = ApiClient(host, w).get_block()
        app.config['READER_ADDRESSES'] = ['CC.*CC']
        _ = ApiClient(host, w).get_block()
        app.config['READER_ADDRESSES'] = ['CC.*DD']
        with pytest.raises(requests.exceptions.HTTPError, match='403'):
            _ = ApiClient(host, w).get_block()


def test_non_app_wallet(app, host, mill_block, requests_proxy, wallet):
    with app.app_context():
        w = Wallet()
        with pytest.raises(requests.exceptions.HTTPError, match='401'):
            ApiClient(host, w).get_block()
        mill_block(wallet)
        with pytest.raises(requests.exceptions.HTTPError, match='401'):
            ApiClient(host, w).get_block()


def test_no_auth(app, host, requests_proxy, wallet):
    response = requests.get(urljoin(host, '/api/block'), timeout=TIMEOUT)
    assert response.status_code == requests.codes.unauthorized


def test_expired_auth(
    app, host, mill_block, requests_proxy, time_stepper, wallet
):
    time_step = time_stepper(delta=API_TOKEN_SECONDS + 1)
    _ = next(time_step)
    client = ApiClient(host, wallet)
    with app.app_context():
        _, _ = mill_block(wallet)
        _ = client.get(urljoin(host, '/api/block'))
        _ = next(time_step)
        response = client.get(urljoin(host, '/api/block'))
        assert response.status_code == requests.codes.ok


def test_last_block(app, host, mill_block, requests_proxy, wallet):
    with app.app_context():
        m, b = mill_block(wallet)
        response = ApiClient(host, wallet).get_block()
        assert response.status_code == requests.codes.ok
        request_block = Block.from_json(response.text)
        assert request_block == b
        assert request_block == m.longest_chain.last_block


def test_get_invalid_block(app, host, mill_block, requests_proxy, wallet):
    with app.app_context():
        _m, _b = mill_block(wallet)
        with pytest.raises(requests.exceptions.HTTPError, match='404'):
            ApiClient(host, wallet).get_block(block_hash='foo')


def test_post_block(app, host, requests_proxy, wallet):
    with app.app_context():
        client = ApiClient(host, wallet)
        m = Miller(milling_wallet=wallet)
        m2 = Miller(milling_wallet=wallet)
        b = m2.create_block()
        m2.mill_block(b)
        response = client.post_block(b)
        assert response.status_code == requests.codes.ok
        response = client.get_block()
        assert response.status_code == requests.codes.ok
        request_block = Block.from_json(response.text)
        assert request_block == b
        assert request_block == m.longest_chain.last_block


def test_post_invalid_block(app, host, requests_proxy, wallet):
    with app.app_context():
        client = ApiClient(host, wallet)
        m = Miller(milling_wallet=wallet)
        b = m.create_block()
        with pytest.raises(requests.exceptions.HTTPError, match='405'):
            client.post_block(b)
        with pytest.raises(requests.exceptions.HTTPError, match='404'):
            client.get_block()


def test_post_txn(app, host, mill_block, requests_proxy, subject, wallet):
    with app.app_context():
        m, _b = mill_block(wallet)
        txn = m.longest_chain.create_subject(wallet, 1, subject)
        txn.sign()
        response = ApiClient(host, wallet).post_transaction(txn)
        assert response.status_code == requests.codes.created
        assert len(m.pending_txns) == 1


def test_post_invalid_txn(
    app, host, mill_block, requests_proxy, subject, wallet
):
    with app.app_context():
        m, _b = mill_block(wallet)
        txn = m.longest_chain.create_subject(wallet, 1, subject)
        with pytest.raises(requests.exceptions.HTTPError, match='400'):
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
        assert response.status_code == requests.codes.ok
        assert response.json() == []
        _ = next(time_step)
        txn = m.longest_chain.create_subject(wallet, 1, subject)
        txn.sign()
        response = ApiClient(host, wallet).post_transaction(txn)
        response = ApiClient(host, wallet).get_pending_transactions()
        assert response.status_code == requests.codes.ok
        txns = [Transaction.from_dict(t) for t in response.json()]
        assert txns == [txn]
        _ = next(time_step)
        txn2 = m.longest_chain.create_subject(wallet, 2, subject)
        txn2.sign()
        response = ApiClient(host, wallet).post_transaction(txn2)
        response = ApiClient(host, wallet).get_pending_transactions()
        assert response.status_code == requests.codes.ok
        txns = [Transaction.from_dict(t) for t in response.json()]
        assert txns == [txn, txn2]
        _ = next(time_step)
        response = ApiClient(host, wallet).get_pending_transactions(
            earliest=now()
        )
        assert response.status_code == requests.codes.ok
        assert response.json() == []
