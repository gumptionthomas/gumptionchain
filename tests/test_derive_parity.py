import json
from pathlib import Path

from gumptionchain.derive import derive_seed

VECTORS = json.loads(
    (Path(__file__).parent / 'fixtures' / 'derive_vectors.json').read_text()
)


def test_python_reproduces_committed_derive_vectors():
    for v in VECTORS:
        seed = derive_seed(bytes.fromhex(v['prf_hex']), v['passphrase'])
        assert seed.hex() == v['seed_hex']
