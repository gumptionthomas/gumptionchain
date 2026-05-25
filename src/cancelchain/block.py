from __future__ import annotations

# mypy: disable-error-code="no-untyped-call,no-any-return"
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from json import JSONDecodeError
from typing import Any, Self

from marshmallow import (
    ValidationError,
    fields,
    post_load,
    validate,
    validates_schema,
)
from pymerkle import InmemoryTree, InvalidProof, verify_inclusion

from cancelchain.exceptions import (
    ExpiredTransactionError,
    FutureTransactionError,
    InvalidBlockError,
    InvalidBlockHashError,
    InvalidCoinbaseError,
    InvalidMerkleRootError,
    InvalidProofError,
    InvalidTransactionError,
    MissingCoinbaseError,
    OutOfOrderTransactionError,
    SealedBlockError,
    UnlinkedBlockError,
)
from cancelchain.milling import mill_hash_str, milling_generator
from cancelchain.models import BlockDAO
from cancelchain.schema import (
    MillHash,
    SansNoneSchema,
    Timestamp,
    asdict_sans_none,
)
from cancelchain.transaction import Transaction, TransactionSchema
from cancelchain.util import dt_2_iso, iso_2_dt, now_iso
from cancelchain.wallet import Wallet

VERSION_1 = '1'
MAX_TRANSACTIONS = 100
TXN_TIMEOUT = timedelta(hours=4)
MISSED_TARGET_MSG = 'Missed target'


def validate_hash_diff(block_hash: str, target: str) -> bool:
    return int(block_hash, 16) < int(target, 16)


class BlockSchema(SansNoneSchema):
    idx = fields.Integer(required=True, validate=validate.Range(min=0))
    timestamp = Timestamp(required=True)
    block_hash = MillHash(required=True)
    prev_hash = MillHash(required=True)
    target = MillHash(required=True)
    proof_of_work = fields.Integer(
        required=True, validate=validate.Range(min=0)
    )
    merkle_root = MillHash(required=True)
    txns = fields.List(
        fields.Nested(TransactionSchema),
        required=True,
        validate=validate.Length(min=1, max=MAX_TRANSACTIONS),
    )
    version = fields.String(required=True, validate=validate.Equal(VERSION_1))

    @validates_schema
    def validate_difficulty(self, data: dict[str, Any], **kwargs: Any) -> None:
        block_hash: str = data.get('block_hash', '')
        target: str = data.get('target', '')
        if not validate_hash_diff(block_hash, target):
            raise ValidationError(MISSED_TARGET_MSG)

    @post_load
    def make_block(self, data: dict[str, Any], **kwargs: Any) -> Block:
        return Block(**data)


