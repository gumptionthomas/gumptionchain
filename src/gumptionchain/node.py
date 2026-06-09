from __future__ import annotations

import logging
from collections.abc import Generator
from logging import Logger
from time import sleep
from typing import Any

import httpx
from flask import current_app
from sqlalchemy.exc import SQLAlchemyError

from gumptionchain.block import Block, expiry_cutoff
from gumptionchain.chain import GENESIS_HASH, Chain, is_genesis_block
from gumptionchain.database import db
from gumptionchain.exceptions import (
    DuplicateMinedTransactionError,
    InvalidBlockError,
    InvalidBlockHashError,
    InvalidTransactionIdError,
    MempoolFullError,
    MissingBlockError,
)
from gumptionchain.models import (
    ChainDAO,
    ChainFill,
    ChainFillBlock,
    TransactionDAO,
    rollback_session,
)
from gumptionchain.signals import new_block as new_block_signal
from gumptionchain.transaction import PendingTxnSet, Transaction
from gumptionchain.util import host_address, now


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
        if TransactionDAO.get(txn.txid) is not None:
            raise DuplicateMinedTransactionError()
        if txn not in self.pending_txns:
            if len(self.pending_txns) >= current_app.config['MAX_PENDING_TXNS']:
                raise MempoolFullError()
            try:
                self.pending_txns.add(txn)
            except SQLAlchemyError:
                rollback_session()
                if txn not in self.pending_txns:
                    raise
            added = True
        if process and added:
            self.send_transaction(txn, visited_hosts=visited_hosts)
        return txn if added else None

    def discard_expired_pending_txns(self) -> None:
        self.pending_txns.discard_expired(expiry_cutoff(now()))

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
            self._discard_confirmed_pending(block)
            new_block_signal.send(self, block=block)
            self.send_block(block, visited_hosts=visited_hosts)
        return block

    def _discard_confirmed_pending(self, block: Block) -> None:
        # A confirmed txn no longer belongs in the mempool: discard the
        # accepted block's regular txns from the pending pool (discard
        # is a no-op for txids not pooled here, e.g. txns first seen
        # inside a gossip-received block). Lives in process_block — not
        # add_block — so it fires only on live acceptance and never
        # inside fill_chain's commit=False batch.
        #
        # Orphan caveat (accept + document, #208): if this block is
        # later orphaned by a reorg, its txns are already gone from
        # THIS node's pool and will not be re-mined here unless a peer
        # re-gossips them or the sender re-submits. The read-time
        # canonical filter (exclude_confirmed) keeps the mempool views
        # correct regardless, so this is a resource trade-off, not a
        # display or consensus bug.
        #
        # Fail-soft: a transient DB error during the prune must never
        # block acceptance or gossip of an already-committed block; the
        # read-time canonical filter is the correctness backstop.
        try:
            for txn in block.regular_txns:
                self.pending_txns.discard(txn)
        except SQLAlchemyError as e:
            rollback_session()
            self.logger.warning(e)

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
                    block = Block.from_json(r.text)
                    # Verify the block's COMPUTED header hash equals the
                    # requested hash, not just the self-reported block_hash
                    # field (which a hostile peer controls in the JSON). The
                    # computed hash binds the block's actual content, so a
                    # peer cannot forge a block that hashes to an
                    # attacker-chosen value (second-preimage resistance) —
                    # this is what stops a hostile peer steering fill_chain's
                    # walk. We also require the self-reported field to agree,
                    # rejecting internally-inconsistent blocks.
                    if (
                        block is not None
                        and block.block_hash == block_hash
                        and block.get_header_hash() == block_hash
                    ):
                        return block
                    self.logger.warning(
                        'request_block: peer %s returned a block whose hash '
                        'does not match the requested %s; ignoring',
                        peer,
                        block_hash,
                    )
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
            max_depth = current_app.config['MAX_CHAIN_FILL_DEPTH']
            requested = 0
            while True:
                assert block is not None
                is_genesis = is_genesis_block(block)
                prev_hash = block.prev_hash
                if (
                    prev_hash is None or Block.from_db(prev_hash)
                ) or is_genesis:
                    break
                requested += 1
                if requested > max_depth:
                    self.logger.warning(
                        'fill_chain: exceeded MAX_CHAIN_FILL_DEPTH (%d) '
                        'walking back from tip %s; aborting',
                        max_depth,
                        last_block.block_hash,
                    )
                    return False
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

    def sync_forward(self, client: Any, progress: Any | None = None) -> str:
        """Network-import a peer's longest chain forward by height.

        Fetch the peer's longest-chain blocks in ascending-height batches
        (`SYNC_BATCH_SIZE` per request) and validate + commit each block
        genesis-first via `add_block(commit=True)` — the proven per-block
        `import` model, with a peer's HTTP API as the source instead of a
        file.

        Resumable: each block is committed as it is validated, so the
        committed local tip *is* the progress. An interruption leaves a
        shorter valid chain and a re-run resumes from the new tip. There is
        no `MAX_CHAIN_FILL_DEPTH` ceiling on this path (that bounds the
        backward `fill_chain` gossip short-fill, which is untouched).

        Anti-tamper / anti-DoS comes from three checks per block:
          1. computed-header-hash integrity (`get_header_hash() ==
             block_hash`) — a peer can't forge a block's identity;
          2. prev_hash linkage to the current tip (genesis links to
             `GENESIS_HASH`) — a peer can't splice a fork into our chain;
          3. full `validate_block` (PoW, merkle, index, txns) inside
             `add_block` before commit — a peer can't cheaply mint garbage.

        A failure of (1) or (2) means the peer's chain doesn't extend ours
        (a fork, or a tampered/inconsistent block): this pass is extend-only,
        so it stops and returns `'diverged'`, having committed nothing past
        the fork point (full reorg-via-forward-sync is a follow-up). A
        `validate_block` failure in `add_block` raises and propagates to the
        caller (the `sync` command's per-peer try/except), having committed
        only the valid ancestors. A clean run to the peer's tip returns
        `'caught_up'`.
        """
        batch_size = current_app.config['SYNC_BATCH_SIZE']
        last_next_idx: int | None = None
        while True:
            tip = self.longest_chain.last_block if self.longest_chain else None
            next_idx = (
                (tip.idx + 1) if tip is not None and tip.idx is not None else 0
            )
            # No-progress guard: if the previous (non-empty) batch failed to
            # advance the committed tip, the same next_idx recurs — a peer
            # serving blocks already in our DB but off the longest chain (which
            # add_block swallows without raising) would otherwise spin forever.
            # Stop rather than hang.
            if next_idx == last_next_idx:
                self.logger.warning(
                    'sync_forward: no progress at idx %s; aborting', next_idx
                )
                return 'diverged'
            last_next_idx = next_idx
            blocks = client.get_blocks(next_idx, batch_size)
            if not blocks:
                return 'caught_up'
            for block in blocks:
                if block.get_header_hash() != block.block_hash:
                    self.logger.warning(
                        'sync_forward: header-hash mismatch at idx %s '
                        '(computed %s != reported %s); aborting',
                        block.idx,
                        block.get_header_hash(),
                        block.block_hash,
                    )
                    return 'diverged'
                tip = (
                    self.longest_chain.last_block
                    if self.longest_chain
                    else None
                )
                expected_prev = (
                    tip.block_hash if tip is not None else GENESIS_HASH
                )
                if block.prev_hash != expected_prev:
                    self.logger.warning(
                        'sync_forward: diverged at idx %s '
                        '(expected prev_hash %s, got %s); aborting',
                        block.idx,
                        expected_prev,
                        block.prev_hash,
                    )
                    return 'diverged'
                self.add_block(block, commit=True)
                if progress is not None:
                    progress.next()
