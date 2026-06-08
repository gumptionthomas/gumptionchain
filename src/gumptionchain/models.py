from __future__ import annotations

import datetime
import json
from collections.abc import Generator
from typing import Any, ClassVar

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Integer,
    Select,
    String,
    Text,
    false,
    or_,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from gumptionchain.database import Base, db
from gumptionchain.payload import StakeKind

# Chain-factory returns below carry `# type: ignore[no-any-return]` because
# Flask-SQLAlchemy's `db.select` / `db.aliased` / `db.desc` facade methods
# return `Any`, even though the underlying `sqlalchemy` primitives are typed.
# Remove the ignores when FSA's stubs improve, or when these sites migrate
# to direct `from sqlalchemy import select, desc` / `from sqlalchemy.orm
# import aliased` imports.


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
    db.Index('ix_block_transaction_transaction_id', 'transaction_id'),
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
    public_key: Mapped[str | None] = mapped_column(String(700))
    signature: Mapped[str | None] = mapped_column(String(700))
    prev_hash: Mapped[str | None] = mapped_column(String(100))
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
        prev_hash: str | None = None,
        inflow_daos: list[InflowDAO] | None = None,
        outflow_daos: list[OutflowDAO] | None = None,
    ) -> None:
        self.txid = txid
        self.version = version
        self.timestamp = timestamp
        self.address = address
        self.public_key = public_key
        self.signature = signature
        self.prev_hash = prev_hash
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


