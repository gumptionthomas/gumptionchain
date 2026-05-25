from __future__ import annotations

import datetime
import json
from urllib.parse import urljoin

import requests

from cancelchain.block import Block
from cancelchain.transaction import Transaction
from cancelchain.util import dt_2_ciso, host_address
from cancelchain.wallet import Wallet

OK = requests.codes.ok
UNAUTHORIZED = requests.codes.unauthorized
PEER_HOST_HEADER = 'Peer-Hosts'
ADDRESS_MISMATCH_MSG = 'Address/wallet mismatch'


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
        self.token: str | None = None
        self.timeout: int | float = timeout if timeout is not None else 10

    def request_token(self, rfs: bool = True) -> str | None:  # noqa: FBT001
        r = requests.get(
            urljoin(self.host, f'/api/token/{self.wallet.address}'),
            timeout=self.timeout,
        )
        if rfs:
            r.raise_for_status()
        if r.status_code == OK:
            secret = self.wallet.decrypt(r.json().get('cipher')).decode()
            r = requests.post(
                urljoin(self.host, f'/api/token/{self.wallet.address}'),
                headers=json_header(),
                data=json.dumps({'challenge': secret}),
                timeout=self.timeout,
            )
            if rfs:
                r.raise_for_status()
            if r.status_code == OK:
                token: str | None = r.json().get('token')
                return token
        return None

    def get_token(self, rfs: bool = True) -> str | None:  # noqa: FBT001
        if self.token is None:
            self.token = self.request_token(rfs=rfs)
        return self.token

    def reset_token(self) -> None:
        self.token = None

    def auth_header(
        self,
        headers: dict[str, str] | None = None,
        rfs: bool = True,  # noqa: FBT001
    ) -> dict[str, str]:
        headers = headers or {}
        token = self.get_token(rfs=rfs)
        if token:
            headers['Authorization'] = f'Bearer {token}'
        return headers

    def get(
        self,
        path: str,
        headers: dict[str, str] | None = None,
        params: dict[str, str] | None = None,
        timeout: int | float | None = None,
        raise_for_status: bool = True,  # noqa: FBT001
    ) -> requests.Response:
        timeout = self.timeout if timeout is None else timeout
        r: requests.Response
        for _i in range(2):
            headers = self.auth_header(headers=headers, rfs=raise_for_status)
            r = requests.get(
                urljoin(self.host, path),
                headers=headers,
                params=params,
                timeout=timeout,
            )
            if r.status_code == UNAUTHORIZED:
                self.reset_token()
            else:
                break
        if raise_for_status:
            r.raise_for_status()
        return r

    def post(
        self,
        path: str,
        headers: dict[str, str] | None = None,
        data: str | bytes | None = None,
        timeout: int | float | None = None,
        raise_for_status: bool = True,  # noqa: FBT001
    ) -> requests.Response:
        timeout = self.timeout if timeout is None else timeout
        r: requests.Response
        for _i in range(2):
            headers = self.auth_header(headers=headers, rfs=raise_for_status)
            r = requests.post(
                urljoin(self.host, path),
                headers=headers,
                data=data,
                timeout=timeout,
            )
            if r.status_code == UNAUTHORIZED:
                self.reset_token()
            else:
                break
        if raise_for_status:
            r.raise_for_status()
        return r

    def get_transfer_transaction(
        self,
        public_key: str,
        amount: int,
        address: str,
        timeout: int | float | None = None,
        raise_for_status: bool = True,  # noqa: FBT001
    ) -> requests.Response:
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

    def get_subject_transaction(
        self,
        public_key: str,
        amount: int,
        subject: str,
        timeout: int | float | None = None,
        raise_for_status: bool = True,  # noqa: FBT001
    ) -> requests.Response:
        return self.get(
            '/api/transaction/subject',
            params={
                'public_key': public_key,
                'amount': str(amount),
                'subject': subject,
            },
            timeout=timeout,
            raise_for_status=raise_for_status,
        )

    def get_forgive_transaction(
        self,
        public_key: str,
        amount: int,
        subject: str,
        timeout: int | float | None = None,
        raise_for_status: bool = True,  # noqa: FBT001
    ) -> requests.Response:
        return self.get(
            '/api/transaction/forgive',
            params={
                'public_key': public_key,
                'amount': str(amount),
                'subject': subject,
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
    ) -> requests.Response:
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
    ) -> requests.Response:
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
    ) -> requests.Response:
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
    ) -> requests.Response:
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
    ) -> requests.Response:
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
    ) -> requests.Response:
        return self.get(
            f'/api/wallet/{address}/balance',
            timeout=timeout,
            raise_for_status=raise_for_status,
        )

    def get_subject_balance(
        self,
        subject: str,
        timeout: int | float | None = None,
        raise_for_status: bool = True,  # noqa: FBT001
    ) -> requests.Response:
        return self.get(
            f'/api/subject/{subject}/balance',
            timeout=timeout,
            raise_for_status=raise_for_status,
        )

    def get_subject_support(
        self,
        subject: str,
        timeout: int | float | None = None,
        raise_for_status: bool = True,  # noqa: FBT001
    ) -> requests.Response:
        return self.get(
            f'/api/subject/{subject}/support',
            timeout=timeout,
            raise_for_status=raise_for_status,
        )
