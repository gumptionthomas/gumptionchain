"""Python side of the JS<->Python transaction signing parity contract.

`clients/wallet/gc-transaction.test.mjs` asserts the JS reconstruction of
data_csv / txid / signing_data matches these same vectors. Here we prove the
fixtures round-trip through the Python domain model — so the vectors the JS
test trusts are genuinely the canonical Python output — and that a Python
signature over the fixture's signing_data verifies. Together the two sides
establish: JS builds the same bytes Python signs, and Python verifies them.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest
from conftest import WALLET_PRIVATE_KEY_B58

from gumptionchain.payload import Inflow, Outflow
from gumptionchain.transaction import Transaction
from gumptionchain.wallet import Wallet


def _txn_from_fixture_dict(d: dict) -> Transaction:
    # Rebuild the dataclass directly from the fixture's `to_dict()` output
    # (the same JSON the JS test consumes), reconstructing the nested
    # in/outflows. We deliberately bypass Transaction.from_dict's full
    # TransactionModel validation: the vectors use a fixed epoch-string
    # timestamp and synthetic change addresses chosen for deterministic
    # canonical bytes, not on-chain validity. This test locks the
    # *serialization* (data_csv/txid/signing_data), which is the parity
    # contract the JS side mirrors.
    return Transaction(
        timestamp=d['timestamp'],
        txid=d.get('txid'),
        address=d.get('address'),
        public_key=d.get('public_key'),
        signature=d.get('signature'),
        inflows=[Inflow(**i) for i in d.get('inflows', [])],
        outflows=[Outflow(**o) for o in d.get('outflows', [])],
        version=d['version'],
        prev_hash=d.get('prev_hash'),
    )


VECTORS_PATH = (
    Path(__file__).resolve().parent / 'fixtures' / 'txn_signing_vectors.json'
)
VECTORS = json.loads(VECTORS_PATH.read_text())


@pytest.fixture(scope='module')
def vectors() -> list[dict]:
    assert VECTORS, 'expected at least one parity vector'
    return VECTORS


def test_every_shape_present(vectors: list[dict]) -> None:
    names = {v['name'] for v in vectors}
    assert {'transfer', 'opposition', 'support', 'rescind'} <= names


@pytest.mark.parametrize('vector', VECTORS, ids=lambda v: v['name'])
def test_fixture_round_trips_through_python(vector: dict) -> None:
    # Reconstruct the (sealed, unsigned) txn from its JSON dict and confirm
    # the recomputed canonical form matches the fixture — proving the vector
    # the JS test asserts against is the real Python serialization.
    txn = _txn_from_fixture_dict(vector['txn'])
    assert txn.data_csv == vector['data_csv']
    assert txn.txid == vector['txid']
    assert txn.calculate_txid() == vector['txid']
    expected_signing = base64.b64decode(vector['signing_data_b64'])
    assert txn.signing_data == expected_signing


@pytest.mark.parametrize('vector', VECTORS, ids=lambda v: v['name'])
def test_python_sign_over_fixture_verifies(vector: dict) -> None:
    # Sign the fixture's signing_data with the same wallet the JS test
    # imports, and verify it. (The JS-sign -> Python-verify direction is
    # exercised end-to-end at the integration layer in a later PR; here we
    # lock the canonical bytes + the verify path.)
    wallet = Wallet(b58ks=vector['wallet_b58'])
    assert vector['wallet_b58'] == WALLET_PRIVATE_KEY_B58
    signing_data = base64.b64decode(vector['signing_data_b64'])
    signature = wallet.sign(signing_data)
    assert wallet.validate_signature(signing_data, signature)
