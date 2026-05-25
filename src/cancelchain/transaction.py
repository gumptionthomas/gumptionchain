from __future__ import annotations

# mypy: disable-error-code="no-untyped-call,no-any-return"
from collections.abc import Generator, Iterator, MutableSet
from dataclasses import dataclass, field
from datetime import datetime
from json import JSONDecodeError
from typing import Any, Self

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
from cancelchain.wallet import Wallet

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
    def validate_pk_address(self, data: dict[str, Any], **kwargs: Any) -> None:
        if not validate_address(data.get('public_key'), data.get('address')):
            raise ValidationError(ADDRESS_MISMATCH_MSG)

    @post_load
    def make_transaction(
        self, data: dict[str, Any], **kwargs: Any
    ) -> Transaction:
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
    txid: str | None = field(default=None)
    address: str | None = field(default=None, compare=False)
    public_key: str | None = field(default=None, compare=False, repr=False)
    signature: str | None = field(default=None, compare=False, repr=False)
    inflows: list[Inflow] = field(default_factory=list, compare=False)
    outflows: list[Outflow] = field(default_factory=list, compare=False)
    version: str = field(default=VERSION_1, compare=False, repr=False)

    def __post_init__(self) -> None:
        # `wallet` is a non-field instance attribute set by `set_wallet()`.
        # Initializing it in `__post_init__` (not as a dataclass field)
        # keeps it out of `dataclasses.asdict()` — Wallet wraps an RSA
        # key whose `__getstate__` raises, which would break deepcopy
        # via asdict if walked.
        self.wallet: Wallet | None = None

    @property
    def timestamp_dt(self) -> datetime | None:
        return iso_2_dt(self.timestamp) if self.timestamp else None

    @property
    def data_csv(self) -> str:
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
    def is_sealed(self) -> bool:
        return self.txid is not None

    @property
    def signing_data(self) -> bytes:
        return ','.join([self.data_csv, str(self.txid)]).encode()

    @property
    def schadenfreude(self) -> int:
        return sum([o.schadenfreude for o in self.outflows])

    @property
    def grace(self) -> int:
        return sum([o.grace for o in self.outflows])

    @property
    def mudita(self) -> int:
        return sum([o.mudita for o in self.outflows])

    def set_wallet(self, wallet: Wallet) -> None:
        self.wallet = wallet
        self.address = self.wallet.address
        self.public_key = self.wallet.public_key_b64

    def add_inflow(self, i: Inflow) -> None:
        self.inflows.append(i)

    def get_inflow(self, index: int = 0) -> Inflow | None:
        try:
            return self.inflows[index]
        except IndexError:
            return None

    def add_outflow(self, o: Outflow) -> None:
        self.outflows.append(o)

    def get_outflow(self, index: int = 0) -> Outflow | None:
        try:
            return self.outflows[index]
        except IndexError:
            return None

    def calculate_txid(self) -> str:
        return mill_hash_str(self.data_csv)

    def seal(self) -> None:
        self.txid = self.calculate_txid()

    def sign(self) -> None:
        if not self.is_sealed:
            raise UnsealedTransactionError()
        if self.wallet is None:
            raise MissingWalletError()
        self.signature = self.wallet.sign(self.signing_data)

    def validate_txid(self) -> None:
        if self.txid != self.calculate_txid():
            raise InvalidTransactionIdError()

    def validate_signature(self) -> None:
        if not validate_signature(
            self.public_key, self.signing_data, self.signature
        ):
            raise InvalidSignatureError()

    def validate(self, coinbase: bool = False) -> None:  # noqa: FBT001
        if coinbase:
            errors = CoinbaseTransactionSchema().validate(self.to_dict())
        else:
            errors = RegularTransactionSchema().validate(self.to_dict())
        if errors:
            raise InvalidTransactionError(errors)
        self.validate_signature()
        self.validate_txid()

    def validate_coinbase(self) -> None:
        self.validate(coinbase=True)

    def to_dict(self) -> dict[str, Any]:
        return asdict_sans_none(self)

    def to_json(self) -> str:
        return TransactionSchema().dumps(self.to_dict())

    def to_dao(self) -> TransactionDAO:
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

    def to_db(self) -> None:
        self.to_dao().commit()

    def __hash__(self) -> int:
        return int(str(self.txid), 16)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Self:
        try:
            return TransactionSchema().load(d)
        except ValidationError as e:
            raise InvalidTransactionError(e.messages)

    @classmethod
    def from_json(cls, j: str) -> Self:
        try:
            return TransactionSchema().loads(j)
        except JSONDecodeError as je:
            raise InvalidTransactionError(je.msg)
        except ValidationError as ve:
            raise InvalidTransactionError(ve.messages)

    @classmethod
    def from_dao(cls, dao: Any) -> Self:
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
    def from_db(cls, txid: str) -> Self | None:
        dao = TransactionDAO.get(txid)
        return cls.from_dao(dao) if dao else None

    @classmethod
    def coinbase(
        cls,
        wallet: Wallet,
        reward: int,
        schadenfreude: int,
        grace: int,
        mudita: int,
    ) -> Self:
        outflows: list[Outflow] = []
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


class PendingTxnSet(MutableSet[Transaction]):
    def __contains__(self, txn: object) -> bool:
        if not isinstance(txn, Transaction):
            return False
        return PendingTxnDAO.get(txn.txid) is not None

    def __iter__(self) -> Generator[Transaction, None, None]:
        return (
            Transaction.from_json(json_data) for json_data in self.query_json()
        )

    def __len__(self) -> int:
        return PendingTxnDAO.count()

    def add(self, txn: Transaction) -> None:
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

    def discard(self, txn: Transaction) -> None:
        # MutableSet.discard semantics: no-op if the element is absent.
        # Guard txid (the dataclass declares it `str | None`) and the
        # DAO lookup (returns None when the pending row isn't present).
        if txn.txid is None:
            return
        dao = PendingTxnDAO.get(txn.txid)
        if dao is not None:
            dao.delete()

    def query_json(
        self,
        earliest: datetime | None = None,
        expired: datetime | None = None,
    ) -> Iterator[str]:
        return PendingTxnDAO.json_datas(earliest=earliest, expired=expired)
