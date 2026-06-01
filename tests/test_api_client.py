import httpx
import pytest

from cancelchain import signing
from cancelchain.api_client import ApiClient
from cancelchain.wallet import Wallet


def test_invalid_wallet(app, host, mill_block, requests_proxy, wallet):
    # A wallet in no *_ADDRESSES list signs a valid request but has no live
    # role -> 403 (forbidden), not 401: the signature verifies.
    with app.app_context():
        _m, _b = mill_block(wallet)
        w = Wallet()
        with pytest.raises(httpx.HTTPStatusError, match='403'):
            ApiClient(host, w).get_block()


def test_host_address(app, host_netloc, requests_proxy, wallet):
    with app.app_context():
        host = f'http://{wallet.address}@{host_netloc}'
        ApiClient(host, wallet)
        w = Wallet()
        invalid_host = f'http://{w.address}@{host_netloc}'
        with pytest.raises(Exception, match='Address/wallet mismatch'):
            ApiClient(invalid_host, wallet)


def test_get_attaches_signature_headers(
    app, host, mill_block, requests_proxy, wallet
):
    """ApiClient.get signs the request: a round-trip authenticates, and the
    transmitted request carries the cc-sig-v1 CC-* headers.
    """
    sent = {}
    with app.app_context():
        _m, _b = mill_block(wallet)
        client = ApiClient(host, wallet)
        orig_send = client._client.send

        def _capture(req, *args, **kwargs):
            sent['headers'] = req.headers
            return orig_send(req, *args, **kwargs)

        client._client.send = _capture
        response = client.get_block()
        assert response.status_code == httpx.codes.OK
        assert sent['headers'][signing.H_VERSION] == signing.SIG_VERSION
        assert sent['headers'][signing.H_ADDRESS] == wallet.address
        assert signing.H_SIGNATURE in sent['headers']


def test_api_client_close_releases_underlying_client(app, host, wallet):
    """ApiClient.close() releases the wrapped httpx.Client."""
    with app.app_context():
        c = ApiClient(host, wallet)
        assert c._client.is_closed is False
        c.close()
        assert c._client.is_closed is True


def test_api_client_context_manager_closes_on_exit(app, host, wallet):
    """`with ApiClient(...) as c:` closes the wrapped httpx.Client on exit."""
    with app.app_context():
        with ApiClient(host, wallet) as c:
            assert c._client.is_closed is False
        assert c._client.is_closed is True
