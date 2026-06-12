import httpx
import pytest

from gumptionchain import signing
from gumptionchain.api_client import ApiClient
from gumptionchain.block import Block
from gumptionchain.signing_key import SigningKey


def test_invalid_signing_key(
    app, host, mill_block, requests_proxy, signing_key
):
    # A signing_key in no *_ADDRESSES list signs a valid request but has no live
    # role -> 403 (forbidden), not 401: the signature verifies.
    with app.app_context():
        _m, _b = mill_block(signing_key)
        w = SigningKey()
        with pytest.raises(httpx.HTTPStatusError, match='403'):
            ApiClient(host, w).get_block()


def test_host_address(app, host_netloc, requests_proxy, signing_key):
    with app.app_context():
        host = f'http://{signing_key.address}@{host_netloc}'
        ApiClient(host, signing_key)
        w = SigningKey()
        invalid_host = f'http://{w.address}@{host_netloc}'
        with pytest.raises(Exception, match='Address/signing-key mismatch'):
            ApiClient(invalid_host, signing_key)


def test_get_attaches_signature_headers(
    app, host, mill_block, requests_proxy, signing_key
):
    """ApiClient.get signs the request: a round-trip authenticates, and the
    transmitted request carries the gc-sig-v1 GC-* headers.
    """
    sent = {}
    with app.app_context():
        _m, _b = mill_block(signing_key)
        client = ApiClient(host, signing_key)
        orig_send = client._client.send

        def _capture(req, *args, **kwargs):
            sent['headers'] = req.headers
            return orig_send(req, *args, **kwargs)

        client._client.send = _capture
        response = client.get_block()
        assert response.status_code == httpx.codes.OK
        assert sent['headers'][signing.H_VERSION] == signing.SIG_VERSION
        assert sent['headers'][signing.H_ADDRESS] == signing_key.address
        assert signing.H_SIGNATURE in sent['headers']


def test_get_blocks_returns_block_list(
    app, host, mill_block, requests_proxy, signing_key
):
    """get_blocks parses the JSON array into a list[Block], ascending by
    idx."""
    with app.app_context():
        _m, b0 = mill_block(signing_key)  # idx 0
        _m, b1 = mill_block(signing_key)  # idx 1
        blocks = ApiClient(host, signing_key).get_blocks(0, 2)
        assert isinstance(blocks, list)
        assert all(isinstance(b, Block) for b in blocks)
        assert [b.idx for b in blocks] == [0, 1]
        assert blocks[0].block_hash == b0.block_hash
        assert blocks[1].block_hash == b1.block_hash


def test_api_client_close_releases_underlying_client(app, host, signing_key):
    """ApiClient.close() releases the wrapped httpx.Client."""
    with app.app_context():
        c = ApiClient(host, signing_key)
        assert c._client.is_closed is False
        c.close()
        assert c._client.is_closed is True


def test_api_client_context_manager_closes_on_exit(app, host, signing_key):
    """`with ApiClient(...) as c:` closes the wrapped httpx.Client on exit."""
    with app.app_context():
        with ApiClient(host, signing_key) as c:
            assert c._client.is_closed is False
        assert c._client.is_closed is True
