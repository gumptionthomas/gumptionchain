from __future__ import annotations

import datetime
from types import TracebackType
from typing import Literal, Self

import httpx

from gumptionchain import signing
from gumptionchain.block import Block
from gumptionchain.transaction import Transaction
from gumptionchain.util import dt_2_ciso, host_address
from gumptionchain.wallet import Wallet

PEER_HOST_HEADER = 'Peer-Hosts'
ADDRESS_MISMATCH_MSG = 'Address/wallet mismatch'


def _make_client(base_url: str, timeout: float) -> httpx.Client:
    """Module-scope factory so tests can monkeypatch a single seam to
    inject httpx.WSGITransport(app=flask_app). Production callers never
    touch this directly.
    """
    return httpx.Client(base_url=base_url, timeout=timeout)


def json_header(headers: dict[str, str] | None = None) -> dict[str, str]:
    headers = headers or {}
    headers['Content-Type'] = 'application/json'
    return headers


def peer_header(
    visited_hosts: list[str] | None,
    headers: dict[str, str] | None = None,
) -> dict[str, str]:
    headers = headers or {}
    if visited_hosts:
        headers[PEER_HOST_HEADER] = ','.join(visited_hosts)
    return headers


class ApiClient:
    def __init__(
        self,
        host: str,
        wallet: Wallet,
        timeout: int | float | None = None,
    ) -> None:
        host, address = host_address(host)
        if address and address != wallet.address:
            raise ValueError(ADDRESS_MISMATCH_MSG)
        self.host = host
        self.wallet = wallet
        self.timeout: int | float = timeout if timeout is not None else 10
        self._client = _make_client(self.host, float(self.timeout))

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    def _send(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        content: str | bytes | None = None,
        timeout: int | float | None = None,
        raise_for_status: bool = True,
    ) -> httpx.Response:
        timeout_v: int | float = self.timeout if timeout is None else timeout
        body = (
            content.encode() if isinstance(content, str) else (content or b'')
        )
        req = self._client.build_request(
            method,
            path,
            headers=headers,
            params=params,
            content=content,
            timeout=timeout_v,
        )
        sig_headers = signing.sign_headers(
            self.wallet,
            method=method,
            path=req.url.path,
            query=req.url.query.decode(),
            body=body,
            node_host=self.host,
        )
        req.headers.update(sig_headers)
        r = self._client.send(req)
        if raise_for_status:
            r.raise_for_status()
        return r

    def get(
        self,
        path: str,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        timeout: int | float | None = None,
        raise_for_status: bool = True,  # noqa: FBT001
    ) -> httpx.Response:
        return self._send(
            'GET',
            path,
            headers=headers,
            params=params,
            timeout=timeout,
            raise_for_status=raise_for_status,
        )

    def post(
        self,
        path: str,
        headers: dict[str, str] | None = None,
        data: str | bytes | None = None,
        timeout: int | float | None = None,
        raise_for_status: bool = True,  # noqa: FBT001
    ) -> httpx.Response:
        return self._send(
            'POST',
            path,
            headers=headers,
            content=data,
            timeout=timeout,
            raise_for_status=raise_for_status,
        )

    def get_transfer_transaction(
        self,
        public_key: str,
        amount: int,
        address: str,
        timeout: int | float | None = None,
        raise_for_status: bool = True,  # noqa: FBT001
    ) -> httpx.Response:
        return self.get(
            '/api/transaction/transfer',
            params={
                'public_key': public_key,
                'amount': str(amount),
                'address': address,
            },
            timeout=timeout,
            raise_for_status=raise_for_status,
        )

    def get_opposition_transaction(
        self,
        public_key: str,
        amount: int,
        subject: str,
        timeout: int | float | None = None,
        raise_for_status: bool = True,  # noqa: FBT001
    ) -> httpx.Response:
        return self.get(
            '/api/transaction/opposition',
            params={
                'public_key': public_key,
                'amount': str(amount),
                'subject': subject,
            },
            timeout=timeout,
            raise_for_status=raise_for_status,
        )

    def get_rescind_transaction(
        self,
        public_key: str,
        amount: int,
        subject: str,
        kind: Literal['opposition', 'support'],
        timeout: int | float | None = None,
        raise_for_status: bool = True,  # noqa: FBT001
    ) -> httpx.Response:
        return self.get(
            '/api/transaction/rescind',
            params={
                'public_key': public_key,
                'amount': str(amount),
                'subject': subject,
                'kind': kind,
            },
            timeout=timeout,
            raise_for_status=raise_for_status,
        )

    def get_support_transaction(
        self,
        public_key: str,
        amount: int,
        subject: str,
        timeout: int | float | None = None,
        raise_for_status: bool = True,  # noqa: FBT001
    ) -> httpx.Response:
        return self.get(
            '/api/transaction/support',
            params={
                'public_key': public_key,
                'amount': str(amount),
                'subject': subject,
            },
            timeout=timeout,
            raise_for_status=raise_for_status,
        )

    def post_transaction(
        self,
        txn: Transaction,
        visited_hosts: list[str] | None = None,
        timeout: int | float | None = None,
        raise_for_status: bool = True,  # noqa: FBT001
    ) -> httpx.Response:
        headers = peer_header(visited_hosts, headers=json_header())
        return self.post(
            f'/api/transaction/{txn.txid}',
            data=txn.to_json(),
            headers=headers,
            timeout=timeout,
            raise_for_status=raise_for_status,
        )

    def get_pending_transactions(
        self,
        earliest: datetime.datetime | None = None,
        timeout: int | float | None = None,
        raise_for_status: bool = True,  # noqa: FBT001
    ) -> httpx.Response:
        params: dict[str, str] | None = None
        if earliest is not None:
            params = {'earliest': dt_2_ciso(earliest)}
        return self.get(
            '/api/transaction/pending',
            params=params,
            timeout=timeout,
            raise_for_status=raise_for_status,
        )

    def get_block(
        self,
        block_hash: str | None = None,
        timeout: int | float | None = None,
        raise_for_status: bool = True,  # noqa: FBT001
    ) -> httpx.Response:
        return self.get(
            f'/api/block/{block_hash}' if block_hash else '/api/block',
            timeout=timeout,
            raise_for_status=raise_for_status,
        )

    def post_block(
        self,
        block: Block,
        visited_hosts: list[str] | None = None,
        timeout: int | float | None = None,
        raise_for_status: bool = True,  # noqa: FBT001
    ) -> httpx.Response:
        headers = peer_header(visited_hosts, headers=json_header())
        return self.post(
            f'/api/block/{block.block_hash}',
            data=block.to_json(),
            headers=headers,
            timeout=timeout,
            raise_for_status=raise_for_status,
        )

    def get_wallet_balance(
        self,
        address: str,
        timeout: int | float | None = None,
        raise_for_status: bool = True,  # noqa: FBT001
    ) -> httpx.Response:
        return self.get(
            f'/api/wallet/{address}/balance',
            timeout=timeout,
            raise_for_status=raise_for_status,
        )

    def get_opposition_balance(
        self,
        subject: str,
        timeout: int | float | None = None,
        raise_for_status: bool = True,  # noqa: FBT001
    ) -> httpx.Response:
        return self.get(
            f'/api/subject/{subject}/opposition',
            timeout=timeout,
            raise_for_status=raise_for_status,
        )

    def get_support_balance(
        self,
        subject: str,
        timeout: int | float | None = None,
        raise_for_status: bool = True,  # noqa: FBT001
    ) -> httpx.Response:
        return self.get(
            f'/api/subject/{subject}/support',
            timeout=timeout,
            raise_for_status=raise_for_status,
        )
