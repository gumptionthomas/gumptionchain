import time

import pytest
from test_browser_wallet_vectors import VECTOR_WALLET_B58

from gumptionchain.attestation import (
    BadAttestationError,
    build_binding_message,
    build_stake_message,
    parse_social_binding,
    parse_stake_attestation,
    sign_social_binding,
    sign_stake_attestation,
    verify_binding,
    verify_stake,
)
from gumptionchain.wallet import Wallet

TS = '1700002000'
# A canonical txid is a 64-char lowercase-hex mill hash.
TX = '1' * 64
CLAIM = {
    'txid': TX,
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
        'txid': TX,
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
        f'{{"txid":"{TX}","kind":"opposition","subject":"goblins","amount":300}}'
    )
    assert build_stake_message({**CLAIM, 'handle': 'me.bsky.social'}) == (
        f'{{"txid":"{TX}","kind":"opposition","subject":"goblins","amount":300,'
        '"handle":"me.bsky.social"}'
    )


def test_build_stake_message_rejects_malformed() -> None:
    with pytest.raises(BadAttestationError):
        build_stake_message({'kind': 'opposition', 'subject': 's', 'amount': 1})
    with pytest.raises(BadAttestationError):
        build_stake_message(
            {'txid': TX, 'kind': 'x', 'subject': 's', 'amount': 1}
        )
    with pytest.raises(BadAttestationError):
        build_stake_message(
            {'txid': TX, 'kind': 'opposition', 'subject': 's', 'amount': 0}
        )


def test_build_stake_message_rejects_malformed_txid() -> None:
    # Not 64-char lowercase hex -> rejected up front as a bad attestation,
    # rather than slipping through to a provenance fetch (see #187).
    for bad in (
        'tx1',  # too short + non-hex 'x'
        'g' * 64,  # right length, non-hex char
        '1' * 63,  # too short
        '1' * 65,  # too long
        'A' * 64,  # uppercase hex is not the canonical form
        '1' * 63 + '/',  # path metacharacter
    ):
        with pytest.raises(BadAttestationError):
            build_stake_message({**CLAIM, 'txid': bad})
    # A valid 64-hex txid is accepted.
    assert build_stake_message({**CLAIM, 'txid': 'a' * 64}).startswith(
        f'{{"txid":"{"a" * 64}"'
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
        'txid': TX,
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
                'message': f'{{"txid":"{TX}","kind":"opposition",'
                '"subject":"goblins","amount":300.0}'
            }
        )
    # reordered keys -> non-canonical encoding
    with pytest.raises(BadAttestationError):
        parse_stake_attestation(
            {
                'message': f'{{"kind":"opposition","txid":"{TX}",'
                '"subject":"goblins","amount":300}'
            }
        )


