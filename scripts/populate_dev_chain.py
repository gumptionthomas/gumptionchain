"""Populate the local dev chain via the real Miller mempool flow (in-process,
no running server): mine coinbase blocks to fund the wallet, then submit +
confirm a confirmed opposition and support stake on a subject. Prints the
txids and a ready-to-paste gc-msg-v1 stake attestation for /verify.

Uses receive_transaction -> create_block -> mill_block (which calls
receive_block), so blocks extend the canonical chain tip — NOT the raw
chain.add_block test helper, which forks off a stale tip.

Run from the gumptionchain repo root:  uv run python /tmp/populate_dev_chain.py
"""

from __future__ import annotations

import json

from dotenv import load_dotenv

# Standalone python won't auto-load .env like the gumptionchain CLI does.
load_dotenv('.env')  # path relative to CWD (the repo root)

from gumptionchain import create_app  # noqa: E402
from gumptionchain.attestation import sign_stake_attestation  # noqa: E402
from gumptionchain.message import to_armored  # noqa: E402
from gumptionchain.miller import Miller  # noqa: E402
from gumptionchain.payload import encode_subject  # noqa: E402

SUBJECT = 'goblins'
COINBASE_BLOCKS = 3


def mine_one(miller: Miller) -> None:
    block = miller.create_block()
    miller.mill_block(block)


def stake_and_confirm(miller, make_txn, wallet) -> str:
    """Create a stake txn via make_txn(chain), submit it to the mempool, mine a
    block to confirm it, and return its txid."""
    txn = make_txn(miller.longest_chain)
    txn.sign()
    miller.receive_transaction(txn.txid, txn.to_json(), process=False)
    mine_one(miller)
    return txn.txid


def main() -> None:
    app = create_app()
    with app.app_context():
        wallet = next(iter(app.wallets.values()))
        miller = Miller(
            host=app.config['NODE_HOST'],
            logger=app.logger,
            milling_wallet=wallet,
        )

        # fund the wallet with coinbase rewards
        for _ in range(COINBASE_BLOCKS):
            mine_one(miller)

        enc = encode_subject(SUBJECT)
        op_txid = stake_and_confirm(
            miller, lambda c: c.create_opposition(wallet, 300, enc), wallet
        )
        sp_txid = stake_and_confirm(
            miller, lambda c: c.create_support(wallet, 150, enc), wallet
        )

        # a pasteable proof for /verify (over the opposition stake)
        claim = {
            'txid': op_txid,
            'kind': 'opposition',
            'subject': enc,
            'amount': 300,
        }
        proof = sign_stake_attestation(wallet, claim)

        tip = miller.longest_chain.last_block
        print('\n=== populated (canonical) ===')  # noqa: T201
        print(f'tip block idx  : {tip.idx}  hash {tip.block_hash}')  # noqa: T201
        print(f'staker address : {wallet.address}')  # noqa: T201
        print(f'subject        : {SUBJECT!r} (encoded {enc})')  # noqa: T201
        print(f'opposition txid: {op_txid}  (300 grains)')  # noqa: T201
        print(f'support    txid: {sp_txid}  (150 grains)')  # noqa: T201
        print('\n=== paste this JSON into /verify ===')  # noqa: T201
        print(json.dumps(proof))  # noqa: T201
        print('\n=== or the armored form ===')  # noqa: T201
        print(to_armored(proof))  # noqa: T201


if __name__ == '__main__':
    main()
