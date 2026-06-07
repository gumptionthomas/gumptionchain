import pytest
from test_browser_wallet_vectors import VECTOR_WALLET_B58

from gumptionchain.attestation import (
    BadAttestationError,
    build_stake_message,
    parse_stake_attestation,
    sign_stake_attestation,
    verify_stake,
)
from gumptionchain.wallet import Wallet

TS = '1700002000'
CLAIM = {
    'txid': 'tx1',
    'kind': 'opposition',
    'subject': 'goblins',
    'amount': 300,
}


def _wallet() -> Wallet:
    return Wallet(b58ks=VECTOR_WALLET_B58)


def _provenance(
    address: str, status: str = 'canonical', confirmations: int = 3
):
    return {
        'txid': 'tx1',
        'address': address,
        'status': status,
        'confirmations': confirmations,
        'outflows': [
            {'kind': 'opposition', 'subject': 'goblins', 'amount': 300},
            {'kind': 'transfer', 'address': 'GCchangeGC', 'amount': 9700},
        ],
    }


def test_build_stake_message_order_and_omission() -> None:
    assert build_stake_message(CLAIM) == (
        '{"txid":"tx1","kind":"opposition","subject":"goblins","amount":300}'
    )
    assert build_stake_message({**CLAIM, 'handle': 'me.bsky.social'}) == (
        '{"txid":"tx1","kind":"opposition","subject":"goblins","amount":300,'
        '"handle":"me.bsky.social"}'
    )


def test_build_stake_message_rejects_malformed() -> None:
    with pytest.raises(BadAttestationError):
        build_stake_message({'kind': 'opposition', 'subject': 's', 'amount': 1})
    with pytest.raises(BadAttestationError):
        build_stake_message(
            {'txid': 't', 'kind': 'x', 'subject': 's', 'amount': 1}
        )
    with pytest.raises(BadAttestationError):
        build_stake_message(
            {'txid': 't', 'kind': 'opposition', 'subject': 's', 'amount': 0}
        )


def test_sign_then_parse_round_trips() -> None:
    proof = sign_stake_attestation(_wallet(), CLAIM, timestamp=int(TS))
    assert parse_stake_attestation(proof) == CLAIM


def test_verify_stake_valid() -> None:
    w = _wallet()
    proof = sign_stake_attestation(w, CLAIM, timestamp=int(TS))
    v = verify_stake(proof, lambda _txid: _provenance(w.address))
    assert v['valid'] is True
    assert v['checks'] == {
        'signature': True,
        'onchain': True,
        'consistent': True,
    }
    assert v['signer'] == w.address
    assert v['confirmations'] == 3
    assert v['reasons'] == []


def test_verify_stake_failure_reasons() -> None:
    w = _wallet()
    proof = sign_stake_attestation(w, CLAIM, timestamp=int(TS))

    assert 'txn-not-found' in verify_stake(proof, lambda _t: None)['reasons']
    assert (
        'not-canonical'
        in verify_stake(
            proof, lambda _t: _provenance(w.address, status='pending')
        )['reasons']
    )
    assert (
        'insufficient-confirmations'
        in verify_stake(
            proof,
            lambda _t: _provenance(w.address, confirmations=1),
            min_confirmations=6,
        )['reasons']
    )
    assert (
        'signer-not-staker'
        in verify_stake(proof, lambda _t: _provenance('GCotherGC'))['reasons']
    )

    bad = dict(proof)
    bad['message'] = build_stake_message({**CLAIM, 'amount': 999})
    assert (
        'bad-signature'
        in verify_stake(bad, lambda _t: _provenance(w.address))['reasons']
    )

    mismatch = {
        'txid': 'tx1',
        'address': w.address,
        'status': 'canonical',
        'confirmations': 3,
        'outflows': [{'kind': 'opposition', 'subject': 'orcs', 'amount': 300}],
    }
    assert (
        'claim-mismatch' in verify_stake(proof, lambda _t: mismatch)['reasons']
    )


def test_parse_rejects_non_claim() -> None:
    with pytest.raises(BadAttestationError):
        parse_stake_attestation({'message': 'not json'})


def test_parse_rejects_non_canonical() -> None:
    # float amount (json.loads -> 300.0): non-int / non-canonical
    with pytest.raises(BadAttestationError):
        parse_stake_attestation(
            {
                'message': '{"txid":"tx1","kind":"opposition",'
                '"subject":"goblins","amount":300.0}'
            }
        )
    # reordered keys -> non-canonical encoding
    with pytest.raises(BadAttestationError):
        parse_stake_attestation(
            {
                'message': '{"kind":"opposition","txid":"tx1",'
                '"subject":"goblins","amount":300}'
            }
        )


def test_validate_rejects_present_offside_key() -> None:
    # off-side key present (even as None) is rejected, matching JS
    with pytest.raises(BadAttestationError):
        build_stake_message(
            {
                'txid': 't',
                'kind': 'transfer',
                'address': 'a',
                'amount': 1,
                'subject': None,
            }
        )
    with pytest.raises(BadAttestationError):
        build_stake_message(
            {
                'txid': 't',
                'kind': 'opposition',
                'subject': 's',
                'amount': 1,
                'address': None,
            }
        )


def test_verify_stake_wraps_bad_proof_envelope() -> None:
    w = _wallet()
    proof = sign_stake_attestation(w, CLAIM, timestamp=int(TS))
    proof['scheme'] = 'gc-sig-v1'  # malformed gc-msg-v1 envelope
    with pytest.raises(BadAttestationError):
        verify_stake(proof, lambda _t: _provenance(w.address))
