from collections.abc import MutableSet
from dataclasses import dataclass, field
from json import JSONDecodeError

from marshmallow import (
    ValidationError,
    fields,
    post_load,
    validate,
    validates_schema,
)

from cancelchain.exceptions import (
    InvalidSignatureError,
    InvalidTransactionError,
    InvalidTransactionIdError,
    MissingWalletError,
    UnsealedTransactionError,
)
from cancelchain.milling import mill_hash_str
from cancelchain.models import (
    InflowDAO,
    OutflowDAO,
    PendingIOflowDAO,
    PendingTxnDAO,
    TransactionDAO,
)
from cancelchain.payload import Inflow, InflowSchema, Outflow, OutflowSchema
from cancelchain.schema import (
    Address,
    Base64,
    MillHash,
    PublicKey,
    SansNoneSchema,
    Timestamp,
    asdict_sans_none,
    validate_address,
    validate_signature,
)
from cancelchain.util import dt_2_iso, iso_2_dt, now_iso

VERSION_1 = '1'
MAX_FLOWS = 50
ADDRESS_MISMATCH_MSG = 'Address/public key mismatch'


class TransactionSchema(SansNoneSchema):
    timestamp = Timestamp(required=True)
    txid = MillHash(required=True)
    address = Address(required=True)
    public_key = PublicKey(required=True)
    signature = Base64(required=False)
    inflows = fields.List(
        fields.Nested(InflowSchema),
        required=True,
        validate=validate.Length(min=0, max=MAX_FLOWS),
    )
    outflows = fields.List(
        fields.Nested(OutflowSchema),
        required=True,
        validate=validate.Length(min=1, max=MAX_FLOWS),
    )
    version = fields.String(required=True, validate=validate.Equal(VERSION_1))

    @validates_schema
    def validate_pk_address(self, data, **kwargs):
        if not validate_address(data.get('public_key'), data.get('address')):
            raise ValidationError(ADDRESS_MISMATCH_MSG)

    @post_load
    def make_transaction(self, data, **kwargs):
        return Transaction(**data)


class RegularTransactionSchema(TransactionSchema):
    inflows = fields.List(
        fields.Nested(InflowSchema),
        required=True,
        validate=validate.Length(min=1, max=MAX_FLOWS),
    )


class CoinbaseTransactionSchema(TransactionSchema):
    inflows = fields.List(
        fields.Nested(InflowSchema),
        required=True,
        validate=validate.Length(equal=0),
    )
    outflows = fields.List(
        fields.Nested(OutflowSchema),
        required=True,
        validate=validate.Length(min=1, max=4),
    )


