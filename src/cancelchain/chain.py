import json
from dataclasses import dataclass, field
from functools import total_ordering

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

CURMUDGEON_PER_GRUMBLE = 100
GENESIS_HASH = mill_hash_str('GENESIS')
MAX_TARGET = '0' * 6 + 'F' * 58
REWARD = 100 * CURMUDGEON_PER_GRUMBLE
TARGET_GOAL_SECONDS = 600
TARGET_INTERVAL = 2016
TARGET_INTERVAL_SECONDS = TARGET_GOAL_SECONDS * TARGET_INTERVAL


def is_genesis_block(block):
    return block.prev_hash == GENESIS_HASH


@total_ordering
@dataclass()
class Chain:
    cid: int = field(default=None, compare=False)
    block_hash: str = field(default=None, compare=True)

    @property
    def blocks(self):
        return self.block_chain(block_hash=self.block_hash)

    @property
    def last_block(self):
        return next(self.blocks, None)

    @property
    def length(self):
        return self.last_block.idx + 1

    @property
    def target(self):
        return self.block_target()

    def block_chain(self, block=None, block_hash=None):
        if not block and block_hash:
            block = Block.from_db(block_hash)
        while block is not None:
            yield block
            prev_block = Block.from_db(block.prev_hash)
            if prev_block is None and not is_genesis_block(block):
                raise MissingPreviousBlockError()
            if is_genesis_block(block) and prev_block is not None:
                raise InvalidBlockError()
            block = prev_block

    def get_block_by_reverse_index(self, i=0):
        chain_dao = self.to_dao()
        if chain_dao is not None:
            index = self.last_block.idx - i
            block_dao = chain_dao.get_block(idx=index)
            return Block.from_dao(block_dao) if block_dao else None
        else:
            for index, block in enumerate(self.blocks):
                if index == i:
                    return block
            return None

    def block_target(self, block=None):
        if not self.last_block:
            return MAX_TARGET
        last_index = self.last_block.idx
        index = block.idx if block else last_index + 1
        if index == 0:
            return MAX_TARGET
        i = last_index - index
        prev_block = self.get_block_by_reverse_index(i + 1)
        prev_target = prev_block.target
        if index % TARGET_INTERVAL == 0:
            start_i = i + TARGET_INTERVAL
            start_block = self.get_block_by_reverse_index(start_i)
            interval_delta = prev_block.timestamp_dt - start_block.timestamp_dt
            factor = interval_delta.total_seconds() / TARGET_INTERVAL_SECONDS
            factor = min(max(factor, 0.25), 4.0)
            new_target = f'{int(int(prev_target, 16) * factor):064x}'
            if int(new_target, 16) > int(MAX_TARGET, 16):
                new_target = MAX_TARGET
            return new_target
        else:
            return prev_target

    def block_reward(self, block=None):
        return REWARD

    def link_block(self, block):
        last_block = self.last_block
        index = last_block.idx + 1 if last_block else 0
        prev_hash = last_block.block_hash if last_block else GENESIS_HASH
        target = self.target or MAX_TARGET
        block.link(index, prev_hash, target)

    def seal_block(self, block, wallet):
        block.seal(wallet, self.block_reward(block))

    def add_block(self, block):
        self.validate_block(block)
        block.to_db()
        self.block_hash = block.block_hash

    def validate(self, progress=None):
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

    def validate_block(self, block):
        block.validate()
        if block.timestamp_dt > now():
            raise FutureBlockError()
        prev_block = Block.from_db(block.prev_hash)
        if prev_block is None and not is_genesis_block(block):
            raise InvalidPreviousHashError()
        if prev_block and block.timestamp_dt < prev_block.timestamp_dt:
            raise OutOfOrderBlockError()
        prev_hash = prev_block.block_hash if prev_block else None
        if block.prev_hash != prev_hash and not is_genesis_block(block):
            raise InvalidPreviousHashError()
        prev_index = prev_block.idx if prev_block else -1
        if block.idx != prev_index + 1:
            raise InvalidBlockIndexError()
        if block.target != self.block_target(block=block):
            raise InvalidTargetError()
        for txn in block.regular_txns:
            self.validate_block_txn(block, txn)
        self.validate_block_coinbase(block)

    def validate_block_txn(self, block, txn, txn_in_block=True):
        # add inflow amounts
        subject_amounts = {}
        other_amounts = 0
        for i in txn.inflows:
            amount, subject = self.validate_txn_inflow(
                block, txn, i, txn_in_block=txn_in_block
            )
            if subject:
                subject_amount = subject_amounts.get(subject, 0)
                subject_amounts[subject] = subject_amount + amount
            else:
                other_amounts += amount
        # subtract outflow amounts
        for o in txn.outflows:
            if o.forgive:
                forgive_amount = subject_amounts.get(o.forgive, 0)
                subject_amounts[o.forgive] = forgive_amount - o.amount
            elif o.subject:
                subject_amount = subject_amounts.get(o.subject)
                if subject_amount and subject_amount > 0:
                    if o.amount > subject_amount:
                        subject_amounts[o.subject] = 0
                        other_amounts -= o.amount - subject_amount
                    else:
                        subject_amounts[o.subject] = subject_amount - o.amount
                else:
                    other_amounts -= o.amount
            else:
                other_amounts -= o.amount
        if other_amounts != 0:
            raise ImbalancedTransactionError()
        for _, amount in subject_amounts.items():
            if amount != 0:
                raise ImbalancedTransactionError()

    def validate_txn_inflow(self, block, txn, i, txn_in_block=True):
        # txn inflow's outflow exists
        ioflow = None
        if ioflow_txn := self.get_transaction(
            i.outflow_txid, start_block=block
        ):
            ioflow = ioflow_txn.get_outflow(i.outflow_idx)
        if not ioflow:
            raise MissingInflowOutflowError()
        # inflow's outflow can't be for forgiveness or support
        if ioflow.forgive is not None or ioflow.support is not None:
            raise InvalidInflowOutflowError()
        # inflow's outflow address equals the txn address
        address = ioflow.address if ioflow.address else ioflow_txn.address
        if address != txn.address:
            raise InflowOutflowAddressMismatchError()
        # txn inflow's outflow not already used in other inflow
        num_inflows = self.get_inflows_count(
            block, i.outflow_txid, i.outflow_idx
        )
        if num_inflows > 1 or (num_inflows > 0 and not txn_in_block):
            raise SpentTransactionError()
        return ioflow.amount, ioflow.subject

    def validate_block_coinbase(self, block):
        block.validate_coinbase()
        reward = self.block_reward(block)
        if block.coinbase.outflows[0].amount != reward:
            raise InvalidCoinbaseErrorRewardError()

    def get_block(self, block_hash):
        block_dao = self.to_dao().get_block(block_hash)
        return Block.from_dao(block_dao) if block_dao else None

    def get_transaction(self, txid, start_block=None):
        block = start_block or self.last_block
        while (block_dao := BlockDAO.get(block.block_hash)) is None:
            for txn in block.txns:
                if txn.txid == txid:
                    return txn
            block = Block.from_db(block.prev_hash)
            if block is None:
                return None
        if block_dao is not None:
            txn_dao = block_dao.get_transaction_in_chain(txid)
            return Transaction.from_dao(txn_dao) if txn_dao else None
        return None

    def get_inflows_count(self, start_block, outflow_txid, outflow_idx):
        i = 0
        block = start_block
        while (block_dao := BlockDAO.get(block.block_hash)) is None:
            for txn in block.txns:
                for inflow in txn.inflows:
                    if (
                        inflow.outflow_txid == outflow_txid
                        and inflow.outflow_idx == outflow_idx
                    ):
                        i += 1
            block = Block.from_db(block.prev_hash)
            if block is None:
                break
        if block_dao is not None:
            i += block_dao.inflows_in_chain_count(outflow_txid, outflow_idx)
        return i

    def unspent_outflows(self, address, limit=None, filter_pending=False):
        amount = 0
        outflow_daos = self.to_dao().unspent_outflows(
            address, filter_pending=filter_pending
        )
        for outflow_dao in outflow_daos:
            txn = Transaction.from_dao(outflow_dao.transaction)
            index = outflow_dao.idx
            outflow = txn.get_outflow(index=index)
            amount += outflow.amount
            yield (outflow_dao.txid, outflow_dao.idx, outflow)
            if limit is not None and amount >= limit:
                break

    def unforgiven_outflows(self, subject, filter_pending=False):
        outflow_daos = self.to_dao().unforgiven_outflows(
            subject, filter_pending=filter_pending
        )
        for outflow_dao in outflow_daos:
            txn = Transaction.from_dao(outflow_dao.transaction)
            index = outflow_dao.idx
            outflow = txn.get_outflow(index=index)
            yield (outflow_dao.txid, outflow_dao.idx, outflow)

    def unforgiven_address_outflows(
        self, address, subject, limit=None, filter_pending=False
    ):
        amount = 0
        outflow_daos = self.to_dao().unforgiven_outflows(
            subject, address=address, filter_pending=filter_pending
        )
        for outflow_dao in outflow_daos:
            txn = Transaction.from_dao(outflow_dao.transaction)
            index = outflow_dao.idx
            outflow = txn.get_outflow(index=index)
            amount += outflow.amount
            yield (outflow_dao.txid, outflow_dao.idx, outflow)
            if limit is not None and amount >= limit:
                break

    def balance(self, address):
        return int(self.to_dao().wallet_balance(address))

    def subject_balance(self, subject):
        return int(self.to_dao().subject_balance(subject))

    def subject_support(self, subject):
        return int(self.to_dao().subject_support(subject))

    def create_transfer(self, wallet, amount, dest_address):
        address = wallet.address
        balance = 0
        t = Transaction()
        unspent = self.unspent_outflows(
            address, limit=amount, filter_pending=True
        )
        for txid, index, outflow in unspent:
            balance += outflow.amount
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
        self, wallet, amount, subject, outflows=None, timestamp=None
    ):
        address = wallet.address
        balance = 0
        t = Transaction()
        if timestamp is not None:
            t.timestamp = dt_2_iso(timestamp)
        if outflows is not None:
            for outflow in outflows:
                balance += outflow[2]
                t.add_inflow(
                    Inflow(outflow_txid=outflow[0], outflow_idx=outflow[1])
                )
        if balance < amount:
            unspent = self.unspent_outflows(
                address, limit=amount - balance, filter_pending=True
            )
            for txid, index, outflow in unspent:
                balance += outflow.amount
                t.add_inflow(Inflow(outflow_txid=txid, outflow_idx=index))
        if balance < amount:
            raise InsufficientFundsError()
        t.add_outflow(Outflow(amount=amount, subject=subject))
        if balance - amount:
            t.add_outflow(Outflow(amount=balance - amount, address=address))
        t.set_wallet(wallet)
        t.seal()
        return t

    def create_forgive(self, wallet, amount, subject):
        address = wallet.address
        balance = 0
        t = Transaction()
        unforgiven = self.unforgiven_address_outflows(
            address, subject, limit=amount, filter_pending=True
        )
        for txid, index, outflow in unforgiven:
            balance += outflow.amount
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
        self, wallet, amount, subject, outflows=None, timestamp=None
    ):
        address = wallet.address
        balance = 0
        t = Transaction()
        if timestamp is not None:
            t.timestamp = dt_2_iso(timestamp)
        if outflows is not None:
            for outflow in outflows:
                balance += outflow[2]
                t.add_inflow(
                    Inflow(outflow_txid=outflow[0], outflow_idx=outflow[1])
                )
        if balance < amount:
            unspent = self.unspent_outflows(
                address, limit=amount - balance, filter_pending=True
            )
            for txid, index, outflow in unspent:
                balance += outflow.amount
                t.add_inflow(Inflow(outflow_txid=txid, outflow_idx=index))
        if balance < amount:
            raise InsufficientFundsError()
        t.add_outflow(Outflow(amount=amount, support=subject))
        if balance - amount:
            t.add_outflow(Outflow(amount=balance - amount, address=address))
        t.set_wallet(wallet)
        t.seal()
        return t

    def to_dict(self):
        return {'cid': self.cid, 'block_hash': self.block_hash}

    def to_json(self):
        return json.dumps(self.to_dict())

    def to_dao(self, create=False):
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

    def to_db(self):
        dao = self.to_dao(create=True)
        dao.commit()
        self.cid = dao.id

    def __lt__(self, other):
        return self.length < other.length

    @classmethod
    def from_dict(cls, d):
        cid = d.get('cid')
        block_hash = d.get('block_hash')
        chain = cls(cid=cid, block_hash=block_hash)
        return chain

    @classmethod
    def from_json(cls, j):
        return cls.from_dict(json.loads(j))

    @classmethod
    def from_dao(cls, dao):
        return cls(cid=dao.id, block_hash=dao.block_hash)

    @classmethod
    def from_db(cls, cid=None, block_hash=None):
        if cid:
            dao = ChainDAO.get(id=cid)
        else:
            dao = ChainDAO.get(block_hash=block_hash)
        return cls.from_dao(dao) if dao else None
