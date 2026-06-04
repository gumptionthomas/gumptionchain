from __future__ import annotations

import json
from collections.abc import Generator, Iterator, MutableSet
from dataclasses import dataclass, field
from datetime import datetime
from json import JSONDecodeError
from typing import Annotated, Any, Literal, Self, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    model_validator,
)

from gumptionchain.exceptions import (
    InvalidSignatureError,
    InvalidTransactionError,
    InvalidTransactionIdError,
    MissingWalletError,
    UnsealedTransactionError,
)
from gumptionchain.milling import mill_hash_str
from gumptionchain.models import (
    InflowDAO,
    OutflowDAO,
    PendingIOflowDAO,
    PendingTxnDAO,
    TransactionDAO,
)
from gumptionchain.payload import (
    Inflow,
    InflowModel,
    Outflow,
    OutflowModel,
    StakeKind,
)
from gumptionchain.schema import (
    AddressType,
    Base64Type,
    MillHashType,
    PublicKeyType,
    TimestampType,
    asdict_sans_none,
    pydantic_errors_to_messages,
    validate_address,
    validate_signature,
)
from gumptionchain.util import dt_2_iso, iso_2_dt, now_iso
from gumptionchain.wallet import Wallet

VERSION_1 = '1'
MAX_FLOWS = 50
ADDRESS_MISMATCH_MSG = 'Address/public key mismatch'


@dataclass(frozen=True)
class CoinbaseMetrics:
    schadenfreude: int = 0
    grace: int = 0
    mudita: int = 0
    regret: int = 0

    def __add__(self, other: CoinbaseMetrics) -> CoinbaseMetrics:
        return CoinbaseMetrics(
            self.schadenfreude + other.schadenfreude,
            self.grace + other.grace,
            self.mudita + other.mudita,
            self.regret + other.regret,
        )

    def nonzero_amounts(self) -> list[int]:
        """Nonzero amounts in coinbase order (sf, grace, mudita, regret)."""
        return [
            v
            for v in (self.schadenfreude, self.grace, self.mudita, self.regret)
            if v
        ]


# ---------------------------------------------------------------------------
# Pydantic v2 models — canonical validators for all callers.
# ---------------------------------------------------------------------------


def txn_from_model_data(data: dict[str, Any]) -> dict[str, Any]:
    """Convert a TransactionModel.model_dump() dict's nested lists from
    list[dict] to list[Inflow] / list[Outflow] before passing to the
    Transaction dataclass constructor.

    Public — PR-4 (block.py) imports this to reconstruct nested
    Transactions from BlockModel.model_dump() output.
    """
    return {
        **data,
        'inflows': [Inflow(**i) for i in data.get('inflows', [])],
        'outflows': [Outflow(**o) for o in data.get('outflows', [])],
    }


class TransactionModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

    timestamp: TimestampType
    txid: MillHashType
    address: AddressType
    public_key: PublicKeyType
    signature: Base64Type | None = None
    inflows: Annotated[
        list[InflowModel], Field(min_length=0, max_length=MAX_FLOWS)
    ]
    outflows: Annotated[
        list[OutflowModel], Field(min_length=1, max_length=MAX_FLOWS)
    ]
    version: Literal['1']
    prev_hash: MillHashType | None = None

    @model_validator(mode='after')
    def validate_pk_address(self) -> Self:
        if not validate_address(self.public_key, self.address):
            raise ValueError(ADDRESS_MISMATCH_MSG)
        return self


class RegularTransactionModel(TransactionModel):
    inflows: Annotated[
        list[InflowModel], Field(min_length=1, max_length=MAX_FLOWS)
    ]
    # Regular transactions are not block-bound; a prev_hash must not be set.
    prev_hash: None = None


