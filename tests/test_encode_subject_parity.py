"""Lock the JS encodeSubject literals (in static/js/transact-glue.test.mjs) to
Python's payload.encode_subject. /verify compares an attestation's claim
subject against on-chain provenance, whose subject is the ENCODED form, so the
attestation signer must encode the subject identically to the chain.
"""

from gumptionchain.payload import encode_subject


def test_encode_subject_matches_js_literals():
    # These exact strings are asserted in the JS test
    # (src/gumptionchain/static/js/transact-glue.test.mjs). Keep them in sync.
    assert encode_subject('goblins') == 'Z29ibGlucw'
    assert encode_subject('cancel me') == 'Y2FuY2VsIG1l'
    # A multi-byte (UTF-8) subject exercises base64url over non-ASCII bytes.
    assert encode_subject('café') == 'Y2Fmw6k'


def test_encode_subject_is_base64url_without_padding():
    # base64url alphabet ('-'/'_'), and no '=' padding.
    encoded = encode_subject('the quick brown fox????')
    assert '+' not in encoded
    assert '/' not in encoded
    assert '=' not in encoded
