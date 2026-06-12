import json
import shutil
import subprocess
from pathlib import Path

import pytest
from test_browser_signing_key_vectors import VECTOR_SIGNING_KEY_B58

from gumptionchain.message import sign_message, verify_message
from gumptionchain.signing_key import SigningKey

CLI = (
    Path(__file__).resolve().parent.parent
    / 'clients'
    / 'signing-key'
    / 'message-cli.mjs'
)
TS = '1700001000'
MESSAGE = 'I made stake T1 — 3 GRIT opposition on goblins'


def _node(mode: str, payload: dict) -> dict:
    out = subprocess.run(  # noqa: S603
        ['node', str(CLI), mode, json.dumps(payload)],  # noqa: S607
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_js_signed_message_verifies_in_python() -> None:
    proof = _node(
        'sign',
        {
            'private_key_b58': VECTOR_SIGNING_KEY_B58,
            'message': MESSAGE,
            'timestamp': TS,
        },
    )
    assert verify_message(proof)['valid'] is True


@pytest.mark.skipif(shutil.which('node') is None, reason='node not installed')
def test_python_signed_message_verifies_in_js() -> None:
    w = SigningKey(b58ks=VECTOR_SIGNING_KEY_B58)
    proof = sign_message(w, MESSAGE, timestamp=int(TS))
    result = _node('verify', proof)
    assert result['valid'] is True
    assert result['address'] == w.address
