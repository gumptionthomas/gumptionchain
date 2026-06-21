import json
import os
from pathlib import Path

from test_browser_signing_key_vectors import VECTOR_SECRET

from gumptionchain.message import sign_message
from gumptionchain.signing_key import SigningKey

VECTORS_PATH = (
    Path(__file__).resolve().parent.parent
    / 'clients'
    / 'sdk'
    / 'testdata'
    / 'gc-msg-vectors.json'
)
_CASES = [
    {'message': 'hello world', 'timestamp': '1700001000'},
    {
        'message': 'I made stake T1 — 3 GRIT opposition on goblins',
        'timestamp': '1700001001',
    },
    {'message': 'multi\nline\nmessage', 'timestamp': '1700001002'},
]


def _expected() -> list[dict]:
    w = SigningKey(secret=VECTOR_SECRET)
    out = []
    for c in _CASES:
        proof = sign_message(w, c['message'], timestamp=int(c['timestamp']))
        out.append(
            {
                **c,
                'signature': proof['signature'],
                'address': proof['address'],
            }
        )
    return out


def test_message_vectors_match() -> None:
    expected = _expected()
    if os.environ.get('GC_REGEN_VECTORS'):
        VECTORS_PATH.write_text(json.dumps(expected, indent=2) + '\n')
    stored = json.loads(VECTORS_PATH.read_text())
    assert stored == expected