def test_validate_rejects_present_offside_key() -> None:
    # off-side key present (even as None) is rejected, matching JS
    with pytest.raises(BadAttestationError):
        build_stake_message(
            {
                'txid': TX,
                'kind': 'transfer',
                'address': 'a',
                'amount': 1,
                'subject': None,
            }
        )
    with pytest.raises(BadAttestationError):
        build_stake_message(
            {
                'txid': TX,
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


# ---------------------------------------------------------------------------
# Social-binding envelope tests (#251)
# ---------------------------------------------------------------------------

BINDING_CLAIM = {'platform': 'github', 'handle': 'gumptionthomas'}


def test_build_binding_message_minimal() -> None:
    assert (
        build_binding_message(BINDING_CLAIM)
        == '{"platform":"github","handle":"gumptionthomas"}'
    )


def test_build_binding_message_with_proof_url() -> None:
    claim = {
        'platform': 'web',
        'handle': 'example.com',
        'proof_url': 'https://example.com/gc.txt',
    }
    assert build_binding_message(claim) == (
        '{"platform":"web","handle":"example.com",'
        '"proof_url":"https://example.com/gc.txt"}'
    )


def test_build_binding_message_utf8_unescaped() -> None:
    msg = build_binding_message({'platform': 'web', 'handle': 'tøm'})
    assert 'tøm' in msg
    assert '\\u' not in msg


def test_build_binding_message_rejects_non_dict() -> None:
    with pytest.raises(BadAttestationError):
        build_binding_message('github:gumptionthomas')  # type: ignore[arg-type]


def test_build_binding_message_rejects_bad_platform() -> None:
    # uppercase
    with pytest.raises(BadAttestationError):
        build_binding_message({'platform': 'GitHub', 'handle': 'x'})
    # 33 chars (too long)
    with pytest.raises(BadAttestationError):
        build_binding_message({'platform': 'a' * 33, 'handle': 'x'})
    # empty
    with pytest.raises(BadAttestationError):
        build_binding_message({'platform': '', 'handle': 'x'})
    # missing
    with pytest.raises(BadAttestationError):
        build_binding_message({'handle': 'x'})
    # invalid char (underscore)
    with pytest.raises(BadAttestationError):
        build_binding_message({'platform': 'git_hub', 'handle': 'x'})


def test_build_binding_message_rejects_bad_handle() -> None:
    # empty
    with pytest.raises(BadAttestationError):
        build_binding_message({'platform': 'github', 'handle': ''})
    # missing
    with pytest.raises(BadAttestationError):
        build_binding_message({'platform': 'github'})
    # 257 chars (too long)
    with pytest.raises(BadAttestationError):
        build_binding_message({'platform': 'github', 'handle': 'h' * 257})
    # non-string
    with pytest.raises(BadAttestationError):
        build_binding_message({'platform': 'github', 'handle': 123})  # type: ignore[dict-item]


def test_build_binding_message_rejects_bad_proof_url() -> None:
    # non-string
    with pytest.raises(BadAttestationError):
        build_binding_message(
            {'platform': 'github', 'handle': 'x', 'proof_url': 42}  # type: ignore[dict-item]
        )
    # http (not https)
    with pytest.raises(BadAttestationError):
        build_binding_message(
            {
                'platform': 'github',
                'handle': 'x',
                'proof_url': 'http://insecure.example',
            }
        )
    # 513 chars ('https://' + 'a'*505 = 513 total)
    with pytest.raises(BadAttestationError):
        build_binding_message(
            {
                'platform': 'github',
                'handle': 'x',
                'proof_url': 'https://' + 'a' * 505,
            }
        )
    # empty string
    with pytest.raises(BadAttestationError):
        build_binding_message(
            {'platform': 'github', 'handle': 'x', 'proof_url': ''}
        )


def test_build_binding_message_boundary_accepted() -> None:
    # handle exactly 256 chars is accepted
    assert build_binding_message(
        {'platform': 'github', 'handle': 'h' * 256}
    ).startswith('{"platform":"github"')
    # proof_url exactly 512 chars is accepted ('https://' + 'a'*504 = 512)
    assert build_binding_message(
        {
            'platform': 'github',
            'handle': 'x',
            'proof_url': 'https://' + 'a' * 504,
        }
    ).startswith('{"platform":"github"')


def test_build_binding_message_proof_url_none_omitted() -> None:
    # proof_url None is accepted and omitted from canonical (same as absent)
    msg = build_binding_message(
        {'platform': 'github', 'handle': 'gumptionthomas', 'proof_url': None}
    )
    assert msg == '{"platform":"github","handle":"gumptionthomas"}'


def test_sign_parse_binding_round_trip() -> None:
    w = _wallet()
    proof = sign_social_binding(w, BINDING_CLAIM)
    assert parse_social_binding(proof) == BINDING_CLAIM


def test_parse_binding_rejects_no_message() -> None:
    with pytest.raises(BadAttestationError):
        parse_social_binding({'address': 'x'})


def test_parse_binding_rejects_non_json_message() -> None:
    with pytest.raises(BadAttestationError):
        parse_social_binding({'message': 'not json'})


def test_parse_binding_rejects_extra_key() -> None:
    with pytest.raises(BadAttestationError):
        parse_social_binding(
            {'message': '{"platform":"github","handle":"x","txid":"abc"}'}
        )


def test_parse_binding_rejects_reordered_keys() -> None:
    with pytest.raises(BadAttestationError):
        parse_social_binding({'message': '{"handle":"x","platform":"github"}'})


def test_parse_binding_rejects_whitespace() -> None:
    with pytest.raises(BadAttestationError):
        parse_social_binding({'message': '{"platform": "github","handle":"x"}'})


def test_parse_binding_rejects_unicode_escaped() -> None:
    # ø is the escaped form of ø — non-canonical since we use ensure_ascii=False
    with pytest.raises(BadAttestationError):
        parse_social_binding(
            {'message': '{"platform":"github","handle":"t\\u00f8m"}'}
        )


def test_binding_domain_separation() -> None:
    w = _wallet()
    binding_proof = sign_social_binding(w, BINDING_CLAIM)
    stake_proof = sign_stake_attestation(w, CLAIM)
    # stake parser must reject a binding proof
    with pytest.raises(BadAttestationError):
        parse_stake_attestation(binding_proof)
    # binding parser must reject a stake proof
    with pytest.raises(BadAttestationError):
        parse_social_binding(stake_proof)


# ---------------------------------------------------------------------------
# verify_binding tests (#251)
# ---------------------------------------------------------------------------

TS_BINDING = 1700002000


def test_verify_binding_valid() -> None:
    w = _wallet()
    proof = sign_social_binding(w, BINDING_CLAIM, timestamp=TS_BINDING)
    verdict = verify_binding(proof)
    assert verdict == {
        'valid': True,
        'checks': {'signature': True},
        'signer': w.address,
        'claim': {'platform': 'github', 'handle': 'gumptionthomas'},
        'reasons': [],
    }


def test_verify_binding_signer_from_proof_address() -> None:
    w = _wallet()
    proof = sign_social_binding(w, BINDING_CLAIM, timestamp=TS_BINDING)
    verdict = verify_binding(proof)
    assert verdict['signer'] == proof['address']


def test_verify_binding_tampered_message() -> None:
    w = _wallet()
    proof = sign_social_binding(w, BINDING_CLAIM, timestamp=TS_BINDING)
    # Swap message to a different canonical binding message (parse still passes)
    other_claim = {'platform': 'github', 'handle': 'otheruser'}
    proof = dict(proof)
    proof['message'] = build_binding_message(other_claim)
    verdict = verify_binding(proof)
    assert verdict['valid'] is False
    assert verdict['checks']['signature'] is False
    assert verdict['reasons'] == ['bad-signature']


def test_verify_binding_expired() -> None:
    w = _wallet()
    # Sign with a timestamp 1000 s in the past; max_age=300 must mark it expired
    stale_ts = int(time.time()) - 1000
    stale_proof = sign_social_binding(w, BINDING_CLAIM, timestamp=stale_ts)
    verdict = verify_binding(stale_proof, max_age=300)
    assert verdict['valid'] is False
    assert verdict['reasons'] == ['expired']


def test_verify_binding_malformed_envelope_raises() -> None:
    w = _wallet()
    proof = sign_social_binding(w, BINDING_CLAIM, timestamp=TS_BINDING)
    bad = dict(proof)
    del bad['public_key']
    with pytest.raises(BadAttestationError):
        verify_binding(bad)


def test_verify_binding_rejects_non_binding_proof() -> None:
    # A stake proof passed to verify_binding must raise BadAttestationError
    # (the parse_social_binding step rejects the claim shape)
    w = _wallet()
    stake_proof = sign_stake_attestation(w, CLAIM, timestamp=TS_BINDING)
    with pytest.raises(BadAttestationError):
        verify_binding(stake_proof)
