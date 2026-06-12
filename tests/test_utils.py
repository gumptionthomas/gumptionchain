from gumptionchain.util import host_address


def test_host_address(signing_key):
    uri = f'https://{signing_key.address}@magrathea.com:5000'
    assert host_address(uri) == (
        'https://magrathea.com:5000',
        signing_key.address,
    )
