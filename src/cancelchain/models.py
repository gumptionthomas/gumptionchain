import datetime
import uuid

from passlib.hash import argon2, pbkdf2_sha256

from cancelchain.database import db
from cancelchain.wallet import Wallet


def rollback_session():
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

    id = db.Column(db.Integer, autoincrement=True, primary_key=True)
    txid = db.Column(db.String(100), unique=True, nullable=False, index=True)
    version = db.Column(db.String(10), nullable=False)
    timestamp = db.Column(db.DateTime, nullable=False)
    address = db.Column(db.String(100), nullable=True)
    public_key = db.Column(db.String(500), nullable=True)
    signature = db.Column(db.String(500), nullable=True)
    blocks = db.relationship(
        'BlockDAO', secondary=block_transactions, back_populates='transactions'
    )

    def __init__(
        self,
        txid,
        version,
        timestamp,
        address=None,
        public_key=None,
        signature=None,
        inflow_daos=None,
        outflow_daos=None,
    ):
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

    def commit(self):
        db.session.add(self)
        db.session.commit()

    @classmethod
    def get(cls, txid):
        return cls.query.filter_by(txid=txid).one_or_none()

    @classmethod
    def transactions_chain(cls, block_chain):
        block_alias = db.aliased(BlockDAO, block_chain.subquery())
        q = db.session.query(TransactionDAO)
        q = q.join(block_alias, TransactionDAO.blocks)
        return q.order_by(TransactionDAO.timestamp.desc(), TransactionDAO.id)


class OutflowDAO(db.Model):
    __tablename__ = 'outflow'

    id = db.Column(db.Integer, autoincrement=True, primary_key=True)
    txid = db.Column(db.String(100), nullable=False)
    idx = db.Column(db.Integer, nullable=False)
    amount = db.Column(db.BigInteger, nullable=False)
    address = db.Column(db.String(100), nullable=True)
    subject = db.Column(db.String(500), nullable=True)
    forgive = db.Column(db.String(500), nullable=True)
    support = db.Column(db.String(500), nullable=True)
    transaction_id = db.Column(
        db.Integer, db.ForeignKey('transaction.id'), nullable=False
    )
    transaction = db.relationship(
        'TransactionDAO',
        backref=db.backref('outflows', order_by='OutflowDAO.idx'),
    )
    __table_args__ = (
        db.UniqueConstraint('txid', 'idx'),
        db.Index('ix_outflow_txid_idx', 'txid', 'idx'),
    )

    def __init__(
        self,
        txid,
        idx,
        amount,
        address=None,
        subject=None,
        forgive=None,
        support=None,
        transaction_dao=None,
    ):
        with db.session.no_autoflush:
            self.txid = txid
            self.idx = idx
            self.amount = amount
            self.address = address
            self.subject = subject
            self.forgive = forgive
            self.support = support
            self.transaction = transaction_dao or None

    @classmethod
    def get(cls, outflow_txid, outflow_idx):
        return cls.query.filter_by(
            outflow_txid=outflow_txid, outflow_idx=outflow_idx
        ).one_or_none()

    @classmethod
    def outflows_chain(cls, transactions_chain):
        txn_alias = db.aliased(TransactionDAO, transactions_chain.subquery())
        q = db.session.query(OutflowDAO)
        q = q.join(txn_alias, OutflowDAO.transaction)
        q = q.order_by(
            txn_alias.timestamp.desc(), txn_alias.txid, OutflowDAO.idx
        )
        return q


