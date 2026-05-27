import httpx
import pytest

from cancelchain.api import API_TOKEN_SECONDS
from cancelchain.api_client import ApiClient
from cancelchain.miller import Miller
from cancelchain.wallet import Wallet


def test_invalid_wallet(app, host, mill_block, requests_proxy, wallet):
    with app.app_context():
        _m, _b = mill_block(wallet)
        w = Wallet()
        with pytest.raises(httpx.HTTPStatusError, match='401'):
            ApiClient(host, w).get_block()


def test_host_address(app, host_netloc, requests_proxy, wallet):
    with app.app_context():
        host = f'http://{wallet.address}@{host_netloc}'
        ApiClient(host, wallet)
        w = Wallet()
        invalid_host = f'http://{w.address}@{host_netloc}'
        with pytest.raises(Exception, match='Address/wallet mismatch'):
            ApiClient(invalid_host, wallet)


def test_expired_token(
    app, host, mill_block, requests_proxy, time_stepper, wallet
):
    with app.app_context():
        time_step = time_stepper(delta=API_TOKEN_SECONDS + 1)
        _ = next(time_step)
        client = ApiClient(host, wallet)
        _m, b = mill_block(wallet)
        response = client.get_block()
        assert response.status_code == httpx.codes.OK
        _ = next(time_step)
        response = client.get_block()
        assert response.status_code == httpx.codes.OK
        _ = next(time_step)
        m2 = Miller(milling_wallet=wallet)
        b = m2.create_block()
        m2.mill_block(b)
        response = client.post_block(b)
        assert response.status_code == httpx.codes.OK


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
