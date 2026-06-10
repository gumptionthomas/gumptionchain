import json
import os
from pathlib import Path

from test_browser_wallet_vectors import VECTOR_WALLET_B58

from gumptionchain.attestation import (
    build_binding_message,
    build_stake_message,
    sign_social_binding,
    sign_stake_attestation,
    verify_binding,
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


# ---------------------------------------------------------------------------
# Social-binding-envelope signature vectors (#251)
# ---------------------------------------------------------------------------

_BINDING_VECTORS_PATH = (
    Path(__file__).resolve().parent.parent
    / 'clients'
    / 'wallet'
    / 'testdata'
    / 'gc-binding-vectors.json'
)

_BINDING_CASES = [
    {
        'claim': {
            'platform': 'github',
            'handle': 'gumptionthomas',
        },
        'timestamp': '1700003000',
    },
    {
        'claim': {
            'platform': 'github',
            'handle': 'gumptionthomas',
            'proof_url': 'https://gist.github.com/gumptionthomas/abc123',
        },
        'timestamp': '1700003001',
    },
    {
        'claim': {
            'platform': 'mastodon',
            'handle': '@tøm@example.social',
        },
        'timestamp': '1700003002',
    },
]


def _expected_binding() -> list[dict]:
    w = Wallet(b58ks=VECTOR_WALLET_B58)
    out = []
    for c in _BINDING_CASES:
        proof = sign_social_binding(
            w, c['claim'], timestamp=int(c['timestamp'])
        )
        out.append(
            {
                **c,
                'message': build_binding_message(c['claim']),
                'signature': proof['signature'],
                'address': proof['address'],
            }
        )
    return out


def test_binding_vectors_match() -> None:
    expected = _expected_binding()
    if os.environ.get('GC_REGEN_VECTORS'):
        _BINDING_VECTORS_PATH.write_text(json.dumps(expected, indent=2) + '\n')
    assert json.loads(_BINDING_VECTORS_PATH.read_text()) == expected


def test_binding_vectors_verify() -> None:
    w = Wallet(b58ks=VECTOR_WALLET_B58)
    vectors = json.loads(_BINDING_VECTORS_PATH.read_text())
    for v in vectors:
        claim = v['claim']
        assert v['message'] == build_binding_message(claim)
        # Reconstruct a full gc-msg-v1 proof from stored fields + the known
        # public key (vectors store only the claim-side fields; the public key
        # is derivable from the known vector wallet).
        proof = {
            'scheme': 'gc-msg-v1',
            'version': '1',
            'address': v['address'],
            'public_key': w.public_key_b64,
            'timestamp': v['timestamp'],
            'message': v['message'],
            'signature': v['signature'],
        }
        result = verify_binding(proof, max_age=None)
        assert result['valid'] is True, (
            f'binding vector failed verification: {v}'
        )
