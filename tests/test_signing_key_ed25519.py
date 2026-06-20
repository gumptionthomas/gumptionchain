from gumptionchain.signing_key import SigningKey


def test_generate_ed25519_round_trips_and_signs():
    sk = SigningKey.generate_ed25519()
    assert sk.private_key is not None
    data = b'hello consensus'
    sig = sk.sign(data)
    pub = SigningKey(b64ks=sk.public_key_b64)
    assert pub.private_key is None
    assert pub.validate_signature(data, sig) is True
    assert pub.validate_signature(b'tampered', sig) is False


def test_ed25519_address_is_stable_and_tagged():
    sk = SigningKey.generate_ed25519()
    a1 = sk.address
    a2 = SigningKey(b64ks=sk.public_key_b64).address
    assert a1 == a2
    assert a1.startswith('GC') and a1.endswith('GC')
