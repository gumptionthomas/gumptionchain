from base64 import b64encode, urlsafe_b64encode

import pytest
from pydantic import ValidationError as PydanticValidationError

from gumptionchain.payload import (
    INVALID_DESTINATION_MSG,
    Inflow,
    InflowModel,
    Outflow,
    OutflowModel,
    _valid_raw_subject,
    encode_subject,
    validate_raw_subject,
    validate_subject,
)


def test_outflow_data_csv(subject, wallet):
    outflow = Outflow(amount=9, address=wallet.address)
    assert outflow.data_csv == f'9,{wallet.address},,,,'
    outflow = Outflow(amount=9, opposition=subject)
    assert outflow.data_csv == f'9,,{subject},,,'
    outflow = Outflow(amount=9, rescind=subject, rescind_kind='opposition')
    assert outflow.data_csv == f'9,,,{subject},,opposition'
    outflow = Outflow(amount=9, support=subject)
    assert outflow.data_csv == f'9,,,,{subject},'


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
    assert m.opposition is None
    assert m.rescind is None
    assert m.support is None


def test_outflow_model_accepts_opposition_only():
    subject = encode_subject('cancel me')
    m = OutflowModel(amount=5, opposition=subject)
    assert m.opposition == subject
    assert m.address is None


def test_outflow_model_accepts_rescind_only():
    subject = encode_subject('forgiven one')
    m = OutflowModel(amount=3, rescind=subject, rescind_kind='opposition')
    assert m.rescind == subject
    assert m.address is None


def test_outflow_model_accepts_support_only():
    subject = encode_subject('supported one')
    m = OutflowModel(amount=7, support=subject)
    assert m.support == subject
    assert m.address is None


def test_outflow_model_rejects_address_and_opposition(wallet):
    subject = encode_subject('test')
    with pytest.raises(PydanticValidationError) as exc_info:
        OutflowModel(amount=5, address=wallet.address, opposition=subject)
    assert any(
        INVALID_DESTINATION_MSG in err['msg'] for err in exc_info.value.errors()
    )


def test_outflow_model_rejects_two_opposition_options():
    subj = encode_subject('test')
    with pytest.raises(PydanticValidationError) as exc_info:
        OutflowModel(amount=5, opposition=subj, rescind=subj)
    assert any(
        INVALID_DESTINATION_MSG in err['msg'] for err in exc_info.value.errors()
    )


def test_outflow_model_rejects_no_destination(wallet):
    with pytest.raises(PydanticValidationError) as exc_info:
        OutflowModel(amount=5)
    assert any(
        INVALID_DESTINATION_MSG in err['msg'] for err in exc_info.value.errors()
    )


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


def test_validate_subject_rejects_non_printable():
    assert validate_subject(encode_subject('\x1b[31mRED')) is False  # ESC, Cc
    assert validate_subject(encode_subject('a\u202eb')) is False  # RLO, Cf
    assert validate_subject(encode_subject('a\u200db')) is False  # ZWJ, Cf
    assert validate_subject(encode_subject('a\u200bb')) is False  # ZWSP, Cf
    assert validate_subject(encode_subject('a\tb')) is False  # tab, Cc
    assert validate_subject(encode_subject('a\nb')) is False  # newline, Cc


def test_validate_subject_accepts_printable():
    assert validate_subject(encode_subject('Acme Corp')) is True
    assert validate_subject(encode_subject('café')) is True
    assert validate_subject(encode_subject('🍎')) is True


def test_validate_raw_subject_rejects_non_printable():
    assert validate_raw_subject('\x1b[31mRED') is False
    assert validate_raw_subject('a\u202eb') is False
    assert validate_raw_subject('a\u200db') is False
    assert validate_raw_subject('a\u200bb') is False
    assert validate_raw_subject('a\tb') is False
    assert validate_raw_subject('a\nb') is False


def test_validate_raw_subject_accepts_printable():
    assert validate_raw_subject('Acme Corp') is True
    assert validate_raw_subject('café') is True
    assert validate_raw_subject('🍎') is True


def test_valid_raw_subject_helper():
    assert _valid_raw_subject('Acme Corp') is True
    assert _valid_raw_subject('x' * 79) is True
    assert _valid_raw_subject('\x1b') is False  # control char
    assert _valid_raw_subject('') is False  # below min length
    assert _valid_raw_subject('x' * 80) is False  # above max length


def test_outflow_rescind_carries_kind():
    o = Outflow(amount=10, rescind='Zm9v', rescind_kind='opposition')
    assert o.rescind == 'Zm9v'
    assert o.rescind_kind == 'opposition'


def test_outflow_model_rescind_requires_kind():
    with pytest.raises(PydanticValidationError):
        OutflowModel(amount=10, rescind='Zm9v')  # missing rescind_kind


def test_outflow_model_rescind_kind_requires_rescind():
    subject = encode_subject('cancel me')
    with pytest.raises(PydanticValidationError):
        OutflowModel(amount=10, opposition=subject, rescind_kind='opposition')


def test_outflow_model_accepts_rescind_with_kind():
    m = OutflowModel(amount=10, rescind='Zm9v', rescind_kind='support')
    assert m.rescind_kind == 'support'
