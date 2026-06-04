from __future__ import annotations

import json
from collections.abc import Generator
from logging import Logger
from typing import Any

import httpx

from gumptionchain.block import MAX_TRANSACTIONS, Block, expiry_cutoff
from gumptionchain.chain import Chain
from gumptionchain.milling import milling_generator
from gumptionchain.node import Node
from gumptionchain.signals import txn_failed as txn_failed_signal
from gumptionchain.transaction import CoinbaseMetrics, Transaction
from gumptionchain.util import host_address, now
from gumptionchain.wallet import Wallet


class Miller(Node):
    def __init__(
        self,
        host: str | None = None,
        peers: list[str] | None = None,
        clients: dict[str, Any] | None = None,
        logger: Logger | None = None,
        milling_wallet: Wallet | None = None,
        milling_peer: str | None = None,
    ) -> None:
        super().__init__(host=host, peers=peers, clients=clients, logger=logger)
        self.milling_client: Any | None = None
        self.milling_peer = milling_peer
        if self.milling_peer is not None:
            self.milling_client = self.clients.get(self.milling_peer)
        self.milling_wallet = milling_wallet
        self.pending_txns_generator: Generator[Any, None, None] | None = None

    def pending_txns_gen(self) -> Generator[Any, None, None]:
        last_call = None
        while True:
            call_dt = now()
            if self.milling_client is not None:
                visited_hosts = [self.milling_client.host]
                try:
                    r = self.milling_client.get_pending_transactions(
                        earliest=last_call
                    )
                    for txn_json in r.json():
                        if txid := txn_json.get('txid'):
                            self.receive_transaction(
                                txid,
                                json.dumps(txn_json),
                                visited_hosts=visited_hosts,
                            )
                except httpx.HTTPError as re:
                    self.logger.error(re)
                except Exception as e:
                    self.logger.exception(e)
            last_call = call_dt
            yield last_call

    def update_pending_txns(self) -> None:
        if self.pending_txns_generator is None:
            self.pending_txns_generator = self.pending_txns_gen()
        _ = next(self.pending_txns_generator)
        self.discard_expired_pending_txns()

    def pending_chain_txns(
        self, chain: Chain
    ) -> Generator[Transaction, None, None]:
        # Filter expired txns in SQL (indexed timestamp >= cutoff) so the
        # mill critical path only parses live rows; the already-mined
        # check stays in Python (needs a chain lookup per txn).
        cutoff = expiry_cutoff(now())
        for json_data in self.pending_txns.query_json(expired=cutoff):
            txn = Transaction.from_json(json_data)
            if not chain.get_transaction(txn.txid):  # type: ignore[arg-type]
                yield txn

    def create_block(self) -> Block:
        chain = self.longest_chain or self.create_chain()
        block = Block()
        chain.link_block(block)
        i = 0
        discard_txns: list[Transaction] = []
        metrics = CoinbaseMetrics()
        self.update_pending_txns()
        for txn in self.pending_chain_txns(chain):
            try:
                m = chain.validate_block_txn(block, txn, txn_in_block=False)
                block.add_txn(txn)
                metrics += m
                i += 1
                if i >= MAX_TRANSACTIONS - 1:
                    break
            except Exception as e:
                discard_txns.append(txn)
                txn_failed_signal.send(self, txn=txn, e=e)
        for txn in discard_txns:
            self.pending_txns.discard(txn)
        chain.seal_block(block, self.milling_wallet, metrics)  # type: ignore[arg-type]
        return block

    def poll_latest_blocks(self, progress: Any | None = None) -> None:
        latest_blocks = self.request_latest_blocks(peer=self.milling_peer)
        for latest_block, peer in latest_blocks:
            if Block.from_db(latest_block.block_hash) is None:  # type: ignore[arg-type]
                self.fill_chain(latest_block, progress=progress)
                host, _ = host_address(peer)
                self.send_block(latest_block, visited_hosts=[host])

    def mill_block(
        self,
        block: Block,
        mp: bool = False,  # noqa: FBT001
        rounds: int | None = None,
        worksize: int | None = None,
        progress: Any | None = None,
    ) -> Block | None:
        solved_block: Block | None = None
        chain = Chain.from_db(block_hash=block.prev_hash)
        for proof_of_work in milling_generator(
            block,  # type: ignore[arg-type]
            mp=mp,
            rounds=rounds,
            worksize=worksize,
            progress=progress,
        ):
            if self.milling_peer is not None:
                self.poll_latest_blocks()
            longest_chain = self.longest_chain
            if longest_chain is not None and (
                chain is None or chain < longest_chain
            ):
                break
            if proof_of_work is not None:
                solved_block = self.receive_block(block.to_json())
        return solved_block
