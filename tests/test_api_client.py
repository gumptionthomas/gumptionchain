import pytest
import requests
from cancelchain.api import API_TOKEN_SECONDS
from cancelchain.api_client import ApiClient
from cancelchain.miller import Miller
from cancelchain.wallet import Wallet


def test_invalid_wallet(app, host, mill_block, requests_proxy, wallet):
    with app.app_context():
        m, b = mill_block(wallet)
        w = Wallet()
        with pytest.raises(requests.exceptions.HTTPError, match='401'):
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
        m, b = mill_block(wallet)
        response = client.get_block()
        assert response.status_code == requests.codes.ok
        _ = next(time_step)
        response = client.get_block()
        assert response.status_code == requests.codes.ok
        _ = next(time_step)
        m2 = Miller(milling_wallet=wallet)
        b = m2.create_block()
        m2.mill_block(b)
        response = client.post_block(b)
        assert response.status_code == requests.codes.ok
