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
from gumptionchain.transaction import CoinbaseMetrics, Transaction
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
    'riiewRJm2wpE3rWTs1ikUc83so8ZXMX8vp9dUTnRgMC8GyfLr99M2sohUdWp62MRBJLvX1'
    'FzqWDaJihdNfsx3ybub662Njn3Rsig8zyWHeXLihfvjnFt6qgPMqfvTevYgmutwK7xWNW5'
    'GHd76EbVtHd7FwCTgMmt5aZC5n3Zem7w95SUUnQaUjsPXYACifojrMtn83L2pyFyzebdum'
    'gQqLTpTtH3hTFQ2HHypF4EGn78Fk66QAaoN99j11VbMFwaeuiycs341bxmkxymEjxUETZQ'
    'AfJDqXx4v9vHnEAr56EBLoQGqvHim7pw72Nk6YLgvgtNoweCkJ8UEw1X9frEniVd9bNFWn'
    'BoTxzuCNcvrjqcejjD3vkV5Qi5EgAiWSnnPwhxwuaFmdX5j2peZKBGQWAkHkXF1Pzqzs2s'
    'dortnKeJh21S6gGErfWsgDc2EHMg59W17buJk5ZpCqx9dvg8p6FQYMLkDYPPxLxSSaSciq'
    'ExANZA7rWJ5ZahAKg56pLDmXNkXxemvmoiaSK1yGetkiPLs8SP3cW3w94nzUMvGTLF1xmJ'
    'whYw9iJ3DPxr18KqPw7GNobBNzvjTuPmN77cvBRoRHTdiSe9gzmhJBtHrRMcLRT8hAgTot'
    'iZeQxobwvxbTVe9WoD59jyZmt8PiNQnFj4BQmhAL9tfMEZLs1M6jk8xpD5JG8saocEo5vG'
    '4btu9a4CcQsBLqj3MHM3RLdk6VuFFi3nrVCk3ud7U6eNBBH4C7De29kCTQQgRwtgx6woor'
    'mjLCbXKK3jvnZYDRVXvPuF48CgxpVLnTxPnipX5vLUu9xk98RiKDQ3FhgHfD4bohJEt4sB'
    'xVbkb31hevZcW54YVmQSRaJRBFaNrwXKmhi2ajBHrn36MbyopbK7qpyoQogS3k1pW4d6yh'
    'fXbneFxGSyQHjcEHxLsfxJtA6S9bQrU68EVqmpifZWwc4jZ67psURdnVYFHWRRt36dxmxD'
    'L1gaPfrsQoZ4V3d7U2Z1ry9YtTdXH4C3L6xb2pRfA4XDP2jMMauV6aTAKhrNwmmuzvGpv8'
    'wziTeHdfErN6XTmjz8VTXVPjjEHMY17ooM45vNVH58RgM8QiDBJVUSsLBebQqAD9aXJTpv'
    'jf2FYdSBBsXkn3yBv8aAAgufiibgPbmktXMog4ECMnGXofWG9f3hgWwuVpwVVrY8UDJQuy'
    '8Eep1AjaVvQjQUXcAkVUs1ShVKEDBiUqYNgQBYmdZWRJKhc1nePtY5UveVTnxe2ezQqVbx'
    'oPayvXoZzVd8D7zVqJeyeEuCr3sKdP4Fo8AcmX4SGhFyqqRbHjcjJ6p7sACQcaiSTmdk5p'
    '97VVRTVMzgp7C3yJ53C2AJwD9g1P4t2wVNSV9voCErjCe8b6EXabUbndb9dT11WZbdDmXL'
    'weKkZ3XiJjMNZW4H2riYJHmzqtYYHzaTbVJA6jSiz2xRuoZikZPR4YUEd2mAiUHyQVeQYT'
    'VTPkLz3TYnZLiDEyrEkup7yGMphuDeYkviETuUPrF4TAhd5BzHtmE1bzYu5LjZnKQ2jNTB'
    'LSjxyYRyC58K4LR4jeY4UXhpFVn3LTgmMUCd4cM9HVQ6zQBhEmxQ9x3P4sKe1gLv3pxNsv'
    'hkyTPxkAFapYc33kPMoEos3kkkgBYRSot1BCr1s8uurP7bgy1bWbe'
)
WALLET_PUBLIC_KEY_B64 = (
    'MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAvJo0ATMovGmJ+UPp1yj0na1aby'
    'TVT1QR9XvBMwmYYP0UmT17mdKmQGc+lv+W/rCwtjySu5ruWD9QDw8uAS1IQGMt1ad8Uq7h'
    'JPGIu5rROlnnE3UeETsy82jvOqm722xpqzsLKq/ZlJJFxTKsx+I/ahm+MDuHeT2TlwTv2W'
    'mD04VmbXSUJvIIt4LKmNcSPZNFd2HGvX+MLIybZhBy6Za2g2/gByLq0aR5FysLDOStCtM5'
    'JgaJzQVbPAf2kHMPE9pHC+N3DdnKkJObhpYay076Fdit6lLZ9Lzok8FmqL/2+4BDbCt51g'
    'Zpz5WRBJ7t5NOIPSKgvwhkM4710AdXSga8PQIDAQAB'
)
WALLET_ADDRESS = 'GCv9o9bgZzrYFdqcYeS6MnZNC5FraUmiwu244C4rbiGBUGC'
WALLET_SIGNATURE_DATA = 'helloworld'
WALLET_SIGNATURE = (
    'lw/CKt9GJqCwW4YPA/ixZ3AuAYj/RfnTGt/xHFRPdL6/4S6OnyX2ff1MkSXtb19VGbF+2B'
    'BkbusV6wOWywYJFSUDOlrFqQosGjFgva/dguXFT0QblmPFNX0J1ii3I6SSHaVCMkzIX+2c'
    '/Awfkx11dr9yJcJUV4XLZbPNnfqbIzCg7p54Fe5nClFBXClkxlwdFzGdbPvqxNeIof0QEV'
    'Q/ri6CPaTQT5fRs2SwaXc4+aLgCFk99u4wrs3J/B5+yhiWQJWbca652aXP4tD9DGBvynw8'
    'WEib/wJkA5mSh0cv8z5fQ+ZPjxhKGfVFBQG6nAlUayKPVwTahHP0t7NTWyA/uw=='
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
        (2, True, None, None, None, None),
        (2, True, None, None, None, None),
        (2, False, SUBJECT_1, None, None, None),
        (2, False, None, SUBJECT_1, None, 'opposition'),
        (2, False, None, None, SUBJECT_1, None),
    ]
)
def valid_outflow(request, wallet):
    address = wallet.address if request.param[1] else None
    return Outflow(
        amount=request.param[0],
        address=address,
        opposition=request.param[2],
        rescind=request.param[3],
        support=request.param[4],
        rescind_kind=request.param[5],
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
        opposition=request.param[2],
        rescind=request.param[3],
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
    txn.add_outflow(Outflow(amount=9, opposition=subject))
    txn.set_wallet(wallet)
    return txn


@pytest.fixture()
def invalid_txn(wallet):
    txn = Transaction()
    txn.set_wallet(wallet)
    return txn


@pytest.fixture(
    params=[
        (10, None, None, None, None),
        (10, 5, None, None, None),
        (10, 5, 5, None, None),
        (10, 5, 5, 5, None),
        (10, 5, 5, 5, 5),
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
        # Compute metrics over all txns in the block (before sealing the
        # coinbase is not yet appended, so b.txns == future regular_txns).
        metrics = sum(
            (c.validate_block_txn(b, t) for t in b.txns),
            CoinbaseMetrics(),
        )
        c.seal_block(b, milling_wallet or wallet, metrics)
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
