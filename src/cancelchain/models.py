from __future__ import annotations

import datetime
import uuid
from collections.abc import Generator
from typing import Any, ClassVar

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from sqlalchemy import (
    CTE,
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    Select,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from cancelchain.database import Base, db
from cancelchain.wallet import Wallet

# Chain-factory returns below carry `# type: ignore[no-any-return]` because
# Flask-SQLAlchemy's `db.select` / `db.aliased` / `db.desc` facade methods
# return `Any`, even though the underlying `sqlalchemy` primitives are typed.
# Remove the ignores when FSA's stubs improve, or when these sites migrate
# to direct `from sqlalchemy import select, desc` / `from sqlalchemy.orm
# import aliased` imports.

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


class TransactionDAO(Base):
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
        return db.session.execute(
            db.select(cls).filter_by(txid=txid)
        ).scalar_one_or_none()

    @classmethod
    def transactions_chain(
        cls, block_chain: Select[tuple[BlockDAO]]
    ) -> Select[tuple[TransactionDAO]]:
        block_alias = db.aliased(BlockDAO, block_chain.subquery())
        return (  # type: ignore[no-any-return]
            db.select(TransactionDAO)
            .join(block_alias, TransactionDAO.blocks)
            .order_by(TransactionDAO.timestamp.desc(), TransactionDAO.id)
        )


class OutflowDAO(Base):
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
        return db.session.execute(
            db.select(cls).filter_by(txid=outflow_txid, idx=outflow_idx)
        ).scalar_one_or_none()

    @classmethod
    def outflows_chain(
        cls, transactions_chain: Select[tuple[TransactionDAO]]
    ) -> Select[tuple[OutflowDAO]]:
        txn_alias = db.aliased(TransactionDAO, transactions_chain.subquery())
        return (  # type: ignore[no-any-return]
            db.select(OutflowDAO)
            .join(txn_alias, OutflowDAO.transaction)
            .order_by(
                txn_alias.timestamp.desc(),
                txn_alias.txid,
                OutflowDAO.idx,
            )
        )


class InflowDAO(Base):
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
                outflow_dao = db.session.execute(
                    db.select(OutflowDAO).filter_by(
                        txid=outflow_txid, idx=outflow_idx
                    )
                ).scalar_one_or_none()
            self.outflow = outflow_dao  # type: ignore[assignment]
            self.transaction = transaction_dao  # type: ignore[assignment]

    @classmethod
    def inflows_chain(
        cls, transactions_chain: Select[tuple[TransactionDAO]]
    ) -> Select[tuple[InflowDAO]]:
        txn_alias = db.aliased(TransactionDAO, transactions_chain.subquery())
        return (  # type: ignore[no-any-return]
            db.select(InflowDAO)
            .join(txn_alias, InflowDAO.transaction)
            .order_by(
                txn_alias.timestamp.desc(),
                txn_alias.txid,
                InflowDAO.idx,
            )
        )


class BlockDAO(Base):
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
        base = (
            db.select(BlockDAO)
            .where(BlockDAO.id == self.id)
            .cte(recursive=True)
        )
        return base.union_all(  # type: ignore[no-any-return]
            db.select(BlockDAO).where(BlockDAO.id == base.c.prev_id)
        )

    @property
    def block_chain(self) -> Select[tuple[BlockDAO]]:
        block_alias = db.aliased(BlockDAO, self._block_chain)
        return db.select(block_alias)  # type: ignore[no-any-return]

    @property
    def transactions_chain(self) -> Select[tuple[TransactionDAO]]:
        return TransactionDAO.transactions_chain(self.block_chain)

    @property
    def outflows_chain(self) -> Select[tuple[OutflowDAO]]:
        return OutflowDAO.outflows_chain(self.transactions_chain)

    @property
    def inflows_chain(self) -> Select[tuple[InflowDAO]]:
        return InflowDAO.inflows_chain(self.transactions_chain)

    def commit(self) -> None:
        db.session.add(self)
        db.session.commit()

    def get_transaction_in_chain(self, txid: str) -> TransactionDAO | None:
        return db.session.execute(
            self.transactions_chain.where(TransactionDAO.txid == txid)
        ).scalar_one_or_none()

    def address_transactions(
        self, address: str
    ) -> Select[tuple[TransactionDAO]]:
        return self.transactions_chain.where(TransactionDAO.address == address)

    def get_block_in_chain(
        self, block_hash: str | None = None, idx: int | None = None
    ) -> BlockDAO | None:
        block_alias = db.aliased(BlockDAO, self.block_chain.subquery())
        stmt = db.select(BlockDAO).join(
            block_alias, BlockDAO.id == block_alias.id
        )
        if block_hash is not None:
            stmt = stmt.where(BlockDAO.block_hash == block_hash)
        if idx is not None:
            stmt = stmt.where(BlockDAO.idx == idx)
        return db.session.execute(stmt).scalar_one_or_none()

    def inflows_in_chain_count(
        self, outflow_txid: str, outflow_idx: int
    ) -> int:
        stmt = self.inflows_chain.where(
            InflowDAO.outflow_txid == outflow_txid,
            InflowDAO.outflow_idx == outflow_idx,
        )
        return (
            1 if db.session.execute(stmt).scalars().first() is not None else 0
        )

    @classmethod
    def count(cls) -> int:
        return (
            db.session.scalar(db.select(db.func.count()).select_from(cls)) or 0
        )

    @classmethod
    def block_hashes(cls) -> Generator[str, None, None]:
        stmt = db.select(cls.block_hash).order_by(
            cls.timestamp.desc(), cls.block_hash
        )
        for (block_hash,) in db.session.execute(stmt):
            yield block_hash

    @classmethod
    def get(
        cls, block_hash: str | None = None, idx: int | None = None
    ) -> BlockDAO | None:
        stmt = db.select(cls)
        if block_hash:
            stmt = stmt.filter_by(block_hash=block_hash)
        else:
            stmt = stmt.filter_by(idx=idx)
        return db.session.execute(stmt).scalar_one_or_none()

    @classmethod
    def longest_chain_blocks_q(cls) -> Select[tuple[BlockDAO]]:
        """Blocks in the longest chain, ordered tip→genesis.

        Matches BlockDAO.block_chain's tip-first ordering so consumers
        that compose on the result (subquery / filter / first) see the
        same row order.
        """
        return (  # type: ignore[no-any-return]
            db.select(BlockDAO)
            .join(
                LongestChainBlockDAO,
                BlockDAO.id == LongestChainBlockDAO.block_id,
            )
            .order_by(LongestChainBlockDAO.position.desc())
        )

    @classmethod
    def longest_chain_transactions_q(cls) -> Select[tuple[TransactionDAO]]:
        """Transactions in the longest chain, ordered tip→genesis.

        Matches TransactionDAO.transactions_chain's ordering
        (timestamp.desc, id) within the longest chain's block set.
        """
        blocks_subq = cls.longest_chain_blocks_q().subquery()
        block_alias = db.aliased(BlockDAO, blocks_subq)
        return (  # type: ignore[no-any-return]
            db.select(TransactionDAO)
            .join(block_alias, TransactionDAO.blocks)
            .order_by(TransactionDAO.timestamp.desc(), TransactionDAO.id)
        )

    @classmethod
    def longest_chain_outflows_q(cls) -> Select[tuple[OutflowDAO]]:
        """Outflows in the longest chain, ordered by their parent txn's
        timestamp desc, then txid, then outflow idx — matching
        OutflowDAO.outflows_chain's ordering.
        """
        txn_subq = cls.longest_chain_transactions_q().subquery()
        txn_alias = db.aliased(TransactionDAO, txn_subq)
        return (  # type: ignore[no-any-return]
            db.select(OutflowDAO)
            .join(txn_alias, OutflowDAO.transaction)
            .order_by(
                txn_alias.timestamp.desc(),
                txn_alias.txid,
                OutflowDAO.idx,
            )
        )

    @classmethod
    def longest_chain_inflows_q(cls) -> Select[tuple[InflowDAO]]:
        """Inflows in the longest chain, ordered analogously to
        InflowDAO.inflows_chain (timestamp desc, txid, inflow idx).
        """
        txn_subq = cls.longest_chain_transactions_q().subquery()
        txn_alias = db.aliased(TransactionDAO, txn_subq)
        return (  # type: ignore[no-any-return]
            db.select(InflowDAO)
            .join(txn_alias, InflowDAO.transaction)
            .order_by(
                txn_alias.timestamp.desc(),
                txn_alias.txid,
                InflowDAO.idx,
            )
        )


class LongestChainBlockDAO(Base):
    """Flat materialization of the canonical chain's block membership.

    One row per block in the currently-longest chain, keyed by block.id
    with `position` 0 at genesis and increasing toward the tip. Maintained
    by ChainDAO.sync_longest_chain_blocks() — never written from anywhere
    else. Phase 6 (2026-05-27) introduced this table to eliminate the
    recursive `BlockDAO._block_chain` CTE from hot-path reads.
    """

    __tablename__ = 'longest_chain_block'

    block_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey('block.id', ondelete='CASCADE'),
        primary_key=True,
    )
    position: Mapped[int] = mapped_column(Integer, unique=True, nullable=False)
    block: Mapped[BlockDAO] = relationship()

    def __init__(self, block_id: int, position: int) -> None:
        self.block_id = block_id
        self.position = position