class CoinbaseTransactionModel(TransactionModel):
    inflows: Annotated[list[InflowModel], Field(min_length=0, max_length=0)]
    # reward + up to 4 sentiment metrics: schadenfreude, grace, mudita, regret
    outflows: Annotated[list[OutflowModel], Field(min_length=1, max_length=5)]
    # Coinbases must carry their block's prev_hash binding.
    prev_hash: MillHashType


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
    prev_hash: str | None = field(default=None, compare=False, repr=False)

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
        fields = [
            str(self.timestamp),
            str(self.address),
            str(self.public_key),
            ','.join(i.data_csv for i in self.inflows),
            ','.join(o.data_csv for o in self.outflows),
            str(self.version),
        ]
        # A4.c v2: coinbases bind their block's prev_hash into the txid.
        # Conditional append keeps regular-txn data_csv (and txids)
        # byte-identical to the pre-binding scheme.
        if self.prev_hash is not None:
            fields.append(str(self.prev_hash))
        return ','.join(fields)

    @property
    def is_sealed(self) -> bool:
        return self.txid is not None

    @property
    def signing_data(self) -> bytes:
        return ','.join([self.data_csv, str(self.txid)]).encode()

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
        Model = (  # noqa: N806
            CoinbaseTransactionModel if coinbase else RegularTransactionModel
        )
        try:
            Model.model_validate(self.to_dict())
        except ValidationError as e:
            raise InvalidTransactionError(pydantic_errors_to_messages(e)) from e
        self.validate_signature()
        self.validate_txid()

    def validate_coinbase(self) -> None:
        self.validate(coinbase=True)

    def to_dict(self) -> dict[str, Any]:
        return asdict_sans_none(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict())

    def to_dao(self) -> TransactionDAO:
        # to_dao() is only meaningful after the txn has been sealed: txid
        # is computed and all in/outflow identity fields are set. The
        # dataclass declares them Optional to allow staged construction;
        # validate them here so mypy strict can narrow at the domain↔DAO
        # boundary AND the persisted row matches the txid signing data
        # (silently dropping inflows or zero-coercing amounts would break
        # both invariants — see PR #47 review).
        if self.txid is None:
            raise UnsealedTransactionError()
        if self.timestamp_dt is None:
            msg = 'Transaction missing timestamp'
            raise InvalidTransactionError(msg)
        for idx, inflow in enumerate(self.inflows):
            if inflow.outflow_txid is None or inflow.outflow_idx is None:
                msg = f'Inflow {idx} missing outflow reference'
                raise InvalidTransactionError(msg)
        for idx, outflow in enumerate(self.outflows):
            if outflow.amount is None:
                msg = f'Outflow {idx} missing amount'
                raise InvalidTransactionError(msg)
        txid = self.txid
        timestamp_dt = self.timestamp_dt
        return TransactionDAO.get(txid) or TransactionDAO(
            txid,
            self.version,
            timestamp_dt,
            address=self.address,
            public_key=self.public_key,
            signature=self.signature,
            prev_hash=self.prev_hash,
            inflow_daos=[
                InflowDAO(txid, idx, inflow.outflow_txid, inflow.outflow_idx)  # type: ignore[arg-type]
                for idx, inflow in enumerate(self.inflows)
            ],
            outflow_daos=[
                OutflowDAO(
                    txid,
                    idx,
                    outflow.amount,  # type: ignore[arg-type]
                    address=outflow.address,
                    opposition=outflow.opposition,
                    rescind=outflow.rescind,
                    support=outflow.support,
                    rescind_kind=outflow.rescind_kind,
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
            model = TransactionModel.model_validate(d)
        except ValidationError as e:
            raise InvalidTransactionError(pydantic_errors_to_messages(e)) from e
        return cls(**txn_from_model_data(model.model_dump()))

    @classmethod
    def from_json(cls, j: str | bytes) -> Self:
        try:
            model = TransactionModel.model_validate_json(j)
        except ValidationError as e:
            raise InvalidTransactionError(pydantic_errors_to_messages(e)) from e
        except JSONDecodeError as e:
            raise InvalidTransactionError(e.msg) from e
        return cls(**txn_from_model_data(model.model_dump()))

    @classmethod
    def from_dao(cls, dao: Any) -> Self:
        return cls(
            timestamp=dt_2_iso(dao.timestamp),
            txid=dao.txid,
            address=dao.address,
            public_key=dao.public_key,
            signature=dao.signature,
            prev_hash=dao.prev_hash,
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
                    opposition=outflow_dao.opposition,
                    rescind=outflow_dao.rescind,
                    support=outflow_dao.support,
                    rescind_kind=cast(
                        'StakeKind | None', outflow_dao.rescind_kind
                    ),
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
        regret: int,
        prev_hash: str,
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
        if regret:
            outflows.append(Outflow(amount=regret, address=wallet.address))
        cb = cls(outflows=outflows, prev_hash=prev_hash)
        cb.set_wallet(wallet)
        cb.seal()
        cb.sign()
        return cb


class PendingTxnSet(MutableSet[Transaction]):
    def __contains__(self, txn: object) -> bool:
        if not isinstance(txn, Transaction) or txn.txid is None:
            return False
        return PendingTxnDAO.get(txn.txid) is not None

    def __iter__(self) -> Generator[Transaction, None, None]:
        return (
            Transaction.from_json(json_data) for json_data in self.query_json()
        )

    def __len__(self) -> int:
        return PendingTxnDAO.count()

    def add(self, txn: Transaction) -> None:
        if txn.txid is None:
            raise UnsealedTransactionError()
        if txn.timestamp_dt is None:
            msg = 'Transaction missing timestamp'
            raise InvalidTransactionError(msg)
        # Validate all in/outflow identity fields BEFORE committing the
        # PendingTxnDAO row so the operation is atomic — otherwise a
        # partial pending row could persist without its spend-tracking
        # PendingIOflowDAO companions, corrupting pending-spend
        # bookkeeping. Mirrors the contract in `Transaction.to_dao()`.
        for idx, inflow in enumerate(txn.inflows):
            if inflow.outflow_txid is None or inflow.outflow_idx is None:
                msg = f'Inflow {idx} missing outflow reference'
                raise InvalidTransactionError(msg)
        for idx, outflow in enumerate(txn.outflows):
            if outflow.amount is None:
                msg = f'Outflow {idx} missing amount'
                raise InvalidTransactionError(msg)
        dao = PendingTxnDAO(
            txid=txn.txid,
            timestamp=txn.timestamp_dt,
            json_data=txn.to_json(),
        )
        dao.commit()
        for inflow in txn.inflows:
            ioflow_txn_dao = TransactionDAO.get(inflow.outflow_txid)  # type: ignore[arg-type]
            if ioflow_txn_dao is None:
                continue
            # outflow_idx may exceed the source txn's outflow count
            # (defensive against post-validation race conditions); if so,
            # skip spend-tracking for this inflow without raising.
            try:
                ioflow_dao = ioflow_txn_dao.outflows[inflow.outflow_idx]  # type: ignore[index]
            except IndexError:
                continue
            if ioflow_dao is None:
                continue
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

    def discard_expired(self, cutoff: datetime) -> int:
        # Bulk-evict every pending txn strictly older than `cutoff` in a
        # single SQL-filtered, single-commit pass (and cascade their
        # ioflow rows). Returns the count removed. Far cheaper than
        # iterating self and calling discard() per txn, which re-parses
        # the whole pool and commits once per eviction.
        return PendingTxnDAO.delete_expired(cutoff)

    def query_json(
        self,
        earliest: datetime | None = None,
        expired: datetime | None = None,
    ) -> Iterator[str]:
        return PendingTxnDAO.json_datas(earliest=earliest, expired=expired)