class InflowDAO(db.Model):
    __tablename__ = 'inflow'

    id = db.Column(db.Integer, autoincrement=True, primary_key=True)
    txid = db.Column(db.String(100), nullable=False)
    idx = db.Column(db.Integer, nullable=False)
    outflow_txid = db.Column(db.String(100), nullable=False)
    outflow_idx = db.Column(db.Integer, nullable=False)
    outflow_id = db.Column(
        db.Integer, db.ForeignKey('outflow.id'), nullable=False
    )
    outflow = db.relationship('OutflowDAO', backref='inflows')
    transaction_id = db.Column(
        db.Integer, db.ForeignKey('transaction.id'), nullable=False
    )
    transaction = db.relationship(
        'TransactionDAO',
        backref=db.backref('inflows', order_by='InflowDAO.idx'),
    )

    __table_args__ = (
        db.UniqueConstraint('txid', 'idx'),
        db.Index('ix_inflow_txid_idx', 'txid', 'idx'),
    )

    def __init__(
        self,
        txid,
        idx,
        outflow_txid,
        outflow_idx,
        outflow_dao=None,
        transaction_dao=None,
    ):
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
            self.transaction = transaction_dao

    @classmethod
    def inflows_chain(cls, transactions_chain):
        txn_alias = db.aliased(TransactionDAO, transactions_chain.subquery())
        q = db.session.query(InflowDAO)
        q = q.join(txn_alias, InflowDAO.transaction)
        q = q.order_by(
            txn_alias.timestamp.desc(), txn_alias.txid, InflowDAO.idx
        )
        return q


class BlockDAO(db.Model):
    __tablename__ = 'block'

    id = db.Column(db.Integer, autoincrement=True, primary_key=True)
    block_hash = db.Column(
        db.String(100), unique=True, nullable=False, index=True
    )
    version = db.Column(db.String(10), nullable=False)
    idx = db.Column(db.Integer, nullable=False, index=True)
    prev_hash = db.Column(db.String(100), nullable=False, index=True)
    timestamp = db.Column(db.DateTime, nullable=False)
    merkle_root = db.Column(db.String(100), nullable=False)
    proof_of_work = db.Column(db.BigInteger, nullable=False)
    target = db.Column(db.String(100), nullable=False)
    prev_id = db.Column(db.Integer, db.ForeignKey('block.id'), nullable=True)
    prev = db.relationship('BlockDAO', remote_side=[id], backref='next')
    transactions = db.relationship(
        'TransactionDAO',
        secondary=block_transactions,
        back_populates='blocks',
        lazy='dynamic',
        order_by=[TransactionDAO.timestamp, TransactionDAO.txid],
    )

    def __init__(
        self,
        block_hash,
        version,
        idx,
        prev_hash,
        timestamp,
        merkle_root,
        proof_of_work,
        target,
        prev_dao=None,
        transaction_daos=None,
    ):
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
    def _block_chain(self):
        q = BlockDAO.query.filter(BlockDAO.id == self.id).cte(recursive=True)
        return q.union_all(BlockDAO.query.filter(BlockDAO.id == q.c.prev_id))

    @property
    def block_chain(self):
        return db.session.query(self._block_chain)

    @property
    def transactions_chain(self):
        return TransactionDAO.transactions_chain(self.block_chain)

    @property
    def outflows_chain(self):
        return OutflowDAO.outflows_chain(self.transactions_chain)

    @property
    def inflows_chain(self):
        return InflowDAO.inflows_chain(self.transactions_chain)

    def commit(self):
        db.session.add(self)
        db.session.commit()

    def get_transaction_in_chain(self, txid):
        return self.transactions_chain.filter(
            TransactionDAO.txid == txid
        ).one_or_none()

    def address_transactions(self, address):
        return self.transactions_chain.filter(TransactionDAO.address == address)

    def get_block_in_chain(self, block_hash=None, idx=None):
        block_alias = db.aliased(BlockDAO, self.block_chain.subquery())
        q = db.session.query(BlockDAO)
        q = q.join(block_alias, BlockDAO.id == block_alias.id)
        if block_hash is not None:
            q = q.filter(BlockDAO.block_hash == block_hash)
        if idx is not None:
            q = q.filter(BlockDAO.idx == idx)
        return q.one_or_none()

    def inflows_in_chain_count(self, outflow_txid, outflow_idx):
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
    def count(cls):
        return db.session.query(db.func.count(cls.id)).one_or_none()[0]

    @classmethod
    def block_hashes(cls):
        for r in cls.query.with_entities(cls.block_hash).order_by(
            cls.timestamp.desc(), cls.block_hash
        ):
            yield r[0]

    @classmethod
    def get(cls, block_hash=None, idx=None):
        q = cls.query
        if block_hash:
            q = q.filter_by(block_hash=block_hash)
        else:
            q = q.filter_by(idx=idx)
        return q.one_or_none()


