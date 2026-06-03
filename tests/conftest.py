import datetime
import json
import logging
from tempfile import NamedTemporaryFile, TemporaryDirectory
from unittest.mock import patch
from urllib.parse import urlparse

import httpx
import pytest

from gumptionchain import create_app
from gumptionchain.application import create_clients
from gumptionchain.block import Block
from gumptionchain.chain import GENESIS_HASH, REWARD, Chain
from gumptionchain.database import db
from gumptionchain.miller import Miller
from gumptionchain.payload import Inflow, Outflow, encode_subject
from gumptionchain.transaction import Transaction
from gumptionchain.util import now
from gumptionchain.wallet import Wallet

READER_WALLET = Wallet()
TRANSACTOR_WALLET = Wallet()
MILLER_WALLET = Wallet()
MILLER_2_WALLET = Wallet()
HOST = 'http://localhost:8080'
REMOTE_HOST = 'http://peer.node:8888'
SUBJECT_RAW = 'failing tests'
SUBJECT_1 = encode_subject('bugs')
SUBJECT_2 = encode_subject('vogons')
TXID_1 = '0' * 64
# HS256 requires ≥32 bytes; this is 35 bytes (above the threshold so
# pyjwt 2.13+ doesn't emit InsecureKeyLengthWarning during tests).
TEST_SECRET_KEY = 'test-secret-key-for-phase-3-32bytes'
WALLET_PRIVATE_KEY_B58 = (
    '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'
)
WALLET_PUBLIC_KEY_B64 = (
    'MIIBojANBgkqhkiG9w0BAQEFAAOCAY8AMIIBigKCAYEA45lLTnrhnG4qVvJ6UACmyV1PzD'
    'QHvdD92JP3UIe7B5yFQmHwMV/q+PLqKplBWkCiyCFR1jyO/ZPbKUjvMKxQ8SNr0FqUirpq'
    'T6iWiMM2NG+6rPkAZxFK46TqD3GHHfw3OY07GHD1hGwcs/D5L9+dhi75LimkUcJTqGP1zk'
    'XQVL7aO7VXSB2hiywz+9lJFrncDB8euq0oqr5ZjpZIu4OzWPYdOxqtgY5KZtRVVNc338JB'
    'HMlsuzofLf9T6sv0AKKJru+aE2d5urVJuYEaJRau1Gvd729SIrUA24DZJCssCJ0EGfAd6t'
    '+fMbbEx9N6xoi94TY+qV5zkAeMHz+wQZM0Z2n0LZfUZRwS2gRq6jGjqIxw8Zf806YoRKhT'
    '3CqhIkYAKG5vP3jT46x1Bb6uGZ9eSW0c4gNr+XV+WWTQ85VObLyuNmKbZQgnpe542zG8s3'
    'EosWsydH6hJKNvtLuiK00gTYIzYKu+MvToLYTYM6ea3x3BAmYd988o7SHwBW5R2ngZAgMB'
    'AAE='
)
WALLET_ADDRESS = 'GCHbFZxRBDZna9WpvDvRjJSdDWomB6PTVRT2zjRgFq6WDUGC'
WALLET_SIGNATURE_DATA = 'helloworld'
WALLET_SIGNATURE = (
    'yiZ0qpvhHay5Q5NPEM1k+ZU36gFBOKfm+bxvmwXI1+xd8hGYkrNfmVzAcF+oCnT+E716y3'
    'va4ujap7T1oKiJDgULGwrCmQUG+HHxEDvWd0LqluHSGOsd6U/xj7X6PGyCgbYIW1Qf1v/J'
    'ZK4sE3vHC8IkSjK6TMo34L0pEgUfRgjMKjD/IfcNqmZyqUm1U/+RLSEc+fLlpizELWenVl'
    'WnKO+Iwxr3X+pn565bxH60+wrs6ZqPBQJCUUE70oxWhPtYQdMBOpZk2CdPGWDZZH9zLqom'
    'S6ep6OQmp1UEtOMO7mscUDTGQpxXjbPsCdyzgLromR4qA6Qmzgh2kDXaFTyc2xuSQnlvZl'
    'GRZMmAGOv5zuxgkVl+sZCJjyz+I2gUa1dldYIoXYoPTnkqNGDuAsozBtk9AmoWeLBHDbRh'
    'DF5KgrTm1VGq84VWE+izA0Z/rRNeDZXL7AzD35/LGMoDdfdV8iM1zbHFpReq/0PSdhVMa4'
    'kRGjfa6Elqc9wSP2kkg06j'
)


