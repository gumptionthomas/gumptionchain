from __future__ import annotations

import logging
from collections.abc import Generator
from logging import Logger
from time import sleep
from typing import Any

import httpx
from sqlalchemy.exc import SQLAlchemyError

from cancelchain.block import TXN_TIMEOUT, Block
from cancelchain.chain import Chain, is_genesis_block
from cancelchain.database import db
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
    def __init__(
        self,
        host: str | None = None,
        peers: list[str] | None = None,
        clients: dict[str, Any] | None = None,
        logger: Logger | None = None,
    ) -> None:
        self.host = host
        self.peers: list[str] = peers or []
        self.clients: dict[str, Any] = clients or {}
        self.logger: Logger = logger or logging.getLogger(__name__)
        self.pending_txns = PendingTxnSet()

    @property
    def longest_chain(self) -> Chain | None:
        longest = ChainDAO.longest()
        return Chain.from_dao(longest) if longest else None

    def send_transaction(
        self,
        txn: Transaction,
        visited_hosts: list[str] | None = None,
    ) -> None:
        visited_hosts = visited_hosts or []
        if self.host:
            host, _ = host_address(self.host)
            visited_hosts.append(host)
        for peer in self.peers:
            host, _address = host_address(peer)
            if host in visited_hosts:
                continue
            client = self.clients.get(peer)
            if client is None:
                self.logger.warning(
                    'send_transaction: no client configured for peer %s', peer
                )
                continue
            try:
                client.post_transaction(txn, visited_hosts=visited_hosts)
            except httpx.HTTPError as re:
                self.logger.warning(re)
            except Exception as e:
                self.logger.exception(e)

    def receive_transaction(
        self,
        txid: str,
        txn_json: str | bytes,
        visited_hosts: list[str] | None = None,
        process: bool = True,  # noqa: FBT001
    ) -> Transaction | None:
        added = False
        txn = Transaction.from_json(
            txn_json if isinstance(txn_json, str) else txn_json.decode()
        )
        if txid != txn.txid:
            raise InvalidTransactionIdError()
        txn.validate()
        if txn not in self.pending_txns:
            try:
                self.pending_txns.add(txn)
            except SQLAlchemyError:
                rollback_session()
                if txn not in self.pending_txns:
                    raise
            added = True
        if process:
            self.send_transaction(txn, visited_hosts=visited_hosts)
        return txn if added else None

    def discard_expired_pending_txns(self) -> None:
        expired_dt = now() - TXN_TIMEOUT
        for txn in self.pending_txns:
            if txn.timestamp_dt is not None and txn.timestamp_dt <= expired_dt:
                self.pending_txns.discard(txn)

    def send_block(
        self,
        block: Block,
        visited_hosts: list[str] | None = None,
    ) -> None:
        visited_hosts = visited_hosts or []
        if self.host:
            host, _ = host_address(self.host)
            visited_hosts.append(host)
        for peer in self.peers:
            host, _address = host_address(peer)
            if host in visited_hosts:
                continue
            client = self.clients.get(peer)
            if client is None:
                self.logger.warning(
                    'send_block: no client configured for peer %s', peer
                )
                continue
            try:
                r = client.post_block(
                    block,
                    visited_hosts=visited_hosts,
                    raise_for_status=False,
                )
                if r.status_code == 404:
                    self.fill_peer(peer, block)
            except httpx.HTTPError as re:
                self.logger.warning(re)
            except Exception as e:
                self.logger.exception(e)

    def receive_block(
        self,
        block_json: str | bytes,
        block_hash: str | None = None,
        visited_hosts: list[str] | None = None,
        process: bool = True,  # noqa: FBT001
    ) -> Block | None:
        block_str = (
            block_json if isinstance(block_json, str) else block_json.decode()
        )
        block = Block.from_json(block_str)
        if block is None:
            raise InvalidBlockError()
        if block_hash is not None and block_hash != block.block_hash:
            raise InvalidBlockHashError()
        if block.block_hash and Block.from_db(block.block_hash):
            return None
        block.validate()
        prev_hash = block.prev_hash
        if (
            prev_hash is not None
            and Block.from_db(prev_hash) is None
            and not is_genesis_block(block)
        ):
            raise MissingBlockError()
        if process:
            block = self.process_block(block, visited_hosts=visited_hosts)  # type: ignore[assignment]
        return block

    def process_block(
        self,
        block: Block,
        visited_hosts: list[str] | None = None,
    ) -> Block | None:
        if block.block_hash and Block.from_db(block.block_hash):
            return None
        if block := self.add_block(block):  # type: ignore[assignment]
            new_block_signal.send(self, block=block)
            self.send_block(block, visited_hosts=visited_hosts)
        return block

    def add_block(self, block: Block, *, commit: bool = True) -> Block | None:
        try:
            chain = Chain.from_db(block_hash=block.prev_hash)
            if chain:
                chain.add_block(block, commit=commit)
            else:
                chain = self.create_chain(block=block, commit=commit)
            chain.to_db(commit=commit)
        except SQLAlchemyError:
            rollback_session()
            # In batch mode (commit=False, used by Node.fill_chain),
            # rollback_session() above has already undone every flushed
            # block earlier in the batch. Re-raise unconditionally so
            # fill_chain's except handler aborts the whole batch — otherwise
            # the swallow path below would let fill_chain continue and
            # commit later blocks on top of a half-rolled-back session,
            # reintroducing the partial-adoption bug A2.e is meant to fix.
            if not commit or not (
                block.block_hash and Block.from_db(block.block_hash)
            ):
                raise
            block = None  # type: ignore[assignment]
        return block

    def create_chain(
        self, block: Block | None = None, *, commit: bool = True
    ) -> Chain:
        block_hash = block.prev_hash if block is not None else None
        chain = Chain(block_hash=block_hash)
        if block is not None:
            chain.add_block(block, commit=commit)
        return chain

    def request_block(self, block_hash: str) -> Block | None:
        for peer in self.peers:
            client = self.clients.get(peer)
            if client is None:
                continue
            try:
                r = client.get_block(
                    block_hash=block_hash, raise_for_status=False
                )
                if r.status_code == 200:
                    return Block.from_json(r.text)
            except httpx.HTTPError as re:
                self.logger.error(re)
            except Exception as e:
                self.logger.exception(e)
        return None

    def request_latest_blocks(
        self, peer: str | None = None
    ) -> Generator[tuple[Block, str], None, None]:
        peers = [peer] if peer is not None else self.peers
        for p in peers:
            client = self.clients.get(p)
            if client is None:
                continue
            try:
                r = client.get_block()
                yield Block.from_json(r.text), p
            except httpx.HTTPError as re:
                self.logger.error(re)
            except Exception as e:
                self.logger.exception(e)

    def fill_peer(self, peer: str, last_block: Block) -> None:
        blocks: list[Block] = []
        accepted = False
        block: Block | None = last_block
        try:
            visited_hosts: list[str] = []
            if self.host:
                host, _ = host_address(self.host)
                visited_hosts.append(host)
            client = self.clients.get(peer)
            if client is None:
                self.logger.warning(
                    'fill_peer: no client configured for peer %s', peer
                )
                return
            while not accepted:
                if block is None:
                    # Walked past the genesis block — `last_block.prev_hash`
                    # chain terminates without a peer-acceptable ancestor.
                    self.logger.warning(
                        'fill_peer: exhausted chain to peer %s without '
                        'a 2xx response; aborting',
                        peer,
                    )
                    return
                blocks.insert(0, block)
                if is_genesis_block(block) or block.prev_hash is None:
                    # Genesis has `prev_hash == GENESIS_HASH` (a sentinel
                    # that won't resolve to a stored Block); a missing
                    # `prev_hash` similarly has no parent to walk to.
                    # Stop before the next from_db lookup returns None.
                    block = None
                    break
                block = Block.from_db(block.prev_hash)
                if block is None:
                    self.logger.warning(
                        'fill_peer: chain walk for peer %s hit a missing '
                        'parent (prev_hash=%s)',
                        peer,
                        blocks[0].prev_hash,
                    )
                    return
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

    def fill_chain(
        self, last_block: Block, progress: Any | None = None
    ) -> bool:
        progress_next: Any = progress.next if progress else lambda n=1: None
        progress_switch: Any = progress.switch if progress else lambda: None
        chain_fill: ChainFill | None = None
        try:
            if last_block.block_hash and Block.from_db(last_block.block_hash):
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
            block: Block | None = last_block
            while True:
                assert block is not None
                is_genesis = is_genesis_block(block)
                prev_hash = block.prev_hash
                if (
                    prev_hash is None or Block.from_db(prev_hash)
                ) or is_genesis:
                    break
                block = self.request_block(prev_hash)
                if block is None:
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
            # Atomic apply: pass commit=False to each per-block add_block so
            # rows are flushed (not committed) into the autobegun root
            # transaction. A single db.session.commit() after the loop
            # persists all blocks atomically; db.session.rollback() on
            # exception undoes every flushed block. Closes audit finding
            # A2.e (hostile-peer partial-fork-prefix adoption).
            applied: list[Block] = []
            try:
                for chain_fill_block in chain_fill.blocks:
                    if chain_fill_block.block_json is None:
                        continue
                    block = Block.from_json(chain_fill_block.block_json)
                    # A concurrent receive between staging and apply may
                    # have already persisted this block. add_block is still
                    # called so the chain tip advances (Block.to_dao()
                    # returns the existing row, so the inner flush is a
                    # no-op for the block but updates chain state). Only
                    # append to applied — and therefore only fire the
                    # post-commit new_block signal — for blocks this batch
                    # actually persisted, honoring signals.py's
                    # "newly-persisted block" contract.
                    already_persisted = bool(
                        block.block_hash and Block.from_db(block.block_hash)
                    )
                    self.add_block(block, commit=False)
                    if not already_persisted:
                        applied.append(block)
                    progress_next()
                db.session.commit()
            except Exception:
                db.session.rollback()
                raise
            # Clean up the ChainFill staging row (its own commit) BEFORE
            # firing signals, then null out the local handle so the outer
            # finally doesn't run another commit. Otherwise any DB writes
            # performed by synchronous new_block listeners would be
            # bundled into the chain_fill cleanup transaction, undermining
            # the "post-commit notification" contract.
            chain_fill.delete()
            chain_fill = None
            # Post-commit — fire signals only for confirmed-persisted
            # blocks, in apply order.
            for block in applied:
                new_block_signal.send(self, block=block)
            return True
        except Exception as e:
            self.logger.exception(e)
        finally:
            if chain_fill is not None:
                chain_fill.delete()
        return False
