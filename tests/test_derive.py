import pytest

from gumptionchain.derive import derive_seed

PRF = bytes(range(1, 33))


def test_prf_only_deterministic_32_bytes():
    a = derive_seed(PRF)
    assert len(a) == 32
    assert a == derive_seed(PRF)


def test_passphrase_changes_and_reproduces():
    plain = derive_seed(PRF)
    p1 = derive_seed(PRF, 'hunter2')
    assert p1 == derive_seed(PRF, 'hunter2')
    assert p1 != plain
    assert p1 != derive_seed(PRF, 'different')


def test_different_prf_differs():
    assert derive_seed(PRF) != derive_seed(bytes(range(2, 34)))


def test_empty_prf_rejected():
    with pytest.raises(ValueError, match='PRF'):
        derive_seed(b'')
