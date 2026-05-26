from __future__ import annotations

# mypy: disable-error-code="no-untyped-call,no-any-return"
from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass
from typing import Annotated, Any, Self

from marshmallow import (
    ValidationError,
    fields,
    post_load,
    validate,
    validates_schema,
)
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from cancelchain.schema import (
    Address,
    AddressType,
    MillHash,
    MillHashType,
    SansNoneSchema,
    truncate,
)

MIN_SUBJECT_LENGTH = 1
MAX_SUBJECT_LENGTH = 79
INVALID_DESTINATION_MSG = 'Invalid destinations'
INVALID_PADDING_MSG = 'Invalid padding'


def encode_subject(raw_subject: str) -> str:
    return urlsafe_b64encode(raw_subject.encode()).rstrip(b'=').decode()


def decode_subject(subject: str) -> str:
    if subject.endswith('='):
        raise TypeError(INVALID_PADDING_MSG)
    subject_bytes = subject.encode()
    subject_bytes += b'=' * (-len(subject_bytes) % 4)
    return urlsafe_b64decode(subject_bytes).decode()


def validate_subject(subject: str) -> bool:
    try:
        raw_subject = decode_subject(subject)
        if MIN_SUBJECT_LENGTH <= len(raw_subject) <= MAX_SUBJECT_LENGTH:
            return encode_subject(raw_subject) == subject
    except Exception:
        pass
    return False


def validate_raw_subject(raw_subject: str) -> bool:
    try:
        if MIN_SUBJECT_LENGTH <= len(raw_subject) <= MAX_SUBJECT_LENGTH:
            return decode_subject(encode_subject(raw_subject)) == raw_subject
    except Exception:
        pass
    return False


class Subject(fields.String):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.validators.insert(0, validate_subject)


class OutflowSchema(SansNoneSchema):
    amount = fields.Integer(required=True, validate=validate.Range(min=1))
    address = Address()
    subject = Subject()
    forgive = Subject()
    support = Subject()

    @validates_schema
    def validate_destinations(
        self, data: dict[str, Any], **kwargs: Any
    ) -> None:
        address = data.get('address')
        options = [
            v
            for v in [data.get(n) for n in ('subject', 'forgive', 'support')]
            if v is not None
        ]
        if not (
            (address and not options)
            or (options and len(options) == 1 and not address)
        ):
            raise ValidationError(INVALID_DESTINATION_MSG)

    @post_load
    def make_outflow(self, data: dict[str, Any], **kwargs: Any) -> Outflow:
        return Outflow(**data)


@dataclass
class Outflow:
    amount: int | None = None
    address: str | None = None
    subject: str | None = None
    forgive: str | None = None
    support: str | None = None

    @property
    def data_csv(self) -> str:
        return ','.join(
            [
                str(self.amount),
                self.address if self.address is not None else '',
                self.subject if self.subject is not None else '',
                self.forgive if self.forgive is not None else '',
                self.support if self.support is not None else '',
            ]
        )

    @property
    def schadenfreude(self) -> int:
        if self.subject is not None and self.amount is not None:
            return int(self.amount / 2)
        return 0

    @property
    def grace(self) -> int:
        if self.forgive is not None and self.amount is not None:
            return int(self.amount / 2)
        return 0

    @property
    def mudita(self) -> int:
        if self.support is not None and self.amount is not None:
            return self.amount
        return 0


class InflowSchema(SansNoneSchema):
    outflow_txid = MillHash(required=True)
    outflow_idx = fields.Integer(required=True, validate=validate.Range(min=0))

    @post_load
    def make_inflow(self, data: dict[str, Any], **kwargs: Any) -> Inflow:
        return Inflow(**data)


@dataclass
class Inflow:
    outflow_txid: str | None = None
    outflow_idx: int | None = None

    @property
    def data_csv(self) -> str:
        return ','.join([str(self.outflow_txid), str(self.outflow_idx)])


# --- Pydantic v2 models (used by PR-3 onwards). The Marshmallow
# Schemas above stay in place until PR-3 swaps transaction.py.


def _check_subject(s: str) -> str:
    if not validate_subject(s):
        msg = f'Invalid subject: {truncate(s)!r}'
        raise ValueError(msg)
    return s


SubjectType = Annotated[str, AfterValidator(_check_subject)]


class OutflowModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

    amount: int = Field(ge=1)
    address: AddressType | None = None
    subject: SubjectType | None = None
    forgive: SubjectType | None = None
    support: SubjectType | None = None

    @model_validator(mode='after')
    def validate_destinations(self) -> Self:
        options = [
            v
            for v in (self.subject, self.forgive, self.support)
            if v is not None
        ]
        if not (
            (self.address and not options)
            or (options and len(options) == 1 and not self.address)
        ):
            raise ValueError(INVALID_DESTINATION_MSG)
        return self


class InflowModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

    outflow_txid: MillHashType
    outflow_idx: int = Field(ge=0)
