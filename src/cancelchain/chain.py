from __future__ import annotations

# mypy: disable-error-code="no-untyped-call,no-any-return"
import json
from collections.abc import Generator, Iterator
from dataclasses import dataclass, field
from functools import total_ordering
from typing import Any, Self

from cancelchain.block import Block
from cancelchain.exceptions import (
    EmptyChainError,
    FutureBlockError,
    ImbalancedTransactionError,
    InflowOutflowAddressMismatchError,
    InsufficientFundsError,
    InvalidBlockError,
    InvalidBlockIndexError,
    InvalidChainError,
    InvalidCoinbaseErrorRewardError,
    InvalidInflowOutflowError,
    InvalidPreviousHashError,
    InvalidTargetError,
    MissingInflowOutflowError,
    MissingPreviousBlockError,
    OutOfOrderBlockError,
    SpentTransactionError,
)
from cancelchain.milling import mill_hash_str
from cancelchain.models import BlockDAO, ChainDAO
from cancelchain.payload import Inflow, Outflow
from cancelchain.transaction import Transaction
from cancelchain.util import dt_2_iso, now
from cancelchain.wallet import Wallet

CURMUDGEON_PER_GRUMBLE = 100
GENESIS_HASH = mill_hash_str('GENESIS')
MAX_TARGET = '0' * 6 + 'F' * 58
REWARD = 100 * CURMUDGEON_PER_GRUMBLE
TARGET_GOAL_SECONDS = 600
TARGET_INTERVAL = 2016
TARGET_INTERVAL_SECONDS = TARGET_GOAL_SECONDS * TARGET_INTERVAL


def is_genesis_block(block: Block) -> bool:
    return block.prev_hash == GENESIS_HASH