@dataclass(order=True)
class Block:
    idx: int | None = field(default=None)
    timestamp: str | None = field(default=None)
    block_hash: str | None = field(default=None)
    prev_hash: str | None = field(default=None, compare=False)
    target: str = field(default='F' * 64, compare=False, repr=False)
    proof_of_work: int | None = field(default=None, compare=False, repr=False)
    merkle_root: str | None = field(default=None, compare=False, repr=False)
    txns: list[Transaction] = field(default_factory=list, compare=False)
    version: str = field(default=VERSION_1, compare=False, repr=False)

    @property
    def timestamp_dt(self) -> datetime | None:
        return iso_2_dt(self.timestamp) if self.timestamp else None

    @property
    def last_txn(self) -> Transaction | None:
        return self.txns[-1] if self.txns else None

    @property
    def regular_txns(self) -> list[Transaction]:
        return self.txns[0:-1] if self.txns else []

    @property
    def coinbase(self) -> Transaction | None:
        return self.last_txn if self.is_sealed else None

    @property
    def schadenfreude(self) -> int:
        return sum([t.schadenfreude for t in self.txns])

    @property
    def grace(self) -> int:
        return sum([t.grace for t in self.txns])

    @property
    def mudita(self) -> int:
        return sum([t.mudita for t in self.txns])

    @property
    def is_sealed(self) -> bool:
        return self.timestamp is not None

    @property
    def is_proved(self) -> bool:
        if self.block_hash:
            return validate_hash_diff(self.block_hash, self.target)
        return False

    @property
    def unproven_header(self) -> str:
        return ','.join(
            (
                str(self.idx),
                str(self.timestamp),
                str(self.prev_hash),
                str(self.target),
                str(self.merkle_root),
                str(self.version),
                '',
            )
        )

    @property
    def header(self) -> str:
        return self.potential_header(self.proof_of_work)

    def get_header_hash(self) -> str:
        return mill_hash_str(self.header)

    def potential_header(self, proof_of_work: int | None) -> str:
        return f'{self.unproven_header}{proof_of_work}'

    def validate_proof_of_work(self, proof_of_work: int) -> bool:
        potential_header = self.potential_header(proof_of_work)
        return validate_hash_diff(mill_hash_str(potential_header), self.target)

    def build_merkle_tree(self) -> InmemoryTree:
        tree = InmemoryTree()
        for txn in self.txns:
            if txn.txid is not None:
                tree.append_entry(txn.txid.encode())
        return tree

    def get_merkle_root(self) -> str | None:
        root = self.build_merkle_tree().root
        return root.digest.hex() if root else None

    def in_merkle_tree(self, txid: str) -> bool:
        txids = [t.txid for t in self.txns]
        if txid not in txids:
            return False
        tree = self.build_merkle_tree()
        idx = txids.index(txid) + 1  # prove_inclusion uses 1-based index
        target = tree.root.digest
        proof = tree.prove_inclusion(idx)
        try:
            verify_inclusion(tree.get_leaf(idx), target, proof)
        except InvalidProof:
            return False
        else:
            return True

    def add_txn(self, txn: Transaction, is_coinbase: bool = False) -> None:  # noqa: FBT001
        if self.is_sealed:
            raise SealedBlockError()
        if not is_coinbase:
            self.validate_transaction(txn, prev_txn=self.last_txn)
        else:
            txn.validate_coinbase()
        self.txns.append(txn)

    def create_coinbase(self, wallet: Wallet, reward: int) -> Transaction:
        return Transaction.coinbase(
            wallet, reward, self.schadenfreude, self.grace, self.mudita
        )

    def add_coinbase(self, wallet: Wallet, reward: int) -> None:
        self.add_txn(self.create_coinbase(wallet, reward), is_coinbase=True)

    def link(self, idx: int, prev_hash: str, target: str) -> None:
        self.idx = idx
        self.prev_hash = prev_hash
        self.target = target

    def seal(self, wallet: Wallet, reward: int) -> None:
        if self.is_sealed:
            raise SealedBlockError()
        if (self.prev_hash is None) or (self.idx is None):
            raise UnlinkedBlockError()
        self.txns.sort()
        self.add_coinbase(wallet, reward)
        self.merkle_root = self.get_merkle_root()
        self.timestamp = now_iso()

    def mill(
        self,
        mp: bool = False,  # noqa: FBT001
        progress: Any = None,
    ) -> None:
        mg = milling_generator(
            self,  # type: ignore[arg-type]
            mp=mp,
            progress=progress,
        )
        while next(mg) is None:
            pass

    def solve(self, proof_of_work: int) -> None:
        if self.validate_proof_of_work(proof_of_work):
            self.proof_of_work = proof_of_work
            self.block_hash = self.get_header_hash()
        else:
            raise InvalidProofError()

    def validate_block_hash(self) -> None:
        if self.block_hash != self.get_header_hash():
            raise InvalidBlockHashError()

    def validate_merkle_root(self) -> None:
        if self.merkle_root != self.get_merkle_root():
            raise InvalidMerkleRootError()

    def validate_transaction(
        self,
        txn: Transaction,
        prev_txn: Transaction | None = None,
    ) -> None:
        txn.validate(coinbase=False)
        txn_ts_dt = txn.timestamp_dt
        if self.timestamp_dt and txn_ts_dt is not None:
            if txn_ts_dt > self.timestamp_dt:
                raise FutureTransactionError()
            if txn_ts_dt < self.timestamp_dt - TXN_TIMEOUT:
                raise ExpiredTransactionError()
        if prev_txn and txn < prev_txn:
            raise OutOfOrderTransactionError()

    def validate_coinbase(self) -> None:
        cb = self.coinbase
        if not cb:
            raise MissingCoinbaseError()
        cb.validate_coinbase()
        comps = []
        if self.schadenfreude:
            comps.append(self.schadenfreude)
        if self.grace:
            comps.append(self.grace)
        if self.mudita:
            comps.append(self.mudita)
        if comps != [o.amount for o in cb.outflows[1:]]:
            raise InvalidCoinbaseError()

    def validate(self) -> None:
        if errors := BlockSchema().validate(self.to_dict()):
            raise InvalidBlockError(errors)
        self.validate_block_hash()
        self.validate_merkle_root()
        prev_txn = None
        for txn in self.regular_txns:
            try:
                self.validate_transaction(txn, prev_txn=prev_txn)
            except InvalidTransactionError as e:
                raise InvalidBlockError({f'Transaction {txn.txid}': e.messages})
            prev_txn = txn
        try:
            self.validate_coinbase()
        except InvalidTransactionError as e:
            raise InvalidBlockError(e.messages)

    def to_dict(self) -> dict[str, Any]:
        return asdict_sans_none(self)

    def to_json(self) -> str:
        return BlockSchema().dumps(self.to_dict())

    def to_dao(self) -> BlockDAO:
        return BlockDAO.get(self.block_hash) or BlockDAO(
            self.block_hash,
            self.version,
            self.idx,
            self.prev_hash,
            self.timestamp_dt,
            self.merkle_root,
            self.proof_of_work,
            self.target,
            transaction_daos=[txn.to_dao() for txn in self.txns],
        )

    def to_db(self) -> None:
        self.to_dao().commit()

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Self:
        try:
            return BlockSchema().load(d)
        except ValidationError as e:
            raise InvalidBlockError(e.messages)

    @classmethod
    def from_json(cls, j: str) -> Self:
        try:
            return BlockSchema().loads(j)
        except JSONDecodeError as je:
            raise InvalidBlockError(je.msg)
        except ValidationError as ve:
            raise InvalidBlockError(ve.messages)

    @classmethod
    def from_dao(cls, dao: Any) -> Self:
        return cls(
            idx=dao.idx,
            timestamp=dt_2_iso(dao.timestamp),
            block_hash=dao.block_hash,
            prev_hash=dao.prev_hash,
            target=dao.target,
            proof_of_work=dao.proof_of_work,
            merkle_root=dao.merkle_root,
            txns=[
                Transaction.from_dao(txn_dao) for txn_dao in dao.transactions
            ],
            version=dao.version,
        )

    @classmethod
    def from_db(cls, block_hash: str) -> Self | None:
        dao = BlockDAO.get(block_hash)
        return cls.from_dao(dao) if dao else None