class OutflowDAO(Base):
    __tablename__ = 'outflow'

    id: Mapped[int] = mapped_column(
        Integer, autoincrement=True, primary_key=True
    )
    txid: Mapped[str] = mapped_column(String(100))
    idx: Mapped[int] = mapped_column(Integer)
    amount: Mapped[int] = mapped_column(BigInteger)
    address: Mapped[str | None] = mapped_column(String(100))
    opposition: Mapped[str | None] = mapped_column(String(500))
    rescind: Mapped[str | None] = mapped_column(String(500))
    support: Mapped[str | None] = mapped_column(String(500))
    rescind_kind: Mapped[str | None] = mapped_column(String(16))
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
        db.Index('ix_outflow_transaction_id', 'transaction_id'),
        db.Index('ix_outflow_address', 'address'),
        db.Index('ix_outflow_opposition', 'opposition'),
        db.Index('ix_outflow_support', 'support'),
    )

    def __init__(
        self,
        txid: str,
        idx: int,
        amount: int,
        address: str | None = None,
        opposition: str | None = None,
        rescind: str | None = None,
        support: str | None = None,
        rescind_kind: str | None = None,
        transaction_dao: TransactionDAO | None = None,
    ) -> None:
        with db.session.no_autoflush:
            self.txid = txid
            self.idx = idx
            self.amount = amount
            self.address = address
            self.opposition = opposition
            self.rescind = rescind
            self.support = support
            self.rescind_kind = rescind_kind
            self.transaction = transaction_dao or None  # type: ignore[assignment]

    @classmethod
    def get(cls, outflow_txid: str, outflow_idx: int) -> OutflowDAO | None:
        return db.session.execute(
            db.select(cls).filter_by(txid=outflow_txid, idx=outflow_idx)
        ).scalar_one_or_none()


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
        # The next two index the SAME inflow->parent-outflow relationship via
        # two different column sets: (outflow_txid, outflow_idx) and outflow_id.
        # Both are kept deliberately because different queries filter on
        # different columns — do not "consolidate" them or a query plan breaks.
        db.Index('ix_inflow_outflow_txid_idx', 'outflow_txid', 'outflow_idx'),
        db.Index('ix_inflow_outflow_id', 'outflow_id'),
        db.Index('ix_inflow_transaction_id', 'transaction_id'),
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

    def commit(self, *, commit: bool = True) -> None:
        db.session.add(self)
        if commit:
            db.session.commit()
        else:
            db.session.flush()

    def _ancestry(self) -> tuple[list[int], int | None]:
        """Resolve this block's ancestry against LongestChainBlockDAO without
        recursion.

        Returns (divergent_ids, cap_position):
        - divergent_ids: ids of blocks on the divergent suffix (not in the
          materialization), nearest-first. Empty when this block is canonical.
        - cap_position: position of the common ancestor in the materialization;
          the canonical prefix is everything with position <= cap_position.
          None only when the materialization is empty (bootstrap), in which
          case divergent_ids covers the whole walked chain.

        Cost: O(divergent-suffix length) indexed `prev` lookups — 0 extra for a
        canonical anchor (first lookup hits), reorg-depth for a fork. Never
        O(chain-height) except transient bootstrap.
        """
        divergent: list[int] = []
        current: BlockDAO | None = self
        while current is not None:
            position = db.session.scalar(
                db.select(LongestChainBlockDAO.position).where(
                    LongestChainBlockDAO.block_id == current.id
                )
            )
            if position is not None:
                return divergent, position
            divergent.append(current.id)
            current = current.prev
        return divergent, None

    def ancestry_blocks_q(self) -> Select[tuple[BlockDAO]]:
        """Blocks in this block's ancestry, CTE-free, ordered tip→genesis.

        Combines the short divergent suffix (ids not in the materialization)
        with the canonical prefix (`LongestChainBlockDAO.position <= cap`) as a
        single composable predicate. Degenerates to materialized membership for
        a canonical anchor (`divergent` empty, `cap` = tip position). Ordered
        by `idx` desc (tip→genesis), so consumers that read rows directly
        (not just as a membership subquery) see a consistent order.
        """
        divergent, cap = self._ancestry()
        clauses = []
        if divergent:
            clauses.append(BlockDAO.id.in_(divergent))
        if cap is not None:
            clauses.append(
                db.select(LongestChainBlockDAO.block_id)
                .where(
                    LongestChainBlockDAO.block_id == BlockDAO.id,
                    LongestChainBlockDAO.position <= cap,
                )
                .exists()
            )
        # or_(false(), *clauses) is always-false when clauses is empty (the
        # unreachable divergent-empty + cap-None case) and a no-op wrapper
        # otherwise.
        return (  # type: ignore[no-any-return]
            db.select(BlockDAO)
            .where(or_(false(), *clauses))
            .order_by(BlockDAO.idx.desc())
        )

    def ancestry_transactions_q(self) -> Select[tuple[TransactionDAO]]:
        blocks_subq = self.ancestry_blocks_q().subquery()
        block_alias = db.aliased(BlockDAO, blocks_subq)
        return (  # type: ignore[no-any-return]
            db.select(TransactionDAO)
            .join(block_alias, TransactionDAO.blocks)
            .order_by(TransactionDAO.timestamp.desc(), TransactionDAO.id)
        )

    def ancestry_outflows_q(self) -> Select[tuple[OutflowDAO]]:
        txn_subq = self.ancestry_transactions_q().subquery()
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

    def ancestry_inflows_q(self) -> Select[tuple[InflowDAO]]:
        txn_subq = self.ancestry_transactions_q().subquery()
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

    def get_transaction_in_chain(self, txid: str) -> TransactionDAO | None:
        divergent, cap = self._ancestry()
        if divergent:
            # scalar_one_or_none (not first): a txid is unique within a valid
            # chain, so >1 match signals corrupt block<->txn associations and
            # should raise, matching the prior CTE behaviour.
            hit: TransactionDAO | None = db.session.execute(
                db.select(TransactionDAO)
                .join(TransactionDAO.blocks)
                .where(BlockDAO.id.in_(divergent))
                .where(TransactionDAO.txid == txid)
            ).scalar_one_or_none()
            if hit is not None:
                return hit
        if cap is not None:
            return db.session.execute(
                db.select(TransactionDAO)
                .join(TransactionDAO.blocks)
                .join(
                    LongestChainBlockDAO,
                    LongestChainBlockDAO.block_id == BlockDAO.id,
                )
                .where(LongestChainBlockDAO.position <= cap)
                .where(TransactionDAO.txid == txid)
            ).scalar_one_or_none()
        return None

    def address_transactions(
        self, address: str
    ) -> Select[tuple[TransactionDAO]]:
        return self.ancestry_transactions_q().where(
            TransactionDAO.address == address
        )

    def get_block_in_chain(
        self, block_hash: str | None = None, idx: int | None = None
    ) -> BlockDAO | None:
        divergent, cap = self._ancestry()
        if divergent:
            stmt = db.select(BlockDAO).where(BlockDAO.id.in_(divergent))
            if block_hash is not None:
                stmt = stmt.where(BlockDAO.block_hash == block_hash)
            if idx is not None:
                stmt = stmt.where(BlockDAO.idx == idx)
            # scalar_one_or_none (not first): preserves the prior CTE
            # single-result semantics — a degenerate/corrupt >1 match raises
            # rather than silently returning an arbitrary block.
            hit: BlockDAO | None = db.session.execute(stmt).scalar_one_or_none()
            if hit is not None:
                return hit
        if cap is not None:
            stmt = (
                db.select(BlockDAO)
                .join(
                    LongestChainBlockDAO,
                    LongestChainBlockDAO.block_id == BlockDAO.id,
                )
                .where(LongestChainBlockDAO.position <= cap)
            )
            if block_hash is not None:
                stmt = stmt.where(BlockDAO.block_hash == block_hash)
            if idx is not None:
                stmt = stmt.where(BlockDAO.idx == idx)
            return db.session.execute(stmt).scalar_one_or_none()
        return None

    def inflows_in_chain_count(
        self, outflow_txid: str, outflow_idx: int
    ) -> int:
        divergent, cap = self._ancestry()
        if divergent:
            hit = (
                db.session.execute(
                    db.select(InflowDAO)
                    .join(InflowDAO.transaction)
                    .join(TransactionDAO.blocks)
                    .where(BlockDAO.id.in_(divergent))
                    .where(InflowDAO.outflow_txid == outflow_txid)
                    .where(InflowDAO.outflow_idx == outflow_idx)
                )
                .scalars()
                .first()
            )
            if hit is not None:
                return 1
        if cap is not None:
            hit = (
                db.session.execute(
                    db.select(InflowDAO)
                    .join(InflowDAO.transaction)
                    .join(TransactionDAO.blocks)
                    .join(
                        LongestChainBlockDAO,
                        LongestChainBlockDAO.block_id == BlockDAO.id,
                    )
                    .where(LongestChainBlockDAO.position <= cap)
                    .where(InflowDAO.outflow_txid == outflow_txid)
                    .where(InflowDAO.outflow_idx == outflow_idx)
                )
                .scalars()
                .first()
            )
            if hit is not None:
                return 1
        return 0

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

        Ordered by `position` desc (tip→genesis) so consumers that
        compose on the result (subquery / filter / first) see a
        consistent row order.
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
    def transaction_counts(cls, block_ids: list[int]) -> dict[int, int]:
        """Map block id → transaction count for the given blocks.

        One grouped query over the block↔transaction association table,
        so a list view can show per-block txn counts without firing a
        COUNT per row against the `lazy='dynamic'` `transactions`
        relationship (an N+1).
        """
        if not block_ids:
            return {}
        rows = db.session.execute(
            db.select(
                block_transactions.c.block_id,
                db.func.count().label('ct'),
            )
            .where(block_transactions.c.block_id.in_(block_ids))
            .group_by(block_transactions.c.block_id)
        ).all()
        return {row[0]: row[1] for row in rows}

    @classmethod
    def longest_chain_transactions_q(cls) -> Select[tuple[TransactionDAO]]:
        """Transactions in the longest chain, ordered tip→genesis.

        Ordered by (timestamp.desc, id) within the longest chain's
        block set.
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
        timestamp desc, then txid, then outflow idx.
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
        """Inflows in the longest chain, ordered by their parent txn's
        timestamp desc, then txid, then inflow idx.
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
    recursive block-ancestry CTE (since deleted in #158) from hot-path
    reads.
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


def _outflow_view(
    *,
    amount: int,
    address: str | None = None,
    opposition: str | None = None,
    support: str | None = None,
    rescind: str | None = None,
    rescind_kind: str | None = None,
) -> dict[str, Any]:
    if opposition is not None:
        return {'kind': 'opposition', 'subject': opposition, 'amount': amount}
    if support is not None:
        return {'kind': 'support', 'subject': support, 'amount': amount}
    if rescind is not None:
        return {
            'kind': 'rescind',
            'subject': rescind,
            'rescind_kind': rescind_kind,
            'amount': amount,
        }
    return {'kind': 'transfer', 'address': address, 'amount': amount}


def _pending_provenance(txid: str) -> dict[str, Any] | None:
    pending = PendingTxnDAO.get(txid)
    if pending is None:
        return None
    data = json.loads(pending.json_data)
    outflows = [
        _outflow_view(
            amount=o['amount'],
            address=o.get('address'),
            opposition=o.get('opposition'),
            support=o.get('support'),
            rescind=o.get('rescind'),
            rescind_kind=o.get('rescind_kind'),
        )
        for o in data.get('outflows', [])
    ]
    return {
        'address': data.get('address'),
        'outflows': outflows,
        'timestamp': data.get('timestamp'),
        'status': 'pending',
        'block_hash': None,
        'height': None,
        'confirmations': 0,
    }


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
        return self.block.ancestry_blocks_q()

    @property
    def transactions(self) -> Select[tuple[TransactionDAO]]:
        if self._is_longest():
            return BlockDAO.longest_chain_transactions_q()
        return self.block.ancestry_transactions_q()

    @property
    def outflows(self) -> Select[tuple[OutflowDAO]]:
        if self._is_longest():
            return BlockDAO.longest_chain_outflows_q()
        return self.block.ancestry_outflows_q()

    @property
    def inflows(self) -> Select[tuple[InflowDAO]]:
        if self._is_longest():
            return BlockDAO.longest_chain_inflows_q()
        return self.block.ancestry_inflows_q()

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

    def unrescinded_outflows(
        self,
        subject: str,
        kind: StakeKind,
        address: str | None = None,
        filter_pending: bool = False,  # noqa: FBT001
    ) -> Select[tuple[OutflowDAO]]:
        column = (
            OutflowDAO.support if kind == 'support' else OutflowDAO.opposition
        )
        inflows_alias = db.aliased(InflowDAO, self.inflows.subquery())
        stmt = self.outflows.where(column == subject)
        stmt = stmt.join(inflows_alias, OutflowDAO.inflows, isouter=True)
        stmt = stmt.where(inflows_alias.id.is_(None))
        if address is not None:
            txn_alias = db.aliased(TransactionDAO, self.transactions.subquery())
            stmt = stmt.join(txn_alias, OutflowDAO.transaction)
            stmt = stmt.where(txn_alias.address == address)
        if filter_pending:
            stmt = stmt.where(~OutflowDAO.pending.any())
        return stmt

    def _stake_balance(self, subject: str, kind: StakeKind) -> int:
        column = (
            OutflowDAO.support if kind == 'support' else OutflowDAO.opposition
        )
        inflows_alias = db.aliased(InflowDAO, self.inflows.subquery())
        stmt = self.outflows.where(column == subject)
        stmt = stmt.join(inflows_alias, OutflowDAO.inflows, isouter=True)
        stmt = stmt.where(inflows_alias.id.is_(None))
        outflows_alias = db.aliased(OutflowDAO, stmt.subquery())
        sum_stmt = db.select(db.func.sum(OutflowDAO.amount)).join(
            outflows_alias, OutflowDAO.id == outflows_alias.id
        )
        return db.session.scalar(sum_stmt) or 0

    def opposition_balance(self, subject: str) -> int:
        return self._stake_balance(subject, 'opposition')

    def support_balance(self, subject: str) -> int:
        return self._stake_balance(subject, 'support')

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

    def subject_leaderboard(
        self,
        limit: int | None = None,
    ) -> Select[Any]:
        inflows_alias = db.aliased(InflowDAO, self.inflows.subquery())

        def _leg(column: Any, kind: StakeKind) -> Select[Any]:
            stmt = self.outflows.where(column.is_not(None))
            stmt = stmt.join(inflows_alias, OutflowDAO.inflows, isouter=True)
            stmt = stmt.where(inflows_alias.id.is_(None))
            stmt = stmt.with_only_columns(
                column.label('subject'),
                OutflowDAO.amount.label('amount'),
                db.literal(kind).label('kind'),
            )
            # UNION legs must not carry their own ORDER BY (the
            # chain-scoped self.outflows select adds one); SQLite rejects
            # an ORDER BY inside a compound SELECT operand.
            return stmt.order_by(None)

        opp = _leg(OutflowDAO.opposition, 'opposition')
        sup = _leg(OutflowDAO.support, 'support')
        union = opp.union_all(sup).subquery()
        stmt = db.select(
            union.c.subject,
            db.func.sum(
                db.case((union.c.kind == 'opposition', union.c.amount), else_=0)
            ).label('opposition'),
            db.func.sum(
                db.case((union.c.kind == 'support', union.c.amount), else_=0)
            ).label('support'),
            db.func.sum(union.c.amount).label('total'),
        )
        stmt = stmt.group_by(union.c.subject)
        stmt = stmt.order_by(db.desc('total'), union.c.subject)
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

    @classmethod
    def pending_provenance(cls, txid: str) -> dict[str, Any] | None:
        return _pending_provenance(txid)

    def transaction_provenance(self, txid: str) -> dict[str, Any] | None:
        txn = self.get_transaction(txid)  # canonical (longest chain) or None
        if txn is not None:
            block = (
                db.session.execute(
                    db.select(BlockDAO)
                    .join(
                        LongestChainBlockDAO,
                        LongestChainBlockDAO.block_id == BlockDAO.id,
                    )
                    .join(BlockDAO.transactions)
                    .where(TransactionDAO.txid == txid)
                )
                .scalars()
                .first()
            )
            tip_height = self.block.idx
            height = block.idx if block is not None else None
            confirmations = tip_height - height + 1 if height is not None else 0
            return {
                'address': txn.address,
                'outflows': [
                    _outflow_view(
                        amount=o.amount,
                        address=o.address,
                        opposition=o.opposition,
                        support=o.support,
                        rescind=o.rescind,
                        rescind_kind=o.rescind_kind,
                    )
                    for o in txn.outflows
                ],
                'timestamp': txn.timestamp.isoformat(),
                'status': 'canonical',
                'block_hash': block.block_hash if block is not None else None,
                'height': height,
                'confirmations': confirmations,
            }
        orphan = TransactionDAO.get(txid)
        if orphan is not None:
            block_hash = orphan.blocks[0].block_hash if orphan.blocks else None
            return {
                'address': orphan.address,
                'outflows': [
                    _outflow_view(
                        amount=o.amount,
                        address=o.address,
                        opposition=o.opposition,
                        support=o.support,
                        rescind=o.rescind,
                        rescind_kind=o.rescind_kind,
                    )
                    for o in orphan.outflows
                ],
                'timestamp': orphan.timestamp.isoformat(),
                'status': 'orphaned',
                'block_hash': block_hash,
                'height': None,
                'confirmations': 0,
            }
        return _pending_provenance(txid)

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
    timestamp: Mapped[datetime.datetime] = mapped_column(DateTime, index=True)
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
            # Same open-boundary rule as block.txn_is_expired: a txn is
            # expired iff its timestamp is strictly older than the cutoff,
            # so keep timestamp >= cutoff (the boundary txn is alive).
            stmt = stmt.where(cls.timestamp >= expired)
        stmt = stmt.order_by(cls.timestamp, cls.txid)
        for (json_data,) in db.session.execute(stmt):
            yield json_data

    @classmethod
    def delete_expired(cls, cutoff: datetime.datetime) -> int:
        """Delete every pending txn strictly older than `cutoff`
        (timestamp < cutoff) in a single commit, returning the count
        removed. Uses an indexed SQL filter to fetch only the expired
        rows (no whole-pool re-parse) and ORM `session.delete()` per row
        so the `ioflows` relationship cascade removes companion
        PendingIOflowDAO rows — a Core bulk DELETE would orphan them, as
        the FK carries no ON DELETE CASCADE. Open boundary: a txn exactly
        at the cutoff is kept (mirrors block.txn_is_expired / json_datas).
        """
        rows = (
            db.session.execute(db.select(cls).where(cls.timestamp < cutoff))
            .scalars()
            .all()
        )
        if not rows:
            # Nothing expired: skip the commit so an empty pass stays a
            # true no-op and never flushes unrelated in-flight session
            # state (matches the pre-refactor per-eviction behavior).
            return 0
        for row in rows:
            db.session.delete(row)
        db.session.commit()
        return len(rows)

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
