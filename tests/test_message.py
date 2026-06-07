import pytest
from test_browser_wallet_vectors import VECTOR_WALLET_B58

from gumptionchain.message import (
    BadProofError,
    from_armored,
    sign_message,
    to_armored,
    verify_message,
)
from gumptionchain.signing import _canonical
from gumptionchain.wallet import Wallet

TS = '1700001000'


def _wallet() -> Wallet:
    return Wallet(b58ks=VECTOR_WALLET_B58)


def test_sign_then_verify_is_valid() -> None:
    w = _wallet()
    proof = sign_message(w, 'I made stake T1', timestamp=int(TS))
    assert proof['scheme'] == 'gc-msg-v1'
    assert proof['address'] == w.address
    r = verify_message(proof)
    assert r == {
        'valid': True,
        'address': w.address,
        'timestamp': TS,
        'message': 'I made stake T1',
    }


def test_tampered_message_is_bad_signature() -> None:
    proof = sign_message(_wallet(), 'original', timestamp=int(TS))
    proof['message'] = 'forged'
    r = verify_message(proof)
    assert r['valid'] is False
    assert r['reason'] == 'bad-signature'


def test_address_mismatch() -> None:
    proof = sign_message(_wallet(), 'hi', timestamp=int(TS))
    proof['address'] = proof['address'] + 'X'
    r = verify_message(proof)
    assert r['valid'] is False
    assert r['reason'] == 'address-mismatch'


def test_malformed_proof_raises() -> None:
    good = sign_message(_wallet(), 'hi', timestamp=int(TS))
    with pytest.raises(BadProofError):
        verify_message({**good, 'scheme': 'gc-sig-v1'})
    with pytest.raises(BadProofError):
        verify_message({'scheme': 'gc-msg-v1', 'version': '1'})
    with pytest.raises(BadProofError):
        verify_message(None)


def test_max_age_enforces_freshness() -> None:
    proof = sign_message(_wallet(), 'hi', timestamp=int(TS))
    now = int(TS) + 1000
    stale = verify_message(proof, max_age=300, now=now)
    assert stale['valid'] is False
    assert stale['reason'] == 'expired'
    assert verify_message(proof, max_age=5000, now=now)['valid'] is True


def test_non_base64_signature_is_bad_signature() -> None:
    proof = sign_message(_wallet(), 'hi', timestamp=int(TS))
    proof['signature'] = '!!! not base64 !!!'
    r = verify_message(proof)
    assert r['valid'] is False
    assert r['reason'] == 'bad-signature'


def test_non_numeric_timestamp_is_malformed() -> None:
    proof = sign_message(_wallet(), 'hi', timestamp=int(TS))
    proof['timestamp'] = 'notanumber'
    with pytest.raises(BadProofError):
        verify_message(proof)
    with pytest.raises(BadProofError):
        verify_message(proof, max_age=300, now=9999999999)


def test_domain_separation_from_gc_sig() -> None:
    # A gc-msg-v1 signature must not validate as a gc-sig-v1 canonical.
    w = _wallet()
    proof = sign_message(w, 'hi', timestamp=int(TS))
    sig_canonical = _canonical(
        method='GET',
        path='/',
        query='',
        body=b'',
        node_host='n',
        timestamp=TS,
        address=w.address,
    )
    assert not w.validate_signature(sig_canonical, proof['signature'])


def test_armored_round_trip() -> None:
    w = _wallet()
    proof = sign_message(w, 'multi\nline\nmessage', timestamp=int(TS))
    armored = to_armored(proof)
    assert armored.startswith('-----BEGIN GUMPTION SIGNED MESSAGE-----')
    back = from_armored(armored)
    assert back == proof
    assert verify_message(back)['valid'] is True


def test_from_armored_rejects_malformed_and_mismatch() -> None:
    proof = sign_message(_wallet(), 'hello', timestamp=int(TS))
    with pytest.raises(BadProofError):
        from_armored('not armored at all')
    tampered = to_armored(proof).replace('hello', 'goodbye')
    with pytest.raises(BadProofError):
        from_armored(tampered)