def pytest_addoption(parser):
    parser.addoption(
        '--runmulti',
        action='store_true',
        default=False,
        help='run multiprocessing tests',
    )


def pytest_configure(config):
    config.addinivalue_line(
        'markers', 'multi: mark test as using multiprocessing'
    )


def pytest_collection_modifyitems(config, items):
    if config.getoption('--runmulti'):
        return
    skip_multi = pytest.mark.skip(reason='need --runmulti option to run')
    for item in items:
        if 'multi' in item.keywords:
            item.add_marker(skip_multi)


@pytest.fixture()
def logger():
    return logging.getLogger()


@pytest.fixture()
def time_stepper(time_machine):
    def _time_stepper_gen(start=None, delta=60):
        dt = start or now()
        while True:
            time_machine.move_to(dt)
            yield dt
            dt += datetime.timedelta(seconds=delta)

    return _time_stepper_gen


@pytest.fixture(scope='session', autouse=True)
def easy_mill_chain():
    with patch('gumptionchain.chain.MAX_TARGET', 'F' * 64) as _fixture:
        yield _fixture


@pytest.fixture()
def host():
    return HOST


@pytest.fixture()
def host_netloc(host):
    p = urlparse(host)
    netloc = f'{p.hostname}'
    if p.port:
        netloc = f'{p.hostname}:{p.port}'
    return netloc


@pytest.fixture()
def remote_host():
    return REMOTE_HOST


@pytest.fixture()
def remote_host_netloc(remote_host):
    p = urlparse(remote_host)
    netloc = f'{p.hostname}'
    if p.port:
        netloc = f'{p.hostname}:{p.port}'
    return netloc


@pytest.fixture()
def subject_raw():
    return SUBJECT_RAW


@pytest.fixture()
def subject(subject_raw):
    return encode_subject(subject_raw)


@pytest.fixture()
def txid():
    return TXID_1


@pytest.fixture()
def wallet():
    return Wallet(b58ks=WALLET_PRIVATE_KEY_B58)


@pytest.fixture()
def reader_wallet():
    return READER_WALLET


@pytest.fixture()
def transactor_wallet():
    return TRANSACTOR_WALLET


@pytest.fixture()
def miller_wallet():
    return MILLER_WALLET


@pytest.fixture()
def miller_2_wallet():
    return MILLER_2_WALLET


@pytest.fixture()
def reward():
    return REWARD


@pytest.fixture(
    params=[
        (2, True, None, None, None),
        (2, True, None, None, None),
        (2, False, SUBJECT_1, None, None),
        (2, False, None, SUBJECT_1, None),
        (2, False, None, None, SUBJECT_1),
    ]
)
def valid_outflow(request, wallet):
    address = wallet.address if request.param[1] else None
    return Outflow(
        amount=request.param[0],
        address=address,
        subject=request.param[2],
        forgive=request.param[3],
        support=request.param[4],
    )


@pytest.fixture(
    params=[
        (0, True, None, None, None),
        (10, True, SUBJECT_1, None, None),
        (10, True, None, SUBJECT_1, None),
        (10, False, SUBJECT_1, SUBJECT_2, None),
        (10, False, SUBJECT_1, None, SUBJECT_2),
    ]
)
def invalid_outflow(request, wallet):
    return Outflow(
        amount=wallet.address if request.param[0] else None,
        address=request.param[1],
        subject=request.param[2],
        forgive=request.param[3],
        support=request.param[4],
    )


@pytest.fixture(params=[(TXID_1, 0), (TXID_1, 1)])
def valid_inflow(request):
    return Inflow(outflow_txid=request.param[0], outflow_idx=request.param[1])


