import json
import shutil
import subprocess
from pathlib import Path

import pytest
from test_browser_wallet_vectors import VECTOR_WALLET_B58

from gumptionchain.attestation import (
    build_stake_message,
    sign_stake_attestation,
    verify_stake,
)
from gumptionchain.wallet import Wallet

CLI = (
    Path(__file__).resolve().parent.parent
    / 'clients'
    / 'wallet'
    / 'attestation-cli.mjs'
)
TS = '1700002000'
CLAIM = {
    'txid': '1' * 64,
    'kind': 'opposition',
    'subject': 'göblins',
    'amount': 300,
}


def _node(mode: str, payload: dict) -> str:
    out = subprocess.run(  # noqa: S603
        ['node', str(CLI), mode, json.dumps(payload)],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )
    return out.stdout


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_canonical_message_is_byte_identical() -> None:
    # Non-ASCII subject exercises ensure_ascii=False parity.
    assert _node('build', CLAIM) == build_stake_message(CLAIM)


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_js_signed_attestation_verifies_in_python() -> None:
    proof = json.loads(
        _node(
            'sign',
            {
                'private_key_b58': VECTOR_WALLET_B58,
                'claim': CLAIM,
                'timestamp': TS,
            },
        )
    )
    w = Wallet(b58ks=VECTOR_WALLET_B58)
    prov = {
        'txid': '1' * 64,
        'address': w.address,
        'status': 'canonical',
        'confirmations': 5,
        'outflows': [
            {'kind': 'opposition', 'subject': 'göblins', 'amount': 300}
        ],
    }
    assert verify_stake(proof, lambda _t: prov)['valid'] is True


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_python_signed_attestation_verifies_in_js() -> None:
    w = Wallet(b58ks=VECTOR_WALLET_B58)
    proof = sign_stake_attestation(w, CLAIM, timestamp=int(TS))
    prov = {
        'txid': '1' * 64,
        'address': w.address,
        'status': 'canonical',
        'confirmations': 5,
        'outflows': [
            {'kind': 'opposition', 'subject': 'göblins', 'amount': 300}
        ],
    }
    verdict = json.loads(_node('verify', {'proof': proof, 'provenance': prov}))
    assert verdict['valid'] is True
