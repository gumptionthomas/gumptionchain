from base64 import b64encode, urlsafe_b64encode

import pytest
from pydantic import ValidationError as PydanticValidationError

from cancelchain.payload import (
    INVALID_DESTINATION_MSG,
    Inflow,
    InflowModel,
    Outflow,
    OutflowModel,
    encode_subject,
    validate_subject,
)


def test_outflow_data_csv(subject, wallet):
    outflow = Outflow(amount=9, address=wallet.address)
    assert outflow.data_csv == f'9,{wallet.address},,,'
    outflow = Outflow(amount=9, subject=subject)
    assert outflow.data_csv == f'9,,{subject},,'
    outflow = Outflow(amount=9, forgive=subject)
    assert outflow.data_csv == f'9,,,{subject},'
    outflow = Outflow(amount=9, support=subject)
    assert outflow.data_csv == f'9,,,,{subject}'


def test_outflow_schadenfreude(subject):
    outflow = Outflow(amount=9, subject=subject)
    assert outflow.schadenfreude == 4


def test_outflow_grace(subject):
    outflow = Outflow(amount=9, forgive=subject)
    assert outflow.grace == 4


def test_outflow_mudita(subject):
    outflow = Outflow(amount=9, support=subject)
    assert outflow.mudita == 9


def test_inflow_data_csv(txid):
    inflow = Inflow(outflow_txid=txid, outflow_idx=0)
    assert inflow.data_csv == f'{txid},0'


def test_validate_subject(subject_raw, subject):
    assert validate_subject(subject)
    subject_urlsafe = urlsafe_b64encode(subject_raw.encode()).decode()
    assert subject_urlsafe.endswith('=')
    assert not validate_subject(subject_urlsafe)


# ---------------------------------------------------------------------------
# OutflowModel / InflowModel Pydantic v2 tests
# ---------------------------------------------------------------------------

VALID_MILL_HASH = b64encode(b'A' * 48).decode()  # 48 bytes → 64 base64 chars


def test_outflow_model_accepts_address_only(wallet):
    m = OutflowModel(amount=10, address=wallet.address)
    assert m.amount == 10
    assert m.address == wallet.address
    assert m.subject is None
    assert m.forgive is None
    assert m.support is None


def test_outflow_model_accepts_subject_only():
    subject = encode_subject('cancel me')
    m = OutflowModel(amount=5, subject=subject)
    assert m.subject == subject
    assert m.address is None


def test_outflow_model_accepts_forgive_only():
    subject = encode_subject('forgiven one')
    m = OutflowModel(amount=3, forgive=subject)
    assert m.forgive == subject
    assert m.address is None


def test_outflow_model_accepts_support_only():
    subject = encode_subject('supported one')
    m = OutflowModel(amount=7, support=subject)
    assert m.support == subject
    assert m.address is None


def test_outflow_model_rejects_address_and_subject(wallet):
    subject = encode_subject('test')
    with pytest.raises(PydanticValidationError) as exc_info:
        OutflowModel(amount=5, address=wallet.address, subject=subject)
    messages = str(exc_info.value)
    assert INVALID_DESTINATION_MSG in messages


def test_outflow_model_rejects_two_subject_options():
    subj = encode_subject('test')
    with pytest.raises(PydanticValidationError) as exc_info:
        OutflowModel(amount=5, subject=subj, forgive=subj)
    messages = str(exc_info.value)
    assert INVALID_DESTINATION_MSG in messages


def test_outflow_model_rejects_no_destination(wallet):
    with pytest.raises(PydanticValidationError) as exc_info:
        OutflowModel(amount=5)
    messages = str(exc_info.value)
    assert INVALID_DESTINATION_MSG in messages


def test_outflow_model_rejects_zero_amount(wallet):
    with pytest.raises(PydanticValidationError):
        OutflowModel(amount=0, address=wallet.address)


def test_inflow_model_accepts_valid():
    m = InflowModel(outflow_txid=VALID_MILL_HASH, outflow_idx=0)
    assert m.outflow_txid == VALID_MILL_HASH
    assert m.outflow_idx == 0


def test_inflow_model_rejects_negative_idx():
    with pytest.raises(PydanticValidationError):
        InflowModel(outflow_txid=VALID_MILL_HASH, outflow_idx=-1)


def test_inflow_model_rejects_invalid_mill_hash():
    with pytest.raises(PydanticValidationError):
        InflowModel(outflow_txid='not-a-hash', outflow_idx=0)