@total_ordering
@dataclass()
class Chain:
    cid: int | None = field(default=None, compare=False)
    block_hash: str | None = field(default=None, compare=True)

    @property
    def blocks(self) -> Generator[Block, None, None]:
        return self.block_chain(block_hash=self.block_hash)

    @property
    def last_block(self) -> Block | None:
        return next(self.blocks, None)

    @property
    def length(self) -> int:
        last = self.last_block
        if last is None:
            return 0
        return (last.idx or 0) + 1

    @property
    def target(self) -> str:
        return self.block_target()

    def block_chain(
        self,
        block: Block | None = None,
        block_hash: str | None = None,
    ) -> Generator[Block, None, None]:
        if block is None and block_hash is not None:
            block = Block.from_db(block_hash)
        while block is not None:
            yield block
            prev_block = (
                Block.from_db(block.prev_hash) if block.prev_hash else None
            )
            if prev_block is None and not is_genesis_block(block):
                raise MissingPreviousBlockError()
            if is_genesis_block(block) and prev_block is not None:
                raise InvalidBlockError()
            block = prev_block

    def get_block_by_reverse_index(self, i: int = 0) -> Block | None:
        chain_dao = self.to_dao()
        if chain_dao is not None:
            last = self.last_block
            last_idx = last.idx if last is not None else 0
            index = (last_idx or 0) - i
            block_dao = chain_dao.get_block(idx=index)
            return Block.from_dao(block_dao) if block_dao else None
        else:
            for index, block in enumerate(self.blocks):
                if index == i:
                    return block
            return None

    def block_target(self, block: Block | None = None) -> str:  # noqa: PLR0911
        if not self.last_block:
            return MAX_TARGET
        last_index = self.last_block.idx or 0
        index = (block.idx or 0) if block else last_index + 1
        if index == 0:
            return MAX_TARGET
        i = last_index - index
        prev_block = self.get_block_by_reverse_index(i + 1)
        if prev_block is None:
            return MAX_TARGET
        prev_target = prev_block.target
        if index % TARGET_INTERVAL == 0:
            start_i = i + TARGET_INTERVAL
            start_block = self.get_block_by_reverse_index(start_i)
            if start_block is None:
                return prev_target
            prev_ts = prev_block.timestamp_dt
            start_ts = start_block.timestamp_dt
            if prev_ts is None or start_ts is None:
                return prev_target
            interval_delta = prev_ts - start_ts
            factor = interval_delta.total_seconds() / TARGET_INTERVAL_SECONDS
            factor = min(max(factor, 0.25), 4.0)
            new_target = f'{int(int(prev_target, 16) * factor):064x}'
            if int(new_target, 16) > int(MAX_TARGET, 16):
                new_target = MAX_TARGET
            return new_target
        else:
            return prev_target

    def block_reward(self, block: Block | None = None) -> int:
        return REWARD

    def link_block(self, block: Block) -> None:
        last_block = self.last_block
        index = (last_block.idx or 0) + 1 if last_block else 0
        prev_hash = last_block.block_hash if last_block else GENESIS_HASH
        target = self.target or MAX_TARGET
        block.link(index, prev_hash or GENESIS_HASH, target)

    def seal_block(self, block: Block, wallet: Wallet) -> None:
        block.seal(wallet, self.block_reward(block))

    def add_block(self, block: Block) -> None:
        self.validate_block(block)
        block.to_db()
        self.block_hash = block.block_hash

    def validate(self, progress: Any = None) -> bool:
        _progress_next = progress.next if progress else lambda n=1: None
        if not self.last_block:
            raise EmptyChainError()
        for block in self.blocks:
            try:
                self.validate_block(block)
                _progress_next(n=1)
            except InvalidBlockError as e:
                raise InvalidChainError({f'Block #{block.idx}': e.messages})
        return True

    def validate_block(self, block: Block) -> None:
        block.validate()
        if block.timestamp_dt is not None and block.timestamp_dt > now():
            raise FutureBlockError()
        prev_block = Block.from_db(block.prev_hash) if block.prev_hash else None
        if prev_block is None and not is_genesis_block(block):
            raise InvalidPreviousHashError()
        if (
            prev_block is not None
            and block.timestamp_dt is not None
            and prev_block.timestamp_dt is not None
            and block.timestamp_dt < prev_block.timestamp_dt
        ):
            raise OutOfOrderBlockError()
        prev_hash = prev_block.block_hash if prev_block else None
        if block.prev_hash != prev_hash and not is_genesis_block(block):
            raise InvalidPreviousHashError()
        prev_index: int = (
            prev_block.idx
            if prev_block is not None and prev_block.idx is not None
            else -1
        )
        if block.idx != prev_index + 1:
            raise InvalidBlockIndexError()
        if block.target != self.block_target(block=block):
            raise InvalidTargetError()
        for txn in block.regular_txns:
            self.validate_block_txn(block, txn)
        self.validate_block_coinbase(block)

    def validate_block_txn(
        self,
        block: Block,
        txn: Transaction,
        txn_in_block: bool = True,  # noqa: FBT001
    ) -> None:
        # add inflow amounts
        subject_amounts: dict[str, int] = {}
        other_amounts = 0
        for i in txn.inflows:
            amount, subject = self.validate_txn_inflow(
                block, txn, i, txn_in_block=txn_in_block
            )
            if subject:
                subject_amount: int | None = subject_amounts.get(subject, 0)
                subject_amounts[subject] = (subject_amount or 0) + amount
            else:
                other_amounts += amount
        # subtract outflow amounts
        for o in txn.outflows:
            if o.forgive:
                forgive_amount = subject_amounts.get(o.forgive, 0)
                subject_amounts[o.forgive] = forgive_amount - (o.amount or 0)
            elif o.subject:
                subject_amount = subject_amounts.get(o.subject)
                if subject_amount and subject_amount > 0:
                    if (o.amount or 0) > subject_amount:
                        subject_amounts[o.subject] = 0
                        other_amounts -= (o.amount or 0) - subject_amount
                    else:
                        subject_amounts[o.subject] = subject_amount - (
                            o.amount or 0
                        )
                else:
                    other_amounts -= o.amount or 0
            else:
                other_amounts -= o.amount or 0
        if other_amounts != 0:
            raise ImbalancedTransactionError()
        for _, amount in subject_amounts.items():
            if amount != 0:
                raise ImbalancedTransactionError()

    def validate_txn_inflow(
        self,
        block: Block,
        txn: Transaction,
        i: Inflow,
        txn_in_block: bool = True,  # noqa: FBT001
    ) -> tuple[int, str | None]:
        # txn inflow's outflow exists
        ioflow: Outflow | None = None
        ioflow_txn: Transaction | None = None
        if i.outflow_txid is not None:
            ioflow_txn = self.get_transaction(i.outflow_txid, start_block=block)
            if ioflow_txn is not None:
                ioflow = ioflow_txn.get_outflow(i.outflow_idx or 0)
        if not ioflow:
            raise MissingInflowOutflowError()
        # inflow's outflow can't be for forgiveness or support
        if ioflow.forgive is not None or ioflow.support is not None:
            raise InvalidInflowOutflowError()
        # inflow's outflow address equals the txn address
        address = (
            ioflow.address
            if ioflow.address
            else (ioflow_txn.address if ioflow_txn else None)
        )
        if address != txn.address:
            raise InflowOutflowAddressMismatchError()
        # txn inflow's outflow not already used in other inflow
        num_inflows = self.get_inflows_count(
            block, i.outflow_txid or '', i.outflow_idx or 0
        )
        if num_inflows > 1 or (num_inflows > 0 and not txn_in_block):
            raise SpentTransactionError()
        return ioflow.amount or 0, ioflow.subject

    def validate_block_coinbase(self, block: Block) -> None:
        block.validate_coinbase()
        reward = self.block_reward(block)
        cb = block.coinbase
        if cb is not None:
            outflow = cb.get_outflow(0)
            if outflow is not None and outflow.amount != reward:
                raise InvalidCoinbaseErrorRewardError()

    def get_block(self, block_hash: str) -> Block | None:
        dao = self.to_dao()
        if dao is None:
            return None
        block_dao = dao.get_block(block_hash)
        return Block.from_dao(block_dao) if block_dao else None

    def get_transaction(
        self, txid: str, start_block: Block | None = None
    ) -> Transaction | None:
        block: Block | None = start_block or self.last_block
        while block is not None and BlockDAO.get(block.block_hash) is None:
            for txn in block.txns:
                if txn.txid == txid:
                    return txn
            block = Block.from_db(block.prev_hash) if block.prev_hash else None
            if block is None:
                return None
        if block is not None:
            block_dao = BlockDAO.get(block.block_hash)
            if block_dao is not None:
                txn_dao = block_dao.get_transaction_in_chain(txid)
                return Transaction.from_dao(txn_dao) if txn_dao else None
        return None

    def get_inflows_count(
        self,
        start_block: Block,
        outflow_txid: str,
        outflow_idx: int,
    ) -> int:
        i = 0
        block: Block | None = start_block
        while block is not None and BlockDAO.get(block.block_hash) is None:
            for txn in block.txns:
                for inflow in txn.inflows:
                    if (
                        inflow.outflow_txid == outflow_txid
                        and inflow.outflow_idx == outflow_idx
                    ):
                        i += 1
            block = Block.from_db(block.prev_hash) if block.prev_hash else None
        if block is not None:
            block_dao = BlockDAO.get(block.block_hash)
            if block_dao is not None:
                i += block_dao.inflows_in_chain_count(outflow_txid, outflow_idx)
        return i

    def unspent_outflows(
        self,
        address: str,
        limit: int | None = None,
        filter_pending: bool = False,  # noqa: FBT001
    ) -> Iterator[tuple[str, int, Outflow]]:
        amount = 0
        outflow_daos = self.to_dao().unspent_outflows(
            address, filter_pending=filter_pending
        )
        for outflow_dao in outflow_daos:
            txn = Transaction.from_dao(outflow_dao.transaction)
            index = outflow_dao.idx
            outflow = txn.get_outflow(index=index)
            if outflow is None:
                continue
            amount += outflow.amount or 0
            yield (outflow_dao.txid, outflow_dao.idx, outflow)
            if limit is not None and amount >= limit:
                break

    def unforgiven_outflows(
        self,
        subject: str,
        filter_pending: bool = False,  # noqa: FBT001
    ) -> Iterator[tuple[str, int, Outflow]]:
        outflow_daos = self.to_dao().unforgiven_outflows(
            subject, filter_pending=filter_pending
        )
        for outflow_dao in outflow_daos:
            txn = Transaction.from_dao(outflow_dao.transaction)
            index = outflow_dao.idx
            outflow = txn.get_outflow(index=index)
            if outflow is None:
                continue
            yield (outflow_dao.txid, outflow_dao.idx, outflow)

    def unforgiven_address_outflows(
        self,
        address: str,
        subject: str,
        limit: int | None = None,
        filter_pending: bool = False,  # noqa: FBT001
    ) -> Iterator[tuple[str, int, Outflow]]:
        amount = 0
        outflow_daos = self.to_dao().unforgiven_outflows(
            subject, address=address, filter_pending=filter_pending
        )
        for outflow_dao in outflow_daos:
            txn = Transaction.from_dao(outflow_dao.transaction)
            index = outflow_dao.idx
            outflow = txn.get_outflow(index=index)
            if outflow is None:
                continue
            amount += outflow.amount or 0
            yield (outflow_dao.txid, outflow_dao.idx, outflow)
            if limit is not None and amount >= limit:
                break

    def balance(self, address: str) -> int:
        return int(self.to_dao().wallet_balance(address))

    def subject_balance(self, subject: str) -> int:
        return int(self.to_dao().subject_balance(subject))

    def subject_support(self, subject: str) -> int:
        return int(self.to_dao().subject_support(subject))

    def create_transfer(
        self, wallet: Wallet, amount: int, dest_address: str
    ) -> Transaction:
        address = wallet.address
        balance = 0
        t = Transaction()
        unspent = self.unspent_outflows(
            address, limit=amount, filter_pending=True
        )
        for txid, index, outflow in unspent:
            balance += outflow.amount or 0
            t.add_inflow(Inflow(outflow_txid=txid, outflow_idx=index))
        if balance < amount:
            raise InsufficientFundsError()
        t.add_outflow(Outflow(amount=amount, address=dest_address))
        if balance - amount:
            t.add_outflow(Outflow(amount=balance - amount, address=address))
        t.set_wallet(wallet)
        t.seal()
        return t

    def create_subject(
        self,
        wallet: Wallet,
        amount: int,
        subject: str,
        outflows: list[tuple[str, int, int]] | None = None,
        timestamp: Any = None,
    ) -> Transaction:
        address = wallet.address
        balance = 0
        t = Transaction()
        if timestamp is not None:
            t.timestamp = dt_2_iso(timestamp)
        if outflows is not None:
            for pre_outflow in outflows:
                balance += pre_outflow[2]
                t.add_inflow(
                    Inflow(
                        outflow_txid=pre_outflow[0],
                        outflow_idx=pre_outflow[1],
                    )
                )
        if balance < amount:
            unspent = self.unspent_outflows(
                address, limit=amount - balance, filter_pending=True
            )
            for txid, index, unspent_outflow in unspent:
                balance += unspent_outflow.amount or 0
                t.add_inflow(Inflow(outflow_txid=txid, outflow_idx=index))
        if balance < amount:
            raise InsufficientFundsError()
        t.add_outflow(Outflow(amount=amount, subject=subject))
        if balance - amount:
            t.add_outflow(Outflow(amount=balance - amount, address=address))
        t.set_wallet(wallet)
        t.seal()
        return t

    def create_forgive(
        self, wallet: Wallet, amount: int, subject: str
    ) -> Transaction:
        address = wallet.address
        balance = 0
        t = Transaction()
        unforgiven = self.unforgiven_address_outflows(
            address, subject, limit=amount, filter_pending=True
        )
        for txid, index, outflow in unforgiven:
            balance += outflow.amount or 0
            t.add_inflow(Inflow(outflow_txid=txid, outflow_idx=index))
        if balance < amount:
            raise InsufficientFundsError()
        t.add_outflow(Outflow(amount=amount, forgive=subject))
        if balance - amount:
            t.add_outflow(Outflow(amount=balance - amount, subject=subject))
        t.set_wallet(wallet)
        t.seal()
        return t

    def create_support(
        self,
        wallet: Wallet,
        amount: int,
        subject: str,
        outflows: list[tuple[str, int, int]] | None = None,
        timestamp: Any = None,
    ) -> Transaction:
        address = wallet.address
        balance = 0
        t = Transaction()
        if timestamp is not None:
            t.timestamp = dt_2_iso(timestamp)
        if outflows is not None:
            for pre_outflow in outflows:
                balance += pre_outflow[2]
                t.add_inflow(
                    Inflow(
                        outflow_txid=pre_outflow[0],
                        outflow_idx=pre_outflow[1],
                    )
                )
        if balance < amount:
            unspent = self.unspent_outflows(
                address, limit=amount - balance, filter_pending=True
            )
            for txid, index, unspent_outflow in unspent:
                balance += unspent_outflow.amount or 0
                t.add_inflow(Inflow(outflow_txid=txid, outflow_idx=index))
        if balance < amount:
            raise InsufficientFundsError()
        t.add_outflow(Outflow(amount=amount, support=subject))
        if balance - amount:
            t.add_outflow(Outflow(amount=balance - amount, address=address))
        t.set_wallet(wallet)
        t.seal()
        return t

    def to_dict(self) -> dict[str, Any]:
        return {'cid': self.cid, 'block_hash': self.block_hash}

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    def to_dao(self, create: bool = False) -> Any:  # noqa: FBT001
        dao = None
        dao = ChainDAO.get(block_hash=self.block_hash)
        if dao is None and self.cid is not None:
            dao = ChainDAO.get(id=self.cid)
            try:
                dao.set_block_hash(self.block_hash)
            except Exception:
                dao = None
        if not dao:
            dao = ChainDAO.get(block_hash=self.block_hash)
        if not dao and create:
            dao = ChainDAO(self.block_hash)
        return dao

    def to_db(self) -> None:
        dao = self.to_dao(create=True)
        dao.commit()
        self.cid = dao.id

    def __lt__(self, other: Chain) -> bool:
        return self.length < other.length

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Self:
        cid = d.get('cid')
        block_hash = d.get('block_hash')
        chain = cls(cid=cid, block_hash=block_hash)
        return chain

    @classmethod
    def from_json(cls, j: str) -> Self:
        return cls.from_dict(json.loads(j))

    @classmethod
    def from_dao(cls, dao: Any) -> Self:
        return cls(cid=dao.id, block_hash=dao.block_hash)

    @classmethod
    def from_db(
        cls, cid: int | None = None, block_hash: str | None = None
    ) -> Self | None:
        if cid:
            dao = ChainDAO.get(id=cid)
        else:
            dao = ChainDAO.get(block_hash=block_hash)
        return cls.from_dao(dao) if dao else None
