import json

import requests

from cancelchain.block import MAX_TRANSACTIONS, TXN_TIMEOUT, Block
from cancelchain.chain import Chain
from cancelchain.milling import milling_generator
from cancelchain.node import Node
from cancelchain.signals import txn_failed as txn_failed_signal
from cancelchain.util import host_address, now


class Miller(Node):
    def __init__(
        self,
        host=None,
        peers=None,
        clients=None,
        logger=None,
        milling_wallet=None,
        milling_peer=None,
    ):
        super().__init__(host=host, peers=peers, clients=clients, logger=logger)
        self.milling_client = None
        self.milling_peer = milling_peer
        if self.milling_peer is not None:
            self.milling_client = self.clients.get(self.milling_peer)
        self.milling_wallet = milling_wallet
        self.pending_txns_generator = None

    def pending_txns_gen(self):
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
                except requests.RequestException as re:
                    self.logger.error(re)
                except Exception as e:
                    self.logger.exception(e)
            last_call = call_dt
            yield last_call

    def update_pending_txns(self):
        if self.pending_txns_generator is None:
            self.pending_txns_generator = self.pending_txns_gen()
        _ = next(self.pending_txns_generator)
        self.discard_expired_pending_txns()

    def pending_chain_txns(self, chain):
        expired_dt = now() - TXN_TIMEOUT
        for txn in self.pending_txns:
            if txn.timestamp_dt > expired_dt and not chain.get_transaction(
                txn.txid
            ):
                yield txn

    def create_block(self):
        chain = self.longest_chain or self.create_chain()
        block = Block()
        chain.link_block(block)
        i = 0
        discard_txns = []
        self.update_pending_txns()
        for txn in self.pending_chain_txns(chain):
            try:
                chain.validate_block_txn(block, txn, txn_in_block=False)
                block.add_txn(txn)
                i += 1
                if i >= MAX_TRANSACTIONS - 1:
                    break
            except Exception as e:
                discard_txns.append(txn)
                txn_failed_signal.send(self, txn=txn, e=e)
        for txn in discard_txns:
            self.pending_txns.discard(txn)
        chain.seal_block(block, self.milling_wallet)
        return block

    def poll_latest_blocks(self, progress=None):
        latest_blocks = self.request_latest_blocks(peer=self.milling_peer)
        for latest_block, peer in latest_blocks:
            if Block.from_db(latest_block.block_hash) is None:
                self.fill_chain(latest_block, progress=progress)
                host, _ = host_address(peer)
                self.send_block(latest_block, visited_hosts=[host])

    def mill_block(
        self, block, mp=False, rounds=None, worksize=None, progress=None
    ):
        solved_block = None
        chain = Chain.from_db(block_hash=block.prev_hash)
        for proof_of_work in milling_generator(
            block, mp=mp, rounds=rounds, worksize=worksize, progress=progress
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