class ChainDAO(Base):
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

    # Bumped on any longest_chain_block mutation; invalidates all
    # ChainDAO instances' cached _is_longest values within this
    # process. Cross-worker invalidation is out of scope — see
    # the Phase 6.5 spec's Risks section.
    _chain_generation: ClassVar[int] = 0

    @classmethod
    def _bump_generation(cls) -> None:
        cls._chain_generation += 1

    def __init__(
        self, block_hash: str, block_dao: BlockDAO | None = None
    ) -> None:
        self.block_hash = block_hash
        self.block = block_dao or BlockDAO.get(block_hash)  # type: ignore[assignment]

    @property
    def blocks(self) -> Select[tuple[BlockDAO]]:
        if self._is_longest():
            return BlockDAO.longest_chain_blocks_q()
        return self.block.block_chain

    @property
    def transactions(self) -> Select[tuple[TransactionDAO]]:
        if self._is_longest():
            return BlockDAO.longest_chain_transactions_q()
        return self.block.transactions_chain

    @property
    def outflows(self) -> Select[tuple[OutflowDAO]]:
        if self._is_longest():
            return BlockDAO.longest_chain_outflows_q()
        return self.block.outflows_chain

    @property
    def inflows(self) -> Select[tuple[InflowDAO]]:
        if self._is_longest():
            return BlockDAO.longest_chain_inflows_q()
        return self.block.inflows_chain

    def unspent_outflows(
        self,
        address: str,
        filter_pending: bool = False,  # noqa: FBT001
    ) -> Select[tuple[OutflowDAO]]:
        inflows_alias = db.aliased(InflowDAO, self.inflows.subquery())
        stmt = self.outflows.where(OutflowDAO.address == address)
        stmt = stmt.join(inflows_alias, OutflowDAO.inflows, isouter=True)
        stmt = stmt.where(inflows_alias.id.is_(None))
        if filter_pending:
            stmt = stmt.where(~OutflowDAO.pending.any())
        return stmt

    def wallet_balance(self, address: str) -> int:
        inflows_alias = db.aliased(InflowDAO, self.inflows.subquery())
        stmt = self.outflows.where(OutflowDAO.address == address)
        stmt = stmt.join(inflows_alias, OutflowDAO.inflows, isouter=True)
        stmt = stmt.where(inflows_alias.id.is_(None))
        outflows_alias = db.aliased(OutflowDAO, stmt.subquery())
        sum_stmt = db.select(db.func.sum(OutflowDAO.amount)).join(
            outflows_alias, OutflowDAO.id == outflows_alias.id
        )
        return db.session.scalar(sum_stmt) or 0

    def unforgiven_outflows(
        self,
        subject: str,
        address: str | None = None,
        filter_pending: bool = False,  # noqa: FBT001
    ) -> Select[tuple[OutflowDAO]]:
        inflows_alias = db.aliased(InflowDAO, self.inflows.subquery())
        stmt = self.outflows.where(OutflowDAO.subject == subject)
        stmt = stmt.join(inflows_alias, OutflowDAO.inflows, isouter=True)
        stmt = stmt.where(inflows_alias.id.is_(None))
        if address is not None:
            txn_alias = db.aliased(TransactionDAO, self.transactions.subquery())
            stmt = stmt.join(txn_alias, OutflowDAO.transaction)
            stmt = stmt.where(txn_alias.address == address)
        if filter_pending:
            stmt = stmt.where(~OutflowDAO.pending.any())
        return stmt

    def subject_balance(self, subject: str) -> int:
        inflows_alias = db.aliased(InflowDAO, self.inflows.subquery())
        stmt = self.outflows.where(OutflowDAO.subject == subject)
        stmt = stmt.join(inflows_alias, OutflowDAO.inflows, isouter=True)
        stmt = stmt.where(inflows_alias.id.is_(None))
        outflows_alias = db.aliased(OutflowDAO, stmt.subquery())
        sum_stmt = db.select(db.func.sum(OutflowDAO.amount)).join(
            outflows_alias, OutflowDAO.id == outflows_alias.id
        )
        return db.session.scalar(sum_stmt) or 0

    def subject_support(self, subject: str) -> int:
        stmt = self.outflows.where(OutflowDAO.support == subject)
        outflows_alias = db.aliased(OutflowDAO, stmt.subquery())
        sum_stmt = db.select(db.func.sum(OutflowDAO.amount)).join(
            outflows_alias, OutflowDAO.id == outflows_alias.id
        )
        return db.session.scalar(sum_stmt) or 0

    def wallet_leaderboard(
        self,
        earliest: datetime.datetime | None = None,
        latest: datetime.datetime | None = None,
        limit: int | None = None,
    ) -> Select[Any]:
        inflows_alias = db.aliased(InflowDAO, self.inflows.subquery())
        txn_alias = db.aliased(TransactionDAO, self.transactions.subquery())
        stmt = db.select(
            OutflowDAO.address,
            db.func.sum(OutflowDAO.amount).label('ct'),
        )
        stmt = stmt.where(OutflowDAO.address.is_not(None))
        stmt = stmt.join(txn_alias, OutflowDAO.transaction)
        stmt = stmt.join(inflows_alias, OutflowDAO.inflows, isouter=True)
        stmt = stmt.where(inflows_alias.id.is_(None))
        if earliest is not None:
            stmt = stmt.where(txn_alias.timestamp >= earliest)
        if latest is not None:
            stmt = stmt.where(txn_alias.timestamp < latest)
        stmt = stmt.group_by(OutflowDAO.address)
        stmt = stmt.order_by(db.desc('ct'), OutflowDAO.address)
        if limit is not None:
            stmt = stmt.limit(limit)
            return db.select(db.aliased(stmt.subquery()))  # type: ignore[no-any-return]
        return stmt  # type: ignore[no-any-return]

    def _is_longest(self) -> bool:
        """True iff this ChainDAO row is currently the longest chain.

        Used by the property accessors (blocks, transactions, outflows,
        inflows) to route hot reads through LongestChainBlockDAO
        instead of the recursive CTE.

        Cached per instance and invalidated by class-level generation
        bumps inside sync_longest_chain_blocks / rebuild paths. The
        cross-worker case (another process reorged the chain) is a
        known stale-cache risk — bounded to one held instance's
        lifetime within this worker; see the Phase 6.5 spec's Risks.
        """
        cached: tuple[int, bool] | None = getattr(
            self, '_is_longest_cache', None
        )
        if cached is not None and cached[0] == ChainDAO._chain_generation:
            return cached[1]
        longest = ChainDAO.longest()
        result = longest is not None and longest.id == self.id
        self._is_longest_cache = (ChainDAO._chain_generation, result)
        return result

    def sync_longest_chain_blocks(self) -> None:
        """Update the longest_chain_block materialization to reflect
        this chain — if this chain is currently the longest.

        Smart-reorg algorithm: walks the chain's tip back via
        BlockDAO.prev, collecting blocks, until it finds one already
        in the materialization (the common ancestor) OR walks to
        genesis.

        - Bootstrap (empty table): short-circuit to
          _rebuild_longest_chain_blocks; avoids N redundant per-step
          'is in table?' lookups against an empty table.
        - Already in sync: first walked block (the tip) matches; the
          collected diverging list is empty; return without mutation.
        - Shallow / deep reorg with common ancestor: truncate the
          materialization above the ancestor's position, insert the
          diverging suffix in genesis-first order. O(reorg depth) walk.
        - Catastrophic 'different chain' (no common ancestor before
          genesis): delete all and insert the entire collected
          diverging list as the new chain (reusing the list avoids
          a redundant second walk via _rebuild_*).

        Called from Chain.to_db() inside the same SQLAlchemy
        session/transaction as the chain row save.
        """
        if not self._is_longest():
            return

        # Bootstrap fast-path: empty materialization → use the
        # rebuild method directly, skipping per-step lookups against
        # an empty table.
        if not db.session.scalar(
            db.select(db.exists(db.select(LongestChainBlockDAO)))
        ):
            self._rebuild_longest_chain_blocks()
            return

        # Smart-reorg walk: collect blocks from new tip back until we
        # hit one already in the materialization OR reach genesis.
        diverging: list[BlockDAO] = []
        current: BlockDAO | None = self.block
        common_ancestor_position: int | None = None
        while current is not None:
            pos = db.session.scalar(
                db.select(LongestChainBlockDAO.position).where(
                    LongestChainBlockDAO.block_id == current.id
                )
            )
            if pos is not None:
                common_ancestor_position = pos
                break
            diverging.append(current)
            current = current.prev

        if not diverging:
            # Tip itself was the first match — already in sync.
            return

        if common_ancestor_position is None:
            # Walked to genesis without overlap: different chain
            # entirely. Use the collected list directly instead of
            # re-walking via _rebuild_*.
            db.session.execute(db.delete(LongestChainBlockDAO))
            for position, block in enumerate(reversed(diverging)):
                db.session.add(
                    LongestChainBlockDAO(
                        block_id=block.id,
                        position=position,
                    )
                )
            ChainDAO._bump_generation()
            return

        # Common ancestor at position K. Truncate above K, append
        # the diverging suffix in genesis-first order.
        db.session.execute(
            db.delete(LongestChainBlockDAO).where(
                LongestChainBlockDAO.position > common_ancestor_position
            )
        )
        for offset, block in enumerate(reversed(diverging), start=1):
            db.session.add(
                LongestChainBlockDAO(
                    block_id=block.id,
                    position=common_ancestor_position + offset,
                )
            )
        ChainDAO._bump_generation()

    def _rebuild_longest_chain_blocks(self) -> None:
        """Wipe and repopulate longest_chain_block by walking the
        chain iteratively from tip → genesis via BlockDAO.prev links.

        Each step is one indexed PK lookup (block.id). Avoids the
        recursive CTE's planner overhead on long chains — the cost
        that caused the project to be shelved in the past. Bumps
        ChainDAO._chain_generation at the end so cached _is_longest
        values on any in-process ChainDAO instance are invalidated.
        """
        db.session.execute(db.delete(LongestChainBlockDAO))
        blocks: list[BlockDAO] = []
        current: BlockDAO | None = self.block
        while current is not None:
            blocks.append(current)
            current = current.prev
        for position, block in enumerate(reversed(blocks)):
            db.session.add(
                LongestChainBlockDAO(
                    block_id=block.id,
                    position=position,
                )
            )
        ChainDAO._bump_generation()

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

    def address_transactions(
        self, address: str
    ) -> Select[tuple[TransactionDAO]]:
        return self.block.address_transactions(address)

    def commit(self) -> None:
        db.session.add(self)
        db.session.commit()

    @classmethod
    def count(cls) -> int:
        return (
            db.session.scalar(db.select(db.func.count()).select_from(cls)) or 0
        )

    @classmethod
    def get(
        cls, block_hash: str | None = None, id: int | None = None
    ) -> ChainDAO | None:
        stmt = db.select(cls)
        if block_hash:
            stmt = stmt.filter_by(block_hash=block_hash)
        else:
            stmt = stmt.filter_by(id=id)
        return db.session.execute(stmt).scalar_one_or_none()

    @classmethod
    def ids(cls) -> Generator[int, None, None]:
        stmt = db.select(cls.id).order_by(cls.id)
        for (cid,) in db.session.execute(stmt):
            yield cid

    @classmethod
    def chains(cls) -> Select[tuple[ChainDAO]]:
        return (  # type: ignore[no-any-return]
            db.select(cls)
            .join(cls.block)
            .order_by(
                BlockDAO.idx.desc(), BlockDAO.timestamp, BlockDAO.block_hash
            )
        )

    @classmethod
    def longest(cls) -> ChainDAO | None:
        return db.session.execute(cls.chains()).scalars().first()


