from __future__ import annotations

# Flask-SQLAlchemy's `db.Model` is dynamically attached and shows up as
# `Any` to mypy strict, which triggers `name-defined` (Name "db.Model"
# is not defined) and `misc` (Class cannot subclass "Model" of type
# "Any") errors on every DAO class declaration here. Switching to a
# typed `DeclarativeBase` subclass would lose the `Model.query` API
# that this codebase still uses (Phase 6 modernizes those call sites
# to `db.session.execute(db.select(...))` style at which point this
# suppression can be removed).
# mypy: disable-error-code="no-untyped-call,no-any-return,name-defined,misc"
import datetime
import uuid
from collections.abc import Generator
from typing import TYPE_CHECKING

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from sqlalchemy import (
    CTE,
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cancelchain.database import db
from cancelchain.wallet import Wallet

if TYPE_CHECKING:
    from sqlalchemy.orm import Query

_PASSWORD_HASHER = PasswordHasher()


def rollback_session() -> None:
    db.session.rollback()


block_transactions = db.Table(
    'block_transaction',
    db.Column(
        'block_id', db.Integer, db.ForeignKey('block.id'), primary_key=True
    ),
    db.Column(
        'transaction_id',
        db.Integer,
        db.ForeignKey('transaction.id'),
        primary_key=True,
    ),
)


class TransactionDAO(db.Model):
    __tablename__ = 'transaction'

    id: Mapped[int] = mapped_column(
        Integer, autoincrement=True, primary_key=True
    )
    txid: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    version: Mapped[str] = mapped_column(String(10))
    timestamp: Mapped[datetime.datetime] = mapped_column(DateTime)
    address: Mapped[str | None] = mapped_column(String(100))
    public_key: Mapped[str | None] = mapped_column(String(500))
    signature: Mapped[str | None] = mapped_column(String(500))
    blocks: Mapped[list[BlockDAO]] = relationship(
        secondary=block_transactions, back_populates='transactions'
    )
    outflows: Mapped[list[OutflowDAO]] = relationship(
        back_populates='transaction', order_by='OutflowDAO.idx'
    )
    inflows: Mapped[list[InflowDAO]] = relationship(
        back_populates='transaction', order_by='InflowDAO.idx'
    )

    def __init__(
        self,
        txid: str,
        version: str,
        timestamp: datetime.datetime,
        address: str | None = None,
        public_key: str | None = None,
        signature: str | None = None,
        inflow_daos: list[InflowDAO] | None = None,
        outflow_daos: list[OutflowDAO] | None = None,
    ) -> None:
        self.txid = txid
        self.version = version
        self.timestamp = timestamp
        self.address = address
        self.public_key = public_key
        self.signature = signature
        for inflow_dao in inflow_daos or []:
            inflow_dao.transaction = self
        for outflow_dao in outflow_daos or []:
            outflow_dao.transaction = self

    def commit(self) -> None:
        db.session.add(self)
        db.session.commit()

    @classmethod
    def get(cls, txid: str) -> TransactionDAO | None:
        return cls.query.filter_by(txid=txid).one_or_none()

    @classmethod
    def transactions_chain(
        cls, block_chain: Query[BlockDAO]
    ) -> Query[TransactionDAO]:
        block_alias = db.aliased(BlockDAO, block_chain.subquery())
        q = db.session.query(TransactionDAO)
        q = q.join(block_alias, TransactionDAO.blocks)
        return q.order_by(TransactionDAO.timestamp.desc(), TransactionDAO.id)


class OutflowDAO(db.Model):
    __tablename__ = 'outflow'

    id: Mapped[int] = mapped_column(
        Integer, autoincrement=True, primary_key=True
    )
    txid: Mapped[str] = mapped_column(String(100))
    idx: Mapped[int] = mapped_column(Integer)
    amount: Mapped[int] = mapped_column(BigInteger)
    address: Mapped[str | None] = mapped_column(String(100))
    subject: Mapped[str | None] = mapped_column(String(500))
    forgive: Mapped[str | None] = mapped_column(String(500))
    support: Mapped[str | None] = mapped_column(String(500))
    transaction_id: Mapped[int] = mapped_column(
        Integer, ForeignKey('transaction.id')
    )
    transaction: Mapped[TransactionDAO] = relationship(
        back_populates='outflows'
    )
    inflows: Mapped[list[InflowDAO]] = relationship(back_populates='outflow')
    pending: Mapped[list[PendingIOflowDAO]] = relationship(
        back_populates='outflow'
    )
    __table_args__ = (
        db.UniqueConstraint('txid', 'idx'),
        db.Index('ix_outflow_txid_idx', 'txid', 'idx'),
    )

    def __init__(
        self,
        txid: str,
        idx: int,
        amount: int,
        address: str | None = None,
        subject: str | None = None,
        forgive: str | None = None,
        support: str | None = None,
        transaction_dao: TransactionDAO | None = None,
    ) -> None:
        with db.session.no_autoflush:
            self.txid = txid
            self.idx = idx
            self.amount = amount
            self.address = address
            self.subject = subject
            self.forgive = forgive
            self.support = support
            self.transaction = transaction_dao or None  # type: ignore[assignment]

    @classmethod
    def get(cls, outflow_txid: str, outflow_idx: int) -> OutflowDAO | None:
        return cls.query.filter_by(
            outflow_txid=outflow_txid, outflow_idx=outflow_idx
        ).one_or_none()

    @classmethod
    def outflows_chain(
        cls, transactions_chain: Query[TransactionDAO]
    ) -> Query[OutflowDAO]:
        txn_alias = db.aliased(TransactionDAO, transactions_chain.subquery())
        q = db.session.query(OutflowDAO)
        q = q.join(txn_alias, OutflowDAO.transaction)
        q = q.order_by(
            txn_alias.timestamp.desc(), txn_alias.txid, OutflowDAO.idx
        )
        return q


class InflowDAO(db.Model):
    __tablename__ = 'inflow'

    id: Mapped[int] = mapped_column(
        Integer, autoincrement=True, primary_key=True
    )
    txid: Mapped[str] = mapped_column(String(100))
    idx: Mapped[int] = mapped_column(Integer)
    outflow_txid: Mapped[str] = mapped_column(String(100))
    outflow_idx: Mapped[int] = mapped_column(Integer)
    outflow_id: Mapped[int] = mapped_column(Integer, ForeignKey('outflow.id'))
    outflow: Mapped[OutflowDAO] = relationship(back_populates='inflows')
    transaction_id: Mapped[int] = mapped_column(
        Integer, ForeignKey('transaction.id')
    )
    transaction: Mapped[TransactionDAO] = relationship(back_populates='inflows')

    __table_args__ = (
        db.UniqueConstraint('txid', 'idx'),
        db.Index('ix_inflow_txid_idx', 'txid', 'idx'),
    )

    def __init__(
        self,
        txid: str,
        idx: int,
        outflow_txid: str,
        outflow_idx: int,
        outflow_dao: OutflowDAO | None = None,
        transaction_dao: TransactionDAO | None = None,
    ) -> None:
        with db.session.no_autoflush:
            self.txid = txid
            self.idx = idx
            self.outflow_txid = outflow_txid
            self.outflow_idx = outflow_idx
            if not outflow_dao:
                outflow_dao = OutflowDAO.query.filter_by(
                    txid=outflow_txid, idx=outflow_idx
                ).one_or_none()
            self.outflow = outflow_dao
            self.transaction = transaction_dao  # type: ignore[assignment]

    @classmethod
    def inflows_chain(
        cls, transactions_chain: Query[TransactionDAO]
    ) -> Query[InflowDAO]:
        txn_alias = db.aliased(TransactionDAO, transactions_chain.subquery())
        q = db.session.query(InflowDAO)
        q = q.join(txn_alias, InflowDAO.transaction)
        q = q.order_by(
            txn_alias.timestamp.desc(), txn_alias.txid, InflowDAO.idx
        )
        return q


class BlockDAO(db.Model):
    __tablename__ = 'block'

    id: Mapped[int] = mapped_column(
        Integer, autoincrement=True, primary_key=True
    )
    block_hash: Mapped[str] = mapped_column(
        String(100), unique=True, index=True
    )
    version: Mapped[str] = mapped_column(String(10))
    idx: Mapped[int] = mapped_column(Integer, index=True)
    prev_hash: Mapped[str] = mapped_column(String(100), index=True)
    timestamp: Mapped[datetime.datetime] = mapped_column(DateTime)
    merkle_root: Mapped[str] = mapped_column(String(100))
    proof_of_work: Mapped[int] = mapped_column(BigInteger)
    target: Mapped[str] = mapped_column(String(100))
    prev_id: Mapped[int | None] = mapped_column(Integer, ForeignKey('block.id'))
    prev: Mapped[BlockDAO | None] = relationship(
        'BlockDAO', remote_side='BlockDAO.id', back_populates='next'
    )
    next: Mapped[list[BlockDAO]] = relationship(
        'BlockDAO', back_populates='prev'
    )
    transactions: Mapped[list[TransactionDAO]] = relationship(
        secondary=block_transactions,
        back_populates='blocks',
        lazy='dynamic',
        order_by=[TransactionDAO.timestamp, TransactionDAO.txid],
    )
    chains: Mapped[list[ChainDAO]] = relationship(back_populates='block')

    def __init__(
        self,
        block_hash: str,
        version: str,
        idx: int,
        prev_hash: str,
        timestamp: datetime.datetime,
        merkle_root: str,
        proof_of_work: int,
        target: str,
        prev_dao: BlockDAO | None = None,
        transaction_daos: list[TransactionDAO] | None = None,
    ) -> None:
        self.block_hash = block_hash
        self.version = version
        self.idx = idx
        self.prev_hash = prev_hash
        self.timestamp = timestamp
        self.merkle_root = merkle_root
        self.proof_of_work = proof_of_work
        self.target = target
        self.prev = prev_dao or BlockDAO.get(prev_hash)
        for transaction_dao in transaction_daos or []:
            self.transactions.append(transaction_dao)

    @property
    def _block_chain(self) -> CTE:
        q = BlockDAO.query.filter(BlockDAO.id == self.id).cte(recursive=True)
        return q.union_all(BlockDAO.query.filter(BlockDAO.id == q.c.prev_id))

    @property
    def block_chain(self) -> Query[BlockDAO]:
        return db.session.query(self._block_chain)

    @property
    def transactions_chain(self) -> Query[TransactionDAO]:
        return TransactionDAO.transactions_chain(self.block_chain)

    @property
    def outflows_chain(self) -> Query[OutflowDAO]:
        return OutflowDAO.outflows_chain(self.transactions_chain)

    @property
    def inflows_chain(self) -> Query[InflowDAO]:
        return InflowDAO.inflows_chain(self.transactions_chain)

    def commit(self) -> None:
        db.session.add(self)
        db.session.commit()

    def get_transaction_in_chain(self, txid: str) -> TransactionDAO | None:
        return self.transactions_chain.filter(
            TransactionDAO.txid == txid
        ).one_or_none()

    def address_transactions(self, address: str) -> Query[TransactionDAO]:
        return self.transactions_chain.filter(TransactionDAO.address == address)

    def get_block_in_chain(
        self, block_hash: str | None = None, idx: int | None = None
    ) -> BlockDAO | None:
        block_alias = db.aliased(BlockDAO, self.block_chain.subquery())
        q = db.session.query(BlockDAO)
        q = q.join(block_alias, BlockDAO.id == block_alias.id)
        if block_hash is not None:
            q = q.filter(BlockDAO.block_hash == block_hash)
        if idx is not None:
            q = q.filter(BlockDAO.idx == idx)
        return q.one_or_none()

    def inflows_in_chain_count(
        self, outflow_txid: str, outflow_idx: int
    ) -> int:
        return (
            1
            if self.inflows_chain.filter(
                InflowDAO.outflow_txid == outflow_txid,
                InflowDAO.outflow_idx == outflow_idx,
            ).first()
            is not None
            else 0
        )

    @classmethod
    def count(cls) -> int:
        result = db.session.query(db.func.count(cls.id)).one_or_none()
        return result[0] if result is not None else 0

    @classmethod
    def block_hashes(cls) -> Generator[str, None, None]:
        for r in cls.query.with_entities(cls.block_hash).order_by(
            cls.timestamp.desc(), cls.block_hash
        ):
            yield r[0]

    @classmethod
    def get(
        cls, block_hash: str | None = None, idx: int | None = None
    ) -> BlockDAO | None:
        q = cls.query
        if block_hash:
            q = q.filter_by(block_hash=block_hash)
        else:
            q = q.filter_by(idx=idx)
        return q.one_or_none()


class ChainDAO(db.Model):
    __tablename__ = 'chain'

    id: Mapped[int] = mapped_column(
        Integer, autoincrement=True, primary_key=True
    )
    block_hash: Mapped[str] = mapped_column(
        String(100), unique=True, index=True
    )
    block_id: Mapped[int] = mapped_column(
        Integer, ForeignKey('block.id'), index=True
    )
    block: Mapped[BlockDAO] = relationship(back_populates='chains')

    def __init__(
        self, block_hash: str, block_dao: BlockDAO | None = None
    ) -> None:
        self.block_hash = block_hash
        self.block = block_dao or BlockDAO.get(block_hash)  # type: ignore[assignment]

    @property
    def blocks(self) -> Query[BlockDAO]:
        return self.block.block_chain

    @property
    def transactions(self) -> Query[TransactionDAO]:
        return self.block.transactions_chain

    @property
    def outflows(self) -> Query[OutflowDAO]:
        return self.block.outflows_chain

    @property
    def inflows(self) -> Query[InflowDAO]:
        return self.block.inflows_chain

    def unspent_outflows(
        self,
        address: str,
        filter_pending: bool = False,  # noqa: FBT001
    ) -> Query[OutflowDAO]:
        inflows_alias = db.aliased(InflowDAO, self.inflows.subquery())
        q = self.outflows.filter(OutflowDAO.address == address)
        q = q.join(inflows_alias, OutflowDAO.inflows, isouter=True)
        q = q.filter(inflows_alias.id.is_(None))
        if filter_pending:
            q = q.filter(~OutflowDAO.pending.any())
        return q

    def wallet_balance(self, address: str) -> int:
        inflows_alias = db.aliased(InflowDAO, self.inflows.subquery())
        q = self.outflows.filter(OutflowDAO.address == address)
        q = q.join(inflows_alias, OutflowDAO.inflows, isouter=True)
        q = q.filter(inflows_alias.id.is_(None))
        outflows_alias = db.aliased(OutflowDAO, q.subquery())
        q2 = db.session.query(db.func.sum(OutflowDAO.amount)).join(
            outflows_alias, OutflowDAO.id == outflows_alias.id
        )
        amount = q2.one_or_none()
        return (amount[0] or 0) if amount is not None else 0

    def unforgiven_outflows(
        self,
        subject: str,
        address: str | None = None,
        filter_pending: bool = False,  # noqa: FBT001
    ) -> Query[OutflowDAO]:
        inflows_alias = db.aliased(InflowDAO, self.inflows.subquery())
        q = self.outflows.filter(OutflowDAO.subject == subject)
        q = q.join(inflows_alias, OutflowDAO.inflows, isouter=True)
        q = q.filter(inflows_alias.id.is_(None))
        if address is not None:
            txn_alias = db.aliased(TransactionDAO, self.transactions.subquery())
            q = q.join(txn_alias, OutflowDAO.transaction)
            q = q.filter(txn_alias.address == address)
        if filter_pending:
            q = q.filter(~OutflowDAO.pending.any())
        return q

    def subject_balance(self, subject: str) -> int:
        inflows_alias = db.aliased(InflowDAO, self.inflows.subquery())
        q = self.outflows.filter(OutflowDAO.subject == subject)
        q = q.join(inflows_alias, OutflowDAO.inflows, isouter=True)
        q = q.filter(inflows_alias.id.is_(None))
        outflows_alias = db.aliased(OutflowDAO, q.subquery())
        q2 = db.session.query(db.func.sum(OutflowDAO.amount)).join(
            outflows_alias, OutflowDAO.id == outflows_alias.id
        )
        amount = q2.one_or_none()
        return (amount[0] or 0) if amount is not None else 0

    def subject_support(self, subject: str) -> int:
        q = self.outflows.filter(OutflowDAO.support == subject)
        outflows_alias = db.aliased(OutflowDAO, q.subquery())
        q2 = db.session.query(db.func.sum(OutflowDAO.amount)).join(
            outflows_alias, OutflowDAO.id == outflows_alias.id
        )
        amount = q2.one_or_none()
        return (amount[0] or 0) if amount is not None else 0

    def wallet_leaderboard(
        self,
        earliest: datetime.datetime | None = None,
        latest: datetime.datetime | None = None,
        limit: int | None = None,
    ) -> Query[OutflowDAO]:
        inflows_alias = db.aliased(InflowDAO, self.inflows.subquery())
        txn_alias = db.aliased(TransactionDAO, self.transactions.subquery())
        q = db.session.query(
            OutflowDAO.address, db.func.sum(OutflowDAO.amount).label('ct')
        )
        q = q.filter(OutflowDAO.address.is_not(None))
        q = q.join(txn_alias, OutflowDAO.transaction)
        q = q.join(inflows_alias, OutflowDAO.inflows, isouter=True)
        q = q.filter(inflows_alias.id.is_(None))
        if earliest is not None:
            q = q.filter(txn_alias.timestamp >= earliest)
        if latest is not None:
            q = q.filter(txn_alias.timestamp < latest)
        q = q.group_by(OutflowDAO.address)
        q = q.order_by(db.desc('ct'), OutflowDAO.address)
        if limit is not None:
            q = q.limit(limit)
            return db.session.query(db.aliased(q.subquery()))
        return q

    def set_block_hash(self, block_hash: str) -> None:
        self.block = BlockDAO.get(block_hash)  # type: ignore[assignment]
        self.block_hash = block_hash

    def get_block(
        self, block_hash: str | None = None, idx: int | None = None
    ) -> BlockDAO | None:
        return self.block.get_block_in_chain(block_hash=block_hash, idx=idx)

    def next_block(self, block: BlockDAO) -> BlockDAO | None:
        for next_block in block.next:
            if self.get_block(block_hash=next_block.block_hash) is not None:
                return next_block
        return None

    def get_transaction(self, txid: str) -> TransactionDAO | None:
        return self.block.get_transaction_in_chain(txid)

    def address_transactions(self, address: str) -> Query[TransactionDAO]:
        return self.block.address_transactions(address)

    def commit(self) -> None:
        db.session.add(self)
        db.session.commit()

    @classmethod
    def count(cls) -> int:
        result = db.session.query(db.func.count(cls.id)).one_or_none()
        return result[0] if result is not None else 0

    @classmethod
    def get(
        cls, block_hash: str | None = None, id: int | None = None
    ) -> ChainDAO | None:
        q = cls.query
        if block_hash:
            q = q.filter_by(block_hash=block_hash)
        else:
            q = q.filter_by(id=id)
        return q.one_or_none()

    @classmethod
    def ids(cls) -> Generator[int, None, None]:
        for r in cls.query.with_entities(cls.id).order_by(cls.id):
            yield r[0]

    @classmethod
    def chains(cls) -> Query[ChainDAO]:
        return cls.query.join(cls.block).order_by(
            BlockDAO.idx.desc(), BlockDAO.timestamp, BlockDAO.block_hash
        )

    @classmethod
    def longest(cls) -> ChainDAO | None:
        return cls.chains().first()


class PendingTxnDAO(db.Model):
    __tablename__ = 'pending_txn'

    id: Mapped[int] = mapped_column(
        Integer, autoincrement=True, primary_key=True
    )
    txid: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    timestamp: Mapped[datetime.datetime] = mapped_column(DateTime)
    json_data: Mapped[str] = mapped_column(Text)
    received: Mapped[datetime.datetime | None] = mapped_column(
        DateTime, default=datetime.datetime.utcnow
    )
    ioflows: Mapped[list[PendingIOflowDAO]] = relationship(
        back_populates='pending_txn', cascade='delete, delete-orphan'
    )

    def add(self) -> None:
        db.session.add(self)

    def commit(self) -> None:
        self.add()
        db.session.commit()

    def delete(self) -> None:
        db.session.delete(self)
        db.session.commit()

    @classmethod
    def count(cls) -> int:
        result = db.session.query(db.func.count(cls.id)).one_or_none()
        return result[0] if result is not None else 0

    @classmethod
    def json_datas(
        cls,
        earliest: datetime.datetime | None = None,
        expired: datetime.datetime | None = None,
    ) -> Generator[str, None, None]:
        q = cls.query.with_entities(cls.json_data)
        if earliest is not None:
            q = q.filter(cls.received >= earliest)
        if expired is not None:
            q = q.filter(cls.timestamp >= expired)
        q = q.order_by(cls.timestamp, cls.txid)
        for r in q:
            yield r[0]

    @classmethod
    def get(cls, txid: str) -> PendingTxnDAO | None:
        return cls.query.filter_by(txid=txid).one_or_none()


class PendingIOflowDAO(db.Model):
    __tablename__ = 'pending_ioflow'

    id: Mapped[int] = mapped_column(
        Integer, autoincrement=True, primary_key=True
    )
    txid: Mapped[str] = mapped_column(String(100))
    outflow_txid: Mapped[str] = mapped_column(String(100))
    outflow_idx: Mapped[int] = mapped_column(Integer)
    pending_txn_id: Mapped[int] = mapped_column(
        Integer, ForeignKey('pending_txn.id')
    )
    pending_txn: Mapped[PendingTxnDAO] = relationship(back_populates='ioflows')
    outflow_id: Mapped[int] = mapped_column(Integer, ForeignKey('outflow.id'))
    outflow: Mapped[OutflowDAO] = relationship(back_populates='pending')

    def add(self) -> None:
        db.session.add(self)

    def commit(self) -> None:
        self.add()
        db.session.commit()


class ChainFill(db.Model):
    __tablename__ = 'chain_fill'

    id: Mapped[int] = mapped_column(
        Integer, autoincrement=True, primary_key=True
    )
    blocks: Mapped[list[ChainFillBlock]] = relationship(
        back_populates='chain_fill',
        order_by='ChainFillBlock.idx',
        cascade='delete, delete-orphan',
    )

    def add(self) -> None:
        db.session.add(self)

    def commit(self) -> None:
        self.add()
        db.session.commit()

    def delete(self) -> None:
        db.session.delete(self)
        db.session.commit()


class ChainFillBlock(db.Model):
    __tablename__ = 'chain_fill_block'

    id: Mapped[int] = mapped_column(
        Integer, autoincrement=True, primary_key=True
    )
    block_hash: Mapped[str] = mapped_column(String(100))
    idx: Mapped[int] = mapped_column(Integer)
    block_json: Mapped[str | None] = mapped_column(Text)
    chain_fill_id: Mapped[int] = mapped_column(
        Integer, ForeignKey('chain_fill.id')
    )
    chain_fill: Mapped[ChainFill] = relationship(back_populates='blocks')

    def add(self) -> None:
        db.session.add(self)

    def commit(self) -> None:
        self.add()
        db.session.commit()


class ApiToken(db.Model):
    __tablename__ = 'api_token'

    id: Mapped[int] = mapped_column(
        Integer, autoincrement=True, primary_key=True
    )
    address: Mapped[str] = mapped_column(String(100), unique=True, index=True)
    public_key: Mapped[str] = mapped_column(String(500))
    hashed: Mapped[str | None] = mapped_column(String(100), unique=True)
    cipher: Mapped[str | None] = mapped_column(String(500), unique=True)
    timestamp: Mapped[datetime.datetime | None] = mapped_column(
        DateTime,
        default=datetime.datetime.utcnow,
        onupdate=datetime.datetime.utcnow,
    )

    @property
    def expired(self) -> bool:
        if self.timestamp is None:
            return True
        now_dt = datetime.datetime.now(datetime.UTC)
        now_dt = now_dt.replace(tzinfo=None)
        return self.timestamp < (now_dt - datetime.timedelta(seconds=60))

    def add(self) -> None:
        db.session.add(self)

    def commit(self) -> None:
        self.add()
        db.session.commit()

    def refreshed_cipher(self) -> str | None:
        if self.expired or not (self.cipher and self.hashed):
            secret = str(uuid.uuid4())
            self.hashed = _PASSWORD_HASHER.hash(secret)
            wallet = Wallet(b64ks=self.public_key)
            self.cipher = wallet.encrypt(secret.encode())
            self.commit()
        return self.cipher

    def reset(self) -> None:
        self.cipher = None
        self.hashed = None
        self.commit()

    def verify(self, secret: object) -> bool:
        if self.expired or not self.hashed or not isinstance(secret, str):
            return False
        try:
            return _PASSWORD_HASHER.verify(self.hashed, secret)
        except (VerifyMismatchError, InvalidHashError):
            return False

    @classmethod
    def get(cls, address: str) -> ApiToken | None:
        return cls.query.filter_by(address=address).one_or_none()

    @classmethod
    def create(cls, wallet: Wallet) -> ApiToken:
        api_token = cls(
            address=wallet.address, public_key=wallet.public_key_b64
        )
        api_token.commit()
        return api_token
