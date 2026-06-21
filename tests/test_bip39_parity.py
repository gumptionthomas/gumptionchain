import json
from pathlib import Path

from gumptionchain.bip39 import mnemonic_to_seed, seed_to_mnemonic

VECTORS = json.loads(
    (Path(__file__).parent / 'fixtures' / 'bip39_vectors.json').read_text()
)


def test_python_reproduces_committed_bip39_vectors():
    for v in VECTORS:
        seed = bytes.fromhex(v['seed_hex'])
        assert seed_to_mnemonic(seed) == v['mnemonic']
        assert mnemonic_to_seed(v['mnemonic']) == seed
