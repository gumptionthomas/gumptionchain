import httpx
from flask import Flask

from gumptionchain import node_proxy_blueprint


class FakeResponse:
    def __init__(self, status_code, body=None, text=''):
        self.status_code = status_code
        self._body = body
        self.text = text

    def json(self):
        if self._body is None:
            raise ValueError('no json')  # noqa: EM101 — test stub
        return self._body


class FakeClient:
    """A stand-in ApiClient: each method returns a preset FakeResponse (or
    raises a preset exception). Records calls for assertions."""

    def __init__(self, **responses):
        self._responses = responses
        self.calls = []

    def _resp(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))
        r = self._responses[name]
        if isinstance(r, Exception):
            raise r
        return r

    def get_signing_key_balance(self, address, *, raise_for_status=True):
        return self._resp('balance', address)

    def get_support_balance(self, subject, *, raise_for_status=True):
        return self._resp('support', subject)

    def get_opposition_balance(self, subject, *, raise_for_status=True):
        return self._resp('opposition', subject)

    def get_subject_search(self, query, limit, *, raise_for_status=True):
        return self._resp('search', query, limit)

    def get_support_transaction(
        self, pk, amount, subject, *, raise_for_status=True
    ):
        return self._resp('build_support', pk, amount, subject)

    def get_opposition_transaction(
        self, pk, amount, subject, *, raise_for_status=True
    ):
        return self._resp('build_oppose', pk, amount, subject)

    def get(self, path, *, raise_for_status=True):
        return self._resp('status', path)

    def post(self, path, *, data=None, headers=None, raise_for_status=True):
        return self._resp('submit', path, data)


def _app(client, **bp_kwargs):
    app = Flask(__name__)
    app.register_blueprint(node_proxy_blueprint(lambda: client, **bp_kwargs))
    return app.test_client()


def test_balance_converts_grains_to_grit():
    client = FakeClient(
        balance=FakeResponse(200, {'balance': 250, 'as_of_block': 'b1'})
    )
    resp = _app(client).get('/api/node/balance/GCaddrGC')
    assert resp.status_code == 200
    assert resp.get_json() == {'grit': 2.5, 'grains': 250, 'as_of_block': 'b1'}


def test_subject_balances_normalizes_and_converts():
    client = FakeClient(
        support=FakeResponse(200, {'support': 500, 'as_of_block': 'b1'}),
        opposition=FakeResponse(200, {'balance': 300, 'as_of_block': 'b1'}),
    )
    resp = _app(client).get(
        '/api/node/subject/balances?subject=Tabs %3E Spaces'
    )
    assert resp.status_code == 200
    # Proves normalization: support.grains came from the node's "support" key
    # while opposition.grains came from the node's "balance" key (#283).
    assert resp.get_json() == {
        'subject': 'Tabs > Spaces',
        'support': {'grit': 5.0, 'grains': 500},
        'opposition': {'grit': 3.0, 'grains': 300},
    }


def test_subject_search_converts_grains_to_grit():
    client = FakeClient(
        search=FakeResponse(
            200,
            {
                'subjects': [
                    {'subject': 'Tabs', 'opposition': 300, 'support': 150},
                    {'subject': 'Tango', 'opposition': 0, 'support': 50},
                ],
                'as_of_block': 'b1',
            },
        )
    )
    resp = _app(client).get('/api/node/subject/search?q=ta&limit=8')
    assert resp.status_code == 200
    assert resp.get_json() == {
        'subjects': [
            {
                'subject': 'Tabs',
                'support': {'grit': 1.5, 'grains': 150},
                'opposition': {'grit': 3.0, 'grains': 300},
            },
            {
                'subject': 'Tango',
                'support': {'grit': 0.5, 'grains': 50},
                'opposition': {'grit': 0.0, 'grains': 0},
            },
        ]
    }
    assert client.calls[0][1] == ('ta', '8')


def test_subject_search_maps_node_down_to_502():
    client = FakeClient(search=httpx.RequestError('boom'))
    resp = _app(client).get('/api/node/subject/search?q=ta')
    assert resp.status_code == 502


