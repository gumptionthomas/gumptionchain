"""Emit Python-generated transaction signing parity vectors.

These vectors lock the JS `gc-transaction.mjs` reconstruction of a
transaction's canonical `data_csv`, `txid`, and `signing_data` to the
Python implementation byte-for-byte. The JS parity test
(`clients/wallet/gc-transaction.test.mjs`) loads the JSON this writes.

Run: `uv run python tests/fixtures/gen_txn_fixtures.py`

Each transaction is built from FIXED, deterministic inputs (literal
`Inflow`/`Outflow` objects — NOT via a chain) so the output is stable
across runs and machines. The signing wallet is the canonical conftest
test wallet, whose b58 private key is embedded in every vector so the JS
test can import the same key and sign.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path
from typing import Any

# Make the conftest test wallet importable when run as a plain script
# (`tests/` is not an installed package). The b58 private key there is the
# single canonical test wallet the JS parity test also imports.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from conftest import WALLET_PRIVATE_KEY_B58

from gumptionchain.payload import Inflow, Outflow, encode_subject
from gumptionchain.transaction import Transaction
from gumptionchain.wallet import Wallet

# Fixed inputs: a stable timestamp and two realistic 64-hex inflow txids.
TIMESTAMP = '1700000000'
INFLOW_TXID = 'a' * 64
DEST_ADDRESS = 'GCpEsT1nat10naddre55fortransfertestvectoronlyGC'
# Subjects are stored base64url-encoded (as the chain stores them).
OPPOSITION_SUBJECT = encode_subject('goblins')
SUPPORT_SUBJECT = encode_subject('paladins')
RESCIND_SUBJECT = encode_subject('goblins')

OUTPUT = Path(__file__).resolve().parent / 'txn_signing_vectors.json'


def _vec(
    name: str, inflows: list[Inflow], outflows: list[Outflow]
) -> dict[str, Any]:
    w = Wallet(b58ks=WALLET_PRIVATE_KEY_B58)
    t = Transaction(
        timestamp=TIMESTAMP,
        inflows=inflows,
        outflows=outflows,
    )
    t.set_wallet(w)
    t.seal()
    return {
        'name': name,
        'wallet_b58': WALLET_PRIVATE_KEY_B58,
        'txn': t.to_dict(),
        'data_csv': t.data_csv,
        'txid': t.txid,
        'signing_data_b64': base64.b64encode(t.signing_data).decode(),
    }


def build_vectors() -> list[dict[str, Any]]:
    inflow = Inflow(outflow_txid=INFLOW_TXID, outflow_idx=0)
    return [
        _vec(
            'transfer',
            [inflow],
            [
                Outflow(amount=700, address=DEST_ADDRESS),
                Outflow(amount=300, address='__CHANGE__'),
            ],
        ),
        _vec(
            'opposition',
            [inflow],
            [
                Outflow(amount=500, opposition=OPPOSITION_SUBJECT),
                Outflow(amount=500, address='__CHANGE__'),
            ],
        ),
        _vec(
            'support',
            [inflow],
            [
                Outflow(amount=400, support=SUPPORT_SUBJECT),
                Outflow(amount=600, address='__CHANGE__'),
            ],
        ),
        _vec(
            'rescind',
            [inflow],
            [
                Outflow(
                    amount=250,
                    rescind=RESCIND_SUBJECT,
                    rescind_kind='opposition',
                ),
                Outflow(
                    amount=750,
                    opposition=RESCIND_SUBJECT,
                ),
            ],
        ),
    ]


def main() -> None:
    vectors = build_vectors()
    OUTPUT.write_text(json.dumps(vectors, indent=2) + '\n')
    print(f'wrote {len(vectors)} vectors to {OUTPUT}')  # noqa: T201


if __name__ == '__main__':
    main()