@dataclass(order=True)
class Transaction:
    timestamp: str = field(default_factory=now_iso)
    txid: str = field(default=None)
    address: str = field(default=None, compare=False)
    public_key: str = field(default=None, compare=False, repr=False)
    signature: str = field(default=None, compare=False, repr=False)
    inflows: list[Inflow] = field(default_factory=list, compare=False)
    outflows: list[Outflow] = field(default_factory=list, compare=False)
    version: str = field(default=VERSION_1, compare=False, repr=False)

    @property
    def timestamp_dt(self):
        return iso_2_dt(self.timestamp) if self.timestamp else None

    @property
    def data_csv(self):
        return ','.join(
            [
                str(self.timestamp),
                str(self.address),
                str(self.public_key),
                ','.join(i.data_csv for i in self.inflows),
                ','.join(o.data_csv for o in self.outflows),
                str(self.version),
            ]
        )

    @property
    def is_sealed(self):
        return self.txid is not None

    @property
    def signing_data(self):
        return ','.join([self.data_csv, self.txid]).encode()

    @property
    def schadenfreude(self):
        return sum([o.schadenfreude for o in self.outflows])

    @property
    def grace(self):
        return sum([o.grace for o in self.outflows])

    @property
    def mudita(self):
        return sum([o.mudita for o in self.outflows])

    def set_wallet(self, wallet):
        self.wallet = wallet
        self.address = self.wallet.address
        self.public_key = self.wallet.public_key_b64

    def add_inflow(self, i):
        self.inflows.append(i)

    def get_inflow(self, index=0):
        try:
            return self.inflows[index]
        except IndexError:
            return None

    def add_outflow(self, o):
        self.outflows.append(o)

    def get_outflow(self, index=0):
        try:
            return self.outflows[index]
        except IndexError:
            return None

    def calculate_txid(self):
        return mill_hash_str(self.data_csv)

    def seal(self):
        self.txid = self.calculate_txid()

    def sign(self):
        if not self.is_sealed:
            raise UnsealedTransactionError()
        if not self.wallet:
            raise MissingWalletError()
        self.signature = self.wallet.sign(self.signing_data)

    def validate_txid(self):
        if self.txid != self.calculate_txid():
            raise InvalidTransactionIdError()

    def validate_signature(self):
        if not validate_signature(
            self.public_key, self.signing_data, self.signature
        ):
            raise InvalidSignatureError()

    def validate(self, coinbase=False):
        if coinbase:
            errors = CoinbaseTransactionSchema().validate(self.to_dict())
        else:
            errors = RegularTransactionSchema().validate(self.to_dict())
        if errors:
            raise InvalidTransactionError(errors)
        self.validate_signature()
        self.validate_txid()

    def validate_coinbase(self):
        self.validate(coinbase=True)

    def to_dict(self):
        return asdict_sans_none(self)

    def to_json(self):
        return TransactionSchema().dumps(self.to_dict())

    def to_dao(self):
        return TransactionDAO.get(self.txid) or TransactionDAO(
            self.txid,
            self.version,
            self.timestamp_dt,
            address=self.address,
            public_key=self.public_key,
            signature=self.signature,
            inflow_daos=[
                InflowDAO(
                    self.txid, idx, inflow.outflow_txid, inflow.outflow_idx
                )
                for idx, inflow in enumerate(self.inflows)
            ],
            outflow_daos=[
                OutflowDAO(
                    self.txid,
                    idx,
                    outflow.amount,
                    address=outflow.address,
                    subject=outflow.subject,
                    forgive=outflow.forgive,
                    support=outflow.support,
                )
                for idx, outflow in enumerate(self.outflows)
            ],
        )

    def to_db(self):
        self.to_dao().commit()

    def __hash__(self):
        return int(self.txid, 16)

    @classmethod
    def from_dict(cls, d):
        try:
            return TransactionSchema().load(d)
        except ValidationError as e:
            raise InvalidTransactionError(e.messages)

    @classmethod
    def from_json(cls, j):
        try:
            return TransactionSchema().loads(j)
        except JSONDecodeError as je:
            raise InvalidTransactionError(je.msg)
        except ValidationError as ve:
            raise InvalidTransactionError(ve.messages)

    @classmethod
    def from_dao(cls, dao):
        return cls(
            timestamp=dt_2_iso(dao.timestamp),
            txid=dao.txid,
            address=dao.address,
            public_key=dao.public_key,
            signature=dao.signature,
            inflows=[
                Inflow(
                    outflow_txid=inflow_dao.outflow_txid,
                    outflow_idx=inflow_dao.outflow_idx,
                )
                for inflow_dao in dao.inflows
            ],
            outflows=[
                Outflow(
                    amount=outflow_dao.amount,
                    address=outflow_dao.address,
                    subject=outflow_dao.subject,
                    forgive=outflow_dao.forgive,
                    support=outflow_dao.support,
                )
                for outflow_dao in dao.outflows
            ],
            version=dao.version,
        )

    @classmethod
    def from_db(cls, txid):
        dao = TransactionDAO.get(txid)
        return cls.from_dao(dao) if dao else None

    @classmethod
    def coinbase(cls, wallet, reward, schadenfreude, grace, mudita):
        outflows = []
        if reward:
            outflows.append(Outflow(amount=reward, address=wallet.address))
        if schadenfreude:
            outflows.append(
                Outflow(amount=schadenfreude, address=wallet.address)
            )
        if grace:
            outflows.append(Outflow(amount=grace, address=wallet.address))
        if mudita:
            outflows.append(Outflow(amount=mudita, address=wallet.address))
        cb = cls(outflows=outflows)
        cb.set_wallet(wallet)
        cb.seal()
        cb.sign()
        return cb


class PendingTxnSet(MutableSet):
    def __contains__(self, txn):
        return PendingTxnDAO.get(txn.txid) is not None

    def __iter__(self):
        return (
            Transaction.from_json(json_data) for json_data in self.query_json()
        )

    def __len__(self):
        return PendingTxnDAO.count()

    def add(self, txn):
        dao = PendingTxnDAO(
            txid=txn.txid, timestamp=txn.timestamp_dt, json_data=txn.to_json()
        )
        dao.commit()
        for inflow in txn.inflows:
            ioflow_txn_dao = TransactionDAO.get(inflow.outflow_txid)
            if ioflow_txn_dao is not None:
                ioflow_dao = ioflow_txn_dao.outflows[inflow.outflow_idx]
                if ioflow_dao is not None:
                    PendingIOflowDAO(
                        txid=txn.txid,
                        outflow_txid=inflow.outflow_txid,
                        outflow_idx=inflow.outflow_idx,
                        pending_txn=dao,
                        outflow=ioflow_dao,
                    ).commit()

    def discard(self, txn):
        PendingTxnDAO.get(txn.txid).delete()

    def query_json(self, earliest=None, expired=None):
        return PendingTxnDAO.json_datas(earliest=earliest, expired=expired)
