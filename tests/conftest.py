import datetime
import json
import logging
from tempfile import NamedTemporaryFile, TemporaryDirectory
from unittest.mock import patch
from urllib.parse import urlparse

import httpx
import pytest

from cancelchain import create_app
from cancelchain.application import create_clients
from cancelchain.block import Block
from cancelchain.chain import REWARD, Chain
from cancelchain.database import db
from cancelchain.miller import Miller
from cancelchain.payload import Inflow, Outflow, encode_subject
from cancelchain.transaction import Transaction
from cancelchain.util import now
from cancelchain.wallet import Wallet

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
    'riiewRJm2wpE3rWTs1ikUc83so8ZXMX8vp9dUTnRgMC8GyfLr99MXF11D1kxpKEq7xz7Gkc6'
    'jJEW4UuWx15AA9ScBBUqWDHmesBTkE5voGFkLkmoAN6GDivUBUKhprAAHB1nWU3kekb15xNZ'
    '9yGwHKPVxCNMMEkKKmbGKX5ipmL6gd15LfVfcH2FfMjLS6GfWGgRea747fmFmLTtTnjkrkMF'
    'psgj8PC842ZmBxjCH4385dhqoQ5cokuPiTK4ZcEV2wyXeMJ8nPZGEBAYYh7P5hbbwDteued2X'
    'fuFA45LJfDs4Qs4935mVNMBVLVxL9nYcWwAMbpberVEfWqLFstenfG297RpLBpRisRYPXVtHv'
    'hwNHCbxwxcFV9KShoSqYugm23mUNKwitcpwzs5zSnm5pRdDrb3UtVJV5sxkL3w4rsvejMmXb'
    'kNoE93qkXtg7ZSvjBxb8boyyrQDYSYiMJhEmjht8PLK5DDzsLRCb6EF29MURWR4TE1BemQ55'
    'qv86oe76ggZr3WzHW7WRvBVYDrWttitqit8Q5mcm4LqB39ciMyNWj4sVX2TZKc8KU3kMnELX'
    's2UDfthqvBaK6USMSBJp6x9Yq1NntxWJj43SVhnQcE8eJZnwj2Q1MuAdeoMRd7cMGErDqyw1'
    'pkYcMGJbfiveRLPpXFjdRuEwTNmX1UAHofayixa3ughnGzhkMcN6TVKovhxHbgrmrk4FaY5oi'
    'u3zNWZBH1KaLxSNoZFaGuAFENKdtUsN31AoPvF7ndrWWGpPFTrsp2L68c6rAnZogunmTDCkAh'
    'kPC58S9ztCxtMRd9CDbmdgQpZBZYwTBjbN8WJuD4h9qERawm3LTA58pSwUpFq2S2Eqpu7v7bf'
    'iNuebkLEWsirEheRHDY9EC2a1B9frtAojDR4x651mzdYwLAi42dkM9NuRkQvBrQmz6rZgRYii'
    'CnMM5zEMeNNC5oGavGxTupQ5TjBFZqF9LuenK5XiaFceTe6TPyeLo4EXEzfHqdn66B1Ka8nRe'
    'kYbfcwe8QWByEu4Gx4UQ87bZ5qnifC3bBimFEY8HN4Ce4XNUqkRVJrJ4WVULiBeR2BePgk8f'
    'Ts2nRcdaEmJ4QcsUcHv8nHyhgnT3dRZEzZvYfwhLMP2ELcfDBr7sx3SUBxTdg23rvhNWMBcDb'
    'PBnanCvnftH9DhZ84fKJ7yxDGqgff7nkkM9utdquHLwzWqL14X7b3qZRcQstp7Twb5iEgkmW2'
    'bXdcxRsPw2hqKRz7bHAmDbkSqFNvX4uEFNoddGyfdpTsCoqFrjZsV9idNUjEXhGXbwEcbApzQ'
    'vYcoMnSEZc1Z7Ww1cpGfty8buFnKm6Pp6AVk6KY3dnLK3Eu73amEw3SAhmBcfDkCcSUqSajbs'
    'BNBQHhrXQEqr9UGWngNyZofCLMz27LUyuKBQ4ezhfoSUmJymMtZvTuEc7QhyrsDxtf7wppXfj'
    'YSBfFhiBceeS8ZstCataRrGJqLwXE89w8SEr7E1mxrSazEbWRH8qTfpBdGKBDrhcP48UYop5b'
    'HkTvwYGDt1PQuoUbeQkV2mnVRbdf6BgQEAKCm1CuJNpZ6GVyzjSqRKmr3YXG8VeiTYkGNfozi'
    'Y21VskgRcn62MZk3DLmEqe4CaZeYSA7ADadQ9YSgvSrbwLWidTp5kZHxRAbbaKfz9'
)
WALLET_PUBLIC_KEY_B64 = (
    'MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAzqvtwjb6TeXLZd9JWYUNaE3CgRq'
    'mBkZYWJi4UhxdijozRNrwIDCe/kOu+P0iaN2HKe4i9qp0goYOmSVxPQQcJYIxuW5aPxZasu'
    'FYqCM6tSdIFT5ohdfn4Po9I3YMXqi5D2hpJJIVjzKffjNgex8ciS5iduSu/rBNwoP/FQmj1'
    'P7SFv6uau1zAOXAsOSDSoFz/zubJgqMdHOh33OfIk2LW7xSyDZtUksok6fWqAHe2U04BF8E'
    'AMBP4OXGvcvgTIKWiw+k9QiUTrKpLEdcb/pEnMbrIxdOMFl7MShamopqYE8ja1MHRlUxGK8'
    'nZhj4PGg0XohZODQ8Ewtaz4OycPjobwIDAQAB'
)
WALLET_ADDRESS = 'CC6L2eN8RKzRFfRF97gviHeSUeR4n2RGRVmVPAa9fEcLMMCC'
WALLET_SIGNATURE_DATA = 'helloworld'
WALLET_SIGNATURE = (
    'ph2w0mVx7bMDJlJLp65J09F+85R8DtpHzwsAW/3O0vX4Z01iQ+/QEC1ie0mnObi/YjFImO0'
    'gmQJ2isQ34BPr3EzqPhtY1MgqKDmTyUXSt2qHQ7gVrs3iaFd7XCSiLMqDKjRcblefzb2A7L'
    'u0j/lP9k664TtZDIIkhcZ6Snmn0f66En91bWiGKQv63bk/cdzHPZMFtJcg178aw4bkwPsVg'
    'iXaDVAIn4wR1L0/MpwfEwrTErKng2BwVxGEjxn6ZxCLMAb13HuuHSnLFUirH0HbZ0vU0jNg'
    'MIS5fq67al6CPp41joQ/DyhmxaOVkbZxp38IF83rKoDKuHVTtwT9mBldmA=='
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
    with patch('cancelchain.chain.MAX_TARGET', 'F' * 64) as _fixture:
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
    return Transaction.coinbase(wallet, *request.param)


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
def remote_app(miller_2_wallet, miller_wallet, wallet):
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
        'cancelchain.api_client._make_client',
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
        'cancelchain.api_client._make_client',
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