class ChainDAO(db.Model):
    __tablename__ = 'chain'

    id = db.Column(db.Integer, autoincrement=True, primary_key=True)
    block_hash = db.Column(
        db.String(100), unique=True, nullable=False, index=True
    )
    block_id = db.Column(
        db.Integer, db.ForeignKey('block.id'), nullable=False, index=True
    )
    block = db.relationship('BlockDAO', backref='chains')

    def __init__(self, block_hash, block_dao=None):
        self.block_hash = block_hash
        self.block = block_dao or BlockDAO.get(block_hash)

    @property
    def blocks(self):
        return self.block.block_chain

    @property
    def transactions(self):
        return self.block.transactions_chain

    @property
    def outflows(self):
        return self.block.outflows_chain

    @property
    def inflows(self):
        return self.block.inflows_chain

    def unspent_outflows(self, address, filter_pending=False):
        inflows_alias = db.aliased(InflowDAO, self.inflows.subquery())
        q = self.outflows.filter(OutflowDAO.address == address)
        q = q.join(inflows_alias, OutflowDAO.inflows, isouter=True)
        q = q.filter(inflows_alias.id.is_(None))
        if filter_pending:
            q = q.filter(~OutflowDAO.pending.any())
        return q

    def wallet_balance(self, address):
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

    def unforgiven_outflows(self, subject, address=None, filter_pending=False):
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

    def subject_balance(self, subject):
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

    def subject_support(self, subject):
        q = self.outflows.filter(OutflowDAO.support == subject)
        outflows_alias = db.aliased(OutflowDAO, q.subquery())
        q2 = db.session.query(db.func.sum(OutflowDAO.amount)).join(
            outflows_alias, OutflowDAO.id == outflows_alias.id
        )
        amount = q2.one_or_none()
        return (amount[0] or 0) if amount is not None else 0

    def wallet_leaderboard(self, earliest=None, latest=None, limit=None):
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

    def set_block_hash(self, block_hash):
        self.block = BlockDAO.get(block_hash)
        self.block_hash = block_hash

    def get_block(self, block_hash=None, idx=None):
        return self.block.get_block_in_chain(block_hash=block_hash, idx=idx)

    def next_block(self, block):
        for next_block in block.next:
            if self.get_block(block_hash=next_block.block_hash) is not None:
                return next_block
        return None

    def get_transaction(self, txid):
        return self.block.get_transaction_in_chain(txid)

    def address_transactions(self, address):
        return self.block.address_transactions(address)

    def commit(self):
        db.session.add(self)
        db.session.commit()

    @classmethod
    def count(cls):
        return db.session.query(db.func.count(cls.id)).one_or_none()[0]

    @classmethod
    def get(cls, block_hash=None, id=None):
        q = cls.query
        if block_hash:
            q = q.filter_by(block_hash=block_hash)
        else:
            q = q.filter_by(id=id)
        return q.one_or_none()

    @classmethod
    def ids(cls):
        for r in cls.query.with_entities(cls.id).order_by(cls.id):
            yield r[0]

    @classmethod
    def chains(cls):
        return cls.query.join(cls.block).order_by(
            BlockDAO.idx.desc(), BlockDAO.timestamp, BlockDAO.block_hash
        )

    @classmethod
    def longest(cls):
        return cls.chains().first()


class PendingTxnDAO(db.Model):
    __tablename__ = 'pending_txn'

    id = db.Column(db.Integer, autoincrement=True, primary_key=True)
    txid = db.Column(db.String(100), unique=True, nullable=False, index=True)
    timestamp = db.Column(db.DateTime, nullable=False)
    json_data = db.Column(db.Text(), nullable=False)
    received = db.Column(db.DateTime, default=datetime.datetime.utcnow)

    def add(self):
        db.session.add(self)

    def commit(self):
        self.add()
        db.session.commit()

    def delete(self):
        db.session.delete(self)
        db.session.commit()

    @classmethod
    def count(cls):
        return db.session.query(db.func.count(cls.id)).one_or_none()[0]

    @classmethod
    def json_datas(cls, earliest=None, expired=None):
        q = cls.query.with_entities(cls.json_data)
        if earliest is not None:
            q = q.filter(cls.received >= earliest)
        if expired is not None:
            q = q.filter(cls.timestamp >= expired)
        q = q.order_by(cls.timestamp, cls.txid)
        for r in q:
            yield r[0]

    @classmethod
    def get(cls, txid):
        return cls.query.filter_by(txid=txid).one_or_none()