def test_subject_balances_rejects_bad_subject():
    client = FakeClient()
    resp = _app(client).get('/api/node/subject/balances?subject=')
    assert resp.status_code == 400
    assert 'subject' in resp.get_json()['error']


def test_build_support_converts_grit_and_passes_raw_subject():
    unsigned = {'txid': 't1', 'outflows': [{'amount': 700, 'support': 'enc'}]}
    client = FakeClient(build_support=FakeResponse(200, unsigned))
    resp = _app(client).post(
        '/api/node/txn/support',
        json={'public_key': 'PUB', 'amount_grit': 7, 'subject': 'goblins'},
    )
    assert resp.status_code == 200
    assert resp.get_json() == unsigned
    name, args, _ = client.calls[0]
    assert name == 'build_support'
    assert args == ('PUB', 700, 'goblins')


def test_build_rejects_non_positive_and_sub_grain_amounts():
    client = FakeClient()
    c = _app(client)
    for bad in (0, -5, 0.001, 'x'):
        resp = c.post(
            '/api/node/txn/oppose',
            json={'public_key': 'P', 'amount_grit': bad, 'subject': 'x'},
        )
        assert resp.status_code == 400, bad


def test_submit_relays_signed_and_returns_txid():
    client = FakeClient(submit=FakeResponse(201, {'received': 't'}))
    signed = {'txid': 'abc123', 'signature': 'SIG', 'outflows': []}
    resp = _app(client).post('/api/node/txn/submit', json={'signed': signed})
    assert resp.status_code == 200
    assert resp.get_json() == {'txid': 'abc123'}
    name, args, _ = client.calls[0]
    assert name == 'submit'
    assert args[0] == '/api/transaction/abc123'


def test_submit_rejects_unsigned_payload():
    client = FakeClient()
    resp = _app(client).post(
        '/api/node/txn/submit', json={'signed': {'txid': 'x'}}
    )
    assert resp.status_code == 400  # missing signature


def test_status_maps_canonical_to_milled():
    client = FakeClient(
        status=FakeResponse(
            200,
            {
                'status': 'canonical',
                'block_hash': 'B',
                'height': 5,
                'confirmations': 3,
            },
        )
    )
    resp = _app(client).get('/api/node/txn/t1/status')
    assert resp.get_json() == {
        'state': 'milled',
        'block': 'B',
        'confirmations': 3,
    }


def test_status_maps_pending_and_orphaned_to_pending():
    for st in ('pending', 'orphaned'):
        client = FakeClient(
            status=FakeResponse(200, {'status': st, 'block_hash': None})
        )
        resp = _app(client).get('/api/node/txn/t1/status')
        assert resp.get_json() == {'state': 'pending'}


def test_status_unknown_txid_is_404():
    client = FakeClient(status=FakeResponse(404, {'error': 'not found'}))
    resp = _app(client).get('/api/node/txn/nope/status')
    assert resp.status_code == 404


def test_node_transport_error_is_502():
    client = FakeClient(balance=httpx.ConnectError('down'))
    resp = _app(client).get('/api/node/balance/GCaddrGC')
    assert resp.status_code == 502
    assert resp.get_json()['error'] == 'node unavailable'


def test_node_4xx_is_passed_through_as_400():
    client = FakeClient(
        build_support=FakeResponse(400, {'error': 'insufficient funds'})
    )
    resp = _app(client).post(
        '/api/node/txn/support',
        json={'public_key': 'P', 'amount_grit': 1, 'subject': 'x'},
    )
    assert resp.status_code == 400
    assert resp.get_json()['error'] == 'insufficient funds'


def test_rate_limit_hook_returns_429():
    client = FakeClient(
        balance=FakeResponse(200, {'balance': 0, 'as_of_block': 'b'})
    )
    c = _app(client, rate_limit=lambda req: False)
    resp = c.get('/api/node/balance/GCaddrGC')
    assert resp.status_code == 429


def test_oversized_body_is_413():
    client = FakeClient()
    c = _app(client, max_body_bytes=8)
    resp = c.post(
        '/api/node/txn/support',
        json={'public_key': 'P', 'amount_grit': 1, 'subject': 'x'},
    )
    assert resp.status_code == 413