@pytest.fixture(params=[(None, None), (None, 0), (TXID_1, None), (TXID_1, -1)])
def invalid_inflow(request):
    return Inflow(outflow_txid=request.param[0], outflow_idx=request.param[1])


@pytest.fixture()
def valid_txn(valid_inflow, valid_outflow, wallet):
    txn = Transaction(inflows=[valid_inflow], outflows=[valid_outflow])
    txn.set_wallet(wallet)
    return txn


@pytest.fixture()
def single_txn(subject, txid, wallet):
    txn = Transaction()
    txn.add_inflow(Inflow(outflow_txid=txid, outflow_idx=0))
    txn.add_outflow(Outflow(amount=9, subject=subject))
    txn.set_wallet(wallet)
    return txn


@pytest.fixture()
def invalid_txn(wallet):
    txn = Transaction()
    txn.set_wallet(wallet)
    return txn


@pytest.fixture(
    params=[
        (10, None, None, None),
        (10, 5, None, None),
        (10, 5, 5, None),
        (10, 5, 5, 5),
    ]
)
def valid_coinbase_txn(request, wallet):
    return Transaction.coinbase(wallet, *request.param, prev_hash=GENESIS_HASH)


@pytest.fixture()
def valid_block(valid_txn, wallet):
    valid_txn.seal()
    valid_txn.sign()
    return Block(txns=[valid_txn])


@pytest.fixture()
def single_block(single_txn):
    single_txn.seal()
    single_txn.sign()
    return Block(txns=[single_txn])


@pytest.fixture()
def mill_block(host):
    def _mill_block(milling_wallet):
        m = Miller(host=host, milling_wallet=milling_wallet)
        b = m.create_block()
        m.mill_block(b)
        return m, b

    return _mill_block


@pytest.fixture()
def add_chain_block(wallet):
    def _add_chain_block(chain=None, block=None, milling_wallet=None):
        c = chain or Chain()
        b = block or Block()
        c.link_block(b)
        c.seal_block(b, milling_wallet or wallet)
        b.mill()
        c.add_block(b)
        return c, b

    return _add_chain_block


@pytest.fixture()
def wallet_private_key_b58():
    return WALLET_PRIVATE_KEY_B58


@pytest.fixture()
def wallet_public_key_b64():
    return WALLET_PUBLIC_KEY_B64


@pytest.fixture()
def wallet_address():
    return WALLET_ADDRESS


@pytest.fixture()
def wallet_dict():
    return {'private_key': WALLET_PRIVATE_KEY_B58}


@pytest.fixture()
def wallet_json(wallet_dict):
    return json.dumps(wallet_dict)


@pytest.fixture()
def wallet_signature_data():
    return WALLET_SIGNATURE_DATA


@pytest.fixture()
def wallet_signature():
    return WALLET_SIGNATURE


@pytest.fixture
def app(
    reader_wallet,
    transactor_wallet,
    miller_2_wallet,
    miller_wallet,
    host_netloc,
    remote_host_netloc,
    wallet,
):
    address = wallet.address
    command_host = f'http://{address}@{host_netloc}'
    peer_host = f'http://{miller_2_wallet.address}@{remote_host_netloc}'
    with (
        NamedTemporaryFile(suffix='.sqlite') as db_file,
        TemporaryDirectory() as walletdir,
    ):
        db_uri = f'sqlite:///{db_file.name}'
        wallet.to_file(walletdir=walletdir)
        miller_2_wallet.to_file(walletdir=walletdir)
        app = create_app(
            config_map={
                'TESTING': True,
                'WTF_CSRF_ENABLED': False,
                'SECRET_KEY': TEST_SECRET_KEY,
                'SQLALCHEMY_DATABASE_URI': db_uri,
                'NODE_HOST': f'http://{host_netloc}',
                'PEERS': [peer_host],
                'WALLET_DIR': walletdir,
                'DEFAULT_COMMAND_HOST': command_host,
                'ADMIN_ADDRESSES': [address],
                'MILLER_ADDRESSES': [miller_wallet.address],
                'TRANSACTOR_ADDRESSES': [transactor_wallet.address],
                'READER_ADDRESSES': [reader_wallet.address],
            }
        )
        with app.app_context():
            db.create_all()
        yield app


