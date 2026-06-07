import json
import os
from pathlib import Path

from test_browser_wallet_vectors import VECTOR_WALLET_B58

from gumptionchain.attestation import (
    build_stake_message,
    sign_stake_attestation,
)
from gumptionchain.wallet import Wallet

VECTORS_PATH = (
    Path(__file__).resolve().parent.parent
    / 'clients'
    / 'wallet'
    / 'testdata'
    / 'gc-attestation-vectors.json'
)
# txids are canonical 64-char lowercase-hex mill hashes (see #187).
_CASES = [
    {
        'claim': {
            'txid': '1' * 64,
            'kind': 'opposition',
            'subject': 'goblins',
            'amount': 300,
        },
        'timestamp': '1700002000',
    },
    {
        'claim': {
            'txid': '2' * 64,
            'kind': 'support',
            'subject': 'göblins',
            'amount': 100,
            'handle': 'me.bsky.social',
        },
        'timestamp': '1700002001',
    },
    {
        'claim': {
            'txid': '3' * 64,
            'kind': 'transfer',
            'address': 'GCxGC',
            'amount': 5,
        },
        'timestamp': '1700002002',
    },
]


def _expected() -> list[dict]:
    w = Wallet(b58ks=VECTOR_WALLET_B58)
    out = []
    for c in _CASES:
        proof = sign_stake_attestation(
            w, c['claim'], timestamp=int(c['timestamp'])
        )
        out.append(
            {
                **c,
                'message': build_stake_message(c['claim']),
                'signature': proof['signature'],
                'address': proof['address'],
            }
        )
    return out


def test_attestation_vectors_match() -> None:
    expected = _expected()
    if os.environ.get('GC_REGEN_VECTORS'):
        VECTORS_PATH.write_text(json.dumps(expected, indent=2) + '\n')
    assert json.loads(VECTORS_PATH.read_text()) == expected
