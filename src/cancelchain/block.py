from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from json import JSONDecodeError
from typing import Annotated, Any, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
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
    MillHashType,
    TimestampType,
    asdict_sans_none,
    pydantic_errors_to_messages,
)
from cancelchain.transaction import (
    Transaction,
    TransactionModel,
    txn_from_model_data,
)
from cancelchain.util import dt_2_iso, iso_2_dt, now_iso
from cancelchain.wallet import Wallet

VERSION_1 = '1'
MAX_TRANSACTIONS = 100
TXN_TIMEOUT = timedelta(hours=4)
MISSED_TARGET_MSG = 'Missed target'


def expiry_cutoff(reference_dt: datetime) -> datetime:
    """The expiry boundary datetime relative to reference_dt: a pending
    txn is expired iff its timestamp is strictly older than this cutoff
    (timestamp < cutoff). This is the SQL-filterable form of the
    `txn_is_expired` rule — callers pass `expiry_cutoff(now())` as the
    `expired` cutoff to PendingTxnDAO.json_datas / delete_expired, which
    keep `timestamp >= cutoff` so the boundary txn stays alive.
    """
    return reference_dt - TXN_TIMEOUT


def txn_is_expired(txn_timestamp_dt: datetime, reference_dt: datetime) -> bool:
    """A txn is expired iff its timestamp is strictly older than
    TXN_TIMEOUT relative to reference_dt. Open boundary: a txn exactly
    TXN_TIMEOUT old (txn_timestamp_dt == reference_dt - TXN_TIMEOUT) is
    NOT expired. Single source of truth for the expiry boundary — every
    other site (Node.discard_expired_pending_txns, Miller.pending_chain_txns,
    and the PendingTxnDAO.json_datas SQL query) applies this same rule
    via `expiry_cutoff`.
    """
    return txn_timestamp_dt < expiry_cutoff(reference_dt)


def validate_hash_diff(block_hash: str, target: str) -> bool:
    return int(block_hash, 16) < int(target, 16)


def _block_from_model_data(data: dict[str, Any]) -> dict[str, Any]:
    """Convert a BlockModel.model_dump() dict's txns list from list[dict]
    to list[Transaction] (with nested Inflow/Outflow instances already
    reconstructed via txn_from_model_data) before passing to the Block
    dataclass constructor.
    """
    return {
        **data,
        'txns': [
            Transaction(**txn_from_model_data(t)) for t in data.get('txns', [])
        ],
    }


class BlockModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

    idx: int = Field(ge=0)
    timestamp: TimestampType
    block_hash: MillHashType
    prev_hash: MillHashType
    target: MillHashType
    proof_of_work: int = Field(ge=0)
    merkle_root: MillHashType
    txns: Annotated[
        list[TransactionModel],
        Field(min_length=1, max_length=MAX_TRANSACTIONS),
    ]
    version: Literal['1']

    @model_validator(mode='after')
    def validate_difficulty(self) -> Self:
        if not validate_hash_diff(self.block_hash, self.target):
            raise ValueError(MISSED_TARGET_MSG)
        return self


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
        if self.prev_hash is None:
            raise UnlinkedBlockError()
        return Transaction.coinbase(
            wallet,
            reward,
            self.schadenfreude,
            self.grace,
            self.mudita,
            prev_hash=self.prev_hash,
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
            if txn_is_expired(txn_ts_dt, self.timestamp_dt):
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
        try:
            BlockModel.model_validate(self.to_dict())
        except ValidationError as e:
            raise InvalidBlockError(pydantic_errors_to_messages(e)) from e
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
        return json.dumps(self.to_dict())

    def to_dao(self) -> BlockDAO:
        # to_dao() is only meaningful after the block is sealed: all
        # identity fields (hash, idx, prev_hash, timestamp, merkle_root,
        # proof_of_work) are set. The dataclass declares them Optional
        # to allow staged construction; enforce them here so mypy strict
        # can narrow at the domain↔DAO boundary.
        if (
            self.block_hash is None
            or self.idx is None
            or self.prev_hash is None
            or self.timestamp_dt is None
            or self.merkle_root is None
            or self.proof_of_work is None
        ):
            msg = 'Block missing identity fields; cannot persist'
            raise InvalidBlockError(msg)
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

    def to_db(self, *, commit: bool = True) -> None:
        self.to_dao().commit(commit=commit)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Self:
        try:
            model = BlockModel.model_validate(d)
        except ValidationError as e:
            raise InvalidBlockError(pydantic_errors_to_messages(e)) from e
        return cls(**_block_from_model_data(model.model_dump()))

    @classmethod
    def from_json(cls, j: str | bytes) -> Self:
        try:
            model = BlockModel.model_validate_json(j)
        except ValidationError as e:
            raise InvalidBlockError(pydantic_errors_to_messages(e)) from e
        except JSONDecodeError as e:
            raise InvalidBlockError(e.msg) from e
        return cls(**_block_from_model_data(model.model_dump()))

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

    @classmethod
    def genesis_from_db(cls) -> Self | None:
        # The canonical-genesis check in Chain.validate_block keeps at most
        # one idx==0 row in the DB, so BlockDAO.get(idx=0)'s
        # scalar_one_or_none() is safe. A multi-genesis DB is only reachable
        # via pre-fix corruption (no such installs exist); if one ever did,
        # MultipleResultsFound surfaces loudly rather than masking the
        # corruption. Keyed on idx (not GENESIS_HASH) to avoid a
        # chain.py -> block.py circular import.
        dao = BlockDAO.get(idx=0)
        return cls.from_dao(dao) if dao else None
