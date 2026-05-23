import json
from urllib.parse import urljoin

import requests

from cancelchain.util import dt_2_ciso, host_address

OK = requests.codes.ok
UNAUTHORIZED = requests.codes.unauthorized
PEER_HOST_HEADER = 'Peer-Hosts'
ADDRESS_MISMATCH_MSG = 'Address/wallet mismatch'


def json_header(headers=None):
    headers = headers or {}
    headers['Content-Type'] = 'application/json'
    return headers


def peer_header(visited_hosts, headers=None):
    headers = headers or {}
    if visited_hosts:
        headers[PEER_HOST_HEADER] = ','.join(visited_hosts)
    return headers


class ApiClient:
    def __init__(self, host, wallet, timeout=None):
        host, address = host_address(host)
        if address and address != wallet.address:
            raise Exception(ADDRESS_MISMATCH_MSG)
        self.host = host
        self.wallet = wallet
        self.token = None
        self.timeout = timeout if timeout is not None else 10

    def request_token(self, rfs=True):
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
                return r.json().get('token')
        return None

    def get_token(self, rfs=True):
        if self.token is None:
            self.token = self.request_token(rfs=rfs)
        return self.token

    def reset_token(self):
        self.token = None

    def auth_header(self, headers=None, rfs=True):
        headers = headers or {}
        token = self.get_token(rfs=rfs)
        if token:
            headers['Authorization'] = f'Bearer {token}'
        return headers

    def get(
        self,
        path,
        headers=None,
        params=None,
        timeout=None,
        raise_for_status=True,
    ):
        timeout = self.timeout if timeout is None else timeout
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
        self, path, headers=None, data=None, timeout=None, raise_for_status=True
    ):
        timeout = self.timeout if timeout is None else timeout
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
        self, public_key, amount, address, timeout=None, raise_for_status=True
    ):
        return self.get(
            '/api/transaction/transfer',
            params={
                'public_key': public_key,
                'amount': amount,
                'address': address,
            },
            timeout=timeout,
            raise_for_status=raise_for_status,
        )

    def get_subject_transaction(
        self, public_key, amount, subject, timeout=None, raise_for_status=True
    ):
        return self.get(
            '/api/transaction/subject',
            params={
                'public_key': public_key,
                'amount': amount,
                'subject': subject,
            },
            timeout=timeout,
            raise_for_status=raise_for_status,
        )

    def get_forgive_transaction(
        self, public_key, amount, subject, timeout=None, raise_for_status=True
    ):
        return self.get(
            '/api/transaction/forgive',
            params={
                'public_key': public_key,
                'amount': amount,
                'subject': subject,
            },
            timeout=timeout,
            raise_for_status=raise_for_status,
        )

    def get_support_transaction(
        self, public_key, amount, subject, timeout=None, raise_for_status=True
    ):
        return self.get(
            '/api/transaction/support',
            params={
                'public_key': public_key,
                'amount': amount,
                'subject': subject,
            },
            timeout=timeout,
            raise_for_status=raise_for_status,
        )

    def post_transaction(
        self, txn, visited_hosts=None, timeout=None, raise_for_status=True
    ):
        headers = peer_header(visited_hosts, headers=json_header())
        return self.post(
            f'/api/transaction/{txn.txid}',
            data=txn.to_json(),
            headers=headers,
            timeout=timeout,
            raise_for_status=raise_for_status,
        )

    def get_pending_transactions(
        self, earliest=None, timeout=None, raise_for_status=True
    ):
        params = None
        if earliest is not None:
            params = {'earliest': dt_2_ciso(earliest)}
        return self.get(
            '/api/transaction/pending',
            params=params,
            timeout=timeout,
            raise_for_status=raise_for_status,
        )

    def get_block(self, block_hash=None, timeout=None, raise_for_status=True):
        return self.get(
            f'/api/block/{block_hash}' if block_hash else '/api/block',
            timeout=timeout,
            raise_for_status=raise_for_status,
        )

    def post_block(
        self, block, visited_hosts=None, timeout=None, raise_for_status=True
    ):
        headers = peer_header(visited_hosts, headers=json_header())
        return self.post(
            f'/api/block/{block.block_hash}',
            data=block.to_json(),
            headers=headers,
            timeout=timeout,
            raise_for_status=raise_for_status,
        )

    def get_wallet_balance(self, address, timeout=None, raise_for_status=True):
        return self.get(
            f'/api/wallet/{address}/balance',
            timeout=timeout,
            raise_for_status=raise_for_status,
        )

    def get_subject_balance(self, subject, timeout=None, raise_for_status=True):
        return self.get(
            f'/api/subject/{subject}/balance',
            timeout=timeout,
            raise_for_status=raise_for_status,
        )

    def get_subject_support(self, subject, timeout=None, raise_for_status=True):
        return self.get(
            f'/api/subject/{subject}/support',
            timeout=timeout,
            raise_for_status=raise_for_status,
        )