@pytest.fixture
def remote_app(
    miller_2_wallet, miller_wallet, wallet, host_netloc, remote_host_netloc
):
    peer_host = f'http://{miller_wallet.address}@{host_netloc}'
    with (
        NamedTemporaryFile(suffix='.sqlite') as db_file,
        TemporaryDirectory() as walletdir,
    ):
        db_uri = f'sqlite:///{db_file.name}'
        wallet.to_file(walletdir=walletdir)
        miller_2_wallet.to_file(walletdir=walletdir)
        miller_wallet.to_file(walletdir=walletdir)
        app = create_app(
            config_map={
                'TESTING': True,
                'WTF_CSRF_ENABLED': False,
                'SECRET_KEY': TEST_SECRET_KEY,
                'SQLALCHEMY_DATABASE_URI': db_uri,
                'NODE_HOST': f'http://{remote_host_netloc}',
                'PEERS': [peer_host],
                'WALLET_DIR': walletdir,
                'MILLER_ADDRESSES': [miller_2_wallet.address],
            }
        )
        with app.app_context():
            db.create_all()
        yield app


@pytest.fixture
def config_app():
    app = create_app()
    yield app


@pytest.fixture
def test_client(app):
    return app.test_client()


@pytest.fixture
def remote_test_client(remote_app):
    return remote_app.test_client()


@pytest.fixture
def requests_proxy(app, host):
    """WSGITransport-backed httpx client that routes outbound HTTP from
    ApiClient into the Flask test app. Named `requests_proxy` for
    backward-compatibility with the ~25 tests that consume the fixture
    by name; the underlying mechanism is httpx + WSGITransport.

    Side effect: rebuilds app.clients under the active _make_client
    monkeypatch so peer-gossip code in Node / Miller routes through
    WSGI too.
    """
    transport = httpx.WSGITransport(app=app)

    def _wsgi_make_client(base_url, timeout):
        return httpx.Client(
            transport=transport, base_url=base_url, timeout=timeout
        )

    with patch(
        'gumptionchain.api_client._make_client',
        side_effect=_wsgi_make_client,
    ):
        for c in list(app.clients.values()):
            c.close()
        app.clients = create_clients(app)
        with httpx.Client(transport=transport, base_url=host) as client:
            yield client
        for c in list(app.clients.values()):
            c.close()


@pytest.fixture
def remote_requests_proxy(remote_app, remote_host):
    """Counterpart to `requests_proxy` for the second Flask app used in
    peer-gossip tests. See `requests_proxy` for mechanism.
    """
    transport = httpx.WSGITransport(app=remote_app)

    def _wsgi_make_client(base_url, timeout):
        return httpx.Client(
            transport=transport, base_url=base_url, timeout=timeout
        )

    with patch(
        'gumptionchain.api_client._make_client',
        side_effect=_wsgi_make_client,
    ):
        for c in list(remote_app.clients.values()):
            c.close()
        remote_app.clients = create_clients(remote_app)
        with httpx.Client(transport=transport, base_url=remote_host) as client:
            yield client
        for c in list(remote_app.clients.values()):
            c.close()


@pytest.fixture
def runner(app):
    return app.test_cli_runner()


@pytest.fixture(
    params=[0, 1, 2],
    ids=['chain_empty', 'chain_genesis_block', 'chain_two_blocks'],
)
def valid_chain(add_chain_block, app, request, wallet):
    with app.app_context():
        chain = Chain()
        for _i in range(0, request.param):
            add_chain_block(chain=chain)
        return chain


@pytest.fixture()
def remote_chain(mill_block, remote_app, time_machine, wallet):
    with remote_app.app_context():
        now_dt = now()
        earlier_dt = now_dt - datetime.timedelta(minutes=10)
        time_machine.move_to(earlier_dt)
        m, _ = mill_block(wallet)
        time_machine.move_to(now_dt)
        return m.longest_chain
