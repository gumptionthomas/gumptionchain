import json
import shutil
import subprocess
from pathlib import Path

import pytest
from test_browser_wallet_vectors import VECTOR_WALLET_B58

from gumptionchain.attestation import (
    BadAttestationError,
    build_binding_message,
    build_stake_message,
    parse_social_binding,
    sign_social_binding,
    sign_stake_attestation,
    verify_binding,
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


# ---------------------------------------------------------------------------
# Social-binding-envelope parity tests (#251)
# ---------------------------------------------------------------------------

_BINDING_TS = '1700004000'
_BINDING_CLAIM_MINIMAL = {
    'platform': 'mastodon',
    'handle': '@tøm@example.social',
}
_BINDING_CLAIM_WITH_URL = {
    'platform': 'github',
    'handle': 'gumptionthomas',
    'proof_url': 'https://gist.github.com/gumptionthomas/abc123',
}


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_binding_canonical_parity() -> None:
    for claim in (_BINDING_CLAIM_MINIMAL, _BINDING_CLAIM_WITH_URL):
        assert _node('build-binding', claim) == build_binding_message(claim)


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_js_signed_binding_verifies_in_python() -> None:
    proof = json.loads(
        _node(
            'sign-binding',
            {
                'private_key_b58': VECTOR_WALLET_B58,
                'claim': _BINDING_CLAIM_WITH_URL,
                'timestamp': _BINDING_TS,
            },
        )
    )
    result = verify_binding(proof)
    assert result['valid'] is True
    assert result['checks']['signature'] is True


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_python_signed_binding_verifies_in_js() -> None:
    w = Wallet(b58ks=VECTOR_WALLET_B58)
    proof = sign_social_binding(
        w, _BINDING_CLAIM_WITH_URL, timestamp=int(_BINDING_TS)
    )
    verdict = json.loads(_node('verify-binding', {'proof': proof}))
    assert verdict['valid'] is True


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_binding_reject_parity() -> None:
    # Build a non-canonical proof: reordered keys (handle before platform).
    w = Wallet(b58ks=VECTOR_WALLET_B58)
    good_proof = sign_social_binding(
        w, _BINDING_CLAIM_MINIMAL, timestamp=int(_BINDING_TS)
    )
    # Tamper: swap key order so the signed message no longer matches
    # build_binding_message (which always emits platform-first).
    bad_message = json.dumps(
        {
            'handle': _BINDING_CLAIM_MINIMAL['handle'],
            'platform': _BINDING_CLAIM_MINIMAL['platform'],
        },
        separators=(',', ':'),
        ensure_ascii=False,
    )
    bad_proof = {**good_proof, 'message': bad_message}

    # Python rejects it.
    with pytest.raises(BadAttestationError):
        parse_social_binding(bad_proof)

    # JS rejects it (exits non-zero).
    with pytest.raises(subprocess.CalledProcessError):
        _node('verify-binding', {'proof': bad_proof})