class PendingIOflowDAO(db.Model):
    __tablename__ = 'pending_ioflow'

    id = db.Column(db.Integer, autoincrement=True, primary_key=True)
    txid = db.Column(db.String(100), nullable=False)
    outflow_txid = db.Column(db.String(100), nullable=False)
    outflow_idx = db.Column(db.Integer, nullable=False)
    pending_txn_id = db.Column(
        db.Integer, db.ForeignKey('pending_txn.id'), nullable=False
    )
    pending_txn = db.relationship(
        'PendingTxnDAO',
        backref=db.backref('ioflows', cascade='delete, delete-orphan'),
    )
    outflow_id = db.Column(
        db.Integer, db.ForeignKey('outflow.id'), nullable=False
    )
    outflow = db.relationship('OutflowDAO', backref='pending')

    def add(self):
        db.session.add(self)

    def commit(self):
        self.add()
        db.session.commit()


class ChainFill(db.Model):
    __tablename__ = 'chain_fill'

    id = db.Column(db.Integer, autoincrement=True, primary_key=True)

    def add(self):
        db.session.add(self)

    def commit(self):
        self.add()
        db.session.commit()

    def delete(self):
        db.session.delete(self)
        db.session.commit()


class ChainFillBlock(db.Model):
    __tablename__ = 'chain_fill_block'

    id = db.Column(db.Integer, autoincrement=True, primary_key=True)
    block_hash = db.Column(db.String(100), nullable=False)
    idx = db.Column(db.Integer, nullable=False)
    block_json = db.Column(db.Text, nullable=True)
    chain_fill_id = db.Column(
        db.Integer, db.ForeignKey('chain_fill.id'), nullable=False
    )
    chain_fill = db.relationship(
        'ChainFill',
        backref=db.backref(
            'blocks',
            order_by='ChainFillBlock.idx',
            cascade='delete, delete-orphan',
        ),
    )

    def add(self):
        db.session.add(self)

    def commit(self):
        self.add()
        db.session.commit()


class ApiToken(db.Model):
    __tablename__ = 'api_token'

    id = db.Column(db.Integer, autoincrement=True, primary_key=True)
    address = db.Column(db.String(100), unique=True, nullable=False, index=True)
    public_key = db.Column(db.String(500), nullable=False)
    hashed = db.Column(db.String(100), unique=True, nullable=True)
    cipher = db.Column(db.String(500), unique=True, nullable=True)
    timestamp = db.Column(
        db.DateTime,
        default=datetime.datetime.utcnow,
        onupdate=datetime.datetime.utcnow,
    )

    @property
    def expired(self):
        now_dt = datetime.datetime.now(datetime.timezone.utc)
        now_dt = now_dt.replace(tzinfo=None)
        return self.timestamp < (now_dt - datetime.timedelta(seconds=60))

    def add(self):
        db.session.add(self)

    def commit(self):
        self.add()
        db.session.commit()

    def refreshed_cipher(self):
        if self.expired or not (self.cipher and self.hashed):
            secret = str(uuid.uuid4())
            try:
                self.hashed = argon2.hash(secret)
            except Exception:
                self.hashed = pbkdf2_sha256.hash(secret)
            wallet = Wallet(b64ks=self.public_key)
            self.cipher = wallet.encrypt(secret.encode())
            self.commit()
        return self.cipher

    def reset(self):
        self.cipher = None
        self.hashed = None
        self.commit()

    def verify(self, secret):
        hm = argon2 if argon2.identify(self.hashed) else pbkdf2_sha256
        return hm.verify(secret, self.hashed) and not self.expired

    @classmethod
    def get(cls, address):
        return cls.query.filter_by(address=address).one_or_none()

    @classmethod
    def create(cls, wallet):
        api_token = cls(
            address=wallet.address, public_key=wallet.public_key_b64
        )
        api_token.commit()
        return api_token
