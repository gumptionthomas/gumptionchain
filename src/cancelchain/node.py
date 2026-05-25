import logging
from time import sleep

import requests
from sqlalchemy.exc import SQLAlchemyError

from cancelchain.block import TXN_TIMEOUT, Block
from cancelchain.chain import Chain, is_genesis_block
from cancelchain.exceptions import (
    InvalidBlockError,
    InvalidBlockHashError,
    InvalidTransactionIdError,
    MissingBlockError,
)
from cancelchain.models import (
    ChainDAO,
    ChainFill,
    ChainFillBlock,
    rollback_session,
)
from cancelchain.signals import new_block as new_block_signal
from cancelchain.transaction import PendingTxnSet, Transaction
from cancelchain.util import host_address, now


class Node:
    def __init__(self, host=None, peers=None, clients=None, logger=None):
        self.host = host
        self.peers = peers or []
        self.clients = clients or {}
        self.logger = logger or logging.getLogger(__name__)
        self.pending_txns = PendingTxnSet()

    @property
    def longest_chain(self):
        longest = ChainDAO.longest()
        return Chain.from_dao(longest) if longest else None

    def send_transaction(self, txn, visited_hosts=None):
        visited_hosts = visited_hosts or []
        if self.host:
            host, _ = host_address(self.host)
            visited_hosts.append(host)
        for peer in self.peers:
            host, _address = host_address(peer)
            if host not in visited_hosts:
                try:
                    self.clients.get(peer).post_transaction(
                        txn, visited_hosts=visited_hosts
                    )
                except requests.RequestException as re:
                    self.logger.warning(re)
                except Exception as e:
                    self.logger.exception(e)

    def receive_transaction(
        self, txid, txn_json, visited_hosts=None, process=True
    ):
        added = False
        txn = Transaction.from_json(txn_json)
        if txid != txn.txid:
            raise InvalidTransactionIdError()
        txn.validate()
        if txn not in self.pending_txns:
            try:
                self.pending_txns.add(txn)
            except SQLAlchemyError:
                rollback_session()
                if txn.txid not in self.pending_txns:
                    raise
            added = True
        if process:
            self.send_transaction(txn, visited_hosts=visited_hosts)
        return txn if added else None

    def discard_expired_pending_txns(self):
        expired_dt = now() - TXN_TIMEOUT
        for txn in self.pending_txns:
            if txn.timestamp_dt <= expired_dt:
                self.pending_txns.discard(txn)

    def send_block(self, block, visited_hosts=None):
        visited_hosts = visited_hosts or []
        if self.host:
            host, _ = host_address(self.host)
            visited_hosts.append(host)
        for peer in self.peers:
            host, _address = host_address(peer)
            if host not in visited_hosts:
                try:
                    r = self.clients.get(peer).post_block(
                        block,
                        visited_hosts=visited_hosts,
                        raise_for_status=False,
                    )
                    if r.status_code == 404:
                        self.fill_peer(peer, block)
                except requests.RequestException as re:
                    self.logger.warning(re)
                except Exception as e:
                    self.logger.exception(e)

    def receive_block(
        self, block_json, block_hash=None, visited_hosts=None, process=True
    ):
        block = Block.from_json(block_json)
        if block is None:
            raise InvalidBlockError()
        if block_hash is not None and block_hash != block.block_hash:
            raise InvalidBlockHashError()
        if Block.from_db(block.block_hash):
            return None
        block.validate()
        prev_hash = block.prev_hash
        if Block.from_db(prev_hash) is None and not is_genesis_block(block):
            raise MissingBlockError()
        if process:
            block = self.process_block(block, visited_hosts=visited_hosts)
        return block

    def process_block(self, block, visited_hosts=None):
        if Block.from_db(block.block_hash):
            return None
        if block := self.add_block(block):
            new_block_signal.send(self, block=block)
            self.send_block(block, visited_hosts=visited_hosts)
        return block

    def add_block(self, block):
        try:
            chain = Chain.from_db(block_hash=block.prev_hash)
            if chain:
                chain.add_block(block)
            else:
                chain = self.create_chain(block=block)
            chain.to_db()
        except SQLAlchemyError:
            rollback_session()
            if not Block.from_db(block.block_hash):
                raise
            block = None
        return block

    def create_chain(self, block=None):
        block_hash = block.prev_hash if block else None
        chain = Chain(block_hash=block_hash)
        if block:
            chain.add_block(block)
        return chain

    def request_block(self, block_hash):
        for peer in self.peers:
            try:
                r = self.clients.get(peer).get_block(
                    block_hash=block_hash, raise_for_status=False
                )
                if r.status_code == 200:
                    return Block.from_json(r.text)
            except requests.RequestException as re:
                self.logger.error(re)
            except Exception as e:
                self.logger.exception(e)
        return None

    def request_latest_blocks(self, peer=None):
        peers = [peer] if peer is not None else self.peers
        for p in peers:
            try:
                r = self.clients.get(p).get_block()
                yield Block.from_json(r.text), p
            except requests.RequestException as re:
                self.logger.error(re)
            except Exception as e:
                self.logger.exception(e)

    def fill_peer(self, peer, last_block):
        blocks = []
        accepted = False
        block = last_block
        try:
            visited_hosts = []
            if self.host:
                host, _ = host_address(self.host)
                visited_hosts.append(host)
            client = self.clients.get(peer)
            while not accepted:
                blocks.insert(0, block)
                block = Block.from_db(block.prev_hash)
                r = client.post_block(
                    block, visited_hosts=visited_hosts, raise_for_status=False
                )
                if r.status_code in [200, 201, 202]:
                    accepted = True
                if r.status_code != 404:
                    r.raise_for_status()
            for block in blocks:
                accepted = False
                delay = 0
                while not accepted:
                    r = client.post_block(
                        block,
                        visited_hosts=visited_hosts,
                        raise_for_status=False,
                    )
                    if r.status_code in [200, 201, 202]:
                        accepted = True
                    else:
                        if r.status_code != 404:
                            r.raise_for_status()
                        sleep(delay)
                        delay += 1
                        if delay > 10:
                            r.raise_for_status()
        except Exception as e:
            self.logger.exception(e)

    def fill_chain(self, last_block, progress=None):
        progress_next = progress.next if progress else lambda n=1: None
        progress_switch = progress.switch if progress else lambda: None
        chain_fill = None
        try:
            if Block.from_db(last_block.block_hash):
                return True
            chain_fill = ChainFill()
            chain_fill.commit()
            ChainFillBlock(
                block_hash=last_block.block_hash,
                idx=last_block.idx,
                block_json=last_block.to_json(),
                chain_fill=chain_fill,
            ).commit()
            progress_next()
            block = last_block
            while True:
                is_genesis = is_genesis_block(block)
                prev_hash = block.prev_hash
                if Block.from_db(prev_hash) or is_genesis:
                    break
                block = self.request_block(prev_hash)
                if not block:
                    self.logger.error(f'Block request failed: {prev_hash}')
                    return False
                progress_next()
                ChainFillBlock(
                    block_hash=block.block_hash,
                    idx=block.idx,
                    block_json=block.to_json(),
                    chain_fill=chain_fill,
                ).commit()
            progress_switch()
            for chain_fill_block in chain_fill.blocks:
                block = Block.from_json(chain_fill_block.block_json)
                self.add_block(block)
                new_block_signal.send(self, block=block)
                progress_next()
            return True
        except Exception as e:
            self.logger.exception(e)
        finally:
            if chain_fill is not None:
                chain_fill.delete()
        return False