class PendingTxnDAO(Base):
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
        return (
            db.session.scalar(db.select(db.func.count()).select_from(cls)) or 0
        )

    @classmethod
    def json_datas(
        cls,
        earliest: datetime.datetime | None = None,
        expired: datetime.datetime | None = None,
    ) -> Generator[str, None, None]:
        stmt = db.select(cls.json_data)
        if earliest is not None:
            stmt = stmt.where(cls.received >= earliest)
        if expired is not None:
            stmt = stmt.where(cls.timestamp >= expired)
        stmt = stmt.order_by(cls.timestamp, cls.txid)
        for (json_data,) in db.session.execute(stmt):
            yield json_data

    @classmethod
    def get(cls, txid: str) -> PendingTxnDAO | None:
        return db.session.execute(
            db.select(cls).filter_by(txid=txid)
        ).scalar_one_or_none()


class PendingIOflowDAO(Base):
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


class ChainFill(Base):
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


class ChainFillBlock(Base):
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


class ApiToken(Base):
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
        return db.session.execute(
            db.select(cls).filter_by(address=address)
        ).scalar_one_or_none()

    @classmethod
    def create(cls, wallet: Wallet) -> ApiToken:
        api_token = cls(
            address=wallet.address, public_key=wallet.public_key_b64
        )
        api_token.commit()
        return api_token
