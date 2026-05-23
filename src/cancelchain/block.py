from dataclasses import dataclass, field
from datetime import timedelta
from json import JSONDecodeError

from marshmallow import (
    ValidationError,
    fields,
    post_load,
    validate,
    validates_schema,
)
from pymerkle import MerkleTree, verify_inclusion
from pymerkle.proof import InvalidProof

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

VERSION_1 = '1'
MAX_TRANSACTIONS = 100
TXN_TIMEOUT = timedelta(hours=4)
MISSED_TARGET_MSG = 'Missed target'


def validate_hash_diff(block_hash, target):
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
    def validate_difficulty(self, data, **kwargs):
        if not validate_hash_diff(data.get('block_hash'), data.get('target')):
            raise ValidationError(MISSED_TARGET_MSG)

    @post_load
    def make_block(self, data, **kwargs):
        return Block(**data)


@dataclass(order=True)
class Block:
    idx: int = field(default=None)
    timestamp: str = field(default=None)
    block_hash: str = field(default=None)
    prev_hash: str = field(default=None, compare=False)
    target: str = field(default='F' * 64, compare=False, repr=False)
    proof_of_work: int = field(default=None, compare=False, repr=False)
    merkle_root: str = field(default=None, compare=False, repr=False)
    txns: list[Transaction] = field(default_factory=list, compare=False)
    version: str = field(default=VERSION_1, compare=False, repr=False)

    @property
    def timestamp_dt(self):
        return iso_2_dt(self.timestamp) if self.timestamp else None

    @property
    def last_txn(self):
        return self.txns[-1] if self.txns else None

    @property
    def regular_txns(self):
        return self.txns[0:-1] if self.txns else []

    @property
    def coinbase(self):
        return self.last_txn if self.is_sealed else None

    @property
    def schadenfreude(self):
        return sum([t.schadenfreude for t in self.txns])

    @property
    def grace(self):
        return sum([t.grace for t in self.txns])

    @property
    def mudita(self):
        return sum([t.mudita for t in self.txns])

    @property
    def is_sealed(self):
        return self.timestamp is not None

    @property
    def is_proved(self):
        if self.block_hash:
            return validate_hash_diff(self.block_hash, self.target)
        return False

    @property
    def unproven_header(self):
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
    def header(self):
        return self.potential_header(self.proof_of_work)

    def get_header_hash(self):
        return mill_hash_str(self.header)

    def potential_header(self, proof_of_work):
        return f'{self.unproven_header}{proof_of_work}'

    def validate_proof_of_work(self, proof_of_work):
        potential_header = self.potential_header(proof_of_work)
        return validate_hash_diff(mill_hash_str(potential_header), self.target)

    def build_merkle_tree(self):
        tree = MerkleTree()
        for record in (t.txid for t in self.txns):
            tree.append_entry(record)
        return tree

    def get_merkle_root(self):
        root_hash = self.build_merkle_tree().root
        return root_hash.decode() if root_hash else None

    def in_merkle_tree(self, txid):
        tree = self.build_merkle_tree()
        target = tree.root
        proof = tree.prove_inclusion(txid)
        try:
            verify_inclusion(txid, target, proof)
        except InvalidProof:
            return False
        else:
            return True

    def add_txn(self, txn, is_coinbase=False):
        if self.is_sealed:
            raise SealedBlockError()
        if not is_coinbase:
            self.validate_transaction(txn, prev_txn=self.last_txn)
        else:
            txn.validate_coinbase()
        self.txns.append(txn)

    def create_coinbase(self, wallet, reward):
        return Transaction.coinbase(
            wallet, reward, self.schadenfreude, self.grace, self.mudita
        )

    def add_coinbase(self, wallet, reward):
        self.add_txn(self.create_coinbase(wallet, reward), is_coinbase=True)

    def link(self, idx, prev_hash, target):
        self.idx = idx
        self.prev_hash = prev_hash
        self.target = target

    def seal(self, wallet, reward):
        if self.is_sealed:
            raise SealedBlockError()
        if (self.prev_hash is None) or (self.idx is None):
            raise UnlinkedBlockError()
        self.txns.sort()
        self.add_coinbase(wallet, reward)
        self.merkle_root = self.get_merkle_root()
        self.timestamp = now_iso()

    def mill(self, mp=False, progress=None):
        mg = milling_generator(self, mp=mp, progress=progress)
        while next(mg) is None:
            pass

    def solve(self, proof_of_work):
        if self.validate_proof_of_work(proof_of_work):
            self.proof_of_work = proof_of_work
            self.block_hash = self.get_header_hash()
        else:
            raise InvalidProofError()

    def validate_block_hash(self):
        if self.block_hash != self.get_header_hash():
            raise InvalidBlockHashError()

    def validate_merkle_root(self):
        if self.merkle_root != self.get_merkle_root():
            raise InvalidMerkleRootError()

    def validate_transaction(self, txn, prev_txn=None):
        txn.validate(coinbase=False)
        txn_ts_dt = txn.timestamp_dt
        if self.timestamp_dt and txn_ts_dt > self.timestamp_dt:
            raise FutureTransactionError()
        if self.timestamp_dt and txn_ts_dt < self.timestamp_dt - TXN_TIMEOUT:
            raise ExpiredTransactionError()
        if prev_txn and txn < prev_txn:
            raise OutOfOrderTransactionError()

    def validate_coinbase(self):
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

    def validate(self):
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

    def to_dict(self):
        return asdict_sans_none(self)

    def to_json(self):
        return BlockSchema().dumps(self.to_dict())

    def to_dao(self):
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

    def to_db(self):
        self.to_dao().commit()

    @classmethod
    def from_dict(cls, d):
        try:
            return BlockSchema().load(d)
        except ValidationError as e:
            raise InvalidBlockError(e.messages)

    @classmethod
    def from_json(cls, j):
        try:
            return BlockSchema().loads(j)
        except JSONDecodeError as je:
            raise InvalidBlockError(je.msg)
        except ValidationError as ve:
            raise InvalidBlockError(ve.messages)

    @classmethod
    def from_dao(cls, dao):
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
    def from_db(cls, block_hash):
        dao = BlockDAO.get(block_hash)
        return cls.from_dao(dao) if dao else None
