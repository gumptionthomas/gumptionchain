import json
import os
from pathlib import Path

from gumptionchain.signing import _canonical
from gumptionchain.signing_key import SigningKey

# A fixed Ed25519 key used solely to generate the golden JS<->Python parity
# vectors. Deterministic across runs (fixed seed). gcsec1… secret string.
VECTOR_SECRET = SigningKey.from_ed25519_seed(bytes(range(1, 33))).secret
VECTORS_PATH = (
    Path(__file__).resolve().parent.parent
    / 'clients'
    / 'sdk'
    / 'testdata'
    / 'gc-sig-vectors.json'
)

_CASES = [
    {
        'method': 'GET',
        'path': '/api/blocks',
        'query': '',
        'body': '',
        'node_host': 'node.example',
        'timestamp': '1700000000',
    },
    {
        'method': 'POST',
        'path': '/api/transactions',
        'query': 'foo=bar',
        'body': '{"hello":"world"}',
        'node_host': 'node.example',
        'timestamp': '1700000001',
    },
]


def _build_vectors() -> dict[str, object]:
    w = SigningKey(secret=VECTOR_SECRET)
    cases = []
    for c in _CASES:
        canonical = _canonical(
            method=c['method'],
            path=c['path'],
            query=c['query'],
            body=c['body'].encode(),
            node_host=c['node_host'],
            timestamp=c['timestamp'],
            address=w.address,
        )
        cases.append(
            {
                **c,
                'canonical': canonical.decode(),
                'signature': w.sign(canonical),
            }
        )
    return {
        'secret': VECTOR_SECRET,
        'public_key_b64': w.public_key_b64,
        'address': w.address,
        'cases': cases,
    }


def test_vectors_committed_and_self_consistent() -> None:
    fresh = _build_vectors()
    # Default run is READ-ONLY: fail loud if the committed oracle is missing,
    # rather than silently regenerating (which would let an accidental deletion
    # pass CI). Regenerate intentionally with GC_REGEN_VECTORS=1.
    if os.environ.get('GC_REGEN_VECTORS'):
        VECTORS_PATH.parent.mkdir(parents=True, exist_ok=True)
        VECTORS_PATH.write_text(json.dumps(fresh, indent=2) + '\n')
    assert VECTORS_PATH.exists(), (
        'gc-sig-vectors.json missing; regenerate with GC_REGEN_VECTORS=1'
    )
    committed = json.loads(VECTORS_PATH.read_text())
    assert committed == fresh, 'gc-sig-vectors.json drifted; regenerate'

    w = SigningKey(secret=committed['secret'])
    for case in committed['cases']:
        assert w.validate_signature(
            case['canonical'].encode(), case['signature']
        )
