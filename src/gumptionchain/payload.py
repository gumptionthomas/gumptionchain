from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass
from typing import Annotated, Self

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from gumptionchain.schema import (
    AddressType,
    MillHashType,
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


def _valid_raw_subject(raw_subject: str) -> bool:
    return (
        MIN_SUBJECT_LENGTH <= len(raw_subject) <= MAX_SUBJECT_LENGTH
        and raw_subject.isprintable()
    )


def validate_subject(subject: str) -> bool:
    try:
        raw_subject = decode_subject(subject)
        if _valid_raw_subject(raw_subject):
            return encode_subject(raw_subject) == subject
    except Exception:
        pass
    return False


def validate_raw_subject(raw_subject: str) -> bool:
    try:
        if _valid_raw_subject(raw_subject):
            return decode_subject(encode_subject(raw_subject)) == raw_subject
    except Exception:
        pass
    return False


def _check_subject(s: str) -> str:
    if not validate_subject(s):
        msg = f'Invalid subject: {truncate(s)!r}'
        raise ValueError(msg)
    return s


Subject = Annotated[str, AfterValidator(_check_subject)]


class OutflowModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

    amount: int = Field(ge=1)
    address: AddressType | None = None
    opposition: Subject | None = None
    rescind: Subject | None = None
    support: Subject | None = None

    @model_validator(mode='after')
    def validate_destinations(self) -> Self:
        options = [
            v
            for v in (self.opposition, self.rescind, self.support)
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


@dataclass
class Outflow:
    amount: int | None = None
    address: str | None = None
    opposition: str | None = None
    rescind: str | None = None
    support: str | None = None

    @property
    def data_csv(self) -> str:
        return ','.join(
            [
                str(self.amount),
                self.address if self.address is not None else '',
                self.opposition if self.opposition is not None else '',
                self.rescind if self.rescind is not None else '',
                self.support if self.support is not None else '',
            ]
        )

    @property
    def schadenfreude(self) -> int:
        if self.opposition is not None and self.amount is not None:
            return int(self.amount / 2)
        return 0

    @property
    def grace(self) -> int:
        if self.rescind is not None and self.amount is not None:
            return int(self.amount / 2)
        return 0

    @property
    def mudita(self) -> int:
        if self.support is not None and self.amount is not None:
            return self.amount
        return 0


@dataclass
class Inflow:
    outflow_txid: str | None = None
    outflow_idx: int | None = None

    @property
    def data_csv(self) -> str:
        return ','.join([str(self.outflow_txid), str(self.outflow_idx)])
