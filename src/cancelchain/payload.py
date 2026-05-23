from base64 import urlsafe_b64decode, urlsafe_b64encode
from dataclasses import dataclass

from marshmallow import (
    ValidationError,
    fields,
    post_load,
    validate,
    validates_schema,
)

from cancelchain.schema import Address, MillHash, SansNoneSchema

MIN_SUBJECT_LENGTH = 1
MAX_SUBJECT_LENGTH = 79
INVALID_DESTINATION_MSG = 'Invalid destinations'
INVALID_PADDING_MSG = 'Invalid padding'


def encode_subject(raw_subject):
    return urlsafe_b64encode(raw_subject.encode()).rstrip(b'=').decode()


def decode_subject(subject):
    if subject.endswith('='):
        raise TypeError(INVALID_PADDING_MSG)
    subject = subject.encode()
    subject += b'=' * (-len(subject) % 4)
    return urlsafe_b64decode(subject).decode()


def validate_subject(subject):
    try:
        raw_subject = decode_subject(subject)
        if MIN_SUBJECT_LENGTH <= len(raw_subject) <= MAX_SUBJECT_LENGTH:
            return encode_subject(raw_subject) == subject
    except Exception:
        pass
    return False


def validate_raw_subject(raw_subject):
    try:
        if MIN_SUBJECT_LENGTH <= len(raw_subject) <= MAX_SUBJECT_LENGTH:
            return decode_subject(encode_subject(raw_subject)) == raw_subject
    except Exception:
        pass
    return False


class Subject(fields.String):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.validators.insert(0, validate_subject)


class OutflowSchema(SansNoneSchema):
    amount = fields.Integer(required=True, validate=validate.Range(min=1))
    address = Address()
    subject = Subject()
    forgive = Subject()
    support = Subject()

    @validates_schema
    def validate_destinations(self, data, **kwargs):
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
    def make_outflow(self, data, **kwargs):
        return Outflow(**data)


@dataclass
class Outflow:
    amount: int = None
    address: str = None
    subject: str = None
    forgive: str = None
    support: str = None

    @property
    def data_csv(self):
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
    def schadenfreude(self):
        return int(self.amount / 2) if self.subject is not None else 0

    @property
    def grace(self):
        return int(self.amount / 2) if self.forgive is not None else 0

    @property
    def mudita(self):
        return self.amount if self.support is not None else 0


class InflowSchema(SansNoneSchema):
    outflow_txid = MillHash(required=True)
    outflow_idx = fields.Integer(required=True, validate=validate.Range(min=0))

    @post_load
    def make_inflow(self, data, **kwargs):
        return Inflow(**data)


@dataclass
class Inflow:
    outflow_txid: str = None
    outflow_idx: int = None

    @property
    def data_csv(self):
        return ','.join([self.outflow_txid, str(self.outflow_idx)])
